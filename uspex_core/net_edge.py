"""Net edge after costs — raw lag alone is not a valid setup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class NetEdgeResult:
    gross_edge_bps: float
    expected_costs_bps: float
    uncertainty_bps: float
    expected_net_edge_bps: float
    ok: bool
    reason: str


def estimate_net_edge(
    *,
    gross_edge_bps: float,
    spread_bps: float = 0.0,
    expected_slippage_bps: float = 0.0,
    taker_fee_bps_roundtrip: float = 11.0,  # ~0.055% * 2 sides default
    funding_impact_bps: float = 0.0,
    uncertainty_bps: float = 2.0,
    min_net_bps: float = 3.0,
    spread_already_in_gross: bool = True,
) -> NetEdgeResult:
    """Net edge after costs.

    Default contract matches ``executable_price``: ``gross_edge_bps`` is vs
    ask (LONG) or bid (SHORT), so the *entry* half of the quoted bid-ask is
    already inside gross. Costs then charge only the *exit* half so the
    round-trip spread is counted once.

    Pass ``spread_already_in_gross=False`` when gross is vs mid / return-gap
    and the full quoted spread still belongs in costs.
    """
    quoted_spread = max(0.0, float(spread_bps))
    # Entry half already in executable gross → charge exit half only.
    spread_cost = quoted_spread * 0.5 if spread_already_in_gross else quoted_spread
    costs = spread_cost + max(0.0, float(expected_slippage_bps))
    costs += max(0.0, float(taker_fee_bps_roundtrip)) + max(0.0, float(funding_impact_bps))
    unc = max(0.0, float(uncertainty_bps))
    net = float(gross_edge_bps) - costs - unc
    ok = net >= float(min_net_bps)
    return NetEdgeResult(
        gross_edge_bps=float(gross_edge_bps),
        expected_costs_bps=costs,
        uncertainty_bps=unc,
        expected_net_edge_bps=net,
        ok=ok,
        reason="NET_EDGE_OK" if ok else "NET_EDGE_TOO_SMALL",
    )


def net_edge_reject_record(net: NetEdgeResult, *, side: str, score: float, edge_bps: Optional[float] = None) -> dict:
    """Structured reject for candidate() so the scanner can journal NET_EDGE_REJECT."""
    return {
        "reject": "NET_EDGE_REJECT",
        "reject_detail": (
            f"{net.reason} gross={net.gross_edge_bps:.1f}bps costs={net.expected_costs_bps:.1f} "
            f"unc={net.uncertainty_bps:.1f} net={net.expected_net_edge_bps:.1f}bps"
        ),
        "side": side,
        "score": float(score),
        "net_edge_bps": net.expected_net_edge_bps,
        "edge_bps": edge_bps,
    }


def executable_price(bid: Optional[float], ask: Optional[float], side: str) -> Optional[float]:
    """LONG enters at ask; SHORT at bid. Never use mid as executable reference."""
    s = str(side).upper()
    try:
        if s == "LONG":
            v = float(ask) if ask is not None else None
        elif s == "SHORT":
            v = float(bid) if bid is not None else None
        else:
            return None
    except (TypeError, ValueError):
        return None
    if v is None or v <= 0:
        return None
    return v
