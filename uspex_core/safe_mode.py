"""Safe Mode / Fail Safe — stop new entries, never blind-close positions."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SafeMode:
    active: bool = False
    reason: str = ""
    since: float = 0.0
    triggers: List[str] = field(default_factory=list)
    exception_streak: int = 0
    api_error_streak: int = 0

    # Thresholds
    max_exception_streak: int = 5
    max_api_error_streak: int = 8

    def arm(self, reason: str) -> None:
        if not self.active:
            self.active = True
            self.since = time.time()
            self.reason = reason
        self.triggers.append(f"{time.time():.0f}:{reason}")
        self.triggers = self.triggers[-30:]

    def note_exception(self, where: str = "") -> bool:
        self.exception_streak += 1
        if self.exception_streak >= self.max_exception_streak:
            self.arm(f"repeated_unhandled_exceptions:{where}")
            return True
        return self.active

    def clear_exception(self) -> None:
        self.exception_streak = 0

    def note_api_error(self, where: str = "") -> bool:
        self.api_error_streak += 1
        if self.api_error_streak >= self.max_api_error_streak:
            self.arm(f"excessive_api_errors:{where}")
            return True
        return self.active

    def clear_api(self) -> None:
        self.api_error_streak = 0

    def note_feed_outage(self) -> None:
        self.arm("feed_outage")

    def note_auth_failure(self) -> None:
        self.arm("bybit_auth_failure")

    def note_reconcile_mismatch(self) -> None:
        self.arm("reconcile_mismatch")

    def note_watcher_crash(self) -> None:
        self.arm("watcher_crash")

    def note_db_error(self) -> None:
        self.arm("db_error")

    def note_unconfirmed_open(self) -> None:
        self.arm("opened_without_confirmed_position")

    def allow_new_entries(self) -> bool:
        return not self.active

    def status_text(self) -> str:
        if not self.active:
            return "Safe Mode: OFF"
        age = time.time() - self.since if self.since else 0
        return f"Safe Mode: ON ({age:.0f}s) • {self.reason} • scanner entries blocked • open positions untouched"
