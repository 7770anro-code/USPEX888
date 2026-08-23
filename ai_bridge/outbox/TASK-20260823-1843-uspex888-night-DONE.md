---
id: TASK-20260823-1843-uspex888-night
from: cursor
to: human
target: videobot
status: done
pr:
commit:
finished_utc: 2026-08-23T19:20:00Z
---

# DONE: Успех 888 — ночной пайплайн

## What changed
- `videobot/nightcal.py` — календарь, валидация, слоты дня
- `videobot/nightpack.py` — подписи/хештеги/outbox, без автопостинга
- `videobot/night.py` + `night_run.py` — shadow/render, бюджет, fail-closed
- `videobot/joblock.py` — замок с живым ботом
- `videobot/store.py` — таблицы night_jobs / night_runs
- `videobot/videobot-night.service` + `.timer`
- `videobot/test_night.py`

## How to verify
```bash
cd videobot && python test_night.py && python test_parse.py
python night_run.py --date 2026-08-24 --no-telegram
```

## Open questions
Когда включать `NIGHT_RENDER=1` на VPS и какой `NIGHT_OWNER_CHAT_ID`.
