#!/usr/bin/env bash
# Keep the usual server deploy (deploy_parallel_v2026.sh) when the server
# cannot talk to GitHub (no password/PAT/deploy key).
#
# From your Mac (SSH to the server must work interactively):
#   bash scripts/deployment/deploy_via_bundle.sh
#   bash scripts/deployment/deploy_via_bundle.sh root@66.116.199.231
#
# What it does:
#   1. git bundle the branch on Mac (Mac already has GitHub access)
#   2. scp bundle to server
#   3. git fetch bundle → origin/<branch>
#   4. run the SAME: bash scripts/deployment/deploy_parallel_v2026.sh <branch> --skip-fetch
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST="${1:-root@66.116.199.231}"
BRANCH="${BRANCH:-fresh-app-2026-05-26}"
APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
cd "$ROOT"

TIP="$(git rev-parse --short "origin/${BRANCH}" 2>/dev/null || git rev-parse --short HEAD)"
BUNDLE="/tmp/inertia-${BRANCH//\//-}-${TIP}.bundle"

echo "==> Bundle origin/${BRANCH} @ ${TIP}"
git fetch origin "${BRANCH}" 2>/dev/null || true
git bundle create "$BUNDLE" "origin/${BRANCH}"
ls -lh "$BUNDLE"

echo "==> scp + deploy_parallel --skip-fetch on ${HOST} (SSH password OK)"
scp "$BUNDLE" "${HOST}:/tmp/inertia-deploy.bundle"

ssh -t "$HOST" bash -s <<REMOTE
set -euo pipefail
cd ${APP_DIR}
git config --global --add safe.directory ${APP_DIR} 2>/dev/null || true
# Update origin ref from Mac bundle — no GitHub credentials needed
git fetch /tmp/inertia-deploy.bundle "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}" \
  || git fetch /tmp/inertia-deploy.bundle "+refs/remotes/origin/${BRANCH}:refs/remotes/origin/${BRANCH}"
rm -f /tmp/inertia-deploy.bundle
echo "==> origin/${BRANCH} now:"
git rev-parse --short "origin/${BRANCH}"
git log -1 --oneline "origin/${BRANCH}"
# Same command you always run — offline sync
bash scripts/deployment/deploy_parallel_v2026.sh ${BRANCH} --skip-fetch
REMOTE

echo "==> Done (same deploy_parallel path; no GitHub password on server)"
