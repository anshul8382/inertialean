#!/usr/bin/env bash
# Run client.secondary_emails migration on server (additive, idempotent).
#
# Usage (on prod as root):
#   cd /opt/Inertia2026v1
#   bash scripts/deployment/run_client_secondary_emails_migration.sh
#
set -euo pipefail

_deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(cd "${_deploy_dir}/../.." && pwd)}"
APP_USER="${INERTIA_DEPLOY_USER:-inertia}"

_app() {
  if [[ "$(id -u)" -eq 0 ]] && id "${APP_USER}" &>/dev/null; then
    sudo -u "${APP_USER}" "$@"
  else
    "$@"
  fi
}

cd "${APP_DIR}"
[[ -f migrations/add_client_secondary_emails.py ]] || {
  echo "ERROR: migrations/add_client_secondary_emails.py not found in ${APP_DIR}" >&2
  exit 1
}

echo "==> Adding client.secondary_emails (if missing)"
_app ./venv/bin/python3 migrations/add_client_secondary_emails.py

echo "==> Verify"
_app ./venv/bin/python3 migrations/add_client_secondary_emails.py --verify

echo "==> Done"
