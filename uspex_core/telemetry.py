"""Session funnel telemetry and latency percentiles."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class LatencyTracker:
    def __init__(self, maxlen: int = 500):
        self.samples: Deque[float] = deque(maxlen=maxlen)
        self.timeouts = 0
        self.calls = 0

    def record(self, seconds: float, timed_out: bool = False) -> None:
        self.calls += 1
        if timed_out:
            self.timeouts += 1
        if seconds is not None and seconds >= 0:
            self.samples.append(float(seconds))

    def stats(self) -> dict:
        vals = sorted(self.samples)
        avg = (sum(vals) / len(vals)) if vals else 0.0
        last = vals[-1] if self.samples else 0.0
        # last in insertion order
        last = self.samples[-1] if self.samples else 0.0
        return {
            "last": round(last, 3),
            "avg": round(avg, 3),
            "p50": round(_percentile(vals, 50), 3),
            "p95": round(_percentile(vals, 95), 3),
            "n": len(vals),
            "timeouts": self.timeouts,
            "calls": self.calls,
        }


@dataclass
class SessionFunnel:
    cycles: int = 0
    checked: int = 0
    candidates: int = 0
    quality_pass: int = 0
    quality_reject: int = 0
    bad_flow_data: int = 0
    bad_book_data: int = 0
    council_started: int = 0
    council_approve: int = 0
    council_reject: int = 0
    cursor_veto: int = 0
    grok_veto: int = 0
    cursor_timeout: int = 0
    grok_timeout: int = 0
    revalidation_pass: int = 0
    revalidation_chase: int = 0
    revalidation_reversal: int = 0
    revalidation_edge_gone: int = 0
    risk_reject: int = 0
    exchange_reject: int = 0
    fill_fail: int = 0
    opened: int = 0
    would_open: int = 0
    would_reject: int = 0
    net_edge_reject: int = 0
    cursor_spawn_timeout: int = 0
    council_task_cancelled: int = 0
    council_task_hung: int = 0
    tp1_hit: int = 0
    early_exit: int = 0
    hard_stop: int = 0
    tp2: int = 0
    closed: int = 0
    last_candidate: str = "—"
    last_candidate_ts: float = 0.0
    last_event: str = "waiting"

    def bump(self, key: str, n: int = 1) -> None:
        if hasattr(self, key):
            setattr(self, key, int(getattr(self, key)) + n)

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    def health_lines(self) -> List[str]:
        d = self.as_dict()
        return [
            f"cycles {d['cycles']} • checked {d['checked']} • candidates {d['candidates']}",
            f"quality pass/reject {d['quality_pass']}/{d['quality_reject']} • bad_flow {d['bad_flow_data']} • bad_book {d['bad_book_data']}",
            f"council start/ok/rej {d['council_started']}/{d['council_approve']}/{d['council_reject']} • cursor_veto {d['cursor_veto']} • grok_veto {d['grok_veto']}",
            f"AI timeout cursor/grok {d['cursor_timeout']}/{d['grok_timeout']}",
            f"reval pass/chase/rev/edge {d['revalidation_pass']}/{d['revalidation_chase']}/{d['revalidation_reversal']}/{d['revalidation_edge_gone']}",
            f"risk/exchange/fill_fail {d['risk_reject']}/{d['exchange_reject']}/{d['fill_fail']}",
            f"opened {d['opened']} • would_open {d['would_open']} • would_reject {d['would_reject']} • net_edge_reject {d['net_edge_reject']}",
            f"cursor_spawn_timeout {d['cursor_spawn_timeout']} • council_cancel/hung {d['council_task_cancelled']}/{d['council_task_hung']}",
            f"tp1 {d['tp1_hit']} • early {d['early_exit']} • stop {d['hard_stop']} • tp2 {d['tp2']} • closed {d['closed']}",
            f"last: {d['last_candidate']} • {d['last_event'][:100]}",
        ]


class FunnelTelemetry:
    def __init__(self):
        self.by_chat: Dict[str, SessionFunnel] = defaultdict(SessionFunnel)
        self.council = LatencyTracker()
        self.cursor = LatencyTracker()
        self.grok = LatencyTracker()
        self.started = time.time()

    def funnel(self, cid: str) -> SessionFunnel:
        return self.by_chat[str(cid)]
