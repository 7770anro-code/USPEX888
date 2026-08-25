"""Seedance 2.5: явно расписать роль каждой референс-картинки."""

from __future__ import annotations

from pipeline import compose_runway_prompt


def seedance_video_prompt(
    continuity: str,
    scene_visual: str,
    camera: str = "",
    motion: str = "",
    *,
    style: str = "cinematic",
    photo_lock: bool = False,
) -> str:
    base = compose_runway_prompt(
        continuity,
        scene_visual,
        camera,
        motion,
        style=style,
        photo_lock=photo_lock,
    )
    if photo_lock:
        roles = (
            "@Image1 = frontal face and outfit of the same person, keep identity. "
            "Do not invent another character. "
        )
        return roles + base
    roles = (
        "@Image1 = master still of the scene and character plate "
        "(face if any, clothes, location, lighting). "
    )
    return roles + base
