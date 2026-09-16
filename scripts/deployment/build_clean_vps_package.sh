#!/usr/bin/env bash
# Build a repeatable "clean VPS" deployment package on your Mac.
# Output: dist/inertia-clean-vps-<tip>.tar.gz + dist/inertia-clean-vps-latest.tar.gz
#
#   bash scripts/deployment/build_clean_vps_package.sh
#   bash scripts/deployment/build_clean_vps_package.sh fresh-app-2026-05-26
#
# Upload later:
#   scp -i ~/.ssh/inertia_vps dist/inertia-clean-vps-latest.tar.gz anshul@NEW_IP:/tmp/
#   ssh … 'sudo tar -xzf /tmp/inertia-clean-vps-latest.tar.gz -C /opt && sudo bash /opt/Inertia2026v1/scripts/deployment/clean_vps_first_install.sh'
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BRANCH="${1:-}"
cd "$ROOT"

if [[ -n "$BRANCH" ]]; then
  REF="origin/${BRANCH}"
  git fetch origin "${BRANCH}" 2>/dev/null || true
  TIP="$(git rev-parse --short "$REF" 2>/dev/null || git rev-parse --short HEAD)"
  ARCHIVE_REF="$REF"
else
  TIP="$(git rev-parse --short HEAD)"
  ARCHIVE_REF="HEAD"
fi

STAMP="$(date +%Y%m%d)"
OUT_DIR="${ROOT}/dist"
mkdir -p "$OUT_DIR"
STAGE="$(mktemp -d /tmp/inertia-clean-vps-XXXXXX)"
APP_NAME="Inertia2026v1"
PKG_NAME="inertia-clean-vps-${STAMP}-${TIP}"
DEST="${STAGE}/${APP_NAME}"

echo "==> Staging ${ARCHIVE_REF} @ ${TIP}"
mkdir -p "$DEST"
git archive --format=tar "$ARCHIVE_REF" | tar -x -C "$DEST"

# Package extras (not always in git archive paths — ensure present)
mkdir -p "$DEST/scripts/deployment" "$DEST/deployment/clean-vps-package" "$DEST/config"

# Copy install helpers into the tree (source of truth in repo)
cp -f "$ROOT/scripts/deployment/clean_vps_first_install.sh" "$DEST/scripts/deployment/" 2>/dev/null || true
cp -f "$ROOT/scripts/deployment/clean_vps_env_check.sh" "$DEST/scripts/deployment/" 2>/dev/null || true
cp -f "$ROOT/deployment/clean-vps-package/inertia-2026v1.service" "$DEST/deployment/clean-vps-package/" 2>/dev/null || true
cp -f "$ROOT/deployment/clean-vps-package/README.md" "$DEST/deployment/clean-vps-package/" 2>/dev/null || true
cp -f "$ROOT/.env.example" "$DEST/.env.example" 2>/dev/null || true

# Manifest
cat >"$DEST/deployment/clean-vps-package/MANIFEST.txt" <<EOF
package=${PKG_NAME}
git_tip=${TIP}
git_ref=${ARCHIVE_REF}
built_at=$(date -Iseconds)
built_on=$(hostname)
app_dir=/opt/Inertia2026v1
notes=No venv, no .env secrets, no uploads. Restore DB and rsync docs separately.
EOF

TARBALL="${OUT_DIR}/${PKG_NAME}.tar.gz"
echo "==> Creating ${TARBALL}"
tar -czf "$TARBALL" -C "$STAGE" "$APP_NAME"
ln -sfn "$(basename "$TARBALL")" "${OUT_DIR}/inertia-clean-vps-latest.tar.gz"
rm -rf "$STAGE"

ls -lh "$TARBALL" "${OUT_DIR}/inertia-clean-vps-latest.tar.gz"
echo "==> Done. Upload with:"
echo "    scp -i ~/.ssh/inertia_vps ${OUT_DIR}/inertia-clean-vps-latest.tar.gz anshul@NEW_IP:/tmp/"
echo "    # then on server: see deployment/clean-vps-package/README.md"
