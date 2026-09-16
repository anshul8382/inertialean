#!/usr/bin/env bash
# Quick sanity check that .env looks production-safe for clean VPS.
set -euo pipefail
APP_DIR="${1:-/opt/Inertia2026v1}"
ENV_FILE="${APP_DIR}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "FAIL: missing ${ENV_FILE}"
  exit 1
fi

ok=1
grep -q '^SECRET_KEY=.\+' "$ENV_FILE" || { echo "FAIL: SECRET_KEY"; ok=0; }
grep -qE '^DB_HOST=(127\.0\.0\.1|localhost)' "$ENV_FILE" || echo "WARN: DB_HOST should be 127.0.0.1 on clean VPS"
grep -qi 'FORCE_2FA_FOR_ALL_USERS=true' "$ENV_FILE" || echo "WARN: set FORCE_2FA_FOR_ALL_USERS=true"
grep -q '^DB_PASSWORD=.\+' "$ENV_FILE" || { echo "FAIL: DB_PASSWORD"; ok=0; }

if [[ "$ok" -eq 1 ]]; then
  echo "OK: basic .env checks passed (${ENV_FILE})"
  exit 0
fi
exit 1
