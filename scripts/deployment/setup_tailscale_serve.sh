#!/usr/bin/env bash
# Expose the app on the Tailscale tailnet only (not the public internet).
#
# Staff open: https://<hostname>.<tailnet>.ts.net
# Public https://inertiainvest.in stays locked down (Apache deny).
#
# Run ON THE SERVER (after tailscale up):
#   cd /opt/Inertia2026v1
#   bash scripts/deployment/setup_tailscale_serve.sh
#   bash scripts/deployment/setup_tailscale_serve.sh --dry-run
#
set -euo pipefail

GUNICORN_PORT="${GUNICORN_PORT:-5004}"
UPSTREAM="${UPSTREAM:-http://127.0.0.1:${GUNICORN_PORT}}"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] $*"
  else
    echo "+ $*"
    "$@"
  fi
}

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Install Tailscale first:" >&2
  echo "  curl -fsSL https://tailscale.com/install.sh | sh" >&2
  echo "  sudo tailscale up" >&2
  exit 1
fi

if ! tailscale status >/dev/null 2>&1; then
  echo "Tailscale is not connected. Run: sudo tailscale up" >&2
  exit 1
fi

HOSTNAME=$(tailscale status --json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
self=d.get('Self',{})
dns=self.get('DNSName','').rstrip('.')
print(dns or self.get('HostName','inertia-server'))
" 2>/dev/null || echo "inertia-server")

echo "=== Tailscale Serve → ${UPSTREAM} ==="
echo "Staff URL (tailnet only): https://${HOSTNAME}"
echo ""

# Proxy HTTPS on tailnet to local Gunicorn (does not bind public :443).
run sudo tailscale serve reset
run sudo tailscale serve --bg --https=443 "${UPSTREAM}"

echo ""
echo "Verify (from an approved device on Tailscale):"
echo "  curl -sI https://${HOSTNAME}/api/v1/health"
echo ""
echo "Tag this server in admin console: tag:inertia-server"
echo "Tag each company laptop/phone: tag:approved-device"
echo "See config/tailscale-acl.example.hujson"
