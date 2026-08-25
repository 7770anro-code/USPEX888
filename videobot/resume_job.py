"""Пауза пайплайна при нехватке кредитов Runway и продолжение с диска.

Сценарий, озвучка и готовые клипы лежат в стабильной папке
`{WORK_DIR}/{chat_id}_resume`, а не в `{chat_id}_{timestamp}` — иначе повторный
«Создать» не видит уже оплаченное.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import config

log = logging.getLogger("videobot")

CHECKPOINT_NAME = "checkpoint.json"
SCRIPT_NAME = "script.json"
MP3_MIN_BYTES = 200
MP4_MIN_BYTES = 10_000
IMAGE_MIN_BYTES = 1000


def resume_work_dir(user_id: int) -> Path:
    return Path(config.WORK_DIR) / f"{int(user_id)}_resume"


def checkpoint_path(work_dir: Path) -> Path:
    return Path(work_dir) / CHECKPOINT_NAME


def script_path(work_dir: Path) -> Path:
    return Path(work_dir) / SCRIPT_NAME


def file_ready(path: Path, *, min_bytes: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= int(min_bytes)
    except OSError:
        return False


def load_checkpoint(work_dir: Path) -> dict[str, Any] | None:
    path = checkpoint_path(work_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def save_checkpoint(work_dir: Path, **fields: Any) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    data = load_checkpoint(work_dir) or {}
    for key, value in fields.items():
        if value is None and key not in data:
            continue
        data[key] = value
    path = checkpoint_path(work_dir)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_script(work_dir: Path) -> dict[str, Any] | None:
    path = script_path(work_dir)
    ckpt = load_checkpoint(work_dir) or {}
    raw: Any = None
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None
    if not isinstance(raw, dict):
        nested = ckpt.get("script")
        raw = nested if isinstance(nested, dict) else None
    if not script_is_resumable(raw):
        return None
    return raw


def save_script(work_dir: Path, script: dict[str, Any]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    script_path(work_dir).write_text(
        json.dumps(script, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    save_checkpoint(work_dir, script=script, stage="script")


def script_is_resumable(script: Any) -> bool:
    if not isinstance(script, dict):
        return False
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return False
    for scene in scenes:
        if not isinstance(scene, dict):
            return False
        if not str(scene.get("narration") or "").strip():
            return False
        if not str(scene.get("visual_prompt") or "").strip():
            return False
    return True


def credits_paused(work_dir: Path) -> bool:
    data = load_checkpoint(work_dir)
    return bool(data and data.get("credits_paused"))


def mark_credits_pause(work_dir: Path, **fields: Any) -> dict[str, Any]:
    return save_checkpoint(work_dir, credits_paused=True, **fields)


def clear_credits_pause(work_dir: Path) -> None:
    save_checkpoint(work_dir, credits_paused=False)


def wipe_resume(user_id: int) -> None:
    import shutil

    path = resume_work_dir(user_id)
    shutil.rmtree(path, ignore_errors=True)


def scene_count(work_dir: Path, fallback: int = 4) -> int:
    script = load_script(work_dir)
    if script and isinstance(script.get("scenes"), list):
        return max(1, len(script["scenes"]))
    ckpt = load_checkpoint(work_dir) or {}
    run = ckpt.get("run") if isinstance(ckpt.get("run"), dict) else {}
    try:
        return max(1, int(run.get("n_scenes") or ckpt.get("n_scenes") or fallback))
    except (TypeError, ValueError):
        return fallback


def resume_progress(work_dir: Path, n_scenes: int | None = None) -> dict[str, Any]:
    n = int(n_scenes or scene_count(work_dir))
    tts = sum(1 for i in range(n) if file_ready(work_dir / f"n{i}.mp3", min_bytes=MP3_MIN_BYTES))
    clips = sum(1 for i in range(n) if file_ready(work_dir / f"c{i}.mp4", min_bytes=MP4_MIN_BYTES))
    muxed = sum(1 for i in range(n) if file_ready(work_dir / f"m{i}.mp4", min_bytes=MP4_MIN_BYTES))
    still = (
        file_ready(work_dir / "bible_still.png", min_bytes=IMAGE_MIN_BYTES)
        or file_ready(work_dir / "banana_still.png", min_bytes=IMAGE_MIN_BYTES)
        or file_ready(work_dir / "user_photo.jpg", min_bytes=IMAGE_MIN_BYTES)
    )
    return {
        "n_scenes": n,
        "has_script": load_script(work_dir) is not None,
        "has_still": still,
        "tts": tts,
        "clips": clips,
        "muxed": muxed,
        "credits_paused": credits_paused(work_dir),
    }


def format_resume_progress(work_dir: Path, n_scenes: int | None = None) -> str:
    p = resume_progress(work_dir, n_scenes)
    n = int(p["n_scenes"])
    script = "да" if p["has_script"] else "нет"
    still = "да" if p["has_still"] else "нет"
    return (
        f"Сценарий: {script}. Первый кадр: {still}. "
        f"Озвучка: {p['tts']}/{n}. Клипы Runway: {p['clips']}/{n}. Монтаж: {p['muxed']}/{n}."
    )


def next_scene_to_render(work_dir: Path, n_scenes: int | None = None) -> int:
    """0-based индекс первой сцены без готового клипа. n_scenes, если всё снято."""
    n = int(n_scenes or scene_count(work_dir))
    for i in range(n):
        if not file_ready(work_dir / f"c{i}.mp4", min_bytes=MP4_MIN_BYTES):
            return i
    return n


def run_kwargs_from_checkpoint(work_dir: Path) -> dict[str, Any] | None:
    data = load_checkpoint(work_dir) or {}
    run = data.get("run")
    if not isinstance(run, dict):
        return None
    idea = str(run.get("idea") or "").strip()
    if not idea and load_script(work_dir):
        script = load_script(work_dir) or {}
        idea = str(script.get("plot") or script.get("title") or "").strip()
    if not idea:
        return None
    settings = run.get("voice_settings")
    if not isinstance(settings, dict):
        settings = None
    revisions = run.get("revisions")
    if not isinstance(revisions, list):
        revisions = None
    return {
        "idea": idea,
        "user_script": bool(run.get("user_script")),
        "voice_id": str(run.get("voice_id") or "") or None,
        "photo_file_id": str(run.get("photo_file_id") or "") or None,
        "voice_name": str(run.get("voice_name") or "Сара"),
        "consent_verified": bool(run.get("consent_verified")),
        "n_scenes": int(run.get("n_scenes") or scene_count(work_dir)),
        "extra_brief": str(run.get("extra_brief") or ""),
        "voice_settings": settings,
        "camera": str(run.get("camera") or ""),
        "motion": str(run.get("motion") or ""),
        "quality": str(run.get("quality") or "optimal"),
        "style": str(run.get("style") or "cinematic"),
        "watermark": bool(run.get("watermark")),
        "hook": str(run.get("hook") or ""),
        "revisions": revisions,
        "preset_brief": str(run.get("preset_brief") or ""),
        "kind": str(run.get("kind") or "motivational"),
        "dynamic_pacing": bool(run.get("dynamic_pacing")),
    }
