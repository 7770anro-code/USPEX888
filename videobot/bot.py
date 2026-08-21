#!/usr/bin/env python3
"""Telegram-бот: идея текстом -> готовое видео 15–20 сек."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message

import config
from pipeline import PipelineError, build_video, ensure_ffmpeg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("videobot")

BUSY = asyncio.Lock()

HELP = (
    "Пришли идею одним сообщением — соберу короткое видео ~15–20 сек.\n"
    "Пайплайн: Grok (сценарий) → ElevenLabs (голос) → Runway (клипы) → ffmpeg.\n"
    "Одно видео за раз, генерация обычно занимает несколько минут."
)


async def cmd_start(message: Message) -> None:
    await message.answer("VideoBot. " + HELP)


async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


async def on_text(message: Message) -> None:
    idea = (message.text or "").strip()
    if not idea or idea.startswith("/"):
        return
    if len(idea) < 8:
        await message.answer("Идея слишком короткая. Напиши 1–3 предложения.")
        return
    if BUSY.locked():
        await message.answer("Уже собираю другое видео. Напиши ещё раз, когда пришлю готовый ролик.")
        return

    async with BUSY:
        status = await message.answer("Принял идею. Начинаю сборку…")

        async def progress(text: str) -> None:
            try:
                await status.edit_text(text)
            except Exception:
                try:
                    await message.answer(text)
                except Exception:
                    log.warning("не смог обновить статус: %s", text)

        work = Path(config.WORK_DIR) / f"{message.chat.id}_{int(time.time())}"
        try:
            video_path, script = await build_video(idea, work, progress)
            caption = script.get("title") or "Готово"
            await message.answer_video(FSInputFile(video_path), caption=caption[:900])
            try:
                await status.edit_text("Готово — видео выше.")
            except Exception:
                pass
        except PipelineError as exc:
            log.warning("pipeline: %s | %s", exc.user_message, exc.detail)
            extra = f"\n\nДетали: {exc.detail}" if exc.detail and exc.detail != exc.user_message else ""
            await message.answer(exc.user_message + extra)
        except Exception as exc:
            log.exception("unhandled")
            await message.answer(f"Сборка упала: {type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(work, ignore_errors=True)


async def on_other(message: Message) -> None:
    await message.answer("Нужен текст идеи, не файл. Напиши сюжет своими словами.")


async def main() -> None:
    missing = config.missing_secrets()
    if missing:
        raise SystemExit("Нет секретов: " + ", ".join(missing))
    try:
        ensure_ffmpeg()
    except PipelineError as exc:
        raise SystemExit(exc.user_message) from exc
    Path(config.WORK_DIR).mkdir(parents=True, exist_ok=True)
    bot = Bot(token=config.VIDEOBOT_TELEGRAM_TOKEN)
    dp = Dispatcher()
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(on_text, F.text)
    dp.message.register(on_other)
    log.info("VideoBot polling start")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
