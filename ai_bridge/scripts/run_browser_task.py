#!/usr/bin/env python3
"""Run a browser-needed AI Bridge task via Kernel cloud browser.

Reads TASK markdown frontmatter:
  needs_browser: true|yes|1
  browser_goal: short description
  browser_steps_json: optional JSON list of {action, ...}

Actions supported in browser_steps_json:
  - {"action":"goto","url":"https://..."}
  - {"action":"click","selector":"..."}
  - {"action":"fill","selector":"...","text":"..."}
  - {"action":"wait","ms":1000}
  - {"action":"eval","code":"await page.title()"}   # raw Playwright snippet (return value OK)
  - {"action":"screenshot_note","note":"..."}        # no file upload; records note only

Writes a JSON report to stdout and optionally --out path (no secrets).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ai_bridge.kernel_browser import (  # noqa: E402
    KernelBrowserError,
    create_browser,
    key_is_configured,
)

FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def parse_front(text: str) -> dict:
    m = FRONT.match(text)
    if not m:
        return {}
    data = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def steps_to_playwright(steps: list[dict]) -> str:
    lines = ["const notes = [];"]
    for i, step in enumerate(steps):
        action = str(step.get("action") or "").lower()
        if action == "goto":
            url = json.dumps(str(step.get("url") or ""))
            lines.append(f"await page.goto({url}, {{ waitUntil: 'domcontentloaded' }});")
        elif action == "click":
            sel = json.dumps(str(step.get("selector") or ""))
            lines.append(f"await page.click({sel});")
        elif action == "fill":
            sel = json.dumps(str(step.get("selector") or ""))
            text = json.dumps(str(step.get("text") or ""))
            lines.append(f"await page.fill({sel}, {text});")
        elif action == "wait":
            ms = int(step.get("ms") or 1000)
            lines.append(f"await page.waitForTimeout({ms});")
        elif action == "eval":
            code = str(step.get("code") or "").strip()
            if not code:
                raise SystemExit(f"step {i}: eval requires code")
            lines.append(f"notes.push(await (async () => {{ {code} }})());")
        elif action == "screenshot_note":
            note = json.dumps(str(step.get("note") or "screenshot placeholder"))
            lines.append(f"notes.push({note});")
        else:
            raise SystemExit(f"step {i}: unknown action {action!r}")
    lines.append("return { title: await page.title(), url: page.url(), notes };")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_file", nargs="?", help="Path to TASK-*.md")
    ap.add_argument("--steps-json", default="", help="Inline JSON steps (overrides file)")
    ap.add_argument("--goal", default="", help="Free-text goal if no structured steps")
    ap.add_argument("--headed", action="store_true", help="Request non-headless session")
    ap.add_argument("--out", default="", help="Write report JSON to this path")
    ap.add_argument("--timeout-sec", type=int, default=90)
    args = ap.parse_args()

    report: dict = {
        "ok": False,
        "kernel_key_configured": key_is_configured(),
        "started_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": None,
        "live_view_url": None,
        "result": None,
        "error": None,
    }

    if not key_is_configured():
        report["error"] = "KERNEL_API_KEY missing in environment"
        _emit(report, args.out)
        return 2

    steps: list[dict] = []
    goal = args.goal
    if args.steps_json:
        steps = json.loads(args.steps_json)
    elif args.task_file:
        path = Path(args.task_file)
        meta = parse_front(path.read_text(encoding="utf-8"))
        if not truthy(meta.get("needs_browser")):
            report["error"] = "task does not set needs_browser: true"
            _emit(report, args.out)
            return 3
        goal = goal or meta.get("browser_goal") or ""
        raw_steps = meta.get("browser_steps_json") or ""
        if raw_steps:
            steps = json.loads(raw_steps)
    else:
        report["error"] = "provide task_file or --steps-json"
        _emit(report, args.out)
        return 2

    if not steps:
        if not goal:
            report["error"] = "no browser_steps_json and no browser_goal"
            _emit(report, args.out)
            return 3
        # Minimal safe default: stay on blank page and echo goal (agent should supply steps).
        code = (
            f"const notes = [{json.dumps(goal)}]; "
            "return { title: await page.title(), url: page.url(), notes };"
        )
    else:
        code = steps_to_playwright(steps)

    session = None
    try:
        session = create_browser(headless=not args.headed, name="ai-bridge-task")
        report["session_id"] = session.session_id
        report["live_view_url"] = session.live_view_url or None
        # Do not put cdp_ws_url (contains jwt) into report/logs.
        result = session.execute_playwright(code, timeout_sec=args.timeout_sec)
        report["result"] = result
        report["ok"] = True
    except (KernelBrowserError, json.JSONDecodeError, ValueError) as exc:
        report["error"] = str(exc)
        report["ok"] = False
    finally:
        if session is not None:
            session.close()
        report["finished_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    _emit(report, args.out)
    return 0 if report["ok"] else 1


def _emit(report: dict, out: str) -> None:
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    print(text)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
