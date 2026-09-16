#!/bin/bash
# Run Airflow CLI with correct env. Must NOT use repo root as cwd: ./airflow/ (AIRFLOW_HOME)
# shadows the apache-airflow Python package and breaks imports.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
export AIRFLOW_HOME="${AIRFLOW_HOME:-$APP_DIR/airflow}"
export AIRFLOW_CONFIG="${AIRFLOW_CONFIG:-$AIRFLOW_HOME/airflow.cfg}"
export AIRFLOW__CORE__EXECUTOR="${AIRFLOW__CORE__EXECUTOR:-SequentialExecutor}"
export AIRFLOW__API__PORT="${AIRFLOW__API__PORT:-${AIRFLOW_PORT:-8080}}"
AIRFLOW_BIN="${AIRFLOW_BIN:-/home/inertia/airflow_venv/bin/airflow}"
cd /tmp
exec "$AIRFLOW_BIN" "$@"
