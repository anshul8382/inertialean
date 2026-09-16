#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$ROOT/.local"
ENVBIN="$BASE/micromamba/envs/inertia-mysql/bin"
PIDFILE="$BASE/mysql.pid"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Local MySQL already running (pid $(cat "$PIDFILE")) on 127.0.0.1:3306"
  exit 0
fi
if [[ ! -x "$ENVBIN/mysqld" ]]; then
  echo "Missing local MySQL at $ENVBIN/mysqld — reinstall micromamba env first."
  exit 1
fi
"$ENVBIN/mysqld" \
  --datadir="$BASE/mysql-data" \
  --basedir="$BASE/micromamba/envs/inertia-mysql" \
  --port=3306 \
  --socket="$BASE/mysql.sock" \
  --pid-file="$PIDFILE" \
  --log-error="$BASE/mysql.err" \
  --bind-address=127.0.0.1 \
  --mysqlx=0 &
disown
for i in $(seq 1 30); do
  if "$ENVBIN/mysqladmin" --socket="$BASE/mysql.sock" -uroot ping --silent 2>/dev/null; then
    echo "Local MySQL ready on 127.0.0.1:3306 (DB: inertia_app2025_dev)"
    exit 0
  fi
  sleep 1
done
echo "Failed to start — see $BASE/mysql.err"; exit 1
