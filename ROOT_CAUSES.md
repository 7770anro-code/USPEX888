# ROOT CAUSES — opened=0 / AI spam rejects

1. **Cursor CLI latency ~9–10s** on VPS while vote timeout was **5s** → systematic `AI_TIMEOUT_CURSOR`.
2. **Grok model `grok-4.6` / `4.5` ~7–10s** while vote timeout was **5s** → systematic `AI_TIMEOUT_GROK`.
3. Council hard budget **8s** < Cursor wall time → deadline cancel even when Grok would finish.
4. Missing `--trust` on Cursor agent in non-interactive cwd → fast fail / unstable votes.
5. Parallel Cursor processes thrashing one agent binary (no vote lock).
6. Canary user flipped back to **AI** mode (heavier path) instead of MEDIUM.
7. Pre-V12 absurd flow/book ratios inflated scores (fixed in V12 robust micro).
8. Literal-repeat revalidation / late chase after slow council (mitigated by TTL + residual edge + Layer A/B).
