"""Единый HTTP-клиент fal.ai (очередь queue.fal.run). Без SDK, ключ в лог не пишем."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

import config
from pipeline import PipelineError, _clip, _download, sleep_backoff

log = logging.getLogger("videobot")

FAL_CREDITS_MSG = (
    "На fal.ai закончились кредиты. Пополните баланс в кабинете fal.ai "
    "и попробуйте снова. Ключ: https://fal.ai/dashboard/keys"
)
FAL_PERSON_MSG = (
    "fal.ai отклонил кадр живого человека (политика партнёра). "
    "Общий план сам уходит на Kling I2V (кадр как start_image); "
    "лица друзей — Kling Element. Если ошибка осталась, это отказ Kling, не Seedance."
)
FAL_STORAGE_INIT = "https://rest.alpha.fal.ai/storage/upload/initiate"

FAL_QUEUE = "https://queue.fal.run"
FAL_POLL_SEC = 4.0
FAL_DONE = frozenset({"COMPLETED"})
FAL_FAIL_STATUSES = frozenset({"FAILED", "ERROR", "CANCELLED", "CANCELED"})


def fal_headers(*, json_body: bool = True) -> dict[str, str]:
    key = (config.FAL_KEY or "").strip()
    if not key:
        raise PipelineError("Нет FAL_KEY — видео через fal.ai недоступно. Ключ: https://fal.ai/dashboard/keys")
    headers = {
        "Authorization": f"Key {key}",
        "Accept": "application/json",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _fal_json_msg(detail: str) -> str:
    raw = detail or ""
    start = raw.find("{")
    if start < 0:
        return ""
    try:
        data = json.loads(raw[start:])
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    items = data.get("detail")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return str(items[0].get("msg") or items[0].get("type") or "")[:280]
    if isinstance(data.get("error"), str):
        return str(data.get("error") or "")[:280]
    return ""


def fal_fail_error(detail: str, *, used_image: bool = False) -> PipelineError:
    from pipeline import (
        RUNWAY_SAFETY_MSG,
        is_runway_credits_fail,
        is_runway_person_moderation,
    )

    extra = _fal_json_msg(detail)
    blob = f"{detail or ''} {extra}".lower()
    if is_runway_credits_fail(detail) or any(
        w in blob for w in ("insufficient", "out of credit", "payment required", "balance")
    ):
        err = PipelineError(FAL_CREDITS_MSG, detail, code="credits")
        return err
    person = is_runway_person_moderation("", detail) or any(
        w in blob
        for w in (
            "content_policy",
            "likeness",
            "real people",
            "partner_validation",
            "private information",
        )
    )
    if person or any(w in blob for w in ("moderat", "safety", "nsfw", "content policy", "blocked")):
        if used_image or person:
            return PipelineError(FAL_PERSON_MSG, detail, code="moderation_person")
        return PipelineError(RUNWAY_SAFETY_MSG, detail, code="moderation")
    if extra:
        return PipelineError(f"fal.ai: {extra}", detail)
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


def _safe_fal_url(url: Any, fallback: str = "") -> str:
    """Только HTTPS *.fal.run с /requests/ — не подставляем чужой host."""
    raw = str(url or "").strip()
    try:
        parsed = urlparse(raw)
    except Exception:
        return fallback
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme == "https"
        and (host == "queue.fal.run" or host.endswith(".fal.run"))
        and "/requests/" in (parsed.path or "")
        and all(c not in raw for c in " \n\r\t")
    ):
        return raw.split("#", 1)[0]
    return fallback


def _with_query(url: str, query: str) -> str:
    if not query:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{query}"


def _model_id_candidates(model_id: str) -> list[str]:
    """fal часто кладёт джобу на родителя: fal-ai/flux/schnell → fal-ai/flux."""
    parts = [p for p in (model_id or "").split("/") if p]
    out: list[str] = []
    while len(parts) >= 2:
        name = "/".join(parts)
        if name not in out:
            out.append(name)
        parts = parts[:-1]
    return out


def _result_url_candidates(url: str) -> list[str]:
    """GET result: у Flux — голый .../requests/{id}; в доке ещё бывает /response."""
    raw = (url or "").strip()
    if not raw:
        return []
    parsed = urlparse(raw)
    path = (parsed.path or "").rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    origin = f"{parsed.scheme}://{parsed.netloc}"
    out = [raw]
    if path.endswith("/response"):
        alt = origin + path[: -len("/response")] + query
    else:
        alt = origin + path + "/response" + query
    if alt and alt not in out:
        out.append(alt)
    return out


_FAL_JOB_URLS: dict[str, tuple[str, str]] = {}


def remember_fal_urls(
    request_id: str,
    *,
    status_url: str = "",
    response_url: str = "",
) -> None:
    rid = (request_id or "").strip()
    status = _safe_fal_url(status_url)
    response = _safe_fal_url(response_url)
    if rid and (status or response):
        prev = _FAL_JOB_URLS.get(rid, ("", ""))
        _FAL_JOB_URLS[rid] = (status or prev[0], response or prev[1])


def recalled_fal_urls(request_id: str) -> tuple[str, str]:
    return _FAL_JOB_URLS.get((request_id or "").strip(), ("", ""))


def fal_side_payload(submitted: dict[str, Any] | None, *, model_id: str = "") -> dict[str, str]:
    blob = submitted if isinstance(submitted, dict) else {}
    return {
        "request_id": str(blob.get("request_id") or ""),
        "status_url": str(blob.get("status_url") or ""),
        "response_url": str(blob.get("response_url") or ""),
        "model_id": str(blob.get("model_id") or model_id or ""),
    }


def read_fal_side(path: Path) -> dict[str, str]:
    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict):
            return {
                "request_id": str(data.get("request_id") or ""),
                "status_url": str(data.get("status_url") or ""),
                "response_url": str(data.get("response_url") or ""),
                "model_id": str(data.get("model_id") or ""),
            }
        return {}
    return {"request_id": raw}


def fal_request_urls(
    model_id: str,
    request_id: str,
    submitted: dict[str, Any] | None = None,
    *,
    status_url: str | None = None,
    response_url: str | None = None,
) -> tuple[str, str]:
    """URL статуса и результата.

    Submit сам отдаёт status_url/response_url — их и берём. Путь модели в них
    может отличаться от id, которым слали POST (flux/schnell → flux).
    Голый .../requests/{id} на *правильном* app — валидный GET result (Flux).
    Тот же путь на *чужом* app (schnell) даёт HTTP 405 Allow: POST.
    """
    rid = (request_id or "").strip()
    blob = submitted if isinstance(submitted, dict) else {}
    remembered = recalled_fal_urls(rid)
    encoded = _encode_model(model_id)
    base = f"{FAL_QUEUE}/{encoded}/requests/{rid}"
    default_status = f"{base}/status"
    default_response = base
    status = _safe_fal_url(
        status_url or blob.get("status_url") or remembered[0] or default_status,
        default_status,
    )
    response = _safe_fal_url(
        response_url or blob.get("response_url") or remembered[1] or default_response,
        default_response,
    )
    return status, response


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
                remember_fal_urls(
                    str(data.get("request_id") or ""),
                    status_url=str(data.get("status_url") or ""),
                    response_url=str(data.get("response_url") or ""),
                )
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


def _status_url_list(model_id: str, request_id: str, status_url: str) -> list[str]:
    rid = (request_id or "").strip()
    out: list[str] = []
    if status_url:
        out.append(_with_query(status_url, "logs=0"))
    for mid in _model_id_candidates(model_id):
        u = f"{FAL_QUEUE}/{_encode_model(mid)}/requests/{rid}/status"
        q = _with_query(u, "logs=0")
        if q not in out:
            out.append(q)
    return out


def _result_url_list(model_id: str, request_id: str, result_url: str) -> list[str]:
    rid = (request_id or "").strip()
    out: list[str] = []
    for cand in _result_url_candidates(result_url):
        if cand not in out:
            out.append(cand)
    for mid in _model_id_candidates(model_id):
        base = f"{FAL_QUEUE}/{_encode_model(mid)}/requests/{rid}"
        for cand in (base, base + "/response"):
            if cand not in out:
                out.append(cand)
    return out


async def _fal_get_json(
    session: aiohttp.ClientSession,
    urls: list[str],
    *,
    used_image: bool = False,
    timeout_sec: float = 30,
) -> tuple[int, dict[str, Any], str, str]:
    """Первый не-405 GET. 429/5xx и прочие 4xx возвращаем вызывающему."""
    last_code, last_raw, last_url = 0, "", ""
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        async with session.get(
            url,
            headers=fal_headers(json_body=False),
            timeout=aiohttp.ClientTimeout(total=timeout_sec),
        ) as resp:
            raw = await resp.text()
            last_code, last_raw, last_url = resp.status, raw, url
            if resp.status == 405:
                continue
            data = json.loads(raw) if raw and resp.status < 400 else {}
            if resp.status < 400 and not isinstance(data, dict):
                data = {}
            return resp.status, data if isinstance(data, dict) else {}, raw, url
    return last_code, {}, last_raw, last_url


async def fal_poll(
    session: aiohttp.ClientSession,
    model_id: str,
    request_id: str,
    *,
    used_image: bool = False,
    timeout_sec: float | None = None,
    status_url: str | None = None,
    response_url: str | None = None,
) -> dict[str, Any]:
    rid = (request_id or "").strip()
    if not rid:
        raise PipelineError("Нет fal request_id — опрашивать нечего.")
    status_url, result_url = fal_request_urls(
        model_id,
        rid,
        status_url=status_url,
        response_url=response_url,
    )
    status_urls = _status_url_list(model_id, rid, status_url)
    result_urls = _result_url_list(model_id, rid, result_url)
    deadline = time.monotonic() + float(timeout_sec or config.FAL_TIMEOUT_SEC)
    last_status = ""
    while time.monotonic() < deadline:
        try:
            code, data, raw, used = await _fal_get_json(
                session, status_urls, used_image=used_image, timeout_sec=30
            )
            if code in (429, 502, 503, 504):
                await sleep_backoff(1)
                continue
            if code >= 400:
                err = fal_fail_error(
                    _clip(f"HTTP {code}: {raw}"), used_image=used_image
                )
                err.status = code
                raise err
            if used and used != status_urls[0]:
                remember_fal_urls(rid, status_url=used.split("?", 1)[0])
                status_urls = _status_url_list(model_id, rid, used.split("?", 1)[0])
        except PipelineError:
            raise
        except Exception as exc:
            log.warning("fal poll error request_id=%s: %s", rid, exc)
            await asyncio_sleep()
            continue
        last_status = str((data or {}).get("status") or "")
        status_u = last_status.upper()
        if data.get("response_url"):
            remember_fal_urls(rid, response_url=str(data.get("response_url") or ""))
            result_urls = _result_url_list(
                model_id, rid, str(data.get("response_url") or result_url)
            )
        if status_u in FAL_FAIL_STATUSES or (data or {}).get("error"):
            detail = _clip(
                f"{status_u}: {(data or {}).get('error') or (data or {}).get('error_type') or raw}",
                300,
            )
            raise fal_fail_error(detail, used_image=used_image)
        if status_u in FAL_DONE:
            status_media = extract_fal_media_url(data or {})
            code, result, raw, used = await _fal_get_json(
                session, result_urls, used_image=used_image, timeout_sec=60
            )
            if used and code < 400:
                remember_fal_urls(rid, response_url=used)
            merged: dict[str, Any] = dict(data or {})
            if isinstance(result, dict):
                merged.update(result)
            media = extract_fal_media_url(merged) or status_media
            if media:
                if code >= 400:
                    log.warning(
                        "fal result GET HTTP %s after COMPLETED — using status media request_id=%s",
                        code,
                        rid,
                    )
                return merged if extract_fal_media_url(merged) else (data or merged)
            if isinstance(result, dict) and result.get("error"):
                err = fal_fail_error(
                    _clip(str(result.get("error")), 300), used_image=used_image
                )
                if err.code != "credits":
                    err.code = err.code or "fal_keep_sidecar"
                raise err
            if code >= 400:
                err = fal_fail_error(
                    _clip(f"HTTP {code}: {raw}"), used_image=used_image
                )
                err.status = code
                if err.code != "credits":
                    err.code = err.code or "fal_keep_sidecar"
                raise err
            raise PipelineError(
                "fal.ai не вернул файл в результате.",
                _clip(raw, 240),
                code="fal_keep_sidecar",
            )
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
    status_url, _result_url = fal_request_urls(model_id, rid)
    code, data, raw, used = await _fal_get_json(
        session,
        _status_url_list(model_id, rid, status_url),
        used_image=used_image,
        timeout_sec=30,
    )
    if code >= 400:
        err = fal_fail_error(_clip(f"HTTP {code}: {raw}"), used_image=used_image)
        err.status = code
        raise err
    if used:
        remember_fal_urls(rid, status_url=used.split("?", 1)[0])
    if not isinstance(data, dict):
        return {}
    status_u = str(data.get("status") or "").upper()
    if status_u in FAL_DONE:
        result = await fal_fetch_result(
            session,
            model_id,
            rid,
            used_image=used_image,
            response_url=data.get("response_url"),
        )
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
    response_url: str | None = None,
) -> dict[str, Any]:
    _status_url, result_url = fal_request_urls(
        model_id, request_id, response_url=response_url
    )
    code, result, raw, used = await _fal_get_json(
        session,
        _result_url_list(model_id, request_id, result_url),
        used_image=used_image,
        timeout_sec=60,
    )
    if code >= 400:
        raise fal_fail_error(_clip(f"HTTP {code}: {raw}"), used_image=used_image)
    if used:
        remember_fal_urls(request_id, response_url=used)
    if not isinstance(result, dict):
        raise PipelineError("fal.ai вернул не JSON результата.", _clip(raw, 240))
    return result


async def asyncio_sleep() -> None:
    import asyncio

    await asyncio.sleep(max(2.0, float(config.FAL_POLL_SEC or FAL_POLL_SEC)))


def fal_model_family(model_id: str) -> str:
    raw = (model_id or "").strip().lower()
    if "kling" in raw:
        return "kling"
    if "seedance" in raw:
        return "seedance"
    return raw


def keep_fal_sidecar(exc: BaseException) -> bool:
    """COMPLETED/timeout/credits — sidecar жив, новый submit сожжёт уже оплаченное."""
    if not isinstance(exc, PipelineError):
        return False
    if getattr(exc, "code", "") in ("fal_keep_sidecar", "credits"):
        return True
    return "слишком долго" in (exc.user_message or "").lower()


async def fal_try_resume(
    session: aiohttp.ClientSession,
    dest_id: Path | None,
    *,
    used_image: bool = False,
    expected_model: str = "",
) -> dict[str, Any] | None:
    """Poll sidecar без нового submit. None — нет джобы / мёртвая (sidecar снят)."""
    if dest_id is None:
        return None
    side = dest_id.with_suffix(dest_id.suffix + ".fal_id")
    if not side.is_file():
        return None
    saved = read_fal_side(side)
    rid_saved = (saved.get("request_id") or "").strip()
    if not rid_saved:
        return None
    saved_model = (saved.get("model_id") or "").strip()
    expected = (expected_model or "").strip()
    if (
        saved_model
        and expected
        and fal_model_family(saved_model) != fal_model_family(expected)
    ):
        # Мёртвый Kling sidecar после WIDE fallback не должен блокировать
        # Seedance (и наоборот). Poll сохранённой камеры: COMPLETED — забрать,
        # FAILED — снять sidecar и дать текущей камере новый submit.
        log.info(
            "fal sidecar other model saved=%s now=%s — poll saved",
            saved_model,
            expected,
        )
    model_id = saved_model or expected
    if not model_id:
        return None
    remember_fal_urls(
        rid_saved,
        status_url=saved.get("status_url") or "",
        response_url=saved.get("response_url") or "",
    )
    log.info("fal resume poll request_id=%s file=%s", rid_saved, dest_id.name)
    try:
        return await fal_poll(
            session,
            model_id,
            rid_saved,
            used_image=used_image,
            status_url=saved.get("status_url") or None,
            response_url=saved.get("response_url") or None,
        )
    except PipelineError as exc:
        if keep_fal_sidecar(exc):
            raise
        try:
            side.unlink()
        except OSError:
            pass
        log.warning(
            "fal resume dead model=%s — new submit (%s)",
            model_id,
            (exc.detail or exc.user_message or "")[:180],
        )
        return None


async def fal_run(
    session: aiohttp.ClientSession,
    model_id: str,
    payload: dict[str, Any],
    *,
    used_image: bool = False,
    dest_id: Path | None = None,
) -> dict[str, Any]:
    """Submit + poll. dest_id — sidecar с request_id для resume."""
    side = dest_id.with_suffix(dest_id.suffix + ".fal_id") if dest_id is not None else None
    resumed = await fal_try_resume(
        session, dest_id, used_image=used_image, expected_model=model_id
    )
    if resumed is not None:
        return resumed
    if side is not None and side.is_file():
        saved = read_fal_side(side)
        rid_saved = (saved.get("request_id") or "").strip()
        saved_model = (saved.get("model_id") or "").strip()
        if (
            rid_saved
            and saved_model
            and fal_model_family(saved_model) != fal_model_family(model_id)
        ):
            raise PipelineError(
                "fal.ai ещё держит готовый клип другой камеры. Не пересоздаю.",
                f"saved={saved_model} now={model_id}",
                code="fal_keep_sidecar",
            )
    submitted = await fal_submit(session, model_id, payload)
    rid = str(submitted.get("request_id") or "")
    if side is not None and rid:
        try:
            side.write_text(
                json.dumps(fal_side_payload(submitted, model_id=model_id), ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            log.warning("не записал fal request_id рядом с %s", dest_id.name if dest_id else "")
    log.info("fal submitted model=%s request_id=%s", model_id, rid)
    try:
        from live_status import note_runway_task

        note_runway_task(live_fal_id(model_id, rid), kind=model_id)
    except Exception:
        pass
    try:
        return await fal_poll(
            session,
            model_id,
            rid,
            used_image=used_image,
            status_url=submitted.get("status_url") if isinstance(submitted, dict) else None,
            response_url=submitted.get("response_url") if isinstance(submitted, dict) else None,
        )
    except PipelineError as exc:
        if side is not None and not keep_fal_sidecar(exc):
            try:
                side.unlink()
            except OSError:
                pass
        raise


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


async def to_fal_https_url(session: aiohttp.ClientSession, image: str) -> str:
    """Kling elements.frontal_image_url не принимает data URI — только https."""
    import base64
    import tempfile

    blob = (image or "").strip()
    if blob.startswith(("http://", "https://")):
        return blob
    if not blob.startswith("data:"):
        raise PipelineError("Kling element нужен https URL или data URI картинки.")
    header, sep, b64 = blob.partition(",")
    if not sep or not b64:
        raise PipelineError("Некорректный data URI для fal.ai.")
    raw = base64.b64decode(b64)
    mime = "image/jpeg"
    suffix = ".jpg"
    low = header.lower()
    if "image/png" in low:
        mime, suffix = "image/png", ".png"
    elif "image/webp" in low:
        mime, suffix = "image/webp", ".webp"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(raw)
        tmp = Path(fh.name)
    try:
        return await fal_storage_upload(session, tmp, mime, raw)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


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
