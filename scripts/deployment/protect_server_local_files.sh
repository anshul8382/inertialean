#!/usr/bin/env bash
# Mark server-local override files (skip-worktree). Run once on soak/prod after editing.
#
#   cd /opt/Inertia2026v1
#   bash scripts/deployment/protect_server_local_files.sh
#
set -euo pipefail

_deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=server_local_paths.sh
source "${_deploy_dir}/server_local_paths.sh"

APP_DIR="${APP_DIR:-$(cd "${_deploy_dir}/../.." && pwd)}"
APP_USER="${INERTIA_DEPLOY_USER:-inertia}"

_app() {
  if [[ "$(id -u)" -eq 0 ]] && id "${APP_USER}" &>/dev/null; then
    sudo -u "${APP_USER}" "$@"
  else
    "$@"
  fi
}

echo "==> Protecting server-local files in ${APP_DIR}"
server_local_apply_skip_worktree "${APP_DIR}" _app

echo "==> Verify (S prefix = skip-worktree):"
_app git -C "${APP_DIR}" ls-files -v "${SERVER_LOCAL_PATHS[@]}" 2>/dev/null || true
