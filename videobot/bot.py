#!/usr/bin/env python3
"""Telegram-бот: идея или свой сценарий → вертикальный ролик 30–60 сек."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
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
from voices import VOICES, voice_by_index, voice_label

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


def photo_start_blocked(photo_file_id: str | None, consent_verified: bool) -> str:
    if photo_file_id and not consent_verified:
        return CONSENT_REQUIRED_MSG
    return ""


HOW_IT_WORKS = (
    "Как это работает — совсем просто:\n\n"
    "1) Тема, готовый текст или пресет.\n"
    "2) Можно своё фото — лицо в ролике будет как на фото.\n"
    "3) Подача, скорость, качество, камера — кнопками, без технических названий.\n"
    "4) Сначала оценка кредитов, потом съёмка. Вертикаль 9:16, 30–60 секунд.\n\n"
    "⚠️ Фото живого человека — только своё или с согласия. "
    "Без кнопки «подтверждаю» я видео не начну. "
    "То же правило будет для клонирования голоса и оживления персонажа, когда это появится."
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


def _mark(cur: str, key: str, label: str) -> str:
    return ("✓ " if cur == key else "") + label


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡️ Видео за 1 клик", callback_data="menu:quick")],
            [InlineKeyboardButton(text="🎬 Своё фото + текст + голос", callback_data="menu:custom")],
            [InlineKeyboardButton(text="🎯 Пресеты", callback_data="menu:preset")],
            [InlineKeyboardButton(text="❓ Как это работает", callback_data="menu:help")],
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


def voice_kb(page: int = 0) -> InlineKeyboardMarkup:
    per = 7
    start = page * per
    chunk = VOICES[start : start + per]
    rows = []
    for i, v in enumerate(chunk):
        idx = start + i
        rows.append([InlineKeyboardButton(text=voice_label(v), callback_data=f"voice:{idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Ещё голоса", callback_data=f"vpage:{page - 1}"))
    if start + per < len(VOICES):
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
        f"Голос: {voice['name']}",
        f"Подача: {(DELIVERY.get(job.get('delivery') or '') or DELIVERY['sure'])['label']}",
        f"Скорость: {(SPEED.get(job.get('speed') or '') or SPEED['norm'])['label']}",
        f"Качество: {(QUALITY.get(job.get('quality') or '') or QUALITY['optimal'])['label']}",
        f"Камера: {(CAMERA.get(job.get('camera') or '') or CAMERA['push'])['label']}",
        f"Движение: {(MOTION.get(job.get('motion') or '') or MOTION['nat'])['label']}",
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
        job = default_job(mode="quick")
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
        job = default_job(mode="custom")
        await _save_job(state, job)
        await state.set_state(Flow.custom_script)
        await msg.answer(
            "🎬 Пришли готовый текст ролика.\n"
            "Это слова, которые зритель услышит. Можно абзацами — я разрежу на клипы.\n"
            "Ориентир: не длиннее ~230 слов, иначе озвучка не влезет в 6 клипов."
        )


async def on_preset_pick(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    pid = (query.data or "preset:viral").split(":", 1)[-1]
    if pid not in PRESETS:
        pid = "viral"
    job = apply_preset(default_job(mode="preset"), pid)
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
    await state.set_state(Flow.tune)
    await message.answer(tune_text(job), reply_markup=tune_kb(job))


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
        "Если на фото живой узнаваемый человек, мне нужно твоё явное согласие.\n\n"
        "Подтверждаю, что это моё фото или у меня есть согласие человека.\n"
        "Без этой кнопки я ролик не сниму.",
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
        await query.message.answer("Выбери голос:", reply_markup=voice_kb(0))


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
    await state.set_state(Flow.custom_voice)
    if query.message:
        await query.message.answer("Спасибо. Теперь выбери голос:", reply_markup=voice_kb(0))


async def on_voice_page(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    if await state.get_state() != Flow.custom_voice.state:
        return
    try:
        page = int((query.data or "vpage:0").split(":")[1])
    except (IndexError, ValueError):
        page = 0
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=voice_kb(page))
        except Exception:
            await query.message.answer("Выбери голос:", reply_markup=voice_kb(page))


async def on_voice_pick(query: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != Flow.custom_voice.state:
        try:
            await query.answer("Сначала пройди шаги с /start — иначе согласие на фото не считается.")
        except Exception:
            pass
        return
    try:
        await query.answer("Голос выбран")
    except Exception:
        pass
    try:
        idx = int((query.data or "voice:1").split(":")[1])
    except (IndexError, ValueError):
        idx = 1
    job = await _job(state)
    job["voice_idx"] = idx
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
    voice = voice_by_index(int(job.get("voice_idx") or 1))
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
    )


async def _download_photo(bot: Bot, file_id: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, destination=dest)
    return dest


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
    raise PipelineError("Не смог отправить готовое видео. Нажми /start и попробуй ещё раз.", str(last or ""))


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
            )
            preview = format_script(script)
            try:
                await message.answer(preview[:3500])
            except Exception:
                pass
            q_label = (QUALITY.get(quality) or QUALITY["optimal"])["label"]
            caption = (script.get("title") or "Готово") + f" · {voice_name} · {q_label} · 9:16"
            await _send_video(message, video_path, caption)
            try:
                await status.edit_text("✅ Готово — видео выше.")
            except Exception:
                pass
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
    dp.callback_query.register(on_noop, F.data.startswith("noop:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("deliv:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("speed:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("qual:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("cam:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("mot:"))
    dp.callback_query.register(on_tune, Flow.tune, F.data.startswith("tune:"))
    dp.callback_query.register(on_job, Flow.confirm, F.data.startswith("job:"))
    dp.message.register(on_quick_idea, Flow.quick_idea, F.text)
    dp.message.register(on_preset_topic, Flow.preset_topic, F.text)
    dp.message.register(on_custom_script, Flow.custom_script, F.text)
    dp.message.register(on_custom_photo, Flow.custom_photo, F.photo)
    dp.message.register(on_custom_photo, Flow.custom_photo, F.document)
    dp.message.register(on_plain_text, F.text)
    dp.message.register(on_other)
    dp.callback_query.register(on_stale_callback)
    log.info("VideoBot polling start")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
