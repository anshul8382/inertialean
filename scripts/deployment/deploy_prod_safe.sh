#!/usr/bin/env bash
# Safe production deploy: git-only updates, smoke test, then systemd restart.
# Use this instead of editing files directly on the server with an editor sync.
#
# Typical workflow:
#   1. Develop in Cursor on your laptop (or a dev VM), commit, push to origin.
#   2. On the server:  ./scripts/deployment/deploy_prod_safe.sh main
#
# Usage:
#   ./scripts/deployment/deploy_prod_safe.sh <branch-or-tag> [--skip-pip] [--skip-migrate] [--allow-dirty]
#
set -euo pipefail

APP_DIR="${APP_DIR:-/home/inertia/app}"
SERVICE="${INERTIA_SYSTEMD_SERVICE:-inertia-app.service}"
BRANCH="${1:-}"
SKIP_PIP=0
SKIP_MIGRATE=0
ALLOW_DIRTY=0

if [[ -z "$BRANCH" ]]; then
  echo "Usage: $0 <branch-or-tag> [--skip-pip] [--skip-migrate] [--allow-dirty]" >&2
  exit 1
fi
shift || true

for arg in "$@"; do
  case "$arg" in
    --skip-pip) SKIP_PIP=1 ;;
    --skip-migrate) SKIP_MIGRATE=1 ;;
    --allow-dirty) ALLOW_DIRTY=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

cd "$APP_DIR"

die() { echo "ERROR: $*" >&2; exit 1; }

echo "==> App dir: $APP_DIR"

# --- Guard: no tracked files deleted from disk (common cause of 503 / import errors) ---
missing="$(git diff --name-only --diff-filter=D 2>/dev/null || true)"
if [[ -n "$missing" ]]; then
  die "Tracked files are missing on disk (restore before deploy):\n$missing\nRun: git restore --source=HEAD -- <paths>  or  git restore ."
fi

cached_missing="$(git diff --cached --name-only --diff-filter=D 2>/dev/null || true)"
if [[ -n "$cached_missing" ]]; then
  die "Staged deletions present; fix index before deploy:\n$cached_missing"
fi

# Critical paths (fail fast even if git state looks odd)
for f in __init__.py models.py routes/main.py wsgi.py utils/permissions.py; do
  [[ -f "$f" ]] || die "Required file missing: $f"
done

if [[ "$ALLOW_DIRTY" -eq 0 ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "Working tree or index has changes. Commit/stash or pass --allow-dirty (not recommended on prod)."
  fi
fi

echo "==> Fetch / checkout $BRANCH"
git fetch origin --prune
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

missing="$(git diff --name-only --diff-filter=D 2>/dev/null || true)"
[[ -z "$missing" ]] || die "After pull, tracked files missing:\n$missing"

if [[ "$SKIP_PIP" -eq 0 ]]; then
  echo "==> pip install -r requirements.txt"
  ./venv/bin/pip install -r requirements.txt -q
fi

if [[ "$SKIP_MIGRATE" -eq 0 ]]; then
  if [[ -x ./scripts/deployment/migrate.sh ]]; then
    echo "==> DB migrate (prod upgrade)"
    ./scripts/deployment/migrate.sh prod upgrade || die "migrate.sh failed"
  else
    echo "WARN: scripts/deployment/migrate.sh not found or not executable; skipping migrations"
  fi
fi

echo "==> Smoke test (import app before restart)"
./venv/bin/python3 -c "from wsgi import app; assert app is not None; print('smoke OK:', app.name)"

echo "==> Restart $SERVICE"
sudo systemctl reset-failed "$SERVICE" 2>/dev/null || true
sudo systemctl restart "$SERVICE"

sleep 2
systemctl is-active --quiet "$SERVICE" || die "Service not active after restart; check: journalctl -u $SERVICE -n 80"

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:5000/api/v1/health || echo 000)"
if [[ "$code" != "200" ]]; then
  code2="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:5000/ || echo 000)"
  [[ "$code2" == "200" || "$code2" == "302" || "$code2" == "301" ]] || die "HTTP probe failed (health=$code, /=$code2)"
fi

echo "==> Deploy finished. Service active; HTTP probe: $code"
