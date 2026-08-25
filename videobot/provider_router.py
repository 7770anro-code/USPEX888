"""Маршрутизация Kling → Seedance → legacy Runway. Одна правка ROUTING — и Runway выключается."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp

import config
from pipeline import PipelineError
from providers.fal_client import FalClient, kling_provider, seedance_provider
from providers.legacy.runway_client import RunwayProvider

log = logging.getLogger("videobot")

# Убрать "legacy_runway" из списков — всегда только Kling+Seedance.
ROUTING: dict[str, list[str]] = {
    "real_photo": ["kling", "seedance", "legacy_runway"],
    "synthetic_multi_scene": ["seedance", "kling", "legacy_runway"],
    "night_pipeline": ["seedance", "kling", "legacy_runway"],
    "montage_generate": ["seedance", "kling", "legacy_runway"],
    "autorolik_face": ["kling", "seedance", "legacy_runway"],
    "autorolik_wide": ["seedance", "kling", "legacy_runway"],
}

MODE_DEFAULT = "synthetic_multi_scene"


def chain_for(mode: str) -> list[str]:
    raw = (mode or "").strip() or MODE_DEFAULT
    names = list(ROUTING.get(raw) or ROUTING[MODE_DEFAULT])
    if config.video_provider() == "runway":
        return ["legacy_runway"]
    force = (config.FAL_VIDEO_MODEL or "").strip().lower()
    if "kling" in force:
        names = ["kling"] + [n for n in names if n != "kling"]
    elif "seedance" in force:
        names = ["seedance"] + [n for n in names if n != "seedance"]
    return names


def get_provider(mode: str, session: aiohttp.ClientSession, *, engine: str | None = None) -> Any:
    name = (engine or (chain_for(mode) or ["kling"])[0]).strip()
    if name == "legacy_runway":
        return RunwayProvider(session)
    if name == "seedance":
        return seedance_provider(session)
    return kling_provider(session)


async def render_clip(
    session: aiohttp.ClientSession,
    prompt: str,
    seconds: int,
    dest: Path,
    *,
    prompt_image: str | None = None,
    quality: str = "optimal",
    clip_index: int = 1,
    clip_total: int = 1,
    seed: int | None = None,
    ratio: str | None = None,
    route_mode: str = MODE_DEFAULT,
    photo_lock: bool = False,
    references: list[str] | None = None,
    elements: list[str] | None = None,
) -> Path:
    """Все кнопки генерации идут сюда, не в конкретный вендор."""
    last: PipelineError | None = None
    for engine in chain_for(route_mode):
        try:
            if engine == "legacy_runway":
                if not config.RUNWAY_API_KEY:
                    continue
                return await RunwayProvider(session).render_clip(
                    session,
                    prompt,
                    seconds,
                    dest,
                    prompt_image=prompt_image,
                    quality=quality,
                    clip_index=clip_index,
                    clip_total=clip_total,
                    seed=seed,
                    ratio=ratio,
                )
            if not config.FAL_KEY:
                continue
            client = FalClient(session, engine=engine)
            frame = prompt_image or ""
            if engine == "seedance":
                return await client.generate_seedance(
                    session,
                    prompt,
                    frame,
                    seconds,
                    dest,
                    references=references,
                    multi_ref=route_mode in ("night_pipeline", "synthetic_multi_scene", "montage_generate")
                    and bool(references and len(references) > 1),
                )
            return await client.generate_kling(
                session,
                prompt,
                frame,
                seconds,
                dest,
                photo_lock=photo_lock,
                elements=elements,
            )
        except PipelineError as exc:
            last = exc
            if getattr(exc, "code", "") in ("credits", "moderation", "moderation_person"):
                log.warning("provider %s user-facing fail, try next: %s", engine, exc.code)
                if getattr(exc, "code", "") == "moderation_person":
                    raise
                if getattr(exc, "code", "") == "credits":
                    continue
                raise
            log.warning("provider %s failed, fallback: %s", engine, exc.detail or exc.user_message)
            continue
    if last:
        raise last
    raise PipelineError("Камера сейчас недоступна. Нужен FAL_KEY (или RUNWAY_API_KEY как запас).")


async def generate_still(
    session: aiohttp.ClientSession,
    prompt: str,
    dest_hint: Path | None = None,
    *,
    route_mode: str = MODE_DEFAULT,
) -> str:
    last: PipelineError | None = None
    for engine in chain_for(route_mode):
        try:
            if engine == "legacy_runway":
                if not config.RUNWAY_API_KEY:
                    continue
                from pipeline import _text_to_image_url_native

                return await _text_to_image_url_native(session, prompt, "720:1280", dest_hint)
            if not config.FAL_KEY:
                continue
            from fal_models import fal_still_url

            return await fal_still_url(session, prompt, dest_hint)
        except PipelineError as exc:
            last = exc
            if getattr(exc, "code", "") in ("credits", "moderation", "moderation_person"):
                if getattr(exc, "code", "") == "credits":
                    continue
                raise
            log.warning("still provider %s failed: %s", engine, exc.detail or exc)
            continue
    if last:
        raise last
    raise PipelineError("Не собрал первый кадр.")
