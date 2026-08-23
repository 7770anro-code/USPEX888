#!/usr/bin/env python3
"""Успех 888 — отдельный ночной процесс (systemd timer), не внутри Telegram-бота.

  python night_runner.py              # полный цикл
  python night_runner.py --smoke      # 1 идея → 1 видео → пост если токены есть
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
from night_accounts import load_accounts
from night_circuit import CircuitOpen, jitter_pause
from night_ideas import assign_to_accounts, generate_ideas
from night_post import PostResult, publish_account
from night_report import confirm_markup, format_report, send_telegram
from night_store import (
    FAILED,
    GENERATING,
    MANUAL_REVIEW,
    PENDING,
    POSTED,
    POSTING,
    VIDEO_READY,
    WAIT_CONFIRM,
    create_job,
    insert_idea,
    jobs_for_date,
    lock_job,
    mark_video_ready,
    recover_stale,
    save_run,
    update_job,
    worker_id,
)
from night_time import today_msk
from night_video import estimate_job, render_idea
from pipeline import PipelineError, ensure_ffmpeg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("videobot.night")


def _outbox(day: str, account_id: str) -> Path:
    path = Path(config.NIGHT_OUTBOX) / day / account_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _blockers_from_accounts(accounts) -> list[str]:
    lines: list[str] = []
    if not config.NIGHT_AUTOPOST:
        lines.append(
            "NIGHT_AUTOPOST=0 — автопостинг выключен. Включите после App Review и OAuth."
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
    if config.NIGHT_AUTOPOST and not config.NIGHT_PUBLIC_VIDEO_BASE_URL:
        lines.append(
            "NIGHT_PUBLIC_VIDEO_BASE_URL пуст — Instagram video_url недоступен, "
            "используем resumable upload на rupload.facebook.com."
        )
    return lines


async def _render_job(job: dict, account, idea: dict, *, n_scenes: int) -> None:
    wid = worker_id()
    lock_job(int(job["id"]), GENERATING, wid)
    day = str(job["run_date"])
    dest = _outbox(day, account.id) / "final.mp4"
    work = Path(config.WORK_DIR) / f"night_{day}_{account.id}_{job['id']}"
    try:
        video, script, cost = await render_idea(
            idea, account, work, dest, n_scenes=n_scenes
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
        if config.NIGHT_REQUIRE_CONFIRM:
            update_job(int(job["id"]), status=WAIT_CONFIRM)
        log.info("job %s video_ready %s", job["id"], video)
    except CircuitOpen as exc:
        update_job(int(job["id"]), status=PENDING, last_error=str(exc), locked_at=None, worker_id="")
        raise
    except PipelineError as exc:
        update_job(
            int(job["id"]),
            status=FAILED,
            last_error=(exc.user_message or str(exc))[:400],
            locked_at=None,
            worker_id="",
        )
        raise
    except Exception as exc:
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
    video = Path(str(job.get("video_path") or ""))
    if not video.is_file():
        update_job(int(job["id"]), last_error="video_path отсутствует, пост пропущен")
        return
    lock_job(int(job["id"]), POSTING, worker_id())
    try:
        results = await publish_account(session, account, video, str(job.get("caption") or job.get("title") or ""))
    except Exception as exc:
        update_job(
            int(job["id"]),
            status=VIDEO_READY,
            last_error=f"post fallback: {type(exc).__name__}",
            locked_at=None,
            worker_id="",
        )
        log.warning("post failed, kept local file job=%s", job["id"])
        return
    tt = next((r for r in results if r.platform == "tiktok"), PostResult("tiktok", False))
    ig = next((r for r in results if r.platform == "instagram"), PostResult("instagram", False))
    errors = [r.error for r in results if r.error]
    blockers = [v for r in results for v in r.vars_needed]
    posted_any = tt.ok or ig.ok
    update_job(
        int(job["id"]),
        status=POSTED if posted_any and not errors else VIDEO_READY,
        tiktok_url=tt.url,
        instagram_url=ig.url,
        tiktok_mode=tt.mode,
        instagram_mode=ig.mode,
        last_error=(" | ".join(errors) + ((" vars: " + ", ".join(blockers)) if blockers else ""))[:400],
        locked_at=None,
        worker_id="",
    )


async def run_night(*, smoke: bool = False, notify: bool = True) -> dict:
    ensure_ffmpeg()
    recover_stale()
    day = today_msk().isoformat()
    accounts = load_accounts()
    n_videos = 1 if smoke else int(config.VIDEOS_PER_NIGHT)
    n_ideas = 5 if smoke else int(config.NIGHT_IDEAS_PER_NIGHT)
    n_scenes = 4
    run_id = uuid.uuid4().hex[:12]
    owner_blockers = _blockers_from_accounts(accounts)

    file_lock = JobLock()
    if not file_lock.acquire():
        report = format_report(
            {
                "date": day,
                "videos_ok": 0,
                "videos_planned": n_videos,
                "ideas": 0,
                "autopost": config.NIGHT_AUTOPOST,
                "jobs": [],
                "owner_blockers": ["videobot.lock занят живым ботом или другим night_runner"],
            }
        )
        save_run(run_id, day, "skipped_lock", report)
        if notify:
            await send_telegram(report)
        print(report, end="")
        return {"text": report, "skipped": True}

    ideas: list = []
    try:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            ideas = await generate_ideas(session, n=n_ideas)
            for idea in ideas:
                insert_idea(idea, day)
            assigned = assign_to_accounts(ideas, accounts[:n_videos], limit=n_videos)
            if smoke:
                assigned = assigned[:1]
            jobs = []
            for acc, idea in assigned:
                estimate_job(idea, acc, n_scenes=n_scenes)
                jid = create_job(
                    {
                        "run_date": day,
                        "account_id": acc.id,
                        "kind": idea["kind"],
                        "title": idea["title"],
                        "plot": idea["plot"],
                        "caption": idea["caption"],
                        "idea_hash": idea["idea_hash"],
                        "tokens": idea["tokens"],
                        "status": PENDING,
                    }
                )
                jobs.append(({"id": jid, "run_date": day, **idea, "account_id": acc.id}, acc, idea))

            mod_hits = 0
            for job, acc, idea in jobs:
                try:
                    await _render_job(job, acc, idea, n_scenes=n_scenes)
                except Exception as exc:
                    log.warning("render stop/continue: %s", type(exc).__name__)
                    if isinstance(exc, PipelineError) and getattr(exc, "code", "") == "moderation":
                        mod_hits += 1
                        update_job(int(job["id"]), status=MANUAL_REVIEW, last_error="moderation")
                        if mod_hits >= config.NIGHT_MODERATION_STOP:
                            log.error("moderation stop after %s hits", mod_hits)
                            break
                    if isinstance(exc, PipelineError) and (
                        getattr(exc, "code", "") == "credits"
                        or "кредит" in (exc.user_message or "").lower()
                    ):
                        break
                pause = 3 if smoke else jitter_pause(8, 25)
                if pause:
                    await asyncio.sleep(pause)

            if (not config.NIGHT_REQUIRE_CONFIRM) and config.NIGHT_AUTOPOST:
                posted_jobs = jobs_for_date(day)
                acc_map = {a.id: a for a in accounts}
                first_post = True
                for row in posted_jobs:
                    if row.get("status") not in (VIDEO_READY, WAIT_CONFIRM):
                        continue
                    acc = acc_map.get(str(row["account_id"]))
                    if not acc:
                        continue
                    if not first_post and not smoke:
                        await asyncio.sleep(
                            jitter_pause(config.NIGHT_POST_PAUSE_MIN, config.NIGHT_POST_PAUSE_MAX)
                        )
                    first_post = False
                    await _post_job(session, row, acc)
            else:
                owner_blockers.append(
                    "Публикация ждёт да/нет утром в Telegram (кнопки или /night). "
                    "Полный автопост: NIGHT_REQUIRE_CONFIRM=0 и NIGHT_AUTOPOST=1."
                )
    except Exception as exc:
        log.exception("night run failed")
        owner_blockers.append(f"прогон оборвался: {type(exc).__name__}")
    finally:
        file_lock.release()

    rows = jobs_for_date(day)
    payload = {
        "date": day,
        "videos_ok": sum(1 for j in rows if Path(str(j.get("video_path") or "")).is_file()),
        "videos_planned": n_videos,
        "ideas": len(ideas) if "ideas" in locals() else 0,
        "autopost": config.NIGHT_AUTOPOST,
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
    if notify:
        wait_ids = [int(j["id"]) for j in rows if j.get("status") == WAIT_CONFIRM]
        await send_telegram(text, reply_markup=confirm_markup(wait_ids) if wait_ids else None)
    print(text, end="")
    return {"text": text, "payload": payload}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Успех 888 night_runner")
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
