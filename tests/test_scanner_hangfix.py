#!/usr/bin/env python3
"""V12.2.1 scanner hang-fix: net-edge journal, spawn timeout, CancelledError settle."""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from uspex_core.journal_codes import JournalCode
from uspex_core.modes import (
    COUNCIL_BUDGET_SEC,
    CURSOR_SPAWN_TIMEOUT_SEC,
    CURSOR_VOTE_TIMEOUT_SEC,
    COUNCIL_CANCEL_SETTLE_SEC,
)
from uspex_core.net_edge import estimate_net_edge, net_edge_reject_record
from uspex_core.task_guard import settle_cancelled_tasks, spawn_exec_with_timeout, vote_from_task
from uspex_core.versioning import BUILD_ID, STRATEGY_VERSION


class TestJournalAndVersion(unittest.TestCase):
    def test_build_id_patch(self):
        self.assertIn("V12_2_1", BUILD_ID)
        self.assertIn("SCANNER_HANGFIX", BUILD_ID)
        self.assertEqual(STRATEGY_VERSION, "V12_2_1_SCANNER_HANGFIX")

    def test_journal_codes(self):
        self.assertEqual(JournalCode.NET_EDGE_REJECT, "NET_EDGE_REJECT")
        self.assertEqual(JournalCode.COUNCIL_TASK_CANCELLED, "COUNCIL_TASK_CANCELLED")
        self.assertEqual(JournalCode.COUNCIL_TASK_HUNG, "COUNCIL_TASK_HUNG")
        self.assertEqual(JournalCode.CURSOR_CONNECT_TIMEOUT, "CURSOR_CONNECT_TIMEOUT")
        self.assertEqual(JournalCode.CURSOR_READ_TIMEOUT, "CURSOR_READ_TIMEOUT")

    def test_spawn_timeout_separate_from_vote(self):
        self.assertLess(CURSOR_SPAWN_TIMEOUT_SEC, CURSOR_VOTE_TIMEOUT_SEC)
        self.assertLess(CURSOR_SPAWN_TIMEOUT_SEC, COUNCIL_BUDGET_SEC)
        self.assertGreater(CURSOR_SPAWN_TIMEOUT_SEC, 0)
        self.assertGreater(COUNCIL_CANCEL_SETTLE_SEC, 0)

    def test_net_edge_reject_record(self):
        net = estimate_net_edge(
            gross_edge_bps=5.0, spread_bps=3.0, expected_slippage_bps=2.0,
            taker_fee_bps_roundtrip=11.0, min_net_bps=3.0,
        )
        self.assertFalse(net.ok)
        rec = net_edge_reject_record(net, side="LONG", score=80.0, edge_bps=5.0)
        self.assertEqual(rec["reject"], JournalCode.NET_EDGE_REJECT)
        self.assertIn("NET_EDGE_TOO_SMALL", rec["reject_detail"])
        self.assertIn("gross=", rec["reject_detail"])

    def test_main_wires_fixes(self):
        src = (ROOT / "main_USPEX_PRO_DESK_V12.py").read_text(encoding="utf-8")
        self.assertIn("spawn_exec_with_timeout", src)
        self.assertIn("settle_cancelled_tasks", src)
        self.assertIn("vote_from_task", src)
        self.assertIn("NET_EDGE_REJECT", src)
        self.assertIn("CURSOR_CONNECT_TIMEOUT", src)
        self.assertIn("COUNCIL_TASK_CANCELLED", src)
        self.assertNotIn(
            "for p in pending:\n                                        p.cancel()\n                                        try: await p",
            src,
        )


class TestTaskGuard(unittest.IsolatedAsyncioTestCase):
    async def test_settle_cancelled_does_not_raise(self):
        async def sleeper():
            await asyncio.sleep(30)

        task = asyncio.create_task(sleeper())
        await asyncio.sleep(0.01)
        settle = await settle_cancelled_tasks([task], timeout=1.0)
        self.assertGreaterEqual(settle.cancelled + settle.hung, 1)
        # Caller (this test) is still alive.
        self.assertTrue(True)

    async def test_vote_from_cancelled_task(self):
        async def sleeper():
            await asyncio.sleep(30)

        task = asyncio.create_task(sleeper())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        fallback = {"timeout": True, "reason": "fallback"}
        out = vote_from_task(task, fallback)
        self.assertIs(out, fallback)

    async def test_spawn_timeout_kills_slow_start(self):
        # `sleep 30` starts almost instantly as a process; use a wrapper that delays exec.
        # Python -c with a sleep before anything still spawns quickly. Instead mock by
        # pointing at a binary that never returns from exec: /bin/sleep is fast to spawn.
        # We test the timeout path by using timeout=0.05 on a command that we delay with
        # a tiny fifo... simpler: run python -c "import time; time.sleep(5)" — spawn of
        # python is usually <50ms. To force spawn timeout we patch create_subprocess_exec.
        async def never_spawn(*_a, **_k):
            await asyncio.sleep(5)
            raise AssertionError("should not reach")

        orig = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = never_spawn  # type: ignore
        try:
            t0 = time.monotonic()
            proc, err = await spawn_exec_with_timeout(["true"], timeout=0.2)
            elapsed = time.monotonic() - t0
        finally:
            asyncio.create_subprocess_exec = orig  # type: ignore
        self.assertIsNone(proc)
        self.assertEqual(err, "SPAWN_TIMEOUT")
        self.assertLess(elapsed, 2.0)

    async def test_vote_from_finished_task(self):
        async def ok():
            return {"ok": True, "decision": "APPROVE"}

        task = asyncio.create_task(ok())
        await task
        out = vote_from_task(task, {"timeout": True})
        self.assertEqual(out["decision"], "APPROVE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
