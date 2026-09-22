#!/usr/bin/env bash
# Runs ON the new host (invoked by provision_new_host.sh). Stages: install|airflow|twofa|dags|dag-status|smoke
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/Inertia2026v1}"
ORIGIN_URL="${ORIGIN_URL:-https://github.com/anshul8382/inertialean.git}"
BRANCH="${BRANCH:-main}"
APP_USER="${INERTIA_APP_USER:-anshul}"
STAGE="${1:-}"

die() { echo "ERROR: $*" >&2; exit 1; }
[[ -n "$STAGE" ]] || die "Usage: $0 install|airflow|twofa|dags|dag-status|smoke"

ensure_root_bits() {
  if [[ "$(id -u)" -ne 0 ]]; then
    exec sudo -E APP_DIR="$APP_DIR" ORIGIN_URL="$ORIGIN_URL" BRANCH="$BRANCH" \
      APP_USER="$APP_USER" bash "$0" "$STAGE"
  fi
}

stage_install() {
  ensure_root_bits
  command -v git >/dev/null || dnf install -y git
  command -v python3 >/dev/null || dnf install -y python3 python3-pip python3-devel
  [[ -f /tmp/inertia_provision_db.sql.gz ]] || die "missing /tmp/inertia_provision_db.sql.gz"
  [[ -f /tmp/inertia_provision.env ]] || die "missing /tmp/inertia_provision.env"

  mkdir -p "$APP_DIR"
  if [[ ! -d "$APP_DIR/.git" ]]; then
    echo "==> git clone $ORIGIN_URL → $APP_DIR"
    if [[ -z "$(ls -A "$APP_DIR" 2>/dev/null || true)" ]]; then
      git clone -b "$BRANCH" "$ORIGIN_URL" "$APP_DIR"
    else
      # non-empty: bootstrap in place
      cd "$APP_DIR"
      git init -b "$BRANCH"
      git remote add origin "$ORIGIN_URL" 2>/dev/null || git remote set-url origin "$ORIGIN_URL"
      git fetch origin --prune
      git checkout -f -B "$BRANCH" "origin/$BRANCH"
    fi
  else
    cd "$APP_DIR"
    git fetch origin --prune
    git checkout "$BRANCH"
    git pull --ff-only origin "$BRANCH"
  fi

  install -m 600 /tmp/inertia_provision.env "$APP_DIR/.env"
  if [[ -f /tmp/inertia_provision_sa.json ]]; then
    install -m 600 /tmp/inertia_provision_sa.json "$APP_DIR/service_account.json"
  fi

  # Ensure 2FA flag present
  grep -q '^FORCE_2FA_FOR_ALL_USERS=' "$APP_DIR/.env" \
    || echo 'FORCE_2FA_FOR_ALL_USERS=true' >> "$APP_DIR/.env"
  sed -i 's/^FORCE_2FA_FOR_ALL_USERS=.*/FORCE_2FA_FOR_ALL_USERS=true/' "$APP_DIR/.env"

  set -a
  # shellcheck disable=SC1090
  source "$APP_DIR/.env"
  set +a
  DB_HOST="${DB_HOST:-127.0.0.1}"
  DB_PORT="${DB_PORT:-3306}"
  [[ -n "${DB_NAME:-}" && -n "${DB_USER:-}" && -n "${DB_PASSWORD:-}" ]] || die ".env missing DB_*"

  echo "==> MySQL create DB/user (idempotent) + import"
  # Fresh DB on each install so a failed mid-import can be retried cleanly
  mysql -u root <<SQL
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
  # Import as root (not app user): dumps often contain DEFINER= on views/routines
  # and MySQL 8.4 app users lack SET_ANY_DEFINER / SUPER.
  gunzip -c /tmp/inertia_provision_db.sql.gz \
    | sed -e 's/DEFINER=`[^`]*`@`[^`]*`//g' \
    | mysql --database="$DB_NAME"
  echo "    DB import OK"

  echo "==> App venv + pip"
  cd "$APP_DIR"
  [[ -d venv ]] || python3 -m venv venv
  ./venv/bin/pip install -U pip wheel
  ./venv/bin/pip install -r requirements.txt

  UNIT_SRC="$APP_DIR/deployment/clean-vps-package/inertia-2026v1.service"
  if [[ -f "$UNIT_SRC" ]]; then
    cp "$UNIT_SRC" /etc/systemd/system/inertia-2026v1.service
    sed -i "s|/opt/Inertia2026v1|${APP_DIR}|g" /etc/systemd/system/inertia-2026v1.service
    sed -i "s|User=anshul|User=${APP_USER}|g" /etc/systemd/system/inertia-2026v1.service
    sed -i "s|Group=anshul|Group=${APP_USER}|g" /etc/systemd/system/inertia-2026v1.service
    systemctl daemon-reload
    systemctl enable --now inertia-2026v1
  else
    echo "WARN: missing $UNIT_SRC — start Gunicorn manually"
  fi

  chown -R "${APP_USER}:${APP_USER}" "$APP_DIR" || true
  # keep .env readable by app user only
  chmod 600 "$APP_DIR/.env"
  chown "${APP_USER}:${APP_USER}" "$APP_DIR/.env"

  sleep 2
  curl -s -o /dev/null -w "health:%{http_code}\n" http://127.0.0.1:5004/api/v1/health || true
  echo "install stage done"
}

