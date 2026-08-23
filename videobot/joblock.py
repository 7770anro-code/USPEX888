"""Межпроцессный замок: Telegram-бот и ночной пайплайн не снимают одновременно."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO

import config


class JobLock:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_lock_path()
        self._fh: IO[str] | None = None

    def acquire(self, *, blocking: bool = False) -> bool:
        if self._fh is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write("videobot\n")
        handle.flush()
        self._fh = handle
        return True

    def release(self) -> None:
        handle = self._fh
        self._fh = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "JobLock":
        if not self.acquire():
            raise RuntimeError(f"замок занят: {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def default_lock_path() -> Path:
    return Path(config.DATA_DIR) / "videobot.lock"
