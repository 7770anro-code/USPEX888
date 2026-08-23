"""Rate-limit, exponential backoff и circuit breaker для платных API."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

log = logging.getLogger("videobot.night")

T = TypeVar("T")


class CircuitOpen(RuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(f"circuit open: {name}")
        self.name = name


class CircuitBreaker:
    def __init__(self, name: str, *, threshold: int = 3, cooldown_sec: float = 300) -> None:
        self.name = name
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self.failures = 0
        self.opened_at = 0.0

    def allow(self) -> bool:
        if self.failures < self.threshold:
            return True
        if time.monotonic() - self.opened_at >= self.cooldown_sec:
            log.info("circuit half-open %s", self.name)
            return True
        return False

    def ok(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()
            log.warning("circuit open %s after %s failures", self.name, self.failures)


class RateGate:
    def __init__(self, min_interval_sec: float) -> None:
        self.min_interval_sec = max(0.0, float(min_interval_sec))
        self._last = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        gap = self.min_interval_sec - (now - self._last)
        if gap > 0:
            await asyncio.sleep(gap)
        self._last = time.monotonic()


RUNWAY = CircuitBreaker("runway")
ELEVEN = CircuitBreaker("elevenlabs")
GROK = CircuitBreaker("grok")
TIKTOK = CircuitBreaker("tiktok")
INSTAGRAM = CircuitBreaker("instagram")

RUNWAY_GATE = RateGate(8.0)
ELEVEN_GATE = RateGate(2.0)
POST_GATE = RateGate(5.0)


async def with_breaker(
    breaker: CircuitBreaker,
    fn: Callable[[], Awaitable[T]],
    *,
    gate: RateGate | None = None,
    retries: int = 4,
    retry_for: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    if not breaker.allow():
        raise CircuitOpen(breaker.name)
    last: BaseException | None = None
    for attempt in range(retries):
        if gate:
            await gate.wait()
        try:
            result = await fn()
            breaker.ok()
            return result
        except CircuitOpen:
            raise
        except retry_for as exc:
            last = exc
            breaker.fail()
            if attempt >= retries - 1 or not breaker.allow():
                break
            delay = min(40.0, (2**attempt) * 1.4) + random.uniform(0.2, 1.2)
            log.warning("%s retry %s: %s", breaker.name, attempt + 1, type(exc).__name__)
            await asyncio.sleep(delay)
    assert last is not None
    raise last


def jitter_pause(lo: float, hi: float) -> float:
    if hi <= 0:
        return 0.0
    return random.uniform(max(0.0, lo), max(lo, hi))
