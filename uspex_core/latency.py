"""End-to-end pipeline latency tracing for candidates and trades."""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from .telemetry import LatencyTracker


STAGES = (
    "signal_detected",
    "quality_pass",
    "council_start",
    "cursor_done",
    "grok_done",
    "council_done",
    "revalidation_done",
    "order_sent",
    "order_ack",
    "fill_confirmed",
)


@dataclass
class PipelineTrace:
    candidate_id: str
    symbol: str
    side: str
    mode: str
    marks: Dict[str, float] = field(default_factory=dict)
    meta: Dict[str, float] = field(default_factory=dict)

    def mark(self, stage: str, ts: Optional[float] = None) -> None:
        self.marks[stage] = float(ts if ts is not None else time.time())

    def _ms(self, a: str, b: str) -> Optional[float]:
        if a not in self.marks or b not in self.marks:
            return None
        return max(0.0, (self.marks[b] - self.marks[a]) * 1000.0)

    def metrics(self) -> Dict[str, Optional[float]]:
        m = {
            "detect_to_council_ms": self._ms("signal_detected", "council_start"),
            "council_ms": self._ms("council_start", "council_done"),
            "cursor_ms": self._ms("council_start", "cursor_done"),
            "grok_ms": self._ms("council_start", "grok_done"),
            "revalidation_ms": self._ms("council_done", "revalidation_done"),
            "order_ack_ms": self._ms("order_sent", "order_ack"),
            "fill_confirm_ms": self._ms("order_ack", "fill_confirmed"),
            "candidate_to_order_ms": self._ms("signal_detected", "order_sent"),
            "candidate_to_fill_ms": self._ms("signal_detected", "fill_confirmed"),
            "signal_age_at_order_ms": self._ms("signal_detected", "order_sent"),
            "signal_age_at_fill_ms": self._ms("signal_detected", "fill_confirmed"),
        }
        m.update(self.meta)
        return m

    def latency_bucket(self) -> str:
        age = self.metrics().get("candidate_to_fill_ms")
        if age is None:
            age = self.metrics().get("candidate_to_order_ms") or self.metrics().get("council_ms")
        if age is None:
            return "unknown"
        sec = age / 1000.0
        if sec <= 3:
            return "0-3s"
        if sec <= 5:
            return "3-5s"
        if sec <= 8:
            return "5-8s"
        if sec <= 12:
            return "8-12s"
        return ">12s"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["metrics"] = self.metrics()
        d["latency_bucket"] = self.latency_bucket()
        return d


class LatencyRegistry:
    """Session-level latency trackers for /health and analytics."""

    def __init__(self):
        self.candidate_to_fill = LatencyTracker()
        self.candidate_to_order = LatencyTracker()
        self.council = LatencyTracker()
        self.cursor = LatencyTracker()
        self.grok = LatencyTracker()
        self.order_ack = LatencyTracker()
        self.fill_confirm = LatencyTracker()
        self.detect_to_council = LatencyTracker()
        self.revalidation = LatencyTracker()
        self.traces: List[PipelineTrace] = []
        self.bucket_stats: Dict[str, Dict[str, float]] = {}

    def ingest(self, trace: PipelineTrace) -> None:
        self.traces.append(trace)
        if len(self.traces) > 1000:
            self.traces = self.traces[-1000:]
        m = trace.metrics()

        def rec(tracker: LatencyTracker, key: str):
            v = m.get(key)
            if v is not None:
                tracker.record(v / 1000.0)

        rec(self.detect_to_council, "detect_to_council_ms")
        rec(self.council, "council_ms")
        rec(self.cursor, "cursor_ms")
        rec(self.grok, "grok_ms")
        rec(self.revalidation, "revalidation_ms")
        rec(self.order_ack, "order_ack_ms")
        rec(self.fill_confirm, "fill_confirm_ms")
        rec(self.candidate_to_order, "candidate_to_order_ms")
        rec(self.candidate_to_fill, "candidate_to_fill_ms")

    def health_lines(self) -> List[str]:
        def fmt(name: str, t: LatencyTracker) -> str:
            s = t.stats()
            return f"{name} last/avg/p50/p95: {s['last']:.3f}/{s['avg']:.3f}/{s['p50']:.3f}/{s['p95']:.3f}s n={s['n']} to={s['timeouts']}"

        return [
            fmt("candidate→fill", self.candidate_to_fill),
            fmt("candidate→order", self.candidate_to_order),
            fmt("detect→council", self.detect_to_council),
            fmt("Council", self.council),
            fmt("Cursor", self.cursor),
            fmt("Grok", self.grok),
            fmt("revalidation", self.revalidation),
            fmt("order ack", self.order_ack),
            fmt("fill confirm", self.fill_confirm),
        ]


def latency_bucket_from_ms(ms: Optional[float]) -> str:
    if ms is None:
        return "unknown"
    sec = ms / 1000.0
    if sec <= 3:
        return "0-3s"
    if sec <= 5:
        return "3-5s"
    if sec <= 8:
        return "5-8s"
    if sec <= 12:
        return "8-12s"
    return ">12s"
