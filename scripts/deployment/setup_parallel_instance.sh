#!/usr/bin/env bash
# Install a parallel Inertia instance (new directory + port) for soak test before cutover.
#
# Run on the SERVER (as user that will own the app, usually `inertia`):
#   export INSTALL_DIR=/opt/inertia-app-v2026 APP_PORT=5003 GIT_BRANCH=fresh-app-2026-05-26
#   export ENV_SOURCE=/home/inertia/app/.env
#   ./scripts/deployment/setup_parallel_instance.sh
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/inertia-app-v2026}"
APP_PORT="${APP_PORT:-5003}"
GIT_BRANCH="${GIT_BRANCH:-fresh-app-2026-05-26}"
GIT_REMOTE="${GIT_REMOTE:-https://github.com/anshul8382/inertia.git}"
ENV_SOURCE="${ENV_SOURCE:-/home/inertia/app/.env}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
SERVICE_NAME="${SERVICE_NAME:-inertia-app-v2026}"
RUN_USER="${RUN_USER:-inertia}"

die() { echo "ERROR: $*" >&2; exit 1; }

echo "==> Parallel instance setup"
echo "    INSTALL_DIR=$INSTALL_DIR"
echo "    APP_PORT=$APP_PORT"
echo "    GIT_BRANCH=$GIT_BRANCH"
echo "    ENV_SOURCE=$ENV_SOURCE"

command -v git >/dev/null || die "git not installed"
command -v "$PYTHON_BIN" >/dev/null || die "$PYTHON_BIN not found (install python3.11)"

if [[ -d "$INSTALL_DIR" ]]; then
  die "$INSTALL_DIR already exists; remove or set INSTALL_DIR to a new path"
fi

PARENT="$(dirname "$INSTALL_DIR")"
mkdir -p "$PARENT"
if [[ "$(id -un)" == "root" ]]; then
  chown "$RUN_USER:$RUN_USER" "$PARENT" 2>/dev/null || true
fi

echo "==> Clone $GIT_BRANCH"
if [[ "$(id -un)" == "$RUN_USER" ]]; then
  git clone --branch "$GIT_BRANCH" --depth 1 "$GIT_REMOTE" "$INSTALL_DIR"
else
  sudo -u "$RUN_USER" git clone --branch "$GIT_BRANCH" --depth 1 "$GIT_REMOTE" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

echo "==> venv ($PYTHON_BIN)"
"$PYTHON_BIN" -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt

if [[ -f "$ENV_SOURCE" ]]; then
  echo "==> .env from $ENV_SOURCE"
  cp "$ENV_SOURCE" .env
else
  echo "WARN: ENV_SOURCE missing; create .env manually"
  touch .env
fi

# Ensure port and staging flag without destroying other keys
if grep -q '^PORT=' .env 2>/dev/null; then
  sed -i "s/^PORT=.*/PORT=$APP_PORT/" .env
else
  echo "PORT=$APP_PORT" >> .env
fi
if grep -q '^FLASK_ENV=' .env 2>/dev/null; then
  sed -i 's/^FLASK_ENV=.*/FLASK_ENV=staging/' .env
else
  echo "FLASK_ENV=staging" >> .env
fi

echo "==> Smoke import"
./venv/bin/python3 -c "from wsgi import app; assert app is not None; print('smoke OK:', app.name)"

# Gunicorn bind: behind Apache use 5004; direct expose can set GUNICORN_BIND in .env
GUNICORN_BIND_PORT="${GUNICORN_BIND_PORT:-5004}"
if [[ -f "$INSTALL_DIR/config/gunicorn_parallel_v2026.py" ]]; then
  echo "==> Using config/gunicorn_parallel_v2026.py from repo"
else
  die "Missing config/gunicorn_parallel_v2026.py — pull latest fresh-app-2026-05-26"
fi
if grep -q '^GUNICORN_BIND=' .env 2>/dev/null; then
  sed -i "s|^GUNICORN_BIND=.*|GUNICORN_BIND=127.0.0.1:${GUNICORN_BIND_PORT}|" .env
else
  echo "GUNICORN_BIND=127.0.0.1:${GUNICORN_BIND_PORT}" >> .env
fi
GUNICORN_CFG="$INSTALL_DIR/config/gunicorn_parallel_v2026.py"

UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
UNIT_SRC="$INSTALL_DIR/config/inertia-app-v2026.service.template"

cat > "$UNIT_SRC" <<EOF
[Unit]
Description=Inertia App v2026 (parallel / soak test)
After=network.target mysql.service

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${INSTALL_DIR}/.env
Environment="PATH=${INSTALL_DIR}/venv/bin"
Environment="FLASK_ENV=staging"
ExecStart=${INSTALL_DIR}/venv/bin/python3 -m gunicorn -c ${GUNICORN_CFG} wsgi:app
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${INSTALL_DIR}

[Install]
WantedBy=multi-user.target
EOF

echo "==> Install systemd unit (sudo)"
sudo cp "$UNIT_SRC" "$UNIT_DST"
sudo systemctl daemon-reload

echo ""
echo "Done. Next:"
echo "  sudo systemctl enable --now ${SERVICE_NAME}"
echo "  curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:${APP_PORT}/health"
echo "  journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "Cutover later: point Apache ProxyPass to ${APP_PORT} or swap bind to 5000 — see docs/PARALLEL_DEPLOY_V2026.md"
