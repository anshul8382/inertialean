#!/usr/bin/env bash
# Git-based deploy of Inertia2026-lean to Hostinger KVM 4.
#
# Flow (Mac):
#   1. Commit + push (or use ship_kvm.sh)
#   2. ./scripts/deployment/deploy_kvm_vps.sh --skip-push --restart-airflow
#        → SSHs to KVM, git fetch/pull --ff-only, pip (optional), restart Gunicorn
#
# First-time: make /opt/Inertia2026v1 a git checkout (preserve .env / venv):
#   scp -i ~/.ssh/inertia_vps scripts/deployment/bootstrap_lean_git.sh anshul@187.127.188.97:/tmp/
#   ssh -i ~/.ssh/inertia_vps anshul@187.127.188.97 \
#     "ORIGIN_URL='git@github.com:anshul8382/inertialean.git' bash /tmp/bootstrap_lean_git.sh"
#
# Usage:
#   ./scripts/deployment/deploy_kvm_vps.sh
#   ./scripts/deployment/deploy_kvm_vps.sh --branch main
#   ./scripts/deployment/deploy_kvm_vps.sh --skip-push
#   ./scripts/deployment/deploy_kvm_vps.sh --skip-pip
#   ./scripts/deployment/deploy_kvm_vps.sh --skip-restart
#   ./scripts/deployment/deploy_kvm_vps.sh --restart-airflow
#   ./scripts/deployment/deploy_kvm_vps.sh --allow-dirty
#
# Env:
#   KVM_HOST=anshul@187.127.188.97
#   KVM_APP_DIR=/opt/Inertia2026v1
#   KVM_SSH_KEY=~/.ssh/inertia_vps
#   KVM_GUNICORN_UNIT=inertia-2026v1
#   KVM_BRANCH=main
#
# Never deploy this tree to BigRock (66.116.199.231).
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST="${KVM_HOST:-anshul@187.127.188.97}"
APP_DIR="${KVM_APP_DIR:-/opt/Inertia2026v1}"
SSH_KEY="${KVM_SSH_KEY:-$HOME/.ssh/inertia_vps}"
GUNICORN_UNIT="${KVM_GUNICORN_UNIT:-inertia-2026v1}"
BRANCH="${KVM_BRANCH:-main}"

SKIP_PUSH=0
SKIP_PIP=0
SKIP_RESTART=0
RESTART_AIRFLOW=0
ALLOW_DIRTY=0

usage() {
  sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'
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

case "$HOST" in
  *66.116.199.231*|*inertiainvest.in*)
    die "Refusing BigRock target ($HOST). Use app 2 workspace for BigRock only."
    ;;
esac

if [[ "$ALLOW_DIRTY" -eq 0 ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "Working tree has uncommitted changes. Commit first or pass --allow-dirty."
  fi
fi

git remote get-url origin >/dev/null 2>&1 || die "No 'origin' remote. Create/push the Cursor (or GitHub) repo first."

echo "==> Git deploy to KVM4"
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

echo "==> Remote: git pull + preserve .env + restart"
# shellcheck disable=SC2087
"${SSH[@]}" "$HOST" bash -s <<EOF
set -euo pipefail
cd '$APP_DIR'

if [[ ! -d .git ]]; then
  echo "ERROR: $APP_DIR is not a git checkout."
  echo "First-time on KVM:"
  echo "  ORIGIN_URL='git@github.com:anshul8382/inertialean.git' bash /tmp/bootstrap_lean_git.sh"
  exit 1
fi

PRESERVE_DIR="\$HOME/.inertia_deploy_preserve"
mkdir -p "\$PRESERVE_DIR"
[[ -f .env ]] && cp -a .env "\$PRESERVE_DIR/.env"
[[ -f service_account.json ]] && cp -a service_account.json "\$PRESERVE_DIR/service_account.json"
[[ -f airflow/airflow.cfg ]] && cp -a airflow/airflow.cfg "\$PRESERVE_DIR/airflow.cfg"

git fetch origin --prune
git checkout '$BRANCH'
git pull --ff-only origin '$BRANCH'

[[ -f "\$PRESERVE_DIR/.env" ]] && cp -a "\$PRESERVE_DIR/.env" .env
[[ -f "\$PRESERVE_DIR/service_account.json" ]] && cp -a "\$PRESERVE_DIR/service_account.json" service_account.json
[[ -f "\$PRESERVE_DIR/airflow.cfg" ]] && cp -a "\$PRESERVE_DIR/airflow.cfg" airflow/airflow.cfg
chmod 600 .env 2>/dev/null || true
chmod 600 service_account.json 2>/dev/null || true

# Only add missing KVM tags — never overwrite existing .env values
touch .env
grep -q '^EMAIL_SOURCE_TAG=' .env || echo 'EMAIL_SOURCE_TAG=KVM4' >> .env
grep -q '^DI_CREATE_OPS_TASKS=' .env || echo 'DI_CREATE_OPS_TASKS=false' >> .env
echo '==> .env keys (values not printed):'
grep -E '^(EMAIL_SOURCE_TAG|DI_CREATE_OPS_TASKS|DB_NAME|DB_USER|CODEBASE_BACKUP_DRIVE_ENABLED)=' .env | sed 's/=.*/=***/' || true

if [[ '$SKIP_PIP' -eq 0 ]] && [[ -x ./venv/bin/pip ]]; then
  echo '==> pip install -r requirements.txt'
  ./venv/bin/pip install -r requirements.txt -q
fi

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
  ssh -i "$SSH_KEY" -t -o IdentitiesOnly=yes "$HOST" "bash -lc $(printf '%q' "$RESTART_CMD")"
fi

echo "==> KVM deploy done."
echo "    Health: ssh $HOST 'curl -s -o /dev/null -w \"%{http_code}\\n\" http://127.0.0.1:5004/api/v1/health'"
