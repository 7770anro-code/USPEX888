#!/usr/bin/env python3
"""V12.1 tests for latency / TTL / Layer A+B / coalesce / entry window / decay.
Honest runner: prints each test name + PASS/FAIL. Run: python3 tests/test_latency_architecture.py
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uspex_core.signal_ttl import evaluate_signal_ttl, adaptive_ttl_ms, SIGNAL_TTL
from uspex_core.latency import PipelineTrace, LatencyRegistry, latency_bucket_from_ms
from uspex_core.coalesce import CandidateCoalescer
from uspex_core.ai_context import AiContextCache, AiContext
from uspex_core.entry_window import classify_entry_window
from uspex_core.latency_decay import simulate_latency_decay, default_snapshots, write_latency_decay_report, LATENCY_STEPS_SEC
from uspex_core.modes import COUNCIL_BUDGET_SEC, CURSOR_VOTE_TIMEOUT_SEC, GROK_VOTE_TIMEOUT_SEC, CURSOR_SPAWN_TIMEOUT_SEC
from uspex_core.council import timeout_vote, council_gate
from uspex_core.journal_codes import JournalCode


class TestLatencyArchitecture(unittest.TestCase):
    def test_council_budget_le_12(self):
        self.assertLessEqual(COUNCIL_BUDGET_SEC, 12.0)
        self.assertGreaterEqual(COUNCIL_BUDGET_SEC, 8.0)
        self.assertLessEqual(CURSOR_VOTE_TIMEOUT_SEC, COUNCIL_BUDGET_SEC)
        self.assertLessEqual(GROK_VOTE_TIMEOUT_SEC, COUNCIL_BUDGET_SEC)
        self.assertLess(CURSOR_SPAWN_TIMEOUT_SEC, CURSOR_VOTE_TIMEOUT_SEC)

    def test_signal_ttl_expired(self):
        d = evaluate_signal_ttl("medium", age_ms=12000, residual_edge=0.01, min_residual=0.03)
        self.assertFalse(d.ok)
        self.assertEqual(d.code, "SIGNAL_EXPIRED")

    def test_signal_ttl_residual_ok_past_hard(self):
        d = evaluate_signal_ttl("medium", age_ms=9000, residual_edge=0.10, min_residual=0.03)
        self.assertTrue(d.ok)
        self.assertEqual(d.code, "RESIDUAL_EDGE_OK")

    def test_signal_decay(self):
        d = evaluate_signal_ttl("medium", age_ms=5000, residual_edge=None, min_residual=0.03)
        self.assertFalse(d.ok)
        self.assertIn(d.code, ("SIGNAL_DECAY", "RESIDUAL_EDGE_GONE"))

    def test_hard_shorter_than_easy(self):
        self.assertLess(SIGNAL_TTL["big"]["hard_ttl_ms"], SIGNAL_TTL["easy"]["hard_ttl_ms"])

    def test_pipeline_trace_metrics(self):
        tr = PipelineTrace("id1", "ETHUSDT", "LONG", "medium")
        t0 = time.time()
        tr.mark("signal_detected", t0)
        tr.mark("council_start", t0 + 0.1)
        tr.mark("council_done", t0 + 1.5)
        tr.mark("revalidation_done", t0 + 1.7)
        tr.mark("order_sent", t0 + 1.8)
        tr.mark("fill_confirmed", t0 + 2.2)
        m = tr.metrics()
        self.assertIsNotNone(m["detect_to_council_ms"])
        self.assertAlmostEqual(m["council_ms"], 1400, delta=50)
        self.assertEqual(tr.latency_bucket(), "0-3s")

    def test_latency_registry(self):
        reg = LatencyRegistry()
        tr = PipelineTrace("a", "X", "LONG", "medium")
        t0 = time.time()
        for st, dt in [("signal_detected", 0), ("council_start", 0.05), ("council_done", 1.0),
                       ("revalidation_done", 1.2), ("order_sent", 1.3), ("order_ack", 1.4), ("fill_confirmed", 1.6)]:
            tr.mark(st, t0 + dt)
        reg.ingest(tr)
        self.assertGreaterEqual(reg.candidate_to_fill.stats()["n"], 1)
        self.assertTrue(any("candidate→fill" in x for x in reg.health_lines()))

    def test_coalesce_dedupe(self):
        c = CandidateCoalescer(window_sec=5, cooldown_sec=10)
        a, start1 = c.upsert(chat_id="1", symbol="BOMEUSDT", side="LONG", mode="medium",
                             price=100, residual_edge=0.1, uspex_score=70, data_quality=80)
        self.assertTrue(start1)
        c.mark_council_start("1", "BOMEUSDT", "LONG")
        b, start2 = c.upsert(chat_id="1", symbol="BOMEUSDT", side="LONG", mode="medium",
                             price=100.1, residual_edge=0.12, uspex_score=72, data_quality=81)
        self.assertFalse(start2)  # same impulse, council inflight / cooldown
        self.assertEqual(a.candidate_id, b.candidate_id)
        self.assertGreaterEqual(b.updates, 2)

    def test_ai_context_cache_ttl(self):
        cache = AiContextCache()
        cache.put_cursor(AiContext("cursor", "ETHUSDT", "LONG", "trend", 70, "none", "x", time.time(), ttl_sec=0.01))
        time.sleep(0.02)
        self.assertIsNone(cache.get_cursor("ETHUSDT"))
        cache.put_grok(AiContext("grok", "GLOBAL", "BOTH", "neutral", 55, "none", "x", time.time(), ttl_sec=60))
        ok, reason = cache.side_allowed("ETHUSDT", "LONG")
        self.assertTrue(ok)

    def test_entry_window_classes(self):
        ran = classify_entry_window(
            side="LONG", first_detect_price=100, best_price_since_detect=100.6, current_price=100.55,
            peak_edge=0.2, residual_edge=0.05, max_chase_bps=30, max_adverse_bps=20, min_residual=0.03,
        )
        self.assertEqual(ran.code, "PRICE_RAN_AWAY")
        pull = classify_entry_window(
            side="LONG", first_detect_price=100, best_price_since_detect=100.4, current_price=100.25,
            peak_edge=0.2, residual_edge=0.08, max_chase_bps=40, max_adverse_bps=25, min_residual=0.03,
        )
        self.assertTrue(pull.ok)
        self.assertEqual(pull.code, "HEALTHY_PULLBACK")

    def test_timeout_not_learning_approve(self):
        allow, gate = council_gate("medium", 99, timeout_vote("Cursor"), timeout_vote("Grok"))
        self.assertFalse(allow)
        self.assertIn("TIMEOUT", gate)

    def test_journal_latency_codes(self):
        for c in ("SIGNAL_EXPIRED", "SIGNAL_DECAY", "COUNCIL_DEADLINE", "RESIDUAL_EDGE_GONE"):
            self.assertTrue(hasattr(JournalCode, c))

    def test_latency_decay_report(self):
        snaps = default_snapshots(100)
        rep = simulate_latency_decay(snaps, "medium")
        self.assertEqual(set(rep.keys()), set(str(x) for x in LATENCY_STEPS_SEC))
        # At 0s almost all should be valid; at 30s many expired/chase
        self.assertGreater(rep["0"]["valid"], rep["30"]["valid"])
        path = ROOT / "fixtures" / "latency_decay_report.json"
        write_latency_decay_report(path, "medium")
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertIn("by_latency_sec", data)

    def test_bucket_helper(self):
        self.assertEqual(latency_bucket_from_ms(2500), "0-3s")
        self.assertEqual(latency_bucket_from_ms(9000), "8-12s")
        self.assertEqual(latency_bucket_from_ms(20000), ">12s")

    def test_main_contains_architecture_hooks(self):
        src = (ROOT / "main_USPEX_PRO_DESK_V12.py").read_text(encoding="utf-8")
        for needle in ("LATENCY_REG", "COALESCER", "AI_CACHE", "ai_context_prewarm_loop",
                       "evaluate_signal_ttl", "classify_entry_window", "PipelineTrace"):
            self.assertIn(needle, src)

    def test_version_v12_2(self):
        from uspex_core.versioning import BUILD_ID, STRATEGY_VERSION, CONFIG_SCHEMA_VERSION
        self.assertIn("V12_2", BUILD_ID)
        self.assertIn("SCANNER_HANGFIX", STRATEGY_VERSION)
        self.assertIn("INSTITUTIONAL", CONFIG_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
