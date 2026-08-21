---
id: TASK-YYYYMMDD-HHMM-slug
from: cloud-code | chatgpt | claude | human
to: cursor
target: uspex | vector
status: inbox
priority: normal | high
created_utc: YYYY-MM-DDTHH:MM:SSZ
needs_browser: false
browser_goal: ""
browser_steps_json: []
---

# Title

## Goal
Что нужно получить в итоге (1–3 предложения).

## Context
Зачем / какая боль / ссылки на файлы.

## Browser (optional)
If `needs_browser: true`, Cloud Agent runs Kernel cloud browser via
`python3 ai_bridge/scripts/run_browser_task.py <this-file>`.
`browser_steps_json` is a JSON array of steps, e.g.:

```json
[
  {"action":"goto","url":"https://example.com"},
  {"action":"click","selector":"text=More information"},
  {"action":"eval","code":"return await page.title();"}
]
```

Requires Cursor secret `KERNEL_API_KEY` (never put the key in this file).

## Proposed solution
Сюда Cloud Code + ChatGPT кладут согласованный план или черновик кода.

## Acceptance criteria
- [ ] критерий 1
- [ ] критерий 2
- [ ] тесты / Shadow only / DEMO only

## Out of scope
Чего не делать.

## Notes for Cursor
Ограничения: не REAL trading, не трогать secrets, не ломать Vector если target=uspex.
Не merge/push в main и не деплой /opt/uspex без явного ОК человека.
