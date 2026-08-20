"""Mode thresholds — monotonic: EASY ≤ MEDIUM ≤ HARD (strictness)."""
from __future__ import annotations

# Council: (USPEX, Cursor, Grok) minimums — HARD hardest.
COUNCIL_THRESHOLDS = {
    "easy":   (58.0, 55.0, 55.0),
    "medium": (68.0, 58.0, 58.0),
    "big":    (80.0, 68.0, 68.0),  # stricter than medium
    "ai":     (72.0, 60.0, 62.0),
}

# Quality/execution guards — HARD stricter (lower fresh_age, tighter spread/drift, higher RR).
PROFILE_GUARDS = {
    "easy":   {"fresh_age": 8.0, "max_spread_bps": 20.0, "min_rr": 1.40, "max_drift_bps": 22.0, "single_available": 0.25, "min_dq": 50.0},
    "medium": {"fresh_age": 7.0, "max_spread_bps": 16.0, "min_rr": 1.50, "max_drift_bps": 28.0, "single_available": 0.30, "min_dq": 58.0},
    "big":    {"fresh_age": 5.0, "max_spread_bps": 12.0, "min_rr": 1.70, "max_drift_bps": 16.0, "single_available": 0.22, "min_dq": 70.0},
    "ai":     {"fresh_age": 7.0, "max_spread_bps": 16.0, "min_rr": 1.50, "max_drift_bps": 26.0, "single_available": 0.28, "min_dq": 60.0},
}

EXIT_POLICY = {
    "easy":   {"early_age": 180.0, "risk_frac": 0.70, "dead_age": 600.0, "bad": 3},
    "medium": {"early_age": 120.0, "risk_frac": 0.55, "dead_age": 420.0, "bad": 2},
    "big":    {"early_age": 90.0,  "risk_frac": 0.45, "dead_age": 300.0, "bad": 2},
    "ai":     {"early_age": 110.0, "risk_frac": 0.52, "dead_age": 390.0, "bad": 2},
    "manual": {"early_age": 180.0, "risk_frac": 0.70, "dead_age": 600.0, "bad": 3},
}

SIGNAL_WINDOWS = {
    "easy": 3.0,
    "medium": 2.5,
    "big": 1.5,
    "ai": 2.0,
    "manual": 2.5,
}

# Candidate scout thresholds (before Council). Flow/book are soft via robust metrics.
PROFILES = {
    "easy":   {"title": "ЛЁГКИЙ", "emoji": "🟢", "score": 55, "move": 0.08, "gap": 0.030, "flow": 1.05, "book": 1.05,
               "max_open": 0, "m1": 20, "m2": 35, "lev": 8, "tp1": 2.5, "tp2": 4.5, "sl": 2.0},
    "medium": {"title": "СРЕДНИЙ", "emoji": "🟡", "score": 65, "move": 0.14, "gap": 0.050, "flow": 1.12, "book": 1.12,
               "max_open": 0, "m1": 30, "m2": 50, "lev": 12, "tp1": 6, "tp2": 10, "sl": 4},
    "big":    {"title": "ХАРД", "emoji": "🔴", "score": 78, "move": 0.28, "gap": 0.10, "flow": 1.28, "book": 1.28,
               "max_open": 0, "m1": 35, "m2": 50, "lev": 18, "tp1": 10, "tp2": 16, "sl": 5},
    "manual": {"title": "РУЧНОЙ", "emoji": "🎮", "score": 65, "move": 0.14, "gap": 0.050, "flow": 1.12, "book": 1.12, "max_open": 0},
    "ai":     {"title": "AI AUTOPILOT", "emoji": "🤖", "score": 70, "move": 0.18, "gap": 0.065, "flow": 1.15, "book": 1.15,
               "max_open": 0, "m1": 25, "m2": 25, "lev": 5, "tp1": 7.5, "tp2": 15, "sl": 5},
}

AUTO_COUNCIL_PROFILES = {"easy", "medium", "big", "ai"}

# Council wall-clock budget (seconds). Fail-closed on timeout.
# Spec allows 8–12s. Cursor CLI cold path is ~9–10s on VPS; Grok fast model ~1s.
# Layer A cache keeps prompts tiny; residual edge / TTL still gate late fills.
COUNCIL_BUDGET_SEC = 12.0
CURSOR_VOTE_TIMEOUT_SEC = 11.0
GROK_VOTE_TIMEOUT_SEC = 5.0

TP1_CLOSE_FRACTION = 0.22  # ~20–25%


def assert_mode_monotonic() -> list:
    """Return list of monotonicity violations (empty = OK)."""
    problems = []
    # Score thresholds should rise easy→medium→big
    if PROFILES["easy"]["score"] > PROFILES["medium"]["score"]:
        problems.append("easy score > medium")
    if PROFILES["medium"]["score"] > PROFILES["big"]["score"]:
        problems.append("medium score > hard")
    # HARD stricter: lower fresh_age, lower max_spread, higher min_rr
    if PROFILE_GUARDS["big"]["fresh_age"] > PROFILE_GUARDS["medium"]["fresh_age"]:
        problems.append("hard fresh_age looser than medium")
    if PROFILE_GUARDS["big"]["max_spread_bps"] > PROFILE_GUARDS["medium"]["max_spread_bps"]:
        problems.append("hard spread looser than medium")
    if PROFILE_GUARDS["big"]["min_rr"] < PROFILE_GUARDS["medium"]["min_rr"]:
        problems.append("hard RR easier than medium")
    if COUNCIL_THRESHOLDS["big"][0] < COUNCIL_THRESHOLDS["medium"][0]:
        problems.append("hard council USPEX easier than medium")
    return problems
