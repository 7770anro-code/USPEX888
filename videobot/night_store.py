"""SQLite state machine ночных задач. Не трогает таблицы голосов бота."""

from __future__ import annotations

import logging
import os
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
from store import db_path, init_db

log = logging.getLogger("videobot.night")

PENDING = "pending"
IDEAS_READY = "ideas_ready"
GENERATING = "generating"
VIDEO_READY = "video_ready"
POSTING = "posting"
POSTED = "posted"
FAILED = "failed"
PUBLISH_UNKNOWN = "publish_unknown"
MANUAL_REVIEW = "manual_review"
WAIT_CONFIRM = "wait_confirm"

ACTIVE_LOCK = (GENERATING, POSTING)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS night_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    account_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    plot TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    idea_hash TEXT NOT NULL DEFAULT '',
    tokens TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    video_path TEXT NOT NULL DEFAULT '',
    tiktok_url TEXT NOT NULL DEFAULT '',
    instagram_url TEXT NOT NULL DEFAULT '',
    tiktok_mode TEXT NOT NULL DEFAULT '',
    instagram_mode TEXT NOT NULL DEFAULT '',
    tiktok_publish_id TEXT NOT NULL DEFAULT '',
    ig_container_id TEXT NOT NULL DEFAULT '',
    runway_credits INTEGER NOT NULL DEFAULT 0,
    eleven_chars INTEGER NOT NULL DEFAULT 0,
    locked_at TEXT,
    worker_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS night_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    plot TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    idea_hash TEXT NOT NULL DEFAULT '',
    tokens TEXT NOT NULL DEFAULT '',
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS night_runs (
    run_id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    status TEXT NOT NULL,
    report TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS night_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_night_jobs_date ON night_jobs(run_date);
CREATE INDEX IF NOT EXISTS idx_night_ideas_hash ON night_ideas(idea_hash);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def ensure() -> None:
    init_db()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path), timeout=30) as conn:
        conn.executescript(_SCHEMA)
        for col, spec in (
            ("tiktok_publish_id", "TEXT NOT NULL DEFAULT ''"),
            ("ig_container_id", "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                conn.execute(f"ALTER TABLE night_jobs ADD COLUMN {col} {spec}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def _connect() -> sqlite3.Connection:
    ensure()
    conn = sqlite3.connect(str(db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def recover_stale(*, minutes: int | None = None) -> int:
    """Зависшие generating/posting: если уже есть publish/container ID — PUBLISH_UNKNOWN."""
    ensure()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=int(minutes or config.NIGHT_STALE_MINUTES))
    mark = cutoff.isoformat(timespec="seconds")
    n = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, status, video_path, locked_at, tiktok_publish_id, ig_container_id "
            "FROM night_jobs WHERE status IN (?, ?) AND locked_at IS NOT NULL AND locked_at < ?",
            (GENERATING, POSTING, mark),
        ).fetchall()
        for row in rows:
            video = Path(str(row["video_path"] or ""))
            has_pub = bool(str(row["tiktok_publish_id"] or "").strip() or str(row["ig_container_id"] or "").strip())
            if str(row["status"]) == POSTING and has_pub:
                nxt = PUBLISH_UNKNOWN
            elif video.is_file():
                nxt = VIDEO_READY
            else:
                nxt = PENDING
            conn.execute(
                "UPDATE night_jobs SET status = ?, locked_at = NULL, worker_id = '', "
                "last_error = ?, updated_at = ? WHERE id = ?",
                (nxt, f"stale lock recovered → {nxt}", _now(), int(row["id"])),
            )
            n += 1
        conn.commit()
    if n:
        log.warning("recovered %s stale night jobs", n)
    return n


def insert_idea(item: dict[str, Any], run_date: str) -> int:
    ensure()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO night_ideas (run_date, kind, title, plot, caption, idea_hash, tokens, used, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                run_date,
                item["kind"],
                item.get("title") or "",
                item.get("plot") or "",
                item.get("caption") or "",
                item.get("idea_hash") or "",
                " ".join(item.get("tokens") or []),
                _now(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def recent_idea_tokens(*, days: int) -> list[set[str]]:
    ensure()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT tokens FROM night_ideas WHERE created_at >= ?",
            (since,),
        ).fetchall()
    out: list[set[str]] = []
    for row in rows:
        toks = {t for t in str(row["tokens"] or "").split() if t}
        if toks:
            out.append(toks)
    return out


def get_job(job_id: int) -> dict[str, Any] | None:
    ensure()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM night_jobs WHERE id = ?", (int(job_id),)).fetchone()
    return _row(row)


def accounts_with_video(run_date: str) -> set[str]:
    """Аккаунты с готовым mp4 за дату — рестарт их не переснимает."""
    return {
        str(job.get("account_id") or "")
        for job in jobs_for_date(run_date)
        if job.get("account_id") and Path(str(job.get("video_path") or "")).is_file()
    }


def jobs_for_date(run_date: str) -> list[dict[str, Any]]:
    ensure()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM night_jobs WHERE run_date = ? ORDER BY id",
            (run_date,),
        ).fetchall()
    return [_row(r) for r in rows if r]


def create_job(job: dict[str, Any]) -> int:
    ensure()
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO night_jobs (run_date, account_id, kind, title, plot, caption, idea_hash, tokens, "
            "status, attempts, last_error, video_path, tiktok_url, instagram_url, tiktok_mode, instagram_mode, "
            "runway_credits, eleven_chars, locked_at, worker_id, updated_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', '', '', '', '', '', 0, 0, NULL, '', ?, ?)",
            (
                job["run_date"],
                job["account_id"],
                job["kind"],
                job.get("title") or "",
                job.get("plot") or "",
                job.get("caption") or "",
                job.get("idea_hash") or "",
                " ".join(job.get("tokens") or []),
                job.get("status") or PENDING,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def lock_job(job_id: int, status: str, wid: str) -> bool:
    ensure()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE night_jobs SET status = ?, locked_at = ?, worker_id = ?, updated_at = ?, "
            "attempts = attempts + 1 WHERE id = ?",
            (status, _now(), wid, _now(), int(job_id)),
        )
        conn.commit()
        return cur.rowcount == 1


def update_job(job_id: int, **fields: Any) -> None:
    ensure()
    if not fields:
        return
    fields = {k: v for k, v in fields.items() if k in {
        "status", "last_error", "video_path", "tiktok_url", "instagram_url",
        "tiktok_mode", "instagram_mode", "tiktok_publish_id", "ig_container_id",
        "runway_credits", "eleven_chars",
        "locked_at", "worker_id", "title", "plot", "caption",
    }}
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [int(job_id)]
    with _connect() as conn:
        conn.execute(f"UPDATE night_jobs SET {cols} WHERE id = ?", vals)
        conn.commit()


def mark_video_ready(job_id: int, video_path: str, *, runway_credits: int, eleven_chars: int) -> None:
    update_job(
        job_id,
        status=VIDEO_READY,
        video_path=video_path,
        runway_credits=int(runway_credits),
        eleven_chars=int(eleven_chars),
        locked_at=None,
        worker_id="",
        last_error="",
    )


def save_run(run_id: str, run_date: str, status: str, report: str) -> None:
    ensure()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO night_runs (run_id, run_date, status, report, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, run_date, status, report, _now()),
        )
        conn.commit()


def last_run() -> dict[str, Any] | None:
    ensure()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM night_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    return _row(row)


def get_setting(key: str) -> str | None:
    ensure()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM night_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row["value"])


def set_setting(key: str, value: str) -> None:
    ensure()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO night_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, str(value), _now()),
        )
        conn.commit()


