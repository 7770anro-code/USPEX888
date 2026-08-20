"""Post-Council revalidation: no literal second-impulse requirement."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Optional


# Mode-aware chase / pullback tolerances (bps of price move vs candidate entry).
ENTRY_TOLERANCE = {
    # max_chase_bps: skip if price ran away with the trade
    # max_adverse_bps: reject if price reversed against the trade too far
    # pullback_ok_bps: favorable then mild pullback still allowed
    "easy":   {"max_chase_bps": 22.0, "max_adverse_bps": 18.0, "max_age_sec": 18.0, "min_residual_edge": 0.02},
    "medium": {"max_chase_bps": 32.0, "max_adverse_bps": 25.0, "max_age_sec": 14.0, "min_residual_edge": 0.03},
    "big":    {"max_chase_bps": 18.0, "max_adverse_bps": 14.0, "max_age_sec": 10.0, "min_residual_edge": 0.06},
    "ai":     {"max_chase_bps": 28.0, "max_adverse_bps": 22.0, "max_age_sec": 12.0, "min_residual_edge": 0.04},
    "manual": {"max_chase_bps": 40.0, "max_adverse_bps": 30.0, "max_age_sec": 20.0, "min_residual_edge": 0.02},
}


@dataclass
class RevalidationResult:
    code: str
    ok: bool
    detail: str
    drift_bps: float = 0.0
    residual_edge: float = 0.0
    reasons: List[str] = field(default_factory=list)


def _oriented_move_bps(entry: float, live: float, side: str) -> float:
    if not entry or not live:
        return 0.0
    raw = (live / entry - 1.0) * 10000.0
    return raw if side.upper() == "LONG" else -raw


def revalidate_entry(
    *,
    profile: str,
    side: str,
    candidate_entry: float,
    live_price: float,
    candidate_age_sec: float,
    execution_age_sec: float,
    fresh_venues: int,
    fresh_age_limit: float,
    spread_bps: float,
    max_spread_bps: float,
    residual_edge: Optional[float],
    flow_status: str = "OK",
    book_status: str = "OK",
    flow_oriented: Optional[float] = None,
    book_oriented: Optional[float] = None,
    structure_ok: bool = True,
    liquidity_ok: bool = True,
    same_direction_hint: bool = True,
) -> RevalidationResult:
    """Validate that a Council-approved idea is still executable — not that the tick repeats."""
    tol = ENTRY_TOLERANCE.get(profile, ENTRY_TOLERANCE["medium"])
    reasons: List[str] = []

    if execution_age_sec > fresh_age_limit:
        return RevalidationResult("STALE", False, f"execution feed stale {execution_age_sec:.1f}s", reasons=["STALE"])
    if fresh_venues < 2:
        return RevalidationResult("STALE", False, f"fresh venues {fresh_venues}<2", reasons=["STALE"])
    if candidate_age_sec > tol["max_age_sec"] * 3:  # council budget ceiling soft age
        # Still allow if structure intact and not chased; hard age is softer than literal signal repeat.
        reasons.append(f"candidate_age={candidate_age_sec:.1f}s")

    if spread_bps > max_spread_bps:
        return RevalidationResult(
            "SPREAD", False,
            f"spread {spread_bps:.1f}>{max_spread_bps:.0f}bps",
            reasons=["SPREAD"],
        )
    if not liquidity_ok:
        return RevalidationResult("LIQUIDITY", False, "liquidity deteriorated", reasons=["LIQUIDITY"])

    drift = _oriented_move_bps(candidate_entry, live_price, side)

    # Reversal: price moved against the intended side.
    if drift < -tol["max_adverse_bps"]:
        return RevalidationResult(
            "REVERSAL", False,
            f"adverse drift {drift:.1f}bps",
            drift_bps=drift, reasons=["REVERSAL"],
        )

    # Chase: price already ran too far in trade direction while AI deliberated.
    if drift > tol["max_chase_bps"]:
        return RevalidationResult(
            "CHASE", False,
            f"chase drift {drift:.1f}bps > {tol['max_chase_bps']:.0f}",
            drift_bps=drift, reasons=["CHASE", "SKIP_CHASE"],
        )

    # Flow/book reversal only when metrics are known and clearly against.
    if flow_status == "OK" and flow_oriented is not None and flow_oriented < 0.75:
        return RevalidationResult(
            "REVERSAL", False,
            f"flow reversal oriented={flow_oriented:.2f}",
            drift_bps=drift, reasons=["FLOW_REVERSAL"],
        )
    if book_status == "OK" and book_oriented is not None and book_oriented < 0.75:
        return RevalidationResult(
            "REVERSAL", False,
            f"book reversal oriented={book_oriented:.2f}",
            drift_bps=drift, reasons=["BOOK_REVERSAL"],
        )

    if not structure_ok or not same_direction_hint:
        return RevalidationResult(
            "REVERSAL", False, "structure/direction broken",
            drift_bps=drift, reasons=["STRUCTURE"],
        )

    edge = 0.0 if residual_edge is None else float(residual_edge)
    # Mild pullback with intact structure: do not demand the original second-impulse magnitude.
    pullback_ok = -tol["max_adverse_bps"] * 0.5 <= drift <= tol["max_chase_bps"] * 0.35
    if residual_edge is not None and edge < tol["min_residual_edge"] and not pullback_ok:
        return RevalidationResult(
            "EDGE_GONE", False,
            f"residual edge {edge:.3f}% < {tol['min_residual_edge']:.3f}%",
            drift_bps=drift, residual_edge=edge, reasons=["EDGE_GONE"],
        )

    # Mild adverse or mild favorable (pullback after impulse) with structure intact → PASS.
    detail = f"PASS drift={drift:.1f}bps age={candidate_age_sec:.1f}s edge={edge:.3f}%"
    if -tol["max_adverse_bps"] * 0.5 <= drift <= 0:
        detail += "; pullback_entry_ok"
    if residual_edge is not None and edge < tol["min_residual_edge"] and pullback_ok:
        detail += "; residual_soft_ok"
    return RevalidationResult(
        "PASS", True, detail,
        drift_bps=drift, residual_edge=edge, reasons=reasons or ["PASS"],
    )
