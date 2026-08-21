#!/usr/bin/env python3
"""Optional VPS helper: list pending inbox tasks after git pull.

Intended for /home/cloud/… clone — NEVER for /opt/uspex.
Does not call LLM APIs. Does not print env secrets.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    if Path("/opt/uspex") in ROOT.parents or ROOT == Path("/opt/uspex"):
        print("REFUSING to run inside /opt/uspex", file=sys.stderr)
        return 2
    print(f"REPO={ROOT}")
    subprocess.run(["git", "status", "-sb"], cwd=ROOT, check=False)
    return subprocess.call([sys.executable, str(ROOT / "ai_bridge" / "scripts" / "list_pending.py")], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
