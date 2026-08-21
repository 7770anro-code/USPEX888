#!/usr/bin/env python3
"""Unit tests for scripts/uspex_watchdog.py — no systemd, no VPS."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "uspex_watchdog.py"
spec = importlib.util.spec_from_file_location("uspex_watchdog", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["uspex_watchdog"] = mod
spec.loader.exec_module(mod)


def _cfg(td: Path, **kwargs) -> mod.WatchdogConfig:
    kw = dict(
        log_path=td / "watchdog.log",
        state_path=td / "state.json",
        halt_path=td / "halt",
        dry_run=True,
        log_ok=True,
    )
    kw.update(kwargs)
    return mod.WatchdogConfig(**kw)


def _healthy() -> mod.Facts:
    return mod.Facts(
        load_state="loaded",
        active_state="active",
        sub_state="running",
        result="success",
        nrestarts=0,
        main_pid=1234,
        pid_alive=True,
        pid_zombie=False,
        cmdline_ok=True,
        journal_age_sec=30.0,
        running_sec=600.0,
    )


class TestDecide(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.cfg = _cfg(Path(self.td.name))
        self.state = mod.State()
        self.now = 1_000_000.0

    def test_healthy_is_ok(self):
        d = mod.decide(_healthy(), self.state, self.cfg, self.now)
        self.assertEqual(d.action, "ok")

    def test_failed_unit_restarts(self):
        f = _healthy()
        f.active_state = "failed"
        f.sub_state = "failed"
        f.pid_alive = False
        f.main_pid = 0
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "restart")
        self.assertIn("not_active", d.reason)

    def test_inactive_restarts(self):
        f = _healthy()
        f.active_state = "inactive"
        f.sub_state = "dead"
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "restart")

    def test_restart_loop_while_activating(self):
        f = _healthy()
        f.active_state = "activating"
        f.sub_state = "auto-restart"
        f.nrestarts = 8
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "restart")
        self.assertIn("restart_loop", d.reason)

    def test_brief_activating_waits(self):
        f = _healthy()
        f.active_state = "activating"
        f.sub_state = "start"
        f.nrestarts = 0
        f.running_sec = 5.0
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "wait")

    def test_pid_missing_restarts(self):
        f = _healthy()
        f.pid_alive = False
        f.main_pid = 999
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "restart")
        self.assertEqual(d.reason, "pid_missing")

    def test_zombie_restarts(self):
        f = _healthy()
        f.pid_zombie = True
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "restart")
        self.assertEqual(d.reason, "pid_zombie")

    def test_journal_silent_restarts_existing_build(self):
        f = _healthy()
        f.journal_age_sec = 800.0
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "restart")
        self.assertEqual(d.reason, "journal_silent")

    def test_journal_silent_grace_after_start(self):
        f = _healthy()
        f.journal_age_sec = 800.0
        f.running_sec = 10.0
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "ok")

    def test_halt_file_skips(self):
        self.cfg.halt_path.write_text("halted\n")
        d = mod.decide(_healthy(), self.state, self.cfg, self.now)
        self.assertEqual(d.action, "skip_halt")

    def test_circuit_breaker_halts_instead_of_writing_code(self):
        f = _healthy()
        f.active_state = "failed"
        f.sub_state = "failed"
        self.state.restarts = [
            {"ts": self.now - 10, "reason": "not_active"},
            {"ts": self.now - 20, "reason": "not_active"},
            {"ts": self.now - 30, "reason": "not_active"},
        ]
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "halt")
        self.assertEqual(d.reason, "repeated_unhealthy_needs_human")

    def test_old_restarts_outside_window_do_not_halt(self):
        f = _healthy()
        f.active_state = "failed"
        f.sub_state = "failed"
        self.state.restarts = [{"ts": self.now - 10_000, "reason": "not_active"}] * 5
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "restart")

    def test_missing_unit_needs_human_not_install(self):
        f = _healthy()
        f.load_state = "not-found"
        d = mod.decide(f, self.state, self.cfg, self.now)
        self.assertEqual(d.action, "needs_human")
        self.assertEqual(d.reason, "unit_missing")


class TestRunOnceAndSafety(unittest.TestCase):
    def test_run_once_restart_dry_run_logs_and_does_not_call_forbidden_units(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(Path(td), dry_run=True)
            facts = _healthy()
            facts.active_state = "failed"
            facts.sub_state = "failed"
            called = []

            def fake_restart(c):
                called.append(c.unit)
                self.assertEqual(c.unit, "uspex.service")

            orig = mod.restart_uspex
            mod.restart_uspex = fake_restart
            try:
                d = mod.run_once(cfg, facts=facts, now=1_000_000.0)
            finally:
                mod.restart_uspex = orig
            self.assertEqual(d.action, "restart")
            self.assertEqual(called, ["uspex.service"])
            log = cfg.log_path.read_text()
            self.assertIn("action=restart", log)
            self.assertIn("reason=not_active:failed/failed", log)
            state = mod.load_state(cfg.state_path)
            self.assertEqual(len(state.restarts), 1)

    def test_halt_writes_marker_not_code(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _cfg(Path(td), dry_run=True)
            facts = _healthy()
            facts.active_state = "failed"
            facts.sub_state = "failed"
            cfg.state_path.write_text(
                '{"restarts":[{"ts":999990,"reason":"x"},{"ts":999992,"reason":"x"},'
                '{"ts":999994,"reason":"x"}],"halted_at":null}'
            )
            d = mod.run_once(cfg, facts=facts, now=1_000_000.0)
            self.assertEqual(d.action, "halt")
            self.assertTrue(cfg.halt_path.exists())
            halt_txt = cfg.halt_path.read_text()
            self.assertIn("HALTED", halt_txt)
            self.assertNotIn("/opt/uspex/main.py", halt_txt)

    def test_restart_uspex_rejects_other_units(self):
        cfg = mod.WatchdogConfig(unit="vector.service", dry_run=False)
        with self.assertRaises(mod.ForbiddenAction):
            mod.restart_uspex(cfg)

    def test_systemctl_show_rejects_vector(self):
        with self.assertRaises(mod.ForbiddenAction):
            mod.systemctl_show("vector.service", ["ActiveState"])

    def test_source_has_no_deploy_primitives(self):
        src = SCRIPT.read_text()
        # Ignore the module docstring (it names forbidden tools).
        code = src.split('"""', 2)[-1]
        for needle in ("git pull", "git clone", "rsync", "pip install", "paper_v8.sqlite3"):
            self.assertNotIn(needle, code)
        self.assertIn('FORBIDDEN_UNITS = ("vector.service",)', code)
        self.assertIn('["systemctl", "restart", ALLOWED_UNIT]', code)


class TestFormatLog(unittest.TestCase):
    def test_machine_parseable(self):
        facts = _healthy()
        d = mod.Decision("restart", "pid_missing", "pid=0")
        line = mod.format_log(1_000_000.0, d, facts, False)
        self.assertIn("action=restart", line)
        self.assertIn("reason=pid_missing", line)
        self.assertTrue(line.startswith("1970-01-12T13:46:40Z") or "T" in line[:30])


if __name__ == "__main__":
    unittest.main(verbosity=2)
