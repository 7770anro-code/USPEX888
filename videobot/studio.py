"""Студия: Topaz, примерка, клон, 1-клик. Результат — в Telegram-чат. Без деплоя."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import FSInputFile

import config
from fal_api import path_to_fal_url
from fal_models import (
    fal_interpolate,
    fal_minimax_clone,
    fal_restore_image,
    fal_upscale_file,
    fal_virtual_tryon,
    is_minimax_voice,
)
from pipeline import DYNAMIC_SCENE_COUNT, PipelineError, file_to_data_uri, media_duration
from presets import camera_prompt, motion_prompt, voice_settings_payload
from store import get_last_title, get_last_video, save_last_video, set_cloned_voice
from voices import voice_by_index
from wave2 import (
    image_upscale_payload,
    runway_generate_file,
    runway_upload,
    video_upscale_payload,
)

log = logging.getLogger("videobot")

MAX_UPLOAD_BYTES = 40 * 1024 * 1024


def studio_work(user_id: int, kind: str) -> Path:
    path = Path(config.WORK_DIR) / f"studio_{kind}_{int(user_id)}_{int(time.time())}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_upload(dest: Path, data: bytes, name: str) -> Path:
    if len(data) > MAX_UPLOAD_BYTES:
        raise PipelineError("Файл слишком большой (лимит 40 МБ).")
    if not data:
        raise PipelineError("Пустой файл.")
    dest.write_bytes(data)
    if dest.stat().st_size < 32:
        raise PipelineError("Файл слишком маленький.")
    return dest


async def send_chat_text(bot: Bot, user_id: int, text: str) -> None:
    await bot.send_message(int(user_id), text[:3900])


async def send_chat_photo(bot: Bot, user_id: int, path: Path, caption: str) -> None:
    await bot.send_document(int(user_id), FSInputFile(path), caption=caption[:900])


async def send_chat_video(bot: Bot, user_id: int, path: Path, caption: str) -> None:
    name = path.name if path.suffix else "video.mp4"
    try:
        await bot.send_video(int(user_id), FSInputFile(path, filename=name), caption=caption[:900])
    except Exception:
        await bot.send_document(int(user_id), FSInputFile(path, filename=name), caption=caption[:900])
    try:
        await bot.send_document(
            int(user_id),
            FSInputFile(path, filename=name),
            caption="Файл в полном качестве",
        )
    except Exception as exc:
        log.warning("studio extra document: %s", exc)


async def upscale_media(src: Path, dest: Path, *, is_image: bool) -> Path:
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    ) as session:
        if config.video_provider() == "fal":
            return await fal_upscale_file(session, src, dest, is_image=is_image)
        if not config.RUNWAY_API_KEY:
            raise PipelineError("Апскейл сейчас недоступен: нет FAL_KEY и нет RUNWAY_API_KEY.")
        if is_image:
            uri = await file_to_data_uri(src, dest.with_name("ref.jpg"))
            return await runway_generate_file(
                session,
                "/v1/image_upscale",
                image_upscale_payload(uri),
                dest,
                used_image=True,
            )
        uri = await runway_upload(session, src)
        return await runway_generate_file(session, "/v1/video_upscale", video_upscale_payload(uri), dest)


async def interpolate_media(src: Path, dest: Path) -> Path:
    if config.video_provider() != "fal" and not config.FAL_KEY:
        raise PipelineError("Слоу-мо идёт через Topaz на fal.ai. Нужен FAL_KEY.")
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    ) as session:
        url = await path_to_fal_url(session, src)
        return await fal_interpolate(session, url, dest)


async def restore_media(src: Path, dest: Path) -> Path:
    if config.video_provider() != "fal" and not config.FAL_KEY:
        raise PipelineError("Реставрация фото идёт через Topaz на fal.ai. Нужен FAL_KEY.")
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    ) as session:
        url = await path_to_fal_url(session, src)
        return await fal_restore_image(session, url, dest)


async def tryon_images(person: Path, clothes: Path, dest: Path) -> Path:
    if config.video_provider() != "fal" and not config.FAL_KEY:
        raise PipelineError("Примерка одежды идёт через fal.ai. Нужен FAL_KEY.")
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=180)
    ) as session:
        person_url = await path_to_fal_url(session, person)
        clothes_url = await path_to_fal_url(session, clothes)
        return await fal_virtual_tryon(session, person_url, clothes_url, dest)


async def run_studio_quick(
    bot: Bot,
    user_id: int,
    idea: str,
    *,
    quality: str = "optimal",
    photo_bytes: bytes | None = None,
    consent: bool = False,
) -> None:
    from bot import _run_job
    from aiogram.types import Message

    text = (idea or "").strip()
    if len(text) < 3:
        raise PipelineError("Напиши тему: хватит 2–3 слов.")
    q = quality if quality in ("fast", "optimal") else "optimal"
    status = await bot.send_message(int(user_id), "⏳ Студия: собираю ролик. Результат пришлю сюда.")
    if not isinstance(status, Message):
        raise PipelineError("Не смог написать в чат.")
    photo_id = None
    if photo_bytes:
        if not consent:
            raise PipelineError("Без согласия фото человека не использую.")
        work = studio_work(user_id, "quick")
        pic = write_upload(work / "user_photo.jpg", photo_bytes, "user_photo.jpg")
        sent = await bot.send_photo(int(user_id), FSInputFile(pic), caption="Фото для ролика")
        if sent.photo:
            photo_id = sent.photo[-1].file_id
    voice = voice_by_index(1)
    await _run_job(
        status,
        idea=text,
        user_script=False,
        voice_id=voice["id"],
        photo_file_id=photo_id,
        bot=bot,
        voice_name=voice["name"],
        consent_verified=bool(photo_id and consent),
        n_scenes=DYNAMIC_SCENE_COUNT,
        extra_brief="",
        voice_settings=voice_settings_payload("sure", "norm"),
        camera="decisive punch-in then slight pull-back, motivated pan with the action, handheld drive",
        motion="subject steps, reaches, turns toward camera, expressive hands, same outfit and location",
        quality=q,
        style="cinematic",
        watermark=False,
        dynamic_pacing=True,
    )


async def run_studio_upscale(bot: Bot, user_id: int, data: bytes, filename: str, mime: str) -> None:
    from bot import BUSY

    is_image = (mime or "").startswith("image/") or (filename or "").lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp")
    )
    work = studio_work(user_id, "upscale")
    ext = "jpg" if is_image else "mp4"
    src = write_upload(work / f"in.{ext}", data, filename)
    dest = work / ("out.png" if is_image else "out.mp4")
    await send_chat_text(bot, user_id, "⏳ Студия: Topaz увеличивает качество…")
    if BUSY.locked():
        raise PipelineError("Сейчас уже идёт другая задача. Подожди результат в чате.")
    await BUSY.acquire()
    try:
        out = await upscale_media(src, dest, is_image=is_image)
        if is_image:
            await send_chat_photo(bot, user_id, out, "Увеличенное фото (Topaz)")
        else:
            keep = save_last_video(int(user_id), out, "Увеличенное видео")
            await send_chat_video(bot, user_id, keep, "Увеличенное видео (Topaz)")
    finally:
        BUSY.release()
        shutil.rmtree(work, ignore_errors=True)


async def run_studio_tryon(bot: Bot, user_id: int, person: bytes, clothes: bytes, *, consent: bool) -> None:
    from bot import BUSY

    if not consent:
        raise PipelineError("Без кнопки согласия фото человека не использую.")
    work = studio_work(user_id, "tryon")
    p = write_upload(work / "person.jpg", person, "person.jpg")
    c = write_upload(work / "clothes.jpg", clothes, "clothes.jpg")
    dest = work / "tryon.png"
    await send_chat_text(bot, user_id, "⏳ Студия: примеряю одежду…")
    if BUSY.locked():
        raise PipelineError("Сейчас уже идёт другая задача. Подожди результат в чате.")
    await BUSY.acquire()
    try:
        out = await tryon_images(p, c, dest)
        await send_chat_photo(bot, user_id, out, "Примерка одежды")
    finally:
        BUSY.release()
        shutil.rmtree(work, ignore_errors=True)


async def clone_user_audio(session: aiohttp.ClientSession, src: Path, *, name: str) -> str:
    """MiniMax на fal.ai, если есть ключ. Иначе ElevenLabs IVC."""
    from wave2 import clone_voice, prepare_clone_audio

    upload = await prepare_clone_audio(src)
    if config.FAL_KEY:
        dur = await media_duration(upload) or 0.0
        if dur and dur < 9.5:
            raise PipelineError("Для MiniMax нужна чистая речь 10+ секунд. Запиши чуть длиннее.")
        return await fal_minimax_clone(session, upload)
    return await clone_voice(session, upload, name=name)


async def run_studio_clone(bot: Bot, user_id: int, audio: bytes, filename: str, *, consent: bool) -> None:
    from bot import BUSY

    if not consent:
        raise PipelineError("Без согласия голос не клонирую.")
    work = studio_work(user_id, "clone")
    src = write_upload(work / (filename or "voice.ogg"), audio, filename or "voice.ogg")
    await send_chat_text(bot, user_id, "⏳ Меню: клонирую голос MiniMax…")
    if BUSY.locked():
        raise PipelineError("Сейчас уже идёт другая задача. Подожди результат в чате.")
    await BUSY.acquire()
    try:
        async with aiohttp.ClientSession() as session:
            vid = await clone_user_audio(session, src, name=f"Клон {user_id}")
        tag = "клон MiniMax" if is_minimax_voice(vid) else "клон"
        set_cloned_voice(int(user_id), vid, "Мой голос")
        _ = tag
        await send_chat_text(
            bot,
            user_id,
            "Голос клонирован (MiniMax). Выбери «Мой голос» в списке вместо пресета ElevenLabs.",
        )
    finally:
        BUSY.release()
        shutil.rmtree(work, ignore_errors=True)


async def run_studio_vibe(bot: Bot, user_id: int, vibe: str) -> None:
    from bot import _run_job
    from aiogram.types import Message
    from edit import scenes_for_vibe, vibe_style, vibe_synth_brief

    brief = " ".join((vibe or "").split())
    if len(brief) < 3:
        raise PipelineError("Напиши вайб или тему: хотя бы 2–3 слова.")
    extra = vibe_synth_brief(brief)
    n = scenes_for_vibe(brief)
    status = await bot.send_message(int(user_id), "⏳ Меню: снимаю вайб. Результат пришлю сюда.")
    if not isinstance(status, Message):
        raise PipelineError("Не смог написать в чат.")
    voice = voice_by_index(1)
    await _run_job(
        status,
        idea=brief[:500],
        user_script=False,
        voice_id=voice["id"],
        photo_file_id=None,
        bot=bot,
        voice_name=voice["name"],
        consent_verified=False,
        n_scenes=n,
        extra_brief=extra,
        voice_settings=voice_settings_payload("sure", "norm"),
        camera=camera_prompt("punch"),
        motion=motion_prompt("drive"),
        quality="optimal",
        style=vibe_style(brief),
        watermark=False,
        kind="motivational",
        dynamic_pacing=True,
        route_mode="montage_generate",
    )


async def run_studio_interpolate(bot: Bot, user_id: int, data: bytes, filename: str, mime: str) -> None:
    from bot import BUSY

    is_image = (mime or "").startswith("image/") or (filename or "").lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp")
    )
    if is_image:
        raise PipelineError("Слоу-мо только для видео. Для фото — реставрация.")
    work = studio_work(user_id, "slowmo")
    src = write_upload(work / "in.mp4", data, filename)
    dest = work / "out.mp4"
    await send_chat_text(bot, user_id, "⏳ Меню: Topaz делает слоу-мо…")
    if BUSY.locked():
        raise PipelineError("Сейчас уже идёт другая задача. Подожди результат в чате.")
    await BUSY.acquire()
    try:
        out = await interpolate_media(src, dest)
        keep = save_last_video(int(user_id), out, "Слоу-мо")
        await send_chat_video(bot, user_id, keep, "Слоу-мо (Topaz interpolate)")
    finally:
        BUSY.release()
        shutil.rmtree(work, ignore_errors=True)


async def run_studio_restore(bot: Bot, user_id: int, data: bytes, filename: str, mime: str) -> None:
    from bot import BUSY

    is_image = (mime or "").startswith("image/") or (filename or "").lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp")
    )
    if not is_image:
        raise PipelineError("Реставрация только для фото. Для видео — апскейл или слоу-мо.")
    work = studio_work(user_id, "restore")
    src = write_upload(work / "in.jpg", data, filename)
    dest = work / "out.png"
    await send_chat_text(bot, user_id, "⏳ Меню: Topaz чинит фото…")
    if BUSY.locked():
        raise PipelineError("Сейчас уже идёт другая задача. Подожди результат в чате.")
    await BUSY.acquire()
    try:
        out = await restore_media(src, dest)
        await send_chat_photo(bot, user_id, out, "Реставрация фото (Topaz)")
    finally:
        BUSY.release()
        shutil.rmtree(work, ignore_errors=True)


async def run_studio_history(bot: Bot, user_id: int) -> None:
    src = get_last_video(int(user_id))
    if not src:
        await send_chat_text(bot, user_id, "Пока нет готового ролика. Сначала сними видео.")
        return
    title = get_last_title(int(user_id)) or "Последний ролик"
    await send_chat_video(bot, user_id, src, title)


def job_error_text(exc: BaseException) -> str:
    if isinstance(exc, PipelineError):
        return exc.user_message
    log.exception("studio job")
    return "Не вышло. Попробуй ещё раз из меню Mini App или кнопок бота."
