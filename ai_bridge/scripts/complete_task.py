#!/usr/bin/env python3
"""Complete a task: write outbox DONE and mark inbox status=done."""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INBOX = ROOT / "ai_bridge" / "inbox"
OUTBOX = ROOT / "ai_bridge" / "outbox"
FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--pr", default="")
    ap.add_argument("--commit", default="")
    ap.add_argument("--agent", default="cursor")
    ap.add_argument("--status", choices=("done", "failed", "blocked"), default="done")
    args = ap.parse_args()

    stem = args.task_id.removesuffix(".md")
    candidates = list(INBOX.glob(f"{stem}.md")) or list(INBOX.glob(f"{stem}*.md"))
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
            lines.append(f"status: {args.status}")
        else:
            lines.append(line)
    if not any(l.startswith("status:") for l in lines):
        lines.append(f"status: {args.status}")
    path.write_text("---\n" + "\n".join(lines) + "\n---\n" + body, encoding="utf-8")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = OUTBOX / f"{tid}-DONE.md"
    out.write_text(
        f"""---
id: {tid}
from: {args.agent}
to: human
target: uspex
status: {args.status}
pr: {args.pr}
commit: {args.commit}
finished_utc: {now}
---

# DONE: {tid}

## What changed
{args.summary}

## How to verify
See PR/commit above if provided. No deploy unless separately approved.

## Open questions
(none)
""",
        encoding="utf-8",
    )
    print(f"COMPLETE {tid} status={args.status}")
    print(f"  outbox={out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
