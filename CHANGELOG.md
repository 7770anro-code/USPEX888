# CHANGELOG — USPEX V12.2 Institutional Fast AI

## Watchdog (not installed on VPS until explicit OK)
- `scripts/uspex_watchdog.py` + `uspex-watchdog.timer` (every 3 min).
- Auto `systemctl restart uspex.service` only (crash / restart-loop / dead PID / silent journal).
- Never deploys new code; after 3 failed revives in 30 min writes `/var/lib/uspex-watchdog/halt`.
- History: `sudo grep action=restart /var/log/uspex-watchdog.log`

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
