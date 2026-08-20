"""Robust flow / orderbook metrics. Extreme ratios never inflate score as raw edge."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

UNKNOWN = "UNKNOWN"

# Minimum meaningful USD notional inside the flow window.
MIN_FLOW_NOTIONAL = 50.0
MIN_SIDE_NOTIONAL = 5.0
# Book qty must have meaningful size on both sides (contracts/coins depending on venue).
MIN_BOOK_QTY = 1e-8
MIN_BOOK_NOTIONAL_PROXY = 1e-6

FLOW_RATIO_CLIP = 5.0
BOOK_RATIO_CLIP = 5.0
NEAR_ZERO = 1e-12


@dataclass(frozen=True)
class MetricResult:
    status: str  # "OK" | "UNKNOWN" | "BAD"
    ratio: Optional[float]
    reliability: float  # 0..1
    raw_ratio: Optional[float]
    reason: str
    normalized: Optional[float] = None  # signed imbalance in [-1, 1] when available

    @property
    def is_unknown(self) -> bool:
        return self.status == "UNKNOWN"

    @property
    def is_bad(self) -> bool:
        return self.status == "BAD"

    @property
    def display(self) -> str:
        if self.status != "OK" or self.ratio is None:
            return self.status
        return f"{self.ratio:.2f}x"


def clip_ratio(r: float, lo: float = 1.0 / FLOW_RATIO_CLIP, hi: float = FLOW_RATIO_CLIP) -> float:
    if not math.isfinite(r) or r <= 0:
        return 1.0
    return max(lo, min(hi, r))


def log_imbalance(buy: float, sell: float) -> float:
    """Symmetric log ratio mapped roughly to [-1, 1] after tanh."""
    b = max(buy, 0.0)
    s = max(sell, 0.0)
    if b + s <= NEAR_ZERO:
        return 0.0
    return math.tanh(math.log1p(b) - math.log1p(s))


def _sum_window(events: Sequence[Tuple[float, float]], cutoff: float) -> float:
    total = 0.0
    for ts, val in events:
        if ts >= cutoff:
            try:
                total += float(val)
            except (TypeError, ValueError):
                continue
    return total


def robust_flow(
    buys: Iterable[Tuple[float, float]],
    sells: Iterable[Tuple[float, float]],
    *,
    now_ts: float,
    window: float = 1.0,
    min_notional: float = MIN_FLOW_NOTIONAL,
    min_side: float = MIN_SIDE_NOTIONAL,
    clip: float = FLOW_RATIO_CLIP,
) -> MetricResult:
    """Compute buy/sell flow ratio with unknown/bad handling for sparse prints."""
    try:
        buy_list = list(buys)
        sell_list = list(sells)
    except Exception:
        return MetricResult("BAD", None, 0.0, None, "malformed_flow_snapshot")

    cutoff = now_ts - window
    b = _sum_window(buy_list, cutoff)
    s = _sum_window(sell_list, cutoff)
    total = b + s

    if total < min_notional:
        return MetricResult(
            "UNKNOWN", None, 0.15, None,
            f"low_flow_notional total={total:.4g}<{min_notional}",
            normalized=0.0,
        )
    if b < NEAR_ZERO and s < NEAR_ZERO:
        return MetricResult("UNKNOWN", None, 0.1, None, "zero_flow_both_sides", normalized=0.0)

    # One-sided extreme with near-zero opposite side: do not emit 1e9x.
    if s < min_side and b >= min_side:
        raw = b / max(s, NEAR_ZERO)
        return MetricResult(
            "UNKNOWN", None, 0.35, raw if math.isfinite(raw) else None,
            "near_zero_sell_denominator",
            normalized=log_imbalance(b, s),
        )
    if b < min_side and s >= min_side:
        raw = b / max(s, NEAR_ZERO)
        return MetricResult(
            "UNKNOWN", None, 0.35, raw if math.isfinite(raw) else None,
            "near_zero_buy_denominator",
            normalized=log_imbalance(b, s),
        )

    if s <= NEAR_ZERO or b <= NEAR_ZERO:
        return MetricResult("UNKNOWN", None, 0.2, None, "degenerate_flow_side", normalized=0.0)

    raw = b / s
    if (not math.isfinite(raw)) or raw <= 0:
        return MetricResult("BAD", None, 0.0, None, "non_finite_flow_ratio")

    clipped = clip_ratio(raw, 1.0 / clip, clip)
    # Reliability falls as we approach clip extremes or thin total notional.
    extremity = abs(math.log(clipped)) / math.log(clip) if clip > 1 else 0.0
    depth_factor = min(1.0, total / (min_notional * 4.0))
    reliability = max(0.2, min(1.0, (1.0 - 0.45 * extremity) * (0.55 + 0.45 * depth_factor)))
    return MetricResult(
        "OK", clipped, reliability, raw,
        "ok" if abs(raw - clipped) < 1e-9 else f"winsorized raw={raw:.3g}",
        normalized=log_imbalance(b, s),
    )


def robust_book(
    bid_qty: float,
    ask_qty: float,
    *,
    mid_price: float = 0.0,
    min_qty: float = MIN_BOOK_QTY,
    clip: float = BOOK_RATIO_CLIP,
) -> MetricResult:
    """Top-of-book bid/ask qty ratio with unknown for missing/extreme books."""
    try:
        bq = float(bid_qty or 0.0)
        aq = float(ask_qty or 0.0)
        px = float(mid_price or 0.0)
    except (TypeError, ValueError):
        return MetricResult("BAD", None, 0.0, None, "malformed_book_snapshot")

    if bq < 0 or aq < 0:
        return MetricResult("BAD", None, 0.0, None, "negative_book_qty")
    if bq < min_qty and aq < min_qty:
        return MetricResult("UNKNOWN", None, 0.1, None, "empty_book", normalized=0.0)

    notional = (bq + aq) * px if px > 0 else (bq + aq)
    if px > 0 and notional < MIN_BOOK_NOTIONAL_PROXY:
        return MetricResult("UNKNOWN", None, 0.2, None, "low_book_liquidity", normalized=0.0)

    if aq < min_qty and bq >= min_qty:
        raw = bq / max(aq, NEAR_ZERO)
        return MetricResult(
            "UNKNOWN", None, 0.3, raw if math.isfinite(raw) else None,
            "one_sided_book_ask_near_zero",
            normalized=log_imbalance(bq, aq),
        )
    if bq < min_qty and aq >= min_qty:
        raw = bq / max(aq, NEAR_ZERO)
        return MetricResult(
            "UNKNOWN", None, 0.3, raw if math.isfinite(raw) else None,
            "one_sided_book_bid_near_zero",
            normalized=log_imbalance(bq, aq),
        )

    raw = bq / aq
    if (not math.isfinite(raw)) or raw <= 0:
        return MetricResult("BAD", None, 0.0, None, "non_finite_book_ratio")

    # Extreme one-sided books (20-50x) → UNKNOWN / clipped, never score bonus fuel.
    if raw > clip * 4 or raw < 1.0 / (clip * 4):
        return MetricResult(
            "UNKNOWN", None, 0.25, raw,
            f"absurd_book_ratio raw={raw:.3g}",
            normalized=log_imbalance(bq, aq),
        )

    clipped = clip_ratio(raw, 1.0 / clip, clip)
    extremity = abs(math.log(clipped)) / math.log(clip) if clip > 1 else 0.0
    reliability = max(0.25, min(1.0, 1.0 - 0.5 * extremity))
    return MetricResult(
        "OK", clipped, reliability, raw,
        "ok" if abs(raw - clipped) < 1e-9 else f"winsorized raw={raw:.3g}",
        normalized=log_imbalance(bq, aq),
    )


def oriented_ratio(metric: MetricResult, side: str) -> MetricResult:
    """Flip ratio for SHORT so >1 always means supportive of the trade direction."""
    if metric.ratio is None:
        return metric
    if side.upper() != "SHORT":
        return metric
    inv = 1.0 / metric.ratio if metric.ratio > NEAR_ZERO else None
    raw_inv = None
    if metric.raw_ratio is not None and metric.raw_ratio > NEAR_ZERO:
        raw_inv = 1.0 / metric.raw_ratio
    norm = (-metric.normalized) if metric.normalized is not None else None
    return MetricResult(
        metric.status,
        inv,
        metric.reliability,
        raw_inv,
        metric.reason + "|oriented_short",
        normalized=norm,
    )


def flow_score_bonus(metric: MetricResult, max_bonus: int = 12) -> int:
    """UNKNOWN/BAD never add score; only reliable OK ratios above 1 contribute."""
    if metric.status != "OK" or metric.ratio is None or metric.reliability < 0.45:
        return 0
    return max(0, min(max_bonus, int(max(0.0, metric.ratio - 1.0) * 10)))


def book_score_bonus(metric: MetricResult, max_bonus: int = 12) -> int:
    if metric.status != "OK" or metric.ratio is None or metric.reliability < 0.45:
        return 0
    return max(0, min(max_bonus, int(max(0.0, metric.ratio - 1.0) * 11)))
