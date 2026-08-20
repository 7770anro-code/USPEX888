#!/usr/bin/env bash
# Ставит VECTOR отдельно от USPEX. Не трогает /opt/uspex.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/opt/vector

if [[ $EUID -ne 0 ]]; then
  echo "запусти от root: sudo bash install_vector.sh"
  exit 1
fi
if [[ ! -f "$SRC/main.py" ]]; then
  echo "рядом должен лежать main.py"
  exit 1
fi
if [[ ! -f "$SRC/.env" ]]; then
  echo "нет .env — скопируй .env.example в .env и впиши TELEGRAM_BOT_TOKEN"
  exit 1
fi

id -u vector >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin vector
mkdir -p "$DST"
cp -f "$SRC/main.py" "$SRC/requirements.txt" "$SRC/vector.service" "$DST/"
cp -f "$SRC/.env" "$DST/.env"
chmod 600 "$DST/.env"

python3 -m venv "$DST/venv"
"$DST/venv/bin/pip" install --upgrade pip
"$DST/venv/bin/pip" install -r "$DST/requirements.txt"

chown -R vector:vector "$DST"
cp "$DST/vector.service" /etc/systemd/system/vector.service
systemctl daemon-reload
systemctl enable vector
systemctl restart vector
echo "VECTOR поднят отдельно от USPEX."
echo "status: systemctl status vector --no-pager"
echo "log:    journalctl -u vector -f"
