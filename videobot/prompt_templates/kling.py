"""Kling 3.0: движение/камера + @Element1, если своё фото (Element Reference)."""

from __future__ import annotations

from pipeline import compose_runway_prompt

ELEMENT_TOKEN = "@Element1"


def kling_video_prompt(
    continuity: str,
    scene_visual: str,
    camera: str = "",
    motion: str = "",
    *,
    style: str = "cinematic",
    photo_lock: bool = False,
    character_lock: bool = True,
) -> str:
    base = compose_runway_prompt(
        continuity,
        scene_visual,
        camera,
        motion,
        style=style,
        photo_lock=photo_lock,
        character_lock=character_lock,
    )
    if not photo_lock:
        return base
    if ELEMENT_TOKEN in base:
        return base
    return f"{ELEMENT_TOKEN} is the same person, same face and clothes. {base}"
