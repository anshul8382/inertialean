#!/usr/bin/env bash
# Deploy without GitHub auth on the server: archive from Mac → scp → extract → restart.
# Run on your Mac (interactive SSH to root@server must work).
#
#   bash scripts/deployment/deploy_via_archive.sh
#   bash scripts/deployment/deploy_via_archive.sh root@66.116.199.231
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST="${1:-root@66.116.199.231}"
APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
SERVICE="${INERTIA_SYSTEMD_SERVICE:-inertia-2026v1}"
cd "$ROOT"
TIP="$(git rev-parse --short HEAD)"
ARCHIVE="/tmp/inertia-${TIP}.tar.gz"
echo "==> Archive $TIP → $ARCHIVE"
git archive --format=tar.gz HEAD -o "$ARCHIVE"
ls -lh "$ARCHIVE"
echo "==> scp to $HOST:/tmp/ (enter SSH password if prompted)"
scp "$ARCHIVE" "$HOST:/tmp/"
echo "==> extract + restart on server"
ssh -t "$HOST" bash -s <<REMOTE
set -euo pipefail
cd ${APP_DIR}
tar -xzf /tmp/inertia-${TIP}.tar.gz
rm -f /tmp/inertia-${TIP}.tar.gz
systemctl restart ${SERVICE}
sleep 2
systemctl is-active --quiet ${SERVICE}
curl -s -o /dev/null -w "health:%{http_code}\\n" --max-time 10 http://127.0.0.1:5000/api/v1/health || true
echo "Deployed archive tip ${TIP} into ${APP_DIR}"
# Best-effort mark working tree (git may still show old HEAD)
git rev-parse --short HEAD 2>/dev/null || true
REMOTE
echo "==> Done"
