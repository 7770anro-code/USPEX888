# VideoBot — деплой на VPS (cloud@217.28.140.122)

Изоляция: **не трогать** `/opt/uspex`, `/opt/vector`, `uspex.service`, `vector.service`.
Этот сервис живёт только в `/opt/videobot` + unit `videobot.service`.

Выложено **2026-08-26** (WIDE Seedance likeness → Kling, commit `8ba3011`): 422 `partner_validation_failed` на общем плане не рвёт съёмку — та же сцена I2V через Kling. Рестарт **только** `videobot.service` (PID 199353 → **200283**, 09:38 UTC). `uspex.service` PID 193645 и `vector.service` PID 193636 не менялись. `.env` и `data/` не перезаписывали. Resume 6748280112: script/n0/c0/c2/m2/n3/wide_still_3 на месте. `/health` 200, polling `@VideobotAI777_bot`. Неподписанный POST `/api/autorolik` → 403. `videobot-night.timer` не ставили.

Выложено **2026-08-26** (COMPLETED Kling sidecar + pause на continue, commit `9a43d23`): сцена 2 не пересоздаётся — poll COMPLETED, video со статуса если `/response` 405, новый submit не шлём; Kling/Seedance сначала resume, потом upload фото; `credits_paused` не сбрасывается в начале continue. Рестарт **только** `videobot.service` (PID 198912 → **199353**, 09:07 UTC). `uspex.service` PID 193645 и `vector.service` PID 193636 не менялись. `.env` и `data/` не перезаписывали (mtime как были). Resume `/tmp/videobot/6748280112_resume` mtime script/n0–n2/c0/c1/m0/m1/c2.fal_id как были. Живой план: skip m0/m1, сцена 2 Kling (n2 есть, sidecar), 3/5 Seedance, 4/6/7 Kling; wipe=false. `/health` 200, polling `@VideobotAI777_bot`. Неподписанный POST `/api/autorolik` → 403. `videobot-night.timer` не ставили.

Выложено **2026-08-26** (resume 6748280112 не стирается, commit `f8341ac`): «Продолжить съёмку» и Mini App «Снять» при `credits_paused` не зовут `wipe_resume`. Рестарт **только** `videobot.service` (PID 198281 → 198912, 08:57 UTC). `uspex.service` PID 193645 и `vector.service` PID 193636 не менялись. `.env` и `data/` не перезаписывали. Resume `/tmp/videobot/6748280112_resume` mtime script/n0/c0/m0/c2.fal_id как были. План на живом диске: skip m0/m1, сцена 2 Kling (n2 уже есть, sidecar COMPLETED), 3/5 Seedance, 4/6/7 Kling. `/health` 200, polling `@VideobotAI777_bot`. Неподписанный POST `/api/autorolik` → 403. `videobot-night.timer` не ставили.

Выложено **2026-08-26** (старый пайплайн + Авторолик без Runway, commit `8e9ffd4`): клипы 1 клик / своё фото / ночь / вайб / Авторолик FACE+WIDE только Kling+Seedance; poll timeout Kling не резьмится в Runway. Рестарт **только** `videobot.service` (PID 197014 → 198281, 08:48 UTC). `uspex.service` PID 193645 и `vector.service` PID 193636 не менялись этим рестартом. `.env` и `data/` не перезаписывали (`.env` mtime/size как были). Resume `/tmp/videobot/6748280112_resume` на месте: `credits_paused=True`, kind=autorolik, 8 сцен, script.json, n0.mp3, c0/c1, sidecar `c2.mp4.fal_id`. `/health` 200, polling `@VideobotAI777_bot`. Неподписанный POST `/api/autorolik` → 403. `videobot-night.timer` не ставили.

