#!/usr/bin/env bash
# Trigger all unpaused DAGs once (manual test) and print latest run state.
# Usage on Lean:
#   export AIRFLOW_HOME=/opt/Inertia2026v1/airflow
#   export PATH=/opt/Inertia2026v1/airflow_venv/bin:$PATH
#   cd /tmp
#   bash /opt/Inertia2026v1/scripts/airflow_trigger_all_dags.sh
#   # wait, then:
#   bash /opt/Inertia2026v1/scripts/airflow_trigger_all_dags.sh --status-only

set -euo pipefail

export AIRFLOW_HOME="${AIRFLOW_HOME:-/opt/Inertia2026v1/airflow}"
export PATH="${AIRFLOW_VENV:-/opt/Inertia2026v1/airflow_venv}/bin:${PATH}"
cd /tmp

STATUS_ONLY=0
if [[ "${1:-}" == "--status-only" ]]; then
  STATUS_ONLY=1
fi

mapfile -t DAGS < <(airflow dags list -o plain 2>/dev/null | awk 'NR>1 && $1!="" {print $1}' | sort -u)

if [[ ${#DAGS[@]} -eq 0 ]]; then
  echo "No DAGs found. Is dag-processor running?"
  exit 1
fi

echo "Found ${#DAGS[@]} DAG(s)"

if [[ "$STATUS_ONLY" -eq 0 ]]; then
  echo "Triggering all DAGs (queued; LocalExecutor runs sequentially)..."
  for d in "${DAGS[@]}"; do
    echo "  trigger $d"
    airflow dags trigger "$d" >/dev/null || echo "  WARN: trigger failed for $d"
  done
  echo "Done triggering. Wait 10–30+ minutes, then re-run with --status-only"
fi

echo ""
echo "Latest run state per DAG:"
printf '%-40s %s\n' "dag_id" "latest_state"
printf '%-40s %s\n' "----------------------------------------" "------------"
for d in "${DAGS[@]}"; do
  st=$(airflow dags list-runs "$d" -o plain 2>/dev/null | awk 'NR==2 {print $3}')
  printf '%-40s %s\n' "$d" "${st:-none}"
done
