#!/usr/bin/env python3
"""Create a new ai_bridge inbox TASK markdown file (no network, no secrets)."""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INBOX = ROOT / "ai_bridge" / "inbox"
TEMPLATE = ROOT / "ai_bridge" / "templates" / "TASK_TEMPLATE.md"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (s[:48] or "task")


def main() -> int:
    ap = argparse.ArgumentParser(description="Create ai_bridge inbox task")
    ap.add_argument("--title", required=True)
    ap.add_argument("--goal", required=True)
    ap.add_argument("--target", choices=("uspex", "vector"), default="uspex")
    ap.add_argument("--from", dest="from_agent", default="human")
    ap.add_argument("--priority", choices=("normal", "high"), default="normal")
    ap.add_argument("--proposal", default="(to be filled by Cloud/GPT)")
    ap.add_argument("--needs-browser", action="store_true")
    ap.add_argument("--browser-goal", default="")
    ap.add_argument("--browser-steps-json", default="[]")
    args = ap.parse_args()

    INBOX.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    tid = f"TASK-{now.strftime('%Y%m%d-%H%M')}-{slugify(args.title)}"
    path = INBOX / f"{tid}.md"
    if path.exists():
        raise SystemExit(f"already exists: {path}")

    needs = "true" if args.needs_browser else "false"
    body = f"""---
id: {tid}
from: {args.from_agent}
to: cursor
target: {args.target}
status: inbox
priority: {args.priority}
created_utc: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}
needs_browser: {needs}
browser_goal: {args.browser_goal!r}
browser_steps_json: {args.browser_steps_json}
---

# {args.title}

## Goal
{args.goal}

## Context
(add links / files / symptoms)

## Proposed solution
{args.proposal}

## Acceptance criteria
- [ ] Implemented and verified locally
- [ ] No secrets in diff
- [ ] DEMO/Shadow rules respected if trading-related

## Out of scope
REAL trading, `/opt/uspex` changes without explicit deploy OK.

## Notes for Cursor
API keys only via env. Prefer branch `cursor/<slug>-bf57`. Do not push main/deploy unless asked.
If needs_browser: run `python3 ai_bridge/scripts/run_browser_task.py` with KERNEL_API_KEY from secrets.
"""
    # Fix browser_goal quoting in frontmatter — use plain string without repr quotes issues
    body = body.replace(f"browser_goal: {args.browser_goal!r}", f'browser_goal: "{args.browser_goal}"')
    path.write_text(body, encoding="utf-8")
    print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
