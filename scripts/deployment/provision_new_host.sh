#!/usr/bin/env bash
# Clone Lean VPS → NEW host: same path, same DB names, same credentials (.env as-is).
#
# Source is ALWAYS Lean (129.121.133.25). Not BigRock. Not Mac tree as DB/.env source.
#
# Prerequisites on NEW (one-time OS — not this script):
#   AlmaLinux, sudo SSH user, MySQL/MariaDB, python3, git
#
# Usage (Mac):
#   export NEW_HOST=anshul@NEW_IP
#   export SSH_KEY=~/.ssh/inertia_vps
#   ./scripts/deployment/provision_new_host.sh              # all stages
#   ./scripts/deployment/provision_new_host.sh --from dags  # resume
#
# Stages: dump → copy_secrets → remote_install → airflow → twofa → dags → smoke
#
# What is copied identically from Lean (no re-keying):
#   .env — DB_*, SECRET_KEY, Gmail/SMTP, Zoho, Drive, FORCE_2FA, EMAIL_*, DI_*, …
#   service_account.json (Google Drive / APIs)
#   mysqldump of DB_NAME
#   uploads/ + static/agreements/ + static/uploads/ (rsync)
# External services that already work on Lean keep working on NEW — same secrets,
# same API accounts. Only host IP / DNS / HTTPS differ.
# Code: inertialean git (same tip Lean ships).
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Locked: Lean only
SRC_HOST="${SRC_HOST:-anshul@129.121.133.25}"
NEW_HOST="${NEW_HOST:-}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/inertia_vps}"
APP_DIR="${LEAN_APP_DIR:-/opt/Inertia2026v1}"
ORIGIN_URL="${ORIGIN_URL:-https://github.com/anshul8382/inertialean.git}"
BRANCH="${LEAN_BRANCH:-main}"
# Always copy Lean credentials unless explicitly overridden with a pre-placed .env
COPY_ENV="${COPY_ENV:-1}"
COPY_UPLOADS="${COPY_UPLOADS:-1}"
WORK="${WORK_DIR:-$ROOT/.deploy_preserve/provision_$(date +%Y%m%d_%H%M%S)}"
FROM_STAGE="${FROM_STAGE:-dump}"

die() { echo "ERROR: $*" >&2; exit 1; }
need() { [[ -n "${!1:-}" ]] || die "Set $1"; }

