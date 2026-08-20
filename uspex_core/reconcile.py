"""False-close protection and position reconciliation helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence


@dataclass
class CloseDecision:
    should_close_local: bool
    reason: str
    missing_checks: int


def evaluate_exchange_absence(
    *,
    age_since_open: float,
    reconcile_grace: float,
    position_visible: bool,
    missing_checks: int,
    required_confirmations: int,
    closed_pnl_confirmed: bool,
    api_ok: bool,
) -> CloseDecision:
    """One empty /position/list must NEVER locally close a trade.

    Rules:
    - Within grace after open → ignore absence.
    - API failure → do not close.
    - Need N consecutive missing confirmations.
    - Still require Closed PnL confirmation before local close.
    """
    if position_visible:
        return CloseDecision(False, "still_open", 0)
    if not api_ok:
        return CloseDecision(False, "api_error_keep_open", missing_checks)
    if age_since_open < reconcile_grace:
        return CloseDecision(False, "grace_period", missing_checks)
    nxt = missing_checks + 1
    if nxt < required_confirmations:
        return CloseDecision(False, f"missing_confirm_{nxt}/{required_confirmations}", nxt)
    if not closed_pnl_confirmed:
        return CloseDecision(False, "awaiting_closed_pnl", nxt)
    return CloseDecision(True, "confirmed_closed", nxt)


def aggregate_closed_pnl_rows(
    rows: Sequence[Mapping],
    *,
    opened_ms: int,
    entry: float,
    entry_tol: float = 0.003,
    now_ms: Optional[int] = None,
) -> Optional[dict]:
    """Aggregate Bybit closed-PnL partials into one logical trade PnL."""
    matches = []
    cutoff = (now_ms or opened_ms) + 5_000_000  # generous upper if now unknown
    if now_ms is not None:
        cutoff = now_ms + 5000
    for r in rows:
        upd = int(r.get("updatedTime") or 0)
        if upd < opened_ms - 5000 or upd > cutoff:
            continue
        ep = float(r.get("avgEntryPrice") or 0)
        dist = abs(ep / float(entry) - 1) if ep and entry else 9e9
        if dist <= entry_tol:
            matches.append(r)
    if not matches:
        return None
    qty = sum(float(r.get("qty") or 0) for r in matches)
    pnl_sum = sum(float(r.get("closedPnl") or 0) for r in matches)
    w_exit = sum(float(r.get("avgExitPrice") or 0) * float(r.get("qty") or 0) for r in matches)
    return {
        "closedPnl": pnl_sum,
        "qty": qty,
        "parts": len(matches),
        "avgExitPrice": (w_exit / qty) if qty > 0 else 0.0,
    }


def classify_restart_positions(local_syms: Iterable[str], exchange_syms: Iterable[str]) -> dict:
    local = set(local_syms)
    exchange = set(exchange_syms)
    return {
        "synced": sorted(local & exchange),
        "exchange_only": sorted(exchange - local),  # never auto-close / hijack
        "local_only": sorted(local - exchange),     # need Closed PnL confirm, not blind close
    }
