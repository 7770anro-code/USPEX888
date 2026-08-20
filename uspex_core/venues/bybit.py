"""Bybit public market adapter (execution still DEMO-only elsewhere)."""
from __future__ import annotations

from typing import Any, Dict

from .base import MarketSnapshot


class BybitPublicAdapter:
    name = "bybit"

    def native_symbol(self, canonical: str) -> str:
        return str(canonical or "").upper()

    def parse_book_update(self, payload: Dict[str, Any], snap: MarketSnapshot) -> MarketSnapshot:
        # Lightweight normalizer for existing WS book payloads.
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            bids = data.get("b") or data.get("bids") or []
            asks = data.get("a") or data.get("asks") or []
            if bids:
                try:
                    snap.bid1 = float(bids[0][0])
                except Exception:
                    pass
            if asks:
                try:
                    snap.ask1 = float(asks[0][0])
                except Exception:
                    pass
            snap.refresh_mid_spread()
            seq = data.get("u") or data.get("seq")
            if seq is not None:
                try:
                    new_seq = int(seq)
                    if snap.sequence_id is not None and new_seq < snap.sequence_id:
                        snap.book_dirty = True
                        snap.sequence_ok = False
                    snap.sequence_prev_id = snap.sequence_id
                    snap.sequence_id = new_seq
                except Exception:
                    snap.book_dirty = True
                    snap.sequence_ok = False
        return snap
