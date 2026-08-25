"""Фоновый автоконтур внутри videobot.service: idea → video по интервалу.

Не systemd timer и не отдельный процесс. Интервал: NIGHT_INTERVAL_MINUTES.
"""

from __future__ import annotations

import asyncio
import logging

import config

log = logging.getLogger("videobot.night")


async def auto_pipeline_loop(busy: asyncio.Lock) -> None:
    delay = int(config.NIGHT_STARTUP_DELAY_SEC)
    interval = int(config.NIGHT_INTERVAL_MINUTES) * 60
    log.info(
        "автоконтур: старт через %s с, дальше каждые %s мин (batch=%s, дневной лимит=%s)",
        delay,
        config.NIGHT_INTERVAL_MINUTES,
        config.NIGHT_BATCH_PER_TICK,
        config.VIDEOS_PER_NIGHT,
    )
    await asyncio.sleep(delay)
    while True:
        if config.NIGHT_BACKGROUND:
            try:
                from night_runner import run_night

                await run_night(notify=True, busy=busy, idle_quiet=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("автоконтур: тик оборвался, следующий по расписанию")
        else:
            log.info("автоконтур выключен (NIGHT_BACKGROUND=0)")
        await asyncio.sleep(interval)


def start_auto_pipeline(busy: asyncio.Lock) -> asyncio.Task:
    return asyncio.create_task(auto_pipeline_loop(busy), name="videobot-auto")
