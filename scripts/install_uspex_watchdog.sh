#!/usr/bin/env bash
# Install uspex-watchdog onto THIS machine (intended for the VPS).
# DO NOT RUN until the human explicitly OKs the watchdog install plan in chat.
# Does NOT touch /opt/uspex code, .env, sqlite, vector.service, or uspex.service.
set -euo pipefail

if [[ "${USPEX_WATCHDOG_I_CONFIRM_INSTALL:-}" != "yes" ]]; then
  echo "REFUSING: set USPEX_WATCHDOG_I_CONFIRM_INSTALL=yes after chat OK" >&2
  echo "This installer copies watchdog files only; it never deploys trading code." >&2
  exit 2
fi
if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
install -o root -g root -m 755 "$SRC/scripts/uspex_watchdog.py" /usr/local/sbin/uspex-watchdog
install -o root -g root -m 644 "$SRC/deploy/systemd/uspex-watchdog.service" /etc/systemd/system/uspex-watchdog.service
install -o root -g root -m 644 "$SRC/deploy/systemd/uspex-watchdog.timer" /etc/systemd/system/uspex-watchdog.timer
install -o root -g root -m 644 "$SRC/deploy/logrotate/uspex-watchdog" /etc/logrotate.d/uspex-watchdog
install -d -o root -g root -m 755 /var/lib/uspex-watchdog
touch /var/log/uspex-watchdog.log
chmod 640 /var/log/uspex-watchdog.log
chown root:adm /var/log/uspex-watchdog.log || chown root:root /var/log/uspex-watchdog.log

systemctl daemon-reload
systemctl enable --now uspex-watchdog.timer
systemctl list-timers uspex-watchdog.timer --no-pager
echo "INSTALLED. History: sudo grep action=restart /var/log/uspex-watchdog.log"
