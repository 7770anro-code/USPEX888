"""Binance USDⓈ-M Futures reference adapter."""
from __future__ import annotations

from typing import Any, Dict

from .base import MarketSnapshot


class BinanceFuturesAdapter:
    name = "binance"

    def native_symbol(self, canonical: str) -> str:
        return str(canonical or "").lower()

    def parse_book_update(self, payload: Dict[str, Any], snap: MarketSnapshot) -> MarketSnapshot:
        bids = payload.get("b") or payload.get("bids") or []
        asks = payload.get("a") or payload.get("asks") or []
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
        # Depth update ID continuity (simplified).
        u = payload.get("u") or payload.get("lastUpdateId")
        pu = payload.get("pu")
        if u is not None:
            try:
                new_u = int(u)
                if snap.sequence_id is not None and pu is not None and int(pu) != snap.sequence_id:
                    snap.book_dirty = True
                    snap.sequence_ok = False
                snap.sequence_prev_id = snap.sequence_id
                snap.sequence_id = new_u
            except Exception:
                snap.book_dirty = True
                snap.sequence_ok = False
        return snap
