#!/usr/bin/env python3
"""Validate TASK markdown frontmatter. Exit 0=ok, 1=errors. No network/secrets."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REQUIRED = {"id", "from", "to", "target", "status"}
TARGETS = {"uspex", "vector"}
STATUSES = {"inbox", "claimed", "wip", "done", "blocked", "failed"}
FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse(path: Path) -> tuple[dict, list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    errs = []
    m = FRONT.match(text)
    if not m:
        return {}, [f"{path.name}: missing YAML frontmatter"]
    data = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip()] = v.strip()
    missing = REQUIRED - set(data)
    if missing:
        errs.append(f"{path.name}: missing keys {sorted(missing)}")
    if data.get("target") not in TARGETS:
        errs.append(f"{path.name}: target must be uspex|vector")
    if data.get("status") not in STATUSES:
        errs.append(f"{path.name}: bad status {data.get('status')}")
    needs = (data.get("needs_browser") or "false").strip().lower()
    if needs in ("1", "true", "yes", "on"):
        raw_steps = (data.get("browser_steps_json") or "").strip()
        if raw_steps and raw_steps not in ("[]", '""', "''"):
            try:
                steps = json.loads(raw_steps)
                if not isinstance(steps, list):
                    errs.append(f"{path.name}: browser_steps_json must be a JSON array")
            except json.JSONDecodeError:
                errs.append(f"{path.name}: browser_steps_json is not valid JSON")
        goal = (data.get("browser_goal") or "").strip().strip('"').strip("'")
        if not raw_steps and not goal:
            errs.append(f"{path.name}: needs_browser requires browser_goal or browser_steps_json")
    if "SECRET" in text.upper() and re.search(r"(api[_-]?key|sk-|xai-|token\s*=)", text, re.I):
        # soft warning style hard fail if looks like a key assignment
        if re.search(r"(sk-[A-Za-z0-9_-]{20,}|xai-[A-Za-z0-9_-]{20,})", text):
            errs.append(f"{path.name}: possible secret material — remove before commit")
    return data, errs


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[2]
    paths = [Path(a) for a in argv[1:]]
    if not paths:
        paths = sorted((root / "ai_bridge" / "inbox").glob("TASK-*.md"))
    all_errs = []
    for path in paths:
        _, errs = parse(path)
        all_errs.extend(errs)
        if not errs:
            print(f"OK {path}")
        else:
            for e in errs:
                print(f"ERR {e}", file=sys.stderr)
    return 1 if all_errs else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
