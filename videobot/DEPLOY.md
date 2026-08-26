# VideoBot — деплой на VPS (cloud@217.28.140.122)

Изоляция: **не трогать** `/opt/uspex`, `/opt/vector`, `uspex.service`, `vector.service`.
Этот сервис живёт только в `/opt/videobot` + unit `videobot.service`.

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
