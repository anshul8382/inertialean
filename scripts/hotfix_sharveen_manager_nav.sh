#!/usr/bin/env bash
# Hotfix prod: manager nav (Sharveen) + dashboard client count + logout URL.
# Run from your Mac (needs SSH to root@66.116.199.231):
#   bash scripts/hotfix_sharveen_manager_nav.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${INERTIA_SSH_HOST:-root@66.116.199.231}"
APP="${INERTIA_APP_DIR:-/opt/Inertia2026v1}"

echo "==> Copying hotfix files to ${HOST}:${APP}"
scp "${ROOT}/services/permission_service.py" "${HOST}:${APP}/services/"
scp "${ROOT}/routes/main.py" "${HOST}:${APP}/routes/"
scp "${ROOT}/templates/base.html" "${HOST}:${APP}/templates/"
scp "${ROOT}/templates/base_clean.html" "${HOST}:${APP}/templates/"
scp "${ROOT}/templates/partials/mobile/more_menu.html" "${HOST}:${APP}/templates/partials/mobile/"

echo "==> Restarting inertia-2026v1"
ssh "${HOST}" "systemctl restart inertia-2026v1.service && sleep 2 && systemctl is-active inertia-2026v1.service"
echo "==> Done. Hard-refresh https://inertiainvest.in and re-login as sharveen."
