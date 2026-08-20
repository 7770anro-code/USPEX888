"""Build / strategy / prompt versioning and config fingerprint."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

BUILD_ID = "USPEX_PRO_DESK_V12_1_LATENCY_TTL_2026-08-20"
STRATEGY_VERSION = "V12_1_LATENCY_TTL_LAYERAB"
PROMPT_VERSION = "P12_1_FAST_CONFIRM_CACHED_CTX"


def config_hash(cfg: Mapping[str, Any] | None = None, **extra: Any) -> str:
    """Stable short hash of decision-relevant config for audit trails."""
    payload = dict(cfg or {})
    payload.update(extra)
    payload.setdefault("build_id", BUILD_ID)
    payload.setdefault("strategy_version", STRATEGY_VERSION)
    payload.setdefault("prompt_version", PROMPT_VERSION)
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
