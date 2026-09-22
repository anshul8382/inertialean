#!/usr/bin/env bash
# One-time: turn existing /opt/Inertia2026v1 into a git checkout of inertialean.
# Works on Lean or KVM4. Run ON the VPS (or via ssh). Preserves .env, venv/, airflow_venv/, uploads/.
#
# Usage (on VPS):
#   export ORIGIN_URL='https://github.com/anshul8382/inertialean.git'
#   bash /opt/Inertia2026v1/scripts/deployment/bootstrap_lean_git.sh
#
# From Mac — Lean:
#   scp -i ~/.ssh/inertia_vps scripts/deployment/bootstrap_lean_git.sh anshul@129.121.133.25:/tmp/
#   ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25 \
#     "ORIGIN_URL='git@github.com:anshul8382/inertialean.git' bash /tmp/bootstrap_lean_git.sh"
#
# From Mac — KVM4 (required once before ship_kvm.sh / deploy_kvm_vps.sh):
#   scp -i ~/.ssh/inertia_vps scripts/deployment/bootstrap_lean_git.sh anshul@187.127.188.97:/tmp/
#   ssh -i ~/.ssh/inertia_vps anshul@187.127.188.97 \
#     "ORIGIN_URL='git@github.com:anshul8382/inertialean.git' bash /tmp/bootstrap_lean_git.sh"
#
set -euo pipefail

APP_DIR="${LEAN_APP_DIR:-/opt/Inertia2026v1}"
BRANCH="${LEAN_BRANCH:-main}"
ORIGIN_URL="${ORIGIN_URL:-}"

die() { echo "ERROR: $*" >&2; exit 1; }

# git must not consume this script's stdin when invoked via `bash -s`
git_q() { git "$@" </dev/null; }

[[ -n "$ORIGIN_URL" ]] || die "Set ORIGIN_URL to the git remote (HTTPS or SSH clone URL)."
[[ -d "$APP_DIR" ]] || die "App dir missing: $APP_DIR"
cd "$APP_DIR"

if [[ -d .git ]] && git_q rev-parse --git-dir >/dev/null 2>&1; then
  echo "==> Already a valid git repo in $APP_DIR"
  git_q remote get-url origin >/dev/null 2>&1 || git_q remote add origin "$ORIGIN_URL"
  git_q remote set-url origin "$ORIGIN_URL"
  git_q fetch origin --prune
  git_q checkout -B "$BRANCH" "origin/$BRANCH" || git_q checkout -B "$BRANCH"
  git_q pull --ff-only origin "$BRANCH" || true
  git_q log -1 --oneline
  echo "OK — existing checkout updated."
  exit 0
fi

if [[ -e .git ]]; then
  echo "==> Removing broken/incomplete .git"
  rm -rf .git
fi

echo "==> Initializing git in $APP_DIR (keeping local .env / venv)"
git_q init -b "$BRANCH"
git_q remote add origin "$ORIGIN_URL"

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
airflow/logs/
airflow/airflow.db
airflow/airflow.cfg
__pycache__/
*.pyc
EXCL

git_q fetch origin --prune
# Prefer a clean track of origin without deleting untracked secrets
git_q checkout -f -B "$BRANCH" "origin/$BRANCH"

echo "==> HEAD:"
git_q log -1 --oneline
echo "OK — $APP_DIR now tracks origin/$BRANCH"
echo "Keep .env and venv/ on disk only (never commit)."
