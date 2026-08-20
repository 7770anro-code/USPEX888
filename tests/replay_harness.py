#!/usr/bin/env python3
"""Synthetic/replay harness — thousands of scenarios for invariants.
Does NOT claim profitability from synthetic PnL.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uspex_core.microstructure import robust_flow, robust_book, flow_score_bonus
from uspex_core.revalidation import revalidate_entry
from uspex_core.reconcile import evaluate_exchange_absence
from uspex_core.council import timeout_vote, council_gate
from uspex_core.data_quality import compute_data_quality, cap_uspex_score_by_quality


def run(n: int = 3000, seed: int = 42) -> dict:
    rng = random.Random(seed)
    fails = []
    stats = {
        "scenarios": 0,
        "false_close_blocked": 0,
        "absurd_flow_no_bonus": 0,
        "timeout_no_trade": 0,
        "stale_no_hard_boost": 0,
        "chase_blocked": 0,
    }

    for i in range(n):
        stats["scenarios"] += 1
        kind = i % 7

        if kind == 0:
            # near-zero sell → must not boost
            buy = 10 ** rng.uniform(2, 9)
            r = robust_flow([(0, buy)], [(0, 1e-18)], now_ts=1.0, window=1.0)
            if flow_score_bonus(r) != 0:
                fails.append(("absurd_flow_boost", i, r))
            else:
                stats["absurd_flow_no_bonus"] += 1

        elif kind == 1:
            # temporary empty position within grace
            d = evaluate_exchange_absence(
                age_since_open=rng.uniform(0, 15), reconcile_grace=20,
                position_visible=False, missing_checks=0, required_confirmations=4,
                closed_pnl_confirmed=False, api_ok=True,
            )
            if d.should_close_local:
                fails.append(("false_close", i, d))
            else:
                stats["false_close_blocked"] += 1

        elif kind == 2:
            # AI timeout must fail closed
            allow, gate = council_gate("medium", 95, timeout_vote("Cursor"), {"ok": True, "decision": "APPROVE", "confidence": 90, "timeout": False})
            if allow or "TIMEOUT" not in gate:
                fails.append(("timeout_trade", i, gate))
            else:
                stats["timeout_no_trade"] += 1

        elif kind == 3:
            # chase detection
            entry = 100.0
            live = entry * (1 + rng.uniform(0.004, 0.02))
            rv = revalidate_entry(
                profile="medium", side="LONG", candidate_entry=entry, live_price=live,
                candidate_age_sec=8, execution_age_sec=1, fresh_venues=2, fresh_age_limit=7,
                spread_bps=5, max_spread_bps=16, residual_edge=0.1,
            )
            if rv.code != "CHASE":
                fails.append(("chase_miss", i, rv))
            else:
                stats["chase_blocked"] += 1

        elif kind == 4:
            # stale execution must hard-reject DQ
            dq = compute_data_quality(
                feed_ages={"bybit": 40.0, "binance": 1.0, "okx": 1.0},
                fresh_age=7, spread_bps=5, turnover24h=1e6,
                flow_reliability=1, book_reliability=1, flow_status="OK", book_status="OK",
            )
            if not dq.hard_reject:
                fails.append(("stale_exec", i, dq))
            else:
                stats["stale_no_hard_boost"] += 1
                if cap_uspex_score_by_quality(99, dq) > 40:
                    fails.append(("dq_cap", i, dq))

        elif kind == 5:
            # extreme book
            r = robust_book(rng.uniform(20, 80), 1.0, mid_price=50)
            if r.status == "OK" and r.ratio and r.ratio > 5.01:
                fails.append(("book_unclipped", i, r))

        else:
            # no trade without confirmed fill analogue: require closed pnl after misses
            d = evaluate_exchange_absence(
                age_since_open=60, reconcile_grace=20, position_visible=False,
                missing_checks=5, required_confirmations=4, closed_pnl_confirmed=False, api_ok=True,
            )
            if d.should_close_local:
                fails.append(("close_without_pnl", i, d))

    return {"ok": not fails, "fails": fails[:20], "fail_count": len(fails), "stats": stats}


def regression_old_vs_new(snapshots_path: Path) -> dict:
    """Compare old absurd-ratio scoring vs new robust scoring on shared snapshots."""
    if not snapshots_path.exists():
        # generate synthetic snapshots
        snaps = []
        rng = random.Random(7)
        for i in range(200):
            snaps.append({
                "buy": 10 ** rng.uniform(0, 8),
                "sell": 10 ** rng.uniform(-6, 3),
                "bq": rng.uniform(0, 100),
                "aq": rng.uniform(0, 5),
                "old_approve": rng.random() > 0.7,
            })
        snapshots_path.parent.mkdir(parents=True, exist_ok=True)
        snapshots_path.write_text(json.dumps(snaps), encoding="utf-8")
    snaps = json.loads(snapshots_path.read_text(encoding="utf-8"))
    matrix = {"old_approve_new_approve": 0, "old_approve_new_reject": 0,
              "old_reject_new_approve": 0, "old_reject_new_reject": 0, "reasons": []}
    for s in snaps:
        # OLD: raw ratio always numeric
        old_flow = (s["buy"] + 1e-9) / (s["sell"] + 1e-9) if (s["buy"] or s["sell"]) else 1.0
        old_score_boost = min(15, int(max(0, old_flow - 1) * 11))
        old_approve = bool(s.get("old_approve")) or old_score_boost >= 10

        r = robust_flow([(0, s["buy"])], [(0, s["sell"])], now_ts=1.0, window=1.0)
        new_boost = flow_score_bonus(r)
        new_approve = new_boost >= 8 and r.status == "OK"

        key = f"old_{'approve' if old_approve else 'reject'}_new_{'approve' if new_approve else 'reject'}"
        matrix[key] += 1
        if old_approve and not new_approve:
            matrix["reasons"].append(f"artifact_blocked status={r.status} raw={r.raw_ratio}")
    matrix["reasons"] = matrix["reasons"][:15]
    return matrix


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    out = run(n)
    reg = regression_old_vs_new(ROOT / "fixtures" / "snapshots" / "micro_snaps.json")
    print(json.dumps({"harness": out["stats"], "fail_count": out["fail_count"], "ok": out["ok"], "regression": reg}, indent=2))
    sys.exit(0 if out["ok"] else 1)
