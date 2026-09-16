#!/usr/bin/env bash
# Deploy live v2026: git sync, pip, restart inertia-2026v1 (Gunicorn from .env GUNICORN_BIND).
#
# Server (run as root — avoids sudo password prompts for systemctl):
#   cd /opt/Inertia2026v1
#   bash scripts/deployment/deploy_parallel_v2026.sh fresh-app-2026-05-26
#
# Probe port: GUNICORN_PORT, else GUNICORN_BIND in APP_DIR/.env, else 5000 (live prod).
# Do not leave the default at 5004 (old soak); that port is not bound after cutover.
#
# If you run as the inertia user instead, that user needs passwordless sudo for
# systemctl (or the restart step will hang on a password prompt).
#
set -euo pipefail

# When invoked as root, run git/pip/python as app owner so the tree stays inertia-owned.
_app_user="${INERTIA_DEPLOY_USER:-inertia}"
_app() {
  if [[ "$(id -u)" -eq 0 ]] && id "${_app_user}" &>/dev/null; then
    sudo -u "${_app_user}" "$@"
  else
    "$@"
  fi
}

# systemctl without nested sudo when already root
_sc() {
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl "$@"
  else
    sudo systemctl "$@"
  fi
}

# Resolve repo root from this script (avoids stale APP_DIR=e.g. /opt/inertia-app-v2026 in shell/profile).
_deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_default_app_dir="$(cd "${_deploy_dir}/../.." && pwd)"
APP_DIR="${APP_DIR:-${_default_app_dir}}"
if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "WARN: APP_DIR=${APP_DIR} missing .git — using ${_default_app_dir}" >&2
  APP_DIR="${_default_app_dir}"
fi

SERVICE="${INERTIA_SYSTEMD_SERVICE:-inertia-2026v1.service}"
_gunicorn_port_from_dotenv() {
  local envf="${APP_DIR}/.env"
  [[ -f "$envf" ]] || return 1
  local bind
  bind="$(grep -E '^GUNICORN_BIND=' "$envf" | tail -1 | cut -d= -f2- | tr -d '[:space:]' | tr -d '"' | tr -d "'")"
  [[ -n "$bind" && "$bind" == *:* ]] || return 1
  echo "${bind##*:}"
}
if [[ -z "${GUNICORN_PORT:-}" ]]; then
  GUNICORN_PORT="$(_gunicorn_port_from_dotenv || true)"
  GUNICORN_PORT="${GUNICORN_PORT:-5000}"
fi
APACHE_SSL_PORT="${APACHE_SSL_PORT:-443}"
BRANCH="${1:-fresh-app-2026-05-26}"
SKIP_PIP=0
SKIP_MIGRATE=1
KEEP_LOCAL=0
RELOAD_HTTPD=1
SKIP_FETCH=0

shift || true
for arg in "$@"; do
  case "$arg" in
    --skip-pip) SKIP_PIP=1 ;;
    --with-migrate) SKIP_MIGRATE=0 ;;
    --keep-local) KEEP_LOCAL=1 ;;
    --allow-dirty) KEEP_LOCAL=1 ;;  # deprecated alias
    --no-httpd) RELOAD_HTTPD=0 ;;
    # Use when origin/<branch> was already updated (e.g. git bundle from Mac) —
    # avoids GitHub password/SSH on the server.
    --skip-fetch|--offline) SKIP_FETCH=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

cd "$APP_DIR"
die() { echo "ERROR: $*" >&2; exit 1; }

# shellcheck source=server_local_paths.sh
source "${_deploy_dir}/server_local_paths.sh"

_git_sync_from_origin() {
  local branch="$1"
  local keep_local="$2"
  local skip_fetch="$3"

  if [[ "$skip_fetch" -eq 0 ]]; then
    _app git -C "${APP_DIR}" fetch origin --prune
  else
    echo "==> --skip-fetch: not contacting GitHub; using existing origin/${branch}"
    _app git -C "${APP_DIR}" rev-parse --verify "origin/${branch}" >/dev/null \
      || die "origin/${branch} missing — update it first (see deploy_via_bundle.sh)"
  fi
  _app git -C "${APP_DIR}" checkout "$branch"

  if [[ "$keep_local" -eq 1 ]]; then
    if [[ -n "$(_app git -C "${APP_DIR}" status --porcelain 2>/dev/null || true)" ]]; then
      die "Working tree dirty (--keep-local set). Commit/stash or re-run without --keep-local"
    fi
    if [[ "$skip_fetch" -eq 1 ]]; then
      _app git -C "${APP_DIR}" merge --ff-only "origin/${branch}"
    else
      _app git -C "${APP_DIR}" pull --ff-only origin "$branch"
    fi
    return
  fi

  purge_legacy_deploy_preserve "${APP_DIR}" 2>/dev/null || true

  echo "==> Skip-worktree (local server files not overwritten): ${SERVER_LOCAL_PATHS[*]}"
  server_local_apply_skip_worktree "${APP_DIR}" _app
  server_local_unstick_non_protected "${APP_DIR}" _app

  if ! _app git -C "${APP_DIR}" reset --hard "origin/$branch"; then
    echo "WARN: reset --hard failed; using read-tree fallback"
    _app git -C "${APP_DIR}" read-tree -u --reset "origin/$branch" \
      || die "git sync failed"
  fi
  _app git -C "${APP_DIR}" clean -fd
}

[[ -d .git ]] || die "Not a git repo: $APP_DIR"
[[ -f config/gunicorn_parallel_v2026.py ]] || die "Missing config/gunicorn_parallel_v2026.py — git pull latest"

echo "==> Parallel deploy: $APP_DIR (gunicorn :$GUNICORN_PORT, apache :$APACHE_SSL_PORT)"

echo "==> git fetch / sync $BRANCH"
_git_sync_from_origin "$BRANCH" "$KEEP_LOCAL" "$SKIP_FETCH"

if [[ "$SKIP_PIP" -eq 0 ]]; then
  echo "==> pip install"
  _app ./venv/bin/pip install -r requirements.txt -q
fi

if [[ "$SKIP_MIGRATE" -eq 0 ]] && [[ -x ./scripts/deployment/migrate.sh ]]; then
  echo "==> DB migrate"
  _app ./scripts/deployment/migrate.sh prod upgrade || die "migrate failed"
fi

echo "==> Smoke test"
_app ./venv/bin/python3 -c "from wsgi import app; assert app is not None; print('smoke OK')"

echo "==> Restart $SERVICE"
_sc reset-failed "$SERVICE" 2>/dev/null || true
_sc restart "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE" || die "Service not active; journalctl -u ${SERVICE%.service} -n 50"

if [[ "$RELOAD_HTTPD" -eq 1 ]]; then
  _sc reload httpd 2>/dev/null || _sc reload apache2 2>/dev/null || true
fi

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${GUNICORN_PORT}/api/v1/health" || echo 000)"
[[ "$code" == "200" ]] || die "Gunicorn probe failed on :${GUNICORN_PORT}/api/v1/health (got $code)"

code_ssl="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "https://127.0.0.1:${APACHE_SSL_PORT}/api/v1/health" || echo 000)"
# Public HTTPS is Apache 443 → Gunicorn; :5003 soak is disabled. Do not fail the deploy on 5003.
if [[ "${APACHE_SSL_PORT}" != "5003" ]]; then
  echo "==> Apache :${APACHE_SSL_PORT} health → $code_ssl"
fi
echo "==> Gunicorn :${GUNICORN_PORT} → $code"
echo "==> Done. https://inertiainvest.in/  (probed 127.0.0.1:${GUNICORN_PORT})"
