"""Latency decay sensitivity — not a profitability backtest."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .revalidation import revalidate_entry
from .signal_ttl import evaluate_signal_ttl
from .entry_window import classify_entry_window


LATENCY_STEPS_SEC = (0, 1, 3, 5, 8, 12, 30)


def simulate_latency_decay(snapshots: List[dict], profile: str = "medium") -> dict:
    """For each artificial latency, count PASS / CHASE / REVERSAL / EDGE_GONE / EXPIRED."""
    report = {str(s): {"valid": 0, "chase": 0, "reversal": 0, "edge_gone": 0, "expired": 0, "n": 0} for s in LATENCY_STEPS_SEC}
    for snap in snapshots:
        entry = float(snap.get("entry", 100))
        side = snap.get("side", "LONG")
        # price path after detect: linear drift toward end_price
        end = float(snap.get("price_after_30s", entry * (1.01 if side == "LONG" else 0.99)))
        residual0 = float(snap.get("residual_edge", 0.08))
        for sec in LATENCY_STEPS_SEC:
            report[str(sec)]["n"] += 1
            # interpolate price at latency
            frac = min(1.0, sec / 30.0)
            live = entry + (end - entry) * frac
            residual = residual0 * max(0.0, 1.0 - frac * 1.2)
            age_ms = sec * 1000.0
            ttl = evaluate_signal_ttl(
                profile, age_ms=age_ms, residual_edge=residual, min_residual=0.03,
                original_lag_pct=residual0, spread_bps=float(snap.get("spread_bps", 5)),
            )
            if not ttl.ok:
                key = "expired" if ttl.code == "SIGNAL_EXPIRED" else "edge_gone"
                report[str(sec)][key] += 1
                continue
            rv = revalidate_entry(
                profile=profile, side=side, candidate_entry=entry, live_price=live,
                candidate_age_sec=sec, execution_age_sec=0.5, fresh_venues=2, fresh_age_limit=7,
                spread_bps=float(snap.get("spread_bps", 5)), max_spread_bps=16,
                residual_edge=residual,
            )
            if rv.ok:
                ew = classify_entry_window(
                    side=side, first_detect_price=entry, best_price_since_detect=max(entry, live) if side == "LONG" else min(entry, live),
                    current_price=live, peak_edge=residual0, residual_edge=residual,
                    max_chase_bps=32, max_adverse_bps=25, min_residual=0.03,
                )
                if ew.ok:
                    report[str(sec)]["valid"] += 1
                elif ew.code == "PRICE_RAN_AWAY":
                    report[str(sec)]["chase"] += 1
                elif ew.code == "REVERSAL":
                    report[str(sec)]["reversal"] += 1
                else:
                    report[str(sec)]["edge_gone"] += 1
            elif rv.code == "CHASE":
                report[str(sec)]["chase"] += 1
            elif rv.code == "REVERSAL":
                report[str(sec)]["reversal"] += 1
            else:
                report[str(sec)]["edge_gone"] += 1
    return report


def default_snapshots(n: int = 200) -> List[dict]:
    import random
    rng = random.Random(11)
    out = []
    for i in range(n):
        side = "LONG" if i % 2 == 0 else "SHORT"
        entry = 100 + rng.random()
        move = rng.uniform(0.002, 0.02)
        end = entry * (1 + move) if side == "LONG" else entry * (1 - move)
        out.append({
            "entry": entry, "side": side, "price_after_30s": end,
            "residual_edge": rng.uniform(0.04, 0.20), "spread_bps": rng.uniform(3, 14),
        })
    return out


def write_latency_decay_report(path: Path, profile: str = "medium") -> dict:
    snaps = default_snapshots(300)
    report = simulate_latency_decay(snaps, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"profile": profile, "n_snapshots": len(snaps), "by_latency_sec": report}, indent=2), encoding="utf-8")
    return report
