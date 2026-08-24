"""Межпроцессный fcntl-замок.

Ручная съёмка в боте и автоконтур живут в одном процессе videobot.service —
их сериализует asyncio.Lock `BUSY`. Этот файл нужен только против второго
процесса: CLI `night_runner.py --smoke` или случайно включённый старый timer.
"""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO

import config


class JobLock:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else Path(config.DATA_DIR) / "videobot.lock"
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
