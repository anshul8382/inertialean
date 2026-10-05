#!/usr/bin/env bash
# Run on Lean/KVM via backup_lean_kvm_mysql.sh (reads DB_* from .env without sourcing junk lines).
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
LABEL="${LABEL:-dump}"
cd "$APP_DIR"

load_db_from_env() {
  local f="${1:-.env}"
  [[ -f "$f" ]] || { echo "missing $f" >&2; return 1; }
  _strip() { sed -e "s/^['\"]//" -e "s/['\"]$//" -e 's/\r$//'; }
  DB_USER=$(grep -E '^DB_USER=' "$f" | tail -1 | cut -d= -f2- | _strip)
  DB_PASSWORD=$(grep -E '^DB_PASSWORD=' "$f" | tail -1 | cut -d= -f2- | _strip)
  DB_HOST=$(grep -E '^DB_HOST=' "$f" | tail -1 | cut -d= -f2- | _strip)
  DB_PORT=$(grep -E '^DB_PORT=' "$f" | tail -1 | cut -d= -f2- | _strip)
  DB_NAME=$(grep -E '^DB_NAME=' "$f" | tail -1 | cut -d= -f2- | _strip)
  DB_HOST=${DB_HOST:-127.0.0.1}
  DB_PORT=${DB_PORT:-3306}
  [[ -n "$DB_USER" && -n "$DB_PASSWORD" && -n "$DB_NAME" ]]
}

load_db_from_env .env

OUT="/tmp/inertia_${LABEL}_$(date +%Y%m%d_%H%M%S).sql.gz"
export MYSQL_PWD="$DB_PASSWORD"
mysqldump -u "$DB_USER" -h "$DB_HOST" -P "$DB_PORT" \
  --single-transaction --routines --triggers --no-tablespaces "$DB_NAME" \
  2>/tmp/inertia_"${LABEL}"_mysqldump.err | gzip -c > "$OUT"
unset MYSQL_PWD

if [[ ! -s "$OUT" ]]; then
  echo "ERROR: empty dump $OUT" >&2
  cat "/tmp/inertia_${LABEL}_mysqldump.err" >&2 || true
  exit 1
fi
ls -lh "$OUT" >&2
echo "PATH:$OUT"
