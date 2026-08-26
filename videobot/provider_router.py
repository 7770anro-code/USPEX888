"""Маршрутизация Kling / Seedance. Старый пайплайн без Runway; Авторолик может оставить тихий хвост."""

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

# Съёмка ролика (1 клик / своё фото / ночь / вайб / Авторолик) — только fal.ai.
# legacy Runway остаётся в файле providers/legacy/ для VIDEO_PROVIDER=runway и Act Two.
FAL_ONLY_MODES = frozenset(
    {
        "real_photo",
        "synthetic_multi_scene",
        "night_pipeline",
        "montage_generate",
        "autorolik_face",
        "autorolik_wide",
    }
)

ROUTING: dict[str, list[str]] = {
    "real_photo": ["kling", "seedance"],
    "synthetic_multi_scene": ["seedance", "kling"],
    "night_pipeline": ["seedance", "kling"],
    "montage_generate": ["seedance", "kling"],
    "autorolik_face": ["kling", "seedance"],
    "autorolik_wide": ["seedance", "kling"],
}

MODE_DEFAULT = "synthetic_multi_scene"


def is_fal_only_mode(mode: str) -> bool:
    raw = (mode or "").strip() or MODE_DEFAULT
    return raw in FAL_ONLY_MODES


def chain_for(mode: str) -> list[str]:
    raw = (mode or "").strip() or MODE_DEFAULT
    names = list(ROUTING.get(raw) or ROUTING[MODE_DEFAULT])
    fal_only = is_fal_only_mode(raw)
    if fal_only:
        names = [n for n in names if n != "legacy_runway"]
    elif config.video_provider() == "runway":
        return ["legacy_runway"]
    force = (config.FAL_VIDEO_MODEL or "").strip().lower()
    if "kling" in force:
        names = ["kling"] + [n for n in names if n != "kling"]
    elif "seedance" in force:
        names = ["seedance"] + [n for n in names if n != "seedance"]
    if fal_only:
        names = [n for n in names if n != "legacy_runway"]
        if not names:
            names = ["kling", "seedance"] if raw == "real_photo" else ["seedance", "kling"]
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
    skip_runway = False
    fal_only = is_fal_only_mode(route_mode)
    for engine in chain_for(route_mode):
        try:
            if engine == "legacy_runway":
                if fal_only:
                    continue
                if skip_runway:
                    log.warning("skip legacy_runway after fal validation error")
                    continue
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
                if photo_lock or elements or route_mode in ("autorolik_face", "real_photo"):
                    log.warning("skip seedance for FACE/photo_lock — partner rejects likenesses")
                    continue
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
            if "слишком долго" in (exc.user_message or "").lower():
                # Kling/Seedance ещё IN_PROGRESS — не бросать в Runway, sidecar жив для resume.
                raise
            if getattr(exc, "code", "") == "fal_keep_sidecar":
                raise
            detail = str(exc.detail or exc.user_message or "")
            status = getattr(exc, "status", None)
            if engine in ("kling", "seedance") and (
                status == 422
                or "value_error" in detail
                or "frontal_image_url" in detail
            ):
                skip_runway = True
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
    if fal_only:
        raise PipelineError("Камера сейчас недоступна. Нужен FAL_KEY.")
    raise PipelineError("Камера сейчас недоступна. Нужен FAL_KEY (или RUNWAY_API_KEY как запас).")


async def generate_still(
    session: aiohttp.ClientSession,
    prompt: str,
    dest_hint: Path | None = None,
    *,
    route_mode: str = MODE_DEFAULT,
) -> str:
    last: PipelineError | None = None
    fal_only = is_fal_only_mode(route_mode)
    for engine in chain_for(route_mode):
        try:
            if engine == "legacy_runway":
                if fal_only:
                    continue
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
