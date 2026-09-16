#!/usr/bin/env bash
# Quick smoke checks after cutover or deploy.
#
#   bash scripts/deployment/smoke_prod_health.sh
#   BASE_URL=https://inertiainvest.in bash scripts/deployment/smoke_prod_health.sh
#
set -euo pipefail

BASE_URL="${BASE_URL:-https://inertiainvest.in}"
GUNICORN_URL="${GUNICORN_URL:-http://127.0.0.1:${GUNICORN_PORT:-5000}}"

check() {
  local url="$1"
  local name="$2"
  local code
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 "$url" 2>/dev/null || echo "000")
  if [[ "$code" =~ ^(200|302|401|403)$ ]]; then
    echo "PASS  $name  HTTP $code  $url"
  else
    echo "FAIL  $name  HTTP $code  $url" >&2
    return 1
  fi
}

fail=0
check "${GUNICORN_URL}/api/v1/health" "gunicorn health" || fail=1
check "${BASE_URL}/api/v1/health" "public health" || fail=1
check "${BASE_URL}/auth/login" "login page" || fail=1

if [[ "$fail" -eq 0 ]]; then
  echo "All smoke checks passed."
else
  exit 1
fi
