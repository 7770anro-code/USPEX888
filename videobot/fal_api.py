"""Единый HTTP-клиент fal.ai (очередь queue.fal.run). Без SDK, ключ в лог не пишем."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

import config
from pipeline import PipelineError, _clip, _download, sleep_backoff

log = logging.getLogger("videobot")

FAL_CREDITS_MSG = (
    "На fal.ai закончились кредиты. Пополните баланс в кабинете fal.ai "
    "и попробуйте снова. Ключ: https://fal.ai/dashboard/keys"
)
FAL_STORAGE_INIT = "https://rest.alpha.fal.ai/storage/upload/initiate"

FAL_QUEUE = "https://queue.fal.run"
FAL_POLL_SEC = 4.0
FAL_DONE = frozenset({"COMPLETED"})
FAL_FAIL_STATUSES = frozenset({"FAILED", "ERROR", "CANCELLED", "CANCELED"})


def fal_headers() -> dict[str, str]:
    key = (config.FAL_KEY or "").strip()
    if not key:
        raise PipelineError("Нет FAL_KEY — видео через fal.ai недоступно. Ключ: https://fal.ai/dashboard/keys")
    return {
        "Authorization": f"Key {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def fal_fail_error(detail: str, *, used_image: bool = False) -> PipelineError:
    from pipeline import (
        RUNWAY_PERSON_MSG,
        RUNWAY_SAFETY_MSG,
        is_runway_credits_fail,
    )

    blob = (detail or "").lower()
    if is_runway_credits_fail(detail) or any(
        w in blob for w in ("insufficient", "out of credit", "payment required", "balance")
    ):
        err = PipelineError(FAL_CREDITS_MSG, detail, code="credits")
        return err
    if any(w in blob for w in ("moderat", "safety", "nsfw", "content policy", "blocked")):
        if used_image:
            return PipelineError(RUNWAY_PERSON_MSG, detail, code="moderation_person")
        return PipelineError(RUNWAY_SAFETY_MSG, detail, code="moderation")
    return PipelineError("fal.ai не смог выполнить задачу.", detail)


def extract_fal_media_url(data: dict[str, Any]) -> str:
    """Достать URL видео/картинки/аудио из типичных схем fal."""
    if not isinstance(data, dict):
        return ""
    video = data.get("video")
    if isinstance(video, dict) and isinstance(video.get("url"), str):
        return video["url"]
    if isinstance(video, str) and video.startswith("http"):
        return video
    if isinstance(data.get("video_url"), str) and data["video_url"].startswith("http"):
        return data["video_url"]
    audio = data.get("audio")
    if isinstance(audio, dict) and isinstance(audio.get("url"), str):
        return audio["url"]
    if isinstance(audio, str) and audio.startswith("http"):
        return audio
    if isinstance(data.get("audio_url"), str) and data["audio_url"].startswith("http"):
        return data["audio_url"]
    image = data.get("image")
    if isinstance(image, dict) and isinstance(image.get("url"), str):
        return image["url"]
    images = data.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and isinstance(first.get("url"), str):
            return first["url"]
        if isinstance(first, str) and first.startswith("http"):
            return first
    nested = data.get("output")
    if isinstance(nested, dict):
        return extract_fal_media_url(nested)
    if isinstance(nested, list) and nested:
        item = nested[0]
        if isinstance(item, dict):
            return extract_fal_media_url(item)
        if isinstance(item, str) and item.startswith("http"):
            return item
    return ""


def extract_fal_voice_id(data: dict[str, Any]) -> str:
    """custom_voice_id из MiniMax clone."""
    if not isinstance(data, dict):
        return ""
    for key in ("custom_voice_id", "voice_id", "customVoiceId"):
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    nested = data.get("output")
    if isinstance(nested, dict):
        return extract_fal_voice_id(nested)
    return ""


def live_fal_id(model_id: str, request_id: str) -> str:
    return f"fal:{model_id}:{request_id}"


def parse_live_fal_id(task_id: str) -> tuple[str, str] | None:
    raw = (task_id or "").strip()
    if not raw.startswith("fal:"):
        return None
    rest = raw[4:]
    model_id, sep, rid = rest.rpartition(":")
    if not sep or not model_id or not rid:
        return None
    return model_id, rid


def is_fal_live_id(task_id: str) -> bool:
    return parse_live_fal_id(task_id) is not None


def _encode_model(model_id: str) -> str:
    return "/".join(quote(part, safe="-._") for part in (model_id or "").split("/") if part)


async def fal_submit(
    session: aiohttp.ClientSession,
    model_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"{FAL_QUEUE}/{_encode_model(model_id)}"
    tries = max(1, int(config.HTTP_RETRIES or 4))
    last = ""
    for attempt in range(tries):
        try:
            async with session.post(
                url,
                headers=fal_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                raw = await resp.text()
                last = raw
                if resp.status in (429, 502, 503, 504) and attempt < tries - 1:
                    await sleep_backoff(attempt)
                    continue
                if resp.status >= 400:
                    err = fal_fail_error(_clip(f"HTTP {resp.status}: {raw}"), used_image=False)
                    err.status = resp.status
                    raise err
                data = json.loads(raw)
                if not isinstance(data, dict) or not data.get("request_id"):
                    raise PipelineError("fal.ai не вернул request_id.", _clip(raw, 240))
                return data
        except PipelineError:
            raise
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < tries - 1:
                await sleep_backoff(attempt)
                continue
            raise PipelineError("Не достучался до fal.ai.", last) from exc
    raise PipelineError("Не достучался до fal.ai.", last)


async def fal_poll(
    session: aiohttp.ClientSession,
    model_id: str,
    request_id: str,
    *,
    used_image: bool = False,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    rid = (request_id or "").strip()
    if not rid:
        raise PipelineError("Нет fal request_id — опрашивать нечего.")
    encoded = _encode_model(model_id)
    status_url = f"{FAL_QUEUE}/{encoded}/requests/{rid}/status"
    result_url = f"{FAL_QUEUE}/{encoded}/requests/{rid}"
    deadline = time.monotonic() + float(timeout_sec or config.FAL_TIMEOUT_SEC)
    last_status = ""
    while time.monotonic() < deadline:
        try:
            async with session.get(
                status_url + "?logs=0",
                headers=fal_headers(),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                raw = await resp.text()
                if resp.status in (429, 502, 503, 504):
                    await sleep_backoff(1)
                    continue
                if resp.status >= 400:
                    err = fal_fail_error(
                        _clip(f"HTTP {resp.status}: {raw}"), used_image=used_image
                    )
                    err.status = resp.status
                    raise err
                data = json.loads(raw) if raw else {}
        except PipelineError:
            raise
        except Exception as exc:
            log.warning("fal poll error request_id=%s: %s", rid, exc)
            await asyncio_sleep()
            continue
        last_status = str((data or {}).get("status") or "")
        status_u = last_status.upper()
        if status_u in FAL_FAIL_STATUSES or (data or {}).get("error"):
            detail = _clip(
                f"{status_u}: {(data or {}).get('error') or (data or {}).get('error_type') or raw}",
                300,
            )
            raise fal_fail_error(detail, used_image=used_image)
        if status_u in FAL_DONE:
            async with session.get(
                result_url,
                headers=fal_headers(),
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    raise fal_fail_error(
                        _clip(f"HTTP {resp.status}: {raw}"), used_image=used_image
                    )
                result = json.loads(raw)
            if not isinstance(result, dict):
                raise PipelineError("fal.ai вернул не JSON результата.", _clip(raw, 240))
            if result.get("error"):
                raise fal_fail_error(
                    _clip(str(result.get("error")), 300), used_image=used_image
                )
            return result
        await asyncio_sleep()
    raise PipelineError(
        "fal.ai слишком долго генерирует. Остановил ожидание.",
        f"request_id={rid} status={last_status or 'unknown'}",
    )


async def fal_peek_status(
    session: aiohttp.ClientSession,
    model_id: str,
    request_id: str,
    *,
    used_image: bool = False,
) -> dict[str, Any]:
    """Один GET /status — без ожидания COMPLETED. Для кнопки «Обновить статус»."""
    rid = (request_id or "").strip()
    if not rid:
        raise PipelineError("Нет fal request_id — опрашивать нечего.")
    encoded = _encode_model(model_id)
    status_url = f"{FAL_QUEUE}/{encoded}/requests/{rid}/status"
    async with session.get(
        status_url + "?logs=0",
        headers=fal_headers(),
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            err = fal_fail_error(_clip(f"HTTP {resp.status}: {raw}"), used_image=used_image)
            err.status = resp.status
            raise err
        data = json.loads(raw) if raw else {}
    if not isinstance(data, dict):
        return {}
    status_u = str(data.get("status") or "").upper()
    if status_u in FAL_DONE:
        result = await fal_fetch_result(session, model_id, rid, used_image=used_image)
        merged = dict(data)
        if isinstance(result, dict):
            merged.update(result)
        return merged
    return data


async def fal_fetch_result(
    session: aiohttp.ClientSession,
    model_id: str,
    request_id: str,
    *,
    used_image: bool = False,
) -> dict[str, Any]:
    encoded = _encode_model(model_id)
    result_url = f"{FAL_QUEUE}/{encoded}/requests/{request_id}"
    async with session.get(
        result_url,
        headers=fal_headers(),
        timeout=aiohttp.ClientTimeout(total=60),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise fal_fail_error(_clip(f"HTTP {resp.status}: {raw}"), used_image=used_image)
        result = json.loads(raw) if raw else {}
    if not isinstance(result, dict):
        raise PipelineError("fal.ai вернул не JSON результата.", _clip(raw, 240))
    return result


async def asyncio_sleep() -> None:
    import asyncio

    await asyncio.sleep(max(2.0, float(config.FAL_POLL_SEC or FAL_POLL_SEC)))


async def fal_run(
    session: aiohttp.ClientSession,
    model_id: str,
    payload: dict[str, Any],
    *,
    used_image: bool = False,
    dest_id: Path | None = None,
) -> dict[str, Any]:
    """Submit + poll. dest_id — sidecar с request_id для resume."""
    side = None
    if dest_id is not None:
        side = dest_id.with_suffix(dest_id.suffix + ".fal_id")
        if side.is_file():
            saved = side.read_text(encoding="utf-8").strip()
            if saved:
                log.info("fal resume poll request_id=%s file=%s", saved, dest_id.name)
                try:
                    return await fal_poll(session, model_id, saved, used_image=used_image)
                except PipelineError as exc:
                    timeout = "слишком долго" in (exc.user_message or "").lower()
                    if not timeout:
                        try:
                            side.unlink()
                        except OSError:
                            pass
                    raise
    submitted = await fal_submit(session, model_id, payload)
    rid = str(submitted.get("request_id") or "")
    if side is not None and rid:
        try:
            side.write_text(rid, encoding="utf-8")
        except OSError:
            log.warning("не записал fal request_id рядом с %s", dest_id.name if dest_id else "")
    log.info("fal submitted model=%s request_id=%s", model_id, rid)
    try:
        from live_status import note_runway_task

        note_runway_task(live_fal_id(model_id, rid), kind=model_id)
    except Exception:
        pass
    return await fal_poll(session, model_id, rid, used_image=used_image)


async def path_to_fal_url(session: aiohttp.ClientSession, path: Path) -> str:
    """https URL (fal storage) или data URI. Telegram file URL с токеном не используем."""
    src = Path(path)
    if not src.is_file():
        raise PipelineError("Файл для fal.ai не найден.")
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
    }.get(src.suffix.lower(), "application/octet-stream")
    raw = src.read_bytes()
    if len(raw) <= 3_500_000:
        import base64

        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return await fal_storage_upload(session, src, mime, raw)


async def fal_storage_upload(
    session: aiohttp.ClientSession,
    path: Path,
    mime: str,
    raw: bytes,
) -> str:
    last = ""
    try:
        async with session.post(
            FAL_STORAGE_INIT,
            headers=fal_headers(),
            json={"file_name": path.name or "file.bin", "content_type": mime},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            raw_text = await resp.text()
            last = raw_text
            if resp.status >= 400:
                raise PipelineError("Не загрузился файл на fal.ai.", _clip(f"HTTP {resp.status}: {raw_text}", 240))
            data = json.loads(raw_text)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("Не загрузился файл на fal.ai.", f"{type(exc).__name__}: {exc}") from exc
    upload_url = str((data or {}).get("upload_url") or (data or {}).get("uploadUrl") or "")
    file_url = str((data or {}).get("file_url") or (data or {}).get("url") or "")
    if not upload_url or not file_url:
        raise PipelineError("fal.ai не дал ссылку для загрузки файла.", _clip(last, 240))
    async with session.put(
        upload_url,
        data=raw,
        headers={"Content-Type": mime},
        timeout=aiohttp.ClientTimeout(total=180),
    ) as put:
        if put.status >= 400:
            body = await put.text()
            raise PipelineError("Не доехал файл до fal.ai.", _clip(f"HTTP {put.status}: {body}", 240))
    return file_url


async def fal_download_media(
    session: aiohttp.ClientSession,
    data: dict[str, Any],
    dest: Path,
) -> Path:
    url = extract_fal_media_url(data)
    if not url:
        raise PipelineError("fal.ai не вернул файл в результате.", _clip(json.dumps(data)[:240]))
    return await _download(session, url, dest)
