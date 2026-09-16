#!/usr/bin/env bash
# Git-based deploy of Inertia2026-lean to the AlmaLinux Lean VPS.
#
# Flow (Mac):
#   1. Commit your changes locally
#   2. ./scripts/deployment/deploy_lean_vps.sh
#        → pushes main to origin
#        → SSHs to VPS, git fetch/pull --ff-only, pip (optional), restart Gunicorn
#
# First-time VPS setup (once): clone this repo into /opt/Inertia2026v1 (or
#   git init + remote add if the tree already exists), keep .env / venv local.
#
# Usage:
#   ./scripts/deployment/deploy_lean_vps.sh
#   ./scripts/deployment/deploy_lean_vps.sh --branch main
#   ./scripts/deployment/deploy_lean_vps.sh --skip-push          # VPS pull only
#   ./scripts/deployment/deploy_lean_vps.sh --skip-pip
#   ./scripts/deployment/deploy_lean_vps.sh --skip-restart
#   ./scripts/deployment/deploy_lean_vps.sh --restart-airflow
#   ./scripts/deployment/deploy_lean_vps.sh --allow-dirty        # push with dirty tree (not recommended)
#
# Env:
#   LEAN_HOST=anshul@129.121.133.25
#   LEAN_APP_DIR=/opt/Inertia2026v1
#   LEAN_SSH_KEY=~/.ssh/inertia_vps
#   LEAN_GUNICORN_UNIT=inertia-2026v1
#   LEAN_BRANCH=main
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST="${LEAN_HOST:-anshul@129.121.133.25}"
APP_DIR="${LEAN_APP_DIR:-/opt/Inertia2026v1}"
SSH_KEY="${LEAN_SSH_KEY:-$HOME/.ssh/inertia_vps}"
GUNICORN_UNIT="${LEAN_GUNICORN_UNIT:-inertia-2026v1}"
BRANCH="${LEAN_BRANCH:-main}"

SKIP_PUSH=0
SKIP_PIP=0
SKIP_RESTART=0
RESTART_AIRFLOW=0
ALLOW_DIRTY=0

usage() {
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --skip-push) SKIP_PUSH=1; shift ;;
    --skip-pip) SKIP_PIP=1; shift ;;
    --skip-restart) SKIP_RESTART=1; shift ;;
    --restart-airflow) RESTART_AIRFLOW=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

SSH=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes)
die() { echo "ERROR: $*" >&2; exit 1; }

cd "$ROOT"
[[ -f "$SSH_KEY" ]] || die "SSH key not found: $SSH_KEY"
[[ -d .git ]] || die "Not a git repo. Run: git init && commit && add origin"
git rev-parse --verify "refs/heads/$BRANCH" >/dev/null 2>&1 \
  || die "Local branch '$BRANCH' not found"

