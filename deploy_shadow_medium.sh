#!/usr/bin/env bash
# Deploy USPEX V12.1 to VPS in SHADOW + prepare MEDIUM canary.
# Usage:
#   export USPEX_HOST=root@217.28.140.122   # or uspex@IP
#   bash deploy_shadow_medium.sh
#
# Does NOT enable REAL trading. Does NOT overwrite remote .env secrets.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOST="${USPEX_HOST:-}"
DST="${USPEX_DST:-/opt/uspex}"

if [[ -z "$HOST" ]]; then
  echo "Задай USPEX_HOST, например:"
  echo "  export USPEX_HOST=root@217.28.140.122"
  echo "  bash deploy_shadow_medium.sh"
  echo
  echo "Локальный SSH pubkey для authorized_keys на сервере:"
  cat "$HOME/.ssh/id_ed25519.pub" 2>/dev/null || echo "(нет ~/.ssh/id_ed25519.pub)"
  exit 2
fi

echo "==> SSH check $HOST"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "echo SSH_OK; hostname; test -d $DST && echo DST_OK || echo DST_MISSING"

echo "==> Backup remote DB if present"
ssh "$HOST" "bash -lc 'mkdir -p $DST; if [[ -f $DST/paper_v8.sqlite3 ]]; then cp -a $DST/paper_v8.sqlite3 $DST/paper_v8.sqlite3.bak.\$(date +%Y%m%d%H%M%S); echo DB_BACKUP_OK; fi'"

echo "==> Sync code (no .env overwrite)"
rsync -az --delete \
  --exclude '.env' \
  --exclude '*.sqlite3' \
  --exclude '*.sqlite3-*' \
  --exclude '.git' \
  --exclude 'presentation' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude 'venv' \
  "$SRC/main_USPEX_PRO_DESK_V12.py" "$HOST:$DST/main.py"
rsync -az --delete "$SRC/uspex_core/" "$HOST:$DST/uspex_core/"
rsync -az "$SRC/requirements.txt" "$SRC/uspex.service" "$HOST:$DST/"

echo "==> Ensure SHADOW + DEMO flags in remote .env (keep secrets)"
ssh "$HOST" "bash -lc '
set -e
ENV=$DST/.env
touch \"\$ENV\"
chmod 600 \"\$ENV\"
# DEMO only
grep -q \"^BYBIT_DEMO=\" \"\$ENV\" && sed -i \"s/^BYBIT_DEMO=.*/BYBIT_DEMO=true/\" \"\$ENV\" || echo BYBIT_DEMO=true >> \"\$ENV\"
# Shadow ON for canary
grep -q \"^USPEX_SHADOW_MODE=\" \"\$ENV\" && sed -i \"s/^USPEX_SHADOW_MODE=.*/USPEX_SHADOW_MODE=1/\" \"\$ENV\" || echo USPEX_SHADOW_MODE=1 >> \"\$ENV\"
# Never enable real
grep -q \"^REAL_TRADING\" \"\$ENV\" && sed -i \"s/^REAL_TRADING.*/REAL_TRADING=false/\" \"\$ENV\" || true
echo ENV_FLAGS:
grep -E \"^(BYBIT_DEMO|USPEX_SHADOW_MODE)=\" \"\$ENV\"
'"

echo "==> Install deps + unit file + restart"
ssh "$HOST" "bash -lc '
set -e
cd $DST
if [[ ! -x venv/bin/pip ]]; then python3 -m venv venv; fi
./venv/bin/pip install -q -r requirements.txt
if [[ -f uspex.service ]]; then
  cp uspex.service /etc/systemd/system/uspex.service 2>/dev/null || sudo cp uspex.service /etc/systemd/system/uspex.service
  systemctl daemon-reload 2>/dev/null || sudo systemctl daemon-reload
fi
# Prefer uspex unit; fall back to common names
if systemctl list-unit-files | grep -q \"^uspex.service\"; then
  systemctl restart uspex || sudo systemctl restart uspex
  systemctl --no-pager --full status uspex | head -25 || sudo systemctl --no-pager --full status uspex | head -25
else
  echo \"WARNING: uspex.service not installed as root; start manually\"
fi
'"

echo
echo "==> DONE"
echo "Shadow ON. В Telegram:"
echo "  1) /admin → Bybit Demo → Shadow toggle = ON (если вдруг выключен)"
echo "  2) Запусти только MEDIUM (СРЕДНИЙ) — canary"
echo "  3) /health — смотри FUNNEL + E2E latency + TTL rejects"
echo "Ордера не отправляются, пока USPEX_SHADOW_MODE=1."
