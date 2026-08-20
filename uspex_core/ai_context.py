"""Layer A slow AI context cache (structure/regime) — pre-warm for fast Layer B confirms."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class AiContext:
    source: str  # cursor | grok
    symbol: str  # symbol or GLOBAL
    allowed_side: str  # LONG | SHORT | BOTH | NONE
    bias: str
    confidence: float
    caution: str
    invalidation: str
    ts: float
    ttl_sec: float = 90.0
    extra: dict = field(default_factory=dict)

    @property
    def age_sec(self) -> float:
        return max(0.0, time.time() - self.ts)

    @property
    def fresh(self) -> bool:
        return self.age_sec <= self.ttl_sec


class AiContextCache:
    def __init__(self):
        self._cursor: Dict[str, AiContext] = {}
        self._grok: Dict[str, AiContext] = {}
        self.last_cursor_refresh = 0.0
        self.last_grok_refresh = 0.0

    def put_cursor(self, ctx: AiContext) -> None:
        self._cursor[ctx.symbol] = ctx
        self.last_cursor_refresh = time.time()

    def put_grok(self, ctx: AiContext) -> None:
        self._grok[ctx.symbol] = ctx
        self.last_grok_refresh = time.time()

    def get_cursor(self, symbol: str) -> Optional[AiContext]:
        ctx = self._cursor.get(symbol) or self._cursor.get("GLOBAL")
        if ctx and ctx.fresh:
            return ctx
        return None

    def get_grok(self, symbol: str) -> Optional[AiContext]:
        ctx = self._grok.get(symbol) or self._grok.get("GLOBAL")
        if ctx and ctx.fresh:
            return ctx
        return None

    def side_allowed(self, symbol: str, side: str) -> tuple:
        """Returns (ok, reason). Missing/stale cache is neutral, not veto."""
        c = self.get_cursor(symbol)
        g = self.get_grok(symbol)
        reasons = []
        if c:
            if c.allowed_side == "NONE":
                return False, "cursor_context_NONE"
            if c.allowed_side not in ("BOTH", side):
                return False, f"cursor_bias={c.allowed_side}"
            reasons.append(f"cursor={c.allowed_side}/{c.confidence:.0f}")
        else:
            reasons.append("cursor_cache_missing_neutral")
        if g:
            if g.allowed_side == "NONE":
                return False, "grok_context_NONE"
            if g.allowed_side not in ("BOTH", side):
                return False, f"grok_bias={g.allowed_side}"
            reasons.append(f"grok={g.allowed_side}/{g.confidence:.0f}")
        else:
            reasons.append("grok_cache_missing_neutral")
        return True, ";".join(reasons)

    def snapshot_for_prompt(self, symbol: str) -> str:
        parts = []
        c = self.get_cursor(symbol)
        g = self.get_grok(symbol)
        if c:
            parts.append(
                f"CURSOR_CTX age={c.age_sec:.0f}s side={c.allowed_side} bias={c.bias} "
                f"conf={c.confidence:.0f} caution={c.caution} inv={c.invalidation}"
            )
        else:
            parts.append("CURSOR_CTX=STALE_OR_MISSING")
        if g:
            parts.append(
                f"GROK_CTX age={g.age_sec:.0f}s side={g.allowed_side} bias={g.bias} "
                f"conf={g.confidence:.0f} caution={g.caution}"
            )
        else:
            parts.append("GROK_CTX=STALE_OR_MISSING")
        return " | ".join(parts)
