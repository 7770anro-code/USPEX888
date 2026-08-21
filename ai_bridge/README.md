# AI Bridge (GitHub bus)

Задачи для Cursor / Cloud / GPT живут в **git**, не на одном Маке.
Пока MacBook выключен — задачу всё равно можно положить в `inbox/` с телефона,
Cloud Code, другого ПК или через GitHub web UI. Cursor забирает её при следующей
сессии (Cloud Agent или когда Мак снова онлайн).

```
Human / Cloud / GPT
        │  commit + push TASK-*.md
        ▼
ai_bridge/inbox/     ◄── GitHub (source of truth)
        │
        │  GitHub Action: validate frontmatter → open Issue label ai-inbox
        │
        ▼
Cursor (Cloud Agent / Mac / later VPS helper)
        │  claim_task → work → complete_task
        ▼
ai_bridge/outbox/TASK-…-DONE.md  (+ optional PR with code)
```

## Когда Cursor читает inbox

1. **В начале агент-сессии** (Cursor Cloud или локально): `python3 ai_bridge/scripts/list_pending.py`
2. **По Issue** с label `ai-inbox` (создаёт Actions при push в inbox)
3. **По явной команде** в чате: «забери inbox» / «claim TASK-…»

Автоматического демона на `/opt/uspex` **нет** и не будет без отдельного ок.
Опционально: зеркало репо в `/home/cloud/` + `poll_inbox.py` только читает inbox
и пишет ACK в outbox (не трогает прод).

## Статусы задачи

| status (frontmatter) | Где | Смысл |
|---|---|---|
| `inbox` | inbox/ | Новая, никто не взял |
| `claimed` | inbox/ (обновлён) + outbox CLAIM | Cursor взял в работу |
| `wip` | outbox | Идёт работа |
| `done` | outbox `*-DONE.md` | Готово |
| `blocked` | outbox | Нужен ответ человека |
| `failed` | outbox | Ошибка |

## Секреты

Только env / GitHub Secrets. Никогда не класть ключи в `ai_bridge/**`.

## Быстрый старт

```bash
# новая задача
python3 ai_bridge/scripts/new_task.py --target uspex --title "Fix X" --goal "..."

# что ждёт выполнения
python3 ai_bridge/scripts/list_pending.py

# взять задачу
python3 ai_bridge/scripts/claim_task.py TASK-YYYYMMDD-HHMM-slug

# закрыть
python3 ai_bridge/scripts/complete_task.py TASK-YYYYMMDD-HHMM-slug --summary "..."
```

Потом: `git add ai_bridge && git commit && git push`.

## Browser tasks (Kernel)

Если в frontmatter `needs_browser: true`:

```bash
# Secret KERNEL_API_KEY must already be in Cursor Cloud Agents → Secrets
python3 ai_bridge/scripts/run_browser_task.py ai_bridge/inbox/TASK-….md --out /tmp/browser_report.json
```

Модуль: `ai_bridge/kernel_browser.py` (create session → Playwright execute on Kernel VM → delete session).
Ключ никогда не логируется; в отчёт не попадает `cdp_ws_url` (там JWT).

Сниппет для текста Automation: `ai_bridge/AUTOMATION_KERNEL.md`
(агент не может сам править Automation UI — вставьте сниппет вручную).
