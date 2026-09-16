#!/usr/bin/env bash
# Cut over https://inertiainvest.in from legacy inertia-app (:5000) to v2026 (:5004).
#
# Run ON THE SERVER as a user with sudo (e.g. inertia):
#   cd /opt/Inertia2026v1
#   bash scripts/deployment/cutover_to_v2026_prod.sh
#   bash scripts/deployment/cutover_to_v2026_prod.sh --dry-run
#   bash scripts/deployment/cutover_to_v2026_prod.sh --rollback
#
set -euo pipefail

_deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=apache_paths.sh
source "${_deploy_dir}/apache_paths.sh"
apache_detect_layout || exit 1

APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
LEGACY_APP_DIR="${LEGACY_APP_DIR:-/home/inertia/app}"
GUNICORN_PORT="${GUNICORN_PORT:-5004}"
LEGACY_PORT="${LEGACY_PORT:-5000}"
V2026_SERVICE="${V2026_SERVICE:-inertia-2026v1}"
LEGACY_SERVICE="${LEGACY_SERVICE:-inertia-app}"
HTTPD_SERVICE="${HTTPD_SERVICE:-httpd}"
APACHE_CONF_SRC="${APACHE_CONF_SRC:-${APP_DIR}/config/apache-inertiainvest-prod-v2026-proxy.conf}"

DRY_RUN=0
ROLLBACK=0
REFRESH_UNIT=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --rollback) ROLLBACK=1 ;;
    --refresh-unit) REFRESH_UNIT=1 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] $*"
  else
    echo "+ $*"
    eval "$@"
  fi
}

require_root() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
  fi
  if [[ "$(id -u)" -ne 0 ]] && ! sudo -n true 2>/dev/null; then
    echo "This script needs sudo. Re-run with sudo or as root." >&2
    exit 1
  fi
}

sudo_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] sudo $*"
  elif [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

health_check() {
  local url="$1"
  local label="$2"
  local code
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] curl $url"
    return 0
  fi
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 "$url" 2>/dev/null || echo "000")
  if [[ "$code" =~ ^(200|302|401|403)$ ]]; then
    echo "OK: $label ($url) HTTP $code"
  else
    echo "WARN: $label HTTP $code ($url)" >&2
    return 1
  fi
}

rollback() {
  echo "=== Rollback: restore legacy :${LEGACY_PORT} on :443 ==="
  require_root
  run "sudo_cmd mkdir -p '${BACKUP_DIR}'"
  if [[ -f "${BACKUP_DIR}/inertiainvest.in.conf.bak" && -n "${LEGACY_APACHE_CONF:-}" ]]; then
    run "sudo_cmd cp '${BACKUP_DIR}/inertiainvest.in.conf.bak' '${LEGACY_APACHE_CONF}'"
    if apache_is_cpanel_proxy_legacy; then
      run "apache_cpanel_rebuild_httpd"
    fi
  elif [[ -n "${LEGACY_APACHE_CONF:-}" && -f "${LEGACY_APACHE_CONF}.pre-cutover-disabled" ]]; then
    run "sudo_cmd mv '${LEGACY_APACHE_CONF}.pre-cutover-disabled' '${LEGACY_APACHE_CONF}'"
  else
    echo "No legacy vhost backup found — restore Apache manually." >&2
    exit 1
  fi
  run "sudo_cmd rm -f '${APACHE_CONF_DEST}'"
  run "sudo_cmd apachectl configtest"
  run "sudo_cmd systemctl reload ${HTTPD_SERVICE}"
  run "sudo_cmd systemctl stop ${V2026_SERVICE} || true"
  run "sudo_cmd systemctl start ${LEGACY_SERVICE}"
  health_check "http://127.0.0.1:${LEGACY_PORT}/api/v1/health" "legacy gunicorn" || true
  health_check "https://inertiainvest.in/api/v1/health" "public HTTPS" || true
  echo "Rollback complete. Verify login and key workflows."
}

if [[ "$ROLLBACK" -eq 1 ]]; then
  rollback
  exit 0
fi

echo "=== Cutover: v2026 production on https://inertiainvest.in ==="
echo "APP_DIR=$APP_DIR  Gunicorn=127.0.0.1:${GUNICORN_PORT}"
echo "Apache conf.d: ${APACHE_CONF_DIR}"
echo "Prod vhost:    ${APACHE_CONF_DEST}"
echo "Soak vhost:    ${SOAK_APACHE_CONF}"

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR not found: $APP_DIR" >&2
  exit 1
fi
if [[ ! -f "$APACHE_CONF_SRC" ]]; then
  echo "Apache config not found: $APACHE_CONF_SRC" >&2
  exit 1
fi

require_root

# 1. Ensure v2026 Gunicorn is running (keep existing systemd unit unless --refresh-unit)
if [[ "$REFRESH_UNIT" -eq 1 ]]; then
  run "sudo_cmd cp '${APP_DIR}/config/inertia-2026v1.service' '/etc/systemd/system/${V2026_SERVICE}.service'"
  run "sudo_cmd systemctl daemon-reload"
else
  echo "Keeping existing /etc/systemd/system/${V2026_SERVICE}.service (pass --refresh-unit to replace)"
fi
run "sudo_cmd systemctl enable ${V2026_SERVICE}"
run "sudo_cmd systemctl reset-failed ${V2026_SERVICE} 2>/dev/null || true"
run "sudo_cmd systemctl restart ${V2026_SERVICE}"
if [[ "$DRY_RUN" -eq 0 ]]; then
  sleep 3
  if ! systemctl is-active --quiet "${V2026_SERVICE}"; then
    echo "ERROR: ${V2026_SERVICE} not active. Logs:" >&2
    journalctl -u "${V2026_SERVICE}" -n 40 --no-pager >&2 || true
    exit 1
  fi
