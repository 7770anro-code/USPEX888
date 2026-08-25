"""Единый fal.ai клиент: Kling 3.0 и Seedance 2.5. Один заголовок Key $FAL_API_KEY."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp

import config
from fal_api import (
    extract_fal_media_url,
    fal_download_media,
    fal_peek_status,
    fal_run,
    fal_submit,
    path_to_fal_url,
)
from fal_models import (
    FLUX_STILL,
    KLING_I2V_PRO,
    KLING_I2V_STD,
    SEEDANCE_I2V,
    SEEDANCE_REF,
    flux_still_payload,
    kling_i2v_payload,
    seedance_i2v_payload,
    seedance_ref_payload,
    quality_video_model,
)
from pipeline import PipelineError, write_runway_model
from providers.base import TaskStatus, VideoProvider

KLING_LIPSYNC = "fal-ai/kling-video/lipsync/audio-to-video"
MINIMAX_CLONE = "fal-ai/minimax/voice-clone"
MINIMAX_TTS = "fal-ai/minimax/speech-02-hd"
GOOGLE_TRYON = "google/virtual-try-on"
TOPAZ_INTERP = "topaz/interpolate/video"
TOPAZ_RESTORE = "topaz/restore/image"


class FalClient(VideoProvider):
    """Один клиент, два метода generate_kling / generate_seedance."""

    name = "fal"

    def __init__(self, session: aiohttp.ClientSession | None = None, *, engine: str = "kling") -> None:
        self.session = session
        self.engine = "seedance" if engine == "seedance" else "kling"

    def available(self) -> bool:
        return bool(config.FAL_KEY)

    async def generate_reference_image(
        self,
        prompt: str,
        references: list[str] | None = None,
    ) -> str:
        session = self._session()
        _ = references
        data = await fal_run(session, FLUX_STILL, flux_still_payload(prompt))
        url = extract_fal_media_url(data)
        if not url:
            raise PipelineError("fal.ai не вернул still.")
        return url

    async def generate_video(
        self,
        prompt: str,
        start_frame: str | None,
        duration: int,
        aspect_ratio: str = "9:16",
    ) -> str:
        session = self._session()
        if self.engine == "seedance":
            model_id, payload = self._seedance_body(prompt, start_frame, duration, aspect_ratio)
        else:
            model_id, payload = self._kling_body(prompt, start_frame, duration)
        submitted = await fal_submit(session, model_id, payload)
        rid = str(submitted.get("request_id") or "")
        if not rid:
            raise PipelineError("fal.ai не вернул request_id.")
        return f"{self.engine}:{model_id}:{rid}"

    async def poll_status(self, task_id: str) -> TaskStatus:
        session = self._session()
        engine, model_id, rid = _split_fal_task(task_id)
        data = await fal_peek_status(session, model_id, rid)
        status = str((data or {}).get("status") or "").upper()
        url = extract_fal_media_url(data)
        percent = 20
        state = "running"
        if status in ("IN_QUEUE", "QUEUED"):
            percent = 12
        elif status in ("IN_PROGRESS", "RUNNING"):
            percent = 55
        elif status in ("COMPLETED", "SUCCEEDED"):
            percent = 100
            state = "done"
        elif status in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
            percent = 0
            state = "failed"
        if url:
            percent = 100
            state = "done"
        stage = engine
        if "seedance" in (model_id or ""):
            stage = "seedance"
        elif "kling" in (model_id or ""):
            stage = "kling"
        return TaskStatus(
            state=state,
            percent=percent,
            stage=stage,
            url=url,
            model=model_id,
            error=str((data or {}).get("error") or ""),
            raw=data if isinstance(data, dict) else {},
        )

    async def lip_sync(self, video_url: str, audio_url: str) -> str:
        session = self._session()
        data = await fal_run(
            session,
            KLING_LIPSYNC,
            {"video_url": video_url, "audio_url": audio_url},
        )
        url = extract_fal_media_url(data)
        if not url:
            raise PipelineError("Kling lip-sync не вернул видео.")
        return url

    async def generate_kling(
        self,
        session: aiohttp.ClientSession,
        prompt: str,
        start_frame: str,
        seconds: int,
        dest: Path,
        *,
        photo_lock: bool = False,
    ) -> Path:
        model_id, payload = self._kling_body(prompt, start_frame, seconds, photo_lock=photo_lock)
        data = await fal_run(session, model_id, payload, used_image=bool(start_frame), dest_id=dest)
        out = await fal_download_media(session, data, dest)
        write_runway_model(dest, model_id)
        return out

    async def generate_seedance(
        self,
        session: aiohttp.ClientSession,
        prompt: str,
        start_frame: str,
        seconds: int,
        dest: Path,
        *,
        references: list[str] | None = None,
        multi_ref: bool = False,
    ) -> Path:
        model_id, payload = self._seedance_body(
            prompt,
            start_frame,
            seconds,
            "9:16",
            references=references,
            multi_ref=multi_ref,
        )
        data = await fal_run(session, model_id, payload, used_image=bool(start_frame), dest_id=dest)
        out = await fal_download_media(session, data, dest)
        write_runway_model(dest, model_id)
        return out

    def _kling_body(
        self,
        prompt: str,
        start_frame: str | None,
        seconds: int,
        *,
        photo_lock: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        override = (config.FAL_VIDEO_MODEL or "").strip()
        model_id = override if override.startswith("fal-ai/kling") else KLING_I2V_PRO
        if not start_frame:
            raise PipelineError("Kling 3.0 I2V нужен start_image_url (кадр или still).")
        payload = kling_i2v_payload(prompt, start_frame, seconds, photo_lock=photo_lock)
        return model_id, payload

    def _seedance_body(
        self,
        prompt: str,
        start_frame: str | None,
        seconds: int,
        aspect_ratio: str,
        *,
        references: list[str] | None = None,
        multi_ref: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        refs = [r for r in (references or []) if r]
        if start_frame and start_frame not in refs:
            refs = [start_frame] + refs
        if multi_ref or len(refs) > 1:
            if not refs:
                raise PipelineError("Seedance reference-to-video нужны картинки персонажа.")
            return SEEDANCE_REF, seedance_ref_payload(prompt, refs, seconds)
        if not start_frame:
            raise PipelineError("Seedance I2V нужен image_url.")
        _ = aspect_ratio
        return SEEDANCE_I2V, seedance_i2v_payload(prompt, start_frame, seconds)

    def _session(self) -> aiohttp.ClientSession:
        if self.session is None:
            raise RuntimeError("FalClient needs aiohttp session")
        return self.session


def _split_fal_task(task_id: str) -> tuple[str, str, str]:
    raw = (task_id or "").strip()
    if raw.startswith("fal:"):
        rest = raw[4:]
        model_id, sep, rid = rest.rpartition(":")
        if sep and model_id and rid:
            engine = "seedance" if "seedance" in model_id else "kling"
            return engine, model_id, rid
    parts = raw.split(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return "kling", parts[0], parts[1]
    return "kling", KLING_I2V_PRO, raw


def kling_provider(session: aiohttp.ClientSession) -> FalClient:
    return FalClient(session, engine="kling")


def seedance_provider(session: aiohttp.ClientSession) -> FalClient:
    return FalClient(session, engine="seedance")
