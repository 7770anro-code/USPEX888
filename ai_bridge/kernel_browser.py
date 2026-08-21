"""Kernel.sh cloud browser client (Playwright-in-VM / CDP).

Auth: KERNEL_API_KEY from environment only. Never log or return the secret.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

API_BASE = (os.environ.get("KERNEL_API_BASE") or "https://api.onkernel.com").rstrip("/")


class KernelBrowserError(RuntimeError):
    pass


def _api_key() -> str:
    key = (os.environ.get("KERNEL_API_KEY") or "").strip()
    if not key:
        raise KernelBrowserError(
            "KERNEL_API_KEY is not set in the environment. "
            "Add it as a Cursor Cloud Agents Runtime Secret (Personal), then re-run."
        )
    return key


def _request(
    method: str,
    path: str,
    body: Optional[dict] = None,
    timeout: int = 120,
) -> dict[str, Any]:
    key = _api_key()
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        # Never include Authorization / key material; detail is API body only.
        raise KernelBrowserError(f"Kernel API {method} {path} HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise KernelBrowserError(f"Kernel API network error: {exc.reason}") from None


@dataclass
class BrowserSession:
    session_id: str
    cdp_ws_url: str = ""
    live_view_url: str = ""
    headless: bool = True

    def execute_playwright(self, code: str, timeout_sec: int = 60) -> Any:
        """Run Playwright/TS code inside Kernel VM (page/context/browser in scope)."""
        payload = _request(
            "POST",
            f"/browsers/{self.session_id}/playwright/execute",
            {"code": code, "timeout_sec": int(timeout_sec)},
            timeout=max(30, int(timeout_sec) + 30),
        )
        if "result" in payload:
            return payload.get("result")
        return payload

    def close(self) -> None:
        try:
            _request("DELETE", f"/browsers/{self.session_id}", None, timeout=60)
        except KernelBrowserError:
            # Best-effort cleanup; session may already be gone.
            pass


def create_browser(*, headless: bool = True, name: str = "ai-bridge") -> BrowserSession:
    """Create a Kernel browser session. Returns ids/URLs only (no secrets)."""
    body: dict[str, Any] = {"headless": bool(headless)}
    if name:
        body["name"] = str(name)[:200]
    data = _request("POST", "/browsers", body, timeout=120)
    sid = data.get("session_id") or data.get("id")
    if not sid:
        raise KernelBrowserError("Kernel create browser response missing session_id")
    return BrowserSession(
        session_id=str(sid),
        cdp_ws_url=str(data.get("cdp_ws_url") or ""),
        live_view_url=str(data.get("browser_live_view_url") or ""),
        headless=bool(data.get("headless", headless)),
    )


def key_is_configured() -> bool:
    return bool((os.environ.get("KERNEL_API_KEY") or "").strip())


def connect_playwright_cdp(cdp_ws_url: str):
    """Optional local Playwright CDP attach. Requires `playwright` package."""
    if not cdp_ws_url:
        raise KernelBrowserError("cdp_ws_url is empty")
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:
        raise KernelBrowserError(
            "playwright package not installed; use BrowserSession.execute_playwright() instead"
        ) from exc
    return sync_playwright().start(), cdp_ws_url
