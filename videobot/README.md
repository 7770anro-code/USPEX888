# VideoBot

Отдельный Telegram-бот: **идея / пресет / свой текст → вертикальный ролик 20–60 секунд (TikTok 9:16)**.

Не связан с USPEX/Vector. На сервере: `/opt/videobot`, unit `videobot.service`.

## Пайплайн

1. **Grok** (`grok-4.5`, fallback fast) — JSON: `continuity` + 4–6 сцен. Пресет добавляет хук, темп и CTA в бриф. «⚡️ Видео за 1 клик» и авто-вайб: 6 коротких сцен, речь 12–18 слов, клипы ~5 сек, итого 20–30 сек.
2. **Озвучка** — ElevenLabs пресеты или клон **MiniMax** (`fal-ai/minimax/voice-clone` → `fal-ai/minimax/speech-02-hd`). Клон выбирается в списке голосов вместо пресета.
3. **fal.ai** (дефолт, `VIDEO_PROVIDER=fal`, ключ `FAL_KEY` или `FAL_API_KEY`) — очередь `https://queue.fal.run`, заголовок `Authorization: Key $FAL_KEY`, без SDK.
   - Маршрутизация в `provider_router.ROUTING`: своё фото → Kling → Seedance → legacy Runway; синтетика / ночь / вайб монтажа / Авторолик WIDE → Seedance → Kling → legacy Runway; Авторолик FACE → Kling → Seedance → legacy Runway. Убрать `"legacy_runway"` из списка — Runway выключается.
   - Качество в UI: **Быстро** = Seedance 2.5 I2V (`bytedance/seedance-2.5/image-to-video`); **Оптимально** = Kling 3.0 Pro I2V (`fal-ai/kling-video/v3/pro/image-to-video`).
   - Вертикаль `9:16`. Duration — **строка**. `generate_audio=false` (нативная речь плохо с рус/укр). TTS клеим сами.
   - Kling Element Reference: `elements=[{frontal_image_url}]` + `@Element1` в промпте (взаимно исключается с `generate_audio`).
   - Seedance multi-ref: `bytedance/seedance-2.5/reference-to-video`, `@Image1` в промпте (ночь и мультисцен).
   - Без фото: still через Flux Schnell (`fal-ai/flux/schnell`), затем тот же first-frame на каждую сцену.
   - Своё фото: если задан `GEMINI_API_KEY` (Google AI Studio, `gemini-2.5-flash-image` / Nano Banana), кадр сначала чистится там, и в Kling/Seedance I2V идёт уже этот still. Без ключа фото идёт как есть.
   - Lip-sync после TTS: `fal-ai/kling-video/lipsync/audio-to-video` (клип 2–10 с). Ошибка → старый ffmpeg mux.
   - Картинка в I2V — https URL или data URI. Крупное видео — fal storage upload.
   - Нехватка кредитов fal.ai — понятный текст в чат (кабинет fal.ai). Resume через sidecar `*.fal_id`.
   - Запасной путь: `VIDEO_PROVIDER=runway` + `RUNWAY_API_KEY` (старый gen4.5 / gen4_turbo). Без явного флага Runway не обязателен.
4. **ffmpeg** — `atempo`, склейка 9:16, субтитры, опциональный водяной знак (текст/лого, вкл/выкл).
5. Перед запуском — оценка: списание в кабинете fal.ai (не фейковые кредиты Runway) + символы ElevenLabs, кнопки **Создать / Отмена**.
6. Готовый ролик уходит двумя файлами: `answer_video` + `answer_document`. «Улучшить качество» — Topaz Proteus (`fal-ai/topaz/upscale/video`) на fal.ai.

## Режимы (/start)

