"""Модели fal.ai: Kling 3.0, Seedance 2.5, Flux still, Topaz, примерка одежды."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp

import config
from fal_api import fal_download_media, fal_run, path_to_fal_url
from pipeline import PipelineError, write_runway_model

# Быстро = Seedance 2.5 I2V. Оптимально = Kling 3.0 Pro I2V.
KLING_I2V_PRO = "fal-ai/kling-video/v3/pro/image-to-video"
KLING_I2V_STD = "fal-ai/kling-video/v3/standard/image-to-video"
KLING_T2V_PRO = "fal-ai/kling-video/v3/pro/text-to-video"
SEEDANCE_I2V = "bytedance/seedance-2.5/image-to-video"
SEEDANCE_T2V = "bytedance/seedance-2.5/text-to-video"
FLUX_STILL = "fal-ai/flux/schnell"
TOPAZ_VIDEO = "fal-ai/topaz/upscale/video"
TOPAZ_IMAGE = "fal-ai/topaz/upscale/image"
TRYON = "fal-ai/image-apps-v2/virtual-try-on"

KLING_DURATIONS = frozenset(range(3, 16))


def use_fal() -> bool:
    provider = (config.VIDEO_PROVIDER or "fal").strip().lower()
    return provider in ("fal", "fal.ai", "kling", "seedance")


def kling_duration(seconds: int) -> str:
    sec = int(seconds or 5)
    if sec < 3:
        sec = 3
    if sec > 15:
        sec = 15
    if sec not in KLING_DURATIONS:
        sec = 5 if sec < 8 else 10
    return str(sec)


def seedance_duration(seconds: int) -> int:
    sec = int(seconds or 5)
    return max(4, min(30, sec))


def quality_video_model(quality: str) -> str:
    override = (config.FAL_VIDEO_MODEL or "").strip()
    if override:
        return override
    if (quality or "") == "fast":
        return SEEDANCE_I2V
    return KLING_I2V_PRO


def i2v_fallback_models(primary: str) -> list[str]:
    chain: list[str] = []
    for name in (primary, KLING_I2V_STD, SEEDANCE_I2V, KLING_I2V_PRO):
        if name and name not in chain:
            chain.append(name)
    return chain


def kling_i2v_payload(
    prompt: str,
    image_url: str,
    seconds: int,
    *,
    end_image_url: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "prompt": (prompt or "").strip() or "cinematic motion, photoreal, vertical 9:16",
        "start_image_url": image_url,
        "duration": kling_duration(seconds),
        "generate_audio": False,
        "negative_prompt": "blur, distort, low quality, extra people, watermark, text overlay",
    }
    if end_image_url:
        body["end_image_url"] = end_image_url
    return body


def seedance_i2v_payload(prompt: str, image_url: str, seconds: int) -> dict[str, Any]:
    return {
        "prompt": (prompt or "").strip() or "cinematic motion, photoreal, vertical 9:16",
        "image_url": image_url,
        "duration": seedance_duration(seconds),
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "generate_audio": False,
    }


def kling_t2v_payload(prompt: str, seconds: int) -> dict[str, Any]:
    return {
        "prompt": (prompt or "").strip(),
        "duration": kling_duration(seconds),
        "aspect_ratio": "9:16",
        "generate_audio": False,
        "negative_prompt": "blur, distort, low quality, watermark, text overlay",
    }


def seedance_t2v_payload(prompt: str, seconds: int) -> dict[str, Any]:
    return {
        "prompt": (prompt or "").strip(),
        "duration": seedance_duration(seconds),
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "generate_audio": False,
    }


def video_payload(model_id: str, prompt: str, image_url: str, seconds: int) -> dict[str, Any]:
    if model_id.startswith("bytedance/seedance"):
        if image_url:
            return seedance_i2v_payload(prompt, image_url, seconds)
        return seedance_t2v_payload(prompt, seconds)
    if image_url:
        return kling_i2v_payload(prompt, image_url, seconds)
    return kling_t2v_payload(prompt, seconds)


def flux_still_payload(prompt: str) -> dict[str, Any]:
    return {
        "prompt": (prompt or "").strip()[:2000] or "cinematic still, vertical 9:16, photoreal",
        "image_size": "portrait_16_9",
        "num_images": 1,
    }


def topaz_video_payload(video_url: str, *, upscale: float = 2.0) -> dict[str, Any]:
    return {
        "video_url": video_url,
        "model": "Proteus",
        "upscale_factor": max(1.0, min(4.0, float(upscale))),
    }


def topaz_image_payload(image_url: str, *, upscale: float = 2.0) -> dict[str, Any]:
    return {
        "image_url": image_url,
        "upscale_factor": max(1.0, min(4.0, float(upscale))),
    }


def tryon_payload(person_url: str, clothing_url: str) -> dict[str, Any]:
    return {
        "person_image_url": person_url,
        "clothing_image_url": clothing_url,
        "preserve_pose": True,
        "aspect_ratio": "9:16",
    }


def as_file_url(image: str) -> str:
    """fal принимает https URL или data URI."""
    blob = (image or "").strip()
    if blob.startswith(("http://", "https://", "data:")):
        return blob
    raise PipelineError("Нужен URL или data URI картинки для fal.ai.")


async def fal_render_clip(
    session: aiohttp.ClientSession,
    prompt: str,
    seconds: int,
    dest: Path,
    *,
    prompt_image: str | None = None,
    quality: str = "optimal",
) -> Path:
    primary = quality_video_model(quality)
    image = as_file_url(prompt_image) if prompt_image else ""
    last_exc: PipelineError | None = None
    chain = i2v_fallback_models(primary) if image else [primary]
    for idx, model_id in enumerate(chain):
        payload = video_payload(model_id, prompt, image, seconds)
        try:
            data = await fal_run(
                session,
                model_id,
                payload,
                used_image=bool(image),
                dest_id=dest,
            )
            out = await fal_download_media(session, data, dest)
            write_runway_model(dest, model_id)
            return out
        except PipelineError as exc:
            last_exc = exc
            if getattr(exc, "code", "") in ("credits", "moderation", "moderation_person"):
                raise
            status = getattr(exc, "status", None)
            if status in (400, 404, 422) and idx < len(chain) - 1:
                continue
            if idx < len(chain) - 1 and "HTTP 4" in (exc.detail or ""):
                continue
            raise
    if last_exc:
        raise last_exc
    raise PipelineError("fal.ai не вернул клип.")


async def fal_still_url(
    session: aiohttp.ClientSession,
    prompt: str,
    dest_hint: Path | None = None,
) -> str:
    data = await fal_run(
        session,
        FLUX_STILL,
        flux_still_payload(prompt),
        dest_id=dest_hint,
    )
    from fal_api import extract_fal_media_url

    url = extract_fal_media_url(data)
    if not url:
        raise PipelineError("fal.ai не вернул still.")
    if dest_hint is not None:
        write_runway_model(dest_hint, FLUX_STILL)
    return url


async def fal_topaz_upscale(
    session: aiohttp.ClientSession,
    video_url: str,
    dest: Path,
    *,
    upscale: float = 2.0,
) -> Path:
    data = await fal_run(
        session,
        TOPAZ_VIDEO,
        topaz_video_payload(video_url, upscale=upscale),
        dest_id=dest,
    )
    out = await fal_download_media(session, data, dest)
    write_runway_model(dest, TOPAZ_VIDEO)
    return out


async def fal_topaz_image(
    session: aiohttp.ClientSession,
    image_url: str,
    dest: Path,
    *,
    upscale: float = 2.0,
) -> Path:
    data = await fal_run(
        session,
        TOPAZ_IMAGE,
        topaz_image_payload(image_url, upscale=upscale),
        dest_id=dest,
    )
    out = await fal_download_media(session, data, dest)
    write_runway_model(dest, TOPAZ_IMAGE)
    return out


async def fal_upscale_file(
    session: aiohttp.ClientSession,
    src: Path,
    dest: Path,
    *,
    is_image: bool,
) -> Path:
    url = await path_to_fal_url(session, src)
    if is_image:
        return await fal_topaz_image(session, url, dest)
    return await fal_topaz_upscale(session, url, dest)


async def fal_virtual_tryon(
    session: aiohttp.ClientSession,
    person_url: str,
    clothing_url: str,
    dest: Path,
) -> Path:
    data = await fal_run(
        session,
        TRYON,
        tryon_payload(person_url, clothing_url),
        used_image=True,
        dest_id=dest,
    )
    out = await fal_download_media(session, data, dest)
    write_runway_model(dest, TRYON)
    return out
