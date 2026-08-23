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

## Ночной пайплайн «Успех 888»

Автономный ночной прогон **без автопостинга**. Готовит вертикальные пакеты 9:16 под ручную загрузку в TikTok и Instagram.

```
календарь JSON → слоты дня → оценка кредитов → shadow-план
                                 ↘ (только NIGHT_RENDER=1 / --render) съёмка
пакет в data/outbox/YYYY-MM-DD/<slot>/  + утренний отчёт в Telegram
```

- Календарь: `calendar.example.json` (на сервере копия `calendar.json`). Фото людей в слотах запрещены.
- По умолчанию **shadow**: сценарий не пишется через Grok, Runway/ElevenLabs не вызываются. В outbox — подписи, хештеги, `meta.json`.
- Съёмка — явный `--render` или `NIGHT_RENDER=1`. Дневной потолок кредитов и `max_jobs` в календаре. Нехватка кредитов — fail-closed, остальные слоты не стартуют.
- Уже `packed` за дату не переснимается без `--force`.
- Замок `data/videobot.lock`: ночь и живой Telegram-бот не снимают одновременно.
- Автопостинг в соцсети **выключен**. Выкладка — руками из outbox.
- Владелец: `/night` в боте. Timer: `videobot-night.timer` (02:15).

```bash
cd videobot
python night_run.py --date 2026-08-24          # shadow
python night_run.py --render --no-telegram     # съёмка, без отчёта
python test_night.py
```

## Пока не трогаем

Тарифы и лимиты кредитов **на пользователя** — специально в конце: там реальные деньги, ошибка дороже дня ожидания.

Ещё позже: автопостинг TikTok/Instagram, Dubbing, video-to-video (Aleph 2), Brand Kit, редактор сцен, история проектов.
