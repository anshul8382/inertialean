#!/usr/bin/env bash
# Compare local lean tree vs Lean VPS app dir (code only — no .env/venvs/uploads).
#
# Usage (Mac):
#   ./scripts/deployment/compare_local_vs_lean_vps.sh
#
# Needs SSH key passphrase interactively.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST="${LEAN_HOST:-anshul@129.121.133.25}"
APP_DIR="${LEAN_APP_DIR:-/opt/Inertia2026v1}"
SSH_KEY="${LEAN_SSH_KEY:-$HOME/.ssh/inertia_vps}"

RSYNC_EXCLUDES=(
  --exclude '.env'
  --exclude '.env.*'
  --exclude 'venv/'
  --exclude 'airflow_venv/'
  --exclude 'uploads/'
  --exclude 'static/agreements/'
  --exclude 'static/uploads/'
  --exclude 'airflow/logs/'
  --exclude 'airflow/airflow.db'
  --exclude 'airflow/*.pid'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.git/'
  --exclude 'logs/'
  --exclude '.DS_Store'
  --exclude 'node_modules/'
  --exclude 'service_account.json'
  --exclude '**/service_account.json'
  --exclude 'instance/tmp/'
  --exclude 'flask_sessions/'
  --exclude '.local/'
  --exclude 'docs/AGENT_APPROVAL_STATUS.md'
  --exclude '.cursor/'
)

SSH_CMD=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes)
RSYNC_SSH="ssh -i ${SSH_KEY} -o IdentitiesOnly=yes"

echo "==> SSH check"
"${SSH_CMD[@]}" "$HOST" "test -d '$APP_DIR' && echo ok_dir"

echo ""
echo "==> Differing files SERVER → would change LOCAL (merge if app code):"
rsync -ani "${RSYNC_EXCLUDES[@]}" -e "$RSYNC_SSH" \
  "$HOST:$APP_DIR/" "$ROOT/" | grep -v '/$' | grep -v '^$' | head -120 || true
echo "(end server→local)"

echo ""
echo "==> Differing files LOCAL → would change SERVER (commit local, then deploy):"
rsync -ani "${RSYNC_EXCLUDES[@]}" -e "$RSYNC_SSH" \
  "$ROOT/" "$HOST:$APP_DIR/" | grep -v '/$' | grep -v '^$' | head -120 || true
echo "(end local→server)"

echo ""
echo "If both blocks are empty → app code matches → commit LOCAL as git baseline."
echo "Never commit .env / venv / uploads."
