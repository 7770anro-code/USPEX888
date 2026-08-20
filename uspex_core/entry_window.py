"""Entry-window / no-chase classification beyond single drift percent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EntryWindowDecision:
    code: str  # PRICE_RAN_AWAY | HEALTHY_PULLBACK | EDGE_DISAPPEARED | REVERSAL | STILL_EXECUTABLE
    ok: bool
    detail: str


def classify_entry_window(
    *,
    side: str,
    first_detect_price: float,
    best_price_since_detect: float,
    current_price: float,
    peak_edge: float,
    residual_edge: float,
    max_chase_bps: float,
    max_adverse_bps: float,
    min_residual: float,
) -> EntryWindowDecision:
    if not first_detect_price or not current_price:
        return EntryWindowDecision("EDGE_DISAPPEARED", False, "missing prices")
    orient = 1.0 if side.upper() == "LONG" else -1.0
    drift_bps = orient * (current_price / first_detect_price - 1.0) * 10000.0
    best_bps = orient * (best_price_since_detect / first_detect_price - 1.0) * 10000.0 if best_price_since_detect else 0.0
    pullback_bps = max(0.0, best_bps - drift_bps)

    if drift_bps < -max_adverse_bps:
        return EntryWindowDecision("REVERSAL", False, f"adverse drift {drift_bps:.1f}bps")
    if residual_edge < min_residual * 0.35 and abs(drift_bps) < 3:
        return EntryWindowDecision("EDGE_DISAPPEARED", False, f"residual {residual_edge:.3f}% gone")
    if drift_bps > max_chase_bps and pullback_bps < max_chase_bps * 0.25:
        return EntryWindowDecision("PRICE_RAN_AWAY", False, f"ran away drift={drift_bps:.1f}bps pullback={pullback_bps:.1f}")
    if drift_bps > 0 and pullback_bps >= 3 and residual_edge >= min_residual * 0.7:
        return EntryWindowDecision(
            "HEALTHY_PULLBACK", True,
            f"pullback {pullback_bps:.1f}bps after best {best_bps:.1f}bps residual={residual_edge:.3f}%",
        )
    if drift_bps <= max_chase_bps and residual_edge >= min_residual * 0.6:
        return EntryWindowDecision(
            "STILL_EXECUTABLE", True,
            f"drift={drift_bps:.1f}bps residual={residual_edge:.3f}% peak={peak_edge:.3f}%",
        )
    if residual_edge < min_residual:
        return EntryWindowDecision("EDGE_DISAPPEARED", False, f"residual {residual_edge:.3f}% < min")
    return EntryWindowDecision("PRICE_RAN_AWAY", False, f"not executable drift={drift_bps:.1f}")
