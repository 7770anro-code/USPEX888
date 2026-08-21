#!/usr/bin/env python3
"""USPEX systemd watchdog — revive the already-deployed uspex.service only.

ALLOWED (no extra confirmation):
  systemctl restart uspex.service when the unit is down, crash-looping,
  or the process is gone / not producing logs.

FORBIDDEN (log + halt, never auto-fix with new code):
  git / rsync / pip / editing /opt/uspex/*.py / uspex_core / .env / sqlite,
  touching /opt/vector, vector.service, or the uspex.service unit file.

Install (only after an explicit human OK): copy this file to
/usr/local/sbin/uspex-watchdog and enable uspex-watchdog.timer.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ALLOWED_UNIT = "uspex.service"
FORBIDDEN_UNITS = ("vector.service",)
EXPECTED_CMDLINE_NEEDLES = (b"/opt/uspex/venv/bin/python", b"/opt/uspex/main.py")

DEFAULT_LOG = Path("/var/log/uspex-watchdog.log")
DEFAULT_STATE = Path("/var/lib/uspex-watchdog/state.json")
DEFAULT_HALT = Path("/var/lib/uspex-watchdog/halt")

JOURNAL_SILENCE_SEC = 720  # 12 min — healthy scanner currently logs at least every ~6 min
GRACE_AFTER_START_SEC = 90
RESTART_LOOP_N = 5
MAX_RESTARTS_PER_WINDOW = 3
WINDOW_SEC = 1800  # 30 min
ACTIVATING_WAIT_SEC = 60


class ForbiddenAction(RuntimeError):
    """Raised if the watchdog is about to do anything other than restart uspex."""


@dataclass
class WatchdogConfig:
    unit: str = ALLOWED_UNIT
    log_path: Path = DEFAULT_LOG
    state_path: Path = DEFAULT_STATE
    halt_path: Path = DEFAULT_HALT
    journal_silence_sec: int = JOURNAL_SILENCE_SEC
    grace_after_start_sec: int = GRACE_AFTER_START_SEC
    restart_loop_n: int = RESTART_LOOP_N
    max_restarts_per_window: int = MAX_RESTARTS_PER_WINDOW
    window_sec: int = WINDOW_SEC
    dry_run: bool = False
    log_ok: bool = False


@dataclass
class Facts:
    load_state: str = ""
    active_state: str = ""
    sub_state: str = ""
    result: str = ""
    nrestarts: int = 0
    main_pid: int = 0
    pid_alive: bool = False
    pid_zombie: bool = False
    cmdline_ok: bool = False
    journal_age_sec: Optional[float] = None
    running_sec: Optional[float] = None


@dataclass
class Decision:
    action: str  # ok | restart | wait | skip_halt | halt | needs_human
    reason: str
    detail: str = ""


@dataclass
class State:
    restarts: List[Dict[str, Any]] = field(default_factory=list)
    halted_at: Optional[str] = None
    halted_reason: str = ""

    def restarts_in_window(self, now: float, window_sec: int) -> int:
        cutoff = now - window_sec
        return sum(1 for r in self.restarts if float(r.get("ts", 0)) >= cutoff)


def _run(argv: Sequence[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    if not argv:
        raise ForbiddenAction("empty command")
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def systemctl_show(unit: str, keys: Sequence[str]) -> Dict[str, str]:
    if unit != ALLOWED_UNIT:
        raise ForbiddenAction(f"refusing to inspect unit {unit}")
    args = ["systemctl", "show", unit, "--no-pager"]
    for k in keys:
        args.append(f"-p{k}")
    proc = _run(args)
    out: Dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def _parse_pid(raw: str) -> int:
    try:
        return int(raw or "0")
    except ValueError:
        return 0


def _usec_to_age_sec(raw: str, now: float) -> Optional[float]:
    raw = (raw or "").strip()
    if not raw or raw == "0":
        return None
    try:
        usec = int(raw)
    except ValueError:
        return None
    started = usec / 1_000_000.0
    if started <= 0:
        return None
    return max(0.0, now - started)


def inspect_pid(pid: int) -> tuple[bool, bool, bool]:
    """Return (alive, zombie, cmdline_ok)."""
    if pid <= 0:
        return False, False, False
    proc = Path(f"/proc/{pid}")
    if not proc.is_dir():
        return False, False, False
    zombie = False
    try:
        status = (proc / "status").read_text(errors="replace")
        for line in status.splitlines():
            if line.startswith("State:"):
                zombie = "\tZ" in line or line.split()[1:2] == ["Z"]
                break
    except OSError:
        return False, False, False
    cmdline_ok = False
    try:
        cmdline = (proc / "cmdline").read_bytes()
        cmdline_ok = all(needle in cmdline for needle in EXPECTED_CMDLINE_NEEDLES)
    except OSError:
        cmdline_ok = False
    return True, zombie, cmdline_ok


def journal_age_sec(unit: str, now: float) -> Optional[float]:
    if unit != ALLOWED_UNIT:
        raise ForbiddenAction(f"refusing journal of {unit}")
    proc = _run(
        [
            "journalctl",
            "-u",
            unit,
            "-n",
            "1",
            "-o",
            "json",
            "--no-pager",
            "-q",
        ]
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw.splitlines()[-1])
        ts = int(payload.get("__REALTIME_TIMESTAMP") or 0)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if ts <= 0:
        return None
    return max(0.0, now - (ts / 1_000_000.0))


def collect_facts(cfg: WatchdogConfig, now: Optional[float] = None) -> Facts:
    now = time.time() if now is None else now
    show = systemctl_show(
        cfg.unit,
        (
            "LoadState",
            "ActiveState",
            "SubState",
            "Result",
            "NRestarts",
            "MainPID",
            "ActiveEnterTimestampUSec",
            "ExecMainStartTimestampUSec",
        ),
    )
    pid = _parse_pid(show.get("MainPID", "0"))
    alive, zombie, cmdline_ok = inspect_pid(pid)
    start_age = _usec_to_age_sec(
        show.get("ExecMainStartTimestampUSec") or show.get("ActiveEnterTimestampUSec", ""),
        now,
    )
    jage: Optional[float] = None
    try:
        jage = journal_age_sec(cfg.unit, now)
    except Exception:
        jage = None
    try:
        nrestarts = int(show.get("NRestarts") or 0)
    except ValueError:
        nrestarts = 0
    return Facts(
        load_state=show.get("LoadState", ""),
        active_state=show.get("ActiveState", ""),
        sub_state=show.get("SubState", ""),
        result=show.get("Result", ""),
        nrestarts=nrestarts,
        main_pid=pid,
        pid_alive=alive,
        pid_zombie=zombie,
        cmdline_ok=cmdline_ok,
        journal_age_sec=jage,
        running_sec=start_age,
    )


def decide(facts: Facts, state: State, cfg: WatchdogConfig, now: float) -> Decision:
    if cfg.halt_path.exists() or state.halted_at:
        return Decision("skip_halt", "halt_file_present", state.halted_reason or "auto-restart disabled")

    if facts.load_state in {"not-found", "masked"}:
        return Decision(
            "needs_human",
            "unit_missing",
            "uspex.service is missing/masked — watchdog will not install unit files",
        )

    reasons: List[str] = []

    if facts.active_state == "activating":
        if facts.nrestarts >= cfg.restart_loop_n or facts.sub_state == "auto-restart":
            reasons.append("restart_loop")
        elif (facts.running_sec or 0) > ACTIVATING_WAIT_SEC:
            reasons.append(f"activating_too_long:{facts.sub_state}")
        else:
            return Decision("wait", "activating", facts.sub_state)

    elif facts.active_state != "active":
        reasons.append(f"not_active:{facts.active_state}/{facts.sub_state}")
        if facts.nrestarts >= cfg.restart_loop_n:
            reasons.append("restart_loop")

    else:
        if facts.main_pid <= 0 or not facts.pid_alive:
            reasons.append("pid_missing")
        elif facts.pid_zombie:
            reasons.append("pid_zombie")
        elif not facts.cmdline_ok:
            reasons.append("unexpected_cmdline")
        elif (
            facts.journal_age_sec is not None
            and facts.journal_age_sec > cfg.journal_silence_sec
            and (facts.running_sec or 0) > cfg.grace_after_start_sec
        ):
            reasons.append("journal_silent")

    if not reasons:
        return Decision("ok", "healthy", f"pid={facts.main_pid} state={facts.active_state}/{facts.sub_state}")

    reason = ",".join(reasons)
    if state.restarts_in_window(now, cfg.window_sec) >= cfg.max_restarts_per_window:
        return Decision(
            "halt",
            "repeated_unhealthy_needs_human",
            f"already {cfg.max_restarts_per_window} watchdog restarts in {cfg.window_sec}s; will not patch code. last={reason}",
        )
    return Decision("restart", reason, f"nrestarts={facts.nrestarts} pid={facts.main_pid}")


def restart_uspex(cfg: WatchdogConfig) -> None:
    if cfg.unit != ALLOWED_UNIT:
        raise ForbiddenAction(f"refusing to restart {cfg.unit}")
    if cfg.dry_run:
        return
    proc = _run(["systemctl", "restart", ALLOWED_UNIT], timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"systemctl restart {ALLOWED_UNIT} failed rc={proc.returncode} "
            f"stderr={(proc.stderr or '')[:400]}"
        )


def load_state(path: Path) -> State:
    if not path.exists():
        return State()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return State()
    return State(
        restarts=list(raw.get("restarts") or []),
        halted_at=raw.get("halted_at"),
        halted_reason=raw.get("halted_reason") or "",
    )


def save_state(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = {
        "restarts": state.restarts[-50:],
        "halted_at": state.halted_at,
        "halted_reason": state.halted_reason,
    }
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)


def append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


def format_log(now_ts: float, decision: Decision, facts: Facts, dry_run: bool) -> str:
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
    jage = "" if facts.journal_age_sec is None else f"{facts.journal_age_sec:.0f}"
    return (
        f"{iso} action={decision.action} reason={decision.reason} "
        f"active_state={facts.active_state} sub_state={facts.sub_state} "
        f"main_pid={facts.main_pid} nrestarts={facts.nrestarts} "
        f"journal_age_sec={jage} dry_run={int(dry_run)} detail={decision.detail}"
    )


def write_halt(cfg: WatchdogConfig, now_ts: float, reason: str) -> None:
    cfg.halt_path.parent.mkdir(parents=True, exist_ok=True)
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
    cfg.halt_path.write_text(
        f"{iso} {reason}\n"
        "Watchdog auto-restart HALTED after repeated failures.\n"
        "This is NOT a code deploy. Inspect logs, then:\n"
        f"  sudo rm {cfg.halt_path}\n"
        "to re-enable auto-restarts of the already-installed uspex.service.\n"
    )


def run_once(cfg: WatchdogConfig, facts: Optional[Facts] = None, now: Optional[float] = None) -> Decision:
    now = time.time() if now is None else now
    state = load_state(cfg.state_path)
    facts = collect_facts(cfg, now=now) if facts is None else facts
    decision = decide(facts, state, cfg, now)

    if decision.action == "ok" and not cfg.log_ok:
        return decision

    append_log(cfg.log_path, format_log(now, decision, facts, cfg.dry_run))

    if decision.action == "restart":
        restart_uspex(cfg)
        state.restarts.append({"ts": now, "reason": decision.reason, "dry_run": cfg.dry_run})
        save_state(cfg.state_path, state)
    elif decision.action == "halt":
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        state.halted_at = iso
        state.halted_reason = decision.detail or decision.reason
        save_state(cfg.state_path, state)
        write_halt(cfg, now, decision.reason + " " + decision.detail)
    return decision


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Restart-only watchdog for uspex.service")
    p.add_argument("--dry-run", action="store_true", help="decide and log, do not systemctl restart")
    p.add_argument("--log-ok", action="store_true", help="also log healthy ticks")
    p.add_argument("--log", type=Path, default=DEFAULT_LOG)
    p.add_argument("--state", type=Path, default=DEFAULT_STATE)
    p.add_argument("--halt", type=Path, default=DEFAULT_HALT)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    cfg = WatchdogConfig(
        log_path=args.log,
        state_path=args.state,
        halt_path=args.halt,
        dry_run=args.dry_run,
        log_ok=args.log_ok,
    )
    try:
        decision = run_once(cfg)
    except ForbiddenAction as exc:
        sys.stderr.write(f"FORBIDDEN: {exc}\n")
        return 2
    except Exception as exc:  # noqa: BLE001 — oneshot must not crash the timer
        append_log(
            cfg.log_path,
            f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} action=error reason=watchdog_exception detail={exc!s}"[:500],
        )
        return 1
    if decision.action in {"needs_human", "halt"}:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
