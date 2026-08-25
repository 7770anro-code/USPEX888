"""SQLite: клон голоса по user_id, водяной знак, последний ролик."""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

log = logging.getLogger("videobot")

_READY: set[str] = set()


def reset_for_tests() -> None:
    _READY.clear()


def data_dir() -> Path:
    path = Path(config.DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "videobot.sqlite3"


def last_video_path(user_id: int) -> Path:
    folder = data_dir() / "last"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{int(user_id)}.mp4"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = db_path()
    init_db()
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    path = db_path()
    key = str(path.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    if key not in _READY or not path.is_file():
        with sqlite3.connect(str(path), timeout=30) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS voices (
                    user_id INTEGER NOT NULL,
                    voice_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    tag TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'custom',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, voice_id)
                );
                CREATE TABLE IF NOT EXISTS prefs (
                    user_id INTEGER PRIMARY KEY,
                    watermark INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS last_videos (
                    user_id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS last_jobs (
                    user_id INTEGER PRIMARY KEY,
                    payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'draft',
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()
        _READY.add(key)
        _migrate_json_voices()


def _row_voice(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": str(row["voice_id"]),
        "name": str(row["name"] or "Мой голос"),
        "tag": str(row["tag"] or "свой"),
        "kind": str(row["kind"] or "custom"),
    }


def load_user_voices(user_id: int) -> list[dict[str, str]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT voice_id, name, tag, kind FROM voices WHERE user_id = ? "
            "ORDER BY CASE kind WHEN 'clone' THEN 0 ELSE 1 END, created_at DESC",
            (int(user_id),),
        ).fetchall()
    return [_row_voice(row) for row in rows]


def save_user_voice(user_id: int, voice: dict[str, str]) -> None:
    init_db()
    vid = str(voice.get("id") or "").strip()
    if not vid:
        return
    name = str(voice.get("name") or "Мой голос").strip() or "Мой голос"
    tag = str(voice.get("tag") or "свой").strip() or "свой"
    kind = str(voice.get("kind") or "custom").strip() or "custom"
    with _connect() as conn:
        if kind == "clone":
            conn.execute("DELETE FROM voices WHERE user_id = ? AND kind = 'clone'", (int(user_id),))
        conn.execute(
            "INSERT OR REPLACE INTO voices (user_id, voice_id, name, tag, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(user_id), vid, name, tag, kind, _now()),
        )
        conn.commit()


def set_cloned_voice(user_id: int, voice_id: str, name: str = "Мой голос") -> None:
    save_user_voice(
        user_id,
        {"id": voice_id, "name": name or "Мой голос", "tag": "клон", "kind": "clone"},
    )


def get_cloned_voice(user_id: int) -> dict[str, str] | None:
    voices = [v for v in load_user_voices(user_id) if v.get("kind") == "clone"]
    return voices[0] if voices else None


def delete_cloned_voice(user_id: int) -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT voice_id FROM voices WHERE user_id = ? AND kind = 'clone' LIMIT 1",
            (int(user_id),),
        ).fetchone()
        conn.execute("DELETE FROM voices WHERE user_id = ? AND kind = 'clone'", (int(user_id),))
        conn.commit()
    return str(row["voice_id"]) if row else None


def clear_user_voices(user_id: int) -> list[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT voice_id FROM voices WHERE user_id = ?",
            (int(user_id),),
        ).fetchall()
        conn.execute("DELETE FROM voices WHERE user_id = ?", (int(user_id),))
        conn.commit()
    return [str(row["voice_id"]) for row in rows]


def get_watermark(user_id: int) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT watermark FROM prefs WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    return bool(row["watermark"]) if row else False


def set_watermark(user_id: int, enabled: bool) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO prefs (user_id, watermark) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET watermark = excluded.watermark",
            (int(user_id), 1 if enabled else 0),
        )
        conn.commit()


def save_last_video(user_id: int, src: Path, title: str = "") -> Path:
    init_db()
    dest = last_video_path(user_id)
    src = Path(src)
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO last_videos (user_id, path, title, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET path = excluded.path, title = excluded.title, "
            "created_at = excluded.created_at",
            (int(user_id), str(dest), str(title or ""), _now()),
        )
        conn.commit()
    return dest


def get_last_video(user_id: int) -> Path | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT path FROM last_videos WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    if not row:
        path = last_video_path(user_id)
        return path if path.is_file() else None
    path = Path(str(row["path"]))
    return path if path.is_file() else None


def get_last_title(user_id: int) -> str:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT title FROM last_videos WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    return str(row["title"] or "") if row else ""


def save_last_job(user_id: int, payload: dict[str, Any], *, status: str = "draft") -> None:
    """Параметры последней пайплайн-съёмки, чтобы крутить правки до финала."""
    init_db()
    data = dict(payload or {})
    status = "final" if status == "final" else "draft"
    data["status"] = status
    blob = json.dumps(data, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO last_jobs (user_id, payload, status, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, "
            "status = excluded.status, updated_at = excluded.updated_at",
            (int(user_id), blob, status, _now()),
        )
        conn.commit()


def get_last_job(user_id: int) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload, status FROM last_jobs WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(str(row["payload"] or "{}"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    data["status"] = str(row["status"] or data.get("status") or "draft")
    return data


def mark_last_job_final(user_id: int) -> dict[str, Any] | None:
    job = get_last_job(user_id)
    if not job:
        return None
    save_last_job(user_id, job, status="final")
    job["status"] = "final"
    return job


def clear_last_job(user_id: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM last_jobs WHERE user_id = ?", (int(user_id),))
        conn.commit()


def _migrate_json_voices() -> None:
    folder = data_dir()
    for path in folder.glob("user_*.json"):
        stem = path.stem
        if not stem.startswith("user_"):
            continue
        try:
            user_id = int(stem.split("_", 1)[1])
        except ValueError:
            continue
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        voices = raw.get("voices") if isinstance(raw, dict) else raw
        if not isinstance(voices, list):
            continue
        for item in voices:
            if not isinstance(item, dict):
                continue
            vid = str(item.get("id") or "").strip()
            if not vid:
                continue
            save_user_voice(
                user_id,
                {
                    "id": vid,
                    "name": str(item.get("name") or "Мой голос"),
                    "tag": str(item.get("tag") or "свой"),
                    "kind": str(item.get("kind") or "custom"),
                },
            )
        bak = path.with_suffix(".json.bak")
        try:
            path.replace(bak)
        except OSError:
            log.warning("не смог убрать JSON голосов %s", path)
        log.info("перенёс голоса из %s в SQLite", path.name)
