"""Legacy Runway: почти не описывать картинку, только движение и камеру."""

from __future__ import annotations

from pipeline import compose_runway_prompt


def runway_video_prompt(
    continuity: str,
    scene_visual: str,
    camera: str = "",
    motion: str = "",
    *,
    style: str = "cinematic",
    photo_lock: bool = False,
) -> str:
    return compose_runway_prompt(
        continuity,
        scene_visual,
        camera,
        motion,
        style=style,
        photo_lock=photo_lock,
    )
