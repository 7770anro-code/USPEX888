# Smoke 2026-08-24 (актуальный код ветки, не прод)

Повторный сквозной прогон **после** трёх крупных изменений: фон внутри живого бота, ручная нарезка/склейка, авто-монтаж через xAI. Постинг в соцсети **не выполнялся**.

**Деплой 2026-08-26 (17):** обрыв загрузки фото Авторолика (`17f85a1`). Рестарт только `videobot.service` (PID 195294 → 197014, 07:34 UTC). USPEX 193645 и VECTOR 193636 без изменений. `python3 test_parse.py` — ok. `smoke_rollout.py` без `--live` — SMOKE OK. Live fal-кредиты не жгли.

**Деплой 2026-08-26 (15):** resume «Продолжить съёмку» (`4300f0c`). Рестарт только `videobot.service` (PID 189659 → 190318, 04:57 UTC). USPEX 91832 и VECTOR 67680 без изменений. На диске resume: 8 сцен, 1 озвучка, 6 фото, 0 клипов, `credits_paused=true`. `python3 test_parse.py` — ok.

**Деплой 2026-08-26 (14):** Авторолик внутри Mini App (`1633eb3`). Рестарт только `videobot.service` (PID 187389 → 189659, 04:51 UTC). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `/health` 200. Неподписанные POST `/api/autorolik` `/status` `/shoot` → 403. На прод-странице «Снять» (`go-auto-shoot`) и текст «Готовое видео придёт в чат». `python3 test_parse.py` — ok. `smoke_rollout.py` без `--live` — SMOKE OK. Live fal-кредиты не жгли.

**Деплой 2026-08-26 (13):** hotfix Kling `reference_image_urls` + текст 422 (`1760a95`). Рестарт только `videobot.service` (PID 187107 → 187389, 01:38 UTC). USPEX 91832 и VECTOR 67680 без изменений. Съёмка 01:31: Kling submit `01a03bb1-c3cb-7f91-bb7c-95f2ef332f6f` + HTTP 422 за ~1 с (`Either frontal_image_url and reference_image_urls or video_url must be provided`, https `v3b.fal.media` уже был). Seedance submit `01a03bb1-cbb2-7262-9d1e-92e75cd77c59` + HTTP 422 `content_policy_violation` / likenesses / `partner_validation_failed`. Чат: голое «fal.ai не смог выполнить задачу» (`fal_fail_error` не брал JSON `msg`). GPU-инференс скорее всего не шёл. `python3 test_parse.py` — ok. `smoke_rollout.py` без `--live` — SMOKE OK.

**Деплой 2026-08-26 (12):** hotfix Kling `elements` https (`fd8bca4`). Рестарт только `videobot.service` (PID 186541 → 187107). USPEX 91832 и VECTOR 67680 без изменений. Съёмка 01:20 и 01:22: Kling submit + HTTP 422 (`frontal_image_url` = data URI), Seedance не submit (poll того же request_id), Runway 400 no credits. Готового I2V не было — GPU-инференс скорее всего не шёл.

**Деплой 2026-08-26 (11):** hotfix sendPhoto >10 МБ (`c6ebae3`): сжатие превью ffmpeg. Рестарт только `videobot.service` (PID 186193 → 186541). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. Прод-попытки 01:01 и 01:03 упали на Telegram Bad Request (11 308 176 байт / лимит 10 485 760) **до** Grok и **до** fal. Kling/Seedance не вызывались.

**Деплой 2026-08-26 (10):** hotfix HMAC Mini App (`73ef903`): `signature` входит в hash. Рестарт только `videobot.service` (PID 185597 → 186193). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `videobot-night.timer` не ставили. На прод-коде HMAC с signature + официальный вектор Telegram — ok. Неподписанный POST `/api/autorolik` → 403. `/health` 200. `@VideobotAI777_bot` в polling.

**Деплой 2026-08-26 (9):** после «ok» на Авторолик (FACE Kling / WIDE Seedance, 1–6 фото, сценарий в чате) и пометку «AI generated» код ветки `cursor/videobot-miniapp-17b5` (`569d3ec`) выложен в `/opt/videobot`. Рестарт только `videobot.service` (PID 184838 → 185597). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling. Mini App `/health` 200, 7 карточек, публичная страница отдаёт Авторолик. `python3 test_parse.py` — ok. Live fal-кредиты на Авторолик не жгли.

**Деплой 2026-08-25 (8):** после «ок» на Kling/Seedance (fal.ai) + Mini App код ветки `cursor/fal-kling-miniapp-00ae` выложен в `/opt/videobot`. Рестарт только `videobot.service` (PID 169141 → 182509). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling. Mini App `/health` 200 на `127.0.0.1:8088`. Перед деплоем: `smoke_rollout.py` (Grok/TTS/монтаж) и `--live-only` на VPS из `/tmp` (Flux + Kling 3с + Seedance 4с).

**Деплой 2026-08-25 (7):** после «ок» на Nano Banana (Gemini 2.5 Flash Image) перед Runway и более частую нарезку 1-клик/вайб (6×~5 сек, 20–30 сек). Рестарт только `videobot.service` (PID 161342 → 169141). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling.

**Деплой 2026-08-24 (6):** после «ок» на ужесточение авто-сценария «1 клик» (хук 8–14 слов, 18–28, энергичная камера синтетики, grok-4.5). Рестарт только `videobot.service` (PID 160726 → 161342). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling.

