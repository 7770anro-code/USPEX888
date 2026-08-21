#!/usr/bin/env python3
"""V3 unit tests: fair value, net edge, venue adapters, budget bounds."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uspex_core.fair_value import VenueQuote, robust_fair_value
from uspex_core.net_edge import estimate_net_edge, executable_price
from uspex_core.venues import BybitPublicAdapter, BinanceFuturesAdapter, OkxAdapter, MarketSnapshot
from uspex_core.modes import COUNCIL_BUDGET_SEC, CURSOR_VOTE_TIMEOUT_SEC, GROK_VOTE_TIMEOUT_SEC, CURSOR_SPAWN_TIMEOUT_SEC
from uspex_core.versioning import BUILD_ID, CONFIG_SCHEMA_VERSION


class TestV3Institutional(unittest.TestCase):
    def test_build_id(self):
        self.assertIn("V12_2", BUILD_ID)
        self.assertTrue(CONFIG_SCHEMA_VERSION)

    def test_budget_12(self):
        self.assertLessEqual(COUNCIL_BUDGET_SEC, 12.0)
        self.assertGreaterEqual(COUNCIL_BUDGET_SEC, 8.0)
        self.assertLessEqual(CURSOR_VOTE_TIMEOUT_SEC, COUNCIL_BUDGET_SEC)
        self.assertLessEqual(GROK_VOTE_TIMEOUT_SEC, COUNCIL_BUDGET_SEC)
        self.assertLess(CURSOR_SPAWN_TIMEOUT_SEC, CURSOR_VOTE_TIMEOUT_SEC)

    def test_outlier_quarantine(self):
        qs = [
            VenueQuote("binance", 100.0, 50, spread_bps=2, liquidity=2),
            VenueQuote("bybit", 100.1, 80, spread_bps=3, liquidity=2),
            VenueQuote("okx", 130.0, 60, spread_bps=2, liquidity=1),  # broken
        ]
        fv = robust_fair_value(qs, executable_bybit=100.05, side="LONG")
        self.assertIsNotNone(fv.fair)
        self.assertLess(abs(fv.fair - 100.05), 1.0)
        self.assertIn("okx", fv.outlier_venues)

    def test_dirty_book_zero_weight(self):
        qs = [
            VenueQuote("binance", 100.0, 50, book_dirty=True),
            VenueQuote("bybit", 100.2, 40, liquidity=2),
        ]
        fv = robust_fair_value(qs)
        self.assertEqual(fv.n_venues, 1)
        self.assertAlmostEqual(fv.fair, 100.2, places=4)

    def test_executable_price_sides(self):
        self.assertEqual(executable_price(99.0, 101.0, "LONG"), 101.0)
        self.assertEqual(executable_price(99.0, 101.0, "SHORT"), 99.0)

    def test_net_edge_too_small(self):
        r = estimate_net_edge(gross_edge_bps=5.0, spread_bps=3.0, expected_slippage_bps=2.0,
                              taker_fee_bps_roundtrip=11.0, min_net_bps=3.0)
        self.assertFalse(r.ok)

    def test_net_edge_ok(self):
        r = estimate_net_edge(gross_edge_bps=30.0, spread_bps=2.0, expected_slippage_bps=2.0,
                              taker_fee_bps_roundtrip=11.0, uncertainty_bps=2.0, min_net_bps=3.0)
        self.assertTrue(r.ok)

    def test_okx_seq_gap_marks_dirty(self):
        ad = OkxAdapter()
        snap = MarketSnapshot(venue="okx", canonical_symbol="BTCUSDT", native_symbol="BTC-USDT-SWAP",
                              sequence_id=10)
        ad.parse_book_update({"data": [{"bids": [["1", "1"]], "asks": [["2", "1"]], "seqId": 20, "prevSeqId": 11}]}, snap)
        self.assertTrue(snap.book_dirty)
        self.assertFalse(snap.sequence_ok)

    def test_bybit_adapter_mid(self):
        ad = BybitPublicAdapter()
        snap = MarketSnapshot(venue="bybit", canonical_symbol="ETHUSDT", native_symbol="ETHUSDT")
        ad.parse_book_update({"data": {"b": [["100", "1"]], "a": [["102", "1"]], "u": 1}}, snap)
        self.assertEqual(snap.mid, 101.0)
        self.assertGreater(snap.spread_bps, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
