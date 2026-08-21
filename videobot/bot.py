#!/usr/bin/env python3
"""Telegram-бот: идея текстом -> готовое видео 20–30 сек."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections import defaultdict
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from pipeline import (
    RATIO_PRESETS,
    STYLES,
    PipelineError,
    build_video,
    ensure_ffmpeg,
    format_script,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("videobot")

BUSY = asyncio.Lock()
SETTINGS: dict[int, dict[str, str]] = defaultdict(
    lambda: {
        "ratio": config.RUNWAY_RATIO or RATIO_PRESETS["9:16"],
        "style": config.DEFAULT_STYLE or "cinematic",
    }
)

HELP = (
    "Пришли идею одним сообщением — соберу ролик ~20–30 сек (2–3 сцены по 10 сек).\n"
    "Grok пишет сценарий, ElevenLabs озвучивает, Runway снимает, ffmpeg склеивает и жжёт субтитры.\n"
    "Команды: /ratio — 9:16 или 16:9, /style — cinematic / ad / cartoon.\n"
    "Одно видео за раз, обычно несколько минут."
)


def _chat_settings(chat_id: int) -> dict[str, str]:
    return SETTINGS[chat_id]


def settings_kb(chat_id: int) -> InlineKeyboardMarkup:
    s = _chat_settings(chat_id)
    ratio_label = next((k for k, v in RATIO_PRESETS.items() if v == s["ratio"]), "9:16")
    style = s["style"]

    def mark(current: str, value: str, label: str) -> str:
        return f"• {label}" if current == value else label

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=mark(ratio_label, "9:16", "9:16"), callback_data="ratio:9:16"),
                InlineKeyboardButton(text=mark(ratio_label, "16:9", "16:9"), callback_data="ratio:16:9"),
            ],
            [
                InlineKeyboardButton(text=mark(style, "cinematic", "кино"), callback_data="style:cinematic"),
                InlineKeyboardButton(text=mark(style, "ad", "реклама"), callback_data="style:ad"),
                InlineKeyboardButton(text=mark(style, "cartoon", "мульт"), callback_data="style:cartoon"),
            ],
        ]
    )


async def cmd_start(message: Message) -> None:
    await message.answer("VideoBot. " + HELP, reply_markup=settings_kb(message.chat.id))


async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=settings_kb(message.chat.id))


async def cmd_ratio(message: Message) -> None:
    await message.answer("Формат кадра:", reply_markup=settings_kb(message.chat.id))


async def cmd_style(message: Message) -> None:
    await message.answer("Стиль ролика:", reply_markup=settings_kb(message.chat.id))


async def on_callback(query: CallbackQuery) -> None:
    data = query.data or ""
    chat_id = query.message.chat.id if query.message else 0
    s = _chat_settings(chat_id)
    if data.startswith("ratio:"):
        key = data.split(":", 1)[1]
        if key in RATIO_PRESETS:
            s["ratio"] = RATIO_PRESETS[key]
    elif data.startswith("style:"):
        key = data.split(":", 1)[1]
        if key in STYLES:
            s["style"] = key
    try:
        await query.answer("Ок")
    except Exception:
        pass
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=settings_kb(chat_id))
        except Exception:
            pass


async def _send_video(message: Message, path: Path, caption: str) -> None:
    last: Exception | None = None
    for attempt in range(3):
        try:
            await message.answer_video(FSInputFile(path), caption=caption[:900])
            return
        except Exception as exc:
            last = exc
            log.warning("send_video attempt %s: %s", attempt + 1, exc)
            await asyncio.sleep(1.5 * (attempt + 1))
    try:
        await message.answer_document(FSInputFile(path), caption=caption[:900])
        return
    except Exception as exc:
        last = exc
    raise PipelineError("Не смог отправить mp4 в Telegram.", str(last or ""))


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

    s = _chat_settings(message.chat.id)
    async with BUSY:
        status = await message.answer(
            f"Принял идею. Формат {s['ratio']}, стиль {s['style']}. Начинаю…"
        )

        async def progress(text: str) -> None:
            try:
                await status.edit_text(text)
            except Exception:
                try:
                    await message.answer(text)
                except Exception:
                    log.warning("не смог обновить статус: %s", text)

        work = Path(config.WORK_DIR) / f"{message.chat.id}_{int(time.time())}"
        ok = False
        try:
            video_path, script = await build_video(
                idea,
                work,
                progress,
                ratio=s["ratio"],
                style=s["style"],
            )
            preview = format_script(script)
            try:
                await message.answer(preview[:3500])
            except Exception:
                pass
            caption = script.get("title") or "Готово"
            await _send_video(message, video_path, caption)
            try:
                await status.edit_text("Готово — видео выше.")
            except Exception:
                pass
            ok = True
        except PipelineError as exc:
            log.warning("pipeline: %s | %s", exc.user_message, exc.detail)
            extra = f"\n\nДетали: {exc.detail}" if exc.detail and exc.detail != exc.user_message else ""
            await message.answer(exc.user_message + extra)
        except Exception as exc:
            log.exception("unhandled")
            await message.answer(f"Сборка упала: {type(exc).__name__}: {exc}")
        finally:
            if ok or not config.KEEP_FAILED_DIR:
                shutil.rmtree(work, ignore_errors=True)
            else:
                log.warning("оставил рабочие файлы: %s", work)


async def on_other(message: Message) -> None:
    await message.answer("Нужен текст идеи, не файл. Напиши сюжет своими словами.")


async def main() -> None:
    if config.XAI_API_KEY_ERROR:
        log.error("%s", config.XAI_API_KEY_ERROR)
        raise SystemExit(config.XAI_API_KEY_ERROR)
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
    dp.message.register(cmd_ratio, Command("ratio"))
    dp.message.register(cmd_style, Command("style"))
    dp.callback_query.register(on_callback)
    dp.message.register(on_text, F.text)
    dp.message.register(on_other)
    log.info("VideoBot polling start")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
