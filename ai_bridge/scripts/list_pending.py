#!/usr/bin/env python3
"""List pending inbox tasks (status=inbox or missing claim)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INBOX = ROOT / "ai_bridge" / "inbox"
OUTBOX = ROOT / "ai_bridge" / "outbox"

FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def parse_front(text: str) -> dict:
    m = FRONT.match(text)
    if not m:
        return {}
    data = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip()] = v.strip()
    return data


def main() -> int:
    rows = []
    for path in sorted(INBOX.glob("TASK-*.md")):
        if path.name.startswith("TASK-") and path.name.endswith(".md"):
            meta = parse_front(path.read_text(encoding="utf-8", errors="replace"))
            status = meta.get("status", "inbox")
            tid = meta.get("id") or path.stem
            done = list(OUTBOX.glob(f"{tid}-DONE.md")) or list(OUTBOX.glob(f"{path.stem}-DONE.md"))
            claim = list(OUTBOX.glob(f"{tid}-CLAIM.md")) or list(OUTBOX.glob(f"{path.stem}-CLAIM.md"))
            if done:
                continue
            rows.append((tid, status, meta.get("target", "?"), meta.get("priority", "?"), "claimed" if claim else "free", path.name))
    if not rows:
        print("NO_PENDING")
        return 0
    print(f"{'ID':<42} {'status':<8} {'target':<8} {'pri':<6} {'lock':<8} file")
    for r in rows:
        print(f"{r[0]:<42} {r[1]:<8} {r[2]:<8} {r[3]:<6} {r[4]:<8} {r[5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
