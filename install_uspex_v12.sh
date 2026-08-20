#!/usr/bin/env bash
# Install USPEX PRO DESK V12 (BYBIT DEMO only). Does NOT auto-enable REAL.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="${USPEX_DST:-/opt/uspex}"

if [[ $EUID -ne 0 ]]; then
  echo "запусти от root: sudo bash install_uspex_v12.sh"
  exit 1
fi
if [[ ! -f "$SRC/main_USPEX_PRO_DESK_V12.py" ]]; then
  echo "нет main_USPEX_PRO_DESK_V12.py"
  exit 1
fi
if [[ ! -f "$SRC/.env" ]]; then
  echo "нет .env — скопируй .env.example и заполни ключи DEMO"
  exit 1
fi

# Backup existing DB before migration
if [[ -f "$DST/paper_v8.sqlite3" ]]; then
  cp -a "$DST/paper_v8.sqlite3" "$DST/paper_v8.sqlite3.bak.$(date +%Y%m%d%H%M%S)"
  echo "DB backup created"
fi

mkdir -p "$DST"
cp -f "$SRC/main_USPEX_PRO_DESK_V12.py" "$DST/main.py"
cp -f "$SRC/requirements.txt" "$DST/"
rm -rf "$DST/uspex_core"
cp -R "$SRC/uspex_core" "$DST/uspex_core"
# Never overwrite live .env with example; keep secrets
if [[ ! -f "$DST/.env" ]]; then
  cp -f "$SRC/.env" "$DST/.env"
  chmod 600 "$DST/.env"
fi

python3 -m venv "$DST/venv" 2>/dev/null || true
"$DST/venv/bin/pip" install -r "$DST/requirements.txt"

echo "Файлы V12 скопированы в $DST"
echo "НЕ запускаю systemctl автоматически — подтверди деплой вручную."
echo "После подтверждения: systemctl restart uspex   # или имя твоего unit"
