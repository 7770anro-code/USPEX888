# VideoBot

Отдельный Telegram-бот: **текст идеи → ролик 15–20 секунд**.

Не связан с USPEX/Vector. На сервере: `/opt/videobot`, unit `videobot.service`.

## Пайплайн

1. **Grok** (`XAI_API_KEY_NEW`) — 2–3 сцены: озвучка + visual-промпт.
2. **ElevenLabs** (`ELEVENLABS_API_KEY`) — TTS, дефолт голос Sarah (`EXAVITQu4vr4xnSDxMaL`), `eleven_multilingual_v2`, ответ читается как сырой `audio/mpeg`.
3. **Runway** (`RUNWAY_API_KEY`) — `https://api.dev.runwayml.com/v1/text_to_video` модель `gen4.5`, заголовок `X-Runway-Version: 2024-11-06`, поллинг `GET /v1/tasks/{id}` (≥5с + jitter). `gen4_turbo` только image-to-video (запасной путь: кадр + I2V).
4. **ffmpeg** — mux аудио на клип, склейка.
5. Отправка mp4 в Telegram (`VIDEOBOT_TELEGRAM_TOKEN`).

Очереди нет: одно сообщение обрабатывается целиком. Ошибки API уходят пользователю текстом.

Деплой: [DEPLOY.md](DEPLOY.md). Секреты: [.env.example](.env.example).
