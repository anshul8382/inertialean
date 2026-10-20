#!/usr/bin/env bash
# Pull KVM MySQL → local inertia_app2025_dev (KVM is READ-ONLY).
#
# On KVM this script only runs mysqldump + scp. It never DROP/CREATE/imports
# on the server. Do NOT use backup_lean_kvm_mysql.sh --refresh-kvm for this.
#
# Prereq (Mac):
#   ssh-add ~/.ssh/inertia_vps
#   ./scripts/start_local_mysql.sh
#
# Usage:
#   ./scripts/deployment/pull_kvm_db_to_local.sh
#   ./scripts/deployment/pull_kvm_db_to_local.sh --dump-only   # backup file only
#   ./scripts/deployment/pull_kvm_db_to_local.sh --yes         # skip confirm
#
# Env:
#   KVM_HOST, SSH_KEY, APP_DIR, LOCAL_DB, LOCAL_MYSQL_*, BACKUP_DIR
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/inertia_vps}"
KVM_HOST="${KVM_HOST:-anshul@187.127.188.97}"
APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
LOCAL_DB="${LOCAL_DB:-inertia_app2025_dev}"
TS="$(date +%Y%m%d_%H%M%S)"
DUMP_ONLY=0
YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dump-only) DUMP_ONLY=1; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

SSH=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15)
SCP=(scp -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15)
die() { echo "ERROR: $*" >&2; exit 1; }

[[ -f "$SSH_KEY" ]] || die "SSH key not found: $SSH_KEY"
mkdir -p "$BACKUP_DIR"

REMOTE_DUMP="$ROOT/scripts/deployment/_remote_mysql_dump.sh"
[[ -f "$REMOTE_DUMP" ]] || die "Missing $REMOTE_DUMP"

echo "==> KVM is READ-ONLY for this script (mysqldump only; no remote import)"
echo "==> Target local DB: $LOCAL_DB"
echo "==> SSH check $KVM_HOST"
"${SSH[@]}" "$KVM_HOST" 'echo kvm_ok' || die "Cannot SSH to KVM. Run: ssh-add $SSH_KEY"

KVM_LOCAL="$BACKUP_DIR/kvm_inertia_app2025_${TS}.sql.gz"
echo ""
echo "==> Dump KVM (read-only mysqldump)"
"${SCP[@]}" "$REMOTE_DUMP" "$KVM_HOST:/tmp/inertia_remote_mysql_dump.sh"
remote_out="$("${SSH[@]}" "$KVM_HOST" "APP_DIR='$APP_DIR' LABEL='kvm' bash /tmp/inertia_remote_mysql_dump.sh" 2>&1)" \
  || die "Dump failed on KVM: $remote_out"
remote_path="$(printf '%s\n' "$remote_out" | sed -n 's/^PATH://p' | tail -1)"
[[ -n "$remote_path" ]] || die "No dump path from KVM (out=$remote_out)"
echo "    Remote dump: $remote_path"
"${SCP[@]}" "$KVM_HOST:$remote_path" "$KVM_LOCAL"
ls -lh "$KVM_LOCAL"
# Optional: keep a copy under app backups on KVM (still read of dump file only)
"${SSH[@]}" "$KVM_HOST" "mkdir -p '$APP_DIR/backups' 2>/dev/null; cp -a '$remote_path' '$APP_DIR/backups/' 2>/dev/null || true"

if [[ "$DUMP_ONLY" -eq 1 ]]; then
  echo ""
  echo "Done (dump only). File: $KVM_LOCAL"
  echo "Restore later with:"
  echo "  gunzip -c '$KVM_LOCAL' | mysql -h 127.0.0.1 -P 3306 -u … $LOCAL_DB"
  exit 0
fi

# Local MySQL client
LOCAL_BIN="$ROOT/.local/micromamba/envs/inertia-mysql/bin"
if [[ -x "$LOCAL_BIN/mysql" ]]; then
  MYSQL=( "$LOCAL_BIN/mysql" )
  MYSQLADMIN=( "$LOCAL_BIN/mysqladmin" )
  SOCK_ARGS=( --socket="$ROOT/.local/mysql.sock" )
