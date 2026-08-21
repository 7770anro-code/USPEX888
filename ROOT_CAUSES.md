# ROOT CAUSES — opened=0 / AI spam rejects

1. **Cursor CLI latency ~9–10s** on VPS while vote timeout was **5s** → systematic `AI_TIMEOUT_CURSOR`.
2. **Grok model `grok-4.6` / `4.5` ~7–10s** while vote timeout was **5s** → systematic `AI_TIMEOUT_GROK`.
3. Council hard budget **8s** < Cursor wall time → deadline cancel even when Grok would finish.
4. Missing `--trust` on Cursor agent in non-interactive cwd → fast fail / unstable votes.
5. Parallel Cursor processes thrashing one agent binary (no vote lock).
6. Canary user flipped back to **AI** mode (heavier path) instead of MEDIUM.
7. Pre-V12 absurd flow/book ratios inflated scores (fixed in V12 robust micro).
8. Literal-repeat revalidation / late chase after slow council (mitigated by TTL + residual edge + Layer A/B).
9. **V12.2 silent net-edge drop** in `candidate()` (`return None` without `trade_events`) → scanner looked idle. Fixed in V12.2.1 as `NET_EDGE_REJECT`.
10. **V12.2 Cursor spawn had no timeout** on `create_subprocess_exec` under `CURSOR_VOTE_LOCK` → hung spawn could stall council. Fixed: `CURSOR_SPAWN_TIMEOUT_SEC` → `CURSOR_CONNECT_TIMEOUT`.
11. **V12.2 `await cancelled_task` under `except Exception`** — `CancelledError` is BaseException → could kill the scanner coroutine while WS/Telegram stayed up. Fixed: `settle_cancelled_tasks` + `vote_from_task`; journal `COUNCIL_TASK_CANCELLED` / `COUNCIL_TASK_HUNG`.
