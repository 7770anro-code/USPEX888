# VideoBot

Отдельный Telegram-бот: **текст идеи → ролик ~20–30 секунд**.

Не связан с USPEX/Vector. На сервере: `/opt/videobot`, unit `videobot.service`.

## Пайплайн

1. **Grok** (`grok-4.5`, fallback fast) — 2–3 сцены, cinematic-промпты.
2. **ElevenLabs** — TTS Sarah / `eleven_multilingual_v2`, сырой `audio/mpeg`.
3. **Runway** `gen4.5` T2V, дефолт **9:16** (`720:1280`), клип ~10 сек; ретраи 429/5xx и INTERNAL.
4. **ffmpeg** — подгон голоса (`atempo`), склейка, субтитры (DejaVu).
5. Telegram: превью сценария + mp4. `/ratio`, `/style`.

Ошибки API — текстом в чат. При сбое рабочие файлы остаются в `/tmp/videobot` (`KEEP_FAILED_DIR=1`).

Деплой: [DEPLOY.md](DEPLOY.md). Секреты: [.env.example](.env.example).
