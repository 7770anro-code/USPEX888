# Automation prompt snippet — Kernel browser

Добавьте (или обновите) инструкции Automation **AI Bridge Inbox Auto** так:

```
After claim_task, if TASK frontmatter has needs_browser: true:
1) Confirm env has KERNEL_API_KEY (print only yes/no, never the value).
2) Run:
   python3 ai_bridge/scripts/run_browser_task.py <task.md> --out ai_bridge/outbox/<TASK-ID>-BROWSER.json
3) Use the JSON report (title/url/notes) to finish the coding work.
4) Commit + push ONLY on feature branch cursor/<slug>-bf57.
Never hardcode or log KERNEL_API_KEY. Never push main / deploy /opt/uspex without human OK.
```

Секрет берётся из Cursor Dashboard → Cloud Agents → Secrets (`KERNEL_API_KEY`).
Agent API не умеет править текст Automation — правку делает человек в UI.
