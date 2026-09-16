#!/usr/bin/env bash
# Pull origin/main into this working tree without losing local secrets/runtime.
#
# Preserved (never from git):
#   .env, .env.*, venv/, airflow_venv/, uploads/, service_account.json
#
# Usage (Mac or VPS, from app/repo root):
#   ./scripts/deployment/pull_preserve_env.sh
#   ./scripts/deployment/pull_preserve_env.sh --branch main
#   ./scripts/deployment/pull_preserve_env.sh --force   # reset --hard to origin (keeps untracked .env)
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BRANCH="${LEAN_BRANCH:-main}"
FORCE=0

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

cd "$ROOT"
[[ -d .git ]] || die "Not a git repo: $ROOT"
git remote get-url origin >/dev/null 2>&1 || die "No origin remote"

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$ROOT/.deploy_preserve/env_$STAMP"
mkdir -p "$BACKUP_DIR"

echo "==> Backup local env / secrets → $BACKUP_DIR"
for f in .env .env.local .env.production service_account.json credentials.json; do
  if [[ -f "$f" ]]; then
    cp -a "$f" "$BACKUP_DIR/"
    echo "    saved $f"
  fi
done

echo "==> Fetch origin"
git fetch origin --prune

if [[ "$FORCE" -eq 1 ]]; then
  echo "==> Hard reset to origin/$BRANCH (untracked .env stays; tracked files match GitHub)"
  git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH"
  git reset --hard "origin/$BRANCH"
else
  echo "==> Fast-forward pull origin/$BRANCH"
  git checkout "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH" "origin/$BRANCH"
  git pull --ff-only origin "$BRANCH"
fi

echo "==> Restore .env if git somehow touched it (should not — gitignored)"
for f in .env .env.local .env.production service_account.json credentials.json; do
  if [[ -f "$BACKUP_DIR/$f" ]]; then
    if [[ ! -f "$f" ]] || ! cmp -s "$BACKUP_DIR/$f" "$f" 2>/dev/null; then
      # Only restore if missing; never overwrite a newer local .env that still exists
      if [[ ! -f "$f" ]]; then
        cp -a "$BACKUP_DIR/$f" "$f"
        echo "    restored missing $f"
      fi
    fi
  fi
done

# Safety: ensure .env was never tracked
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "WARNING: .env is tracked by git — untrack with: git rm --cached .env"
fi

echo "==> HEAD: $(git log -1 --oneline)"
echo "==> Local .env present: $([[ -f .env ]] && echo yes || echo NO)"
echo "Done. Backup kept at $BACKUP_DIR (safe to delete later)."
