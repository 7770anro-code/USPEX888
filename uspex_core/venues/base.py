"""Normalized venue adapter contract (V3 §56)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


@dataclass
class MarketSnapshot:
    venue: str
    canonical_symbol: str
    native_symbol: str
    instrument_type: str = "linear_perp"
    base: str = ""
    quote: str = "USDT"
    settle: str = "USDT"
    bid1: Optional[float] = None
    ask1: Optional[float] = None
    mid: Optional[float] = None
    mark_price: Optional[float] = None
    index_price: Optional[float] = None
    timestamp_exchange_ms: Optional[int] = None
    timestamp_receive_monotonic_ns: Optional[int] = None
    age_ms: float = 0.0
    sequence_id: Optional[int] = None
    sequence_prev_id: Optional[int] = None
    sequence_ok: bool = True
    book_dirty: bool = False
    spread_bps: float = 0.0
    top_depth_usd: float = 0.0
    depth_5bps_usd: float = 0.0
    depth_10bps_usd: float = 0.0
    trade_buy_notional_window: float = 0.0
    trade_sell_notional_window: float = 0.0
    normalized_flow: Optional[float] = None
    normalized_book_imbalance: Optional[float] = None
    data_quality_score: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def refresh_mid_spread(self) -> None:
        if self.bid1 and self.ask1 and self.bid1 > 0 and self.ask1 > 0:
            self.mid = 0.5 * (self.bid1 + self.ask1)
            self.spread_bps = (self.ask1 - self.bid1) / self.mid * 10000.0


class VenueAdapter(Protocol):
    name: str

    def native_symbol(self, canonical: str) -> str:
        ...

    def parse_book_update(self, payload: Dict[str, Any], snap: MarketSnapshot) -> MarketSnapshot:
        ...
