# Run commands — local Mac vs new VPS

Canonical copy for git / VPS. Mac deployment agent: `.local/deployment-agent/RUN_COMMANDS.md`  
Print on Mac: `python3 .local/deployment-agent/deploy_agent.py run-commands`

Local uses **existing MySQL** from `app 2` (`inertia_app2025_dev` on `127.0.0.1:3306`).  
Server: **new localhost DB**; restore a **prod dump later** (do not tunnel prod for this lean first run).

---

## A. Local Mac (Inertia2026-lean)

```bash
cd /Users/anshulkhare/Downloads/Inertia2026-lean

# 1) Reuse app 2's local MySQL data/binaries (once)
ln -sfn "/Users/anshulkhare/Downloads/app 2/.local" .local

# 2) Start local MySQL (keep this terminal open, or run in background)
bash scripts/start_local_mysql.sh

# 3) Python env (once)
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# If you already installed once, still run this after requirements.txt changes:
# pip install -r requirements.txt

# 4) Env (once) — copy secrets from app 2, then force local DB + AI off
cp "/Users/anshulkhare/Downloads/app 2/.env" .env
chmod 600 .env
# In .env set:
#   FLASK_ENV=development
#   DB_HOST=127.0.0.1
#   DB_PORT=3306
#   DB_NAME=inertia_app2025_dev
#   DB_USER=inertia_admin
#   ENABLE_AI_SERVICES=false
#   OLLAMA_ENABLED=false
#   SESSION_COOKIE_SECURE=False
#   REMEMBER_COOKIE_SECURE=False

# 5) Smoke DB
export FLASK_ENV=development
python -c "
from main import create_app
from config import DevelopmentConfig
from extensions import db
app = create_app(DevelopmentConfig)
with app.app_context():
    db.session.execute(db.text('SELECT 1'))
    print('DB OK', app.config['DB_NAME'])
"

# 6) Run app
python run_app.py
# Open http://127.0.0.1:5001/
```

If MySQL is already running: skip step 2. Login with an existing **dev** user (same as `app 2` local DB). 2FA is on if `FORCE_2FA_FOR_ALL_USERS=true`.

---

## B. New VPS (later — new DB, then copy prod)

Do **not** point the new server at old-host MySQL. Create a **new** localhost database, then restore a dump from `/opt/Inertia2026v1` when ready.

### B1. Packages + MySQL (AlmaLinux, as root)

```bash
dnf install -y python3 python3-pip python3-devel gcc nginx mysql-server
# If mysql-server is unavailable: dnf install -y mariadb-server mariadb
systemctl enable --now mysqld   # or mariadb
# bind localhost only (my.cnf): bind-address=127.0.0.1
# never open 3306 on firewalld
```

### B2. Empty DB + user (before prod copy)

```bash
mysql -u root
```

```sql
CREATE DATABASE IF NOT EXISTS inertia_app2025
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'inertia_app'@'localhost' IDENTIFIED BY 'SET_A_STRONG_PASSWORD';
GRANT ALL PRIVILEGES ON inertia_app2025.* TO 'inertia_app'@'localhost';
FLUSH PRIVILEGES;
```

### B3. App + venv + .env (no prod data yet)

```bash
# After tarball extract to /opt/Inertia2026v1 on NEW VPS 129.121.133.25
# (same path name as old prod — different machine; old stays 66.116.199.231)
sudo cp /opt/Inertia2026v1/.env.example /opt/Inertia2026v1/.env
sudo nano /opt/Inertia2026v1/.env
# Required:
#   FLASK_ENV=production
#   SECRET_KEY=...   (python3 -c "import secrets; print(secrets.token_urlsafe(48))")
#   DB_HOST=127.0.0.1
#   DB_PORT=3306
#   DB_NAME=inertia_app2025
#   DB_USER=inertia_app
#   DB_PASSWORD=...
#   ENABLE_AI_SERVICES=false
#   OLLAMA_ENABLED=false
#   FORCE_2FA_FOR_ALL_USERS=true
#   SESSION_COOKIE_SECURE=true

sudo bash /opt/Inertia2026v1/scripts/deployment/clean_vps_env_check.sh
sudo bash /opt/Inertia2026v1/scripts/deployment/clean_vps_first_install.sh
```

Empty DB will not have users until you restore a dump **or** run `python scripts/create_local_admin.py` (dev-only; prefer dump for prod).

### B4. Copy prod data onto the **new** DB (cutover, not first empty install)

On **old** host:

```bash
mysqldump -u root -p --single-transaction --routines --triggers inertia_app2025 \
  | gzip > /tmp/inertia_app2025_$(date +%Y%m%d).sql.gz
```

Copy dump to new VPS, then:

```bash
gunzip -c /tmp/inertia_app2025_YYYYMMDD.sql.gz | mysql -u inertia_app -p inertia_app2025
```

Then rsync uploads (see `docs/PROD_CUTOVER_COPY_LIST.md`). Smoke on `http://NEW_IP` **before** DNS.

---

## Related

- Local first run: `RUN_FIRST.md`
- Cutover file list: `docs/PROD_CUTOVER_COPY_LIST.md`
- VPS phases: `docs/VPS_CLEAN_MIGRATION_RUNBOOK.md`
- Package install: `deployment/clean-vps-package/README.md`
