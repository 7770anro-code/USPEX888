"""OKX reference adapter — seqId/prevSeqId integrity (checksum deprecated)."""
from __future__ import annotations

from typing import Any, Dict

from .base import MarketSnapshot


class OkxAdapter:
    name = "okx"

    def native_symbol(self, canonical: str) -> str:
        # BTCUSDT -> BTC-USDT-SWAP heuristic for linear perps.
        s = str(canonical or "").upper()
        if s.endswith("USDT") and "-" not in s:
            base = s[:-4]
            return f"{base}-USDT-SWAP"
        return s

    def parse_book_update(self, payload: Dict[str, Any], snap: MarketSnapshot) -> MarketSnapshot:
        data = payload.get("data")
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else payload)
        if not isinstance(row, dict):
            return snap
        bids = row.get("bids") or []
        asks = row.get("asks") or []
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
        seq = row.get("seqId")
        prev = row.get("prevSeqId")
        if seq is not None:
            try:
                new_seq = int(seq)
                if prev is not None and snap.sequence_id is not None and int(prev) != snap.sequence_id:
                    snap.book_dirty = True
                    snap.sequence_ok = False
                snap.sequence_prev_id = snap.sequence_id
                snap.sequence_id = new_seq
            except Exception:
                snap.book_dirty = True
                snap.sequence_ok = False
        return snap
