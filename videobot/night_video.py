"""Модуль 2: топ-N идей → Runway+ElevenLabs, без фото и без клона голоса."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import config
import pipeline as pipeline_mod
from night_accounts import Account
from night_circuit import ELEVEN, ELEVEN_GATE, RUNWAY, RUNWAY_GATE, CircuitOpen
from presets import camera_prompt, estimate_cost, motion_prompt, voice_settings_payload
from pipeline import PipelineError, build_video, is_runway_safety_fail, is_runway_person_moderation

log = logging.getLogger("videobot.night")


def estimate_job(idea: dict[str, Any], account: Account, *, n_scenes: int = 4) -> dict[str, Any]:
    cost = estimate_cost(
        n_scenes=n_scenes,
        quality=account.quality,
        text=f"{idea.get('plot') or ''} {idea.get('caption') or ''}",
        need_still=True,
    )
    log.info(
        "credits account=%s title=%r runway≈%s eleven_chars≈%s",
        account.id,
        idea.get("title"),
        cost.get("runway"),
        cost.get("eleven_chars"),
    )
    return cost


async def render_idea(
    idea: dict[str, Any],
    account: Account,
    work_dir: Path,
    dest_mp4: Path,
    *,
    n_scenes: int = 4,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if not RUNWAY.allow() or not ELEVEN.allow():
        raise CircuitOpen("runway" if not RUNWAY.allow() else "elevenlabs")
    await RUNWAY_GATE.wait()
    await ELEVEN_GATE.wait()
    brief = (
        "Только синтетические сцены, без реальных людей и узнаваемых лиц. "
        f"Тип: {idea.get('kind')}. Хук: {idea.get('hook') or idea.get('title')}. "
        f"{idea.get('plot') or ''}"
    )
    pipeline_mod.CANCEL_ON_TIMEOUT = False
    try:
        video, script = await build_video(
            str(idea.get("plot") or idea.get("title") or ""),
            work_dir,
            None,
            ratio="720:1280",
            style=account.style,
            voice_id=account.voice_id,
            reference_image=None,
            user_script=False,
            n_scenes=n_scenes,
            extra_brief=brief,
            voice_settings=voice_settings_payload(account.delivery, account.speed),
            camera=camera_prompt("push" if account.theme != "absurd" else "lock"),
            motion=motion_prompt("nat" if account.theme != "absurd" else "dyn"),
            quality=account.quality,
            watermark=False,
        )
        RUNWAY.ok()
        ELEVEN.ok()
    except PipelineError as exc:
        if getattr(exc, "code", "") == "credits" or "credit" in (exc.detail or "").lower():
            RUNWAY.fail()
        if is_runway_safety_fail("", exc.detail) or is_runway_person_moderation("", exc.detail):
            RUNWAY.fail()
            exc.code = "moderation"
        raise
    finally:
        pipeline_mod.CANCEL_ON_TIMEOUT = True
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    src = Path(video)
    if src.resolve() != dest_mp4.resolve():
        shutil.copyfile(src, dest_mp4)
    cost = estimate_job(idea, account, n_scenes=n_scenes)
    log.info("video ready account=%s path=%s credits=%s", account.id, dest_mp4, cost.get("runway"))
    return dest_mp4, script, cost
