---
id: TASK-20260820-example-github-bridge
from: cursor
to: human
target: uspex
status: done
priority: normal
created_utc: 2026-08-20T00:00:00Z
---
# Example: GitHub AI bridge is live

## Goal
Проверить, что Cloud Code видит inbox через GitHub.

## Context
Это пример. Удалите или закройте после первого успешного цикла.

Закрыто Cloud Agent `cursor/ai-inbox-auto-20260821-020031-bf57` после первого
успешного цикла: `list_pending.py` увидел задачу с `origin/main`, claim + DONE
в outbox. Новых pending TASK не создаём — иначе cron будет крутить цикл.

## Proposed solution
Cloud Code: создай новый TASK по шаблону `ai_bridge/templates/TASK_TEMPLATE.md`.
Cursor: ответит в `ai_bridge/outbox/`.

## Acceptance criteria
- [x] Cloud Code создал свой TASK в inbox и запушил
- [x] Cursor ответил DONE в outbox + PR при необходимости

## Out of scope
Торговые ордера, REAL, секреты.
