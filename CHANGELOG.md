# CHANGELOG — USPEX V12.2.1 Scanner Hangfix

## BUILD
- `USPEX_PRO_DESK_V12_2_1_SCANNER_HANGFIX_2026-08-21`
- Strategy: `V12_2_1_SCANNER_HANGFIX`
- Prompt: `P12_2_FAST_CONFIRM_GROK_FAST`
- Config schema: `C3_INSTITUTIONAL_V1`

## Changes
- Journal `NET_EDGE_REJECT` when `candidate()` drops a setup on net-edge (was silent `return None`).
- Cursor CLI spawn has its own timeout (`CURSOR_SPAWN_TIMEOUT_SEC=3`), separate from vote read timeout (11s). Spawn miss → `CURSOR_CONNECT_TIMEOUT`; hung communicate → `CURSOR_READ_TIMEOUT`.
- Council cancel path no longer `await`s cancelled tasks under `except Exception`. Uses `settle_cancelled_tasks` + `vote_from_task` so child `CancelledError` cannot kill the scanner. Logs `COUNCIL_TASK_CANCELLED` / `COUNCIL_TASK_HUNG`.

# CHANGELOG — USPEX V12.2 Institutional Fast AI

## BUILD
- `USPEX_PRO_DESK_V12_2_INSTITUTIONAL_FAST_AI_2026-08-20`
- Strategy: `V12_2_INSTITUTIONAL_LAYERAB_FAST_AI`
- Prompt: `P12_2_FAST_CONFIRM_GROK_FAST`
- Config schema: `C3_INSTITUTIONAL_V1`

## Changes
- Separate `XAI_VOTE_MODEL` (default `grok-4-1-fast-non-reasoning`).
- Council budget 12s; Cursor vote timeout 11s; Grok 5s.
- Cursor votes: `--trust`, serialized `CURSOR_VOTE_LOCK`.
- Fair value + net-edge + executable price wired into `candidate()`.
- Venue adapter package (Bybit/Binance/OKX) with sequence/dirty book hooks.
- Deploy forces MEDIUM + Shadow + vote model on VPS.
