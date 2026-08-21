# VideoBot

Отдельный Telegram-бот: **текст идеи → ролик 15–20 секунд**.

Не связан с USPEX/Vector. На сервере: `/opt/videobot`, unit `videobot.service`.

## Пайплайн

1. **Grok** (`XAI_API_KEY_NEW`) — 2–3 сцены: озвучка + visual-промпт.
2. **ElevenLabs** (`ELEVENLABS_API_KEY`) — TTS каждой сцены.
3. **Runway** (`RUNWAY_API_KEY`) — text-to-video `gen4.5` (если T2V недоступен — кадр `gen4_image_turbo` + `gen4_turbo`).
4. **ffmpeg** — mux аудио на клип, склейка.
5. Отправка mp4 в Telegram (`VIDEOBOT_TELEGRAM_TOKEN`).

Очереди нет: одно сообщение обрабатывается целиком. Ошибки API уходят пользователю текстом.

Деплой: [DEPLOY.md](DEPLOY.md). Секреты: [.env.example](.env.example).
