"""Build / strategy / prompt versioning and config fingerprint."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

BUILD_ID = "USPEX_PRO_DESK_V12_0_DEMO_RELIABILITY_2026-08-20"
STRATEGY_VERSION = "V12_0_DEMO_RELIABILITY"
PROMPT_VERSION = "P12_0_SHORT_ROLE_JSON"


def config_hash(cfg: Mapping[str, Any] | None = None, **extra: Any) -> str:
    """Stable short hash of decision-relevant config for audit trails."""
    payload = dict(cfg or {})
    payload.update(extra)
    payload.setdefault("build_id", BUILD_ID)
    payload.setdefault("strategy_version", STRATEGY_VERSION)
    payload.setdefault("prompt_version", PROMPT_VERSION)
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
