"""Ночной пайплайн «Успех 888»: план → (опционально) рендер → пакет в outbox.

Shadow по умолчанию: кредиты Runway/ElevenLabs не тратятся.
Автопостинга в TikTok/Instagram нет.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

import config
from joblock import JobLock
from nightcal import Calendar, CalendarError, Slot, load_calendar, slots_for_day, today_in_tz
from nightpack import outbox_dir, write_package
from presets import (
    apply_preset,
    camera_prompt,
    default_job,
    estimate_cost,
    motion_prompt,
    voice_settings_payload,
)
from store import (
    list_night_jobs,
    packed_night_slot_ids,
    save_night_run,
    upsert_night_job,
)
from voices import voice_by_index

log = logging.getLogger("videobot.night")

BuildFn = Callable[..., Awaitable[tuple[Path, dict[str, Any]]]]

DONE_STATUSES = frozenset({"packed"})


def calendar_path() -> Path:
    raw = (config.NIGHT_CALENDAR or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent / "calendar.example.json"


def outbox_root() -> Path:
    raw = (config.NIGHT_OUTBOX or "").strip()
    if raw:
        return Path(raw)
    return Path(config.DATA_DIR) / "outbox"


def slot_job(slot: Slot, calendar: Calendar) -> dict[str, Any]:
    job = default_job(mode="preset")
    apply_preset(job, slot.preset)
    job["idea"] = slot.topic
    job["quality"] = slot.quality or calendar.quality_default or job["quality"]
    if slot.n_scenes:
        job["n_scenes"] = int(slot.n_scenes)
    job["watermark"] = bool(calendar.watermark)
    job["platforms"] = list(slot.platforms)
    return job


def estimate_slot(slot: Slot, calendar: Calendar) -> dict[str, Any]:
    job = slot_job(slot, calendar)
    return estimate_cost(
        n_scenes=int(job["n_scenes"]),
        quality=str(job["quality"]),
        text=slot.topic,
        need_still=True,
    )


def plan_slots(
    calendar: Calendar,
    day: date,
    *,
    force: bool = False,
    done_ids: set[str] | None = None,
    used_runway: int = 0,
) -> list[dict[str, Any]]:
    done = set(done_ids or ())
    rows: list[dict[str, Any]] = []
    selected = 0 if force else len(done)
    spent = 0 if force else max(0, int(used_runway))
    budget_left = int(calendar.daily_budget_runway) - spent
    for slot in slots_for_day(calendar, day):
        cost = estimate_slot(slot, calendar)
        runway = int(cost["runway"])
        if slot.id in done and not force:
            rows.append(_row(slot, "skipped_done", runway, cost, "уже готов за эту дату"))
            continue
        if selected >= calendar.max_jobs:
            rows.append(_row(slot, "skipped_cap", runway, cost, f"лимит {calendar.max_jobs} роликов"))
            continue
        if runway > budget_left:
            rows.append(
                _row(slot, "skipped_budget", runway, cost, f"бюджет {budget_left} кр., нужно {runway}")
            )
            continue
        rows.append(_row(slot, "queued", runway, cost, ""))
        selected += 1
        budget_left -= runway
    return rows


def _row(slot: Slot, status: str, runway: int, cost: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "slot": slot,
        "slot_id": slot.id,
        "status": status,
        "preset": slot.preset,
        "topic": slot.topic,
        "platforms": list(slot.platforms),
        "runway_credits": runway,
        "cost_text": cost.get("text") or "",
        "error": error,
        "outbox": "",
    }


def format_report(report: dict[str, Any]) -> str:
    jobs = report.get("jobs") or []
    lines = [
        f"{report.get('brand') or 'Успех 888'} · ночной пайплайн",
        f"Дата: {report.get('date')} · режим: {report.get('mode')}",
        (
            f"Слотов: {report.get('planned', 0)} план / {report.get('rendered', 0)} рендер / "
            f"{report.get('failed', 0)} ошибка / {report.get('skipped', 0)} пропуск"
        ),
        f"Бюджет: {report.get('runway_used', 0)} / {report.get('budget', 0)} кр. Runway",
        "",
    ]
    for job in jobs:
        mark = {
            "planned": "·",
            "packed": "✓",
            "queued": "·",
            "failed": "✗",
        }.get(str(job.get("status")), "–")
        extra = f" — {job['error']}" if job.get("error") else ""
        lines.append(
            f"{mark} {job.get('slot_id')} · {job.get('status')} · {job.get('preset')} · "
            f"{job.get('runway_credits')} кр.{extra}"
        )
        topic = str(job.get("topic") or "")
        if topic:
            lines.append(f"  {topic[:120]}")
        if job.get("outbox"):
            lines.append(f"  пакет: {job['outbox']}")
    lines.append("")
    lines.append("Публикация в TikTok/Instagram — вручную из outbox. Автопостинг выключен.")
    return "\n".join(lines).strip() + "\n"


async def send_owner_report(text: str, *, token: str, chat_id: int) -> bool:
    if not token or int(chat_id or 0) <= 0:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        timeout = aiohttp.ClientTimeout(total=20, sock_connect=10, sock_read=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={"chat_id": int(chat_id), "text": text[:3900], "disable_web_page_preview": True},
            ) as resp:
                if resp.status >= 400:
                    log.warning("telegram report HTTP %s", resp.status)
                    return False
                return True
    except Exception as exc:
        log.warning("telegram report failed: %s", exc)
        return False


async def _default_build(**kwargs: Any) -> tuple[Path, dict[str, Any]]:
    from pipeline import build_video

    return await build_video(**kwargs)


async def _render_slot(
    slot: Slot,
    calendar: Calendar,
    work: Path,
    build_fn: BuildFn,
) -> tuple[Path, dict[str, Any]]:
    job = slot_job(slot, calendar)
    voice = voice_by_index(int(job["voice_idx"]))
    return await build_fn(
        idea=slot.topic,
        work_dir=work,
        progress=None,
        ratio="720:1280",
        style=str(job["style"]),
        voice_id=voice["id"],
        reference_image=None,
        user_script=False,
        n_scenes=int(job["n_scenes"]),
        extra_brief=str(job.get("brief") or ""),
        voice_settings=voice_settings_payload(str(job["delivery"]), str(job["speed"])),
        camera=camera_prompt(str(job["camera"])),
        motion=motion_prompt(str(job["motion"])),
        quality=str(job["quality"]),
        watermark=bool(job["watermark"]),
    )


def _persist_row(day: date, row: dict[str, Any]) -> None:
    upsert_night_job(
        run_date=day.isoformat(),
        slot_id=str(row["slot_id"]),
        status=str(row["status"]),
        preset=str(row.get("preset") or ""),
        topic=str(row.get("topic") or ""),
        platforms=list(row.get("platforms") or []),
        quality=str(row.get("quality") or ""),
        runway_credits=int(row.get("runway_credits") or 0),
        outbox=str(row.get("outbox") or ""),
        error=str(row.get("error") or ""),
    )


def _public_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "slot_id": row["slot_id"],
        "status": row["status"],
        "preset": row.get("preset") or "",
        "topic": row.get("topic") or "",
        "platforms": list(row.get("platforms") or []),
        "runway_credits": int(row.get("runway_credits") or 0),
        "outbox": row.get("outbox") or "",
        "error": row.get("error") or "",
    }


async def run_night(
    *,
    calendar: Calendar | None = None,
    day: date | None = None,
    render: bool = False,
    force: bool = False,
    notify: bool = True,
    build_fn: BuildFn | None = None,
    lock: JobLock | None = None,
    outbox: Path | None = None,
) -> dict[str, Any]:
    cal = calendar or load_calendar(calendar_path())
    run_day = day or today_in_tz(cal.timezone)
    mode = "render" if render else "shadow"
    box = Path(outbox) if outbox else outbox_root()
    packed_ids = packed_night_slot_ids(run_day.isoformat())
    used_runway = sum(
        int(job["runway_credits"])
        for job in list_night_jobs(run_day.isoformat())
        if job["status"] == "packed"
    )
    rows = plan_slots(
        cal,
        run_day,
        force=force,
        done_ids=packed_ids,
        used_runway=used_runway,
    )
    if render:
        if build_fn is None:
            missing = config.missing_render_secrets()
            if missing:
                raise CalendarError("нет секретов для рендера: " + ", ".join(missing))
        file_lock = lock or JobLock()
        if not file_lock.acquire():
            for row in rows:
                if row["status"] == "queued":
                    row["status"] = "skipped_lock"
                    row["error"] = "бот или другой ночной запуск уже снимает"
            rows = rows
        else:
            try:
                builder = build_fn or _default_build
                stop = False
                for row in rows:
                    if row["status"] != "queued":
                        continue
                    if stop:
                        row["status"] = "skipped_budget"
                        row["error"] = "остановлено: кредиты или ошибка предыдущего слота"
                        continue
                    slot: Slot = row["slot"]
                    dest = outbox_dir(box, run_day, slot.id)
                    work = Path(config.WORK_DIR) / f"night_{run_day.isoformat()}_{slot.id}"
                    try:
                        video, script = await _render_slot(slot, cal, work, builder)
                        job = slot_job(slot, cal)
                        meta = write_package(
                            dest,
                            slot,
                            day=run_day,
                            mode="render",
                            script=script,
                            video=video,
                            runway_credits=int(row["runway_credits"]),
                            quality=str(job["quality"]),
                        )
                        row["status"] = "packed"
                        row["outbox"] = meta["outbox"]
                        row["quality"] = job["quality"]
                    except Exception as exc:
                        from pipeline import PipelineError, is_runway_credits_fail

                        user = getattr(exc, "user_message", "") or str(exc)
                        detail = getattr(exc, "detail", "") or ""
                        row["status"] = "failed"
                        row["error"] = (user or "ошибка рендера")[:400]
                        log.warning("night slot %s failed: %s | %s", slot.id, user, detail)
                        if isinstance(exc, PipelineError) and (
                            getattr(exc, "code", "") == "credits" or is_runway_credits_fail(detail)
                        ):
                            stop = True
            finally:
                file_lock.release()
    else:
        for row in rows:
            if row["status"] != "queued":
                continue
            slot = row["slot"]
            dest = outbox_dir(box, run_day, slot.id)
            job = slot_job(slot, cal)
            meta = write_package(
                dest,
                slot,
                day=run_day,
                mode="shadow",
                script=None,
                video=None,
                runway_credits=int(row["runway_credits"]),
                quality=str(job["quality"]),
            )
            row["status"] = "planned"
            row["outbox"] = meta["outbox"]
            row["quality"] = job["quality"]

    for row in rows:
        _persist_row(run_day, row)

    planned = sum(1 for r in rows if r["status"] in ("planned", "queued"))
    rendered = sum(1 for r in rows if r["status"] == "packed")
    failed = sum(1 for r in rows if r["status"] == "failed")
    skipped = sum(1 for r in rows if str(r["status"]).startswith("skipped_"))
    runway_used = sum(
        int(r["runway_credits"]) for r in rows if r["status"] in ("packed", "planned")
    )
    report = {
        "brand": cal.name,
        "run_id": uuid.uuid4().hex[:12],
        "date": run_day.isoformat(),
        "mode": mode,
        "budget": cal.daily_budget_runway,
        "planned": planned,
        "rendered": rendered,
        "failed": failed,
        "skipped": skipped,
        "runway_used": runway_used,
        "jobs": [_public_job(r) for r in rows],
        "auto_publish": False,
    }
    text = format_report(report)
    report["text"] = text
    save_night_run(
        run_id=str(report["run_id"]),
        run_date=run_day.isoformat(),
        mode=mode,
        planned=planned,
        rendered=rendered,
        failed=failed,
        skipped=skipped,
        runway_used=runway_used,
        report=text,
    )
    plan_path = box / run_day.isoformat() / "_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if notify:
        owner = int(cal.owner_chat_id or config.NIGHT_OWNER_CHAT_ID or 0)
        await send_owner_report(text, token=config.VIDEOBOT_TELEGRAM_TOKEN, chat_id=owner)
    log.info(
        "night %s %s planned=%s packed=%s failed=%s skipped=%s",
        mode,
        run_day.isoformat(),
        planned,
        rendered,
        failed,
        skipped,
    )
    return report


def status_text(calendar: Calendar | None = None, day: date | None = None) -> str:
    from store import get_last_night_run, list_night_jobs

    cal = calendar or load_calendar(calendar_path())
    run_day = day or today_in_tz(cal.timezone)
    jobs = list_night_jobs(run_day.isoformat())
    last = get_last_night_run()
    if not jobs and not last:
        return (
            f"{cal.name}: ночной пайплайн ещё не запускался.\n"
            f"Календарь: {len(cal.slots)} слотов, бюджет {cal.daily_budget_runway} кр./сутки.\n"
            "По умолчанию shadow — ролики не снимает."
        )
    if jobs:
        fake = {
            "brand": cal.name,
            "date": run_day.isoformat(),
            "mode": (last or {}).get("mode") or "shadow",
            "planned": sum(1 for j in jobs if j["status"] == "planned"),
            "rendered": sum(1 for j in jobs if j["status"] == "packed"),
            "failed": sum(1 for j in jobs if j["status"] == "failed"),
            "skipped": sum(1 for j in jobs if str(j["status"]).startswith("skipped_")),
            "runway_used": sum(int(j["runway_credits"]) for j in jobs if j["status"] in ("packed", "planned")),
            "budget": cal.daily_budget_runway,
            "jobs": jobs,
        }
        return format_report(fake)
    return last.get("report") or "Нет отчёта."
