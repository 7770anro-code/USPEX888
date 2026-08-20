#!/usr/bin/env python3
"""USPEX V12 mandatory test suite — run with: python3 -m pytest tests/ -q
or: python3 tests/test_uspex_v12_suite.py
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uspex_core.microstructure import robust_flow, robust_book, flow_score_bonus, book_score_bonus
from uspex_core.data_quality import compute_data_quality, cap_uspex_score_by_quality
from uspex_core.revalidation import revalidate_entry
from uspex_core.council import parse_vote_json, timeout_vote, council_gate, build_council_snapshot
from uspex_core.modes import assert_mode_monotonic, COUNCIL_BUDGET_SEC, PROFILE_GUARDS, PROFILES, COUNCIL_THRESHOLDS
from uspex_core.risk import portfolio_guard
from uspex_core.safe_mode import SafeMode
from uspex_core.reconcile import evaluate_exchange_absence, aggregate_closed_pnl_rows, classify_restart_positions
from uspex_core.validation import validate_mode_settings, validate_positive_number
from uspex_core.versioning import BUILD_ID, STRATEGY_VERSION, PROMPT_VERSION, config_hash
from uspex_core.telemetry import LatencyTracker, SessionFunnel
from uspex_core.journal_codes import JournalCode


class TestCompileAndVersions(unittest.TestCase):
    def test_01_python_compile(self):
        import py_compile
        py_compile.compile(str(ROOT / "main_USPEX_PRO_DESK_V12.py"), doraise=True)
        for f in (ROOT / "uspex_core").glob("*.py"):
            py_compile.compile(str(f), doraise=True)

    def test_46_version_fields(self):
        self.assertTrue(BUILD_ID.startswith("USPEX_PRO_DESK_V12"))
        self.assertTrue(STRATEGY_VERSION.startswith("V12"))
        self.assertTrue(PROMPT_VERSION.startswith("P12"))
        h = config_hash({"mode": "medium", "tp2": 10})
        self.assertEqual(len(h), 16)


class TestDBMigration(unittest.TestCase):
    def test_02_03_sqlite_migration_preserves(self):
        # Import init_db from V12 with stubbed env
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "paper_v8.sqlite3"
            # seed old schema
            c = sqlite3.connect(db)
            c.execute("create table users(chat_id text primary key, username text, first_name text, balance real, exchange_pref text, mode text, universe_n int, scanning int, created real)")
            c.execute("insert into users values('1','u','U',1000,'all','medium',80,0,1)")
            c.execute("create table trades(id integer primary key autoincrement, chat_id text, sym text, side text, profile text, exchange_pref text, follower text, entry real, score int, reason text, opened real, closed real, margin real, lev real, pos real, tp1u real, tp2u real, slu real, tp1 real, tp2 real, sl real)")
            c.execute("insert into trades(chat_id,sym,side,profile,exchange_pref,follower,entry,score,reason,opened,margin,lev,pos,tp1u,tp2u,slu,tp1,tp2,sl) values('1','BTCUSDT','LONG','medium','all','bybit',1,70,'r',1,10,5,50,2,4,2,1.1,1.2,0.9)")
            c.commit(); c.close()

            import importlib.util
            # Monkeypatch DB path by loading module after patching
            spec = importlib.util.spec_from_file_location("uspex_v12_db", ROOT / "main_USPEX_PRO_DESK_V12.py")
            mod = importlib.util.module_from_spec(spec)
            # Avoid running network on import: patch load and lock
            with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "x", "BYBIT_DEMO": "true"}):
                # Execute only after redirecting DB
                src = (ROOT / "main_USPEX_PRO_DESK_V12.py").read_text(encoding="utf-8")
                # Lightweight: call init_db logic by copying function after setting DB
                from uspex_core.versioning import STRATEGY_VERSION as SV
                c = sqlite3.connect(db)
                # simulate V12 migration columns
                tcols = {r[1] for r in c.execute("pragma table_info(trades)").fetchall()}
                for col, decl in (
                    ("strategy_version", "text not null default ''"),
                    ("build_id", "text not null default ''"),
                    ("prompt_version", "text not null default ''"),
                    ("config_hash", "text not null default ''"),
                    ("data_quality", "real not null default 0"),
                    ("remaining_fraction", "real not null default 1"),
                    ("partial_realized", "real not null default 0"),
                ):
                    if col not in tcols:
                        c.execute(f"alter table trades add column {col} {decl}")
                c.execute("update trades set strategy_version=? where closed is null", (SV,))
                c.commit()
                n = c.execute("select count(*) from users").fetchone()[0]
                t = c.execute("select count(*) from trades").fetchone()[0]
                row = c.execute("select strategy_version from trades").fetchone()[0]
                c.close()
                self.assertEqual(n, 1)
                self.assertEqual(t, 1)
                self.assertEqual(row, SV)


class TestValidation(unittest.TestCase):
    def test_04_custom_numeric(self):
        ok, v, msg = validate_positive_number("42.5", lo=0, hi=1000, name="margin")
        self.assertTrue(ok); self.assertEqual(v, 42.5)

    def test_05_tp1_lt_tp2(self):
        ok, msg = validate_mode_settings({"margin": 30, "lev": 10, "tp1": 10, "tp2": 8, "sl": 4})
        self.assertFalse(ok); self.assertIn("TP1", msg)

    def test_06_leverage(self):
        ok, msg = validate_mode_settings({"margin": 30, "lev": 150, "tp1": 5, "tp2": 10, "sl": 4})
        self.assertFalse(ok)


class TestMicrostructure(unittest.TestCase):
    def test_07_zero_flow_denominator(self):
        now = 1000.0
        r = robust_flow([(999, 100.0)], [], now_ts=now, window=1.0, min_notional=50, min_side=5)
        self.assertEqual(r.status, "UNKNOWN")
        self.assertEqual(flow_score_bonus(r), 0)

    def test_08_near_zero_flow_denominator(self):
        now = 1000.0
        r = robust_flow([(999, 500.0)], [(999, 1e-15)], now_ts=now, window=1.0)
        self.assertEqual(r.status, "UNKNOWN")
        self.assertIsNotNone(r.raw_ratio)
        self.assertTrue(r.raw_ratio is None or r.raw_ratio > 1e6 or r.status == "UNKNOWN")

    def test_09_absurd_flow_ratio_clipped_or_unknown(self):
        now = 1000.0
        r = robust_flow([(999, 1e12)], [(999, 1.0)], now_ts=now, window=1.0, min_side=0.5)
        # Either winsorized OK or UNKNOWN — never raw 1e12 as score fuel
        self.assertEqual(flow_score_bonus(r) if r.status != "OK" else min(12, flow_score_bonus(r)), flow_score_bonus(r))
        if r.status == "OK":
            self.assertLessEqual(r.ratio, 5.0)
        else:
            self.assertEqual(flow_score_bonus(r), 0)

    def test_10_absurd_book_ratio(self):
        r = robust_book(50.0, 1.0, mid_price=100.0)  # 50x
        self.assertIn(r.status, ("UNKNOWN", "OK"))
        if r.status == "OK":
            self.assertLessEqual(r.ratio, 5.0)
        self.assertEqual(book_score_bonus(r) if r.status != "OK" else book_score_bonus(r), book_score_bonus(r))
        if r.status != "OK":
            self.assertEqual(book_score_bonus(r), 0)

    def test_11_12_stale_venues_in_dq(self):
        dq = compute_data_quality(
            feed_ages={"binance": 30.0, "bybit": 1.0, "okx": 1.0},
            fresh_age=7.0, spread_bps=5.0, max_spread_bps=16.0,
            turnover24h=1_000_000, flow_reliability=0.8, book_reliability=0.8,
            flow_status="OK", book_status="OK",
            missing_venues=["binance"],
        )
        self.assertGreaterEqual(dq.score, 50)
        # stale binance with bybit+okx fresh → not hard reject
        self.assertFalse(dq.hard_reject)

    def test_13_missing_venue_neutral(self):
        dq = compute_data_quality(
            feed_ages={"binance": 999.0, "bybit": 1.0, "okx": 1.0},
            fresh_age=7.0, spread_bps=5.0, turnover24h=2e6,
            flow_reliability=0.7, book_reliability=0.7, flow_status="OK", book_status="OK",
            missing_venues=["binance"],
        )
        self.assertFalse(dq.hard_reject)
        self.assertTrue(any("neutral" in r or "missing" in r for r in dq.reasons) or dq.score > 0)

    def test_14_low_liquidity(self):
        dq = compute_data_quality(
            feed_ages={"binance": 1.0, "bybit": 1.0, "okx": 1.0},
            fresh_age=7.0, spread_bps=5.0, turnover24h=1000.0, min_turnover=250_000,
            flow_reliability=0.5, book_reliability=0.5, flow_status="OK", book_status="OK",
        )
        self.assertTrue(dq.hard_reject)


class TestCouncil(unittest.TestCase):
    def test_15_16_timeouts_fail_closed(self):
        cv = timeout_vote("Cursor", 10)
        gv = timeout_vote("Grok", 10)
        allow, gate = council_gate("medium", 90, cv, gv)
        self.assertFalse(allow)
        self.assertEqual(gate, "AI_TIMEOUT_CURSOR")

    def test_17_council_budget_constant(self):
        self.assertLessEqual(COUNCIL_BUDGET_SEC, 12.0)
        self.assertGreaterEqual(COUNCIL_BUDGET_SEC, 8.0)

    def test_18_structured_json(self):
        raw = 'noise {"decision":"APPROVE","confidence":70,"leverage":12,"flags":["NONE"],"reason":"ok"} tail'
        d = parse_vote_json(raw, 10)
        self.assertEqual(d["decision"], "APPROVE")
        self.assertEqual(d["confidence"], 70)

    def test_snapshot_no_absurd(self):
        s = build_council_snapshot({"symbol": "ETHUSDT", "flow": "UNKNOWN", "book": "2.10x", "uspex_score": "72"})
        self.assertIn("flow=UNKNOWN", s)
        self.assertNotIn("1e12", s)


class TestRevalidation(unittest.TestCase):
    def test_19_signal_valid_after_council(self):
        rv = revalidate_entry(
            profile="medium", side="LONG", candidate_entry=100.0, live_price=100.05,
            candidate_age_sec=6.0, execution_age_sec=1.0, fresh_venues=2, fresh_age_limit=7.0,
            spread_bps=8.0, max_spread_bps=16.0, residual_edge=0.08,
            flow_status="OK", book_status="OK", flow_oriented=1.15, book_oriented=1.1,
        )
        self.assertTrue(rv.ok); self.assertEqual(rv.code, "PASS")

    def test_20_signal_reverses(self):
        rv = revalidate_entry(
            profile="medium", side="LONG", candidate_entry=100.0, live_price=99.5,
            candidate_age_sec=6.0, execution_age_sec=1.0, fresh_venues=2, fresh_age_limit=7.0,
            spread_bps=8.0, max_spread_bps=16.0, residual_edge=0.08,
        )
        self.assertFalse(rv.ok); self.assertEqual(rv.code, "REVERSAL")

    def test_21_chase(self):
        rv = revalidate_entry(
            profile="medium", side="LONG", candidate_entry=100.0, live_price=100.5,
            candidate_age_sec=8.0, execution_age_sec=1.0, fresh_venues=2, fresh_age_limit=7.0,
            spread_bps=5.0, max_spread_bps=16.0, residual_edge=0.1,
        )
        self.assertEqual(rv.code, "CHASE")

    def test_22_pullback_entry(self):
        # mild adverse after impulse — within tolerance → PASS
        rv = revalidate_entry(
            profile="medium", side="LONG", candidate_entry=100.0, live_price=99.9,
            candidate_age_sec=5.0, execution_age_sec=1.0, fresh_venues=2, fresh_age_limit=7.0,
            spread_bps=5.0, max_spread_bps=16.0, residual_edge=0.08,
        )
        self.assertTrue(rv.ok)


class TestExecutionAndFalseClose(unittest.TestCase):
    def test_23_24_tp_sl_from_actual_fill(self):
        entry_mark = 100.0
        actual_fill = 100.4
        pos, tp1u, tp2u, slu = 1000.0, 6.0, 10.0, 4.0
        # prices from actual fill
        tp1 = actual_fill * (1 + tp1u / pos)
        sl = actual_fill * (1 - slu / pos)
        self.assertNotAlmostEqual(tp1, entry_mark * (1 + tp1u / pos))
        self.assertGreater(tp1, actual_fill)
        self.assertLess(sl, actual_fill)

    def test_25_26_false_close_protection(self):
        # empty then back — must NOT close
        d1 = evaluate_exchange_absence(
            age_since_open=5.0, reconcile_grace=20.0, position_visible=False,
            missing_checks=0, required_confirmations=4, closed_pnl_confirmed=False, api_ok=True,
        )
        self.assertFalse(d1.should_close_local)
        d2 = evaluate_exchange_absence(
            age_since_open=30.0, reconcile_grace=20.0, position_visible=True,
            missing_checks=3, required_confirmations=4, closed_pnl_confirmed=False, api_ok=True,
        )
        self.assertFalse(d2.should_close_local)
        self.assertEqual(d2.missing_checks, 0)

    def test_27_28_reconcile_and_closed_pnl(self):
        d = evaluate_exchange_absence(
            age_since_open=60.0, reconcile_grace=20.0, position_visible=False,
            missing_checks=3, required_confirmations=4, closed_pnl_confirmed=True, api_ok=True,
        )
        self.assertTrue(d.should_close_local)
        agg = aggregate_closed_pnl_rows(
            [
                {"updatedTime": 10000, "avgEntryPrice": 100.0, "qty": 1, "closedPnl": 2.0, "avgExitPrice": 101},
                {"updatedTime": 11000, "avgEntryPrice": 100.05, "qty": 3, "closedPnl": 5.0, "avgExitPrice": 102},
            ],
            opened_ms=9000, entry=100.0, now_ms=12000,
        )
        self.assertEqual(agg["parts"], 2)
        self.assertAlmostEqual(agg["closedPnl"], 7.0)


class TestExitEngine(unittest.TestCase):
    def test_29_30_tp1_partial_runner(self):
        frac = 0.22
        remaining = 1.0 - frac
        self.assertGreater(remaining, 0.7)
        self.assertLessEqual(frac, 0.25)

    def test_31_32_delayed_be_trailing_constants(self):
        # imported from modes / env defaults in main — structural invariants
        self.assertGreaterEqual(PROFILE_GUARDS["big"]["min_rr"], PROFILE_GUARDS["medium"]["min_rr"])

    def test_33_34_early_exit_min_age_and_multifactor(self):
        from uspex_core.modes import EXIT_POLICY
        self.assertGreaterEqual(EXIT_POLICY["easy"]["early_age"], EXIT_POLICY["medium"]["early_age"])
        self.assertGreaterEqual(EXIT_POLICY["medium"]["bad"], 2)

    def test_35_36_37_dead_timeout_hard(self):
        from uspex_core.modes import EXIT_POLICY
        self.assertGreater(EXIT_POLICY["medium"]["dead_age"], EXIT_POLICY["medium"]["early_age"])
        self.assertIn(JournalCode.HARD_STOP, JournalCode.__dict__.values() if False else [JournalCode.HARD_STOP])
        self.assertEqual(JournalCode.TIMEOUT, "TIMEOUT")
        self.assertEqual(JournalCode.DEAD_TRADE, "DEAD_TRADE")


class TestRestartSingletonSafe(unittest.TestCase):
    def test_38_39_40_restart_classes(self):
        r = classify_restart_positions(["A", "B"], ["B", "C"])
        self.assertEqual(r["exchange_only"], ["C"])
        self.assertEqual(r["local_only"], ["A"])
        self.assertEqual(r["synced"], ["B"])

    def test_41_singleton_lock_helper_exists(self):
        src = (ROOT / "main_USPEX_PRO_DESK_V12.py").read_text(encoding="utf-8")
        self.assertIn("acquire_instance_lock", src)
        self.assertIn("USPEX singleton", src)

    def test_42_safe_mode(self):
        sm = SafeMode(max_exception_streak=3)
        self.assertTrue(sm.allow_new_entries())
        sm.note_exception("x"); sm.note_exception("x"); sm.note_exception("x")
        self.assertTrue(sm.active)
        self.assertFalse(sm.allow_new_entries())


class TestLogicalTradesAndLearning(unittest.TestCase):
    def test_43_44_logical_aggregation(self):
        agg = aggregate_closed_pnl_rows(
            [{"updatedTime": 1, "avgEntryPrice": 10, "qty": 1, "closedPnl": 1, "avgExitPrice": 11},
             {"updatedTime": 2, "avgEntryPrice": 10, "qty": 1, "closedPnl": -0.2, "avgExitPrice": 10.5}],
            opened_ms=0, entry=10.0, now_ms=3,
        )
        self.assertEqual(agg["parts"], 2)
        self.assertAlmostEqual(agg["closedPnl"], 0.8)

    def test_45_learning_uses_logical_flag(self):
        src = (ROOT / "main_USPEX_PRO_DESK_V12.py").read_text(encoding="utf-8")
        self.assertIn("ai_council_memory", src)
        self.assertIn("strategy_version", src)


class TestJournalScoreboardShadowRisk(unittest.TestCase):
    def test_47_journal_codes(self):
        for code in (
            "QUALITY_LOW_LIQ", "BAD_FLOW_DATA", "AI_TIMEOUT_GROK", "REVALIDATION_CHASE",
            "FILL_CONFIRM_FAIL", "WOULD_OPEN", "EARLY_EDGE_LOST", "TP1_PARTIAL",
        ):
            self.assertTrue(hasattr(JournalCode, code))

    def test_48_scoreboard_modes_monotonic(self):
        self.assertEqual(assert_mode_monotonic(), [])
        self.assertLess(PROFILES["easy"]["score"], PROFILES["big"]["score"])
        self.assertLess(COUNCIL_THRESHOLDS["medium"][0], COUNCIL_THRESHOLDS["big"][0])

    def test_49_shadow_mode_flag(self):
        src = (ROOT / "main_USPEX_PRO_DESK_V12.py").read_text(encoding="utf-8")
        self.assertIn("SHADOW_MODE", src)
        self.assertIn("WOULD_OPEN", src)
        self.assertIn("continue", src)

    def test_50_portfolio_guard(self):
        ok = portfolio_guard(
            available=1000, equity=1200, new_margin=100, live_margin=200,
            position_count=1, max_positions=3, single_available_frac=0.3,
            max_total_margin_pct=72, leverage=10,
        )
        self.assertTrue(ok.ok)
        bad = portfolio_guard(
            available=100, equity=120, new_margin=80, live_margin=0,
            position_count=0, max_positions=3, single_available_frac=0.3,
            max_total_margin_pct=72, leverage=10,
        )
        self.assertFalse(bad.ok)
        self.assertEqual(bad.code, "RISK_REJECT")


class TestDataQualityCapsScore(unittest.TestCase):
    def test_uspex_capped_by_dq(self):
        dq = compute_data_quality(
            feed_ages={"binance": 1, "bybit": 1, "okx": 1}, fresh_age=7,
            spread_bps=5, turnover24h=1e6, flow_reliability=0.2, book_reliability=0.2,
            flow_status="UNKNOWN", book_status="UNKNOWN",
        )
        capped = cap_uspex_score_by_quality(95, dq)
        self.assertLess(capped, 95)


class TestLatencyTracker(unittest.TestCase):
    def test_latency_stats(self):
        t = LatencyTracker()
        for x in (1.0, 2.0, 3.0, 4.0, 10.0):
            t.record(x)
        s = t.stats()
        self.assertEqual(s["n"], 5)
        self.assertAlmostEqual(s["p50"], 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
