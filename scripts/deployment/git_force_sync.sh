#!/usr/bin/env bash
# Force server working tree to match origin/<branch>.
# Default: skip-worktree on services/email_service.py (local MAIL config).
# Use --full to overwrite that file from git.
#
# Usage (on server):
#   bash scripts/deployment/git_force_sync.sh fresh-app-2026-05-26
#   bash scripts/deployment/git_force_sync.sh fresh-app-2026-05-26 --full
#
set -euo pipefail

BRANCH="${1:-fresh-app-2026-05-26}"
FULL_SYNC=0
if [[ "${2:-}" == "--full" ]]; then
  FULL_SYNC=1
fi

_deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=server_local_paths.sh
source "${_deploy_dir}/server_local_paths.sh"

APP_DIR="${APP_DIR:-$(cd "${_deploy_dir}/../.." && pwd)}"
APP_USER="${INERTIA_DEPLOY_USER:-inertia}"

die() { echo "ERROR: $*" >&2; exit 1; }

_app() {
  if [[ "$(id -u)" -eq 0 ]] && id "${APP_USER}" &>/dev/null; then
    sudo -u "${APP_USER}" "$@"
  else
    "$@"
  fi
}

[[ -d "${APP_DIR}/.git" ]] || die "Not a git repo: ${APP_DIR}"

echo "==> Force sync ${APP_DIR} -> origin/${BRANCH}"

purge_legacy_deploy_preserve "${APP_DIR}" 2>/dev/null || true

if [[ "$FULL_SYNC" -eq 0 ]]; then
  echo "==> Skip-worktree: ${SERVER_LOCAL_PATHS[*]}"
  server_local_apply_skip_worktree "${APP_DIR}" _app
  server_local_unstick_non_protected "${APP_DIR}" _app
else
  echo "==> FULL sync (--full): clearing skip-worktree on protected paths"
  for path in "${SERVER_LOCAL_PATHS[@]}"; do
    [[ -n "$path" ]] || continue
    _app git -C "${APP_DIR}" update-index --no-skip-worktree "$path" 2>/dev/null || true
  done
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    if server_local_is_protected "$path"; then
      continue
    fi
    _app git -C "${APP_DIR}" update-index --no-assume-unchanged "$path" 2>/dev/null || true
    _app git -C "${APP_DIR}" update-index --no-skip-worktree "$path" 2>/dev/null || true
  done < <(_app git -C "${APP_DIR}" ls-files -v | awk '/^[Sh]/ {print $2}')
fi

_app git -C "${APP_DIR}" fetch origin --prune
_app git -C "${APP_DIR}" checkout "$BRANCH"

if ! _app git -C "${APP_DIR}" reset --hard "origin/${BRANCH}"; then
  echo "WARN: reset --hard failed; using read-tree fallback"
  _app git -C "${APP_DIR}" read-tree -u --reset "origin/${BRANCH}" \
    || die "read-tree failed — check file ownership (chown ${APP_USER} on repo)"
fi

_app git -C "${APP_DIR}" clean -fd

_app git -C "${APP_DIR}" status --short --branch
echo "==> Synced to origin/${BRANCH} ($(_app git -C "${APP_DIR}" rev-parse --short HEAD))"