Выложено **2026-08-26** (обрыв загрузки фото Авторолика → 409/retry, не голый 500, commit `17f85a1`): multipart по частям; если Mini App закрыли на upload — уже полученные фото идут в `_spawn`, иначе pending `upload_failed` и JSON 409. Рестарт **только** `videobot.service` (PID 195294 → 197014, 07:34 UTC). `uspex.service` PID 193645 и `vector.service` PID 193636 не менялись этим рестартом. `.env` и `data/` не перезаписывали (pending `6748280112.json` mtime 10:00:50 как был). `/health` 200, polling `@VideobotAI777_bot`. Неподписанный POST `/api/autorolik` → 403. На прод-JS есть `upload_failed` и `X-Telegram-Init-Data`. `videobot-night.timer` не ставили.

Выложено **2026-08-26** (мёртвый pending Авторолика → stale, commit `1813a5c`): status/startup отличают живой воркер от хвоста `scripting`/`shooting`; мёртвый без сценария становится `stale` (форма «Собрать сценарий»), со сценарием — `review`. Рестарт **только** `videobot.service` (PID 193631 → 194488, 07:00 UTC). `uspex.service` PID 193645 и `vector.service` PID 193636 не менялись этим рестартом. `.env` и `data/` не перезаписывали. Sweep на старте: `expire to stale user=6748280112 was=scripting`. 6 фото в `6748280112_photos` на месте. Resume `/tmp/videobot/6748280112_resume` (`credits_paused=True`) не трогали. `/health` 200, polling `@VideobotAI777_bot`. Неподписанный POST `/api/autorolik/script` → 403. На прод-JS есть `stale`. `videobot-night.timer` не ставили.

