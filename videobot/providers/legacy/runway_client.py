"""Legacy Runway. Логика HTTP не менялась — обёртка VideoProvider над pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp

import config
from providers.base import TaskStatus, VideoProvider


class RunwayProvider(VideoProvider):
    name = "legacy_runway"

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self.session = session

    def available(self) -> bool:
        return bool(config.RUNWAY_API_KEY)

    async def generate_reference_image(
        self,
        prompt: str,
        references: list[str] | None = None,
    ) -> str:
        from pipeline import _text_to_image_url_native

        session = self.session
        if session is None:
            raise RuntimeError("RunwayProvider needs aiohttp session")
        _ = references
        return await _text_to_image_url_native(session, prompt, "720:1280")

    async def generate_video(
        self,
        prompt: str,
        start_frame: str | None,
        duration: int,
        aspect_ratio: str = "9:16",
    ) -> str:
        from pipeline import _runway_submit, runway_video_payload, video_models_for_quality

        session = self.session
        if session is None:
            raise RuntimeError("RunwayProvider needs aiohttp session")
        i2v, _t2v = video_models_for_quality("optimal")
        ratio = "720:1280" if aspect_ratio in ("9:16", "720:1280") else "720:1280"
        payload = runway_video_payload(
            i2v, prompt, ratio, int(duration), prompt_image=start_frame
        )
        path = "/v1/image_to_video" if start_frame else "/v1/text_to_video"
        task_id, _model = await _runway_submit(
            session, path, payload, used_image=bool(start_frame)
        )
        return f"runway:{task_id}"

    async def poll_status(self, task_id: str) -> TaskStatus:
        from pipeline import fetch_runway_task

        session = self.session
        if session is None:
            raise RuntimeError("RunwayProvider needs aiohttp session")
        rid = task_id.split(":", 1)[-1]
        data = await fetch_runway_task(session, rid)
        status = str((data or {}).get("status") or "").upper()
        percent = 50
        if status in ("SUCCEEDED", "COMPLETED"):
            percent = 100
        elif status in ("PENDING", "RUNNING", "THROTTLED"):
            percent = 40
        elif status in ("FAILED", "CANCELLED", "CANCELED"):
            percent = 0
        url = ""
        out = (data or {}).get("output") or (data or {}).get("artifacts") or []
        if isinstance(out, list) and out:
            first = out[0]
            if isinstance(first, dict):
                url = str(first.get("url") or "")
            elif isinstance(first, str):
                url = first
        return TaskStatus(
            state=status or "unknown",
            percent=percent,
            stage="runway",
            url=url,
            error=str((data or {}).get("failure") or (data or {}).get("error") or ""),
            model="legacy_runway",
            raw=data if isinstance(data, dict) else {},
        )

    async def lip_sync(self, video_url: str, audio_url: str) -> str:
        _ = video_url, audio_url
        return ""

    async def render_clip(
        self,
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
    ) -> Path:
        from pipeline import _runway_clip_native

        return await _runway_clip_native(
            session,
            prompt,
            seconds,
            dest,
            ratio=ratio,
            prompt_image=prompt_image,
            clip_index=clip_index,
            clip_total=clip_total,
            seed=seed,
            quality=quality,
        )
