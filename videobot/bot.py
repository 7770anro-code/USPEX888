#!/usr/bin/env python3
"""Telegram-бот: идея или свой сценарий → вертикальный ролик 30–60 сек."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from pipeline import (
    PipelineError,
    build_video,
    ensure_ffmpeg,
    file_to_data_uri,
    format_script,
    script_too_long_for_custom,
    target_scene_count,
)
from presets import (
    CAMERA,
    DELIVERY,
    MOTION,
    PRESETS,
    QUALITY,
    SPEED,
    apply_preset,
    camera_prompt,
    default_job,
    estimate_cost,
    motion_prompt,
    voice_settings_payload,
)
from store import (
    clear_user_voices,
    delete_cloned_voice,
    get_cloned_voice,
    get_last_title,
    get_last_video,
    get_watermark,
    init_db,
    load_user_voices,
    save_last_video,
    save_user_voice,
    set_cloned_voice,
    set_watermark,
)
from voices import catalog_for, voice_by_index, voice_label
from wave2 import (
    CLONE_CONSENT_MSG,
    act_two_payload,
    clone_voice,
    create_designed_voice,
    delete_eleven_voice,
    design_voice_previews,
    extend_video_payload,
    image_upscale_payload,
    runway_generate_file,
    runway_upload,
    speech_to_speech,
    video_upscale_payload,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("videobot")

BUSY = asyncio.Lock()
TIKTOK_RATIO = "720:1280"
CONSENT_REQUIRED_MSG = (
    "Без кнопки согласия я это фото не использую. Нажми /start и подтверди согласие."
)
PHOTO_CONSENT_PROMPT = (
    "Если на фото живой узнаваемый человек, мне нужно твоё явное согласие.\n\n"
    "Подтверждаю, что это моё фото или у меня есть согласие человека.\n"
    "Без этой кнопки я ролик не сниму."
)


def photo_start_blocked(photo_file_id: str | None, consent_verified: bool) -> str:
    if photo_file_id and not consent_verified:
        return CONSENT_REQUIRED_MSG
    return ""


HOW_IT_WORKS = (
    "Как это работает — совсем просто:\n\n"
    "1) Тема, готовый текст или пресет.\n"
    "2) Можно своё фото — лицо в ролике будет как на фото.\n"
    "3) «Оживить фото» — фото + короткое видео мимики (Act Two).\n"
    "4) Можно клонировать свой голос — отдельное согласие, не то же, что на фото.\n"
    "5) Подача, скорость, качество, камера, водяной знак — кнопками.\n"
    "6) Сначала оценка кредитов Runway, потом съёмка. После ролика — «Улучшить качество».\n\n"
    "⚠️ Фото живого человека — только своё или с согласия. "
    "Без кнопки «Подтверждаю: моё фото / есть согласие» я фото не использую. "
    "Клон голоса — отдельная кнопка «Разрешаю клонировать голос»."
)


class Flow(StatesGroup):
    quick_idea = State()
    preset_topic = State()
    custom_script = State()
    custom_photo = State()
    custom_consent = State()
    custom_voice = State()
    tune = State()
    confirm = State()
    w2_design_text = State()
    w2_design_pick = State()
    w2_clone_consent = State()
    w2_clone_audio = State()
    w2_sts_voice = State()
    w2_sts_audio = State()
    w2_upscale = State()
    act_photo = State()
    w2_act_photo = State()
    w2_act_video = State()
    w2_extend_video = State()
    w2_extend_prompt = State()


def _extra_voices(chat_id: int | None) -> list[dict[str, str]]:
    if not chat_id:
        return []
    return load_user_voices(int(chat_id))


def _voices_kb(message: Message | None, page: int = 0) -> InlineKeyboardMarkup:
    chat_id = message.chat.id if message else None
    return voice_kb(page, _extra_voices(chat_id))


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡️ Видео за 1 клик", callback_data="menu:quick")],
            [InlineKeyboardButton(text="🎬 Своё фото + текст + голос", callback_data="menu:custom")],
            [InlineKeyboardButton(text="🧟 Оживить фото", callback_data="menu:acttwo")],
            [InlineKeyboardButton(text="🎙 Клонировать мой голос", callback_data="menu:clone")],
            [InlineKeyboardButton(text="🗑 Удалить мой голос", callback_data="menu:unclone")],
            [InlineKeyboardButton(text="🎯 Пресеты", callback_data="menu:preset")],
            [InlineKeyboardButton(text="🧰 Ещё возможности", callback_data="menu:more")],
            [InlineKeyboardButton(text="❓ Как это работает", callback_data="menu:help")],
        ]
    )


def more_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧬 Голос по описанию", callback_data="more:design")],
            [InlineKeyboardButton(text="🎤 Переозвучить запись", callback_data="more:sts")],
            [InlineKeyboardButton(text="✨ Увеличить качество любого файла", callback_data="more:upscale")],
            [InlineKeyboardButton(text="▶️ Продолжить ролик", callback_data="more:extend")],
            [InlineKeyboardButton(text="🗑 Удалить все свои голоса", callback_data="more:forget")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
        ]
    )


def clone_consent_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Разрешаю клонировать голос",
                    callback_data="w2c:yes",
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="w2c:no")],
        ]
    )


def clone_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить мой голос", callback_data="menu:unclone")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
        ]
    )


def result_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Улучшить качество", callback_data="upscale:last")],
            [InlineKeyboardButton(text="🎬 Новый ролик", callback_data="menu:home")],
        ]
    )


def presets_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=p["label"], callback_data=f"preset:{pid}")]
        for pid, p in PRESETS.items()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def consent_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтверждаю: моё фото / есть согласие",
                    callback_data="consent:yes",
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="consent:no")],
        ]
    )


def photo_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить фото", callback_data="photo:skip")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
        ]
    )


def _mark(cur: str, key: str, label: str) -> str:
    return ("✓ " if cur == key else "") + label


def voice_kb(page: int = 0, extra: list[dict[str, str]] | None = None) -> InlineKeyboardMarkup:
    catalog = catalog_for(extra)
    per = 7
    start = page * per
    chunk = catalog[start : start + per]
    rows = []
    for i, v in enumerate(chunk):
        idx = start + i
        rows.append([InlineKeyboardButton(text=voice_label(v), callback_data=f"voice:{idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Ещё голоса", callback_data=f"vpage:{page - 1}"))
    if start + per < len(catalog):
        nav.append(InlineKeyboardButton(text="Ещё голоса ➡️", callback_data=f"vpage:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pair_rows(items: list[tuple[str, str]], prefix: str, current: str) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key, label in items:
        row.append(InlineKeyboardButton(text=_mark(current, key, label), callback_data=f"{prefix}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def tune_kb(job: dict[str, Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="Подача", callback_data="noop:x")])
    rows += _pair_rows([(k, v["label"]) for k, v in DELIVERY.items()], "deliv", job.get("delivery") or "sure")
    rows.append([InlineKeyboardButton(text="Скорость", callback_data="noop:x")])
    rows += _pair_rows([(k, v["label"]) for k, v in SPEED.items()], "speed", job.get("speed") or "norm")
    rows.append([InlineKeyboardButton(text="Качество", callback_data="noop:x")])
    rows += _pair_rows([(k, v["label"]) for k, v in QUALITY.items()], "qual", job.get("quality") or "optimal")
    rows.append([InlineKeyboardButton(text="Камера", callback_data="noop:x")])
    rows += _pair_rows([(k, v["label"]) for k, v in CAMERA.items()], "cam", job.get("camera") or "push")
    rows.append([InlineKeyboardButton(text="Движение", callback_data="noop:x")])
    rows += _pair_rows([(k, v["label"]) for k, v in MOTION.items()], "mot", job.get("motion") or "nat")
    wm_on = bool(job.get("watermark"))
    rows.append(
        [
            InlineKeyboardButton(
                text="✓ Водяной знак: вкл" if wm_on else "Водяной знак: выкл",
                callback_data="wm:off" if wm_on else "wm:on",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="➡️ К оценке стоимости", callback_data="tune:cost")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Создать", callback_data="job:go")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="job:edit")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="job:no")],
        ]
    )


def tune_text(job: dict[str, Any]) -> str:
    voice = voice_by_index(int(job.get("voice_idx") or 1))
    preset = PRESETS.get(job.get("preset_id") or "")
    lines = ["Настройки ролика — жми кнопки, цифры не нужны."]
    if preset:
        lines.append(f"Пресет: {preset['label']}")
    lines += [
        f"Голос: {job.get('voice_name') or voice['name']}",
        f"Подача: {(DELIVERY.get(job.get('delivery') or '') or DELIVERY['sure'])['label']}",
        f"Скорость: {(SPEED.get(job.get('speed') or '') or SPEED['norm'])['label']}",
        f"Качество: {(QUALITY.get(job.get('quality') or '') or QUALITY['optimal'])['label']}",
        f"Камера: {(CAMERA.get(job.get('camera') or '') or CAMERA['push'])['label']}",
        f"Движение: {(MOTION.get(job.get('motion') or '') or MOTION['nat'])['label']}",
        f"Водяной знак: {'вкл' if job.get('watermark') else 'выкл'}",
        f"Сцен: {int(job.get('n_scenes') or 5)}",
    ]
    return "\n".join(lines)


def cost_text(job: dict[str, Any]) -> str:
    idea = (job.get("idea") or "").strip()
    n = int(job.get("n_scenes") or target_scene_count(idea) or 5)
    need_still = not job.get("photo_file_id")
    est = estimate_cost(
        n_scenes=n,
        quality=str(job.get("quality") or "optimal"),
        text=idea,
        need_still=need_still,
    )
    return (
        "Примерная стоимость до запуска (кредиты не спишутся, пока не нажмёшь «Создать»):\n\n"
        + est["text"]
        + "\n\nЭто оценка по числу клипов и тарифу качества, не чек провайдера."
    )


async def _new_job(mode: str, user_id: int | None = None) -> dict[str, Any]:
    job = default_job(mode=mode)
    if user_id:
        job["watermark"] = get_watermark(int(user_id))
    return job


async def _job(state: FSMContext) -> dict[str, Any]:
    data = await state.get_data()
    job = data.get("job")
    if isinstance(job, dict):
        return job
    return default_job(mode="quick")


async def _save_job(state: FSMContext, job: dict[str, Any]) -> None:
    await state.update_data(job=job)


async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Я собираю вертикальное видео для TikTok.\n"
        "Нажми кнопку — я подскажу каждый шаг.",
        reply_markup=main_menu(),
    )


async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HOW_IT_WORKS, reply_markup=main_menu())


async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, отменил. Можно начать заново.", reply_markup=main_menu())


async def _start_clone_voice(msg: Message, state: FSMContext) -> None:
    await state.set_state(Flow.w2_clone_consent)
    await msg.answer(CLONE_CONSENT_MSG, reply_markup=clone_consent_kb())


async def _start_act_two(msg: Message, state: FSMContext) -> None:
    job = await _new_job("act_two", msg.chat.id)
    job["photo_file_id"] = None
    job["consent_verified"] = False
    await _save_job(state, job)
    await state.set_state(Flow.act_photo)
    await msg.answer(
        "🧟 Оживить фото (Act Two).\n\n"
        "Сначала пришли фото — то же согласие, что и для своего фото в ролике. "
        "Без кнопки подтверждения я это фото не использую.\n"
        "Потом короткое видео мимики и движений (3–30 сек)."
    )


async def _delete_cloned_voice(msg: Message) -> None:
    vid = delete_cloned_voice(msg.chat.id)
    if not vid:
        await msg.answer("Клонированного голоса пока нет.", reply_markup=main_menu())
        return
    async with aiohttp.ClientSession() as session:
        await delete_eleven_voice(session, vid)
    await msg.answer("Клон голоса удалён.", reply_markup=main_menu())


async def on_menu(query: CallbackQuery, state: FSMContext) -> None:
    data = query.data or ""
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    if data == "menu:home":
        await state.clear()
        await msg.answer("Главное меню:", reply_markup=main_menu())
        return
    if data == "menu:help":
        await msg.answer(HOW_IT_WORKS, reply_markup=main_menu())
        return
    if data == "menu:quick":
        await state.clear()
        job = await _new_job("quick", msg.chat.id)
        await _save_job(state, job)
        await state.set_state(Flow.quick_idea)
        await msg.answer(
            "⚡️ Напиши идею одним сообщением.\n"
            "Например: «утренний кофе на балконе, город просыпается»."
        )
        return
    if data == "menu:preset":
        await state.clear()
        await state.set_state(Flow.preset_topic)
        await msg.answer("Выбери пресет — ты пишешь только тему, остальное уже настроено:", reply_markup=presets_kb())
        return
    if data == "menu:custom":
        await state.clear()
        job = await _new_job("custom", msg.chat.id)
        await _save_job(state, job)
        await state.set_state(Flow.custom_script)
        await msg.answer(
            "🎬 Пришли готовый текст ролика.\n"
            "Это слова, которые зритель услышит. Можно абзацами — я разрежу на клипы.\n"
            "Ориентир: не длиннее ~230 слов, иначе озвучка не влезет в 6 клипов."
        )
        return
    if data == "menu:acttwo":
        await _start_act_two(msg, state)
        return
    if data == "menu:clone":
        await _start_clone_voice(msg, state)
        return
    if data == "menu:unclone":
        await _delete_cloned_voice(msg)
        return
    if data == "menu:more":
        await state.clear()
        await msg.answer(
            "Волна 2 — отдельные штуки на 1–2 запроса, без съёмки полного ролика.\n"
            "Две быстрые кнопки в главном меню как были.",
            reply_markup=more_kb(),
        )
        return


async def on_preset_pick(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    pid = (query.data or "preset:viral").split(":", 1)[-1]
    if pid not in PRESETS:
        pid = "viral"
    uid = query.message.chat.id if query.message else None
    job = apply_preset(await _new_job("preset", uid), pid)
    await _save_job(state, job)
    await state.set_state(Flow.preset_topic)
    p = PRESETS[pid]
    if query.message:
        await query.message.answer(
            f"Пресет «{p['label']}». Напиши тему одним сообщением.\n"
            "Я сам соберу хук, сцены, голос и финальный призыв."
        )


async def on_quick_idea(message: Message, state: FSMContext) -> None:
    idea = (message.text or "").strip()
    if len(idea) < 8:
        await message.answer("Напиши чуть больше — хотя бы одно предложение.")
        return
    job = await _job(state)
    job["idea"] = idea
    job["n_scenes"] = target_scene_count(idea)
    job["user_script"] = False
    await _save_job(state, job)
    await state.set_state(Flow.tune)
    await message.answer(tune_text(job), reply_markup=tune_kb(job))


async def on_preset_topic(message: Message, state: FSMContext) -> None:
    idea = (message.text or "").strip()
    if len(idea) < 8:
        await message.answer("Напиши тему чуть подробнее.")
        return
    job = await _job(state)
    if not job.get("preset_id"):
        await message.answer("Сначала выбери пресет кнопкой.", reply_markup=presets_kb())
        return
    job["idea"] = idea
    job["user_script"] = False
    await _save_job(state, job)
    await state.set_state(Flow.confirm)
    await message.answer(cost_text(job), reply_markup=confirm_kb())


async def on_custom_script(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer("Текста маловато. Напиши речь на 20–40 секунд своими словами.")
        return
    if script_too_long_for_custom(text):
        await message.answer(
            "Текст слишком длинный для ролика 30–60 сек: озвучка не влезет в 6 клипов "
            "по 10 секунд даже с ускорением, последние слова обрежутся.\n"
            "Сократи примерно до 230–250 слов и пришли снова."
        )
        return
    job = await _job(state)
    job["idea"] = text
    job["user_script"] = True
    job["n_scenes"] = target_scene_count(text)
    job["photo_file_id"] = None
    job["consent_verified"] = False
    await _save_job(state, job)
    await state.set_state(Flow.custom_photo)
    await message.answer(
        "Теперь фото — если хочешь своё лицо в ролике, пришли его сюда.\n"
        "Это будет первый кадр каждого клипа, чтобы лицо не прыгало.\n"
        "Если фото не нужно — нажми «Пропустить».",
        reply_markup=photo_skip_kb(),
    )


async def _maybe_start_consent(message: Message, state: FSMContext, file_id: str) -> None:
    job = await _job(state)
    job["photo_file_id"] = file_id
    job["consent_verified"] = False
    await _save_job(state, job)
    await state.set_state(Flow.custom_consent)
    await message.answer(
        PHOTO_CONSENT_PROMPT,
        reply_markup=consent_kb(),
    )


async def on_custom_photo(message: Message, state: FSMContext) -> None:
    photos = message.photo or []
    if photos:
        await _maybe_start_consent(message, state, photos[-1].file_id)
        return
    doc = message.document
    mime = (doc.mime_type or "") if doc else ""
    if doc and mime.startswith("image/"):
        await _maybe_start_consent(message, state, doc.file_id)
        return
    await message.answer(
        "Пришли именно фото (картинкой или файлом-изображением), или нажми «Пропустить».",
        reply_markup=photo_skip_kb(),
    )


async def on_photo_skip(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    if await state.get_state() != Flow.custom_photo.state:
        if query.message:
            await query.message.answer("Эта кнопка уже не действует. Нажми /start.", reply_markup=main_menu())
        return
    job = await _job(state)
    job["photo_file_id"] = None
    job["consent_verified"] = False
    await _save_job(state, job)
    await state.set_state(Flow.custom_voice)
    if query.message:
        await query.message.answer("Выбери голос:", reply_markup=_voices_kb(query.message, 0))


async def on_consent(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    if await state.get_state() != Flow.custom_consent.state:
        if query.message:
            await query.message.answer("Сначала пришли фото и нажми согласие заново. /start", reply_markup=main_menu())
        return
    if (query.data or "") == "consent:no":
        await state.clear()
        if query.message:
            await query.message.answer("Ок, без фото не продолжаю. Можно начать заново.", reply_markup=main_menu())
        return
    job = await _job(state)
    if not job.get("photo_file_id"):
        await state.clear()
        if query.message:
            await query.message.answer("Фото не нашёл. Нажми /start и пришли его снова.", reply_markup=main_menu())
        return
    job["consent_verified"] = True
    await _save_job(state, job)
    if job.get("mode") == "act_two":
        await state.set_state(Flow.w2_act_video)
        if query.message:
            await query.message.answer(
                "Теперь короткое видео 3–30 сек, где есть мимика и движения — "
                "по нему оживим фото."
            )
        return
    await state.set_state(Flow.custom_voice)
    if query.message:
        await query.message.answer("Спасибо. Теперь выбери голос:", reply_markup=_voices_kb(query.message, 0))


async def on_voice_page(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    st = await state.get_state()
    if st not in (Flow.custom_voice.state, Flow.w2_sts_voice.state):
        return
    try:
        page = int((query.data or "vpage:0").split(":")[1])
    except (IndexError, ValueError):
        page = 0
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=_voices_kb(query.message, page))
        except Exception:
            await query.message.answer("Выбери голос:", reply_markup=_voices_kb(query.message, page))


async def on_voice_pick(query: CallbackQuery, state: FSMContext) -> None:
    st = await state.get_state()
    extra = _extra_voices(query.message.chat.id if query.message else None)
    try:
        idx = int((query.data or "voice:1").split(":")[1])
    except (IndexError, ValueError):
        idx = 1
    picked = voice_by_index(idx, extra)
    if st == Flow.w2_sts_voice.state:
        try:
            await query.answer("Голос выбран")
        except Exception:
            pass
        job = await _job(state)
        job["voice_id"] = picked["id"]
        job["voice_name"] = picked["name"]
        await _save_job(state, job)
        await state.set_state(Flow.w2_sts_audio)
        if query.message:
            await query.message.answer("Пришли голосовое или аудиофайл — переозвучу этим голосом.")
        return
    if st != Flow.custom_voice.state:
        try:
            await query.answer("Сначала пройди шаги с /start — иначе согласие на фото не считается.")
        except Exception:
            pass
        return
    try:
        await query.answer("Голос выбран")
    except Exception:
        pass
    job = await _job(state)
    job["voice_idx"] = idx
    job["voice_id"] = picked["id"]
    job["voice_name"] = picked["name"]
    blocked = photo_start_blocked(job.get("photo_file_id"), bool(job.get("consent_verified")))
    if blocked:
        if query.message:
            await query.message.answer(blocked, reply_markup=main_menu())
        return
    await _save_job(state, job)
    await state.set_state(Flow.tune)
    if query.message:
        await query.message.answer(tune_text(job), reply_markup=tune_kb(job))


async def on_noop(query: CallbackQuery) -> None:
    try:
        await query.answer()
    except Exception:
        pass


async def on_tune(query: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != Flow.tune.state:
        try:
            await query.answer("Эта кнопка уже не действует.")
        except Exception:
            pass
        return
    data = query.data or ""
    kind, _, key = data.partition(":")
    job = await _job(state)
    catalogs = {"deliv": DELIVERY, "speed": SPEED, "qual": QUALITY, "cam": CAMERA, "mot": MOTION}
    fields = {"deliv": "delivery", "speed": "speed", "qual": "quality", "cam": "camera", "mot": "motion"}
    try:
        await query.answer()
    except Exception:
        pass
    if kind == "tune" and key == "cost":
        await _save_job(state, job)
        await state.set_state(Flow.confirm)
        if query.message:
            await query.message.answer(cost_text(job), reply_markup=confirm_kb())
        return
    if kind == "wm" and key in ("on", "off"):
        job["watermark"] = key == "on"
        if query.from_user:
            set_watermark(query.from_user.id, bool(job["watermark"]))
        await _save_job(state, job)
        if query.message:
            try:
                await query.message.edit_text(tune_text(job), reply_markup=tune_kb(job))
            except Exception:
                await query.message.answer(tune_text(job), reply_markup=tune_kb(job))
        return
    catalog = catalogs.get(kind)
    field = fields.get(kind)
    if catalog and field and key in catalog:
        job[field] = key
        await _save_job(state, job)
        if query.message:
            try:
                await query.message.edit_text(tune_text(job), reply_markup=tune_kb(job))
            except Exception:
                await query.message.answer(tune_text(job), reply_markup=tune_kb(job))


async def on_job(query: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != Flow.confirm.state:
        try:
            await query.answer("Сначала подтверди стоимость.")
        except Exception:
            pass
        return
    data = query.data or ""
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if data == "job:no":
        await state.clear()
        if msg:
            await msg.answer("Ок, кредиты не тратим.", reply_markup=main_menu())
        return
    job = await _job(state)
    if data == "job:edit":
        await state.set_state(Flow.tune)
        if msg:
            await msg.answer(tune_text(job), reply_markup=tune_kb(job))
        return
    blocked = photo_start_blocked(job.get("photo_file_id"), bool(job.get("consent_verified")))
    if blocked:
        await state.clear()
        if msg:
            await msg.answer(blocked, reply_markup=main_menu())
        return
    await state.clear()
    if not isinstance(msg, Message) or not (job.get("idea") or "").strip():
        if msg:
            await msg.answer("Что-то потерялось. Нажми /start.", reply_markup=main_menu())
        return
    extra = _extra_voices(msg.chat.id)
    voice = voice_by_index(int(job.get("voice_idx") or 1), extra)
    if job.get("voice_id"):
        voice = {"id": str(job["voice_id"]), "name": str(job.get("voice_name") or voice["name"])}
    await _run_job(
        msg,
        idea=str(job["idea"]),
        user_script=bool(job.get("user_script")),
        voice_id=voice["id"],
        photo_file_id=job.get("photo_file_id") if job.get("consent_verified") else None,
        consent_verified=bool(job.get("consent_verified")),
        bot=msg.bot,
        voice_name=voice["name"],
        n_scenes=int(job.get("n_scenes") or 5),
        extra_brief=str(job.get("brief") or ""),
        voice_settings=voice_settings_payload(str(job.get("delivery") or "sure"), str(job.get("speed") or "norm")),
        camera=camera_prompt(str(job.get("camera") or "push")),
        motion=motion_prompt(str(job.get("motion") or "nat")),
        quality=str(job.get("quality") or "optimal"),
        style=str(job.get("style") or "cinematic"),
        watermark=bool(job.get("watermark")),
    )


async def _download_photo(bot: Bot, file_id: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, destination=dest)
    return dest


def tiktok_upload_filename(title: str = "") -> str:
    slug = re.sub(r"[^\w]+", "_", (title or "").strip(), flags=re.UNICODE)
    slug = re.sub(r"_+", "_", slug).strip("_")[:48]
    return f"{slug or 'video'}_tiktok.mp4"


async def _send_video(message: Message, path: Path, caption: str, *, filename: str = "") -> None:
    name = filename or tiktok_upload_filename(path.stem)
    last: Exception | None = None
    video_ok = False
    for attempt in range(3):
        try:
            await message.answer_video(FSInputFile(path, filename=name), caption=caption[:900])
            video_ok = True
            break
        except Exception as exc:
            last = exc
            log.warning("send_video attempt %s: %s", attempt + 1, exc)
            await asyncio.sleep(1.5 * (attempt + 1))
    if not video_ok:
        try:
            await message.answer_document(FSInputFile(path, filename=name), caption=caption[:900])
            return
        except Exception as exc:
            last = exc
        raise PipelineError("Не смог отправить готовое видео. Нажми /start и попробуй ещё раз.", str(last or ""))
    try:
        await message.answer_document(
            FSInputFile(path, filename=name),
            caption="Файл в полном качестве для загрузки в TikTok",
        )
    except Exception as exc:
        log.warning("send_document extra: %s", exc)


async def _run_job(
    message: Message,
    *,
    idea: str,
    user_script: bool,
    voice_id: str | None,
    photo_file_id: str | None,
    bot: Bot,
    voice_name: str = "Сара",
    consent_verified: bool = False,
    n_scenes: int = 5,
    extra_brief: str = "",
    voice_settings: dict[str, Any] | None = None,
    camera: str = "",
    motion: str = "",
    quality: str = "optimal",
    style: str = "cinematic",
    watermark: bool = False,
) -> None:
    blocked = photo_start_blocked(photo_file_id, consent_verified)
    if blocked:
        await message.answer(blocked, reply_markup=main_menu())
        return
    if BUSY.locked():
        await message.answer(
            "⏳ Я уже снимаю другой ролик. Напиши ещё раз, когда пришлю готовое видео.",
            reply_markup=main_menu(),
        )
        return
    await BUSY.acquire()
    ok = False
    work = Path(config.WORK_DIR) / f"{message.chat.id}_{int(time.time())}"
    try:
        status = await message.answer("▓░░░░░░░░░ 0%\nПоехали")

        async def progress(text: str) -> None:
            try:
                await status.edit_text(text)
            except Exception:
                try:
                    await message.answer(text)
                except Exception:
                    log.warning("status: %s", text)

        try:
            photo_path = None
            if photo_file_id and consent_verified:
                photo_path = work / "user_photo.jpg"
                work.mkdir(parents=True, exist_ok=True)
                await _download_photo(bot, photo_file_id, photo_path)
            video_path, script = await build_video(
                idea,
                work,
                progress,
                ratio=TIKTOK_RATIO,
                style=style,
                voice_id=voice_id,
                reference_image=photo_path,
                user_script=user_script,
                n_scenes=n_scenes,
                extra_brief=extra_brief,
                voice_settings=voice_settings,
                camera=camera,
                motion=motion,
                quality=quality,
                watermark=watermark,
            )
            preview = format_script(script)
            try:
                await message.answer(preview[:3500])
            except Exception:
                pass
            q_label = (QUALITY.get(quality) or QUALITY["optimal"])["label"]
            caption = (script.get("title") or "Готово") + f" · {voice_name} · {q_label} · 9:16"
            title = str(script.get("title") or "video")
            keep = save_last_video(message.chat.id, video_path, title)
            await _send_video(
                message,
                keep,
                caption,
                filename=tiktok_upload_filename(title),
            )
            try:
                await status.edit_text("✅ Готово — видео выше.")
            except Exception:
                pass
            await message.answer(
                "Можно улучшить качество этого ролика.",
                reply_markup=result_kb(),
            )
            ok = True
        except PipelineError as exc:
            log.warning("pipeline: %s | %s", exc.user_message, exc.detail)
            await message.answer(exc.user_message, reply_markup=main_menu())
        except Exception:
            log.exception("unhandled")
            await message.answer(
                "Упс, что-то сломалось на моей стороне. Нажми /start и попробуй ещё раз.",
                reply_markup=main_menu(),
            )
        finally:
            if ok or not config.KEEP_FAILED_DIR:
                shutil.rmtree(work, ignore_errors=True)
            else:
                log.warning("оставил рабочие файлы: %s", work)
    finally:
        BUSY.release()


def _w2_work(message: Message) -> Path:
    path = Path(config.WORK_DIR) / f"w2_{message.chat.id}_{int(time.time())}"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _tg_download(bot: Bot, file_id: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, destination=dest)
    return dest


def _media_file_id(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return message.photo[-1].file_id, "jpg"
    if message.voice:
        return message.voice.file_id, "ogg"
    if message.audio:
        return message.audio.file_id, "mp3"
    if message.video_note:
        return message.video_note.file_id, "mp4"
    if message.video:
        return message.video.file_id, "mp4"
    doc = message.document
    if not doc:
        return None
    name = (doc.file_name or "file.bin").lower()
    mime = (doc.mime_type or "").lower()
    if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return doc.file_id, "jpg"
    if mime.startswith("audio/") or name.endswith((".mp3", ".wav", ".ogg", ".m4a")):
        return doc.file_id, "mp3"
    if mime.startswith("video/") or name.endswith((".mp4", ".mov", ".webm")):
        return doc.file_id, "mp4"
    return doc.file_id, "bin"


async def on_w2_menu(query: CallbackQuery, state: FSMContext) -> None:
    data = (query.data or "more:")[5:]
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    if data == "design":
        await state.set_state(Flow.w2_design_text)
        await msg.answer("Опиши голос своими словами: тембр, возраст, характер, акцент. Без имени знаменитости.")
        return
    if data == "clone":
        await _start_clone_voice(msg, state)
        return
    if data == "sts":
        await state.set_state(Flow.w2_sts_voice)
        await msg.answer("Сначала голос, которым переозвучить запись:", reply_markup=_voices_kb(msg, 0))
        return
    if data == "upscale":
        await state.set_state(Flow.w2_upscale)
        await msg.answer("Пришли фото или видео до 30 сек — увеличу детализацию.")
        return
    if data == "act":
        await _start_act_two(msg, state)
        return
    if data == "extend":
        await state.set_state(Flow.w2_extend_video)
        await msg.answer("Пришли вертикальный ролик до 30 сек — продолжу движение тем же кадром.")
        return
    if data == "forget":
        ids = clear_user_voices(msg.chat.id)
        async with aiohttp.ClientSession() as session:
            for vid in ids:
                await delete_eleven_voice(session, vid)
        await state.clear()
        await msg.answer("Свои голоса убрал.", reply_markup=main_menu())
        return
    await msg.answer("Выбери пункт:", reply_markup=more_kb())


async def on_w2_clone_consent(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    if await state.get_state() != Flow.w2_clone_consent.state:
        return
    msg = query.message
    if (query.data or "") == "w2c:no":
        await state.clear()
        if msg:
            await msg.answer("Ок, голос не клонирую.", reply_markup=main_menu())
        return
    job = await _job(state)
    job["w2_consent"] = True
    await _save_job(state, job)
    await state.set_state(Flow.w2_clone_audio)
    if msg:
        await msg.answer("Пришли голосовое 10–30 секунд чистой речи или аудиофайл.")


async def on_w2_design_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    status = await message.answer("⏳ Собираю 3 варианта голоса…")
    work = _w2_work(message)
    try:
        async with aiohttp.ClientSession() as session:
            previews = await design_voice_previews(session, text)
        job = await _job(state)
        job["w2_desc"] = text
        job["w2_previews"] = [{"generated_voice_id": p["generated_voice_id"]} for p in previews]
        await _save_job(state, job)
        rows = []
        for i, prev in enumerate(previews):
            audio = base64.b64decode(prev["audio_base_64"])
            await message.answer_voice(
                BufferedInputFile(audio, filename=f"preview{i + 1}.mp3"),
                caption=f"Вариант {i + 1}",
            )
            rows.append([InlineKeyboardButton(text=f"Взять вариант {i + 1}", callback_data=f"w2p:{i}")])
        rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
        await state.set_state(Flow.w2_design_pick)
        await status.edit_text("Послушай и выбери один:")
        await message.answer("Какой оставляем?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=more_kb())
    except Exception:
        log.exception("w2 design")
        await message.answer("Не собрал голос. Попробуй другое описание.", reply_markup=more_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def on_w2_design_pick(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    if await state.get_state() != Flow.w2_design_pick.state:
        return
    msg = query.message
    try:
        idx = int((query.data or "w2p:0").split(":")[1])
    except (IndexError, ValueError):
        idx = 0
    job = await _job(state)
    previews = job.get("w2_previews") or []
    if not (0 <= idx < len(previews)):
        if msg:
            await msg.answer("Этот вариант уже не действует. Нажми /start.")
        return
    gid = str(previews[idx].get("generated_voice_id") or "")
    desc = str(job.get("w2_desc") or "custom")
    try:
        async with aiohttp.ClientSession() as session:
            voice_id = await create_designed_voice(
                session,
                generated_voice_id=gid,
                name="Дизайн",
                description=desc,
            )
        if msg:
            save_user_voice(
                msg.chat.id,
                {"id": voice_id, "name": "Дизайн", "tag": "по описанию", "kind": "design"},
            )
        await state.clear()
        if msg:
            await msg.answer(
                "Голос сохранён — он будет в списке, когда снимаешь ролик или переозвучиваешь запись.",
                reply_markup=main_menu(),
            )
    except PipelineError as exc:
        if msg:
            await msg.answer(exc.user_message, reply_markup=more_kb())
    except Exception:
        log.exception("w2 design pick")
        if msg:
            await msg.answer("Не сохранил голос. Попробуй ещё раз.", reply_markup=more_kb())


async def on_w2_clone_audio(message: Message, state: FSMContext) -> None:
    job = await _job(state)
    if not job.get("w2_consent"):
        await message.answer(CLONE_CONSENT_MSG, reply_markup=clone_consent_kb())
        await state.set_state(Flow.w2_clone_consent)
        return
    media = _media_file_id(message)
    if not media or media[1] not in ("ogg", "mp3", "bin"):
        await message.answer("Нужно голосовое или аудиофайл.")
        return
    file_id, ext = media
    work = _w2_work(message)
    status = await message.answer("⏳ Клонирую голос…")
    try:
        src = await _tg_download(message.bot, file_id, work / f"clone.{ext}")
        old = get_cloned_voice(message.chat.id)
        async with aiohttp.ClientSession() as session:
            voice_id = await clone_voice(session, src, name="Мой голос")
            if old and old.get("id") and old["id"] != voice_id:
                await delete_eleven_voice(session, old["id"])
        set_cloned_voice(message.chat.id, voice_id, "Мой голос")
        await state.clear()
        await status.edit_text("Голос склонирован и сохранён.")
        await message.answer(
            "Клон привязан к вашему аккаунту. Он появится в списке голосов. "
            "Удалить можно кнопкой ниже.",
            reply_markup=clone_done_kb(),
        )
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=more_kb())
    except Exception:
        log.exception("w2 clone")
        await message.answer("Не клонировал. Пришли более чистое голосовое.", reply_markup=more_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def on_w2_sts_audio(message: Message, state: FSMContext) -> None:
    job = await _job(state)
    voice_id = str(job.get("voice_id") or "")
    if not voice_id:
        await message.answer("Сначала выбери голос кнопкой.", reply_markup=_voices_kb(message, 0))
        await state.set_state(Flow.w2_sts_voice)
        return
    media = _media_file_id(message)
    if not media or media[1] not in ("ogg", "mp3", "bin", "mp4"):
        await message.answer("Пришли голосовое или аудио.")
        return
    file_id, ext = media
    work = _w2_work(message)
    status = await message.answer("⏳ Переозвучиваю…")
    try:
        src = await _tg_download(message.bot, file_id, work / f"in.{ext}")
        dest = work / "out.mp3"
        async with aiohttp.ClientSession() as session:
            await speech_to_speech(session, src, voice_id, dest)
        await message.answer_audio(FSInputFile(dest, filename="restyle.mp3"), caption=job.get("voice_name") or "Голос")
        await state.clear()
        await status.edit_text("Готово.")
        await message.answer("Ещё что-нибудь?", reply_markup=main_menu())
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=more_kb())
    except Exception:
        log.exception("w2 sts")
        await message.answer("Не переозвучил. Попробуй другую запись.", reply_markup=more_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def on_w2_upscale(message: Message, state: FSMContext) -> None:
    media = _media_file_id(message)
    if not media:
        await message.answer("Пришли фото или видео.")
        return
    file_id, ext = media
    work = _w2_work(message)
    status = await message.answer("⏳ Увеличиваю качество…")
    try:
        src = await _tg_download(message.bot, file_id, work / f"in.{ext}")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)) as session:
            if ext == "jpg":
                uri = await file_to_data_uri(src, work / "ref.jpg")
                dest = work / "out.png"
                await runway_generate_file(
                    session,
                    "/v1/image_upscale",
                    image_upscale_payload(uri),
                    dest,
                    used_image=True,
                )
                await message.answer_document(FSInputFile(dest, filename="upscale.png"), caption="Увеличенное фото")
            else:
                uri = await runway_upload(session, src)
                dest = work / "out.mp4"
                await runway_generate_file(session, "/v1/video_upscale", video_upscale_payload(uri), dest)
                keep = save_last_video(message.chat.id, dest, "Увеличенное видео")
                await _send_video(message, keep, "Увеличенное видео", filename="upscale_tiktok.mp4")
        await state.clear()
        await status.edit_text("Готово.")
        await message.answer("Можно ещё раз улучшить или снять новый ролик.", reply_markup=result_kb())
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=more_kb())
    except Exception:
        log.exception("w2 upscale")
        await message.answer("Не увеличил. Пришли другой файл.", reply_markup=more_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def on_act_photo(message: Message, state: FSMContext) -> None:
    photos = message.photo or []
    file_id = photos[-1].file_id if photos else None
    doc = message.document
    if not file_id and doc and (doc.mime_type or "").startswith("image/"):
        file_id = doc.file_id
    if not file_id:
        await message.answer("Нужно фото — картинкой или файлом-изображением.")
        return
    job = await _job(state)
    job["mode"] = "act_two"
    job["photo_file_id"] = file_id
    job["consent_verified"] = False
    await _save_job(state, job)
    await state.set_state(Flow.custom_consent)
    await message.answer(PHOTO_CONSENT_PROMPT, reply_markup=consent_kb())


async def on_w2_act_video(message: Message, state: FSMContext) -> None:
    job = await _job(state)
    photo_id = str(job.get("photo_file_id") or job.get("act_photo_id") or "")
    blocked = photo_start_blocked(photo_id or None, bool(job.get("consent_verified")))
    if blocked:
        await message.answer(blocked, reply_markup=consent_kb())
        await state.set_state(Flow.custom_consent)
        return
    media = _media_file_id(message)
    if not media or media[1] != "mp4":
        await message.answer("Пришли видео-перформанс 3–30 секунд.")
        return
    if BUSY.locked():
        await message.answer("⏳ Я уже занят другим роликом. Подожди, пока пришлю готовое.")
        return
    work = _w2_work(message)
    status = await message.answer("⏳ Оживляю фото…")
    await BUSY.acquire()
    try:
        photo = await _tg_download(message.bot, photo_id, work / "face.jpg")
        video = await _tg_download(message.bot, media[0], work / "perf.mp4")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)) as session:
            image_uri = await file_to_data_uri(photo, work / "face_ref.jpg")
            video_uri = await runway_upload(session, video)
            dest = work / "act.mp4"
            await runway_generate_file(
                session,
                "/v1/character_performance",
                act_two_payload(image_uri, video_uri),
                dest,
                used_image=True,
            )
            keep = save_last_video(message.chat.id, dest, "Оживлённое фото")
            await _send_video(message, keep, "Оживлённое фото", filename="act_tiktok.mp4")
        await state.clear()
        await status.edit_text("Готово.")
        await message.answer("Можно улучшить качество этого ролика.", reply_markup=result_kb())
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=main_menu())
    except Exception:
        log.exception("w2 act")
        await message.answer("Не оживил. Другое фото или более короткое видео.", reply_markup=main_menu())
    finally:
        shutil.rmtree(work, ignore_errors=True)
        BUSY.release()


async def on_w2_extend_video(message: Message, state: FSMContext) -> None:
    media = _media_file_id(message)
    if not media or media[1] != "mp4":
        await message.answer("Пришли видеофайлом или как ролик.")
        return
    job = await _job(state)
    job["extend_file_id"] = media[0]
    await _save_job(state, job)
    await state.set_state(Flow.w2_extend_prompt)
    await message.answer(
        "Коротко напиши, что должно произойти дальше (одно предложение).\n"
        "Или отправь «дальше» — продолжу мягко то же движение."
    )


async def on_w2_extend_prompt(message: Message, state: FSMContext) -> None:
    job = await _job(state)
    file_id = str(job.get("extend_file_id") or "")
    if not file_id:
        await message.answer("Сначала пришли видео.", reply_markup=more_kb())
        return
    prompt = (message.text or "").strip()
    work = _w2_work(message)
    status = await message.answer("⏳ Дописываю ролик…")
    try:
        src = await _tg_download(message.bot, file_id, work / "in.mp4")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)) as session:
            uri = await runway_upload(session, src)
            dest = work / "ext.mp4"
            await runway_generate_file(session, "/v1/video_to_video", extend_video_payload(uri, prompt), dest)
            await _send_video(message, dest, "Продолжение ролика", filename="extend_tiktok.mp4")
        keep = save_last_video(message.chat.id, dest, "Продолжение")
        await state.clear()
        await status.edit_text("Готово.")
        await message.answer("Можно улучшить качество этого ролика.", reply_markup=result_kb())
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=more_kb())
    except Exception:
        log.exception("w2 extend")
        await message.answer("Не продолжил ролик. Попробуй более короткий файл.", reply_markup=more_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)


async def on_upscale_last(query: CallbackQuery) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    src = get_last_video(msg.chat.id)
    if not src:
        await msg.answer("Нет готового ролика для улучшения. Сначала сними видео.", reply_markup=main_menu())
        return
    if BUSY.locked():
        await msg.answer("⏳ Я уже занят. Подожди, пока пришлю результат.")
        return
    title = get_last_title(msg.chat.id) or "video"
    work = _w2_work(msg)
    status = await msg.answer("⏳ Улучшаю качество готового ролика…")
    await BUSY.acquire()
    try:
        local = work / "final.mp4"
        shutil.copyfile(src, local)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)) as session:
            uri = await runway_upload(session, local)
            dest = work / "upscale.mp4"
            await runway_generate_file(session, "/v1/video_upscale", video_upscale_payload(uri), dest)
            keep = save_last_video(msg.chat.id, dest, title)
            await _send_video(
                msg,
                keep,
                "Улучшенное качество",
                filename=tiktok_upload_filename(title),
            )
        await status.edit_text("Готово — улучшенный файл выше.")
        await msg.answer("Можно улучшить ещё раз или снять новый ролик.", reply_markup=result_kb())
    except PipelineError as exc:
        await msg.answer(exc.user_message, reply_markup=result_kb())
    except Exception:
        log.exception("upscale last")
        await msg.answer("Не получилось улучшить. Попробуй ещё раз чуть позже.", reply_markup=result_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)
        BUSY.release()


async def on_stale_callback(query: CallbackQuery) -> None:
    try:
        await query.answer("Эта кнопка уже не действует. Нажми /start.")
    except Exception:
        pass


async def on_plain_text(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current:
        return
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        await message.answer("Нажми кнопку в меню — так проще.", reply_markup=main_menu())
        return
    await message.answer("Выбери, как снимаем:", reply_markup=main_menu())


async def on_other(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current == Flow.custom_photo.state:
        await on_custom_photo(message, state)
        return
    if current == Flow.act_photo.state:
        await on_act_photo(message, state)
        return
    if current == Flow.w2_clone_audio.state:
        await on_w2_clone_audio(message, state)
        return
    if current == Flow.w2_sts_audio.state:
        await on_w2_sts_audio(message, state)
        return
    if current == Flow.w2_upscale.state:
        await on_w2_upscale(message, state)
        return
    if current == Flow.w2_act_photo.state:
        await on_act_photo(message, state)
        return
    if current == Flow.w2_act_video.state:
        await on_w2_act_video(message, state)
        return
    if current == Flow.w2_extend_video.state:
        await on_w2_extend_video(message, state)
        return
    await message.answer("Нажми кнопку в меню или пришли текст, когда я попрошу.", reply_markup=main_menu())


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
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    init_db()
    bot = Bot(token=config.VIDEOBOT_TELEGRAM_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_cancel, Command("cancel"))
    dp.callback_query.register(on_menu, F.data.startswith("menu:"))
    dp.callback_query.register(on_preset_pick, Flow.preset_topic, F.data.startswith("preset:"))
    dp.callback_query.register(on_photo_skip, Flow.custom_photo, F.data == "photo:skip")
    dp.callback_query.register(on_consent, Flow.custom_consent, F.data.startswith("consent:"))
    dp.callback_query.register(on_voice_page, Flow.custom_voice, F.data.startswith("vpage:"))
    dp.callback_query.register(on_voice_pick, Flow.custom_voice, F.data.startswith("voice:"))
    dp.callback_query.register(on_voice_page, Flow.w2_sts_voice, F.data.startswith("vpage:"))
    dp.callback_query.register(on_voice_pick, Flow.w2_sts_voice, F.data.startswith("voice:"))
    dp.callback_query.register(on_w2_menu, F.data.startswith("more:"))
    dp.callback_query.register(on_w2_clone_consent, Flow.w2_clone_consent, F.data.startswith("w2c:"))
    dp.callback_query.register(on_w2_design_pick, Flow.w2_design_pick, F.data.startswith("w2p:"))
    dp.callback_query.register(on_noop, F.data.startswith("noop:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("deliv:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("speed:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("qual:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("cam:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("mot:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("wm:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("tune:"))
    dp.callback_query.register(on_job, Flow.confirm, F.data.startswith("job:"))
    dp.callback_query.register(on_upscale_last, F.data == "upscale:last")
    dp.message.register(on_quick_idea, Flow.quick_idea, F.text)
    dp.message.register(on_preset_topic, Flow.preset_topic, F.text)
    dp.message.register(on_custom_script, Flow.custom_script, F.text)
    dp.message.register(on_w2_design_text, Flow.w2_design_text, F.text)
    dp.message.register(on_w2_extend_prompt, Flow.w2_extend_prompt, F.text)
    dp.message.register(on_custom_photo, Flow.custom_photo, F.photo)
    dp.message.register(on_custom_photo, Flow.custom_photo, F.document)
    dp.message.register(on_act_photo, Flow.act_photo, F.photo)
    dp.message.register(on_act_photo, Flow.act_photo, F.document)
    dp.message.register(on_w2_clone_audio, Flow.w2_clone_audio)
    dp.message.register(on_w2_sts_audio, Flow.w2_sts_audio)
    dp.message.register(on_w2_upscale, Flow.w2_upscale)
    dp.message.register(on_act_photo, Flow.w2_act_photo)
    dp.message.register(on_w2_act_video, Flow.w2_act_video)
    dp.message.register(on_w2_extend_video, Flow.w2_extend_video)
    dp.message.register(on_plain_text, F.text)
    dp.message.register(on_other)
    dp.callback_query.register(on_stale_callback)
    log.info("VideoBot polling start")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
