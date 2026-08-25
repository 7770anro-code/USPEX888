"""Промпты I2V: Kling Element Reference, Seedance multi-ref, legacy Runway."""

from prompt_templates.kling import kling_video_prompt
from prompt_templates.seedance import seedance_video_prompt
from prompt_templates.runway import runway_video_prompt


def video_prompt_for(
    engine: str,
    continuity: str,
    scene_visual: str,
    camera: str = "",
    motion: str = "",
    *,
    style: str = "cinematic",
    photo_lock: bool = False,
) -> str:
    name = (engine or "").strip().lower()
    if name == "kling":
        return kling_video_prompt(
            continuity, scene_visual, camera, motion, style=style, photo_lock=photo_lock
        )
    if name == "seedance":
        return seedance_video_prompt(
            continuity, scene_visual, camera, motion, style=style, photo_lock=photo_lock
        )
    return runway_video_prompt(
        continuity, scene_visual, camera, motion, style=style, photo_lock=photo_lock
    )
