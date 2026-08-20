"""Fast Triple AI Council: short factual snapshot + structured JSON + hard timeout."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping

from .modes import COUNCIL_THRESHOLDS


def mode_mission(profile: str) -> str:
    return {
        "easy": "EASY: more frequent smaller-risk setups; tolerate mild noise; reject only real conflicts; prefer capital preservation.",
        "medium": "MEDIUM/BALANCED canary: quality over quantity; need live impulse, liquidity, sane RR.",
        "big": "HARD: rare high-quality aggressive setups only; volatility alone is NEVER a reason to APPROVE.",
        "ai": "AI adaptive: use live facts first; memory only if sample sufficient; never compensate weak edge with leverage.",
    }.get(profile, "MEDIUM/BALANCED")


def build_council_snapshot(facts: Mapping[str, Any]) -> str:
    """Compact factual lines for AI — never pass absurd raw ratios as edge."""
    keys = (
        "symbol", "side", "mode", "uspex_score", "data_quality", "dq_reasons",
        "bybit_entry", "mark", "candidate_age_sec", "price_drift_bps",
        "residual_edge", "fresh_venues", "spread_bps",
        "flow", "book", "liquidity", "funding", "oi_delta",
        "btc_regime", "tp1", "tp2", "stop", "rr_tp2_stop", "max_chase_bps",
        "memory",
    )
    parts = []
    for k in keys:
        if k not in facts:
            continue
        v = facts[k]
        if v is None or v == "" or v == "omit":
            continue
        parts.append(f"{k}={v}")
    return "; ".join(parts)


CURSOR_VOTE_SYSTEM = (
    "You are CURSOR: independent microstructure/structure reviewer in USPEX Triple AI Council. "
    "Do NOT agree for consensus. Check chase/pullback, setup integrity, freshness. "
    "OI=0 / missing funding / no-memory / flow=UNKNOWN / book=UNKNOWN are NOT automatic vetoes. "
    "RR = TP2/Stop (not TP1/Stop). Reply ONLY compact JSON."
)

GROK_VOTE_SYSTEM = (
    "You are GROK: independent regime/news/risk critic in USPEX Triple AI Council. "
    "Do NOT rubber-stamp. Try to break the idea on regime, chase, liquidity, RR. "
    "Missing optional telemetry is neutral. Reply ONLY compact JSON."
)


def cursor_vote_prompt(snapshot: str, profile: str) -> str:
    return (
        f"ROLE=CURSOR MODE={mode_mission(profile)}\n"
        f"FACTS: {snapshot}\n"
        "Decide APPROVE or REJECT. confidence 0-100. leverage=safe ceiling.\n"
        'JSON: {"decision":"APPROVE|REJECT","confidence":0-100,"leverage":1-100,'
        '"flags":["NONE|LATE|CHASE|FLOW|BOOK|SPREAD|RR|STALE"],"reason":"<=120 chars"}'
    )


def grok_vote_prompt(snapshot: str, profile: str) -> str:
    return (
        f"ROLE=GROK MODE={mode_mission(profile)}\n"
        f"FACTS: {snapshot}\n"
        "Adversarial review. APPROVE only if residual edge still real.\n"
        'JSON: {"decision":"APPROVE|REJECT","confidence":0-100,"leverage":1-100,'
        '"flags":["NONE|CHASE|REGIME|SPREAD|VOL|RR|STALE"],"reason":"<=120 chars"}'
    )


def parse_vote_json(raw: str, default_lev: float = 10.0) -> Dict[str, Any]:
    text = (raw or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSON not found")
    d = json.loads(m.group(0))
    dec = str(d.get("decision", "")).upper().strip()
    if dec not in ("APPROVE", "REJECT"):
        raise ValueError("bad decision")
    flags = d.get("flags") if isinstance(d.get("flags"), list) else []
    return {
        "ok": True,
        "decision": dec,
        "confidence": max(0, min(100, float(d.get("confidence", 0) or 0))),
        "leverage": max(1, min(100, float(d.get("leverage", default_lev) or default_lev))),
        "flags": [str(x)[:24] for x in flags[:5]],
        "reason": str(d.get("reason", "")).replace("\n", " ")[:180],
        "timeout": False,
    }


def timeout_vote(who: str, lev: float = 10.0) -> Dict[str, Any]:
    return {
        "ok": False,
        "decision": "REJECT",
        "confidence": 0,
        "leverage": max(1, min(100, lev)),
        "flags": ["TIMEOUT"],
        "reason": f"{who} timeout FAIL_CLOSED",
        "timeout": True,
    }


def council_gate(profile: str, uspex_score: float, cursor_vote: Mapping, grok_vote: Mapping):
    ut, ct, gt = COUNCIL_THRESHOLDS.get(profile, COUNCIL_THRESHOLDS["medium"])
    us = float(uspex_score)
    cc = float((cursor_vote or {}).get("confidence", 0) or 0)
    gc = float((grok_vote or {}).get("confidence", 0) or 0)
    cd = str((cursor_vote or {}).get("decision", "")).upper()
    gd = str((grok_vote or {}).get("decision", "")).upper()
    cursor_ok = bool((cursor_vote or {}).get("ok")) and cd == "APPROVE" and cc >= ct and not (cursor_vote or {}).get("timeout")
    grok_ok = bool((grok_vote or {}).get("ok")) and gd == "APPROVE" and gc >= gt and not (grok_vote or {}).get("timeout")
    uspex_ok = us >= ut
    allow = uspex_ok and cursor_ok and grok_ok
    if (cursor_vote or {}).get("timeout"):
        return False, "AI_TIMEOUT_CURSOR"
    if (grok_vote or {}).get("timeout"):
        return False, "AI_TIMEOUT_GROK"
    if allow:
        return True, f"TRIPLE_{profile.upper()}_CONSENSUS"
    if not cursor_ok and cd == "REJECT":
        return False, "COUNCIL_CURSOR_VETO"
    if not grok_ok and gd == "REJECT":
        return False, "COUNCIL_GROK_VETO"
    return False, f"REJECT_{profile.upper()}_COUNCIL"
