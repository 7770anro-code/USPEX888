"""Съёмка серии: план Grok → SCRIPT_SYSTEM сериала → Runway, без чужих роликов из сети."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import aiohttp

import config
import pipeline as pipeline_mod
from night_accounts import serial_account
from night_circuit import ELEVEN, ELEVEN_GATE, RUNWAY, RUNWAY_GATE, CircuitOpen
from night_store import (
    IDEAS_READY,
    WAIT_CONFIRM,
    create_job,
    mark_video_ready,
    require_confirm,
    update_job,
)
from night_video import estimate_job
from presets import camera_prompt, motion_prompt, voice_settings_payload
from pipeline import PipelineError, build_video, is_runway_person_moderation, is_runway_safety_fail
from serial_plot import (
    DEFAULT_CONTINUITY,
    DEFAULT_LORE,
    DEFAULT_SEED,
    DEFAULT_TITLE,
    N_SCENES,
    SERIAL_SCRIPT_SYSTEM,
    SERIAL_SLUG,
    plan_episode,
    serial_script_brief,
)
from serial_store import (
    add_note,
    dump_script,
    get_serial,
    insert_episode,
    last_episode,
    list_notes,
    next_run_dates,
    serial_dir,
    update_episode,
    update_serial,
    upsert_serial,
)

log = logging.getLogger("videobot.serial")


def ensure_default_serial() -> dict[str, Any]:
    acc = serial_account()
    if acc is None:
        raise PipelineError("Нет слота NIGHT_ACC4 (role=serial) — мультсериал не к чему привязать.")
    found = get_serial(slug=SERIAL_SLUG)
    if found:
        if not found.get("account_id"):
            update_serial(int(found["id"]), account_id=acc.id)
            found = get_serial(slug=SERIAL_SLUG) or found
        return found
    return upsert_serial(
        {
            "slug": SERIAL_SLUG,
            "title": DEFAULT_TITLE,
            "account_id": acc.id,
            "format": "reveal",
            "seed": DEFAULT_SEED,
            "lore": DEFAULT_LORE,
            "continuity": DEFAULT_CONTINUITY,
            "summary": "",
            "last_cliff": "",
            "episode_count": 0,
        }
    )


def status_text(serial: dict[str, Any] | None = None) -> str:
    serial = serial or ensure_default_serial()
    acc = serial_account()
    n = int(serial.get("episode_count") or 0)
    last = last_episode(int(serial["id"]))
    notes = list_notes(int(serial["id"]), limit=3)
    lines = [
        f"📺 {serial.get('title') or DEFAULT_TITLE} · аккаунт {serial.get('account_id') or acc.id if acc else '?'}",
        f"Серий снято: {n}. Формат: reveal (фрукты/машины, без логотипов).",
        f"Слот TikTok: NIGHT_ACC{acc.index if acc else 4}_TIKTOK_ACCESS_TOKEN"
        + (" ✓" if acc and acc.has_tiktok else " (пока пуст — съёмка всё равно идёт, пост потом)"),
    ]
    if last:
        lines.append(f"Последняя: #{last.get('n')} «{last.get('title')}» → {last.get('run_date')} ({last.get('status')})")
        if last.get("cliffhanger"):
            lines.append(f"Клиффхэнгер: {last.get('cliffhanger')}")
    if serial.get("summary"):
        lines.append("Память: " + str(serial.get("summary") or "")[:400])
    if notes:
        lines.append("Правки: " + " | ".join(str(x.get("text") or "")[:80] for x in notes))
    lines.append("Пост: да/нет в /night в день run_date, как остальной автопост.")
    return "\n".join(lines)


def apply_owner_note(text: str) -> dict[str, Any]:
    serial = ensure_default_serial()
    nxt = int(serial.get("episode_count") or 0) + 1
    add_note(int(serial["id"]), text, episode_from=nxt)
    return get_serial(slug=SERIAL_SLUG) or serial


async def _render_one(
    serial: dict[str, Any],
    plan: dict[str, Any],
    *,
    n: int,
    run_date: str,
    progress: Any = None,
) -> dict[str, Any]:
    acc = serial_account()
    if acc is None:
        raise PipelineError("Нет serial-аккаунта.")
    if not RUNWAY.allow() or not ELEVEN.allow():
        raise CircuitOpen("runway" if not RUNWAY.allow() else "elevenlabs")
    await RUNWAY_GATE.wait()
    await ELEVEN_GATE.wait()

    idea = {
        "title": plan["title"],
        "plot": plan["plot"],
        "caption": plan["caption"],
        "hook": plan["hook"],
        "kind": "serial",
        "tokens": [],
        "idea_hash": f"serial-{serial['id']}-{n}",
    }
    jid = create_job(
        {
            "run_date": run_date,
            "account_id": acc.id,
            "kind": "serial",
            "title": f"{serial.get('title') or DEFAULT_TITLE} · {n}. {plan['title']}",
            "plot": plan["plot"],
            "caption": plan["caption"],
            "hook": plan["hook"],
            "idea_hash": idea["idea_hash"],
            "tokens": [f"ep{n}", "serial"],
            "status": IDEAS_READY,
        }
    )
    ep_id = insert_episode(
        int(serial["id"]),
        n,
        {
            "title": plan["title"],
            "plot": plan["plot"],
            "hook": plan["hook"],
            "cliffhanger": plan["cliffhanger"],
            "caption": plan["caption"],
            "run_date": run_date,
            "night_job_id": jid,
            "status": "generating",
        },
    )

    dest_dir = Path(config.NIGHT_OUTBOX) / run_date / acc.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"serial_{n}.mp4"
    work = Path(config.WORK_DIR) / f"serial_{serial['id']}_{n}"
    still_src = Path(str(serial.get("still_path") or ""))
    ref = still_src if still_src.is_file() else None
    extra = serial_script_brief(serial, plan)
    pipeline_mod.CANCEL_ON_TIMEOUT = False
    try:
        video, script = await build_video(
            plan["plot"],
            work,
            progress,
            ratio="720:1280",
            style=acc.style or "cartoon",
            voice_id=acc.voice_id,
            reference_image=ref,
            user_script=False,
            n_scenes=N_SCENES,
            extra_brief=extra,
            voice_settings=voice_settings_payload(acc.delivery, acc.speed),
            camera=camera_prompt("orbit"),
            motion=motion_prompt("drive"),
            quality=acc.quality or "fast",
            watermark=False,
            hook=plan["hook"],
            script_system=SERIAL_SCRIPT_SYSTEM,
            photo_lock=False,
            route_mode="synthetic_multi_scene",
        )
        RUNWAY.ok()
        ELEVEN.ok()
    except PipelineError as exc:
        if getattr(exc, "code", "") == "credits" or "credit" in (exc.detail or "").lower():
            RUNWAY.fail()
        if is_runway_safety_fail("", exc.detail) or is_runway_person_moderation("", exc.detail):
            RUNWAY.fail()
            exc.code = "moderation"
        update_episode(ep_id, status="failed", last_error=(exc.user_message or "")[:400])
        update_job(jid, status="failed", last_error=(exc.user_message or "")[:400])
        raise
    finally:
        pipeline_mod.CANCEL_ON_TIMEOUT = True

    dest.parent.mkdir(parents=True, exist_ok=True)
    src = Path(video)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    bible = work / "bible_still.png"
    still_dest = serial_dir(int(serial["id"])) / "bible_still.png"
    if bible.is_file() and not still_dest.is_file():
        shutil.copyfile(bible, still_dest)
    elif ref and ref.is_file() and not still_dest.is_file():
        shutil.copyfile(ref, still_dest)

    cost = estimate_job(idea, acc, n_scenes=N_SCENES)
    mark_video_ready(
        jid,
        str(dest),
        runway_credits=int(cost.get("runway") or 0),
        eleven_chars=int(cost.get("eleven_chars") or 0),
    )
    if require_confirm():
        update_job(jid, status=WAIT_CONFIRM)
    (dest.parent / f"serial_{n}_script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lore = str(serial.get("lore") or "")
    add = str(plan.get("lore_add") or "").strip()
    if add and add.lower() not in lore.lower():
        lore = (lore + " " + add).strip()[:2500]
    continuity = str(plan.get("continuity") or serial.get("continuity") or DEFAULT_CONTINUITY)[:900]
    summary = str(plan.get("summary_update") or serial.get("summary") or "")[:1200]
    update_serial(
        int(serial["id"]),
        lore=lore,
        continuity=continuity,
        summary=summary,
        last_cliff=plan.get("cliffhanger") or "",
        still_path=str(still_dest) if still_dest.is_file() else str(serial.get("still_path") or ""),
        episode_count=n,
    )
    update_episode(
        ep_id,
        script_json=dump_script(script),
        video_path=str(dest),
        status="wait_confirm" if require_confirm() else "video_ready",
        last_error="",
    )
    try:
        from library import archive_night_video

        archive_night_video(
            dest,
            run_date=str(run_date),
            account=str(acc.id),
            job_id=f"serial_{n}",
            title=str(plan.get("title") or f"Серия {n}"),
        )
    except Exception:
        log.warning("serial library archive failed episode=%s", n)
    log.info("serial episode %s ready job=%s path=%s", n, jid, dest)
    return {
        "episode_id": ep_id,
        "n": n,
        "job_id": jid,
        "run_date": run_date,
        "video_path": str(dest),
        "title": plan["title"],
        "hook": plan["hook"],
        "cliffhanger": plan.get("cliffhanger") or "",
        "caption": plan.get("caption") or "",
        "cost": cost,
    }


async def generate_episodes(count: int = 1, *, progress: Any = None) -> list[dict[str, Any]]:
    count = max(1, min(7, int(count)))
    serial = ensure_default_serial()
    acc = serial_account()
    if acc is None:
        raise PipelineError("Нет serial-аккаунта.")
    last0 = last_episode(int(serial["id"]))
    start_n = int(last0["n"]) + 1 if last0 else 1
    dates = next_run_dates(str(serial.get("account_id") or acc.id), count)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    out: list[dict[str, Any]] = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for i in range(count):
            serial = get_serial(slug=SERIAL_SLUG) or serial
            n = start_n + i
            notes = list_notes(int(serial["id"]))
            last = last_episode(int(serial["id"]))
            if progress:
                await progress(f"Серия {n}: пишу сюжет…")
            plan = await plan_episode(session, serial, notes, n=n, last=last)
            if progress:
                await progress(f"Серия {n}: снимаю «{plan['title']}»…")
            row = await _render_one(
                serial,
                plan,
                n=n,
                run_date=dates[i] if i < len(dates) else dates[-1],
                progress=progress,
            )
            out.append(row)
    return out
