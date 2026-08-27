"""Две папки владельца: ночной автоконтур и ручные съёмки.

Ночные ролики пайплайн сам кладёт в NIGHT_OUTBOX; эта библиотека
подтягивает их без ручного копирования. Ручные (Авторолик, фото+голос,
монтаж) архивируются в момент save_last_video — last/ больше не затирает историю.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

log = logging.getLogger("videobot")

MIN_VIDEO_BYTES = 16 * 1024
_SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+$")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def is_owner_user(user_id: int) -> bool:
    """Карточки и API архива — только при заданном NIGHT_OWNER_CHAT_ID."""
    try:
        owner = int(config.NIGHT_OWNER_CHAT_ID or 0)
    except (TypeError, ValueError):
        return False
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return False
    return owner > 0 and uid == owner


def library_root() -> Path:
    path = Path(config.DATA_DIR) / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def night_library_dir() -> Path:
    path = library_root() / "night"
    path.mkdir(parents=True, exist_ok=True)
    return path


def manual_library_dir(user_id: int) -> Path:
    path = library_root() / "manual" / str(int(user_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_slug(text: str, fallback: str = "video") -> str:
    cleaned = _SLUG.sub("_", str(text or "").strip())[:48].strip("._-")
    return cleaned or fallback


def _size_label(n: int) -> str:
    if n < 1024 * 1024:
        return f"{max(1, n // 1024)} КБ"
    return f"{n / (1024 * 1024):.1f} МБ"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _link_or_copy(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)
    return dest


def _write_meta(dest: Path, payload: dict[str, Any]) -> None:
    meta = dest.with_suffix(".json")
    if meta.exists():
        return
    try:
        meta.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("library meta write failed %s", dest.name)


def archive_night_video(
    src: Path,
    *,
    run_date: str = "",
    account: str = "",
    job_id: str = "",
    title: str = "",
) -> Path | None:
    """Сразу после рендера ночи — файл появляется в папке владельца."""
    src = Path(src)
    try:
        if not src.is_file() or src.stat().st_size < MIN_VIDEO_BYTES:
            return None
    except OSError:
        return None
    name = f"{_safe_slug(run_date, 'date')}_{_safe_slug(account, 'acc')}_{_safe_slug(str(job_id or src.stem), src.stem)}.mp4"
    dest = night_library_dir() / name
    path = _link_or_copy(src, dest)
    label = (title or "").strip() or f"{account} · {job_id or src.stem}".strip(" ·")
    _write_meta(
        path,
        {
            "title": label,
            "run_date": run_date,
            "account": account,
            "job_id": str(job_id or src.stem),
            "source": str(src),
        },
    )
    return path


def sync_night_library() -> int:
    """Старые outbox-ролики тоже попадают в папку, без ручного забора."""
    root = Path(config.NIGHT_OUTBOX)
    if not root.is_dir():
        return 0
    added = 0
    try:
        root_res = root.resolve()
    except OSError:
        return 0
    for src in root.rglob("*.mp4"):
        try:
            rel = src.resolve().relative_to(root_res)
        except (ValueError, OSError):
            continue
        parts = rel.parts
        dest = archive_night_video(
            src,
            run_date=str(parts[0]) if parts else "",
            account=str(parts[1]) if len(parts) > 1 else "",
            job_id=src.stem,
        )
        if dest is not None:
            added += 1
    return added


def archive_manual_video(user_id: int, src: Path, title: str = "") -> Path | None:
    src = Path(src)
    try:
        if not src.is_file() or src.stat().st_size < MIN_VIDEO_BYTES:
            return None
    except OSError:
        return None
    dest_dir = manual_library_dir(int(user_id))
    if _is_under(src, dest_dir):
        return src
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _safe_slug(title, "video")
    dest = dest_dir / f"{ts}_{slug}.mp4"
    if dest.exists():
        dest = dest_dir / f"{ts}_{slug}_{src.stat().st_size}.mp4"
    path = _link_or_copy(src, dest)
    _write_meta(path, {"title": (title or "").strip() or slug, "source": str(src)})
    return path


def _title_from_meta(path: Path, fallback: str) -> str:
    meta = path.with_suffix(".json")
    if not meta.is_file():
        return fallback
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    title = str((data or {}).get("title") or "").strip()
    return title or fallback


def _item(path: Path, *, kind: str, item_id: str, title: str) -> dict[str, Any]:
    st = path.stat()
    when = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "id": item_id,
        "kind": kind,
        "title": title,
        "name": path.name,
        "size": st.st_size,
        "size_label": _size_label(st.st_size),
        "mtime": int(st.st_mtime),
        "when": when,
    }


def list_night_videos(limit: int = 60) -> list[dict[str, Any]]:
    sync_night_library()
    items: list[dict[str, Any]] = []
    folder = night_library_dir()
    for path in folder.glob("*.mp4"):
        try:
            if path.stat().st_size < MIN_VIDEO_BYTES:
                continue
        except OSError:
            continue
        items.append(
            _item(path, kind="night", item_id=path.name, title=_title_from_meta(path, path.stem))
        )
    items.sort(key=lambda row: int(row.get("mtime") or 0), reverse=True)
    cap = max(1, min(int(limit or 60), 80))
    return items[:cap]


def list_manual_videos(user_id: int, limit: int = 60) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    folder = Path(config.DATA_DIR) / "library" / "manual" / str(int(user_id))
    if folder.is_dir():
        for path in folder.glob("*.mp4"):
            try:
                if path.stat().st_size < MIN_VIDEO_BYTES:
                    continue
            except OSError:
                continue
            items.append(
                _item(
                    path,
                    kind="manual",
                    item_id=path.name,
                    title=_title_from_meta(path, path.stem),
                )
            )
    if not items:
        last = Path(config.DATA_DIR) / "last" / f"{int(user_id)}.mp4"
        try:
            if last.is_file() and last.stat().st_size >= MIN_VIDEO_BYTES:
                items.append(
                    _item(last, kind="manual", item_id="last.mp4", title="Последний ручной ролик")
                )
        except OSError:
            pass
    items.sort(key=lambda row: int(row.get("mtime") or 0), reverse=True)
    cap = max(1, min(int(limit or 60), 80))
    return items[:cap]


def resolve_library_file(kind: str, user_id: int, item_id: str) -> Path | None:
    name = str(item_id or "").strip()
    if not name or Path(name).name != name or not _SAFE_FILE.fullmatch(name):
        return None
    if not name.lower().endswith(".mp4"):
        return None
    key = str(kind or "").strip().lower()
    if key in ("night", "n"):
        root = night_library_dir()
        path = root / name
    elif key in ("manual", "m"):
        if name == "last.mp4":
            root = Path(config.DATA_DIR) / "last"
            path = root / f"{int(user_id)}.mp4"
        else:
            root = manual_library_dir(int(user_id))
            path = root / name
    else:
        return None
    if not _is_under(path, root):
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None
