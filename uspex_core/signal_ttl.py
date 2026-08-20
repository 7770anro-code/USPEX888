"""Signal half-life / TTL — do not execute dead micro impulses after Council delay."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Mode-aware hard TTL for FAST Layer-B triggers (milliseconds).
SIGNAL_TTL = {
    # hard_ttl: absolute expire; half_life: expected useful life; residual_required_after: after this age need residual edge
    "easy":   {"half_life_ms": 4500, "hard_ttl_ms": 10000, "residual_required_after_ms": 5000},
    "medium": {"half_life_ms": 3500, "hard_ttl_ms": 8000,  "residual_required_after_ms": 4000},
    "big":    {"half_life_ms": 2500, "hard_ttl_ms": 6000,  "residual_required_after_ms": 3000},
    "ai":     {"half_life_ms": 3000, "hard_ttl_ms": 7000,  "residual_required_after_ms": 3500},
    "manual": {"half_life_ms": 8000, "hard_ttl_ms": 20000, "residual_required_after_ms": 10000},
}


@dataclass
class TtlDecision:
    code: str  # RESIDUAL_EDGE_OK | SIGNAL_EXPIRED | SIGNAL_DECAY | RESIDUAL_EDGE_GONE
    ok: bool
    age_ms: float
    hard_ttl_ms: float
    half_life_ms: float
    detail: str


def adaptive_ttl_ms(
    profile: str,
    *,
    original_lag_pct: float = 0.0,
    spread_bps: float = 0.0,
    volatility_proxy: float = 0.0,
    liquidity_ok: bool = True,
) -> dict:
    base = dict(SIGNAL_TTL.get(profile, SIGNAL_TTL["medium"]))
    # Larger original lag → slightly longer TTL; wide spread / thin liq → shorter.
    lag_boost = min(1500.0, max(0.0, abs(original_lag_pct) * 2000.0))
    spread_pen = min(2000.0, max(0.0, (spread_bps - 8.0) * 80.0))
    vol_pen = min(1500.0, max(0.0, volatility_proxy * 500.0))
    liq_pen = 0.0 if liquidity_ok else 1500.0
    hard = max(2500.0, base["hard_ttl_ms"] + lag_boost - spread_pen - vol_pen - liq_pen)
    half = max(1200.0, min(hard * 0.55, base["half_life_ms"] + lag_boost * 0.5))
    residual_after = max(half, base["residual_required_after_ms"])
    return {"half_life_ms": half, "hard_ttl_ms": hard, "residual_required_after_ms": residual_after}


def evaluate_signal_ttl(
    profile: str,
    *,
    age_ms: float,
    residual_edge: Optional[float],
    min_residual: float,
    original_lag_pct: float = 0.0,
    spread_bps: float = 0.0,
    volatility_proxy: float = 0.0,
    liquidity_ok: bool = True,
) -> TtlDecision:
    ttl = adaptive_ttl_ms(
        profile,
        original_lag_pct=original_lag_pct,
        spread_bps=spread_bps,
        volatility_proxy=volatility_proxy,
        liquidity_ok=liquidity_ok,
    )
    age = max(0.0, float(age_ms))
    if age > ttl["hard_ttl_ms"]:
        # Past hard TTL: only survive if residual edge still clearly alive.
        if residual_edge is not None and residual_edge >= min_residual:
            return TtlDecision(
                "RESIDUAL_EDGE_OK", True, age, ttl["hard_ttl_ms"], ttl["half_life_ms"],
                f"past hard TTL {ttl['hard_ttl_ms']:.0f}ms but residual {residual_edge:.3f}% ok",
            )
        return TtlDecision(
            "SIGNAL_EXPIRED", False, age, ttl["hard_ttl_ms"], ttl["half_life_ms"],
            f"age {age:.0f}ms > hard TTL {ttl['hard_ttl_ms']:.0f}ms",
        )
    if age > ttl["residual_required_after_ms"]:
        if residual_edge is None:
            return TtlDecision(
                "SIGNAL_DECAY", False, age, ttl["hard_ttl_ms"], ttl["half_life_ms"],
                f"age {age:.0f}ms requires residual edge but none measured",
            )
        if residual_edge < min_residual:
            return TtlDecision(
                "RESIDUAL_EDGE_GONE", False, age, ttl["hard_ttl_ms"], ttl["half_life_ms"],
                f"residual {residual_edge:.3f}% < {min_residual:.3f}% after {age:.0f}ms",
            )
        return TtlDecision(
            "RESIDUAL_EDGE_OK", True, age, ttl["hard_ttl_ms"], ttl["half_life_ms"],
            f"residual still alive {residual_edge:.3f}% age={age:.0f}ms",
        )
    if age > ttl["half_life_ms"] and residual_edge is not None and residual_edge < min_residual * 0.5:
        return TtlDecision(
            "SIGNAL_DECAY", False, age, ttl["hard_ttl_ms"], ttl["half_life_ms"],
            f"past half-life {ttl['half_life_ms']:.0f}ms with weak residual {residual_edge:.3f}%",
        )
    return TtlDecision(
        "RESIDUAL_EDGE_OK", True, age, ttl["hard_ttl_ms"], ttl["half_life_ms"],
        f"within TTL age={age:.0f}ms half={ttl['half_life_ms']:.0f}ms",
    )