stage_airflow() {
  ensure_root_bits
  cd "$APP_DIR"
  PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  [[ -d airflow_venv ]] || python3 -m venv airflow_venv
  # shellcheck disable=SC1091
  source airflow_venv/bin/activate
  pip install -U pip
  pip install 'apache-airflow==3.0.6' apache-airflow-providers-fab \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.0.6/constraints-${PYVER}.txt"

  export INERTIA_APP_DIR="$APP_DIR"
  export AIRFLOW_HOME="$APP_DIR/airflow"
  export AIRFLOW_CMD="$APP_DIR/airflow_venv/bin/airflow"
  export AIRFLOW__CORE__EXECUTOR=SequentialExecutor
  export AIRFLOW__CORE__DAGS_FOLDER="$APP_DIR/airflow/dags"
  set -a
  # shellcheck disable=SC1090
  source "$APP_DIR/.env"
  set +a
  export AIRFLOW__SMTP__SMTP_HOST="${MAIL_SERVER:-smtp.gmail.com}"
  export AIRFLOW__SMTP__SMTP_PORT="${MAIL_PORT:-587}"
  export AIRFLOW__SMTP__SMTP_STARTTLS=True
  export AIRFLOW__SMTP__SMTP_USER="${MAIL_USERNAME:-}"
  export AIRFLOW__SMTP__SMTP_PASSWORD="${MAIL_PASSWORD:-}"
  export AIRFLOW__SMTP__SMTP_MAIL_FROM="${MAIL_DEFAULT_SENDER:-$MAIL_USERNAME}"
  export AIRFLOW__EMAIL__SUBJECT_TEMPLATE="$APP_DIR/airflow/email_templates/failure_subject.jinja2"
  export AIRFLOW__EMAIL__HTML_CONTENT_TEMPLATE="$APP_DIR/airflow/email_templates/failure_content.html"

  cd /tmp
  sudo -u "$APP_USER" env \
    INERTIA_APP_DIR="$INERTIA_APP_DIR" \
    AIRFLOW_HOME="$AIRFLOW_HOME" \
    AIRFLOW_CMD="$AIRFLOW_CMD" \
    AIRFLOW__CORE__EXECUTOR=SequentialExecutor \
    PATH="$APP_DIR/airflow_venv/bin:$PATH" \
    "$APP_DIR/venv/bin/python" "$APP_DIR/scripts/setup_airflow.py" \
    || "$APP_DIR/venv/bin/python" "$APP_DIR/scripts/setup_airflow.py"

  for pair in \
    "airflow-scheduler-lean.service:airflow-scheduler.service" \
    "airflow-api-server-lean.service:airflow-api-server.service" \
    "airflow-dag-processor-lean.service:airflow-dag-processor.service"
  do
    src_name="${pair%%:*}"
    dst_name="${pair##*:}"
    src="$APP_DIR/config/$src_name"
    dst="/etc/systemd/system/$dst_name"
    [[ -f "$src" ]] || { echo "WARN: missing $src"; continue; }
    cp "$src" "$dst"
    sed -i "s|/opt/Inertia2026v1|${APP_DIR}|g" "$dst"
    sed -i "s|User=anshul|User=${APP_USER}|g" "$dst"
    sed -i "s|Group=anshul|Group=${APP_USER}|g" "$dst"
  done

  chown -R "${APP_USER}:${APP_USER}" "$APP_DIR/airflow" "$APP_DIR/airflow_venv" || true
  systemctl daemon-reload
  systemctl enable --now airflow-scheduler airflow-api-server airflow-dag-processor
  sleep 3
  systemctl is-active airflow-scheduler airflow-api-server airflow-dag-processor || true
  echo "airflow stage done"
}

