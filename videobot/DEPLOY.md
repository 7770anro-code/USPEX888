# VideoBot — деплой на VPS (cloud@217.28.140.122)

Изоляция: **не трогать** `/opt/uspex`, `/opt/vector`, `uspex.service`, `vector.service`.
Этот сервис живёт только в `/opt/videobot` + unit `videobot.service`.

Выложено **2026-08-24** (после «ок»: опциональные фото+голос в «1 клик»): код ветки `cursor/night-pipeline-00ae` снова в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 157325 → 160726). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, автоконтур стартовал (45 с → каждые 90 мин).

Выложено **2026-08-24** (после «ок»: IVC-ошибки, опциональный Model Router, resume после кредитов Runway): код ветки `cursor/night-pipeline-00ae` снова в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 154786 → 157325). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. `@VideobotAI777_bot` в polling, автоконтур стартовал (45 с → каждые 90 мин).

Выложено **2026-08-24** (hotfix цикла правок после «ок»): код с защитой финала и extra_brief 4000 снова в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 153447 → 154786). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили.

Выложено **2026-08-24** (повторно, после «ок» на качество сценария / короткую тему / цикл правок): код ветки `cursor/night-pipeline-00ae` снова скопирован в `/opt/videobot`. Рестарт **только** `videobot.service` (PID 150351 → 153447). `uspex.service` PID 91832 и `vector.service` PID 67680 не менялись. `.env` и `data/` не перезаписывали. `videobot-night.timer` не ставили. Бот `@VideobotAI777_bot` снова в polling, автоконтур стартовал (45 с → каждые 90 мин).

Выложено **2026-08-24** на `/opt/videobot` после первого «ок» владельца. Рестарт только `videobot.service`. `uspex.service` / `vector.service` не трогали. `videobot-night.timer` не ставили.

## Что должно быть в `/opt/videobot/.env`

```
XAI_API_KEY_NEW=...
ELEVENLABS_API_KEY=...
RUNWAY_API_KEY=...
VIDEOBOT_TELEGRAM_TOKEN=...
```

Токен бота — от @BotFather. Остальные ключи — Cursor Secrets / кабинеты провайдеров.

`XAI_API_KEY_NEW` в Cursor Secrets может быть обёрткой `XAI_API_KEY=xai-...` (96 символов). Бот сам снимает префикс `XAI_API_KEY=` и проверяет, что внутри ключ длиной 84 с префиксом `xai-`. Чистить секрет вручную не нужно.

Опционально (не включать без slug конфига): `RUNWAY_USE_MODEL_ROUTER=1` и `RUNWAY_ROUTER_CONFIG_ID=<slug>` с https://dev.runwayml.com/model-routers. По умолчанию прямой `gen4.5` / `gen4_turbo`. Veo/Gemini — тот же ключ, в UI не выводим (A/B 2026-08-24: без явного выигрыша). При необходимости `RUNWAY_MODEL` / `RUNWAY_STILL_MODEL`.

Нехватка кредитов Runway: ручная съёмка паузится в `/tmp/videobot/{chat_id}_resume` (или `WORK_DIR`). Кнопка «Продолжить съёмку» доснимает с места остановки. «Обновить статус» только GET, без новых задач.

Клон голоса: `POST /v1/voices/add` требует план ElevenLabs с Instant Voice Clone. На тарифе без IVC API отвечает `paid_plan_required` / `can_not_use_instant_voice_cloning` — это не баг ключа TTS.

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
- Runway 401 — неверный `RUNWAY_API_KEY` (в API это Bearer, как `RUNWAYML_API_SECRET` из кабинета dev.runwayml.com)

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
