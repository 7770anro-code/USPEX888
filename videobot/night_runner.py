#!/usr/bin/env python3
"""Автоконтур idea→video. CLI для smoke; в проде крутится внутри bot.py.

  python night_runner.py              # один тик
  python night_runner.py --smoke      # 1 идея → 1 видео, без Telegram
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path

import aiohttp

import config
from joblock import JobLock
from night_accounts import accounts_round_robin, load_accounts
from night_circuit import CircuitOpen, jitter_pause
from night_ideas import assign_to_accounts, generate_ideas
from night_post import persist_publish_results, publish_account
from night_report import confirm_markup, format_report, send_telegram
from night_store import (
    FAILED,
    GENERATING,
    IDEAS_READY,
    MANUAL_REVIEW,
    PENDING,
    POSTING,
    VIDEO_READY,
    WAIT_CONFIRM,
    autopost_enabled,
    consecutive_moderation,
    create_job,
    insert_idea,
    jobs_for_date,
    lock_job,
    mark_video_ready,
    pending_owner_ids,
    ready_video_count,
    recover_stale,
    remaining_daily_slots,
    require_confirm,
    runway_credits_today,
    save_run,
    update_job,
    worker_id,
)
from night_time import today_msk
from night_video import estimate_job, render_idea
from pipeline import PipelineError, ensure_ffmpeg

log = logging.getLogger("videobot.night")


def _outbox(day: str, account_id: str) -> Path:
    path = Path(config.NIGHT_OUTBOX) / day / account_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _blockers_from_accounts(accounts) -> list[str]:
    lines: list[str] = []
    if require_confirm() or not autopost_enabled():
        lines.append(
            "Первая неделя: идеи и видео сами весь день, публикация — да/нет в Telegram "
            "(/night или кнопки). Полный автопост позже: /night_mode auto "
            "или NIGHT_REQUIRE_CONFIRM=0 и NIGHT_AUTOPOST=1."
        )
    for acc in accounts:
        if not acc.has_tiktok:
            lines.append(
                f"{acc.id}: нет {acc.tiktok_token_var}. "
                "Нужны TikTok app, OAuth, scope video.upload (inbox) или video.publish (direct). "
                "Без App Review публикация только private/draft."
            )
        if not acc.has_instagram:
            lines.append(
                f"{acc.id}: нет {acc.ig_token_var} и/или {acc.ig_user_var}. "
                "Нужен IG Business/Creator + Facebook Page + instagram_content_publish "
                "(часто Meta App Review). Не обходим."
            )
    if autopost_enabled() and not config.NIGHT_PUBLIC_VIDEO_BASE_URL:
        lines.append(
            "NIGHT_PUBLIC_VIDEO_BASE_URL пуст — Instagram video_url недоступен, "
            "используем resumable upload на rupload.facebook.com."
        )
    return lines


async def _render_job(job: dict, account, idea: dict, *, n_scenes: int) -> None:
    from live_status import (
        finish_job,
        format_status,
        get_job,
        job_key_night,
        job_scope,
        live_markup_dict,
        set_message,
        start_job,
    )
    from night_report import edit_telegram_message, send_telegram_message

    wid = worker_id()
    lock_job(int(job["id"]), GENERATING, wid)
    day = str(job["run_date"])
    dest = _outbox(day, account.id) / f"{job['id']}.mp4"
    work = Path(config.WORK_DIR) / f"night_{day}_{account.id}_{job['id']}"
    job_key = job_key_night(int(job["id"]))
    owner = int(config.NIGHT_OWNER_CHAT_ID or 0)
    start_job(
        job_key,
        chat_id=owner,
        title=str(idea.get("title") or ""),
        scene_total=n_scenes,
    )
    msg_id = 0
    if owner > 0:
        sent = await send_telegram_message(
            format_status(get_job(job_key)),
            chat_id=owner,
            reply_markup=live_markup_dict(job_key),
        )
        msg_id = int(sent or 0)
        if msg_id:
            set_message(job_key, msg_id)

    async def progress(text: str) -> None:
        if owner > 0 and msg_id:
            snap = get_job(job_key)
            markup = live_markup_dict(job_key) if snap and not snap.get("done") else None
            await edit_telegram_message(owner, msg_id, text, reply_markup=markup)

    try:
        with job_scope(job_key):
            video, script, cost = await render_idea(
                idea, account, work, dest, n_scenes=n_scenes, progress=progress
            )
        (dest.parent / "script.json").write_text(
            json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (dest.parent / "caption.txt").write_text(str(idea.get("caption") or ""), encoding="utf-8")
        # путь в БД сразу — постинг отдельный шаг, рестарт не перегенерирует
        mark_video_ready(
            int(job["id"]),
            str(video),
            runway_credits=int(cost.get("runway") or 0),
            eleven_chars=int(cost.get("eleven_chars") or 0),
        )
        if require_confirm():
            update_job(int(job["id"]), status=WAIT_CONFIRM)
        finish_job(job_key, label="Готово, ждёт да/нет в /night" if require_confirm() else "Готово")
        if owner > 0 and msg_id:
            await edit_telegram_message(
                owner,
                msg_id,
                format_status(get_job(job_key)),
                reply_markup=None,
            )
        log.info("job %s video_ready %s", job["id"], video)
    except CircuitOpen as exc:
        finish_job(job_key, failed=True, label=str(exc))
        update_job(int(job["id"]), status=PENDING, last_error=str(exc), locked_at=None, worker_id="")
        raise
    except PipelineError as exc:
        finish_job(job_key, failed=True, label=exc.user_message)
        update_job(
            int(job["id"]),
            status=FAILED,
            last_error=(exc.user_message or str(exc))[:400],
            locked_at=None,
            worker_id="",
        )
        raise
    except Exception as exc:
        finish_job(job_key, failed=True, label=type(exc).__name__)
        update_job(
            int(job["id"]),
            status=FAILED,
            last_error=f"{type(exc).__name__}",
            locked_at=None,
            worker_id="",
        )
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def _post_job(session: aiohttp.ClientSession, job: dict, account) -> None:
    from night_store import video_belongs_to_account

    video = Path(str(job.get("video_path") or ""))
    if not video.is_file():
        update_job(int(job["id"]), last_error="video_path отсутствует, пост пропущен")
        return
    if not video_belongs_to_account(str(video), str(account.id), str(job.get("run_date") or "")):
        update_job(
            int(job["id"]),
            status=MANUAL_REVIEW,
            last_error="отказ: один файл на несколько аккаунтов не публикую",
            locked_at=None,
            worker_id="",
        )
        return
    lock_job(int(job["id"]), POSTING, worker_id())
    try:
        results = await publish_account(
            session,
            account,
            video,
            str(job.get("caption") or job.get("title") or ""),
            tiktok_publish_id=str(job.get("tiktok_publish_id") or ""),
            ig_container_id=str(job.get("ig_container_id") or ""),
            confirmed=False,
        )
    except Exception as exc:
        has_id = bool(job.get("tiktok_publish_id") or job.get("ig_container_id"))
        update_job(
            int(job["id"]),
            status="publish_unknown" if has_id else VIDEO_READY,
            last_error=f"post fallback: {type(exc).__name__}",
            locked_at=None,
            worker_id="",
        )
        log.warning("post failed, kept local file job=%s", job["id"])
        return
    persist_publish_results(int(job["id"]), job, results)


async def run_night(
    *,
    smoke: bool = False,
    notify: bool = True,
    busy: asyncio.Lock | None = None,
    idle_quiet: bool = False,
) -> dict:
    ensure_ffmpeg()
    recover_stale()
    day = today_msk().isoformat()
    confirm = require_confirm()
    auto = autopost_enabled()
    if confirm:
        for job in jobs_for_date(day):
            if job.get("status") == VIDEO_READY and Path(str(job.get("video_path") or "")).is_file():
                update_job(int(job["id"]), status=WAIT_CONFIRM)
    accounts = load_accounts()
    daily_limit = 1 if smoke else int(config.VIDEOS_PER_NIGHT)
    remaining = remaining_daily_slots(day, daily_limit=daily_limit)
    batch = 1 if smoke else min(int(config.NIGHT_BATCH_PER_TICK), remaining)
    n_ideas = 5 if smoke else int(config.NIGHT_IDEAS_PER_NIGHT)
    n_scenes = 4
    run_id = uuid.uuid4().hex[:12]
    owner_blockers = _blockers_from_accounts(accounts)
    ideas: list = []
    held_busy = False
    new_job_ids: list[int] = []

    if remaining <= 0 and not smoke:
        log.info("автоконтур: дневной лимит %s уже набран, тик пустой", daily_limit)
        return {"skipped": True, "reason": "daily_quota", "payload": {"videos_ok": ready_video_count(day)}}

    budget = int(config.NIGHT_RUNWAY_DAILY_BUDGET or 0)
    if budget and runway_credits_today(day) >= budget:
        log.info("автоконтур: дневной бюджет Runway (%s) исчерпан", budget)
        return {"skipped": True, "reason": "runway_budget"}

    if busy is not None:
        if busy.locked():
            log.info("автоконтур: пропуск тика, идёт ручная съёмка")
            return {"skipped": True, "reason": "busy"}
        await busy.acquire()
        held_busy = True

    file_lock = JobLock()
    if not file_lock.acquire():
        if held_busy and busy is not None:
            busy.release()
        log.info("автоконтур: videobot.lock занят другим процессом (CLI/timer)")
        if idle_quiet:
            return {"skipped": True, "reason": "file_lock"}
        report = format_report(
            {
                "date": day,
                "videos_ok": 0,
                "videos_planned": batch,
                "ideas": 0,
                "autopost": auto,
                "jobs": [],
                "owner_blockers": ["videobot.lock занят другим процессом (CLI smoke или старый timer)"],
            }
        )
        save_run(run_id, day, "skipped_lock", report)
        if notify:
            await send_telegram(report)
        print(report, end="")
        return {"text": report, "skipped": True}

    produced = 0
    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            ideas = await generate_ideas(session, n=n_ideas)
            for idea in ideas:
                insert_idea(idea, day)
            save_run(run_id, day, IDEAS_READY, f"ideas_ready {len(ideas)}")
            shoot = accounts_round_robin(accounts, jobs_for_date(day), batch)
            assigned = assign_to_accounts(
                ideas, shoot, limit=batch, existing_jobs=jobs_for_date(day)
            )
            if smoke:
                assigned = assigned[:1]
            jobs = []
            for acc, idea in assigned:
                cost = estimate_job(idea, acc, n_scenes=n_scenes)
                if budget and runway_credits_today(day) + int(cost.get("runway") or 0) > budget:
                    owner_blockers.append(
                        f"Остановка: NIGHT_RUNWAY_DAILY_BUDGET={budget} (сегодня уже {runway_credits_today(day)})."
                    )
                    break
                jid = create_job(
                    {
                        "run_date": day,
                        "account_id": acc.id,
                        "kind": idea["kind"],
                        "title": idea["title"],
                        "plot": idea["plot"],
                        "caption": idea["caption"],
                        "hook": idea.get("hook") or "",
                        "idea_hash": idea["idea_hash"],
                        "tokens": idea["tokens"],
                        "status": IDEAS_READY,
                    }
                )
                new_job_ids.append(jid)
                jobs.append(({"id": jid, "run_date": day, **idea, "account_id": acc.id}, acc, idea))

            for job, acc, idea in jobs:
                try:
                    await _render_job(job, acc, idea, n_scenes=n_scenes)
                    produced += 1
                except Exception as exc:
                    log.warning("render stop/continue: %s", type(exc).__name__)
                    if isinstance(exc, PipelineError) and getattr(exc, "code", "") in (
                        "moderation",
                        "moderation_person",
                    ):
                        update_job(int(job["id"]), status=MANUAL_REVIEW, last_error="moderation")
                        mod_hits = consecutive_moderation(day)
                        if mod_hits >= config.NIGHT_MODERATION_STOP:
                            log.error("moderation stop after %s hits", mod_hits)
                            owner_blockers.append(
                                f"Стоп: {mod_hits} moderation/rejection подряд. Правки вручную."
                            )
                            break
                    if isinstance(exc, PipelineError) and (
                        getattr(exc, "code", "") == "credits"
                        or "кредит" in (exc.user_message or "").lower()
                    ):
                        owner_blockers.append("Runway вернул нехватку кредитов — тик остановлен.")
                        break
                pause = 3 if smoke else jitter_pause(8, 25)
                if pause:
                    await asyncio.sleep(pause)

            if (not confirm) and auto:
                posted_jobs = jobs_for_date(day)
                acc_map = {a.id: a for a in accounts}
                first_post = True
                for row in posted_jobs:
                    if row.get("status") not in (VIDEO_READY, WAIT_CONFIRM):
                        continue
                    if int(row["id"]) not in new_job_ids:
                        continue
                    if consecutive_moderation(day) >= config.NIGHT_MODERATION_STOP:
                        owner_blockers.append(
                            "Стоп публикации: несколько moderation/rejection подряд."
                        )
                        break
                    acc = acc_map.get(str(row["account_id"]))
                    if not acc:
                        continue
                    if not first_post and not smoke:
                        await asyncio.sleep(
                            jitter_pause(config.NIGHT_POST_PAUSE_MIN, config.NIGHT_POST_PAUSE_MAX)
                        )
                    first_post = False
                    await _post_job(session, row, acc)
            elif produced:
                owner_blockers.append(
                    "Публикация ждёт да/нет в Telegram (кнопки или /night). "
                    "Полный автопост позже: /night_mode auto."
                )
    except Exception as exc:
        log.exception("night run failed")
        owner_blockers.append(f"прогон оборвался: {type(exc).__name__}")
    finally:
        file_lock.release()
        if held_busy and busy is not None:
            busy.release()

    rows = jobs_for_date(day)
    payload = {
        "date": day,
        "videos_ok": sum(1 for j in rows if Path(str(j.get("video_path") or "")).is_file()),
        "videos_planned": daily_limit,
        "ideas": len(ideas) if "ideas" in locals() else 0,
        "autopost": auto,
        "require_confirm": confirm,
        "jobs": [
            {
                "account": j.get("account_id"),
                "kind": j.get("kind"),
                "status": j.get("status"),
                "title": j.get("title"),
                "video_path": j.get("video_path"),
                "runway_credits": j.get("runway_credits"),
                "eleven_chars": j.get("eleven_chars"),
                "tiktok_url": j.get("tiktok_url"),
                "instagram_url": j.get("instagram_url"),
                "error": j.get("last_error"),
                "blockers": [],
            }
            for j in rows
        ],
        "owner_blockers": owner_blockers,
    }
    text = format_report(payload)
    save_run(run_id, day, "done", text)
    out = Path(config.NIGHT_OUTBOX) / day / "_report.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    should_notify = notify and (produced > 0 or (not idle_quiet and not smoke))
    if should_notify:
        wait_ids = [jid for jid in pending_owner_ids(day) if jid in new_job_ids] or pending_owner_ids(day)
        await send_telegram(text, reply_markup=confirm_markup(wait_ids) if wait_ids else None)
    if not idle_quiet:
        print(text, end="")
    else:
        log.info("автоконтур тик: produced=%s remaining_after=%s", produced, remaining_daily_slots(day, daily_limit=daily_limit))
    return {"text": text, "payload": payload, "produced": produced}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Успех 888 auto pipeline (один тик)")
    parser.add_argument("--smoke", action="store_true", help="1 идея, 1 видео, короткие паузы")
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args(argv)
    missing = config.missing_render_secrets()
    if missing:
        print("нет секретов съёмки: " + ", ".join(missing))
        return 2
    result = asyncio.run(run_night(smoke=bool(args.smoke), notify=not args.no_telegram))
    payload = result.get("payload") or {}
    ok = int(payload.get("videos_ok") or 0)
    return 0 if ok or result.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
