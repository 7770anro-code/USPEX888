# USPEX Architecture (V12.2 / Master Spec V3 subset)

## Execution
- **BYBIT DEMO only**. `REAL_TRADING_ENABLED=False` hard-locked.
- Shadow Mode: full pipeline, **zero orders** (`USPEX_SHADOW_MODE=1`).

## Layers
- **Layer A (slow)**: regime/structure cache refreshed ~45s (`AiContextCache`).
- **Layer B (fast)**: cross-exchange lag trigger → quality → coalesce → Triple AI confirm → TTL → revalidation → risk → (shadow WOULD_OPEN | demo order).

## Triple AI
- USPEX quantitative score + DATA_QUALITY_SCORE.
- Cursor CLI (`agent -p --trust`) serialized under lock; timeout fail-closed.
- Grok vote model: `XAI_VOTE_MODEL=grok-4-1-fast-non-reasoning` (fast path ~1s).
- Council budget: **12s** (spec 8–12). Cursor vote timeout 11s; Grok 5s.

## V3 foundations added
- `uspex_core/fair_value.py` — robust fair value, outlier quarantine.
- `uspex_core/net_edge.py` — net edge after costs; executable bid/ask entry.
- `uspex_core/venues/` — Bybit/Binance/OKX adapter contract + sequence integrity stubs.

## Not fully migrated yet (known limitation)
- Private Bybit WS fill state-machine as sole source of truth.
- Full multi-venue SymbolRegistry / stablecoin depeg / lead-lag estimator.
- A/B market-order slippage policies.
