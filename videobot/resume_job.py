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


def should_wipe_resume(*, wipe: bool = False, paused: bool = False) -> bool:
    """Стереть диск только если нет credits_paused.

    Mini App «Снять» передаёт wipe=True даже при зависшей досъёмке.
    Pause на диске важнее: сценарий, озвучка и готовые клипы остаются.
    Явный «Начать заново» сначала вызывает wipe_resume — тогда paused уже False.
    """
    _ = wipe
    return not bool(paused)


def resume_shoot_plan(work_dir: Path, n_scenes: int | None = None) -> dict[str, Any]:
    """Что сделает «Продолжить съёмку»: какие сцены skip, какие снять, каким движком."""
    script = load_script(work_dir)
    n = int(n_scenes or scene_count(work_dir))
    scenes = script.get("scenes") if isinstance(script, dict) else None
    if not isinstance(scenes, list):
        scenes = []
    autorolik = str((script or {}).get("kind") or "") == "autorolik" or any(
        isinstance(s, dict) and "face_scene" in s for s in scenes
    )
    route_for_scene = None
    if autorolik:
        from autorolik import route_for_scene as _route_for_scene

        route_for_scene = _route_for_scene
    steps: list[dict[str, Any]] = []
    for i in range(n):
        scene = scenes[i] if i < len(scenes) and isinstance(scenes[i], dict) else {}
        muxed = file_ready(work_dir / f"m{i}.mp4", min_bytes=MP4_MIN_BYTES)
        clip = file_ready(work_dir / f"c{i}.mp4", min_bytes=MP4_MIN_BYTES)
        tts = file_ready(work_dir / f"n{i}.mp3", min_bytes=MP3_MIN_BYTES)
        sidecar = (work_dir / f"c{i}.mp4.fal_id").is_file()
        if muxed:
            action = "skip_muxed"
        elif clip:
            action = "mux_existing_clip"
        else:
            action = "render_clip"
        route = ""
        engine = ""
        if autorolik:
            face = bool(scene.get("face_scene"))
            route = route_for_scene(scene) if scene and route_for_scene else (
                "autorolik_face" if face else "autorolik_wide"
            )
            engine = "kling" if route == "autorolik_face" else "seedance"
        elif action == "render_clip":
            route = "real_photo" if (
                file_ready(work_dir / "user_photo.jpg", min_bytes=IMAGE_MIN_BYTES)
                or file_ready(work_dir / "user_photo_1.jpg", min_bytes=IMAGE_MIN_BYTES)
            ) else "synthetic_multi_scene"
            engine = "kling" if route == "real_photo" else "seedance"
        redo_tts = action != "skip_muxed" and not tts
        steps.append(
            {
                "index": i,
                "action": action,
                "tts": tts,
                "clip": clip,
                "muxed": muxed,
                "sidecar": sidecar,
                "redo_tts": redo_tts,
                "route": route,
                "engine": engine,
                "face_scene": scene.get("face_scene") if "face_scene" in scene else None,
            }
        )
    keep = {
        "script": script is not None,
        "tts_keep": [i for i in range(n) if file_ready(work_dir / f"n{i}.mp3", min_bytes=MP3_MIN_BYTES)],
        "clip_keep": [i for i in range(n) if file_ready(work_dir / f"c{i}.mp4", min_bytes=MP4_MIN_BYTES)],
        "mux_keep": [i for i in range(n) if file_ready(work_dir / f"m{i}.mp4", min_bytes=MP4_MIN_BYTES)],
    }
    return {
        "n_scenes": n,
        "autorolik": autorolik,
        "credits_paused": credits_paused(work_dir),
        "has_script": script is not None,
        "wipe": should_wipe_resume(wipe=False, paused=credits_paused(work_dir)),
        "wipe_even_if_requested": should_wipe_resume(
            wipe=True, paused=credits_paused(work_dir)
        ),
        "next_render": next_scene_to_render(work_dir, n),
        "keep": keep,
        "steps": steps,
    }


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
        f"Озвучка: {p['tts']}/{n}. Клипы: {p['clips']}/{n}. Монтаж: {p['muxed']}/{n}."
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
    ids = [str(x) for x in (run.get("photo_file_ids") or []) if x]
    fid = str(run.get("photo_file_id") or "") or None
    if fid and fid not in ids:
        ids = [fid] + ids
    return {
        "idea": idea,
        "user_script": bool(run.get("user_script")),
        "voice_id": str(run.get("voice_id") or "") or None,
        "photo_file_id": fid or (ids[0] if ids else None),
        "photo_file_ids": ids or None,
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
        "route_mode": str(run.get("route_mode") or "") or None,
    }
