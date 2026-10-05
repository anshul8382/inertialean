#!/usr/bin/env bash
# Backup Lean + KVM MySQL, optionally refresh KVM DB from Lean dump.
#
# Source of truth for refresh: Lean only (129.121.133.25).
# Never BigRock. Keeps KVM .env (DB passwords / secrets) unchanged.
#
# Prereq (Mac):
#   ssh-add ~/.ssh/inertia_vps
#
# Usage:
#   ./scripts/deployment/backup_lean_kvm_mysql.sh              # backups only → backups/
#   ./scripts/deployment/backup_lean_kvm_mysql.sh --refresh-kvm # backup both, then Lean → KVM
#
# Env:
#   SSH_KEY, LEAN_HOST, KVM_HOST, APP_DIR, BACKUP_DIR
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/inertia_vps}"
LEAN_HOST="${LEAN_HOST:-anshul@129.121.133.25}"
KVM_HOST="${KVM_HOST:-anshul@187.127.188.97}"
APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
TS="$(date +%Y%m%d_%H%M%S)"
REFRESH_KVM=0
YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --refresh-kvm) REFRESH_KVM=1; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
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

echo "==> SSH check"
"${SSH[@]}" "$LEAN_HOST" 'echo lean_ok' || die "Cannot SSH to Lean ($LEAN_HOST). Run: ssh-add $SSH_KEY"
"${SSH[@]}" "$KVM_HOST" 'echo kvm_ok' || die "Cannot SSH to KVM ($KVM_HOST). Run: ssh-add $SSH_KEY"

REMOTE_DUMP="$ROOT/scripts/deployment/_remote_mysql_dump.sh"
[[ -f "$REMOTE_DUMP" ]] || die "Missing $REMOTE_DUMP"

remote_dump() {
  local host="$1" label="$2" out_local="$3"
  echo ""
  echo "==> Dump $label ($host)"
  local remote_out remote_path
  "${SCP[@]}" "$REMOTE_DUMP" "$host:/tmp/inertia_remote_mysql_dump.sh"
  remote_out="$("${SSH[@]}" "$host" "APP_DIR='$APP_DIR' LABEL='$label' bash /tmp/inertia_remote_mysql_dump.sh" 2>&1)" \
    || die "Dump failed on $label ($host): $remote_out"
  remote_path="$(printf '%s\n' "$remote_out" | sed -n 's/^PATH://p' | tail -1)"
  [[ -n "$remote_path" ]] || die "No dump path from $label (out=$remote_out)"
  echo "    Remote: $remote_path"
  "${SCP[@]}" "$host:$remote_path" "$out_local"
  ls -lh "$out_local"
  "${SSH[@]}" "$host" "mkdir -p '$APP_DIR/backups' 2>/dev/null; cp -a '$remote_path' '$APP_DIR/backups/' 2>/dev/null || true"
}

LEAN_LOCAL="$BACKUP_DIR/lean_inertia_app2025_${TS}.sql.gz"
KVM_LOCAL="$BACKUP_DIR/kvm_inertia_app2025_${TS}.sql.gz"

remote_dump "$LEAN_HOST" "lean" "$LEAN_LOCAL"
remote_dump "$KVM_HOST" "kvm" "$KVM_LOCAL"

echo ""
echo "==> Local backups"
ls -lh "$LEAN_LOCAL" "$KVM_LOCAL"

if [[ "$REFRESH_KVM" -eq 0 ]]; then
  echo ""
  echo "Done (backup only). To refresh KVM from Lean:"
  echo "  $0 --refresh-kvm"
  exit 0
fi

echo ""
echo "==> Refresh KVM DB from Lean dump (KVM .env kept; app DB dropped/recreated)"
echo "    Source: $LEAN_LOCAL"
if [[ "$YES" -eq 1 ]]; then
  CONFIRM="REFRESH-KVM"
else
  read -r -p "Type REFRESH-KVM to confirm overwrite of KVM MySQL data: " CONFIRM
fi
[[ "$CONFIRM" == "REFRESH-KVM" ]] || die "Aborted (confirmation mismatch)"

"${SCP[@]}" "$LEAN_LOCAL" "$KVM_HOST:/tmp/inertia_lean_to_kvm.sql.gz"

# Import remotely via a script file (avoids nested-heredoc quoting bugs)
REMOTE_IMPORT=$(mktemp)
cat > "$REMOTE_IMPORT" <<'REMOTE'
#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
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
load_db_from_env .env || { echo "KVM .env missing DB_*"; exit 1; }
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"

echo "==> Stop Gunicorn during import"
sudo systemctl stop inertia-2026v1 || true

echo "==> DROP/CREATE ${DB_NAME} + import Lean dump (as root, strip DEFINER)"
sudo mysql <<SQL
DROP DATABASE IF EXISTS \`${DB_NAME}\`;
CREATE DATABASE \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
GRANT ALL ON \`${DB_NAME}\`.* TO '${DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

gunzip -c /tmp/inertia_lean_to_kvm.sql.gz \
  | sed -e 's/DEFINER=`[^`]*`@`[^`]*`//g' \
  | sudo mysql --database="$DB_NAME"

echo "==> Restart Gunicorn + health"
sudo systemctl reset-failed inertia-2026v1 2>/dev/null || true
sudo systemctl start inertia-2026v1
sudo systemctl is-active inertia-2026v1
curl -s -o /dev/null -w 'health:%{http_code}\n' http://127.0.0.1:5004/api/v1/health || true
echo "==> KVM HEAD:"
git -C "$APP_DIR" log -1 --oneline || true
REMOTE

"${SCP[@]}" "$REMOTE_IMPORT" "$KVM_HOST:/tmp/inertia_lean_to_kvm_import.sh"
rm -f "$REMOTE_IMPORT"
ssh -i "$SSH_KEY" -t -o IdentitiesOnly=yes "$KVM_HOST" "APP_DIR='$APP_DIR' bash /tmp/inertia_lean_to_kvm_import.sh"

echo ""
echo "Done. KVM MySQL refreshed from Lean."
echo "  Lean backup: $LEAN_LOCAL"
echo "  KVM pre-refresh backup: $KVM_LOCAL"
