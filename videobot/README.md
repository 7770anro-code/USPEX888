# VideoBot

Отдельный Telegram-бот: **идея / пресет / свой текст → вертикальный ролик 20–60 секунд (TikTok 9:16)**.

Не связан с USPEX/Vector. На сервере: `/opt/videobot`, unit `videobot.service`.

## Пайплайн

1. **Grok** (`grok-4.5`, fallback fast) — JSON: `continuity` + 4–6 сцен. Пресет добавляет хук, темп и CTA в бриф. «⚡️ Видео за 1 клик» и авто-вайб: 6 коротких сцен, речь 12–18 слов, клипы ~5 сек, итого 20–30 сек.
2. **ElevenLabs** — TTS, сырой `audio/mpeg`. 21 голос кнопками + клон из SQLite. Подача и скорость — `voice_settings`.
3. **fal.ai** (дефолт, `VIDEO_PROVIDER=fal`, ключ `FAL_KEY`) — очередь `https://queue.fal.run`, заголовок `Authorization: Key $FAL_KEY`, без SDK.
   - Качество в UI: **Быстро** = Seedance 2.5 I2V (`bytedance/seedance-2.5/image-to-video`); **Оптимально** = Kling 3.0 Pro I2V (`fal-ai/kling-video/v3/pro/image-to-video`). Fallback: Kling Standard → Seedance → Kling Pro.
   - Вертикаль `9:16`. Клип 5 или 10 сек. `generate_audio=false` — TTS клеим сами.
   - Без фото: still через Flux Schnell (`fal-ai/flux/schnell`), затем тот же first-frame на каждую сцену (как Seedance I2V на Runway: last-frame chaining не используем).
   - Своё фото: если задан `GEMINI_API_KEY` (Google AI Studio, `gemini-2.5-flash-image` / Nano Banana), кадр сначала чистится там, и в Kling/Seedance I2V идёт уже этот still. Без ключа фото идёт как есть.
   - Картинка в I2V — https URL или data URI. Нативный звук модели выключен.
   - Нехватка кредитов fal.ai — понятный текст в чат (кабинет fal.ai). Resume через sidecar `*.fal_id`.
   - Запасной путь: `VIDEO_PROVIDER=runway` + `RUNWAY_API_KEY` (старый gen4.5 / gen4_turbo). Без явного флага Runway не обязателен.
4. **ffmpeg** — `atempo`, склейка 9:16, субтитры, опциональный водяной знак (текст/лого, вкл/выкл).
5. Перед запуском — оценка: списание в кабинете fal.ai (не фейковые кредиты Runway) + символы ElevenLabs, кнопки **Создать / Отмена**.
6. Готовый ролик уходит двумя файлами: `answer_video` + `answer_document`. «Улучшить качество» — Topaz Proteus (`fal-ai/topaz/upscale/video`) на fal.ai.

## Режимы (/start)

- **Видео за 1 клик** — короткая тема (хук/сценарий/камера сами) → опционально своё фото (**та же кнопка согласия** `consent:yes`) и голос (можно пропустить — Сара) → настройки → оценка стоимости. 6 коротких клипов, ~20–30 сек.
- **Своё фото + текст + голос** — сценарий, фото, **та же кнопка согласия** (`consent:yes`), голос, стоимость. Своё фото тоже прогоняется через Nano Banana, если есть `GEMINI_API_KEY`.
- **Оживить фото** — Act Two (`model=act_two` на Runway): фото + короткое видео мимики. Без `RUNWAY_API_KEY` пункт объясняет, что сейчас камера на fal.ai. Согласие на фото — **та же кнопка**, что в custom-режиме.
- **Клонировать мой голос** — отдельное согласие (не фото) → запись/файл → `POST /v1/voices/add` → `voice_id` в SQLite по `user_id`. Кнопка **«Удалить мой голос»**. То же в Mini App.
- **Студия** — Telegram Mini App (`webapp/`): 1-клик, апскейл/реставрация Topaz, виртуальная примерка одежды, клон голоса. HMAC `initData`, долгие джобы → результат в чат. Без `WEBAPP_PUBLIC_URL` (HTTPS) кнопки живут в обычном меню.
- **Нарезка и монтаж** (`/edit`): **ручной** — таймкоды/порядок и ffmpeg; **авто** — описание → план клипов через xAI API (не браузер grok.com) → ffmpeg. fal.ai/Runway/ElevenLabs не вызываются.
- **Пресеты** — Вирусный TikTok / Реклама товара / Мем / Личный бренд (+ Кино-история). Пользователь пишет только тему.

Фото человека: пайплайн **не стартует** без `consent_verified` (`photo_start_blocked` / `CONSENT_REQUIRED_MSG`).

Ошибки API — текстом в чат. Деплой: [DEPLOY.md](DEPLOY.md).

## Волна 2

SQLite `videobot/data/videobot.sqlite3`: клон голоса, водяной знак, путь к последнему ролику.

- Instant Voice Clone — согласие отдельно от фото, хранение `voice_id` по `user_id`. Нужен платный план ElevenLabs с IVC (на Free API отвечает `paid_plan_required` / `can_not_use_instant_voice_cloning`).
- Act Two с /start, то же согласие что custom-фото (нужен Runway)
- Topaz video/image upscale готового файла и любого вложения (fal.ai)
- Виртуальная примерка одежды (`fal-ai/image-apps-v2/virtual-try-on`), согласие как на фото
- Пресеты задают стиль/темп/голос в бриф Grok
- Оценка до «Создать»: fal.ai, не выдуманные кредиты Runway
- Водяной знак ffmpeg вкл/выкл, без Brand Kit

В «Ещё возможности»: голос по описанию, Speech-to-Speech, Topaz, примерка, Seedance extend (extend — Runway, без ключа недоступен).

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