**Деплой 2026-08-24 (5):** после «ок» на опциональные фото+голос в «1 клик». Рестарт только `videobot.service` (PID 157325 → 160726). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling.

**Деплой 2026-08-24 (4):** после «ок» на IVC/роутер/resume кредитов. Рестарт только `videobot.service` (PID 154786 → 157325). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling.

**Деплой 2026-08-24 (3):** hotfix цикла «Улучшить качество» (финал закрывает правки, extra_brief 4000). Рестарт только `videobot.service` (PID 153447 → 154786). USPEX 91832 и VECTOR 67680 без изменений. `.env` и `data/` не трогали. `videobot-night.timer` не ставили.

**Деплой 2026-08-24 (2):** после «ок» на хук/энергию/grok-4.5, короткую тему и цикл «Улучшить качество» код снова выложен в `/opt/videobot`. Рестарт только `videobot.service` (PID 150351 → 153447). `uspex.service` PID 91832 и `vector.service` PID 67680 без изменений. `.env` и SQLite/`data/` не трогали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling. `XAI_MODEL` в прод `.env` по-прежнему fast (27 символов); идеи и сценарии берутся через `XAI_CREATIVE_MODEL` default `grok-4.5`.

**Деплой 2026-08-24:** код ветки `cursor/night-pipeline-00ae` скопирован в `/opt/videobot`, рестарт только `videobot.service` (PID сменился). `uspex.service` и `vector.service` остались active с теми же PID. `videobot-night.timer` не ставили. Существующий `/opt/videobot/.env` не перезаписывали — дописали только отсутствующие ключи автоконтура. SQLite с клонами голоса сохранили. Бот `@VideobotAI777_bot` в polling, автоконтур стартовал (интервал 90 мин, batch=1, лимит 3/день). `NIGHT_OWNER_CHAT_ID` в `.env` по-прежнему пуст — кнопки «да/нет» в Telegram не уйдут, пока владелец не пропишет свой chat id.

Путь автоконтура тот же, что у фоновой задачи: `night_runner.run_night(smoke=True, notify=False)` — это та же функция, что вызывает `auto_pipeline_loop` внутри `videobot.service`.

## 1. Полный цикл idea → видео (фон)

- Grok (`grok-4.5`) собрал 5 идей.
- Аккаунт `motiv`, идея «Одна кнопка утра» / «Одна кнопка «Начать»».
- Runway `gen4.5`: still + 4 клипа image-to-video, 9:16. Resume не понадобился.
- **ElevenLabs — подтверждено в этом прогоне (не из старого файла):**
  - 4 вызова TTS, голос Уилл (`bIHbv24MWmeRgasZH58o`, пресет аккаунта, не клон).
  - Сырые mp3: 111 221 / 131 701 / 117 908 / 110 385 байт.
  - В БД: ElevenLabs ≈ **338** символов.
  - В готовом mp4 есть аудиодорожка **aac** stereo 44100 Hz, длительность **40.05 с**.
  - `volumedetect`: mean **−27.2 dB**, max **−5.7 dB** — это речь, не тишина (тишина была бы около −91 dB).
- Файл: `720×1280` h264+aac, **40.0 с**, 6.1 МБ, `videobot/data/outbox/2026-08-24/motiv/1.mp4`.
- Статус в БД: `wait_confirm`.
- Оценка Runway ≈ **205** кредитов.

Перед прогоном в SQLite агента нашлась **прототипная** схема `night_jobs`/`night_runs` (слоты `slot_id`, без `id`/`account_id`). `ensure()` теперь переименовывает такие таблицы и создаёт текущую. Без этого INSERT живого автоконтура упал бы. Починено в этом же PR (`night_store.py`).

## 2. Ручная нарезка и склейка (`/cut`, `/edit`)

Тестовое видео 20 с, 720×1280, с тоном 440 Hz (звук проверялся так же, как у готового ролика). Вызваны те же функции, что бот: `cut_video` / `concat_videos`.

- Вырез 1.0–5.0 с → 4.0 с, aac, mean −24.1 dB.
- Вырез 8.0–13.0 с → 5.0 с.
- Склейка двух кусков → **9.06 с**, aac, mean −24.1 dB, звук на месте.

## 3. Авто-монтаж по описанию (xAI → ffmpeg)

Бриф: «2–3 куска, суммарно 8–12 секунд, без первых и последних двух секунд».

- План от **Grok** (официальный xAI API, не эвристика, не браузер): клипы 2.5–5.5, 8.0–12.0, 14.0–17.5.
- `render_clips` → **10.56 с**, aac, mean −24.1 dB, звук на месте.

## Что не готово (без изменений)

- Автопостинг: нет `NIGHT_ACC1_TIKTOK_ACCESS_TOKEN`, `NIGHT_ACC1_IG_ACCESS_TOKEN`, `NIGHT_ACC1_IG_USER_ID` (и то же для acc2/acc3).
- `NIGHT_AUTOPOST=0`, `NIGHT_REQUIRE_CONFIRM=1`.
- `NIGHT_OWNER_CHAT_ID` не задан — кнопки в Telegram в этот прогон не слались.
- Живой Telegram `/cut` в чате не открывался: прогон функций бота на ffmpeg/xAI.

Секреты и значения токенов в этот файл не входят.