Выложено **2026-08-26** (чат при закрытом Telegram + правки сцен + обложка «Успех 888», commit `8ef24ba`): Mini App джобы через `_spawn` (не убиваются закрытием вкладки/приложения); сценарий и готовый ролик — обычными сообщениями в чат; в Mini App поля Речь/Кадр по сценам (`POST /api/autorolik/script`); `/start` и шапка меню с `cover.jpg` (Flux Schnell). Рестарт **только** `videobot.service` (PID 191502 → 192443, 05:49 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `/health` 200, `@VideobotAI777_bot` в polling. `GET /cover.jpg` 200 (135419 байт). Неподписанные POST `/api/autorolik/script` и `/shoot` → 403. На прод-HTML есть `go-auto-save` и `cover.jpg`. `videobot-night.timer` не ставили.

Выложено **2026-08-26** (Mini App «Обновить статус» в Авторолике, commit `dbd16d4`): кнопка на экране «Пишу сценарий…» / ревью / прогресс съёмки вручную дергает `/api/autorolik/status`, показывает время обновления, съёмку не стартует. Рестарт **только** `videobot.service` (PID 191258 → 191502, 05:16 UTC; чистое состояние перед попыткой владельца было 190318 → 191258 в 05:12). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `/health` 200, `@VideobotAI777_bot` в polling. Неподписанный POST `/api/autorolik/status` → 403. На прод-HTML есть `go-auto-refresh`.

Выложено **2026-08-26** (resume «Продолжить съёмку» не стирает диск, commit `4300f0c`): клик в чате снова ставит `credits_paused`, подхватывает `photo_file_ids` и `user_photo_*.jpg` на диске. Рестарт **только** `videobot.service` (PID 189659 → 190318, 04:57 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. Папка resume на месте: script.json, n0.mp3, 6 фото, клипов 0.

Выложено **2026-08-26** (Авторолик сценарий/съёмка внутри Mini App, commit `1633eb3`): после «Собрать сценарий» сценарий, «Снять/Правки/Отмена» и прогресс по сценам остаются в Mini App; в чат — готовое видео. Рестарт **только** `videobot.service` (PID 187389 → 189659, 04:51 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `/health` 200, `@VideobotAI777_bot` в polling. Неподписанные POST `/api/autorolik`, `/status`, `/shoot` → 403. На прод-HTML есть `go-auto-shoot`.

Выложено **2026-08-26** (hotfix Kling `reference_image_urls` + текст 422, commit `1760a95`): elements = `frontal_image_url` + `reference_image_urls` (тот же https); FACE не идёт в Seedance; `fal_fail_error` показывает `detail[].msg`; после сбоя камеры — «Продолжить съёмку», не меню Mini App. Рестарт **только** `videobot.service` (PID 187107 → 187389, 01:38 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. Съёмка 01:31: Kling HTTP 422 (`Either frontal_image_url and reference_image_urls or video_url must be provided`, https уже был), Seedance HTTP 422 `content_policy_violation` / likenesses, чат голое «fal.ai не смог выполнить задачу». Готового клипа fal не было — GPU скорее всего не шёл.

Выложено **2026-08-26** (hotfix Kling elements https, commit `fd8bca4`): `frontal_image_url` через fal storage, не data URI; 422 не резьмится как Seedance/Runway. Рестарт **только** `videobot.service` (PID 186541 → 187107, 01:27 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. Съёмка 01:20/01:22: Kling HTTP 422 (data URI в elements), Seedance не слался (resume того же job), Runway 400 no credits. Готового клипа fal не было.

Выложено **2026-08-26** (hotfix sendPhoto >10 МБ, commit `c6ebae3`): ffmpeg-сжатие превью до лимита Telegram. Рестарт **только** `videobot.service` (PID 186193 → 186541, 01:08 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, Mini App `/health` 200. На прод-коде compress + маппинг «too big for a photo» — ok. Kling/Seedance на упавшей попытке 01:01/01:03 **не вызывались**.

Выложено **2026-08-26** (hotfix HMAC Mini App, commit `73ef903`): `signature` снова в data-check-string. Рестарт **только** `videobot.service` (PID 185597 → 186193, 00:59 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, Mini App `/health` 200. На прод-коде: HMAC с `signature` ок, официальный вектор Telegram ок, неподписанный `POST /api/autorolik` → 403.

Выложено **2026-08-26** (после «ok»: Авторолик FACE Kling / WIDE Seedance + «AI generated»): код ветки `cursor/videobot-miniapp-17b5` (commit `569d3ec`) в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 184838 → 185597, 00:28 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали (`WEBAPP_PUBLIC_URL` остался). `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, Mini App `127.0.0.1:8088` (`/health` 200), 7 карточек включая Авторолик. Неподписанный `POST /api/autorolik` → 403.

Выложено **2026-08-25** (после «ок»: Kling/Seedance на fal.ai + Mini App): код ветки `cursor/fal-kling-miniapp-00ae` в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 169141 → 182509, 23:47 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали (`FAL_API_KEY` уже был). `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, Mini App локально `127.0.0.1:8088` (`/health` 200). Без `WEBAPP_PUBLIC_URL` кнопка «🎬 Открыть меню» остаётся callback. Автоконтур стартовал (45 с → каждые 90 мин).

Выложено **2026-08-25** (после «ок»: Nano Banana перед Runway + динамичный монтаж 1-клик/вайб): код ветки `cursor/night-pipeline-00ae` снова в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 161342 → 169141, 08:37 UTC). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, автоконтур стартовал (45 с → каждые 90 мин). `GEMINI_API_KEY` уже был в прод `.env`.

Выложено **2026-08-24** (после «ок»: авто-сценарий 1-клик = стандарт «Лестница Микро»): код ветки `cursor/night-pipeline-00ae` снова в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 160726 → 161342). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, автоконтур стартовал (45 с → каждые 90 мин).

Выложено **2026-08-24** (после «ок»: опциональные фото+голос в «1 клик»): код ветки `cursor/night-pipeline-00ae` снова в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 157325 → 160726). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, автоконтур стартовал (45 с → каждые 90 мин).

Выложено **2026-08-24** (после «ок»: IVC-ошибки, опциональный Model Router, resume после кредитов Runway): код ветки `cursor/night-pipeline-00ae` снова в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 154786 → 157325). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, автоконтур стартовал (45 с → каждые 90 мин).

Выложено **2026-08-24** (hotfix цикла правок после «ок»): код с защитой финала и extra_brief 4000 снова в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 153447 → 154786). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили.

Выложено **2026-08-24** (повторно, после «ок» на качество сценария / короткую тему / цикл правок): код ветки `cursor/night-pipeline-00ae` снова скопирован в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 150351 → 153447). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. Бот `@VideobotAI777_bot` снова в polling, автоконтур стартовал (45 с → каждые 90 мин).

Выложено **2026-08-24** на `/opt/videobot` после первого «ок» владельца. Рестарт только `videobot.service`. `uspex.service` / `vector.service` не трогали. `videobot-night.timer` не ставили.

## Что должно быть в `/opt/videobot/.env`

```
XAI_API_KEY_NEW=...
ELEVENLABS_API_KEY=...
VIDEOBOT_TELEGRAM_TOKEN=...
FAL_API_KEY=...             # https://fal.ai/dashboard/keys  (дефолт камеры; читается и как FAL_KEY)
# FAL_KEY=...               # тот же ключ, другое имя — достаточно одного
# VIDEO_PROVIDER=fal
# запасной путь, если явно вернуть старую камеру:
# VIDEO_PROVIDER=runway
# RUNWAY_API_KEY=...
# Mini App (HTTPS снаружи, nginx → 127.0.0.1:8088):
# WEBAPP_PUBLIC_URL=https://example.com/studio/
# WEBAPP_HOST=127.0.0.1
# WEBAPP_PORT=8088
# опционально, своё фото перед I2V:
# GEMINI_API_KEY=...   # Google AI Studio, https://aistudio.google.com/apikey
```

Токен бота — от @BotFather. Остальные ключи — Cursor Secrets / кабинеты провайдеров.

`XAI_API_KEY_NEW` в Cursor Secrets может быть обёрткой `XAI_API_KEY=xai-...` (96 символов). Бот сам снимает префикс `XAI_API_KEY=` и проверяет, что внутри ключ длиной 84 с префиксом `xai-`. Чистить секрет вручную не нужно.

Дефолт камеры — fal.ai (Kling 3.0 Pro / Seedance 2.5). Нужен `FAL_API_KEY` или `FAL_KEY` (один ключ на Kling и Seedance), пока `VIDEO_PROVIDER` не `runway`. Значение ключа не придумывать, в git и чаты не класть — только в `/opt/videobot/.env` на VPS.

Mini App — отдельная страница в процессе бота (`WEBAPP_HOST:WEBAPP_PORT`). Telegram открывает её кнопкой `web_app` только по HTTPS `WEBAPP_PUBLIC_URL` (nginx → 127.0.0.1:8088). Файлы и долгие джобы идут HMAC POST на `/api/*`, результат — сообщением бота в чат (`sendData` не подходит). Без URL кнопка «🎬 Открыть меню» остаётся обычным callback. Пример nginx (не включать без домена и сертификата):

```
location /studio/ {
    proxy_pass http://127.0.0.1:8088/;
    proxy_set_header Host $host;
    proxy_read_timeout 3600;
    client_max_body_size 42m;
}
```

Опционально (не включать без slug конфига): `RUNWAY_USE_MODEL_ROUTER=1` и `RUNWAY_ROUTER_CONFIG_ID=<slug>` с https://dev.runwayml.com/model-routers. Запасной путь `VIDEO_PROVIDER=runway`.

Нехватка кредитов fal.ai — текст в чат, кабинет fal.ai. Старый resume Runway (`{WORK_DIR}/{chat_id}_resume`) жив при `VIDEO_PROVIDER=runway`.

Клон голоса: MiniMax `fal-ai/minimax/voice-clone` (речь ≥10 сек). Запас ElevenLabs `POST /v1/voices/add` требует план с Instant Voice Clone. На тарифе без IVC API отвечает `paid_plan_required` / `can_not_use_instant_voice_cloning` — это не баг ключа TTS.

## Команды по SSH (один раз, после «ок»)

С ноутбука, из корня репозитория (ветка с папкой `videobot/`):

```bash
ssh cloud@217.28.140.122
```

На сервере:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip ffmpeg fonts-dejavu-core

# отдельный пользователь, не uspex и не vector
sudo useradd --system --home /opt/videobot --shell /usr/sbin/nologin videobot || true
sudo mkdir -p /opt/videobot /tmp/videobot
sudo chown -R videobot:videobot /opt/videobot /tmp/videobot
```

Скопировать код (с машины, где есть git checkout). **Не** класть в `/opt/uspex`.

```bash
# пример: с локальной машины
rsync -av --exclude venv --exclude .env videobot/ cloud@217.28.140.122:/tmp/videobot-src/
ssh cloud@217.28.140.122 'sudo rsync -av /tmp/videobot-src/ /opt/videobot/ && sudo chown -R videobot:videobot /opt/videobot'
```

Или, если репозиторий уже на сервере в домашней папке `cloud`:

```bash
sudo rsync -av --exclude venv --exclude .env ~/USPEX888/videobot/ /opt/videobot/
sudo chown -R videobot:videobot /opt/videobot
```

venv и зависимости:

```bash
sudo -u videobot python3 -m venv /opt/videobot/venv
sudo -u videobot /opt/videobot/venv/bin/pip install -U pip
sudo -u videobot /opt/videobot/venv/bin/pip install -r /opt/videobot/requirements.txt
```

`.env` (права только на владельца):

```bash
sudo cp /opt/videobot/.env.example /opt/videobot/.env
sudo nano /opt/videobot/.env   # вписать четыре ключа
sudo chown videobot:videobot /opt/videobot/.env
sudo chmod 600 /opt/videobot/.env
```

systemd:

```bash
sudo cp /opt/videobot/videobot.service /etc/systemd/system/videobot.service
sudo systemctl daemon-reload
sudo systemctl enable videobot.service
sudo systemctl start videobot.service
sudo systemctl status videobot.service --no-pager
sudo journalctl -u videobot.service -n 80 --no-pager
```

Проверка ffmpeg: `ffmpeg -version`.

## Если бот не стартует

```bash
sudo journalctl -u videobot.service -n 150 --no-pager
```

Типичное:

- `Нет секретов: ...` — пустой `/opt/videobot/.env`
- `нет ffmpeg` — `sudo apt-get install -y ffmpeg`
- Telegram 401 — неверный `VIDEOBOT_TELEGRAM_TOKEN`
- fal.ai 401 — неверный `FAL_KEY` (в API это `Authorization: Key …`, кабинет https://fal.ai/dashboard/keys)
- Runway 401 — неверный `RUNWAY_API_KEY` (только если `VIDEO_PROVIDER=runway`)

Перезапуск **только** этого unit:

```bash
sudo systemctl restart videobot.service
```

Не выполнять `systemctl restart uspex` / `vector`.

## Автоконтур внутри videobot.service (после отдельного «ок»)

Генерация idea→video идёт в процессе бота, не отдельным timer. `videobot-night.timer` **не копировать и не enable**.

Первая неделя: `NIGHT_REQUIRE_CONFIRM=1`, `NIGHT_AUTOPOST=0` — публикация только после да/нет в Telegram (`/night`). Интервал по умолчанию 90 мин (`NIGHT_INTERVAL_MINUTES`). Дневной лимит роликов — `VIDEOS_PER_NIGHT`. Чтобы снимать чаще весь день, поднимите лимит в `.env`.

```bash
# не делать:
# sudo systemctl enable --now videobot-night.timer
sudo -u videobot /opt/videobot/venv/bin/python /opt/videobot/night_runner.py --smoke --no-telegram
```

Токены TikTok/Instagram владелец сам кладёт в `/opt/videobot/.env` (имена `NIGHT_ACCn_*`). В логах значения не пишутся.

## Roadmap / Backlog (Волна 2)

Не деплоить и не кодить сейчас. Список в [README.md](README.md) § Roadmap: Voice Cloning / Design / STS / Dubbing, act_two, Aleph 2, extend, upscale, Brand Kit, редактор сцен, история, тарифы. Для клонирования голоса и оживления персонажа — то же обязательное согласие кнопкой, что для фото.
