"""Kling 3.0: движение/камера + @Element1, если своё фото (Element Reference)."""

from __future__ import annotations

import re

from pipeline import compose_runway_prompt

ELEMENT_TOKEN = "@Element1"

# Seedance пишет @ImageN = роль слота. Kling I2V это читает как индекс референса
# и даёт 422 «Invalid reference index 1 … Only 0 images provided».
_SEEDANCE_IMAGE_ROLE = re.compile(
    r"@Image\d+\s*(?:=|is)\s+.*?\."
    r"(?:\s+Do not invent another character\.)?"
    r"(?:\s+Face is not the subject\.)?"
    r"(?:\s+Do not lock identity to a person\.)?",
    re.IGNORECASE | re.DOTALL,
)
_SEEDANCE_IMAGE_TOKEN = re.compile(r"@Image\d+\b", re.IGNORECASE)


def strip_seedance_image_refs(prompt: str) -> str:
    """Убрать Seedance @ImageN из промпта. @Element1 (Kling FACE) не трогаем."""
    text = prompt or ""
    text = _SEEDANCE_IMAGE_ROLE.sub(" ", text)
    text = _SEEDANCE_IMAGE_TOKEN.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


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
