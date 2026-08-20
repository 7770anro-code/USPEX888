"""Deterministic portfolio / risk guard. AI cannot override these limits."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class PortfolioGuardResult:
    ok: bool
    code: str
    detail: str


def portfolio_guard(
    *,
    available: float,
    equity: float,
    new_margin: float,
    live_margin: float,
    position_count: int,
    max_positions: int,
    single_available_frac: float,
    max_total_margin_pct: float,
    leverage: float,
    max_leverage: float = 100.0,
    per_position_risk: float = 0.0,
    max_per_position_risk: Optional[float] = None,
    same_direction_count: int = 0,
    max_same_direction: int = 3,
    correlated_exposure: float = 0.0,
    max_correlated_exposure: Optional[float] = None,
) -> PortfolioGuardResult:
    if available < 0 or equity <= 0:
        return PortfolioGuardResult(False, "RISK_REJECT", "invalid wallet snapshot")
    if max_positions > 0 and position_count >= max_positions:
        return PortfolioGuardResult(False, "RISK_REJECT", f"position_count {position_count}>={max_positions}")
    if leverage <= 0 or leverage > max_leverage:
        return PortfolioGuardResult(False, "RISK_REJECT", f"leverage {leverage} out of range")
    single_cap = available * single_available_frac
    if new_margin > single_cap + 1e-9:
        return PortfolioGuardResult(
            False, "RISK_REJECT",
            f"margin ${new_margin:.2f} > singleCap ${single_cap:.2f}",
        )
    total_cap = equity * max_total_margin_pct / 100.0
    if live_margin + new_margin > total_cap + 1e-9:
        return PortfolioGuardResult(
            False, "RISK_REJECT",
            f"live+new ${live_margin+new_margin:.2f} > totalCap ${total_cap:.2f}",
        )
    if max_per_position_risk is not None and per_position_risk > max_per_position_risk:
        return PortfolioGuardResult(
            False, "RISK_REJECT",
            f"per-position risk ${per_position_risk:.2f} > cap ${max_per_position_risk:.2f}",
        )
    if same_direction_count >= max_same_direction:
        return PortfolioGuardResult(
            False, "RISK_REJECT",
            f"same-direction concentration {same_direction_count}>={max_same_direction}",
        )
    if max_correlated_exposure is not None and correlated_exposure > max_correlated_exposure:
        return PortfolioGuardResult(
            False, "RISK_REJECT",
            f"correlated exposure ${correlated_exposure:.2f} > ${max_correlated_exposure:.2f}",
        )
    return PortfolioGuardResult(True, "RISK_OK", "portfolio guard pass")
