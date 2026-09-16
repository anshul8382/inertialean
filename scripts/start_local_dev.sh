#!/usr/bin/env bash
# Local dev: requires SSH tunnel to server MySQL (see .env DB_PORT, usually 3307).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DB_PORT="${DB_PORT:-3307}"
APP_PORT="${APP_PORT:-5001}"

echo "==> Checking MySQL tunnel on 127.0.0.1:${DB_PORT}..."
if ! nc -z 127.0.0.1 "$DB_PORT" 2>/dev/null; then
  echo ""
  echo "ERROR: No database on 127.0.0.1:${DB_PORT}."
  echo "Open a separate terminal and run (keep it open):"
  echo "  ssh -L ${DB_PORT}:127.0.0.1:3306 root@66.116.199.231"
  echo ""
  exit 1
fi

if lsof -i ":${APP_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port ${APP_PORT} already in use — app may already be running."
  echo "Open http://127.0.0.1:${APP_PORT}/"
  exit 0
fi

export FLASK_ENV=development
echo "==> Starting Flask on http://127.0.0.1:${APP_PORT}/"
exec "$ROOT/venv/bin/python" run_app.py