fi
health_check "http://127.0.0.1:${GUNICORN_PORT}/api/v1/health" "v2026 gunicorn" || {
  echo "v2026 Gunicorn not healthy on :${GUNICORN_PORT}." >&2
  if [[ "$DRY_RUN" -eq 0 ]]; then
    echo "Try: bash scripts/deployment/deploy_parallel_v2026.sh fresh-app-2026-05-26 --skip-pip" >&2
    journalctl -u "${V2026_SERVICE}" -n 40 --no-pager >&2 || true
  fi
  exit 1
}

# 2. Backup and install Apache prod proxy
run "sudo_cmd mkdir -p '${BACKUP_DIR}' '${APACHE_INERTIA_DIR}'"
if [[ -n "${LEGACY_APACHE_CONF:-}" && -f "$LEGACY_APACHE_CONF" ]]; then
  run "sudo_cmd cp -a '${LEGACY_APACHE_CONF}' '${BACKUP_DIR}/inertiainvest.in.conf.bak'"
  echo "Backed up legacy :443 vhost: ${LEGACY_APACHE_CONF}"
else
  echo "WARN: No legacy :443 vhost in conf.d (cPanel may manage :443 elsewhere)." >&2
  echo "      If cutover后 public URL still shows old app, run: apachectl -S | grep 443" >&2
fi
if [[ -f "${SOAK_APACHE_CONF}" ]]; then
  run "sudo_cmd cp -a '${SOAK_APACHE_CONF}' '${BACKUP_DIR}/inertia2026v1-5003-ssl.conf.bak'"
fi

if apache_is_cpanel_proxy_legacy && [[ -f "${LEGACY_APACHE_CONF}" ]]; then
  echo "cPanel mode: run v2026 on :${LEGACY_PORT} (Apache proxy unchanged; stop legacy first)"
  run "sudo_cmd rm -f '${APACHE_CONF_DEST}'"
  run "sudo_cmd systemctl stop ${LEGACY_SERVICE} || true"
  run "sudo_cmd systemctl disable ${LEGACY_SERVICE} || true"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] GUNICORN_BIND=127.0.0.1:${LEGACY_PORT}; restart ${V2026_SERVICE}"
  else
    apache_cpanel_bind_v2026_on_port "${APP_DIR}" "${LEGACY_PORT}"
    run "sudo_cmd systemctl restart ${V2026_SERVICE}"
    sleep 3
    GUNICORN_PORT="${LEGACY_PORT}"
  fi
  # Ensure userdata proxy targets :5000 if we edited it earlier
  while IFS= read -r dir; do
    [[ -f "${dir}/proxy_flask.conf" ]] && _apache_patch_proxy_port_in_file "${dir}/proxy_flask.conf" "${LEGACY_PORT}"
  done < <(apache_cpanel_userdata_dirs 2>/dev/null || true)
else
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] install vhost ${APACHE_CONF_SRC} -> ${APACHE_CONF_DEST}"
  else
    apache_install_vhost_from_template "${APACHE_CONF_SRC}" "${APACHE_CONF_DEST}"
  fi
  # Disable legacy :443 vhost if separate file
  if [[ -n "${LEGACY_APACHE_CONF:-}" && -f "$LEGACY_APACHE_CONF" && "$LEGACY_APACHE_CONF" != "$APACHE_CONF_DEST" ]]; then
    run "sudo_cmd mv '${LEGACY_APACHE_CONF}' '${LEGACY_APACHE_CONF}.pre-cutover-disabled'"
    echo "Disabled legacy :443 vhost: ${LEGACY_APACHE_CONF}.pre-cutover-disabled"
  fi
fi
run "sudo_cmd apachectl configtest"

# 3. Reload Apache
run "sudo_cmd systemctl reload ${HTTPD_SERVICE}"
health_check "http://127.0.0.1:${GUNICORN_PORT}/api/v1/health" "local Gunicorn" || true
health_check "https://inertiainvest.in/api/v1/health" "public HTTPS (v2026)" || {
  echo "Public health check failed. Consider: bash $0 --rollback" >&2
  exit 1
}

# 4. Stop legacy app (non-cPanel path; cPanel already stopped above)
if ! apache_is_cpanel_proxy_legacy; then
  run "sudo_cmd systemctl stop ${LEGACY_SERVICE} || true"
  run "sudo_cmd systemctl disable ${LEGACY_SERVICE} || true"
fi

# 5. Disable public soak port 5003
if [[ -f "$SOAK_APACHE_CONF" ]]; then
  run "sudo_cmd mv '${SOAK_APACHE_CONF}' '${SOAK_APACHE_CONF}.disabled'"
  run "sudo_cmd systemctl reload ${HTTPD_SERVICE}"
  echo "Disabled soak vhost: ${SOAK_APACHE_CONF}.disabled"
fi

echo ""
echo "Cutover complete."
echo "  App:     https://inertiainvest.in"
echo "  Code:    ${APP_DIR}"
echo "  Service: ${V2026_SERVICE}"
echo "  Backup:  ${BACKUP_DIR}"
echo ""
echo "Next steps:"
echo "  1. Smoke test login, practice analytics, invoices."
echo "  2. Optional private access: bash scripts/deployment/enable_private_access_tailscale.sh"
echo "  3. Optional WhatsApp hooks vhost: see docs/PROD_CUTOVER_AND_ACCESS.md"
echo "  4. Rollback if needed: bash $0 --rollback"
