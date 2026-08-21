# AI Bridge protocol

## Actors
- **Submitter**: human / Cloud Code / ChatGPT (writes inbox TASK)
- **Executor**: Cursor Cloud Agent / Automation (reads inbox, writes outbox, opens code PR)
- **Bus**: GitHub private repo `USPEX888`
- **Browser**: Kernel.sh cloud browser when `needs_browser: true` (secret `KERNEL_API_KEY`)

## Offline Mac
1. Submitter pushes `ai_bridge/inbox/TASK-*.md` to GitHub (any device).
2. GitHub Action validates + opens Issue `ai-inbox`.
3. Automation **AI Bridge Inbox Auto** (every 30 min) or a manual Cloud Agent run:
   - `list_pending` → `claim_task` → implement → `complete_task`
4. If TASK has `needs_browser: true`, run:
   `python3 ai_bridge/scripts/run_browser_task.py ai_bridge/inbox/TASK-….md`
   (uses Kernel Playwright execute API; key only from env).

## Forbidden
- Secrets in markdown / git / logs
- Touching `/opt/uspex` without deploy confirmation
- Auto-merge / push to `main` without human OK in chat
