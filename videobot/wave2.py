"""Волна 2: ElevenLabs IVC/STS/Design и Runway Act Two / Magnific / extend."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

import aiohttp

import config
from pipeline import (
    PipelineError,
    RUNWAY_HOST,
    _clip,
    _download,
    _runway_headers,
    _runway_poll,
    _runway_submit,
    runway_content_moderation,
    write_runway_model,
)
from store import (  # noqa: F401 — реэкспорт для старых импортов
    clear_user_voices,
    load_user_voices,
    save_user_voice,
)

log = logging.getLogger("videobot")

CLONE_CONSENT_MSG = (
    "🎙 Клонирование голоса\n\n"
    "Я запишу образец и создам голосовой профиль через ElevenLabs Instant Voice Clone. "
    "Профиль привязан только к вашему Telegram-аккаунту. "
    "Удалить его можно кнопкой «Удалить мой голос».\n\n"
    "Пришлите чистую речь без музыки, лучше 1–2 минуты (минимум около 30 секунд).\n\n"
    "Нажимая «Разрешаю клонировать голос», вы подтверждаете, что это ваш голос "
    "(или есть согласие человека) и даёте разрешение на обработку голосовых данных.\n\n"
    "Это согласие отдельно от согласия на фото."
)

CLONE_PLAN_MSG = (
    "На тарифе ElevenLabs нет Instant Voice Clone. "
    "Нужен платный план с IVC (обычно Starter и выше) в кабинете elevenlabs.io → Subscription. "
    "Ключ ELEVENLABS_API_KEY тот же; после апгрейда подождите пару минут и пришлите голосовое ещё раз."
)
CLONE_KEY_PERM_MSG = (
    "Ключ ELEVENLABS_API_KEY без права создавать голоса. "
    "В кабинете ElevenLabs откройте API Key и включите Voices / Instant Voice Cloning."
)
CLONE_KEY_BAD_MSG = (
    "ElevenLabs не принял ключ ELEVENLABS_API_KEY (невалидный или отозван). Проверьте ключ в .env."
)
CLONE_SHORT_MSG = (
    "Аудио слишком короткое для клона. Нужна чистая речь примерно 1–2 минуты "
    "(минимум около 30 секунд), без музыки на фоне."
)
CLONE_FORMAT_MSG = (
    "ElevenLabs не прочитал аудиофайл. Пришлите голосовое ещё раз или mp3/wav без музыки."
)
CLONE_RATE_MSG = "ElevenLabs просит подождать (лимит запросов). Повторите клон через минуту."
CLONE_LIMIT_MSG = (
    "На аккаунте ElevenLabs закончился лимит слотов голоса. "
    "Удалите старый клон в боте или в кабинете, либо расширьте план."
)

ELEVEN_DESIGN_URL = "https://api.elevenlabs.io/v1/text-to-voice/design"
ELEVEN_CREATE_VOICE_URL = "https://api.elevenlabs.io/v1/text-to-voice"
ELEVEN_IVC_URL = "https://api.elevenlabs.io/v1/voices/add"
ELEVEN_STS_URL = "https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}"
ELEVEN_DELETE_VOICE_URL = "https://api.elevenlabs.io/v1/voices/{voice_id}"


def parse_elevenlabs_error(raw: str) -> dict[str, str]:
    """Достать code/status/message из JSON ElevenLabs. Ключи и токены не возвращаем."""
    out = {"code": "", "status": "", "type": "", "message": ""}
    try:
        data = json.loads(raw or "")
    except json.JSONDecodeError:
        return out
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        out["code"] = str(detail.get("code") or "")
        out["status"] = str(detail.get("status") or "")
        out["type"] = str(detail.get("type") or "")
        out["message"] = str(detail.get("message") or "")[:300]
        return out
    if isinstance(detail, str):
        out["message"] = detail[:300]
        return out
    if isinstance(data, dict):
        out["message"] = str(data.get("message") or data.get("error") or "")[:300]
    return out


def clone_fail_user_message(http_status: int, raw: str) -> str:
    """Понятный текст в чат по коду ElevenLabs. Тариф не чиним кодом — только объясняем."""
    parsed = parse_elevenlabs_error(raw)
    blob = " ".join(
        [
            parsed["code"],
            parsed["status"],
            parsed["type"],
            parsed["message"],
            raw or "",
        ]
    ).lower()
    if (
        "can_not_use_instant_voice_cloning" in blob
        or "paid_plan_required" in blob
        or "does not include instant voice cloning" in blob
    ):
        return CLONE_PLAN_MSG
    if "missing_permissions" in blob or "missing the permission" in blob:
        return CLONE_KEY_PERM_MSG
    if http_status in (401, 403) and (
        "unauthorized" in blob or "invalid" in blob or "api key" in blob
    ):
        return CLONE_KEY_BAD_MSG
    if http_status == 429 or "rate_limit" in blob or "too_many_requests" in blob:
        return CLONE_RATE_MSG
    if "voice_limit" in blob or "voice slots" in blob or "max_voices" in blob:
        return CLONE_LIMIT_MSG
    if any(
        token in blob
        for token in (
            "too short",
            "audio_too_short",
            "minimum duration",
            "at least 1 minute",
            "at least 30",
        )
    ):
        return CLONE_SHORT_MSG
    if any(
        token in blob
        for token in (
            "invalid_audio",
            "could not decode",
            "unsupported format",
            "invalid file",
            "failed to read",
            "corrupt",
        )
    ):
        return CLONE_FORMAT_MSG
    if http_status in (401, 403):
        return CLONE_KEY_BAD_MSG
    return "Не получилось клонировать голос."


def _clone_content_type(path: Path) -> str:
    suf = path.suffix.lower()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(suf, "application/octet-stream")


async def prepare_clone_audio(audio_path: Path) -> Path:
    """Telegram voice — OGG/Opus; ElevenLabs IVC лучше ест wav/mp3."""
    src = Path(audio_path)
    if not src.is_file():
        raise PipelineError("Не нашёл запись голоса. Пришлите голосовое ещё раз.")
    if shutil.which("ffmpeg") is None:
        return src
    dest = src.with_name(src.stem + "_ivc.wav")
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "44100",
        "-sample_fmt",
        "s16",
        str(dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size < 1000:
        log.warning(
            "clone ffmpeg wav failed rc=%s err=%s",
            proc.returncode,
            _clip(err.decode("utf-8", "replace"), 200),
        )
        return src
    return dest


def voice_design_payload(description: str) -> dict[str, Any]:
    text = (description or "").strip()[:1000]
    if len(text) < 20:
        raise PipelineError("Опиши голос чуть подробнее — хотя бы пару фраз (тембр, возраст, характер).")
    return {
        "voice_description": text,
        "auto_generate_text": True,
        "model_id": "eleven_ttv_v3",
    }


def image_upscale_payload(image_uri: str) -> dict[str, Any]:
    return {
        "model": "magnific_precision_upscaler_v2",
        "imageUri": image_uri,
        "scaleFactor": 2,
        "flavor": "photo",
    }


def video_upscale_payload(video_uri: str) -> dict[str, Any]:
    return {
        "model": "magnific_video_upscaler_creative",
        "videoUri": video_uri,
        "resolution": "2k",
        "flavor": "natural",
        "creativity": 20,
    }


def act_two_payload(image_uri: str, video_uri: str) -> dict[str, Any]:
    return {
        "model": "act_two",
        "character": {"type": "image", "uri": image_uri},
        "reference": {"type": "video", "uri": video_uri},
        "bodyControl": True,
        "expressionIntensity": 3,
        "ratio": "720:1280",
        "contentModeration": runway_content_moderation(),
    }


def extend_video_payload(video_uri: str, prompt: str) -> dict[str, Any]:
    text = (prompt or "").strip() or (
        "continue the same motion naturally, same clothes, same location, minimal body movement"
    )
    return {
        "model": "seedance2_5",
        "promptVideo": video_uri,
        "promptText": text[:15000],
        "mode": "extend",
        "audio": True,
        "ratio": "720:1280",
        "contentModeration": runway_content_moderation(),
    }


def _eleven_headers(*, json_body: bool = True) -> dict[str, str]:
    headers = {"xi-api-key": config.ELEVENLABS_API_KEY}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


async def design_voice_previews(session: aiohttp.ClientSession, description: str) -> list[dict[str, Any]]:
    if not config.ELEVENLABS_API_KEY:
        raise PipelineError("Голос сейчас недоступен. Попробуй ещё раз чуть позже.")
    payload = voice_design_payload(description)
    async with session.post(
        ELEVEN_DESIGN_URL,
        headers=_eleven_headers(),
        json=payload,
        timeout=aiohttp.ClientTimeout(total=120),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise PipelineError("Не получилось собрать голос по описанию.", _clip(f"HTTP {resp.status}: {raw}", 350))
        data = json.loads(raw)
    previews = data.get("previews") if isinstance(data, dict) else None
    if not isinstance(previews, list) or not previews:
        raise PipelineError("ElevenLabs не вернул варианты голоса.", _clip(raw, 240))
    out = []
    for item in previews[:3]:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("generated_voice_id") or "")
        b64 = str(item.get("audio_base_64") or item.get("audio_base64") or "")
        if gid and b64:
            out.append({"generated_voice_id": gid, "audio_base_64": b64})
    if not out:
        raise PipelineError("В превью голоса нет аудио. Попробуй другое описание.")
    return out


async def create_designed_voice(
    session: aiohttp.ClientSession,
    *,
    generated_voice_id: str,
    name: str,
    description: str,
) -> str:
    body = {
        "voice_name": (name or "Дизайн")[:64],
        "voice_description": (description or name or "custom")[:500],
        "generated_voice_id": generated_voice_id,
    }
    async with session.post(
        ELEVEN_CREATE_VOICE_URL,
        headers=_eleven_headers(),
        json=body,
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise PipelineError("Не сохранил выбранный голос.", _clip(f"HTTP {resp.status}: {raw}", 350))
        data = json.loads(raw)
    voice_id = str((data or {}).get("voice_id") or "")
    if not voice_id:
        raise PipelineError("ElevenLabs не вернул id голоса.", _clip(raw, 240))
    return voice_id


async def clone_voice(
    session: aiohttp.ClientSession,
    audio_path: Path,
    *,
    name: str,
) -> str:
    if not config.ELEVENLABS_API_KEY:
        raise PipelineError("Голос сейчас недоступен. Попробуй ещё раз чуть позже.")
    upload = await prepare_clone_audio(audio_path)
    form = aiohttp.FormData()
    form.add_field("name", (name or "Клон")[:64])
    form.add_field("description", "VideoBot user clone")
    form.add_field(
        "files",
        upload.read_bytes(),
        filename=upload.name or "sample.wav",
        content_type=_clone_content_type(upload),
    )
    async with session.post(
        ELEVEN_IVC_URL,
        headers=_eleven_headers(json_body=False),
        data=form,
        timeout=aiohttp.ClientTimeout(total=120),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            parsed = parse_elevenlabs_error(raw)
            log.warning(
                "clone_voice http=%s elevenlabs_code=%s elevenlabs_status=%s elevenlabs_type=%s msg=%s",
                resp.status,
                parsed["code"] or "-",
                parsed["status"] or "-",
                parsed["type"] or "-",
                parsed["message"] or _clip(raw, 180),
            )
            err = PipelineError(
                clone_fail_user_message(resp.status, raw),
                _clip(f"HTTP {resp.status}: {raw}", 350),
            )
            err.status = resp.status
            raise err
        data = json.loads(raw)
    voice_id = str((data or {}).get("voice_id") or "")
    if not voice_id:
        raise PipelineError("ElevenLabs не вернул id клона.", _clip(raw, 240))
    return voice_id


async def speech_to_speech(
    session: aiohttp.ClientSession,
    audio_path: Path,
    voice_id: str,
    dest: Path,
) -> Path:
    if not config.ELEVENLABS_API_KEY:
        raise PipelineError("Голос сейчас недоступен. Попробуй ещё раз чуть позже.")
    url = ELEVEN_STS_URL.format(voice_id=voice_id)
    form = aiohttp.FormData()
    form.add_field(
        "audio",
        audio_path.read_bytes(),
        filename=audio_path.name or "speech.ogg",
        content_type="application/octet-stream",
    )
    form.add_field("model_id", "eleven_multilingual_sts_v2")
    async with session.post(
        url,
        headers={**_eleven_headers(json_body=False), "Accept": "audio/mpeg"},
        params={"output_format": "mp3_44100_128"},
        data=form,
        timeout=aiohttp.ClientTimeout(total=120),
    ) as resp:
        raw = await resp.read()
        if resp.status >= 400:
            raise PipelineError(
                "Не получилось переозвучить запись.",
                _clip(f"HTTP {resp.status}: {raw.decode('utf-8', 'replace')}", 350),
            )
    dest.write_bytes(raw)
    return dest


async def delete_eleven_voice(session: aiohttp.ClientSession, voice_id: str) -> None:
    from fal_models import is_minimax_voice

    vid = (voice_id or "").strip()
    if not vid or is_minimax_voice(vid):
        return
    url = ELEVEN_DELETE_VOICE_URL.format(voice_id=vid)
    try:
        async with session.delete(
            url,
            headers=_eleven_headers(json_body=False),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status >= 400 and resp.status != 404:
                log.warning("delete voice %s http=%s", voice_id, resp.status)
    except Exception as exc:
        log.warning("delete voice %s: %s", voice_id, exc)


async def runway_upload(session: aiohttp.ClientSession, path: Path) -> str:
    if not config.RUNWAY_API_KEY:
        raise PipelineError("Камера сейчас недоступна. Попробуй ещё раз чуть позже.")
    filename = path.name or "media.bin"
    async with session.post(
        f"{RUNWAY_HOST}/v1/uploads",
        headers=_runway_headers(),
        json={"filename": filename, "type": "ephemeral"},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise PipelineError("Не загрузился файл на Runway.", _clip(f"HTTP {resp.status}: {raw}", 350))
        data = json.loads(raw)
    upload_url = str(data.get("uploadUrl") or "")
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    runway_uri = str(data.get("runwayUri") or "")
    if not upload_url or not runway_uri:
        raise PipelineError("Runway не дал ссылку для загрузки файла.", _clip(raw, 240))
    form = aiohttp.FormData()
    for key, value in fields.items():
        form.add_field(str(key), str(value))
    form.add_field("file", path.read_bytes(), filename=filename, content_type="application/octet-stream")
    async with session.post(upload_url, data=form, timeout=aiohttp.ClientTimeout(total=180)) as up:
        if up.status >= 400:
            body = await up.text()
            raise PipelineError("Не доехал файл до Runway.", _clip(f"HTTP {up.status}: {body}", 350))
    return runway_uri


async def runway_generate_file(
    session: aiohttp.ClientSession,
    api_path: str,
    payload: dict[str, Any],
    dest: Path,
    *,
    used_image: bool = False,
) -> Path:
    task_id, model_used = await _runway_submit(session, api_path, payload, used_image=used_image)
    if model_used:
        write_runway_model(dest, model_used)
    url = await _runway_poll(session, task_id, used_image=used_image)
    return await _download(session, url, dest)
