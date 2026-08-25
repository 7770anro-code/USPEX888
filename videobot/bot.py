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
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

import config
from pipeline import (
    PipelineError,
    RUNWAY_CREDITS_MSG,
    DYNAMIC_SCENE_COUNT,
    build_video,
    compact_runway_models,
    ensure_ffmpeg,
    fetch_runway_task,
    file_to_data_uri,
    format_runway_usage,
    format_script,
    is_runway_credits_fail,
    script_too_long_for_custom,
    target_scene_count,
)
from presets import (
    CAMERA,
    DELIVERY,
    MOTION,
    PRESETS,
    SPEED,
    apply_preset,
    camera_prompt,
    default_job,
    estimate_cost,
    motion_prompt,
    quality_catalog,
    voice_settings_payload,
)
from joblock import JobLock
from live_status import (
    allow_runway_get,
    finish_job,
    format_status,
    get_job,
    job_key_manual,
    job_scope,
    live_kb,
    note_fal_poll,
    parse_callback_key,
    set_message,
    start_job,
)
from edit import (
    MAX_CLIPS,
    MAX_INPUT_SEC,
    check_incoming,
    concat_videos,
    cut_video,
    format_clips,
    media_duration,
    parse_timecodes,
    plan_clips,
    render_clips,
    scenes_for_vibe,
    vibe_style,
    vibe_synth_brief,
)
from store import (
    clear_last_job,
    clear_user_voices,
    delete_cloned_voice,
    get_cloned_voice,
    get_last_job,
    get_last_title,
    get_last_video,
    get_watermark,
    init_db,
    load_user_voices,
    mark_last_job_final,
    save_last_job,
    save_last_video,
    save_user_voice,
    set_cloned_voice,
    set_watermark,
)
from voices import catalog_for, voice_by_index, voice_label
from resume_job import (
    credits_paused,
    format_resume_progress,
    load_checkpoint,
    mark_credits_pause,
    resume_work_dir,
    run_kwargs_from_checkpoint,
    save_checkpoint,
    wipe_resume,
)
from wave2 import (
    CLONE_CONSENT_MSG,
    act_two_payload,
    create_designed_voice,
    delete_eleven_voice,
    design_voice_previews,
    extend_video_payload,
    runway_generate_file,
    runway_upload,
    speech_to_speech,
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
    "1) Тема (хватит 2–3 слов — хук и сценарий соберу сам; в «1 клик» фото и голос по желанию), "
    "готовый текст или пресет.\n"
    "2) Можно своё фото — лицо в ролике будет как на фото. Без кнопки согласия фото не беру.\n"
    "3) «Оживить фото» — фото + короткое видео мимики (Act Two).\n"
    "4) Можно клонировать свой голос — отдельное согласие, не то же, что на фото.\n"
    "5) Подача, скорость, качество, камера, водяной знак — кнопками.\n"
    "6) Камера — Kling 3.0 Pro / Seedance 2.5 на fal.ai (списание в кабинете fal.ai, не кредиты Runway). "
    "После ролика можно править по кругу («Улучшить качество» / Topaz) и только кнопкой "
    "«Готово, это финал» зафиксировать.\n"
    "7) «Нарезка и монтаж»: вручную — ffmpeg без кредитов; авто по своему видео — план Grok + ffmpeg, Runway нет; "
    "авто «описать вайб» — оригинальная синтетика тем же пайплайном, что ночь "
    "(IDEA_SYSTEM → SCRIPT_SYSTEM_SYNTH → Kling/Seedance), чужие ролики не скачиваю.\n"
    "8) Мультсериал «Гибриды» (владелец): reveal-формат, серии продолжают сюжет, слот NIGHT_ACC4, пост да/нет в /night.\n"
    "9) «🎞 Авторолик»: до 6 фото друзей + согласие → 4–8 сцен хайпового монтажа UKRAINIAN CORE. "
    "FACE (крупный план друга) — Kling @ElementN; WIDE (дрон над городом, колонна в тумане, "
    "решётка фар, силуэты в контровом) — Seedance, лицо не главное, камеры больше. "
    "Сначала подтверждаешь или правишь сценарий в чате, потом съёмка.\n"
    "10) Меню Mini App: «🎬 Открыть меню» — семь категорий (создать / авторолик / монтаж / улучшить / "
    "примерка / мой голос / мои видео). Без HTTPS (WEBAPP_PUBLIC_URL) те же кнопки живут в обычном меню.\n\n"
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
    tryon_person = State()
    tryon_clothes = State()
    act_photo = State()
    w2_act_photo = State()
    w2_act_video = State()
    w2_extend_video = State()
    w2_extend_prompt = State()
    edit_cut_video = State()
    edit_cut_times = State()
    edit_concat = State()
    edit_auto_pick = State()
    edit_auto_video = State()
    edit_auto_brief = State()
    edit_auto_vibe = State()
    serial_note = State()
    revise_notes = State()
    auto_consent = State()
    auto_photos = State()
    auto_topic = State()
    auto_review = State()
    auto_edit = State()


def _extra_voices(chat_id: int | None) -> list[dict[str, str]]:
    if not chat_id:
        return []
    return load_user_voices(int(chat_id))


def _voices_kb(
    message: Message | None,
    page: int = 0,
    *,
    allow_skip: bool = False,
) -> InlineKeyboardMarkup:
    chat_id = message.chat.id if message else None
    kb = voice_kb(page, _extra_voices(chat_id))
    if not allow_skip:
        return kb
    rows = [list(row) for row in kb.inline_keyboard]
    rows.insert(
        0,
        [InlineKeyboardButton(text="Пропустить голос", callback_data="vskip:default")],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu() -> InlineKeyboardMarkup:
    studio_btn = InlineKeyboardButton(text="🎬 Открыть меню", callback_data="menu:studio")
    if config.WEBAPP_PUBLIC_URL:
        studio_btn = InlineKeyboardButton(
            text="🎬 Открыть меню",
            web_app=WebAppInfo(url=config.WEBAPP_PUBLIC_URL),
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡️ Видео за 1 клик", callback_data="menu:quick")],
            [InlineKeyboardButton(text="🎬 Своё фото + текст + голос", callback_data="menu:custom")],
            [InlineKeyboardButton(text="🎞 Авторолик", callback_data="menu:auto")],
            [InlineKeyboardButton(text="🧟 Оживить фото", callback_data="menu:acttwo")],
            [InlineKeyboardButton(text="🎙 Клонировать мой голос", callback_data="menu:clone")],
            [InlineKeyboardButton(text="🗑 Удалить мой голос", callback_data="menu:unclone")],
            [InlineKeyboardButton(text="🎯 Пресеты", callback_data="menu:preset")],
            [InlineKeyboardButton(text="✂️ Нарезка и монтаж", callback_data="menu:edit")],
            [InlineKeyboardButton(text="🧰 Ещё возможности", callback_data="menu:more")],
            [studio_btn],
            [InlineKeyboardButton(text="❓ Как это работает", callback_data="menu:help")],
        ]
    )


def more_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧬 Голос по описанию", callback_data="more:design")],
            [InlineKeyboardButton(text="🎤 Переозвучить запись", callback_data="more:sts")],
            [InlineKeyboardButton(text="✨ Увеличить качество любого файла", callback_data="more:upscale")],
            [InlineKeyboardButton(text="👗 Примерка одежды", callback_data="more:tryon")],
            [InlineKeyboardButton(text="▶️ Продолжить ролик", callback_data="more:extend")],
            [InlineKeyboardButton(text="📺 Мультсериал", callback_data="serial:hub")],
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


def edit_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✂️ Вручную: нарезать", callback_data="edit:cut")],
            [InlineKeyboardButton(text="📎 Вручную: склеить", callback_data="edit:concat")],
            [InlineKeyboardButton(text="🤖 Авто-монтаж по описанию", callback_data="edit:auto")],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
        ]
    )


def edit_auto_source_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Прислать своё видео", callback_data="edit:own")],
            [InlineKeyboardButton(text="✨ Описать вайб / тему", callback_data="edit:gen")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:edit")],
        ]
    )


