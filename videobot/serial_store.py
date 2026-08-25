"""Сериал: лор, running summary, серии, правки владельца. Та же SQLite, что night_store."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
from store import db_path, init_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS serials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    account_id TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'reveal',
    seed TEXT NOT NULL DEFAULT '',
    lore TEXT NOT NULL DEFAULT '',
    continuity TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    last_cliff TEXT NOT NULL DEFAULT '',
    still_path TEXT NOT NULL DEFAULT '',
    episode_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS serial_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_id INTEGER NOT NULL,
    n INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    plot TEXT NOT NULL DEFAULT '',
    hook TEXT NOT NULL DEFAULT '',
    cliffhanger TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    script_json TEXT NOT NULL DEFAULT '',
    video_path TEXT NOT NULL DEFAULT '',
    night_job_id INTEGER,
    run_date TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(serial_id, n)
);
CREATE TABLE IF NOT EXISTS serial_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    episode_from INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_serial_episodes_serial ON serial_episodes(serial_id, n);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        init_db()
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.executescript(_SCHEMA)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def _connect() -> sqlite3.Connection:
    from night_store import ensure as ensure_night

    ensure_night()
    conn = sqlite3.connect(str(db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def get_serial(*, slug: str = "hybrids") -> dict[str, Any] | None:
    ensure()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM serials WHERE slug = ?", (slug,)).fetchone()
    return _row(row)


def get_serial_by_id(serial_id: int) -> dict[str, Any] | None:
    ensure()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM serials WHERE id = ?", (int(serial_id),)).fetchone()
    return _row(row)


def upsert_serial(data: dict[str, Any]) -> dict[str, Any]:
    ensure()
    now = _now()
    slug = str(data.get("slug") or "hybrids")
    with _connect() as conn:
        existing = conn.execute("SELECT id FROM serials WHERE slug = ?", (slug,)).fetchone()
        if existing:
            fields = {
                k: data[k]
                for k in (
                    "title",
                    "account_id",
                    "format",
                    "seed",
                    "lore",
                    "continuity",
                    "summary",
                    "last_cliff",
                    "still_path",
                    "episode_count",
                    "status",
                )
                if k in data
            }
            fields["updated_at"] = now
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE serials SET {cols} WHERE slug = ?",
                list(fields.values()) + [slug],
            )
            conn.commit()
        else:
            conn.execute(
                "INSERT INTO serials (slug, title, account_id, format, seed, lore, continuity, "
                "summary, last_cliff, still_path, episode_count, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    slug,
                    data.get("title") or "Гибриды",
                    data.get("account_id") or "serial",
                    data.get("format") or "reveal",
                    data.get("seed") or "",
                    data.get("lore") or "",
                    data.get("continuity") or "",
                    data.get("summary") or "",
                    data.get("last_cliff") or "",
                    data.get("still_path") or "",
                    int(data.get("episode_count") or 0),
                    data.get("status") or "active",
                    now,
                    now,
                ),
            )
            conn.commit()
    found = get_serial(slug=slug)
    if not found:
        raise RuntimeError("serial upsert failed")
    return found


def update_serial(serial_id: int, **fields: Any) -> None:
    ensure()
    allowed = {
        "title",
        "account_id",
        "format",
        "seed",
        "lore",
        "continuity",
        "summary",
        "last_cliff",
        "still_path",
        "episode_count",
        "status",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE serials SET {cols} WHERE id = ?",
            list(fields.values()) + [int(serial_id)],
        )
        conn.commit()


def insert_episode(serial_id: int, n: int, data: dict[str, Any]) -> int:
    ensure()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO serial_episodes (serial_id, n, title, plot, hook, cliffhanger, caption, "
            "script_json, video_path, night_job_id, run_date, status, last_error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(serial_id),
                int(n),
                data.get("title") or "",
                data.get("plot") or "",
                data.get("hook") or "",
                data.get("cliffhanger") or "",
                data.get("caption") or "",
                data.get("script_json") or "",
                data.get("video_path") or "",
                data.get("night_job_id"),
                data.get("run_date") or "",
                data.get("status") or "planned",
                data.get("last_error") or "",
                _now(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_episode(episode_id: int, **fields: Any) -> None:
    ensure()
    allowed = {
        "title",
        "plot",
        "hook",
        "cliffhanger",
        "caption",
        "script_json",
        "video_path",
        "night_job_id",
        "run_date",
        "status",
        "last_error",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE serial_episodes SET {cols} WHERE id = ?",
            list(fields.values()) + [int(episode_id)],
        )
        conn.commit()


def get_episode(serial_id: int, n: int) -> dict[str, Any] | None:
    ensure()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM serial_episodes WHERE serial_id = ? AND n = ?",
            (int(serial_id), int(n)),
        ).fetchone()
    return _row(row)


def list_episodes(serial_id: int, *, limit: int = 40) -> list[dict[str, Any]]:
    ensure()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM serial_episodes WHERE serial_id = ? ORDER BY n DESC LIMIT ?",
            (int(serial_id), int(limit)),
        ).fetchall()
    return [_row(r) for r in rows if r]


def last_episode(serial_id: int) -> dict[str, Any] | None:
    ensure()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM serial_episodes WHERE serial_id = ? ORDER BY n DESC LIMIT 1",
            (int(serial_id),),
        ).fetchone()
    return _row(row)


def add_note(serial_id: int, text: str, *, episode_from: int = 0) -> int:
    ensure()
    blob = " ".join((text or "").split())[:2000]
    if len(blob) < 3:
        raise ValueError("note too short")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO serial_notes (serial_id, text, episode_from, created_at) VALUES (?, ?, ?, ?)",
            (int(serial_id), blob, int(episode_from), _now()),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_notes(serial_id: int, *, limit: int = 12) -> list[dict[str, Any]]:
    ensure()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM serial_notes WHERE serial_id = ? ORDER BY id DESC LIMIT ?",
            (int(serial_id), int(limit)),
        ).fetchall()
    return [_row(r) for r in rows if r]


def serial_run_dates(account_id: str) -> set[str]:
    """Даты, на которые уже стоит серия этого аккаунта (чтобы пакет шёл по одной в день)."""
    ensure()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM serial_episodes WHERE run_date != '' AND serial_id IN "
            "(SELECT id FROM serials WHERE account_id = ?) "
            "AND status NOT IN ('failed')",
            (account_id,),
        ).fetchall()
    return {str(r["run_date"]) for r in rows if r and r["run_date"]}


def next_run_dates(account_id: str, n: int, *, start: Any | None = None) -> list[str]:
    from night_time import today_msk

    n = max(1, min(7, int(n)))
    day = start or today_msk()
    taken = serial_run_dates(account_id)
    out: list[str] = []
    guard = 0
    while len(out) < n and guard < 400:
        key = day.isoformat()
        if key not in taken:
            out.append(key)
            taken.add(key)
        day = day + timedelta(days=1)
        guard += 1
    return out


def dump_script(script: dict[str, Any] | None) -> str:
    if not script:
        return ""
    return json.dumps(script, ensure_ascii=False)[:20000]


def serial_dir(serial_id: int) -> Path:
    path = Path(config.DATA_DIR) / "serials" / str(int(serial_id))
    path.mkdir(parents=True, exist_ok=True)
    return path
