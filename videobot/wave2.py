"""Волна 2: тонкие API-фичи без БД. Согласие на голос/лицо — как для фото."""

from __future__ import annotations

import json
import logging
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
)

log = logging.getLogger("videobot")

CLONE_CONSENT_MSG = (
    "Без кнопки согласия я этот голос не клонирую. "
    "Подтверди, что это твой голос или есть согласие человека."
)
ACT_CONSENT_MSG = (
    "Оживление фото — только своё лицо / своё видео-перформанс или с согласия. "
    "Без кнопки подтверждения не начинаю."
)

ELEVEN_DESIGN_URL = "https://api.elevenlabs.io/v1/text-to-voice/design"
ELEVEN_CREATE_VOICE_URL = "https://api.elevenlabs.io/v1/text-to-voice"
ELEVEN_IVC_URL = "https://api.elevenlabs.io/v1/voices/add"
ELEVEN_STS_URL = "https://api.elevenlabs.io/v1/speech-to-speech/{voice_id}"
ELEVEN_DELETE_VOICE_URL = "https://api.elevenlabs.io/v1/voices/{voice_id}"


def data_dir() -> Path:
    path = Path(config.DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _user_file(chat_id: int) -> Path:
    return data_dir() / f"user_{int(chat_id)}.json"


def load_user_voices(chat_id: int) -> list[dict[str, str]]:
    path = _user_file(chat_id)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    voices = raw.get("voices") if isinstance(raw, dict) else raw
    if not isinstance(voices, list):
        return []
    out: list[dict[str, str]] = []
    for item in voices:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip() or "Мой голос"
        if not vid:
            continue
        out.append(
            {
                "id": vid,
                "name": name,
                "tag": str(item.get("tag") or "свой"),
                "kind": str(item.get("kind") or "custom"),
            }
        )
    return out


def save_user_voice(chat_id: int, voice: dict[str, str]) -> None:
    voices = load_user_voices(chat_id)
    voices = [v for v in voices if v.get("id") != voice.get("id")]
    voices.insert(0, voice)
    _user_file(chat_id).write_text(
        json.dumps({"voices": voices}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_user_voices(chat_id: int) -> list[str]:
    ids = [v["id"] for v in load_user_voices(chat_id)]
    path = _user_file(chat_id)
    if path.is_file():
        path.unlink()
    return ids


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
    form = aiohttp.FormData()
    form.add_field("name", (name or "Клон")[:64])
    form.add_field("description", "VideoBot user clone")
    form.add_field(
        "files",
        audio_path.read_bytes(),
        filename=audio_path.name or "sample.ogg",
        content_type="application/octet-stream",
    )
    async with session.post(
        ELEVEN_IVC_URL,
        headers=_eleven_headers(json_body=False),
        data=form,
        timeout=aiohttp.ClientTimeout(total=120),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise PipelineError("Не получилось клонировать голос.", _clip(f"HTTP {resp.status}: {raw}", 350))
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
    url = ELEVEN_DELETE_VOICE_URL.format(voice_id=voice_id)
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
    task_id = await _runway_submit(session, api_path, payload, used_image=used_image)
    url = await _runway_poll(session, task_id, used_image=used_image)
    return await _download(session, url, dest)