def bool_setting(key: str, default: bool) -> bool:
    raw = get_setting(key)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def require_confirm() -> bool:
    """Утро = да/нет, пока владелец явно не включит автопост."""
    return bool_setting("require_confirm", config.NIGHT_REQUIRE_CONFIRM)


def autopost_enabled() -> bool:
    """Полный автопост без кнопок. По умолчанию выключен (env NIGHT_AUTOPOST=0)."""
    return bool_setting("autopost", config.NIGHT_AUTOPOST)


def set_publish_mode(*, confirm: bool, autopost: bool) -> None:
    set_setting("require_confirm", "1" if confirm else "0")
    set_setting("autopost", "1" if autopost else "0")


def pending_owner_ids(run_date: str) -> list[int]:
    return [
        int(job["id"])
        for job in jobs_for_date(run_date)
        if job.get("status") in (WAIT_CONFIRM, PUBLISH_UNKNOWN)
    ]


def consecutive_moderation(run_date: str) -> int:
    """Сколько последних задач дня подряд ушли в moderation / MANUAL_REVIEW."""
    n = 0
    for job in reversed(jobs_for_date(run_date)):
        err = str(job.get("last_error") or "").lower()
        if job.get("status") == MANUAL_REVIEW or "moderation" in err or "rejection" in err:
            n += 1
            continue
        break
    return n


def video_belongs_to_account(video_path: str, account_id: str, run_date: str) -> bool:
    """Один mp4 — один аккаунт. Чужой outbox не публикуем."""
    video = Path(video_path)
    expected = Path(config.NIGHT_OUTBOX) / run_date / account_id
    try:
        video.resolve().relative_to(expected.resolve())
        return True
    except (OSError, ValueError):
        return False