stage_twofa() {
  # may run as app user
  cd "$APP_DIR"
  grep -q '^FORCE_2FA_FOR_ALL_USERS=true' .env || {
    grep -q '^FORCE_2FA_FOR_ALL_USERS=' .env \
      && sed -i 's/^FORCE_2FA_FOR_ALL_USERS=.*/FORCE_2FA_FOR_ALL_USERS=true/' .env \
      || echo 'FORCE_2FA_FOR_ALL_USERS=true' >> .env
  }
  ./venv/bin/python migrations/enforce_mandatory_2fa_all_users.py
  if systemctl is-active --quiet inertia-2026v1 2>/dev/null; then
    sudo systemctl restart inertia-2026v1 || true
  fi
  echo "twofa stage done (users must enroll TOTP on next login if not already)"
}

stage_dags() {
  export AIRFLOW_HOME="$APP_DIR/airflow"
  export PATH="$APP_DIR/airflow_venv/bin:$PATH"
  cd /tmp
  sleep 15
  airflow dags list -o plain | head -5 || true
  # unpause all
  while read -r d; do
    [[ -z "$d" || "$d" == "dag_id" ]] && continue
    airflow dags unpause "$d" >/dev/null 2>&1 || true
  done < <(airflow dags list -o plain 2>/dev/null | awk 'NR>1 {print $1}')
  bash "$APP_DIR/scripts/airflow_trigger_all_dags.sh" || true
  echo "dags stage done — wait then: bash $0 dag-status"
}

stage_dag_status() {
  export AIRFLOW_HOME="$APP_DIR/airflow"
  export PATH="$APP_DIR/airflow_venv/bin:$PATH"
  cd /tmp
  bash "$APP_DIR/scripts/airflow_trigger_all_dags.sh" --status-only
}

stage_smoke() {
  curl -s -o /dev/null -w "health:%{http_code}\n" http://127.0.0.1:5004/api/v1/health || true
  systemctl is-active inertia-2026v1 2>/dev/null || true
  systemctl is-active airflow-scheduler airflow-api-server 2>/dev/null || true
  # Same Lean .env → Gmail/Zoho/Drive should pass without reconfiguration
  if [[ -x "$APP_DIR/venv/bin/python" && -f "$APP_DIR/scripts/check_external_integrations.py" ]]; then
    echo "==> external integrations (Lean credentials on NEW)"
    (cd "$APP_DIR" && ./venv/bin/python scripts/check_external_integrations.py) || true
  fi
  echo "smoke done"
}

case "$STAGE" in
  install) stage_install ;;
  airflow) stage_airflow ;;
  twofa) stage_twofa ;;
  dags) stage_dags ;;
  dag-status) stage_dag_status ;;
  smoke) stage_smoke ;;
  *) die "Unknown stage: $STAGE" ;;
esac
