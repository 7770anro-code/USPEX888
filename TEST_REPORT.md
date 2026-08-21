# TEST_REPORT — V12.2.1 Scanner Hangfix

## Local (2026-08-21)

| Suite | Command | Exit | Status |
|---|---|---|---|
| Hangfix | `python3 -m unittest tests.test_scanner_hangfix` | 0 | PASS |
| Unit V3 | `python3 -m unittest tests.test_v3_institutional` | 0 | PASS |
| Latency/TTL/LayerAB | `python3 -m unittest tests.test_latency_architecture` | 0 | PASS |
| V12 suite | `python3 -m unittest tests.test_uspex_v12_suite` | 0 | PASS |

NOT RUN (need live VPS after deploy):
- DEMO_INTEGRATION_live_bybit_shadow
- LIVE_COUNCIL_p95_measurement
- Confirm `NET_EDGE_REJECT` / `CURSOR_CONNECT_TIMEOUT` / `COUNCIL_TASK_CANCELLED` appear in `trade_events` instead of 9h silence

## STATUS
Code-complete for hang observability + CancelledError guard. Deploy to VPS only after explicit OK.

# TEST_REPORT — V12.2 Institutional Fast AI

## Local (2026-08-20)

| Suite | Command | Exit | Status |
|---|---|---|---|
| Unit V3 | `python3 -m unittest tests.test_v3_institutional` | 0 | PASS |
| Latency/TTL/LayerAB | `python3 -m unittest tests.test_latency_architecture` | 0 | PASS |
| V12 suite (~50) | `python3 -m unittest tests.test_uspex_v12_suite` | 0 | PASS |
| Replay 3000 | `python3 tests/replay_harness.py` | 0 | PASS |
| Honest matrix | `python3 tests/run_honest_matrix.py` | 0 | PASS 6 / FAIL 0 / NOT_RUN 2 |

NOT RUN (need live VPS after deploy):
- DEMO_INTEGRATION_live_bybit_shadow
- LIVE_COUNCIL_p95_measurement

## STATUS
`READY_FOR_SHADOW` after successful VPS boot + funnel shows non-timeout rejects/opens.
Not yet `READY_FOR_DEMO` (Shadow must prove AI timeouts fixed + no false closes).
