#!/bin/bash
# Airflow 3: start API server (replaces webserver) + scheduler.
# Scheduler talks to the execution API; both must run.
#
# Paths (override via env):
#   INERTIA_APP_DIR   — app root (default: parent of scripts/)
#   AIRFLOW_VENV      — Airflow venv (default: tries Lean then BigRock locations)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${INERTIA_APP_DIR:-$(dirname "$SCRIPT_DIR")}"
AIRFLOW_HOME="${AIRFLOW_HOME:-$APP_DIR/airflow}"
AIRFLOW_PORT="${AIRFLOW_PORT:-8080}"

if [ -z "${AIRFLOW_VENV:-}" ]; then
  if [ -x "$APP_DIR/airflow_venv/bin/airflow" ]; then
    AIRFLOW_VENV="$APP_DIR/airflow_venv"
  elif [ -x "/home/anshul/airflow_venv/bin/airflow" ]; then
    AIRFLOW_VENV="/home/anshul/airflow_venv"
  elif [ -x "/home/inertia/airflow_venv/bin/airflow" ]; then
    AIRFLOW_VENV="/home/inertia/airflow_venv"
  else
    AIRFLOW_VENV="$APP_DIR/airflow_venv"
  fi
fi

AIRFLOW_CMD="$AIRFLOW_VENV/bin/airflow"

export INERTIA_APP_DIR="$APP_DIR"
export AIRFLOW_HOME="$AIRFLOW_HOME"
export AIRFLOW_CONFIG="${AIRFLOW_CONFIG:-$AIRFLOW_HOME/airflow.cfg}"
export AIRFLOW__CORE__EXECUTOR=SequentialExecutor
export AIRFLOW__CORE__DAGS_FOLDER="$AIRFLOW_HOME/dags"
export AIRFLOW__LOGGING__BASE_LOG_FOLDER="$AIRFLOW_HOME/logs"
export AIRFLOW__API__PORT="$AIRFLOW_PORT"
export PATH="$AIRFLOW_VENV/bin:$PATH"

# Lean VPS: EMAIL_SOURCE_TAG=Lean server in app .env — also load for Airflow failure subjects
if [ -f "$APP_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$APP_DIR/.env"
  set +a
fi

if [ -n "${EMAIL_SOURCE_TAG:-}" ] && [ -f "$AIRFLOW_HOME/email_templates/failure_subject.jinja2" ]; then
  export AIRFLOW__EMAIL__SUBJECT_TEMPLATE="$AIRFLOW_HOME/email_templates/failure_subject.jinja2"
fi

if [ ! -x "$AIRFLOW_CMD" ]; then
    echo "❌ Airflow venv not found at: $AIRFLOW_CMD"
    echo "   Example install:"
    echo "   python3 -m venv $APP_DIR/airflow_venv"
    echo "   $APP_DIR/airflow_venv/bin/pip install 'apache-airflow==3.0.6' apache-airflow-providers-fab \\"
    echo "     --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.0.6/constraints-3.9.txt"
    exit 1
fi

mkdir -p "$AIRFLOW_HOME/logs"

echo "🌐 Starting Airflow API server on port $AIRFLOW_PORT (bind 127.0.0.1)..."
cd /tmp
nohup "$AIRFLOW_CMD" api-server -H 127.0.0.1 -p "$AIRFLOW_PORT" \
  > "$AIRFLOW_HOME/logs/api_server.log" 2>&1 &
API_PID=$!
echo "$API_PID" > "$AIRFLOW_HOME/api_server.pid"
echo "✅ API server PID: $API_PID"

sleep 6

echo "⏰ Starting Airflow scheduler..."
nohup "$AIRFLOW_CMD" scheduler > "$AIRFLOW_HOME/logs/scheduler.log" 2>&1 &
SCHED_PID=$!
echo "$SCHED_PID" > "$AIRFLOW_HOME/scheduler.pid"
echo "✅ Scheduler PID: $SCHED_PID"

echo ""
echo "✅ Airflow 3 components started (APP_DIR=$APP_DIR)."
echo "📊 UI / API: http://127.0.0.1:$AIRFLOW_PORT (SSH tunnel recommended)"
echo "🛑 Stop: $SCRIPT_DIR/stop_airflow.sh"
