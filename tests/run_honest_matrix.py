#!/usr/bin/env python3
"""Honest test matrix runner for USPEX V12.1 master TZ.
Prints command, exit code, PASS/FAIL/NOT RUN for each required group.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list, name: str) -> dict:
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    status = "PASS" if p.returncode == 0 else "FAIL"
    return {
        "name": name,
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "status": status,
        "tail": (p.stdout + p.stderr)[-800:],
    }


def main():
    rows = []
    rows.append(run([sys.executable, "-m", "py_compile", "main_USPEX_PRO_DESK_V12.py"], "01_python_compile_main"))
    rows.append(run([sys.executable, "-m", "py_compile"] + [str(p) for p in (ROOT / "uspex_core").glob("*.py")], "01b_python_compile_core"))
    rows.append(run([sys.executable, "tests/test_uspex_v12_suite.py"], "30_core_suite_unit"))
    rows.append(run([sys.executable, "tests/test_latency_architecture.py"], "38_54_latency_ttl_layerAB"))
    rows.append(run([sys.executable, "tests/replay_harness.py", "3000"], "31_replay_harness_3000"))
    # latency decay is a sensitivity test, not PnL proof
    rows.append(run([sys.executable, "-c",
                     "from pathlib import Path; from uspex_core.latency_decay import write_latency_decay_report; "
                     "import json; r=write_latency_decay_report(Path('fixtures/latency_decay_report.json')); "
                     "print(json.dumps({k:r[k] for k in ['0','3','8','30']}, indent=2)); "
                     "assert r['0']['valid']>r['30']['valid']"],
                    "49_latency_decay_sensitivity"))

    # Explicit NOT RUN for live DEMO integration (no secrets / no deploy)
    rows.append({
        "name": "DEMO_INTEGRATION_live_bybit_shadow",
        "command": "NOT EXECUTED — requires live Bybit Demo + user deploy confirmation",
        "exit_code": None,
        "status": "NOT RUN",
        "tail": "Would measure candidate→fill p50/p95 on DEMO; blocked by no-auto-deploy policy.",
    })
    rows.append({
        "name": "LIVE_COUNCIL_p95_measurement",
        "command": "NOT EXECUTED — needs Cursor CLI + Grok API on running bot",
        "exit_code": None,
        "status": "NOT RUN",
        "tail": "Unit tests cover timeout fail-closed + budget constants; live p95 unknown.",
    })

    out = {"results": rows, "pass": sum(1 for r in rows if r["status"] == "PASS"),
           "fail": sum(1 for r in rows if r["status"] == "FAIL"),
           "not_run": sum(1 for r in rows if r["status"] == "NOT RUN")}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    # fail process if any FAIL
    sys.exit(1 if out["fail"] else 0)


if __name__ == "__main__":
    main()
