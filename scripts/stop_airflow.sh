#!/bin/bash
# Stop Airflow API server + scheduler (and legacy webserver if present).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${INERTIA_APP_DIR:-$(dirname "$SCRIPT_DIR")}"
AIRFLOW_HOME="${AIRFLOW_HOME:-$APP_DIR/airflow}"

stop_pidfile() {
  local f="$1"
  if [ -f "$f" ]; then
    local pid
    pid="$(cat "$f" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      echo "Stopped PID $pid ($f)"
    fi
    rm -f "$f"
  fi
}

stop_pidfile "$AIRFLOW_HOME/api_server.pid"
stop_pidfile "$AIRFLOW_HOME/scheduler.pid"
stop_pidfile "$AIRFLOW_HOME/airflow-webserver.pid"
stop_pidfile "$AIRFLOW_HOME/webserver.pid"

# Best-effort: pkill only processes for this AIRFLOW_HOME
pkill -f "airflow api-server" 2>/dev/null || true
pkill -f "airflow scheduler" 2>/dev/null || true

echo "Airflow stop attempted (AIRFLOW_HOME=$AIRFLOW_HOME)."
