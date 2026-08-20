"""Candidate coalescing — one Council call per impulse fingerprint."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class LiveCandidate:
    candidate_id: str
    symbol: str
    side: str
    mode: str
    fingerprint: str
    first_detect_ts: float
    first_detect_price: float
    best_price_since_detect: float
    current_price: float
    peak_edge: float
    residual_edge: float
    drift_bps: float
    pullback_bps: float
    last_update_ts: float
    council_inflight: bool = False
    last_council_ts: float = 0.0
    updates: int = 1
    uspex_score: float = 0.0
    data_quality: float = 0.0
    payload: dict = field(default_factory=dict)

    @property
    def age_ms(self) -> float:
        return max(0.0, (time.time() - self.first_detect_ts) * 1000.0)


class CandidateCoalescer:
    def __init__(self, window_sec: float = 4.0, cooldown_sec: float = 8.0):
        self.window_sec = window_sec
        self.cooldown_sec = cooldown_sec
        self._live: Dict[Tuple[str, str, str], LiveCandidate] = {}

    @staticmethod
    def fingerprint(symbol: str, side: str, mode: str, lag_bucket: float) -> str:
        raw = f"{symbol}|{side}|{mode}|{round(lag_bucket, 2)}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def upsert(
        self,
        *,
        chat_id: str,
        symbol: str,
        side: str,
        mode: str,
        price: float,
        residual_edge: float,
        uspex_score: float,
        data_quality: float,
        payload: Optional[dict] = None,
    ) -> Tuple[LiveCandidate, bool]:
        """Return (candidate, should_start_council).

        should_start_council=False means this tick only refreshed an existing impulse.
        """
        key = (str(chat_id), symbol, side)
        now = time.time()
        fp = self.fingerprint(symbol, side, mode, residual_edge)
        cur = self._live.get(key)
        if cur and (now - cur.first_detect_ts) <= self.window_sec and cur.side == side:
            # Update existing impulse
            orient = 1.0 if side == "LONG" else -1.0
            if orient * (price - cur.best_price_since_detect) > 0:
                cur.best_price_since_detect = price
            cur.current_price = price
            cur.residual_edge = residual_edge
            cur.peak_edge = max(cur.peak_edge, residual_edge)
            cur.drift_bps = orient * (price / cur.first_detect_price - 1.0) * 10000.0 if cur.first_detect_price else 0.0
            # Pullback = retreat from best toward first detect
            if cur.best_price_since_detect:
                cur.pullback_bps = orient * (cur.best_price_since_detect / max(price, 1e-12) - 1.0) * 10000.0
            cur.last_update_ts = now
            cur.updates += 1
            cur.uspex_score = max(cur.uspex_score, uspex_score)
            cur.data_quality = data_quality
            if payload:
                cur.payload.update(payload)
            start = (not cur.council_inflight) and (now - cur.last_council_ts >= self.cooldown_sec)
            return cur, start

        cid = f"{symbol}-{side}-{int(now*1000)}-{fp}"
        live = LiveCandidate(
            candidate_id=cid, symbol=symbol, side=side, mode=mode, fingerprint=fp,
            first_detect_ts=now, first_detect_price=price, best_price_since_detect=price,
            current_price=price, peak_edge=residual_edge, residual_edge=residual_edge,
            drift_bps=0.0, pullback_bps=0.0, last_update_ts=now,
            uspex_score=uspex_score, data_quality=data_quality, payload=dict(payload or {}),
        )
        self._live[key] = live
        return live, True

    def mark_council_start(self, chat_id: str, symbol: str, side: str) -> None:
        cur = self._live.get((str(chat_id), symbol, side))
        if cur:
            cur.council_inflight = True

    def mark_council_done(self, chat_id: str, symbol: str, side: str) -> None:
        cur = self._live.get((str(chat_id), symbol, side))
        if cur:
            cur.council_inflight = False
            cur.last_council_ts = time.time()

    def get(self, chat_id: str, symbol: str, side: str) -> Optional[LiveCandidate]:
        return self._live.get((str(chat_id), symbol, side))

    def drop(self, chat_id: str, symbol: str, side: str) -> None:
        self._live.pop((str(chat_id), symbol, side), None)
