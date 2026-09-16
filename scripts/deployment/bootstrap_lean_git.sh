#!/usr/bin/env bash
# One-time: turn existing /opt/Inertia2026v1 on Lean into a git checkout of this repo.
# Run ON the VPS (or via ssh). Preserves .env, venv/, airflow_venv/, uploads/.
#
# Usage (on VPS):
#   export ORIGIN_URL='https://origin.cursor.com/<org>/inertia2026-lean.git'
#   sudo -u anshul bash /opt/Inertia2026v1/scripts/deployment/bootstrap_lean_git.sh
#
# Or from Mac after origin exists:
#   ssh -i ~/.ssh/inertia_vps anshul@129.121.133.25 \
#     "ORIGIN_URL='...' bash -s" < scripts/deployment/bootstrap_lean_git.sh
#
set -euo pipefail

APP_DIR="${LEAN_APP_DIR:-/opt/Inertia2026v1}"
BRANCH="${LEAN_BRANCH:-main}"
ORIGIN_URL="${ORIGIN_URL:-}"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ -n "$ORIGIN_URL" ]] || die "Set ORIGIN_URL to the git remote (HTTPS clone URL)."
[[ -d "$APP_DIR" ]] || die "App dir missing: $APP_DIR"
cd "$APP_DIR"

if [[ -d .git ]]; then
  echo "==> Already a git repo"
  git remote get-url origin 2>/dev/null || git remote add origin "$ORIGIN_URL"
  git remote set-url origin "$ORIGIN_URL"
  git fetch origin --prune
  git checkout -B "$BRANCH" "origin/$BRANCH" || git checkout -B "$BRANCH"
  git pull --ff-only origin "$BRANCH" || true
  git log -1 --oneline
  echo "OK — existing checkout updated."
  exit 0
fi

echo "==> Initializing git in $APP_DIR (keeping local .env / venv)"
# Stash-like: ignore local-only paths before first checkout
git init -b "$BRANCH"
git remote add origin "$ORIGIN_URL"

# Do not let checkout clobber secrets/venvs — they should be untracked / gitignored
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

git fetch origin --prune
# Prefer a clean track of origin without deleting untracked secrets
git checkout -f -B "$BRANCH" "origin/$BRANCH"

echo "==> HEAD:"
git log -1 --oneline
echo "OK — $APP_DIR now tracks origin/$BRANCH"
echo "Keep .env and venv/ on disk only (never commit)."
