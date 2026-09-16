#!/usr/bin/env bash
# Server-only overrides: git skip-worktree (no backup dirs, no preserve/restore).
#
# One-time on server:
#   cd /opt/Inertia2026v1
#   bash scripts/deployment/protect_server_local_files.sh
#
# Or manually:
#   sudo -u inertia git update-index --skip-worktree services/email_service.py
#
set -euo pipefail

# shellcheck disable=SC2034
SERVER_LOCAL_PATHS=(
  services/email_service.py
  airflow/airflow.cfg
)

if [[ -n "${INERTIA_SERVER_LOCAL_PATHS:-}" ]]; then
  IFS=':' read -ra SERVER_LOCAL_PATHS <<< "${INERTIA_SERVER_LOCAL_PATHS}"
fi

server_local_is_protected() {
  local candidate="$1"
  local path
  for path in "${SERVER_LOCAL_PATHS[@]}"; do
    [[ "$candidate" == "$path" ]] && return 0
  done
  return 1
}

# Mark paths so deploy reset/clean does not overwrite local server config.
server_local_apply_skip_worktree() {
  local app_dir="$1"
  local git_cmd="$2"
  local path
  for path in "${SERVER_LOCAL_PATHS[@]}"; do
    [[ -n "$path" ]] || continue
    if [[ ! -f "${app_dir}/${path}" ]]; then
      echo "  skip (missing): ${path}"
      continue
    fi
    ${git_cmd} git -C "${app_dir}" update-index --skip-worktree "${path}" 2>/dev/null || true
    echo "  skip-worktree: ${path}"
  done
}

# Remove old .deploy_preserve from earlier deploy versions (one-time cleanup).
purge_legacy_deploy_preserve() {
  local app_dir="$1"
  local d="${app_dir}/.deploy_preserve"
  [[ -d "${d}" ]] || return 0
  if [[ "$(id -u)" -eq 0 ]]; then
    chmod -R u+w "${d}" 2>/dev/null || true
    rm -rf "${d}" || return 1
  else
    rm -rf "${d}" 2>/dev/null || return 1
  fi
  return 0
}

# Unstick index flags on other files only (not email_service.py).
server_local_unstick_non_protected() {
  local app_dir="$1"
  local git_cmd="$2"
  local path
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    if server_local_is_protected "$path"; then
      continue
    fi
    ${git_cmd} git -C "${app_dir}" update-index --no-assume-unchanged "$path" 2>/dev/null || true
    ${git_cmd} git -C "${app_dir}" update-index --no-skip-worktree "$path" 2>/dev/null || true
  done < <(${git_cmd} git -C "${app_dir}" ls-files -v 2>/dev/null | awk '/^[Sh]/ {print $2}' || true)
}
