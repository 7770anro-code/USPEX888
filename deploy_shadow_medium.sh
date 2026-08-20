#!/usr/bin/env bash
# Deploy USPEX V12.2 to VPS in SHADOW + MEDIUM canary.
# Usage:
#   export USPEX_HOST=cloud@217.28.140.122
#   bash deploy_shadow_medium.sh
#
# Does NOT enable REAL trading. Does NOT overwrite remote .env secrets (only flags).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOST="${USPEX_HOST:-cloud@217.28.140.122}"
DST="${USPEX_DST:-/opt/uspex}"
STAGING="/tmp/uspex_deploy_$$"

echo "==> SSH check $HOST"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "echo SSH_OK; hostname; sudo -n test -d $DST && echo DST_OK"

echo "==> Backup remote main + DB"
ssh "$HOST" "sudo -n bash -lc '
set -e
TS=\$(date +%Y%m%d%H%M%S)
cp -a $DST/main.py $DST/main.py.bak.\$TS 2>/dev/null || true
if [[ -f $DST/paper_v8.sqlite3 ]]; then cp -a $DST/paper_v8.sqlite3 $DST/paper_v8.sqlite3.bak.\$TS; echo DB_BACKUP_OK; fi
'"

echo "==> Upload to staging + install as uspex"
ssh "$HOST" "rm -rf $STAGING && mkdir -p $STAGING/uspex_core"
rsync -az "$SRC/main_USPEX_PRO_DESK_V12.py" "$HOST:$STAGING/main.py"
rsync -az --delete --exclude '__pycache__' --exclude '*.pyc' "$SRC/uspex_core/" "$HOST:$STAGING/uspex_core/"
rsync -az "$SRC/requirements.txt" "$SRC/uspex.service" "$HOST:$STAGING/"

ssh "$HOST" "sudo -n bash -lc '
set -e
install -o uspex -g uspex -m 644 $STAGING/main.py $DST/main.py
rm -rf $DST/uspex_core
cp -a $STAGING/uspex_core $DST/uspex_core
chown -R uspex:uspex $DST/uspex_core
install -o uspex -g uspex -m 644 $STAGING/requirements.txt $DST/requirements.txt
install -o root -g root -m 644 $STAGING/uspex.service /etc/systemd/system/uspex.service
rm -rf $STAGING
'"

echo "==> Ensure SHADOW + DEMO + fast vote model (keep secrets)"
ssh "$HOST" "sudo -n bash -lc '
set -e
ENV=$DST/.env
touch \"\$ENV\"
chmod 600 \"\$ENV\"
chown uspex:uspex \"\$ENV\"
set_kv() {
  local k=\"\$1\" v=\"\$2\"
  if grep -q \"^\${k}=\" \"\$ENV\"; then sed -i \"s|^\${k}=.*|\${k}=\${v}|\" \"\$ENV\"
  else echo \"\${k}=\${v}\" >> \"\$ENV\"; fi
}
set_kv BYBIT_DEMO true
set_kv USPEX_SHADOW_MODE 1
set_kv XAI_VOTE_MODEL grok-4-1-fast-non-reasoning
# strip accidental REAL flags if present
grep -q \"^REAL_TRADING\" \"\$ENV\" && sed -i \"s/^REAL_TRADING.*/REAL_TRADING=false/\" \"\$ENV\" || true
# unbuffered logs
grep -q \"^PYTHONUNBUFFERED=\" \"\$ENV\" || echo PYTHONUNBUFFERED=1 >> \"\$ENV\"
echo ENV_FLAGS:
grep -E \"^(BYBIT_DEMO|USPEX_SHADOW_MODE|XAI_VOTE_MODEL|PYTHONUNBUFFERED)=\" \"\$ENV\"
'"

echo "==> Force MEDIUM canary for scanning users"
ssh "$HOST" "sudo -n -u uspex python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/opt/uspex/paper_v8.sqlite3')
print('before', c.execute('select chat_id,scanning,mode,execution_mode from users').fetchall())
c.execute(\"update users set mode='medium' where scanning=1\")
c.commit()
print('after', c.execute('select chat_id,scanning,mode,execution_mode from users').fetchall())
PY"

echo "==> Deps + restart"
ssh "$HOST" "sudo -n bash -lc '
set -e
cd $DST
if [[ ! -x venv/bin/pip ]]; then sudo -u uspex python3 -m venv venv; fi
sudo -u uspex ./venv/bin/pip install -q -r requirements.txt
# ensure unbuffered in unit
grep -q PYTHONUNBUFFERED /etc/systemd/system/uspex.service || sed -i \"/EnvironmentFile/a Environment=PYTHONUNBUFFERED=1\" /etc/systemd/system/uspex.service
systemctl daemon-reload
systemctl restart uspex
sleep 3
systemctl --no-pager --full status uspex | head -25
journalctl -u uspex.service -n 25 --no-pager
'"

echo
echo "==> DONE — V12.2 Institutional Fast AI"
echo "Shadow ON. Mode = MEDIUM. Grok vote model = grok-4-1-fast-non-reasoning."
echo "Тестируй в Telegram через 2–3 минуты после строки READY в логах."
