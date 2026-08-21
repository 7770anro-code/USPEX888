#!/usr/bin/env python3
"""Claim an inbox task: write outbox CLAIM + set status=claimed in inbox file."""
from __future__ import annotations

import argparse
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INBOX = ROOT / "ai_bridge" / "inbox"
OUTBOX = ROOT / "ai_bridge" / "outbox"
FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id", help="TASK-... id or inbox filename stem")
    ap.add_argument("--agent", default="cursor")
    args = ap.parse_args()

    stem = args.task_id.removesuffix(".md")
    candidates = list(INBOX.glob(f"{stem}.md"))
    if not candidates:
        candidates = list(INBOX.glob(f"{stem}*.md"))
    if not candidates:
        raise SystemExit(f"inbox task not found: {args.task_id}")
    path = candidates[0]
    text = path.read_text(encoding="utf-8")
    m = FRONT.match(text)
    if not m:
        raise SystemExit("missing YAML frontmatter")
    front, body = m.group(1), m.group(2)
    lines = []
    tid = stem
    for line in front.splitlines():
        if line.startswith("id:"):
            tid = line.split(":", 1)[1].strip() or tid
        if line.startswith("status:"):
            lines.append("status: claimed")
        else:
            lines.append(line)
    if not any(l.startswith("status:") for l in lines):
        lines.append("status: claimed")
    path.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")

    OUTBOX.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    claim = OUTBOX / f"{tid}-CLAIM.md"
    claim.write_text(
        f"""---
id: {tid}
from: {args.agent}
to: human
status: claimed
host: {socket.gethostname()}
claimed_utc: {now}
---

# CLAIMED: {tid}

Cursor/agent claimed this task. Work in progress.
""",
        encoding="utf-8",
    )
    print(f"CLAIMED {tid}")
    print(f"  inbox={path.relative_to(ROOT)}")
    print(f"  outbox={claim.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
