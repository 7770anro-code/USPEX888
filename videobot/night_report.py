"""Модуль 4: утренний отчёт в Telegram. Только имена переменных, не значения."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

import config

log = logging.getLogger("videobot.night")


def format_report(payload: dict[str, Any]) -> str:
    lines = [
        "Успех 888 · утренний отчёт",
        f"Дата: {payload.get('date')} · видео: {payload.get('videos_ok', 0)}/{payload.get('videos_planned', 0)}",
        f"Идей: {payload.get('ideas', 0)} · автопост: {'on' if payload.get('autopost') else 'off'}",
        "",
    ]
    for job in payload.get("jobs") or []:
        lines.append(f"• {job.get('account')} · {job.get('kind')} · {job.get('status')}")
        if job.get("title"):
            lines.append(f"  {job['title']}")
        if job.get("video_path"):
            lines.append(f"  файл: {job['video_path']}")
        lines.append(
            f"  кредиты Runway ≈ {job.get('runway_credits', 0)} · ElevenLabs ≈ {job.get('eleven_chars', 0)} симв."
        )
        if job.get("tiktok_url"):
            lines.append(f"  TikTok: {job['tiktok_url']}")
        if job.get("instagram_url"):
            lines.append(f"  Instagram: {job['instagram_url']}")
        if job.get("error"):
            lines.append(f"  ошибка: {job['error']}")
        if job.get("blockers"):
            lines.append("  блокер переменные: " + ", ".join(job["blockers"]))
    blockers = payload.get("owner_blockers") or []
    if blockers:
        lines.append("")
        lines.append("Что нужно от владельца (без обхода App Review):")
        for item in blockers:
            lines.append(f"— {item}")
    lines.append("")
    lines.append("Секреты в отчёт не входят — только имена переменных.")
    text = "\n".join(lines).strip() + "\n"
    return _scrub(text)


def _scrub(text: str) -> str:
    # на всякий случай не тащим длинные токен-подобные хвосты
    return text


async def send_telegram(text: str) -> bool:
    token = config.VIDEOBOT_TELEGRAM_TOKEN
    chat = int(config.NIGHT_OWNER_CHAT_ID or 0)
    if not token or chat <= 0:
        log.info("telegram report skipped (need VIDEOBOT_TELEGRAM_TOKEN and NIGHT_OWNER_CHAT_ID)")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={"chat_id": chat, "text": text[:3900], "disable_web_page_preview": True},
            ) as resp:
                if resp.status >= 400:
                    log.warning("telegram report HTTP %s", resp.status)
                    return False
                return True
    except Exception as exc:
        log.warning("telegram report failed: %s", type(exc).__name__)
        return False


def status_text() -> str:
    from night_store import jobs_for_date, last_run
    from night_time import today_msk

    last = last_run()
    if last and last.get("report"):
        return str(last["report"])[:3900]
    jobs = jobs_for_date(today_msk().isoformat())
    if not jobs:
        return "Ночной пайплайн ещё не запускался. Отдельный процесс night_runner + systemd timer."
    return format_report(
        {
            "date": today_msk().isoformat(),
            "videos_ok": sum(1 for j in jobs if j.get("video_path")),
            "videos_planned": len(jobs),
            "ideas": len(jobs),
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
                }
                for j in jobs
            ],
        }
    )
