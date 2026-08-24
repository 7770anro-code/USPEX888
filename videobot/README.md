# VideoBot

Отдельный Telegram-бот: **идея / пресет / свой текст → вертикальный ролик 30–60 секунд (TikTok 9:16)**.

Не связан с USPEX/Vector. На сервере: `/opt/videobot`, unit `videobot.service`.

## Пайплайн

1. **Grok** (`grok-4.5`, fallback fast) — JSON: `continuity` + 4–6 сцен. Пресет добавляет хук, темп и CTA в бриф.
2. **ElevenLabs** — TTS, сырой `audio/mpeg`. 21 голос кнопками + клон из SQLite. Подача и скорость — `voice_settings`.
3. **Runway** `https://api.dev.runwayml.com`, `X-Runway-Version: 2024-11-06`.
   - Вертикаль `720:1280`. Клип 5 или 10 сек.
   - Качество в UI: **Быстро** (`gen4_turbo` I2V) / **Оптимально** (`gen4.5`).
   - Одно исходное фото на все клипы, last-frame chaining. `contentModeration.publicFigureThreshold=auto`.
4. **ffmpeg** — `atempo`, склейка 9:16, субтитры, опциональный водяной знак (текст/лого, вкл/выкл).
5. Перед запуском — оценка кредитов Runway + символы ElevenLabs, кнопки **Создать / Отмена** (и «Изменить»).
6. Готовый ролик уходит двумя файлами: `answer_video` + `answer_document`. На экране результата — **«Улучшить качество»** (`POST /v1/video_upscale` на `final.mp4`).

## Режимы (/start)

- **Видео за 1 клик** — идея → настройки → оценка стоимости.
- **Своё фото + текст + голос** — сценарий, фото, **та же кнопка согласия** (`consent:yes`), голос, стоимость.
- **Оживить фото** — Act Two (`model=act_two`): фото + короткое видео мимики. Согласие на фото — **та же кнопка**, что в custom-режиме (хард-константа).
- **Клонировать мой голос** — отдельное согласие (не фото) → запись/файл → `POST /v1/voices/add` → `voice_id` в SQLite по `user_id`. Кнопка **«Удалить мой голос»**.
- **Пресеты** — Вирусный TikTok / Реклама товара / Мем / Личный бренд (+ Кино-история). Пользователь пишет только тему.

Фото человека: пайплайн **не стартует** без `consent_verified` (`photo_start_blocked` / `CONSENT_REQUIRED_MSG`).

Ошибки API — текстом в чат. Деплой: [DEPLOY.md](DEPLOY.md).

## Волна 2

SQLite `videobot/data/videobot.sqlite3`: клон голоса, водяной знак, путь к последнему ролику.

- Instant Voice Clone — согласие отдельно от фото, хранение `voice_id` по `user_id`
- Act Two с /start, то же согласие что custom-фото
- Magnific video upscale готового `final.mp4` с экрана результата
- Пресеты задают стиль/темп/голос в бриф Grok
- Оценка кредитов до «Создать»
- Водяной знак ffmpeg вкл/выкл, без Brand Kit

В «Ещё возможности»: голос по описанию, Speech-to-Speech, upscale любого файла, Seedance extend.

## Автоконтур «Успех 888»

Крутится **внутри** `videobot.service` как фоновая задача бота, интервал `NIGHT_INTERVAL_MINUTES` (по умолчанию 90 мин). Отдельный `videobot-night.timer` **не нужен и не включать**.

Цикл тика: идеи (Grok) → до `NIGHT_BATCH_PER_TICK` видео (по умолчанию 1) → очередь на постинг с да/нет в Telegram. Дневной потолок — `VIDEOS_PER_NIGHT`. Пока лимит не набран, тики продолжаются весь день.

- Синтетика only: без фото людей, без Act Two, без клона голоса.
- `VIDEOS_PER_NIGHT` — лимит роликов **за день** (не за тик). Разный голос/темп/стиль на аккаунт, round-robin.
- State machine в SQLite: `pending → ideas_ready → generating → video_ready → posting → posted | failed` (+ `wait_confirm` / `publish_unknown` / `manual_review`). Файл пишется сразу; следующий тик не переснимает уже готовое. Stale `generating`/`posting` старше `NIGHT_STALE_MINUTES` снимаются с лока.
- Дедуп идей 21 день (Jaccard, окно 14–30 через `NIGHT_DEDUP_DAYS`) + запрет похожих тем в один день на том же аккаунте.
- По умолчанию публикация только после да/нет (`/night`, кнопки). Полный автопост позже: `/night_mode auto`.
- Один файл не уходит на все 3 аккаунта. Denylist реальных людей и опасных тем. Стоп после нескольких moderation/rejection подряд.
- TikTok: `is_aigc=true`. Timeout → `PUBLISH_UNKNOWN` (resume по publish/container/task ID). Ретраи только 429/5xx/сеть; OAuth/App Review/формат/модерация → `MANUAL_REVIEW`.
- **Замки:** ручная съёмка и автоконтур в одном процессе сериализует `BUSY` (`asyncio.Lock`). `videobot.lock` (fcntl) оставлен только против второго процесса — CLI `night_runner.py --smoke` или случайно включённый старый timer.
- `NIGHT_RUNWAY_DAILY_BUDGET=0` — без потолка кредитов; ненулевое значение останавливает тик, когда сумма за день достигнута.

```bash
python night_runner.py --smoke --no-telegram
```

Деплой `videobot.service` — только после отдельного «ок» владельца. `videobot-night.timer` не ставить.

## Пока не трогаем

Тарифы и лимиты кредитов **на пользователя** — специально в конце: там реальные деньги, ошибка дороже дня ожидания.

Ещё позже: Dubbing, video-to-video (Aleph 2), Brand Kit, редактор сцен, история проектов.
