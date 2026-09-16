#!/usr/bin/env bash
# Staff-only access model (after v2026 cutover):
#   - PUBLIC https://inertiainvest.in → login blocked (Apache lockdown)
#   - STAFF https://<server>.<tailnet>.ts.net → Tailscale Serve → Gunicorn
#   - Office PCs: same as approved devices (Tailscale on company machines)
#   - Home personal browser: blocked (no Tailscale / not approved)
#
# Run ON THE SERVER:
#   cd /opt/Inertia2026v1
#   bash scripts/deployment/enable_private_access_tailscale.sh
#   bash scripts/deployment/enable_private_access_tailscale.sh --with-hooks
#   bash scripts/deployment/enable_private_access_tailscale.sh --with-serve
#
set -euo pipefail

_deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=apache_paths.sh
source "${_deploy_dir}/apache_paths.sh"
apache_detect_layout || exit 1

APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
LOCKDOWN_CONF_SRC="${LOCKDOWN_CONF_SRC:-${APP_DIR}/config/apache-inertiainvest-public-lockdown.conf}"
LEGACY_ACCESS_DEST="${APACHE_INERTIA_DIR}/inertiainvest-private-access.conf"
HOOKS_CONF_SRC="${HOOKS_CONF_SRC:-${APP_DIR}/config/apache-inertiainvest-hooks.conf}"
HTTPD_SERVICE="${HTTPD_SERVICE:-httpd}"

DRY_RUN=0
INSTALL_HOOKS=0
SETUP_SERVE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --with-hooks) INSTALL_HOOKS=1 ;;
    --with-serve) SETUP_SERVE=1 ;;
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

sudo_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] sudo $*"
  elif [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

echo "=== Staff-only access (office + approved Tailscale devices) ==="
echo ""
echo "Model:"
echo "  • Public inertiainvest.in → login/UI denied"
echo "  • Staff use Tailscale URL on company-approved devices"
echo "  • Office = company PCs with Tailscale (no static office IP needed)"
echo "  • Home personal laptop/phone without Tailscale → blocked"
echo ""

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale not installed on server yet."
  echo "  curl -fsSL https://tailscale.com/install.sh | sh"
  echo "  sudo tailscale up"
  echo ""
  echo "Admin console (tailscale.com):"
  echo "  • Device management → enable Device approval"
  echo "  • Invite up to 6 users (Personal free plan)"
  echo "  • Tag server: tag:inertia-server"
  echo "  • Tag each company device: tag:approved-device"
  echo "  • Optional ACL: config/tailscale-acl.example.hujson"
  echo ""
  if [[ "$DRY_RUN" -eq 0 ]]; then
    read -r -p "Install Apache public lockdown anyway? [y/N] " ans
    [[ "${ans,,}" == "y" ]] || exit 0
  fi
else
  echo "Tailscale status:"
  tailscale status 2>/dev/null | head -8 || true
  echo ""
fi

if [[ ! -f "$LOCKDOWN_CONF_SRC" ]]; then
  echo "Missing: $LOCKDOWN_CONF_SRC" >&2
  exit 1
fi

run "sudo_cmd mkdir -p '${APACHE_INERTIA_DIR}'"
# Remove legacy IP-allowlist snippet (wrong model for public hostname).
run "sudo_cmd rm -f '${LEGACY_ACCESS_DEST}'"
run "sudo_cmd cp '${LOCKDOWN_CONF_SRC}' '${LOCKDOWN_CONF_DEST}'"
echo "Installed public lockdown: ${LOCKDOWN_CONF_DEST}"

if [[ "$INSTALL_HOOKS" -eq 1 ]]; then
  if [[ -f "$HOOKS_CONF_SRC" ]]; then
    run "sudo_cmd cp '${HOOKS_CONF_SRC}' '${HOOKS_CONF_DEST}'"
    echo "Installed public webhook vhost: ${HOOKS_CONF_DEST}"
    echo "Meta webhook: https://hooks.inertiainvest.in/api/v1/whatsapp/webhook"
    echo ".env: WHATSAPP_WEBHOOK_URL=https://hooks.inertiainvest.in/api/v1/whatsapp/webhook"
  else
    echo "Hooks config not found: $HOOKS_CONF_SRC" >&2
  fi
fi

run "sudo_cmd apachectl configtest"
run "sudo_cmd systemctl reload ${HTTPD_SERVICE}"

if [[ "$SETUP_SERVE" -eq 1 ]]; then
  run "bash '${APP_DIR}/scripts/deployment/setup_tailscale_serve.sh'"
elif [[ "$DRY_RUN" -eq 0 ]]; then
  echo ""
  echo "Next: expose app on tailnet only:"
  echo "  bash scripts/deployment/setup_tailscale_serve.sh"
fi

echo ""
echo "Done."
echo "  Staff bookmark: https://<your-server>.<tailnet>.ts.net  (from setup_tailscale_serve.sh)"
echo "  Public site:    https://inertiainvest.in → 403 (expected)"
echo "  Disable lockdown: sudo rm ${LOCKDOWN_CONF_DEST} && sudo systemctl reload ${HTTPD_SERVICE}"
