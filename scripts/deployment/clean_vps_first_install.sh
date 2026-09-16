#!/usr/bin/env bash
# First-time (or rebuild) install on a CLEAN AlmaLinux VPS — no cPanel.
# Run ON THE SERVER after extracting the package to /opt/Inertia2026v1:
#
#   sudo bash /opt/Inertia2026v1/scripts/deployment/clean_vps_first_install.sh
#
# Prerequisites: packages (python3, nginx, mysql/mariadb) already installed,
# DB created, .env present with real secrets. This script: venv, pip, systemd.
#
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
SERVICE_NAME="${INERTIA_SYSTEMD_SERVICE:-inertia-2026v1}"
APP_USER="${INERTIA_APP_USER:-anshul}"
UNIT_SRC="${APP_DIR}/deployment/clean-vps-package/inertia-2026v1.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)."
  exit 1
fi

cd "$APP_DIR"
echo "==> App dir: ${APP_DIR}"

if [[ ! -f .env ]]; then
  echo "ERROR: ${APP_DIR}/.env missing. Copy from .env.example and set production secrets first."
  exit 1
fi

echo "==> Python venv"
if [[ ! -d venv ]]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt

echo "==> Permissions"
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR" || true

echo "==> systemd unit"
if [[ -f "$UNIT_SRC" ]]; then
  cp "$UNIT_SRC" "/etc/systemd/system/${SERVICE_NAME}.service"
  # Ensure paths match
  sed -i "s|/opt/Inertia2026v1|${APP_DIR}|g" "/etc/systemd/system/${SERVICE_NAME}.service"
  sed -i "s|User=anshul|User=${APP_USER}|g" "/etc/systemd/system/${SERVICE_NAME}.service"
  sed -i "s|Group=anshul|Group=${APP_USER}|g" "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}"
  systemctl restart "${SERVICE_NAME}"
  sleep 2
  systemctl --no-pager --full status "${SERVICE_NAME}" || true
else
  echo "WARN: missing ${UNIT_SRC} — create systemd unit manually"
fi

echo "==> Local health (best effort)"
curl -s -o /dev/null -w "health_http:%{http_code}\n" --max-time 10 http://127.0.0.1:5004/api/v1/health \
  || curl -s -o /dev/null -w "health_http:%{http_code}\n" --max-time 10 http://127.0.0.1:5000/api/v1/health \
  || true

echo "==> First install steps done. Next: nginx/proxy, smoke on http://SERVER_IP, rsync uploads, then DNS."
