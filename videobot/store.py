"""SQLite: клон голоса по user_id, водяной знак, последний ролик, ночные слоты."""

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
                """
            )
            conn.commit()
        _READY.add(key)
        _migrate_json_voices()
    _ensure_night_tables()


_NIGHT_SCHEMA = """
CREATE TABLE IF NOT EXISTS night_jobs (
    run_date TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    status TEXT NOT NULL,
    preset TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    platforms TEXT NOT NULL DEFAULT '',
    quality TEXT NOT NULL DEFAULT '',
    runway_credits INTEGER NOT NULL DEFAULT 0,
    outbox TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_date, slot_id)
);
CREATE TABLE IF NOT EXISTS night_runs (
    run_id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    mode TEXT NOT NULL,
    planned INTEGER NOT NULL DEFAULT 0,
    rendered INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    runway_used INTEGER NOT NULL DEFAULT 0,
    report TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _ensure_night_tables() -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.executescript(_NIGHT_SCHEMA)
        conn.commit()


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


def upsert_night_job(
    *,
    run_date: str,
    slot_id: str,
    status: str,
    preset: str = "",
    topic: str = "",
    platforms: list[str] | None = None,
    quality: str = "",
    runway_credits: int = 0,
    outbox: str = "",
    error: str = "",
) -> None:
    init_db()
    now = _now()
    plats = ",".join(platforms or [])
    with _connect() as conn:
        conn.execute(
            "INSERT INTO night_jobs (run_date, slot_id, status, preset, topic, platforms, "
            "quality, runway_credits, outbox, error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_date, slot_id) DO UPDATE SET "
            "status = excluded.status, preset = excluded.preset, topic = excluded.topic, "
            "platforms = excluded.platforms, quality = excluded.quality, "
            "runway_credits = excluded.runway_credits, outbox = excluded.outbox, "
            "error = excluded.error, updated_at = excluded.updated_at",
            (
                run_date,
                slot_id,
                status,
                preset,
                topic,
                plats,
                quality,
                int(runway_credits),
                outbox,
                error,
                now,
                now,
            ),
        )
        conn.commit()


def _job_row(row: sqlite3.Row) -> dict[str, Any]:
    plats = [p for p in str(row["platforms"] or "").split(",") if p]
    return {
        "run_date": str(row["run_date"]),
        "slot_id": str(row["slot_id"]),
        "status": str(row["status"]),
        "preset": str(row["preset"] or ""),
        "topic": str(row["topic"] or ""),
        "platforms": plats,
        "quality": str(row["quality"] or ""),
        "runway_credits": int(row["runway_credits"] or 0),
        "outbox": str(row["outbox"] or ""),
        "error": str(row["error"] or ""),
    }


def list_night_jobs(run_date: str) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM night_jobs WHERE run_date = ? ORDER BY created_at, slot_id",
            (run_date,),
        ).fetchall()
    return [_job_row(row) for row in rows]


def packed_night_slot_ids(run_date: str) -> set[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT slot_id FROM night_jobs WHERE run_date = ? AND status = 'packed'",
            (run_date,),
        ).fetchall()
    return {str(row["slot_id"]) for row in rows}


def save_night_run(
    *,
    run_id: str,
    run_date: str,
    mode: str,
    planned: int,
    rendered: int,
    failed: int,
    skipped: int,
    runway_used: int,
    report: str,
) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO night_runs (run_id, run_date, mode, planned, rendered, "
            "failed, skipped, runway_used, report, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                run_date,
                mode,
                int(planned),
                int(rendered),
                int(failed),
                int(skipped),
                int(runway_used),
                report,
                _now(),
            ),
        )
        conn.commit()


def get_last_night_run() -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM night_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return {
        "run_id": str(row["run_id"]),
        "run_date": str(row["run_date"]),
        "mode": str(row["mode"]),
        "planned": int(row["planned"] or 0),
        "rendered": int(row["rendered"] or 0),
        "failed": int(row["failed"] or 0),
        "skipped": int(row["skipped"] or 0),
        "runway_used": int(row["runway_used"] or 0),
        "report": str(row["report"] or ""),
    }
