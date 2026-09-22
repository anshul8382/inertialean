#!/usr/bin/env bash
# Run ON BigRock as root. Builds one curated tree to copy off-box (WinSCP/rsync).
# Usage:
#   bash assemble_bigrock_inertia_export.sh
#   bash assemble_bigrock_inertia_export.sh /home/inertia/EXPORT_for_archive
#
set -euo pipefail

DEST="${1:-/home/inertia/EXPORT_for_archive_$(date +%Y%m%d)}"
SRC_HOME="${SRC_HOME:-/home/inertia}"
SRC_OPT="${SRC_OPT:-/opt/Inertia2026v1}"

RSYNC_EXCLUDES=(
  --exclude 'EXPORT_for_archive*/'
  --exclude 'backup-*/'
  --exclude 'backup-*.tar.gz'
  --exclude 'backups/'
  --exclude 'inertia_codebase_backups/'
  --exclude '*_backup_*/'
  --exclude '*backup*.tar.gz'
  --exclude '*backup*.sql'
  --exclude 'database_backup_*.sql'
  --exclude 'app_backup_*.tar.gz'
  --exclude 'app-backup-*.tar.gz'
  --exclude 'equities4wealth-backup-*.tar.gz'
  --exclude 'cpmove_failed_mysql_dbs.*'
  --exclude 'cpmove-*.tar.gz'
  --exclude 'SYS-SNAP/'
  --exclude 'venv/'
  --exclude 'airflow_venv/'
  --exclude '*/venv/'
  --exclude '*/airflow_venv/'
  --exclude '.env'
  --exclude '*/.env'
  --exclude 'service_account.json'
  --exclude '*/service_account.json'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.pytest_cache/'
  --exclude 'node_modules/'
  --exclude 'mail/'
  --exclude 'tmp/'
  --exclude 'access-logs/'
  --exclude 'logs/'
  --exclude '*/logs/'
  --exclude '.cache/'
  --exclude 'app_test/'
  --exclude 'app_test_*/'
  --exclude 'app_test_*'
  --exclude '*_test_backup*/'
)

echo "==> Export destination: $DEST"
rm -rf "$DEST"
mkdir -p "$DEST/home_inertia" "$DEST/opt_Inertia2026v1"

if [[ -d "$SRC_HOME" ]]; then
  echo "==> Sync $SRC_HOME → $DEST/home_inertia"
  rsync -a "${RSYNC_EXCLUDES[@]}" "$SRC_HOME/" "$DEST/home_inertia/"
else
  echo "WARN: missing $SRC_HOME"
fi

if [[ -d "$SRC_OPT" ]]; then
  echo "==> Sync $SRC_OPT → $DEST/opt_Inertia2026v1"
  rsync -a "${RSYNC_EXCLUDES[@]}" \
    --exclude 'uploads/' \
    --exclude 'static/uploads/' \
    --exclude 'static/agreements/' \
    "$SRC_OPT/" "$DEST/opt_Inertia2026v1/"
  # uploads/agreements already archived on Mac; skip here to save space.
  # Remove the three --exclude lines above if you want them in this export too.
else
  echo "WARN: missing $SRC_OPT"
fi

# Manifest for the Windows copy step
{
  echo "assembled_at=$(date -Is)"
  echo "host=$(hostname)"
  echo "dest=$DEST"
  du -sh "$DEST" "$DEST/home_inertia" "$DEST/opt_Inertia2026v1" 2>/dev/null || true
  echo "--- top home_inertia ---"
  ls -1 "$DEST/home_inertia" | head -80
} | tee "$DEST/MANIFEST.txt"

echo ""
echo "Done. Copy THIS folder only with WinSCP:"
echo "  Remote: $DEST"
echo "  Local:  D:\\Backups\\bigrock-20260922\\EXPORT_for_archive\\"
echo ""
echo "du:"
du -sh "$DEST"
