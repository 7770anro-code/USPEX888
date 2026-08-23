# VideoBot — деплой на VPS (cloud@217.28.140.122)

Изоляция: **не трогать** `/opt/uspex`, `/opt/vector`, `uspex.service`, `vector.service`.
Этот сервис живёт только в `/opt/videobot` + unit `videobot.service`.

Пока нет явного «ок» на выкладку — **не копировать на сервер и не systemctl start**.

## Что должно быть в `/opt/videobot/.env`

```
XAI_API_KEY_NEW=...
ELEVENLABS_API_KEY=...
RUNWAY_API_KEY=...
VIDEOBOT_TELEGRAM_TOKEN=...
```

Токен бота — от @BotFather. Остальные ключи — Cursor Secrets / кабинеты провайдеров.

`XAI_API_KEY_NEW` в Cursor Secrets может быть обёрткой `XAI_API_KEY=xai-...` (96 символов). Бот сам снимает префикс `XAI_API_KEY=` и проверяет, что внутри ключ длиной 84 с префиксом `xai-`. Чистить секрет вручную не нужно.

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

## Ночной пайплайн (после отдельного «ок»)

Изоляция та же: только `/opt/videobot`, units `videobot-night.service` + `videobot-night.timer`.
По умолчанию **shadow** (`NIGHT_RENDER=0`) — кредиты не тратятся.

```bash
sudo cp /opt/videobot/calendar.example.json /opt/videobot/calendar.json
sudo chown videobot:videobot /opt/videobot/calendar.json
# в .env: NIGHT_CALENDAR=/opt/videobot/calendar.json
#         NIGHT_OWNER_CHAT_ID=<telegram chat id владельца>
#         NIGHT_RENDER=0
sudo cp /opt/videobot/videobot-night.service /etc/systemd/system/
sudo cp /opt/videobot/videobot-night.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now videobot-night.timer
sudo systemctl list-timers videobot-night.timer --no-pager
sudo -u videobot /opt/videobot/venv/bin/python /opt/videobot/night_run.py --date 2026-08-24
```

Съёмка ночью — только после явного `NIGHT_RENDER=1` в `.env`. Автопостинг в TikTok/Instagram не включать.

## Roadmap / Backlog (Волна 2)

Не деплоить и не кодить сейчас. Список в [README.md](README.md) § Roadmap: Voice Cloning / Design / STS / Dubbing, act_two, Aleph 2, extend, upscale, Brand Kit, редактор сцен, история, тарифы. Для клонирования голоса и оживления персонажа — то же обязательное согласие кнопкой, что для фото.