- **Авторолик** — до 6 фото друзей + согласие → Grok пишет 4–8 сцен UKRAINIAN CORE. `face_scene`: подлежащее = друг крупно/узнаваемо → Kling `@ElementN`; подлежащее = город/машины/толпа/предмет или друг мельком/со спины/частично → Seedance (безопаснее). Один тёплый/контровый цветокор на все сцены, чтобы смена движка не читалась. Сначала подтверждаешь/правишь сценарий в чате, потом съёмка. Голос — Сара или уже сохранённый клон.
- **Видео за 1 клик** — короткая тема (хук/сценарий/камера сами) → опционально своё фото (**та же кнопка согласия** `consent:yes`) и голос (можно пропустить — Сара) → настройки → оценка стоимости. 6 коротких клипов, ~20–30 сек.
- **Своё фото + текст + голос** — сценарий, фото, **та же кнопка согласия** (`consent:yes`), голос, стоимость. Своё фото тоже прогоняется через Nano Banana, если есть `GEMINI_API_KEY`.
- **Оживить фото** — Act Two (`model=act_two` на Runway): фото + короткое видео мимики. Без `RUNWAY_API_KEY` пункт объясняет, что сейчас камера на fal.ai. Согласие на фото — **та же кнопка**, что в custom-режиме.
- **Клонировать мой голос** — отдельное согласие (не фото) → запись/файл ≥10 с → MiniMax `fal-ai/minimax/voice-clone` (`custom_voice_id` с префиксом `mm:` в SQLite). В списке голосов вместо пресета ElevenLabs. Запас: ElevenLabs IVC, если нет `FAL_KEY`.
- **Открыть меню** — Telegram Mini App (`webapp/`), семь категорий:
  1. 🎬 Создать видео — существующие режимы
  2. 🎞 Авторолик — до 6 фото, сценарий в чате, Kling FACE / Seedance WIDE
  3. ✂️ Монтаж — существующий (вайб / своё видео в чате)
  4. ✨ Улучшить — Topaz 4K (`fal-ai/topaz/upscale/*`), слоу-мо (`topaz/interpolate/video`), реставрация фото (`topaz/restore/image`)
  5. 👗 Примерка — `google/virtual-try-on` (фото человека + одежда), то же согласие `consent:yes`
  6. 🎙 Мой голос — MiniMax clone
  7. 📊 Мои видео — последний готовый ролик в чат
  Отдельная страница `webapp/` (HTML/JS), кнопка `web_app`. Общение: HMAC `initData` + HTTP API бота (файлы и долгие джобы). `sendData()` не используем — лимит маленького JSON и закрывает WebApp. Под карточкой одна строка-подсказка; на первом запуске и первом заходе в режим — короткий тултип. Без `WEBAPP_PUBLIC_URL` (HTTPS) кнопка остаётся callback.
- **Нарезка и монтаж** (`/edit`): **ручной** — таймкоды/порядок и ffmpeg; **авто** — описание → план клипов через xAI API (не браузер grok.com) → ffmpeg. fal.ai/Runway/ElevenLabs не вызываются.
- **Пресеты** — Вирусный TikTok / Реклама товара / Мем / Личный бренд (+ Кино-история). Пользователь пишет только тему.

Фото человека: пайплайн **не стартует** без `consent_verified` (`photo_start_blocked` / `CONSENT_REQUIRED_MSG`). Публичных лиц в Авторолик не подставляем — только друзья с согласия. На роликах с реальным фото и в ночном пайплайне один постпродакшн-шаг `apply_ai_generated_disclosure`: оверлей **AI generated** в углу кадра + та же строка в caption поста.

Ошибки API — текстом в чат. Деплой: [DEPLOY.md](DEPLOY.md).

## Волна 2

SQLite `videobot/data/videobot.sqlite3`: клон голоса, водяной знак, путь к последнему ролику.

- Instant Voice Clone — MiniMax на fal.ai (речь 10+ сек), согласие отдельно от фото, хранение `mm:{custom_voice_id}` по `user_id`. Запас ElevenLabs IVC без `FAL_KEY`.
- Act Two с /start, то же согласие что custom-фото (нужен Runway)
- Topaz video/image upscale, interpolate (слоу-мо), restore готового файла и любого вложения (fal.ai)
- Виртуальная примерка одежды (`google/virtual-try-on`), согласие как на фото
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
