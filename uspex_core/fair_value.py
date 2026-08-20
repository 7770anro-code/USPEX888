"""Robust cross-venue fair value. One broken venue must not define the signal."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class VenueQuote:
    venue: str
    mid: float
    age_ms: float
    spread_bps: float = 0.0
    liquidity: float = 1.0
    sequence_ok: bool = True
    book_dirty: bool = False
    comparable: bool = True


@dataclass(frozen=True)
class FairValueResult:
    fair: Optional[float]
    edge_bps: Optional[float]
    n_venues: int
    weights: Dict[str, float]
    outlier_venues: List[str]
    reason: str


def _weight(q: VenueQuote) -> float:
    if not q.comparable or q.book_dirty or not q.sequence_ok:
        return 0.0
    if not math.isfinite(q.mid) or q.mid <= 0:
        return 0.0
    fresh = max(0.05, 1.0 - min(1.0, q.age_ms / 5000.0))
    spread_pen = max(0.15, 1.0 - min(1.0, q.spread_bps / 40.0))
    liq = max(0.1, min(2.0, float(q.liquidity or 1.0)))
    return fresh * spread_pen * liq


def robust_fair_value(
    quotes: Sequence[VenueQuote],
    *,
    executable_bybit: Optional[float] = None,
    side: str = "LONG",
    mad_z: float = 3.5,
) -> FairValueResult:
    usable = [q for q in quotes if _weight(q) > 0]
    if len(usable) < 1:
        return FairValueResult(None, None, 0, {}, [], "no_fresh_venues")

    mids = [q.mid for q in usable]
    med = statistics.median(mids)
    abs_dev = [abs(m - med) for m in mids]
    mad = statistics.median(abs_dev) if abs_dev else 0.0
    scale = 1.4826 * mad if mad > 0 else max(med * 1e-4, 1e-12)

    kept: List[VenueQuote] = []
    outliers: List[str] = []
    for q in usable:
        z = abs(q.mid - med) / scale
        if z > mad_z and len(usable) >= 3:
            outliers.append(q.venue)
            continue
        kept.append(q)
    if not kept:
        kept = usable
        outliers = []

    weights = {q.venue: _weight(q) for q in kept}
    wsum = sum(weights.values()) or 1.0
    fair = sum(q.mid * weights[q.venue] for q in kept) / wsum

    edge_bps = None
    if executable_bybit and executable_bybit > 0 and fair > 0:
        # LONG wants fair > ask; SHORT wants fair < bid.
        raw = (fair - executable_bybit) / executable_bybit * 10000.0
        edge_bps = raw if str(side).upper() == "LONG" else -raw

    return FairValueResult(
        fair=fair,
        edge_bps=edge_bps,
        n_venues=len(kept),
        weights={k: v / wsum for k, v in weights.items()},
        outlier_venues=outliers,
        reason="ok" if not outliers else f"outliers={','.join(outliers)}",
    )


def quotes_from_mids(
    mids: Mapping[str, Optional[float]],
    ages_ms: Mapping[str, float] | None = None,
    spreads_bps: Mapping[str, float] | None = None,
) -> List[VenueQuote]:
    ages_ms = ages_ms or {}
    spreads_bps = spreads_bps or {}
    out: List[VenueQuote] = []
    for venue, mid in mids.items():
        if mid is None:
            continue
        try:
            m = float(mid)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(m) or m <= 0:
            continue
        out.append(
            VenueQuote(
                venue=venue,
                mid=m,
                age_ms=float(ages_ms.get(venue, 0.0) or 0.0),
                spread_bps=float(spreads_bps.get(venue, 0.0) or 0.0),
            )
        )
    return out
