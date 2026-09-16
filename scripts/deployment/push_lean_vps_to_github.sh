#!/usr/bin/env bash
# Capture the *working Lean VPS* tree into GitHub (server = source of truth).
# Run ON the VPS as the app user, OR from Mac with --via-ssh.
#
# Secrets / local runtime are NEVER committed:
#   .env, venv/, airflow_venv/, uploads/, service_account.json, airflow logs/db
#
# On VPS:
#   export ORIGIN_URL='git@github.com:anshul8382/inertialean.git'
#   bash /tmp/push_lean_vps_to_github.sh
#   # or after script exists on server:
#   bash /opt/Inertia2026v1/scripts/deployment/push_lean_vps_to_github.sh --force-push
#
# From Mac (opens SSH; enter key passphrase if asked):
#   ./scripts/deployment/push_lean_vps_to_github.sh --via-ssh --force-push
#
# Then on Mac pull server truth:
#   git fetch origin && git reset --hard origin/main
#
set -euo pipefail

APP_DIR="${LEAN_APP_DIR:-/opt/Inertia2026v1}"
BRANCH="${LEAN_BRANCH:-main}"
ORIGIN_URL="${ORIGIN_URL:-git@github.com:anshul8382/inertialean.git}"
HOST="${LEAN_HOST:-anshul@129.121.133.25}"
SSH_KEY="${LEAN_SSH_KEY:-$HOME/.ssh/inertia_vps}"
VIA_SSH=0
FORCE_PUSH=0
MSG="${COMMIT_MSG:-Snapshot working Lean VPS tree}"

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --via-ssh) VIA_SSH=1; shift ;;
    --force-push) FORCE_PUSH=1; shift ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --message|-m) MSG="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

run_on_vps() {
  [[ -d "$APP_DIR" ]] || die "App dir missing: $APP_DIR"
  cd "$APP_DIR"

  mkdir -p .git/info
  cat > .git/info/exclude <<'EXCL'
.env
.env.*
venv/
airflow_venv/
uploads/
static/agreements/
static/uploads/
service_account.json
**/service_account.json
credentials.json
client_secret*.json
token.json
*.pem
airflow/logs/
airflow/airflow.db
airflow/*.pid
__pycache__/
*.pyc
.DS_Store
logs/
tmp/
instance/tmp/
flask_sessions/
.local/
EXCL

  if [[ ! -d .git ]]; then
    echo "==> git init in $APP_DIR"
    git init -b "$BRANCH"
  fi

  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$ORIGIN_URL"
  else
    git remote add origin "$ORIGIN_URL"
  fi

  echo "==> remote: $(git remote get-url origin)"
  echo "==> Staging working tree (secrets excluded)"
  git add -A
  git rm -r --cached .env 2>/dev/null || true
  git rm -r --cached venv airflow_venv 2>/dev/null || true
  git rm --cached -f service_account.json 2>/dev/null || true

  if git diff --cached --name-only | grep -E '(^|/)\.env$|service_account\.json|credentials\.json|\.pem$' >/dev/null; then
    die "Refusing to commit secrets found in index."
  fi

  if ! git diff --cached --quiet; then
    git -c user.email="${GIT_EMAIL:-anshul@equities4wealth.com}" \
        -c user.name="${GIT_NAME:-Inertia Lean VPS}" \
        commit -m "$MSG"
  else
    echo "==> Nothing new to commit (index clean)"
  fi

  echo "==> Push $BRANCH → origin (force=$FORCE_PUSH)"
  if [[ "$FORCE_PUSH" -eq 1 ]]; then
    git push -u origin "$BRANCH" --force
  else
    if ! git push -u origin "$BRANCH"; then
      echo "Push rejected (diverged from Mac commit). Re-run with --force-push if VPS should win."
      exit 1
    fi
  fi

  echo "==> Done. GitHub matches this VPS tree (minus secrets/venvs)."
  git log -1 --oneline
  echo "On Mac:  git fetch origin && git reset --hard origin/main"
}

if [[ "$VIA_SSH" -eq 1 ]]; then
  ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
  SCRIPT="$ROOT/scripts/deployment/push_lean_vps_to_github.sh"
  [[ -f "$SCRIPT" ]] || die "Missing $SCRIPT"
  [[ -f "$SSH_KEY" ]] || die "SSH key not found: $SSH_KEY"
  EXTRA=()
  [[ "$FORCE_PUSH" -eq 1 ]] && EXTRA+=(--force-push)
  echo "==> SSH $HOST — enter passphrase if prompted"
  # Upload + run with env; do not use BatchMode so passphrase works
  ssh -i "$SSH_KEY" -o IdentitiesOnly=yes "$HOST" \
    "ORIGIN_URL=$(printf %q "$ORIGIN_URL") LEAN_APP_DIR=$(printf %q "$APP_DIR") LEAN_BRANCH=$(printf %q "$BRANCH") COMMIT_MSG=$(printf %q "$MSG") bash -s -- ${EXTRA[*]-}" \
    < "$SCRIPT"
  exit $?
fi

run_on_vps
