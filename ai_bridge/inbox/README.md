# Inbox

Сюда кладут `TASK-*.md` (human / Cloud / GPT).

```bash
python3 ai_bridge/scripts/new_task.py --title "..." --goal "..." --target uspex
git add ai_bridge/inbox && git commit -m "ai-inbox: ..." && git push
```

Пока Mac выключен — push с другого устройства достаточен. Cursor заберёт при следующей сессии.
