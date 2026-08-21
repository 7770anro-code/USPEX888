# AI Bridge protocol

## Actors
- **Submitter**: human / Cloud Code / ChatGPT (writes inbox TASK)
- **Executor**: Cursor (reads inbox, writes outbox, opens code PR)
- **Bus**: GitHub private repo `USPEX888`

## Offline Mac
1. Submitter pushes `ai_bridge/inbox/TASK-*.md` to GitHub (any device).
2. GitHub Action validates + opens Issue `ai-inbox`.
3. When electricity/Mac/Cloud Agent returns, Cursor lists pending, claims, implements, completes.
4. No dependency on a local always-on Mac daemon.

## Forbidden
- Secrets in markdown
- Touching `/opt/uspex` without deploy confirmation
- Auto-merge to production servers