def edit_concat_kb(n: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if n >= 2:
        rows.append([InlineKeyboardButton(text=f"✅ Склеить {n} клипов", callback_data="edit:go")])
    rows.append([InlineKeyboardButton(text="🗑 Сбросить список", callback_data="edit:reset")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


REVISE_ASK = (
    "Что именно не так? Напиши свободным текстом — например:\n"
    "• картинка мыльная, тёмная или не та камера\n"
    "• голос, темп, слишком коротко или длинно\n"
    "• конкретная сцена (вторая скучная, хук слабый)\n"
    "• другой сюжет / другой призыв к действию\n\n"
    "Пересниму ролик с учётом этого. Когда устроит — нажми «✅ Готово, это финал»."
)


def result_kb(*, can_finalize: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✨ Улучшить качество", callback_data="upscale:last")],
    ]
    if can_finalize:
        rows.append(
            [InlineKeyboardButton(text="✅ Готово, это финал", callback_data="revise:final")]
        )
    rows.append([InlineKeyboardButton(text="🎬 Новый ролик", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def compose_live_text(job_key: str) -> tuple[str, bool]:
    """Текст статуса. GET fal/Runway только по сохранённому task_id, с паузой ≥5 с."""
    snap = get_job(job_key)
    stale = False
    tid = str((snap or {}).get("runway_task_id") or "").strip()
    if snap and tid and not snap.get("done"):
        if allow_runway_get(tid):
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    from fal_api import is_fal_live_id

                    if is_fal_live_id(tid) and config.FAL_KEY:
                        from providers.fal_client import FalClient

                        status = await FalClient(session).poll_status(tid)
                        note_fal_poll(tid, status)
                    else:
                        await fetch_runway_task(session, tid)
            except Exception:
                log.warning("live status GET failed job=%s", job_key)
            snap = get_job(job_key)
        else:
            stale = True
    alive = bool(snap and not snap.get("done"))
    return format_status(snap, stale_runway=stale), alive


async def edit_live_message(status: Message, job_key: str, text: str, *, alive: bool) -> None:
    markup = live_kb(job_key) if alive else None
    try:
        await status.edit_text(text[:3900], reply_markup=markup)
    except Exception:
        try:
            await status.answer(text[:3900], reply_markup=markup)
        except Exception:
            log.warning("status: %s", text[:120])


async def on_live_refresh(query: CallbackQuery) -> None:
    key = parse_callback_key(query.data or "")
    if not key:
        try:
            await query.answer("Не понял кнопку статуса.")
        except Exception:
            pass
        return
    snap = get_job(key)
    if snap and int(snap.get("chat_id") or 0) and query.message:
        if int(query.message.chat.id) != int(snap["chat_id"]):
            try:
                await query.answer("Это статус другой съёмки.")
            except Exception:
                pass
            return
    text, alive = await compose_live_text(key)
    paused = False
    if query.message:
        paused = credits_paused(resume_work_dir(query.message.chat.id))
    if paused and not alive and query.message:
        try:
            await query.message.edit_text(
                credits_pause_text(query.message.chat.id),
                reply_markup=credits_pause_kb(key),
            )
        except Exception:
            await query.message.answer(
                credits_pause_text(query.message.chat.id),
                reply_markup=credits_pause_kb(key),
            )
    elif query.message:
        await edit_live_message(query.message, key, text, alive=alive)
    try:
        await query.answer("Обновил" if get_job(key) else "Съёмки нет")
    except Exception:
        pass


async def on_resume_callback(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    action = (query.data or "resume:go").split(":", 1)[-1]
    work = resume_work_dir(msg.chat.id)
    if action == "fresh":
        pending = (load_checkpoint(work) or {}).get("pending_new")
        wipe_resume(msg.chat.id)
        await state.clear()
        if isinstance(pending, dict) and str(pending.get("idea") or "").strip():
            await msg.answer("Старую съёмку убрал. Снимаю новую тему с нуля.")
            settings = pending.get("voice_settings")
            revisions = pending.get("revisions")
            await _run_job(
                msg,
                idea=str(pending.get("idea") or ""),
                user_script=bool(pending.get("user_script")),
                voice_id=str(pending.get("voice_id") or "") or None,
                photo_file_id=str(pending.get("photo_file_id") or "") or None,
                bot=msg.bot,
                voice_name=str(pending.get("voice_name") or "Сара"),
                consent_verified=bool(pending.get("consent_verified")),
                n_scenes=int(pending.get("n_scenes") or 5),
                extra_brief=str(pending.get("extra_brief") or ""),
                voice_settings=settings if isinstance(settings, dict) else None,
                camera=str(pending.get("camera") or ""),
                motion=str(pending.get("motion") or ""),
                quality=str(pending.get("quality") or "optimal"),
                style=str(pending.get("style") or "cinematic"),
                watermark=bool(pending.get("watermark")),
                hook=str(pending.get("hook") or ""),
                revisions=revisions if isinstance(revisions, list) else None,
                preset_brief=str(pending.get("preset_brief") or ""),
                kind=str(pending.get("kind") or "motivational"),
                dynamic_pacing=bool(pending.get("dynamic_pacing")),
                wipe=True,
            )
            return
        await msg.answer(
            "Старую съёмку убрал. Нажми «Видео за 1 клик», если снимаем новую тему.",
            reply_markup=main_menu(),
        )
        return
    kwargs = run_kwargs_from_checkpoint(work)
    if not kwargs:
        await msg.answer(
            "Нет сохранённой съёмки, которую можно продолжить. Нажми /start.",
            reply_markup=main_menu(),
        )
        return
    await state.clear()
    await msg.answer("Продолжаю с места остановки — сценарий и озвучку не пересобираю.")
    await _run_job(msg, bot=msg.bot, wipe=False, **kwargs)


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
    rows += _pair_rows([(k, v["label"]) for k, v in quality_catalog().items()], "qual", job.get("quality") or "optimal")
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


def credits_pause_kb(job_key: str = "") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="▶ Продолжить съёмку", callback_data="resume:go")],
        [InlineKeyboardButton(text="🗑 Начать заново", callback_data="resume:fresh")],
    ]
    if job_key:
        rows.insert(
            1,
            [InlineKeyboardButton(text="🔄 Обновить статус", callback_data=f"live:{job_key}")],
        )
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def credits_pause_text(chat_id: int, *, headline: str = "") -> str:
    work = resume_work_dir(chat_id)
    head = (headline or RUNWAY_CREDITS_MSG).strip()
    return (
        f"{head}\n\n"
        f"{format_resume_progress(work)}\n\n"
        "Прогресс на диске. После пополнения баланса нажмите «Продолжить съёмку» — "
        "сценарий и озвучку заново не спишем, доснимем с места остановки."
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
        f"Качество: {(quality_catalog().get(job.get('quality') or '') or quality_catalog()['optimal'])['label']}",
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
    clip_sec = 5 if job.get("dynamic_pacing") else 10
    est = estimate_cost(
        n_scenes=n,
        clip_sec=clip_sec,
        quality=str(job.get("quality") or "optimal"),
        text=idea,
        need_still=need_still,
    )
    if config.video_provider() == "fal":
        lead = (
            "Перед запуском: списание в кабинете fal.ai, не кредиты Runway. "
            "Озвучка — ElevenLabs. Кредиты не спишутся, пока не нажмёшь «Создать»:\n\n"
        )
    else:
        lead = (
            "Примерная стоимость до запуска (кредиты не спишутся, пока не нажмёшь «Создать»):\n\n"
        )
    return (
        lead
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
    await message.answer(
        "Ок, отменил. Можно начать заново.",
        reply_markup=main_menu(),
    )


async def cmd_edit(message: Message, state: FSMContext) -> None:
    await _start_edit(message, state)


def _incoming_video(message: Message) -> dict[str, Any] | None:
    if message.video:
        return {
            "file_id": message.video.file_id,
            "size": int(message.video.file_size or 0),
            "duration": float(message.video.duration or 0),
            "name": "clip.mp4",
        }
    if message.video_note:
        return {
            "file_id": message.video_note.file_id,
            "size": int(message.video_note.file_size or 0),
            "duration": float(message.video_note.duration or 0),
            "name": "note.mp4",
        }
    doc = message.document
    if not doc:
        return None
    name = (doc.file_name or "clip.mp4").lower()
    mime = (doc.mime_type or "").lower()
    if not (mime.startswith("video/") or name.endswith((".mp4", ".mov", ".webm", ".m4v"))):
        return None
    return {
        "file_id": doc.file_id,
        "size": int(doc.file_size or 0),
        "duration": None,
        "name": doc.file_name or "clip.mp4",
    }


async def _start_edit(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "✂️ Нарезка и монтаж — два режима.\n\n"
        "• Вручную: сам задаёшь таймкоды или порядок файлов, только ffmpeg, без LLM.\n"
        "• Авто: либо своё видео (план клипов через xAI API + ffmpeg, Runway не тратится), "
        "либо описать вайб/тему — сниму оригинальную синтетику с нуля тем же пайплайном, "
        "что ночной контур. Интернет не ищу, чужие фильмы и ролики не скачиваю.\n"
        "Браузер grok.com/chatgpt.com не открываю.\n\n"
        f"Лимиты Telegram на свои файлы: входящий ≤ 20 МБ и ≤ {MAX_INPUT_SEC} сек, "
        f"до {MAX_CLIPS} кусков, готовый файл ≤ 49 МБ.",
        reply_markup=edit_hub_kb(),
    )


async def on_edit_callback(query: CallbackQuery, state: FSMContext) -> None:
    data = (query.data or "edit:")[5:]
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    if data == "cut":
        await state.clear()
        await state.set_state(Flow.edit_cut_video)
        await msg.answer("Пришли одно видео. Потом напишешь начало и конец куска.")
        return
    if data == "concat":
        await state.clear()
        await state.update_data(edit_clips=[])
        await state.set_state(Flow.edit_concat)
        await msg.answer(
            "Пришли видео по одному, в том порядке, в каком склеивать.\n"
            "Когда все на месте — кнопка «Склеить».",
            reply_markup=edit_concat_kb(0),
        )
        return
    if data == "reset":
        await state.update_data(edit_clips=[])
        await state.set_state(Flow.edit_concat)
        await msg.answer("Список клипов пустой. Пришли видео заново.", reply_markup=edit_concat_kb(0))
        return
    if data == "go":
        await _run_concat(msg, state)
        return
    if data == "auto":
        await state.clear()
        await state.set_state(Flow.edit_auto_pick)
        await msg.answer(
            "Авто-монтаж — выбери источник.\n\n"
            "📤 Своё видео — как раньше: план клипов через xAI API, режет ffmpeg, Runway не тратится.\n"
            "✨ Описать вайб/тему — сниму ОРИГИНАЛЬНЫЙ синтетический ролик с нуля "
            "(тот же пайплайн, что ночной контур: IDEA_SYSTEM → SCRIPT_SYSTEM_SYNTH → Runway → голос). "
            "Интернет не ищу, чужие фильмы и ролики не скачиваю. Название фильма — только ориентир стиля. "
            "Это тратит кредиты Runway и ElevenLabs. Хронометраж ≈30 сек. "
            "Субтитры — текст озвучки через ffmpeg.\n\n"
            "Можно сразу прислать видео или написать вайб текстом.",
            reply_markup=edit_auto_source_kb(),
        )
        return
    if data == "own":
        await state.clear()
        await state.set_state(Flow.edit_auto_video)
        await msg.answer(
            "Авто-монтаж: пришли одно видео. Потом коротко опиши, что вырезать.\n"
            "Например: «динамичный ролик 30-45 сек» или «оставь самые яркие моменты»."
        )
        return
    if data == "gen":
        await state.clear()
        await state.set_state(Flow.edit_auto_vibe)
        await msg.answer(
            "Напиши вайб, тему или фильм как ориентир стиля — 2–3 слова тоже сойдут.\n"
            "Сниму новый синтетический ролик, ничего из интернета не качаю.\n"
            "Пример: «ночной неоновый город, энергия Drive» или «мотивация, лестница, фокус»."
        )
        return


async def on_edit_cut_video(message: Message, state: FSMContext) -> None:
    clip = _incoming_video(message)
    if not clip:
        await message.answer("Нужен видеофайл (mp4/mov/webm), не фото.")
        return
    try:
        check_incoming(size=clip["size"] or None, duration=clip["duration"])
    except PipelineError as exc:
        await message.answer(exc.user_message)
        return
    await state.update_data(edit_source=clip)
    caption = (message.caption or "").strip()
    if caption:
        try:
            parse_timecodes(caption)
        except PipelineError:
            caption = ""
    if caption:
        await _run_cut(message, state, caption)
        return
    await state.set_state(Flow.edit_cut_times)
    await message.answer(
        "Напиши начало и конец куска.\n"
        "Примеры: 0:05-0:18 · 12 40 · с 1:00 по 1:12"
    )


async def on_edit_cut_times(message: Message, state: FSMContext) -> None:
    await _run_cut(message, state, message.text or "")


async def _run_cut(message: Message, state: FSMContext, times: str) -> None:
    try:
        start, end = parse_timecodes(times)
    except PipelineError as exc:
        await message.answer(exc.user_message)
        return
    data = await state.get_data()
    source = data.get("edit_source")
    if not isinstance(source, dict) or not source.get("file_id"):
        await message.answer("Сначала пришли видео.", reply_markup=edit_hub_kb())
        return
    if BUSY.locked():
        await message.answer("⏳ Сейчас занят другой задачей. Напиши таймкоды ещё раз чуть позже.")
        return
    await BUSY.acquire()
    work = Path(config.WORK_DIR) / f"edit_{message.chat.id}_{int(time.time())}"
    try:
        src = work / "src.mp4"
        dest = work / "cut.mp4"
        await message.answer("Режу кусок… кредиты не списываю.")
        await _tg_download(message.bot, str(source["file_id"]), src)
        check_incoming(size=src.stat().st_size, duration=None)
        await cut_video(src, dest, start, end)
        await _send_video(message, dest, "Нарезанный кусок", filename="cut.mp4")
        await state.clear()
        await message.answer("Готово. Ещё нарезка или склейка?", reply_markup=edit_hub_kb())
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=edit_hub_kb())
    except Exception:
        log.exception("edit cut failed")
        await message.answer("Не получилось нарезать. Другой файл или другие таймкоды.", reply_markup=edit_hub_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)
        BUSY.release()


async def on_edit_concat_video(message: Message, state: FSMContext) -> None:
    clip = _incoming_video(message)
    if not clip:
        await message.answer("Нужен видеофайл (mp4/mov/webm).")
        return
    try:
        check_incoming(size=clip["size"] or None, duration=clip["duration"])
    except PipelineError as exc:
        await message.answer(exc.user_message)
        return
    data = await state.get_data()
    clips = list(data.get("edit_clips") or [])
    if len(clips) >= MAX_CLIPS:
        await message.answer(f"Уже {MAX_CLIPS} клипов — это максимум. Жми «Склеить» или сбрось список.")
        return
    clips.append(clip)
    await state.update_data(edit_clips=clips)
    extra = "Можно склеить." if len(clips) >= 2 else "Пришли ещё хотя бы один файл."
    await message.answer(
        f"Клип {len(clips)}/{MAX_CLIPS} в очереди. {extra}",
        reply_markup=edit_concat_kb(len(clips)),
    )


async def _run_concat(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    clips = list(data.get("edit_clips") or [])
    if len(clips) < 2:
        await message.answer("Нужно минимум два видео.", reply_markup=edit_concat_kb(len(clips)))
        return
    if BUSY.locked():
        await message.answer("⏳ Сейчас занят другой задачей. Нажми «Склеить» ещё раз чуть позже.")
        return
    await BUSY.acquire()
    work = Path(config.WORK_DIR) / f"edit_{message.chat.id}_{int(time.time())}"
    try:
        await message.answer(f"Склеиваю {len(clips)} клипов без переходов… кредиты не списываю.")
        paths: list[Path] = []
        for i, clip in enumerate(clips):
            dest = work / f"in_{i:02d}.mp4"
            await _tg_download(message.bot, str(clip["file_id"]), dest)
            check_incoming(size=dest.stat().st_size, duration=None)
            paths.append(dest)
        out = work / "montage.mp4"
        await concat_videos(paths, out)
        await _send_video(message, out, "Склейка", filename="montage.mp4")
        await state.clear()
        await message.answer("Готово. Ещё нарезка или склейка?", reply_markup=edit_hub_kb())
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=edit_concat_kb(len(clips)))
    except Exception:
        log.exception("edit concat failed")
        await message.answer("Не получилось склеить. Другие файлы или меньше клипов.", reply_markup=edit_concat_kb(len(clips)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        BUSY.release()


async def on_edit_auto_pick_video(message: Message, state: FSMContext) -> None:
    await on_edit_auto_video(message, state)


async def on_edit_auto_pick_text(message: Message, state: FSMContext) -> None:
    brief = (message.text or "").strip()
    if len(brief) < 3:
        await message.answer(
            "Чуть конкретнее — вайб/тема хотя бы 2–3 слова, либо пришли своё видео."
        )
        return
    await _run_synth_vibe(message, state, brief)


async def on_edit_auto_vibe(message: Message, state: FSMContext) -> None:
    brief = (message.text or "").strip()
    if len(brief) < 3:
        await message.answer("Напиши вайб или тему: хотя бы 2–3 слова.")
        return
    await _run_synth_vibe(message, state, brief)


async def _run_synth_vibe(message: Message, state: FSMContext, brief: str) -> None:
    """Оригинальная синтетика через тот же пайплайн, что ночь. Ничего не скачиваем."""
    brief = " ".join((brief or "").split())
    if len(brief) < 3:
        await message.answer("Тема слишком короткая.")
        return
    await state.clear()
    n = scenes_for_vibe(brief)
    extra = vibe_synth_brief(brief)
    voice = voice_by_index(1)
    quality = "optimal"
    if credits_paused(resume_work_dir(message.chat.id)):
        save_checkpoint(
            resume_work_dir(message.chat.id),
            pending_new={
                "idea": brief[:500],
                "user_script": False,
                "voice_id": voice["id"],
                "photo_file_id": "",
                "voice_name": voice["name"],
                "consent_verified": False,
                "n_scenes": n,
                "extra_brief": extra,
                "voice_settings": voice_settings_payload("sure", "norm"),
                "camera": camera_prompt("punch"),
                "motion": motion_prompt("drive"),
                "quality": quality,
                "style": vibe_style(brief),
                "watermark": False,
                "hook": "",
                "kind": "motivational",
                "dynamic_pacing": True,
            },
        )
        await message.answer(
            credits_pause_text(message.chat.id),
            reply_markup=credits_pause_kb(job_key_manual(message.chat.id)),
        )
        return
    cost = estimate_cost(n_scenes=n, clip_sec=5, quality=quality, text=brief, need_still=True)
    await message.answer(
        "Снимаю оригинальный синтетический ролик, интернет не ищу, чужие клипы не качаю.\n"
        f"≈{n} коротких сцен (~5 сек), цель 20–30 сек, качество «Оптимально». "
        f"Оценка Runway ≈{cost.get('runway')} кр.\n"
        "Субтитры — текст озвучки (ffmpeg drawtext)."
    )
    await _run_job(
        message,
        idea=brief[:500],
        user_script=False,
        voice_id=voice["id"],
        photo_file_id=None,
        bot=message.bot,
        voice_name=voice["name"],
        consent_verified=False,
        n_scenes=n,
        extra_brief=extra,
        voice_settings=voice_settings_payload("sure", "norm"),
        camera=camera_prompt("punch"),
        motion=motion_prompt("drive"),
        quality=quality,
        style=vibe_style(brief),
        watermark=False,
        kind="motivational",
        dynamic_pacing=True,
        route_mode="montage_generate",
    )


async def on_edit_auto_video(message: Message, state: FSMContext) -> None:
    clip = _incoming_video(message)
    if not clip:
        await message.answer("Нужен видеофайл (mp4/mov/webm), не фото.")
        return
    try:
        check_incoming(size=clip["size"] or None, duration=clip["duration"])
    except PipelineError as exc:
        await message.answer(exc.user_message)
        return
    await state.update_data(edit_source=clip)
    caption = (message.caption or "").strip()
    if len(caption) >= 4:
        await _run_auto_edit(message, state, caption)
        return
    await state.set_state(Flow.edit_auto_brief)
    await message.answer(
        "Коротко напиши, что сделать с роликом.\n"
        "Примеры: «динамичный 30-45 сек», «оставь яркие моменты», «нарезка под рилс 20 сек»."
    )


async def on_edit_auto_brief(message: Message, state: FSMContext) -> None:
    brief = (message.text or "").strip()
    if len(brief) < 4:
        await message.answer("Чуть подробнее — хотя бы несколько слов, чего хочешь от монтажа.")
        return
    await _run_auto_edit(message, state, brief)


async def _run_auto_edit(message: Message, state: FSMContext, brief: str) -> None:
    data = await state.get_data()
    source = data.get("edit_source")
    if not isinstance(source, dict) or not source.get("file_id"):
        await message.answer("Сначала пришли видео.", reply_markup=edit_hub_kb())
        return
    if BUSY.locked():
        await message.answer("⏳ Сейчас занят другой задачей. Напиши описание ещё раз чуть позже.")
        return
    await BUSY.acquire()
    work = Path(config.WORK_DIR) / f"edit_{message.chat.id}_{int(time.time())}"
    try:
        src = work / "src.mp4"
        dest = work / "auto.mp4"
        await message.answer(
            "Скачиваю ролик, спрашиваю план у Grok (xAI API, не браузер), потом режу ffmpeg. Runway не трогаю."
        )
        await _tg_download(message.bot, str(source["file_id"]), src)
        check_incoming(size=src.stat().st_size, duration=None)
        duration = await media_duration(src) or float(source.get("duration") or 0)
        if duration < 1:
            raise PipelineError("Не удалось узнать длительность файла (ffprobe). Другой ролик?")
        check_incoming(size=None, duration=duration)
        clips, origin = await plan_clips(duration=duration, brief=brief)
        note = (
            "План от Grok"
            if origin == "grok"
            else "План модели не подошёл — собрал простой эвристический монтаж"
        )
        await message.answer(f"{note}: {format_clips(clips)}")
        await render_clips(src, dest, clips)
        await _send_video(message, dest, "Авто-монтаж", filename="auto_edit.mp4")
        await state.clear()
        extra = ""
        if origin != "grok":
            extra = " Если не то — уточни описание или нарежь вручную таймкодами."
        await message.answer("Готово." + extra, reply_markup=edit_hub_kb())
    except PipelineError as exc:
        await message.answer(
            exc.user_message + "\nМожно переформулировать запрос или перейти в ручной режим.",
            reply_markup=edit_hub_kb(),
        )
    except Exception:
        log.exception("edit auto failed")
        await message.answer(
            "Авто-монтаж не собрался. Попробуй другое описание или ручные таймкоды.",
            reply_markup=edit_hub_kb(),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
        BUSY.release()


async def on_night_callback(query: CallbackQuery) -> None:
    chat_id = query.message.chat.id if query.message else 0
    if not _is_night_owner(chat_id):
        await query.answer("Только владелец.")
        return
    data = query.data or ""
    try:
        await query.answer()
    except Exception:
        pass
    from night_post import publish_job_id
    from night_store import PUBLISH_UNKNOWN, WAIT_CONFIRM, jobs_for_date, update_job
    from night_time import today_msk

    msg = query.message
    if data == "night:skipall":
        for job in jobs_for_date(today_msk().isoformat()):
            if job.get("status") in (WAIT_CONFIRM, PUBLISH_UNKNOWN):
                update_job(int(job["id"]), status="video_ready", last_error="owner skipped")
        if isinstance(msg, Message):
            await msg.answer("Ок, ролики остаются локально. Постинг не трогаю.")
        return
    if data == "night:okall":
        texts = []
        for job in jobs_for_date(today_msk().isoformat()):
            if job.get("status") in (WAIT_CONFIRM, PUBLISH_UNKNOWN):
                texts.append(await publish_job_id(int(job["id"])))
        if isinstance(msg, Message):
            await msg.answer("\n\n".join(texts)[:3900] or "Нет задач в ожидании.")
        return
    if data.startswith("night:skip:"):
        jid = int(data.split(":")[-1])
        update_job(jid, status="video_ready", last_error="owner skipped")
        if isinstance(msg, Message):
            await msg.answer(f"Задача {jid}: только локально.")
        return
    if data.startswith("night:ok:"):
        jid = int(data.split(":")[-1])
        text = await publish_job_id(jid)
        if isinstance(msg, Message):
            await msg.answer(text[:3900])


def _is_night_owner(chat_id: int) -> bool:
    owner = int(config.NIGHT_OWNER_CHAT_ID or 0)
    return (not owner) or int(chat_id) == owner


def night_confirm_kb(job_ids: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for jid in job_ids[:6]:
        rows.append(
            [
                InlineKeyboardButton(text=f"Да · {jid}", callback_data=f"night:ok:{jid}"),
                InlineKeyboardButton(text=f"Нет · {jid}", callback_data=f"night:skip:{jid}"),
            ]
        )
    if job_ids:
        rows.append([InlineKeyboardButton(text="Опубликовать все", callback_data="night:okall")])
        rows.append([InlineKeyboardButton(text="Только сохранить", callback_data="night:skipall")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def cmd_night(message: Message, state: FSMContext) -> None:
    if not _is_night_owner(message.chat.id):
        await message.answer("Эта команда только для владельца автоконтура.")
        return
    from night_report import mode_text, status_text
    from night_store import pending_owner_ids
    from night_time import today_msk

    try:
        text = status_text() + "\n\n" + mode_text()
    except Exception as exc:
        text = f"Ночной пайплайн: {type(exc).__name__}"
    wait_ids = pending_owner_ids(today_msk().isoformat())
    kb = night_confirm_kb(wait_ids) if wait_ids else main_menu()
    await message.answer(text[:3900], reply_markup=kb)


async def cmd_night_mode(message: Message, state: FSMContext) -> None:
    if not _is_night_owner(message.chat.id):
        await message.answer("Эта команда только для владельца автоконтура.")
        return
    from night_report import mode_text
    from night_store import set_publish_mode

    raw = (message.text or "").strip().split(maxsplit=1)
    arg = raw[1].strip().lower() if len(raw) > 1 else ""
    if arg in {"confirm", "данет", "safe"}:
        set_publish_mode(confirm=True, autopost=False)
        await message.answer(
            "Включил режим первой недели: идеи+видео сами, публикация только после да/нет.\n"
            + mode_text(),
            reply_markup=main_menu(),
        )
        return
    if arg in {"auto", "autopost"}:
        set_publish_mode(confirm=False, autopost=True)
        await message.answer(
            "Включил полный автопост без подтверждения. "
            "Имеет смысл после App Review и прогрева аккаунтов.\n"
            + mode_text()
            + "\nВернуть да/нет: /night_mode confirm",
            reply_markup=main_menu(),
        )
        return
    await message.answer(
        mode_text()
        + "\n\n/night — отчёт и кнопки да/нет\n"
        + "/night_mode confirm — только с подтверждением (default)\n"
        + "/night_mode auto — без подтверждения (позже, после App Review)\n"
        + "/serial — мультсериал (следующая серия / пакет / правка)",
        reply_markup=main_menu(),
    )


async def cmd_serial(message: Message, state: FSMContext) -> None:
    if not _is_night_owner(message.chat.id):
        await message.answer("Мультсериал только для владельца автоконтура.")
        return
    await state.clear()
    from serial_bot import start_serial_hub

    await start_serial_hub(message)


async def on_serial_callback(query: CallbackQuery, state: FSMContext) -> None:
    chat_id = query.message.chat.id if query.message else 0
    if not _is_night_owner(chat_id):
        await query.answer("Только владелец.")
        return
    data = (query.data or "serial:")[7:]
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    from serial_bot import hub_intro, run_serial_batch, serial_hub_kb, start_serial_hub
    from serial_render import status_text

    if data in {"hub", "status"}:
        await start_serial_hub(msg) if data == "hub" else await msg.answer(
            status_text()[:3900], reply_markup=serial_hub_kb()
        )
        return
    if data == "note":
        await state.set_state(Flow.serial_note)
        await msg.answer(
            "Напиши поворот сюжета свободным текстом — учту со СЛЕДУЮЩЕЙ серии "
            "(как правки в «Улучшить качество», но для арки).\n"
            "Пример: «в следующей серии пусть гибрид Али и Бори будет полосатый и очень стеснительный»."
        )
        return
    if data == "next":
        await run_serial_batch(msg, 1, busy=BUSY)
        return
    if data in {"b3", "b5", "b7"}:
        await run_serial_batch(msg, int(data[1:]), busy=BUSY)
        return
    await msg.answer(hub_intro()[:3900], reply_markup=serial_hub_kb())


async def on_serial_note(message: Message, state: FSMContext) -> None:
    if not _is_night_owner(message.chat.id):
        await message.answer("Мультсериал только для владельца автоконтура.")
        return
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Чуть подробнее — что должно случиться дальше?")
        return
    from serial_bot import serial_hub_kb
    from serial_render import apply_owner_note, status_text

    try:
        apply_owner_note(text)
    except Exception as exc:
        await message.answer(f"Не записал правку: {type(exc).__name__}")
        return
    await state.clear()
    await message.answer(
        "Записал. Следующая серия пойдёт с этим поворотом.\n\n" + status_text()[:3200],
        reply_markup=serial_hub_kb(),
    )


async def _start_clone_voice(msg: Message, state: FSMContext) -> None:
    await state.set_state(Flow.w2_clone_consent)
    await msg.answer(CLONE_CONSENT_MSG, reply_markup=clone_consent_kb())


async def _open_studio(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if config.WEBAPP_PUBLIC_URL:
        await msg.answer(
            "Меню внутри Telegram: создать видео, монтаж, 4K/слоу-мо, примерка, клон голоса, история. "
            "Результат приходит в этот чат.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎬 Открыть меню",
                            web_app=WebAppInfo(url=config.WEBAPP_PUBLIC_URL),
                        )
                    ],
                    [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
                ]
            ),
        )
        return
    await msg.answer(
        "Mini App откроется, когда задан HTTPS WEBAPP_PUBLIC_URL (nginx → этот бот). "
        "Пока те же функции кнопками:",
        reply_markup=more_kb(),
    )


async def _start_tryon(msg: Message, state: FSMContext) -> None:
    job = await _new_job("tryon", msg.chat.id)
    job["photo_file_id"] = None
    job["consent_verified"] = False
    await _save_job(state, job)
    await state.set_state(Flow.tryon_person)
    await msg.answer(
        "👗 Примерка одежды (fal.ai).\n\n"
        "Сначала фото человека — то же согласие, что для своего фото в ролике. "
        "Без кнопки подтверждения я это фото не использую.\n"
        "Потом отдельным сообщением — фото одежды."
    )


async def _start_act_two(msg: Message, state: FSMContext) -> None:
    if config.video_provider() == "fal" and not config.RUNWAY_API_KEY:
        await msg.answer(
            "«Оживить фото» (Act Two) — это Runway. Сейчас камера на fal.ai (Kling/Seedance). "
            "Нужен RUNWAY_API_KEY или сними ролик в «1 клик».",
            reply_markup=main_menu(),
        )
        return
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


async def _drop_remote_voice(session: aiohttp.ClientSession, voice_id: str) -> None:
    """ElevenLabs IVC удаляем. MiniMax (mm:) живёт на fal — чужой DELETE не зовём."""
    from fal_models import is_minimax_voice

    vid = (voice_id or "").strip()
    if not vid or is_minimax_voice(vid):
        return
    await delete_eleven_voice(session, vid)


async def _delete_cloned_voice(msg: Message) -> None:
    vid = delete_cloned_voice(msg.chat.id)
    if not vid:
        await msg.answer("Клонированного голоса пока нет.", reply_markup=main_menu())
        return
    async with aiohttp.ClientSession() as session:
        await _drop_remote_voice(session, vid)
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
        if credits_paused(resume_work_dir(msg.chat.id)):
            await msg.answer(
                credits_pause_text(msg.chat.id),
                reply_markup=credits_pause_kb(job_key_manual(msg.chat.id)),
            )
            return
        job = await _new_job("quick", msg.chat.id)
        await _save_job(state, job)
        await state.set_state(Flow.quick_idea)
        await msg.answer(
            "⚡️ Напиши тему — хватит 2–3 слов.\n"
            "Например: «лестница микро» или «утренний кофе».\n"
            "Заголовок, хук, сюжет, сцены и подпись придумаю сам.\n"
            "Потом можно (необязательно) своё фото и голос."
        )
        return
    if data == "menu:preset":
        await state.clear()
        if credits_paused(resume_work_dir(msg.chat.id)):
            await msg.answer(
                credits_pause_text(msg.chat.id),
                reply_markup=credits_pause_kb(job_key_manual(msg.chat.id)),
            )
            return
        await state.set_state(Flow.preset_topic)
        await msg.answer("Выбери пресет — ты пишешь только тему, остальное уже настроено:", reply_markup=presets_kb())
        return
    if data == "menu:custom":
        await state.clear()
        if credits_paused(resume_work_dir(msg.chat.id)):
            await msg.answer(
                credits_pause_text(msg.chat.id),
                reply_markup=credits_pause_kb(job_key_manual(msg.chat.id)),
            )
            return
        job = await _new_job("custom", msg.chat.id)
        await _save_job(state, job)
        await state.set_state(Flow.custom_script)
        await msg.answer(
            "🎬 Пришли готовый текст ролика.\n"
            "Это слова, которые зритель услышит. Можно абзацами — я разрежу на клипы.\n"
            "Ориентир: не длиннее ~230 слов, иначе озвучка не влезет в 6 клипов."
        )
        return
    if data == "menu:auto":
        await _start_autorolik(msg, state)
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
    if data == "menu:studio":
        await _open_studio(msg, state)
        return
    if data == "menu:edit":
        await _start_edit(msg, state)
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
            f"Пресет «{p['label']}». Напиши тему — хватит 2–3 слов.\n"
            "Заголовок, хук, сюжет и сцены соберу сам."
        )


async def on_quick_idea(message: Message, state: FSMContext) -> None:
    idea = (message.text or "").strip()
    if len(idea) < 3:
        await message.answer("Напиши тему: 2–3 слова достаточно, остальное придумаю сам.")
        return
    job = await _job(state)
    job["idea"] = idea
    job["n_scenes"] = DYNAMIC_SCENE_COUNT
    job["dynamic_pacing"] = True
    job["user_script"] = False
    job["photo_file_id"] = None
    job["consent_verified"] = False
    await _save_job(state, job)
    await state.set_state(Flow.custom_photo)
    await message.answer(
        "Тема принята — хук, сюжет и камеру соберу сам.\n\n"
        "Если хочешь своё лицо в ролике, пришли фото. Это необязательно.\n"
        "Это будет первый кадр каждого клипа, чтобы лицо не прыгало.\n"
        "Если фото не нужно — нажми «Пропустить».",
        reply_markup=photo_skip_kb(),
    )


async def on_preset_topic(message: Message, state: FSMContext) -> None:
    idea = (message.text or "").strip()
    if len(idea) < 3:
        await message.answer("Напиши тему чуть конкретнее — хватит 2–3 слов.")
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


async def _go_voice_step(
    message: Message | None,
    state: FSMContext,
    job: dict[str, Any],
    *,
    after_consent: bool,
) -> None:
    """После фото: custom обязан выбрать голос; 1-клик может пропустить (Сара)."""
    await state.set_state(Flow.custom_voice)
    if not message:
        return
    quick = job.get("mode") == "quick"
    if quick:
        text = (
            "Спасибо. Теперь выбери голос или пропусти — будет Сара. "
            "Хук и сценарий всё равно соберу сам."
            if after_consent
            else "Выбери голос или пропусти — будет Сара. Хук и сценарий всё равно соберу сам."
        )
        await message.answer(text, reply_markup=_voices_kb(message, 0, allow_skip=True))
        return
    if after_consent:
        await message.answer("Спасибо. Теперь выбери голос:", reply_markup=_voices_kb(message, 0))
        return
    await message.answer("Выбери голос:", reply_markup=_voices_kb(message, 0))


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
    await _go_voice_step(query.message if isinstance(query.message, Message) else None, state, job, after_consent=False)


async def on_consent(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    if await state.get_state() not in (Flow.custom_consent.state, Flow.auto_consent.state):
        if query.message:
            await query.message.answer("Сначала пришли фото и нажми согласие заново. /start", reply_markup=main_menu())
        return
    if (query.data or "") == "consent:no":
        await state.clear()
        if query.message:
            await query.message.answer("Ок, без фото не продолжаю. Можно начать заново.", reply_markup=main_menu())
        return
    job = await _job(state)
    if job.get("mode") == "autorolik":
        job["consent_verified"] = True
        await _save_job(state, job)
        await state.set_state(Flow.auto_photos)
        if query.message:
            from autorolik import photos_kb

            await query.message.answer(
                "Пришли до 6 фото друзей — по одному или файлом. "
                "Когда хватит, жми «Дальше».",
                reply_markup=photos_kb(count=0),
            )
        return
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
    if job.get("mode") == "tryon":
        await state.set_state(Flow.tryon_clothes)
        if query.message:
            await query.message.answer(
                "Теперь фото одежды — вещь целиком, лучше на спокойном фоне."
            )
        return
    await _go_voice_step(query.message if isinstance(query.message, Message) else None, state, job, after_consent=True)


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
    job = await _job(state)
    allow_skip = st == Flow.custom_voice.state and job.get("mode") == "quick"
    if query.message:
        try:
            await query.message.edit_reply_markup(
                reply_markup=_voices_kb(query.message, page, allow_skip=allow_skip)
            )
        except Exception:
            await query.message.answer(
                "Выбери голос:",
                reply_markup=_voices_kb(query.message, page, allow_skip=allow_skip),
            )


async def on_voice_skip(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    if await state.get_state() != Flow.custom_voice.state:
        if query.message:
            await query.message.answer("Эта кнопка уже не действует. Нажми /start.", reply_markup=main_menu())
        return
    job = await _job(state)
    if job.get("mode") != "quick":
        if query.message:
            await query.message.answer("В этом режиме голос нужно выбрать кнопкой.")
        return
    picked = voice_by_index(1)
    job["voice_idx"] = 1
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
    catalogs = {"deliv": DELIVERY, "speed": SPEED, "qual": quality_catalog(), "cam": CAMERA, "mot": MOTION}
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
    run_kw = dict(
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
        dynamic_pacing=bool(job.get("dynamic_pacing")),
        route_mode="real_photo"
        if job.get("photo_file_id") and job.get("consent_verified")
        else "synthetic_multi_scene",
    )
    if credits_paused(resume_work_dir(msg.chat.id)):
        save_checkpoint(
            resume_work_dir(msg.chat.id),
            pending_new={
                k: v
                for k, v in run_kw.items()
                if k != "bot"
            },
        )
        await msg.answer(
            credits_pause_text(msg.chat.id),
            reply_markup=credits_pause_kb(job_key_manual(msg.chat.id)),
        )
        return
    await _run_job(msg, **run_kw)


def _autorolik_voice(user_id: int) -> tuple[str, str]:
    cloned = get_cloned_voice(int(user_id))
    if cloned and cloned.get("id"):
        return str(cloned["id"]), str(cloned.get("name") or "Мой голос")
    voice = voice_by_index(1)
    return voice["id"], voice["name"]


async def _start_autorolik(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if credits_paused(resume_work_dir(msg.chat.id)):
        await msg.answer(
            credits_pause_text(msg.chat.id),
            reply_markup=credits_pause_kb(job_key_manual(msg.chat.id)),
        )
        return
    job = await _new_job("autorolik", msg.chat.id)
    job["photo_file_ids"] = []
    job["dynamic_pacing"] = True
    job["user_script"] = True
    await _save_job(state, job)
    await state.set_state(Flow.auto_consent)
    await msg.answer(
        "🎞 Авторолик — хайповый монтаж UKRAINIAN CORE.\n"
        "До 6 фото друзей. Крупные планы лиц → Kling, широкие (дрон, колонна, фары, толпа) → Seedance.\n\n"
        "Это согласие на все фото в ролике.",
        reply_markup=consent_kb(),
    )


async def _autorolik_write_script(
    message: Message,
    state: FSMContext,
    *,
    notes: str = "",
) -> None:
    from autorolik import (
        format_script_preview,
        grok_autorolik,
        load_pending,
        review_kb,
        save_pending,
    )

    job = await _job(state)
    ids = list(job.get("photo_file_ids") or [])
    pending = load_pending(message.chat.id) or {}
    if not ids:
        ids = list(pending.get("photo_file_ids") or [])
        job["photo_file_ids"] = ids
    if not ids:
        await message.answer("Нужно хотя бы одно фото.", reply_markup=main_menu())
        return
    topic = str(job.get("idea") or pending.get("idea") or "").strip()
    previous = pending.get("script") if notes else None
    await message.answer("Пишу сценарий Авторолика (4–8 сцен, FACE/WIDE)…")
    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            script = await grok_autorolik(
                session,
                n_photos=len(ids),
                idea=topic,
                notes=notes,
                previous=previous if isinstance(previous, dict) else None,
            )
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=main_menu())
        return
    save_pending(
        message.chat.id,
        {
            "script": script,
            "photo_file_ids": ids,
            "consent_verified": True,
            "idea": topic,
        },
    )
    job["script"] = script
    job["consent_verified"] = True
    await _save_job(state, job)
    await state.set_state(Flow.auto_review)
    await message.answer(format_script_preview(script), reply_markup=review_kb())


async def _autorolik_shoot(message: Message, state: FSMContext, *, bot: Bot) -> None:
    from autorolik import clear_pending, load_pending

    pending = load_pending(message.chat.id) or {}
    job = await _job(state)
    script = pending.get("script") or job.get("script")
    ids = [str(x) for x in (pending.get("photo_file_ids") or job.get("photo_file_ids") or []) if x]
    if not isinstance(script, dict) or not ids:
        await message.answer(
            "Сценарий или фото не нашёл. Начни Авторолик заново.",
            reply_markup=main_menu(),
        )
        return
    consent = bool(job.get("consent_verified") or pending.get("consent_verified"))
    if not consent:
        await message.answer(CONSENT_REQUIRED_MSG, reply_markup=main_menu())
        return
    voice_id, voice_name = _autorolik_voice(message.chat.id)
    scenes = script.get("scenes") or []
    await state.clear()
    await _run_job(
        message,
        idea=str(pending.get("idea") or job.get("idea") or script.get("title") or "авторолик"),
        user_script=True,
        voice_id=voice_id,
        photo_file_id=ids[0],
        bot=bot,
        voice_name=voice_name,
        consent_verified=True,
        n_scenes=max(4, len(scenes)),
        extra_brief="",
        voice_settings=voice_settings_payload("sure", "norm"),
        camera="",
        motion="",
        quality="optimal",
        style="cinematic",
        watermark=False,
        hook=str(script.get("hook") or ""),
        kind="autorolik",
        wipe=True,
        dynamic_pacing=True,
        route_mode="autorolik_face",
        photo_file_ids=ids,
        script_override=script,
    )
    clear_pending(message.chat.id)


async def on_auto_photo(message: Message, state: FSMContext) -> None:
    from autorolik import MAX_PHOTOS, photos_kb

    job = await _job(state)
    ids = list(job.get("photo_file_ids") or [])
    fid = None
    if message.photo:
        fid = message.photo[-1].file_id
    else:
        doc = message.document
        mime = (doc.mime_type or "") if doc else ""
        if doc and mime.startswith("image/"):
            fid = doc.file_id
    if not fid:
        await message.answer(
            "Пришли именно фото (картинкой или файлом).",
            reply_markup=photos_kb(count=len(ids)),
        )
        return
    if len(ids) >= MAX_PHOTOS:
        await message.answer(
            f"Уже {MAX_PHOTOS} фото — седьмое не беру. Жми «Дальше».",
            reply_markup=photos_kb(count=len(ids)),
        )
        return
    if fid not in ids:
        ids.append(fid)
    job["photo_file_ids"] = ids
    await _save_job(state, job)
    left = MAX_PHOTOS - len(ids)
    extra = f"Можно ещё {left}." if left else "Лимит 6. Жми «Дальше»."
    await message.answer(
        f"Фото {len(ids)}/{MAX_PHOTOS}. {extra}",
        reply_markup=photos_kb(count=len(ids)),
    )


async def on_auto_topic(message: Message, state: FSMContext) -> None:
    job = await _job(state)
    job["idea"] = (message.text or "").strip()[:1500]
    await _save_job(state, job)
    await _autorolik_write_script(message, state)


async def on_auto_edit_notes(message: Message, state: FSMContext) -> None:
    notes = (message.text or "").strip()
    if len(notes) < 3:
        await message.answer("Напиши правку парой слов — что поменять в сценах.")
        return
    await _autorolik_write_script(message, state, notes=notes)


async def on_auto_callback(query: CallbackQuery, state: FSMContext) -> None:
    data = query.data or ""
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    from autorolik import photos_kb, topic_kb

    key = data.split(":", 1)[-1] if ":" in data else ""
    if key == "reset":
        job = await _job(state)
        job["photo_file_ids"] = []
        await _save_job(state, job)
        await state.set_state(Flow.auto_photos)
        await msg.answer("Фото сбросил. Пришли заново, до 6 штук.", reply_markup=photos_kb(count=0))
        return
    if key == "next":
        job = await _job(state)
        ids = list(job.get("photo_file_ids") or [])
        if not ids:
            await msg.answer("Нужно хотя бы одно фото.", reply_markup=photos_kb(count=0))
            return
        await state.set_state(Flow.auto_topic)
        await msg.answer(
            "Тема или вайб текстом — или без темы, соберу сам.",
            reply_markup=topic_kb(),
        )
        return
    if key == "notopic":
        job = await _job(state)
        job["idea"] = ""
        await _save_job(state, job)
        await _autorolik_write_script(msg, state)
        return
    if key == "edit":
        await state.set_state(Flow.auto_edit)
        await msg.answer("Напиши, что поменять в сценарии — верну новый монтажный план.")
        return
    if key == "no":
        from autorolik import clear_pending

        clear_pending(msg.chat.id)
        await state.clear()
        await msg.answer("Ок, Авторолик отменил.", reply_markup=main_menu())
        return
    if key == "go":
        await _autorolik_shoot(msg, state, bot=query.bot)
        return


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
    hook: str = "",
    revisions: list[str] | None = None,
    preset_brief: str = "",
    kind: str = "motivational",
    wipe: bool = False,
    dynamic_pacing: bool = False,
    route_mode: str | None = None,
    photo_file_ids: list[str] | None = None,
    script_override: dict[str, Any] | None = None,
) -> None:
    ids = [str(x) for x in (photo_file_ids or []) if x]
    if photo_file_id and str(photo_file_id) not in ids:
        ids = [str(photo_file_id)] + ids
    blocked = photo_start_blocked(ids[0] if ids else None, consent_verified)
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
    file_lock = JobLock()
    if not file_lock.acquire():
        BUSY.release()
        await message.answer(
            "⏳ Сейчас уже идёт съёмка. Напиши позже.",
            reply_markup=main_menu(),
        )
        return
    ok = False
    work = resume_work_dir(message.chat.id)
    paused = credits_paused(work)
    if wipe or not paused:
        wipe_resume(message.chat.id)
        work = resume_work_dir(message.chat.id)
    job_key = job_key_manual(message.chat.id)
    save_checkpoint(
        work,
        credits_paused=False,
        run={
            "idea": idea,
            "user_script": bool(user_script),
            "voice_id": voice_id or "",
            "photo_file_id": (ids[0] if ids else "") or "",
            "photo_file_ids": ids,
            "voice_name": voice_name,
            "consent_verified": bool(consent_verified),
            "n_scenes": int(n_scenes or 5),
            "extra_brief": extra_brief or "",
            "voice_settings": dict(voice_settings or {}) or None,
            "camera": camera,
            "motion": motion,
            "quality": quality,
            "style": style,
            "watermark": bool(watermark),
            "hook": hook or "",
            "revisions": list(revisions or []),
            "preset_brief": preset_brief or "",
            "kind": kind or "motivational",
            "dynamic_pacing": bool(dynamic_pacing),
        },
    )
    try:
        start_job(job_key, chat_id=message.chat.id, title="", scene_total=n_scenes)
        status = await message.answer(
            format_status(get_job(job_key)),
            reply_markup=live_kb(job_key),
        )
        set_message(job_key, status.message_id)

        async def progress(text: str) -> None:
            snap = get_job(job_key)
            alive = bool(snap and not snap.get("done"))
            await edit_live_message(status, job_key, text, alive=alive)

        try:
            photo_path = None
            element_paths: list[Path] = []
            if ids and consent_verified:
                work.mkdir(parents=True, exist_ok=True)
                for i, fid in enumerate(ids, start=1):
                    dest = work / f"user_photo_{i}.jpg"
                    if not dest.is_file():
                        await _download_photo(bot, fid, dest)
                    element_paths.append(dest)
                photo_path = element_paths[0]
            with job_scope(job_key):
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
                    hook=hook,
                    dynamic_pacing=dynamic_pacing,
                    route_mode=route_mode
                    or ("real_photo" if photo_path else "synthetic_multi_scene"),
                    script_override=script_override,
                    element_images=element_paths or None,
                )
            preview = format_script(script)
            try:
                await message.answer(preview[:3500])
            except Exception:
                pass
            q_label = (quality_catalog().get(quality) or quality_catalog()["optimal"])["label"]
            caption = (script.get("title") or "Готово") + f" · {voice_name} · {q_label} · 9:16"
            still_name = str(script.get("runway_still_model") or "").strip()
            models_line = compact_runway_models(script.get("runway_models") if isinstance(script.get("runway_models"), list) else [])
            if still_name:
                caption += f"\nпервый кадр: {still_name}"
            elif script.get("nano_banana"):
                caption += "\nпервый кадр: Nano Banana → I2V"
            if models_line:
                caption += f"\n{models_line}"
            title = str(script.get("title") or "video")
            keep = save_last_video(message.chat.id, video_path, title)
            save_last_job(
                message.chat.id,
                {
                    "idea": str(script.get("plot") or idea),
                    "hook": str(script.get("hook") or hook or ""),
                    "title": title,
                    "caption": str(script.get("caption") or ""),
                    "kind": kind or "motivational",
                    "user_script": bool(user_script) and not list(revisions or []),
                    "preset_brief": str(preset_brief or ("" if revisions else extra_brief) or ""),
                    "revisions": list(revisions or []),
                    "voice_id": voice_id or "",
                    "voice_name": voice_name,
                    "photo_file_id": photo_file_id or "",
                    "consent_verified": bool(consent_verified),
                    "n_scenes": int(n_scenes or 5),
                    "voice_settings": dict(voice_settings or {}),
                    "camera": camera,
                    "motion": motion,
                    "quality": quality,
                    "style": style,
                    "watermark": bool(watermark),
                    "dynamic_pacing": bool(dynamic_pacing),
                },
                status="draft",
            )
            await _send_video(
                message,
                keep,
                caption,
                filename=tiktok_upload_filename(title),
            )
            finish_job(job_key, label="Готово — видео выше.")
            try:
                await status.edit_text("✅ Черновик готов — видео выше.")
            except Exception:
                pass
            n_rev = len(revisions or [])
            if n_rev:
                hint = f"Учёл правку #{n_rev}. Можно ещё раз улучшить или зафиксировать финал."
            else:
                hint = (
                    "Это черновик. Напиши, что поменять — или подтверди финал кнопкой ниже."
                )
            usage = format_runway_usage(script)
            if usage:
                hint = usage + "\n\n" + hint
            await message.answer(hint, reply_markup=result_kb(can_finalize=True))
            ok = True
        except PipelineError as exc:
            log.warning("pipeline: %s | %s", exc.user_message, exc.detail)
            finish_job(job_key, failed=True, label=exc.user_message)
            if getattr(exc, "code", "") == "credits" or is_runway_credits_fail(exc.detail):
                mark_credits_pause(work)
                await message.answer(
                    credits_pause_text(message.chat.id, headline=exc.user_message),
                    reply_markup=credits_pause_kb(job_key),
                )
            else:
                await message.answer(exc.user_message, reply_markup=main_menu())
        except Exception:
            log.exception("unhandled")
            finish_job(job_key, failed=True, label="Сломалось на моей стороне")
            await message.answer(
                "Упс, что-то сломалось на моей стороне. Нажми /start и попробуй ещё раз.",
                reply_markup=main_menu(),
            )
        finally:
            if ok:
                wipe_resume(message.chat.id)
            elif not config.KEEP_FAILED_DIR and not credits_paused(work):
                shutil.rmtree(work, ignore_errors=True)
            else:
                log.warning("оставил рабочие файлы: %s", work)
    finally:
        file_lock.release()
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
        await msg.answer("Пришли фото или видео до 30 сек — увеличу детализацию (Topaz на fal.ai).")
        return
    if data == "tryon":
        await _start_tryon(msg, state)
        return
    if data == "act":
        await _start_act_two(msg, state)
        return
    if data == "extend":
        if config.video_provider() == "fal" and not config.RUNWAY_API_KEY:
            await msg.answer(
                "Продолжение ролика (extend) — это Runway. Сейчас камера на fal.ai. "
                "Нужен RUNWAY_API_KEY или сними новый ролик.",
                reply_markup=more_kb(),
            )
            return
        await state.set_state(Flow.w2_extend_video)
        await msg.answer("Пришли вертикальный ролик до 30 сек — продолжу движение тем же кадром.")
        return
    if data == "forget":
        ids = clear_user_voices(msg.chat.id)
        async with aiohttp.ClientSession() as session:
            for vid in ids:
                await _drop_remote_voice(session, vid)
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
        from studio import clone_user_audio

        src = await _tg_download(message.bot, file_id, work / f"clone.{ext}")
        old = get_cloned_voice(message.chat.id)
        async with aiohttp.ClientSession() as session:
            voice_id = await clone_user_audio(session, src, name="Мой голос")
            if old and old.get("id") and old["id"] != voice_id:
                await _drop_remote_voice(session, old["id"])
        set_cloned_voice(message.chat.id, voice_id, "Мой голос")
        await state.clear()
        await status.edit_text("Голос склонирован и сохранён.")
        await message.answer(
            "Клон MiniMax привязан к аккаунту. Выбери «Мой голос» в списке вместо пресета ElevenLabs. "
            "Удалить можно кнопкой ниже.",
            reply_markup=clone_done_kb(),
        )
    except PipelineError as exc:
        log.warning("w2 clone: %s | %s", exc.user_message, exc.detail)
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


async def on_tryon_person(message: Message, state: FSMContext) -> None:
    photos = message.photo or []
    file_id = photos[-1].file_id if photos else None
    doc = message.document
    if not file_id and doc and (doc.mime_type or "").startswith("image/"):
        file_id = doc.file_id
    if not file_id:
        await message.answer("Нужно фото человека — картинкой или файлом-изображением.")
        return
    job = await _job(state)
    job["mode"] = "tryon"
    job["photo_file_id"] = file_id
    job["consent_verified"] = False
    await _save_job(state, job)
    await state.set_state(Flow.custom_consent)
    await message.answer(PHOTO_CONSENT_PROMPT, reply_markup=consent_kb())


async def on_tryon_clothes(message: Message, state: FSMContext) -> None:
    photos = message.photo or []
    file_id = photos[-1].file_id if photos else None
    doc = message.document
    if not file_id and doc and (doc.mime_type or "").startswith("image/"):
        file_id = doc.file_id
    if not file_id:
        await message.answer("Нужно фото одежды — картинкой или файлом-изображением.")
        return
    job = await _job(state)
    person_id = str(job.get("photo_file_id") or "")
    blocked = photo_start_blocked(person_id or None, bool(job.get("consent_verified")))
    if blocked:
        await message.answer(blocked, reply_markup=consent_kb())
        await state.set_state(Flow.custom_consent)
        return
    if BUSY.locked():
        await message.answer("⏳ Я уже занят. Подожди, пока пришлю результат.")
        return
    work = _w2_work(message)
    status = await message.answer("⏳ Примеряю одежду…")
    await BUSY.acquire()
    try:
        from studio import tryon_images

        person = await _tg_download(message.bot, person_id, work / "person.jpg")
        clothes = await _tg_download(message.bot, file_id, work / "clothes.jpg")
        dest = work / "tryon.png"
        out = await tryon_images(person, clothes, dest)
        await message.answer_document(FSInputFile(out, filename="tryon.png"), caption="Примерка одежды")
        await state.clear()
        await status.edit_text("Готово.")
        await message.answer("Ещё что-нибудь?", reply_markup=main_menu())
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=more_kb())
    except Exception:
        log.exception("tryon")
        await message.answer("Не примерил. Пришли другие фото.", reply_markup=more_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)
        BUSY.release()


async def on_w2_upscale(message: Message, state: FSMContext) -> None:
    media = _media_file_id(message)
    if not media:
        await message.answer("Пришли фото или видео.")
        return
    file_id, ext = media
    work = _w2_work(message)
    status = await message.answer("⏳ Увеличиваю качество (Topaz)…")
    try:
        from studio import upscale_media

        src = await _tg_download(message.bot, file_id, work / f"in.{ext}")
        dest = work / ("out.png" if ext == "jpg" else "out.mp4")
        out = await upscale_media(src, dest, is_image=ext == "jpg")
        if ext == "jpg":
            await message.answer_document(FSInputFile(out, filename="upscale.png"), caption="Увеличенное фото")
        else:
            keep = save_last_video(message.chat.id, out, "Увеличенное видео")
            clear_last_job(message.chat.id)
            await _send_video(message, keep, "Увеличенное видео", filename="upscale_tiktok.mp4")
        await state.clear()
        await status.edit_text("Готово.")
        await message.answer("Можно ещё раз улучшить или снять новый ролик.", reply_markup=result_kb(can_finalize=False))
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
            clear_last_job(message.chat.id)
            await _send_video(message, keep, "Оживлённое фото", filename="act_tiktok.mp4")
        await state.clear()
        await status.edit_text("Готово.")
        await message.answer("Можно улучшить качество этого ролика.", reply_markup=result_kb(can_finalize=False))
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
        clear_last_job(message.chat.id)
        await state.clear()
        await status.edit_text("Готово.")
        await message.answer("Можно улучшить качество этого ролика.", reply_markup=result_kb(can_finalize=False))
    except PipelineError as exc:
        await message.answer(exc.user_message, reply_markup=more_kb())
    except Exception:
        log.exception("w2 extend")
        await message.answer("Не продолжил ролик. Попробуй более короткий файл.", reply_markup=more_kb())
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _job_is_final(job: dict[str, Any] | None) -> bool:
    return bool(job) and str(job.get("status") or "") == "final"


def _revision_extra_brief(job: dict[str, Any]) -> str:
    from night_ideas import script_brief_from_idea

    notes = [str(n).strip() for n in (job.get("revisions") or []) if str(n).strip()]
    parts: list[str] = []
    if notes:
        numbered = "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1))
        parts.append(
            "Правки зрителя к предыдущей версии (учесть обязательно, не игнорировать):\n"
            + numbered
        )
    parts.append(
        script_brief_from_idea(
            {
                "kind": job.get("kind") or "motivational",
                "hook": job.get("hook") or "",
                "plot": job.get("idea") or "",
                "title": job.get("title") or "",
            },
            extra=str(job.get("preset_brief") or ""),
        )
    )
    return "\n\n".join(parts)


async def _pixel_upscale_last(msg: Message) -> None:
    src = get_last_video(msg.chat.id)
    if not src:
        await msg.answer("Нет готового ролика для улучшения. Сначала сними видео.", reply_markup=main_menu())
        return
    if BUSY.locked():
        await msg.answer("⏳ Я уже занят. Подожди, пока пришлю результат.")
        return
    title = get_last_title(msg.chat.id) or "video"
    work = _w2_work(msg)
    status = await msg.answer("⏳ Улучшаю картинку готового файла (Topaz)…")
    await BUSY.acquire()
    try:
        from studio import upscale_media

        local = work / "final.mp4"
        shutil.copyfile(src, local)
        dest = work / "upscale.mp4"
        out = await upscale_media(local, dest, is_image=False)
        keep = save_last_video(msg.chat.id, out, title)
        await _send_video(
            msg,
            keep,
            "Улучшенное качество картинки",
            filename=tiktok_upload_filename(title),
        )
        await status.edit_text("Готово — увеличенный файл выше.")
        await msg.answer(
            "Это увеличение картинки готового файла. Переснять сюжет с правками можно после «идея → видео».",
            reply_markup=result_kb(can_finalize=False),
        )
    except PipelineError as exc:
        await msg.answer(exc.user_message, reply_markup=result_kb(can_finalize=False))
    except Exception:
        log.exception("upscale last")
        await msg.answer("Не получилось улучшить. Попробуй ещё раз чуть позже.", reply_markup=result_kb(can_finalize=False))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        BUSY.release()


async def on_upscale_last(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    if BUSY.locked():
        await msg.answer("⏳ Я уже снимаю другой ролик. Напиши, когда пришлю результат.")
        return
    job = get_last_job(msg.chat.id)
    if _job_is_final(job):
        await msg.answer(
            "Этот ролик уже финал, правки к нему закрыты. Сними новый, если нужно иначе.",
            reply_markup=main_menu(),
        )
        return
    if job and str(job.get("idea") or "").strip():
        current = await state.get_state()
        if current == Flow.revise_notes.state:
            await msg.answer("Уже жду правки текстом. Напиши, что поменять — или нажми «Готово, это финал».")
            return
        await state.set_state(Flow.revise_notes)
        await msg.answer(REVISE_ASK, reply_markup=result_kb(can_finalize=True))
        return
    if get_last_video(msg.chat.id):
        await msg.answer(
            "У этого файла нет исходной съёмки, чтобы переснять сюжет. "
            "Могу увеличить картинку. Если нужен другой монтаж — сними новый ролик.",
            reply_markup=result_kb(can_finalize=False),
        )
        await _pixel_upscale_last(msg)
        return
    await msg.answer("Нет готового ролика для улучшения. Сначала сними видео.", reply_markup=main_menu())


async def on_revise_notes(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Напиши чуть конкретнее, что поменять.")
        return
    if BUSY.locked():
        await message.answer("⏳ Уже идёт съёмка. Напиши правки, когда пришлю черновик.")
        return
    job = get_last_job(message.chat.id)
    if _job_is_final(job):
        await state.clear()
        await message.answer(
            "Этот ролик уже финал, новые правки к нему не беру. Сними новый ролик.",
            reply_markup=main_menu(),
        )
        return
    if not job or not str(job.get("idea") or "").strip():
        await state.clear()
        await message.answer(
            "Съёмка потерялась. Нажми /start и сними ролик заново.",
            reply_markup=main_menu(),
        )
        return
    notes = [str(n).strip() for n in (job.get("revisions") or []) if str(n).strip()]
    notes.append(text)
    job["revisions"] = notes
    save_last_job(message.chat.id, job, status="draft")
    await state.clear()
    extra = _revision_extra_brief(job)
    settings = job.get("voice_settings")
    if not isinstance(settings, dict):
        settings = None
    await _run_job(
        message,
        idea=str(job.get("idea") or ""),
        user_script=False,
        voice_id=str(job.get("voice_id") or "") or None,
        photo_file_id=str(job.get("photo_file_id") or "") or None,
        bot=message.bot,
        voice_name=str(job.get("voice_name") or "Сара"),
        consent_verified=bool(job.get("consent_verified")),
        n_scenes=int(job.get("n_scenes") or 5),
        extra_brief=extra,
        voice_settings=settings,
        camera=str(job.get("camera") or ""),
        motion=str(job.get("motion") or ""),
        quality="optimal",
        style=str(job.get("style") or "cinematic"),
        watermark=bool(job.get("watermark")),
        hook=str(job.get("hook") or ""),
        revisions=notes,
        preset_brief=str(job.get("preset_brief") or ""),
        kind=str(job.get("kind") or "motivational"),
        dynamic_pacing=bool(job.get("dynamic_pacing")),
    )


async def on_revise_final(query: CallbackQuery, state: FSMContext) -> None:
    try:
        await query.answer()
    except Exception:
        pass
    msg = query.message
    if not isinstance(msg, Message):
        return
    await state.clear()
    existing = get_last_job(msg.chat.id)
    if _job_is_final(existing):
        await msg.answer("Этот ролик уже был финалом.", reply_markup=main_menu())
        return
    job = mark_last_job_final(msg.chat.id)
    if not job:
        await msg.answer("Нечего подтверждать — сначала сними ролик.", reply_markup=main_menu())
        return
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    title = str(job.get("title") or get_last_title(msg.chat.id) or "ролик")
    await msg.answer(
        f"Финал зафиксирован: «{title}». Этот ролик больше не черновик.\n"
        "Новый ролик — кнопка в меню.",
        reply_markup=main_menu(),
    )


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
    if current == Flow.auto_photos.state:
        await on_auto_photo(message, state)
        return
    if current == Flow.auto_topic.state:
        await message.answer("Напиши тему текстом или нажми «Без темы».")
        return
    if current == Flow.auto_edit.state:
        await message.answer("Напиши правки текстом — что поменять в сценах.")
        return
    if current == Flow.auto_review.state:
        await message.answer("Нажми «Снять», «Правки» или «Отмена».")
        return
    if current == Flow.revise_notes.state:
        await message.answer("Напиши правки текстом — что поменять в ролике.")
        return
    if current == Flow.custom_photo.state:
        await on_custom_photo(message, state)
        return
    if current == Flow.act_photo.state:
        await on_act_photo(message, state)
        return
    if current == Flow.tryon_person.state:
        await on_tryon_person(message, state)
        return
    if current == Flow.tryon_clothes.state:
        await on_tryon_clothes(message, state)
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
    if current == Flow.serial_note.state:
        await message.answer("Напиши правку сюжета текстом.")
        return
    if current == Flow.edit_cut_video.state:
        await on_edit_cut_video(message, state)
        return
    if current == Flow.edit_concat.state:
        await on_edit_concat_video(message, state)
        return
    if current == Flow.edit_auto_pick.state:
        await on_edit_auto_video(message, state)
        return
    if current == Flow.edit_auto_video.state:
        await on_edit_auto_video(message, state)
        return
    if current == Flow.edit_auto_vibe.state:
        await message.answer("Нужен текст вайба, не файл. Либо нажми «Прислать своё видео».")
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
    dp.message.register(cmd_night, Command("night"))
    dp.message.register(cmd_night_mode, Command("night_mode"))
    dp.message.register(cmd_serial, Command("serial"))
    dp.message.register(cmd_edit, Command("edit"))
    dp.message.register(cmd_edit, Command("cut"))
    dp.callback_query.register(on_night_callback, F.data.startswith("night:"))
    dp.callback_query.register(on_serial_callback, F.data.startswith("serial:"))
    dp.callback_query.register(on_edit_callback, F.data.startswith("edit:"))
    dp.callback_query.register(on_menu, F.data.startswith("menu:"))
    dp.callback_query.register(on_preset_pick, Flow.preset_topic, F.data.startswith("preset:"))
    dp.callback_query.register(on_photo_skip, Flow.custom_photo, F.data == "photo:skip")
    dp.callback_query.register(on_consent, Flow.custom_consent, F.data.startswith("consent:"))
    dp.callback_query.register(on_consent, Flow.auto_consent, F.data.startswith("consent:"))
    dp.callback_query.register(on_auto_callback, F.data.startswith("auto:"))
    dp.callback_query.register(on_voice_skip, Flow.custom_voice, F.data == "vskip:default")
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
    dp.callback_query.register(on_revise_final, F.data == "revise:final")
    dp.callback_query.register(on_resume_callback, F.data.startswith("resume:"))
    dp.message.register(on_quick_idea, Flow.quick_idea, F.text)
    dp.message.register(on_preset_topic, Flow.preset_topic, F.text)
    dp.message.register(on_custom_script, Flow.custom_script, F.text)
    dp.message.register(on_w2_design_text, Flow.w2_design_text, F.text)
    dp.message.register(on_w2_extend_prompt, Flow.w2_extend_prompt, F.text)
    dp.message.register(on_edit_cut_times, Flow.edit_cut_times, F.text)
    dp.message.register(on_edit_auto_brief, Flow.edit_auto_brief, F.text)
    dp.message.register(on_edit_auto_pick_text, Flow.edit_auto_pick, F.text)
    dp.message.register(on_edit_auto_vibe, Flow.edit_auto_vibe, F.text)
    dp.message.register(on_serial_note, Flow.serial_note, F.text)
    dp.message.register(on_revise_notes, Flow.revise_notes, F.text)
    dp.message.register(on_auto_topic, Flow.auto_topic, F.text)
    dp.message.register(on_auto_edit_notes, Flow.auto_edit, F.text)
    dp.message.register(on_auto_photo, Flow.auto_photos, F.photo)
    dp.message.register(on_auto_photo, Flow.auto_photos, F.document)
    dp.message.register(on_custom_photo, Flow.custom_photo, F.photo)
    dp.message.register(on_custom_photo, Flow.custom_photo, F.document)
    dp.message.register(on_act_photo, Flow.act_photo, F.photo)
    dp.message.register(on_act_photo, Flow.act_photo, F.document)
    dp.message.register(on_tryon_person, Flow.tryon_person, F.photo)
    dp.message.register(on_tryon_person, Flow.tryon_person, F.document)
    dp.message.register(on_tryon_clothes, Flow.tryon_clothes, F.photo)
    dp.message.register(on_tryon_clothes, Flow.tryon_clothes, F.document)
    dp.message.register(on_w2_clone_audio, Flow.w2_clone_audio)
    dp.message.register(on_w2_sts_audio, Flow.w2_sts_audio)
    dp.message.register(on_w2_upscale, Flow.w2_upscale)
    dp.message.register(on_act_photo, Flow.w2_act_photo)
    dp.message.register(on_w2_act_video, Flow.w2_act_video)
    dp.message.register(on_w2_extend_video, Flow.w2_extend_video)
    dp.message.register(on_edit_cut_video, Flow.edit_cut_video, F.video)
    dp.message.register(on_edit_cut_video, Flow.edit_cut_video, F.video_note)
    dp.message.register(on_edit_cut_video, Flow.edit_cut_video, F.document)
    dp.message.register(on_edit_concat_video, Flow.edit_concat, F.video)
    dp.message.register(on_edit_concat_video, Flow.edit_concat, F.video_note)
    dp.message.register(on_edit_concat_video, Flow.edit_concat, F.document)
    dp.message.register(on_edit_auto_pick_video, Flow.edit_auto_pick, F.video)
    dp.message.register(on_edit_auto_pick_video, Flow.edit_auto_pick, F.video_note)
    dp.message.register(on_edit_auto_pick_video, Flow.edit_auto_pick, F.document)
    dp.message.register(on_edit_auto_video, Flow.edit_auto_video, F.video)
    dp.message.register(on_edit_auto_video, Flow.edit_auto_video, F.video_note)
    dp.message.register(on_edit_auto_video, Flow.edit_auto_video, F.document)
    dp.message.register(on_plain_text, F.text)
    dp.message.register(on_other)
    dp.callback_query.register(on_live_refresh, F.data.startswith("live:"))
    dp.callback_query.register(on_stale_callback)
    from night_loop import start_auto_pipeline
    from webapp_server import start_webapp

    auto_task = start_auto_pipeline(BUSY) if config.NIGHT_BACKGROUND else None
    web_runner = await start_webapp(bot)
    if config.WEBAPP_PUBLIC_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Открыть меню",
                    web_app=WebAppInfo(url=config.WEBAPP_PUBLIC_URL),
                )
            )
        except Exception as exc:
            log.warning("не поставил MenuButtonWebApp: %s", exc)
    log.info("VideoBot polling start")
    try:
        await dp.start_polling(bot)
    finally:
        if auto_task is not None:
            auto_task.cancel()
            try:
                await auto_task
            except asyncio.CancelledError:
                pass
        if web_runner is not None:
            await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
