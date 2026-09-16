#!/bin/bash
# Prune local-only clutter under the app repo (safe for Mac dev copy).
# Does NOT touch application source in routes/, services/, templates/, agents/.
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_ROOT"

echo "Cleaning: $APP_ROOT"

# Duplicate venv
rm -rf .venv

# Runtime
find flask_sessions -type f -delete 2>/dev/null || true
rm -f airflow/airflow.db-shm airflow/airflow.db-wal 2>/dev/null || true

# Python cache (skip venv)
find . -path ./venv -prune -o -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

# Large gitignored logs
rm -f logs/gunicorn_access.log logs/gunicorn_error.log logs/gunicorn_boot.log \
      logs/test_app.log logs/restart_with_error_handling.log 2>/dev/null || true

# Airflow task/scheduler logs (regenerated on next DAG run; never commit)
if [ -d airflow/logs ]; then
  find airflow/logs -mindepth 1 ! -name '.gitkeep' -delete 2>/dev/null || true
fi
rm -f airflow/*.err airflow/*.out 2>/dev/null || true

# SQL backups: keep 6 newest
if [ -d backups ]; then
  (
    cd backups
    ls -t inertia_app2025_*.sql.gz 2>/dev/null | tail -n +7 | while read -r f; do rm -f "$f"; done
    rm -f inertia_app_code_*.tar.gz inertia_app_git_*.bundle _*.sql 2>/dev/null || true
  )
fi

echo "Done. Sizes:"
du -sh backups logs airflow/logs venv _deprecated 2>/dev/null || true
