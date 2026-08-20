"""DATA_QUALITY_SCORE 0–100 for each candidate."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence


@dataclass
class DataQualityResult:
    score: float
    hard_reject: bool
    reasons: List[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)

    @property
    def code(self) -> str:
        if self.hard_reject:
            if any("stale" in r for r in self.reasons):
                return "QUALITY_STALE_FEED"
            if any("liq" in r for r in self.reasons):
                return "QUALITY_LOW_LIQ"
            return "QUALITY_REJECT"
        return "QUALITY_OK"


def compute_data_quality(
    *,
    feed_ages: Mapping[str, float],
    execution_venue: str = "bybit",
    fresh_age: float = 7.0,
    spread_bps: float = 0.0,
    max_spread_bps: float = 18.0,
    turnover24h: float = 0.0,
    min_turnover: float = 250_000.0,
    flow_reliability: float = 0.0,
    book_reliability: float = 0.0,
    flow_status: str = "UNKNOWN",
    book_status: str = "UNKNOWN",
    price_continuity_ok: bool = True,
    missing_venues: Sequence[str] = (),
) -> DataQualityResult:
    """Compose a 0–100 data quality score with optional hard reject on critical faults."""
    reasons: List[str] = []
    components: dict = {}

    ex_age = float(feed_ages.get(execution_venue, 999.0))
    fresh_venues = [ex for ex, age in feed_ages.items() if float(age) <= fresh_age]
    n_fresh = len(fresh_venues)
    components["fresh_venues"] = n_fresh
    components["execution_age"] = ex_age

    # Freshness 0–30
    if ex_age > fresh_age:
        fresh_pts = 0.0
        reasons.append(f"execution_stale age={ex_age:.1f}s")
    else:
        fresh_pts = 20.0 * max(0.0, 1.0 - ex_age / max(fresh_age, 1e-9))
        # Bonus for second fresh comparator; third is nice-to-have / neutral if missing.
        if n_fresh >= 2:
            fresh_pts += 10.0
        elif n_fresh == 1:
            fresh_pts += 4.0
            reasons.append("only_execution_fresh")
    components["freshness"] = round(fresh_pts, 2)

    # Spread 0–20
    if spread_bps <= 0:
        spread_pts = 10.0
        reasons.append("spread_unknown")
    elif spread_bps > max_spread_bps:
        spread_pts = 0.0
        reasons.append(f"spread_wide {spread_bps:.1f}bps")
    else:
        spread_pts = 20.0 * (1.0 - spread_bps / max_spread_bps)
    components["spread"] = round(spread_pts, 2)

    # Liquidity 0–20
    if turnover24h <= 0:
        liq_pts = 8.0
        reasons.append("turnover_unknown")
    elif turnover24h < min_turnover:
        liq_pts = 4.0
        reasons.append(f"low_liquidity turnover={turnover24h:.0f}")
    else:
        liq_pts = min(20.0, 8.0 + 12.0 * min(1.0, turnover24h / (min_turnover * 10.0)))
    components["liquidity"] = round(liq_pts, 2)

    # Flow reliability 0–15
    if flow_status == "BAD":
        flow_pts = 0.0
        reasons.append("bad_flow_data")
    elif flow_status == "UNKNOWN":
        flow_pts = 5.0  # neutral — no bonus, not a veto
        reasons.append("flow_unknown")
    else:
        flow_pts = 15.0 * max(0.0, min(1.0, flow_reliability))
    components["flow"] = round(flow_pts, 2)

    # Book reliability 0–15
    if book_status == "BAD":
        book_pts = 0.0
        reasons.append("bad_book_data")
    elif book_status == "UNKNOWN":
        book_pts = 5.0
        reasons.append("book_unknown")
    else:
        book_pts = 15.0 * max(0.0, min(1.0, book_reliability))
    components["book"] = round(book_pts, 2)

    score = fresh_pts + spread_pts + liq_pts + flow_pts + book_pts
    if not price_continuity_ok:
        score -= 15.0
        reasons.append("price_continuity_break")
    if missing_venues:
        # Missing third venue is neutral if Bybit + one comparator exist.
        optional_missing = [v for v in missing_venues if v != execution_venue]
        if n_fresh < 2 and optional_missing:
            score -= 8.0
            reasons.append("missing_comparator:" + ",".join(optional_missing))
        else:
            reasons.append("venue_missing_neutral:" + ",".join(optional_missing))

    score = max(0.0, min(100.0, score))
    hard = False
    if ex_age > fresh_age:
        hard = True
    if n_fresh < 2:
        hard = True
        reasons.append("critical_insufficient_fresh_venues")
    if flow_status == "BAD" or book_status == "BAD":
        hard = True
    if turnover24h > 0 and turnover24h < min_turnover * 0.25:
        hard = True
        reasons.append("critical_low_liq")

    return DataQualityResult(score=round(score, 1), hard_reject=hard, reasons=reasons, components=components)


def cap_uspex_score_by_quality(uspex_score: float, dq: DataQualityResult) -> float:
    """USPEX score cannot stay high when data quality is poor."""
    s = float(uspex_score)
    q = float(dq.score)
    if dq.hard_reject:
        return min(s, 40.0)
    if q < 45:
        return min(s, 50.0 + q * 0.2)
    if q < 60:
        return min(s, s * (0.70 + 0.30 * (q / 60.0)))
    if q < 75:
        return min(s, s * (0.85 + 0.15 * ((q - 60.0) / 15.0)))
    return s
