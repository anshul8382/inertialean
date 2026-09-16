#!/usr/bin/env bash
# Run monthly_salary sales/internal incentive columns migration (additive, idempotent).
#
# Usage (on prod as root):
#   cd /opt/Inertia2026v1
#   bash scripts/deployment/run_monthly_salary_incentive_migration.sh
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
[[ -f migrations/add_monthly_salary_incentive_columns.py ]] || {
  echo "ERROR: migrations/add_monthly_salary_incentive_columns.py not found in ${APP_DIR}" >&2
  exit 1
}

echo "==> Adding monthly_salary.sales_incentive / internal_incentive (if missing)"
_app ./venv/bin/python3 migrations/add_monthly_salary_incentive_columns.py

echo "==> Verify"
_app ./venv/bin/python3 migrations/add_monthly_salary_incentive_columns.py --verify

echo "==> Done"