else
  MYSQL=( mysql )
  MYSQLADMIN=( mysqladmin )
  SOCK_ARGS=()
fi

# Prefer local .env for local credentials (never use KVM passwords to write remote)
load_local_db() {
  local f="$ROOT/.env"
  [[ -f "$f" ]] || return 1
  _strip() { sed -e "s/^['\"]//" -e "s/['\"]$//" -e 's/\r$//'; }
  # shellcheck disable=SC2034
  LOCAL_USER=$(grep -E '^DB_USER=' "$f" | tail -1 | cut -d= -f2- | _strip)
  LOCAL_PASS=$(grep -E '^DB_PASSWORD=' "$f" | tail -1 | cut -d= -f2- | _strip)
  LOCAL_HOST=$(grep -E '^DB_HOST=' "$f" | tail -1 | cut -d= -f2- | _strip)
  LOCAL_PORT=$(grep -E '^DB_PORT=' "$f" | tail -1 | cut -d= -f2- | _strip)
  LOCAL_HOST=${LOCAL_HOST:-127.0.0.1}
  LOCAL_PORT=${LOCAL_PORT:-3306}
  [[ -n "${LOCAL_USER:-}" ]]
}

if ! load_local_db; then
  LOCAL_USER="${LOCAL_MYSQL_USER:-root}"
  LOCAL_PASS="${LOCAL_MYSQL_PASSWORD:-}"
  LOCAL_HOST="${LOCAL_MYSQL_HOST:-127.0.0.1}"
  LOCAL_PORT="${LOCAL_MYSQL_PORT:-3306}"
fi

echo ""
echo "==> Ensure local MySQL is up"
./scripts/start_local_mysql.sh || true

echo "==> Ping local MySQL"
if [[ -n "${LOCAL_PASS:-}" ]]; then
  export MYSQL_PWD="$LOCAL_PASS"
fi
if ! "${MYSQLADMIN[@]}" "${SOCK_ARGS[@]}" -h "$LOCAL_HOST" -P "$LOCAL_PORT" -u "$LOCAL_USER" ping --silent 2>/dev/null; then
  # root via socket often works for micromamba local
  if "${MYSQLADMIN[@]}" "${SOCK_ARGS[@]}" -uroot ping --silent 2>/dev/null; then
    LOCAL_USER=root
    LOCAL_PASS=""
    unset MYSQL_PWD || true
  else
    die "Local MySQL not reachable. Start with ./scripts/start_local_mysql.sh"
  fi
fi

if [[ "$YES" -eq 0 ]]; then
  echo ""
  echo "This will REPLACE local database '$LOCAL_DB' with the KVM dump."
  echo "KVM itself will NOT be modified."
  read -r -p "Type PULL-KVM to confirm: " CONFIRM
  [[ "$CONFIRM" == "PULL-KVM" ]] || die "Aborted"
fi

echo ""
echo "==> Recreate local $LOCAL_DB and import (localhost only)"
"${MYSQL[@]}" "${SOCK_ARGS[@]}" -h "$LOCAL_HOST" -P "$LOCAL_PORT" -u "$LOCAL_USER" \
  -e "DROP DATABASE IF EXISTS \`$LOCAL_DB\`; CREATE DATABASE \`$LOCAL_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
# Strip DEFINER (needs SUPER on some dumps) — local-only import
gunzip -c "$KVM_LOCAL" \
  | LC_ALL=C sed -e 's/DEFINER=`[^`]*`@`[^`]*`//g' \
                 -e 's/DEFINER=[^*]*\*/\*/g' \
  | "${MYSQL[@]}" "${SOCK_ARGS[@]}" -h "$LOCAL_HOST" -P "$LOCAL_PORT" -u "$LOCAL_USER" "$LOCAL_DB"
unset MYSQL_PWD || true

echo ""
echo "Done. Local '$LOCAL_DB' refreshed from KVM (read-only pull)."
echo "  Dump kept at: $KVM_LOCAL"
echo "  Point .env DB_NAME=$LOCAL_DB if needed, then restart Flask."