SSH_BASE=(ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
SCP_BASE=(scp -i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
RSYNC_SSH="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

ssh_src() { "${SSH_BASE[@]}" "$SRC_HOST" "$@"; }
ssh_new() { "${SSH_BASE[@]}" "$NEW_HOST" "$@"; }
ssh_new_t() { ssh -i "$SSH_KEY" -t -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$NEW_HOST" "$@"; }

usage() {
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --from) FROM_STAGE="${2:-}"; shift 2 ;;
    *) die "Unknown: $1" ;;
  esac
done

need NEW_HOST
[[ -f "$SSH_KEY" ]] || die "SSH key missing: $SSH_KEY"
[[ "$SRC_HOST" == *129.121.133.25* ]] || die "Source must be Lean (129.121.133.25); got SRC_HOST=$SRC_HOST"
mkdir -p "$WORK"
echo "==> Lean → NEW clone (credentials identical)"
echo "    Work: $WORK"
echo "    SRC=$SRC_HOST  NEW=$NEW_HOST  APP_DIR=$APP_DIR"

stage_ge() {
  local order=(dump copy_secrets remote_install airflow twofa dags smoke)
  local start="$FROM_STAGE" cur="$1" s
  local started=0
  for s in "${order[@]}"; do
    [[ "$s" == "$start" ]] && started=1
    [[ "$started" -eq 1 && "$s" == "$cur" ]] && return 0
  done
  return 1
}

# ----- dump -----
if stage_ge dump; then
  echo ""
  echo "==> [dump] mysqldump on Lean (DB_* from Lean .env)"
  ssh_src bash -s <<EOF
set -euo pipefail
cd '$APP_DIR'
set -a; source .env; set +a
OUT=/tmp/inertia_provision_\$(date +%Y%m%d_%H%M%S).sql.gz
mysqldump -u "\$DB_USER" -p"\$DB_PASSWORD" -h "\${DB_HOST:-127.0.0.1}" -P "\${DB_PORT:-3306}" \
  --single-transaction --routines --triggers "\$DB_NAME" | gzip -c > "\$OUT"
echo "\$OUT"
ls -lh "\$OUT"
EOF
  DUMP_REMOTE=$(ssh_src 'ls -t /tmp/inertia_provision_*.sql.gz 2>/dev/null | head -1')
  [[ -n "$DUMP_REMOTE" ]] || die "No dump file on Lean"
  echo "    Fetching $DUMP_REMOTE"
  "${SCP_BASE[@]}" "$SRC_HOST:$DUMP_REMOTE" "$WORK/db.sql.gz"
fi

# ----- copy_secrets -----
if stage_ge copy_secrets; then
  echo ""
  echo "==> [copy_secrets] Lean .env + SA + DB dump → NEW /tmp (same passwords)"
  if [[ "$COPY_ENV" == "1" ]]; then
    "${SCP_BASE[@]}" "$SRC_HOST:$APP_DIR/.env" "$WORK/.env"
  else
    [[ -f "$WORK/.env" ]] || die "COPY_ENV=0 and $WORK/.env missing"
  fi
  "${SCP_BASE[@]}" "$SRC_HOST:$APP_DIR/service_account.json" "$WORK/service_account.json" 2>/dev/null \
    || echo "    (no service_account.json on Lean — skip)"
  "${SCP_BASE[@]}" "$WORK/db.sql.gz" "$NEW_HOST:/tmp/inertia_provision_db.sql.gz"
  "${SCP_BASE[@]}" "$WORK/.env" "$NEW_HOST:/tmp/inertia_provision.env"
  if [[ -f "$WORK/service_account.json" ]]; then
    "${SCP_BASE[@]}" "$WORK/service_account.json" "$NEW_HOST:/tmp/inertia_provision_sa.json"
  fi
  "${SCP_BASE[@]}" "$ROOT/scripts/deployment/provision_new_host_remote.sh" "$NEW_HOST:/tmp/provision_new_host_remote.sh"
fi

# ----- remote_install -----
if stage_ge remote_install; then
  echo ""
  echo "==> [remote_install] on NEW (sudo may prompt)"
  ssh_new_t "ORIGIN_URL='$ORIGIN_URL' BRANCH='$BRANCH' APP_DIR='$APP_DIR' bash /tmp/provision_new_host_remote.sh install"

  if [[ "$COPY_UPLOADS" == "1" ]]; then
    echo "==> [uploads] rsync Lean → NEW (uploads + agreements)"
    ssh_new "mkdir -p '$APP_DIR/uploads' '$APP_DIR/static/agreements' '$APP_DIR/static/uploads'"
    rsync -az -e "$RSYNC_SSH" \
      "$SRC_HOST:$APP_DIR/uploads/" "$NEW_HOST:$APP_DIR/uploads/" || true
    rsync -az -e "$RSYNC_SSH" \
      "$SRC_HOST:$APP_DIR/static/agreements/" "$NEW_HOST:$APP_DIR/static/agreements/" || true
    rsync -az -e "$RSYNC_SSH" \
      "$SRC_HOST:$APP_DIR/static/uploads/" "$NEW_HOST:$APP_DIR/static/uploads/" || true
    ssh_new_t "sudo chown -R anshul:anshul '$APP_DIR/uploads' '$APP_DIR/static/agreements' '$APP_DIR/static/uploads' 2>/dev/null || true"
  fi
fi

# ----- airflow -----
if stage_ge airflow; then
  echo ""
  echo "==> [airflow] same as Lean (venv + units + migrate)"
  ssh_new_t "APP_DIR='$APP_DIR' bash /tmp/provision_new_host_remote.sh airflow"
fi

# ----- 2FA -----
if stage_ge twofa; then
  echo ""
  echo "==> [twofa] FORCE_2FA + enforce (Lean policy)"
  ssh_new_t "APP_DIR='$APP_DIR' bash /tmp/provision_new_host_remote.sh twofa"
fi

# ----- dags -----
if stage_ge dags; then
  echo ""
  echo "==> [dags] unpause all + trigger smoke"
  ssh_new "APP_DIR='$APP_DIR' bash /tmp/provision_new_host_remote.sh dags"
fi

# ----- smoke -----
if stage_ge smoke; then
  echo ""
  echo "==> [smoke]"
  ssh_new "APP_DIR='$APP_DIR' bash /tmp/provision_new_host_remote.sh smoke"
fi

echo ""
echo "Done. NEW is a Lean clone (same credentials)."
echo "  Login: http://NEW_IP/  (Lean .env already has SESSION_COOKIE_SECURE for HTTP if set)"
echo "  DAG status: ssh $NEW_HOST 'APP_DIR=$APP_DIR bash /tmp/provision_new_host_remote.sh dag-status'"
echo "  Artifacts: $WORK"
