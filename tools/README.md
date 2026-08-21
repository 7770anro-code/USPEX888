# tools/ — local harnesses (not part of USPEX/VECTOR runtime)

## orchestrator.py

Multi-round Lead→Reviewer code harness. **Never merges/pushes/deploys.**

### Run (Mac)

```bash
cd /Users/ar/Desktop/vector-terminal
export OPENAI_API_KEY=...
export XAI_API_KEY=...          # if using grok reviewers
# optional: confirm you verified prices manually
# export ORCH_PRICE_VERIFIED=1
# optional: override rates JSON
# export ORCH_PRICE_PER_MTOK_JSON='{"grok-4.5":{"input":2,"cached_input":0.3,"output":6}}'
python3 tools/orchestrator.py "short task description"
```

`ORCH_REPO_ROOT` defaults to the repo root (parent of `tools/`).

### Lead output formats (vector-terminal)

- `{"patch": "...unified diff..."}` — `git apply --check --index`
- `{"files":[{"path":"...","content":"..."}]}` — full files (Cursor-style)

### Notes

- `PRICE_PER_MTOK` is **UNVERIFIED** until `ORCH_PRICE_VERIFIED=1`.
- On macOS, pytest does not require `unshare` by default; set `ORCH_REQUIRE_NET_ISOLATION=1` to force fail-closed.
- VPS workspace (separate from `/opt/uspex`): `/home/cloud/orchestrator-workspace/`