if [[ "$ALLOW_DIRTY" -eq 0 ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "Working tree has uncommitted changes. Commit first or pass --allow-dirty."
  fi
fi

git remote get-url origin >/dev/null 2>&1 || die "No 'origin' remote. Create/push the Cursor (or GitHub) repo first."

echo "==> Git deploy to Lean"
echo "    branch:  $BRANCH"
echo "    remote:  $(git remote get-url origin)"
echo "    target:  ${HOST}:${APP_DIR}"

if [[ "$SKIP_PUSH" -eq 0 ]]; then
  echo "==> git push origin $BRANCH"
  git push -u origin "$BRANCH"
else
  echo "==> Skipping push (--skip-push)"
fi

echo "==> SSH check"
"${SSH[@]}" "$HOST" "test -d '$APP_DIR' && echo ok_dir" || die "Cannot reach $HOST or missing $APP_DIR"

echo "==> Remote: git pull + Lean env tags + restart"
# shellcheck disable=SC2087
"${SSH[@]}" "$HOST" bash -s <<EOF
set -euo pipefail
cd '$APP_DIR'

if [[ ! -d .git ]]; then
  echo "ERROR: $APP_DIR is not a git checkout."
  echo "First-time: clone the repo into $APP_DIR (preserve .env and venv), or:"
  echo "  cd $APP_DIR && git init -b $BRANCH && git remote add origin <URL>"
  echo "  git fetch origin && git checkout -B $BRANCH origin/$BRANCH"
  exit 1
fi

# Preserve host secrets across pull (.env is gitignored; still backup/restore)
PRESERVE_DIR="\$HOME/.inertia_deploy_preserve"
mkdir -p "\$PRESERVE_DIR"
[[ -f .env ]] && cp -a .env "\$PRESERVE_DIR/.env"
[[ -f service_account.json ]] && cp -a service_account.json "\$PRESERVE_DIR/service_account.json"
[[ -f airflow/airflow.cfg ]] && cp -a airflow/airflow.cfg "\$PRESERVE_DIR/airflow.cfg"
# Never overwrite secrets / venvs via git
git fetch origin --prune
git checkout '$BRANCH'
git pull --ff-only origin '$BRANCH'

# Restore secrets if pull somehow touched them (or checkout created empties)
[[ -f "\$PRESERVE_DIR/.env" ]] && cp -a "\$PRESERVE_DIR/.env" .env
[[ -f "\$PRESERVE_DIR/service_account.json" ]] && cp -a "\$PRESERVE_DIR/service_account.json" service_account.json
[[ -f "\$PRESERVE_DIR/airflow.cfg" ]] && cp -a "\$PRESERVE_DIR/airflow.cfg" airflow/airflow.cfg
chmod 600 .env 2>/dev/null || true
chmod 600 service_account.json 2>/dev/null || true

# Only add missing Lean tags — never overwrite existing .env values
touch .env
grep -q '^EMAIL_SOURCE_TAG=' .env || echo 'EMAIL_SOURCE_TAG=Lean server' >> .env
grep -q '^DI_CREATE_OPS_TASKS=' .env || echo 'DI_CREATE_OPS_TASKS=false' >> .env
echo '==> .env keys (values not printed):'
grep -E '^(EMAIL_SOURCE_TAG|DI_CREATE_OPS_TASKS|DB_NAME|DB_USER)=' .env | sed 's/=.*/=***/' || true

if [[ '$SKIP_PIP' -eq 0 ]] && [[ -x ./venv/bin/pip ]]; then
  echo '==> pip install -r requirements.txt'
  ./venv/bin/pip install -r requirements.txt -q
fi

# Quick import smoke (optional if wsgi missing)
if [[ -x ./venv/bin/python3 ]] && [[ -f wsgi.py || -f main.py ]]; then
  ./venv/bin/python3 -c "from main import create_app; a=create_app(); print('smoke OK')" || true
fi

echo '==> Remote HEAD:'
git log -1 --oneline
EOF

if [[ "$SKIP_RESTART" -eq 0 ]]; then
  echo "==> Restart services (ssh -t for sudo password if needed)"
  RESTART_CMD="sudo systemctl reset-failed '$GUNICORN_UNIT' 2>/dev/null || true; sudo systemctl restart '$GUNICORN_UNIT'; sudo systemctl is-active '$GUNICORN_UNIT'; curl -s -o /dev/null -w 'health:%{http_code}\n' http://127.0.0.1:5004/api/v1/health || true"
  if [[ "$RESTART_AIRFLOW" -eq 1 ]]; then
    RESTART_CMD="$RESTART_CMD; for u in airflow-scheduler airflow-api-server airflow-dag-processor; do sudo systemctl restart \"\$u\" || true; sudo systemctl is-active \"\$u\" || true; done"
  fi
  # -t allocates TTY so sudo can prompt; drop BatchMode for this step
  ssh -i "$SSH_KEY" -t -o IdentitiesOnly=yes "$HOST" "bash -lc $(printf '%q' "$RESTART_CMD")"
fi

echo "==> Deploy done."
echo "    Optional on VPS:"
echo "      export AIRFLOW_HOME=$APP_DIR/airflow"
echo "      $APP_DIR/airflow_venv/bin/airflow dags trigger task_assignment_daily"
echo "      $APP_DIR/airflow_venv/bin/airflow dags unpause review_workflow_daily_report"
