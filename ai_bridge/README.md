# AI Bridge via GitHub

Общий канал связи: **Cloud Code / ChatGPT / Claude ↔ GitHub ↔ Cursor**.

IP между ИИ не нужен. Пишем в репозиторий — читаем из репозитория.

## Поток

```
Ты → Cloud Code / ChatGPT
        ↓  создаёт файл в ai_bridge/inbox/  ИЛИ  Issue с label ai-inbox
     GitHub (push / PR)
        ↓
     Cursor забирает задачу
        ↓  делает код + тесты
     Cursor пишет ответ в ai_bridge/outbox/  ИЛИ  PR + комментарий
        ↓
     Ты / Cloud Code видите результат в GitHub
```

## Правила

1. **Один writer кода в проекте — Cursor.** Cloud/GPT кладут ТЗ и черновики в `inbox`, не правят `main_*.py` напрямую без PR.
2. Каждая задача — один файл `ai_bridge/inbox/TASK-YYYYMMDD-HHMM-<slug>.md` по шаблону.
3. Cursor отвечает файлом `ai_bridge/outbox/TASK-...-DONE.md` + PR с кодом.
4. Цель обязательна: `target: uspex` или `target: vector` (не смешивать).
5. Секреты (API keys) в bridge-файлы **не писать**.

## Labels (Issues)

| Label | Кто ставит | Смысл |
|-------|------------|--------|
| `ai-inbox` | Cloud/GPT | Новая задача для Cursor |
| `ai-wip` | Cursor | В работе |
| `ai-done` | Cursor | Сделано, смотри outbox/PR |
| `target-uspex` | любой | Касается USPEX |
| `target-vector` | любой | Касается Vector |

## Команды для Cloud Code

После того как придумали решение с GPT:

```bash
# из корня клона репо
cp ai_bridge/templates/TASK_TEMPLATE.md \
   ai_bridge/inbox/TASK-$(date -u +%Y%m%d-%H%M)-my-idea.md
# отредактировать файл, затем:
git checkout -b orch/cloud-$(date -u +%Y%m%d-%H%M)
git add ai_bridge/inbox/
git commit -m "ai-inbox: <коротко о задаче>"
git push -u origin HEAD
# открыть PR в main (или в рабочую ветку) с title: [ai-inbox][uspex] ...
```

## Команды для Cursor

Когда видит новый inbox / Issue `ai-inbox`:

1. Перенести статус → `ai-wip`
2. Внедрить в код
3. Положить `ai_bridge/outbox/TASK-...-DONE.md`
4. PR с реализацией + label `ai-done`

## Что нужно один раз от тебя

Подключить remote GitHub (сейчас `origin` может отсутствовать):

```bash
git remote add origin git@github.com:<USER>/<REPO>.git
git push -u origin main   # или текущую ветку
```

Без remote Cloud Code и Cursor **не увидят** файлы друг друга.
