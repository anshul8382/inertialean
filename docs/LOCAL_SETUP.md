# Local setup — database and environment

Run the Inertia app on your Mac with a **local** MySQL database (`inertia_app2025_dev`). Do **not** use the SSH tunnel to prod MySQL for routine development.

**App root:** `/Users/anshulkhare/Downloads/app 2`

**DB process (local → cutover `_test`+prod → data-only refresh `_test`):** **`docs/DB_MIGRATION_AND_TEST_PROCESS.md`**.

---

## 1. Prerequisites

| Tool | Version | Install (macOS) |
|------|---------|-----------------|
| Python | 3.9+ | `brew install python@3.11` |
| MySQL | 8.0+ | `brew install mysql` then `brew services start mysql` |
| Git | any | already have the repo |

---

## 2. Python virtual environment

Use a **new** venv on your Mac (the bundled `.venv` may be from another OS).

### If `python3 -m venv venv` crashes (SIGABRT / “Unable to symlink”)

Apple’s Command Line Tools Python 3.9 often fails on `ensurepip`. Use this instead:

```bash
cd "/Users/anshulkhare/Downloads/app 2"
rm -rf venv
python3 -m venv venv --without-pip
source venv/bin/activate
curl -sS https://bootstrap.pypa.io/pip/3.9/get-pip.py -o /tmp/get-pip.py
python /tmp/get-pip.py
pip install -r requirements.txt
```

**Better long-term:** install Homebrew Python 3.11+ (`brew install python@3.11`) and use that for `venv`.

### Normal path (when `venv` works)

```bash
cd "/Users/anshulkhare/Downloads/app 2"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 3. MySQL database

### Option A — Empty dev database (schema from migrations)

```bash
# Log in as MySQL root (password you set at install)
mysql -u root -p
```

```sql
CREATE DATABASE IF NOT EXISTS inertia_app2025_dev
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'inertia_admin'@'localhost' IDENTIFIED BY 'YOUR_PASSWORD';
GRANT ALL PRIVILEGES ON inertia_app2025_dev.* TO 'inertia_admin'@'localhost';
GRANT ALL PRIVILEGES ON inertia_app2025.* TO 'inertia_admin'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Or use the helper script (prompts for root password):

```bash
bash scripts/setup_databases.sh
```

### Option B — Restore from a backup (full prod-like data)

If you have dumps under `backups/` (e.g. `inertia_app2025_20260525_210001.sql.gz`):

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS inertia_app2025 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
gunzip -c backups/inertia_app2025_20260525_210001.sql.gz | mysql -u inertia_admin -p inertia_app2025
```

Then in `.env` set `DB_NAME=inertia_app2025` (not `_dev`).

---

## 4. Environment file (`.env`)

```bash
cd "/Users/anshulkhare/Downloads/app 2"
cp .env.example .env
```

Edit `.env` — **minimum required:**

```env
FLASK_ENV=development
SECRET_KEY=any-long-random-string-for-local
DB_USER=inertia_admin
DB_PASSWORD=YOUR_PASSWORD
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=inertia_app2025_dev
ENABLE_AI_SERVICES=false
```

`config.py` loads `.env` automatically via `python-dotenv`.

---

## 5. Initialize schema (Option A only)

If you used an **empty** database, apply Flask-Migrate migrations:

```bash
source venv/bin/activate
export FLASK_APP=main.py
export FLASK_ENV=development

flask db upgrade
```

Some older one-off scripts also live in `migrations/*.py` (run only if a feature doc asks for them).

If `flask db upgrade` fails on a fresh DB, try restoring **Option B** from `backups/` instead — that is how production is usually bootstrapped.

---

## 6. Create a login user

```bash
source venv/bin/activate
python scripts/create_local_admin.py
```

Default: `admin@example.com` / `admin` (override with env vars below).

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD=YourSecurePass python scripts/create_local_admin.py
```

If you restored a production backup, users already exist — use an existing email or reset with:

```bash
python set_user_password.py   # edit email/password inside the script first
```

---

## 7. Run the app

```bash
source venv/bin/activate
export FLASK_ENV=development
python run_app.py
```

| `FLASK_ENV` | URL | Port |
|-------------|-----|------|
| `development` | http://localhost:5001 | 5001 |
| `test` | http://localhost:5002 | 5002 |
| `production` | http://localhost:5000 | 5000 |

Open **http://localhost:5001** and log in.

---

## 8. Verify it works

1. Login page loads without 500.
2. **Tools → Agents Dashboard** (`/agents/`) — may show DB warnings until agent tables exist; run migrations if needed.
3. Check logs in the terminal for import/DB errors.

Quick DB connectivity test:

```bash
python -c "
from main import create_app
from config import DevelopmentConfig
app = create_app(DevelopmentConfig)
with app.app_context():
    from extensions import db
    db.session.execute(db.text('SELECT 1'))
    print('DB OK')
"
```

---

## 9. Optional services (not required for basic UI)

| Feature | Env vars |
|---------|----------|
| Email | `MAIL_*` |
| AI / hybrid reports | `ENABLE_AI_SERVICES=true`, API keys |
| Airflow DAGs | separate `AIRFLOW_HOME`; see `airflow/` |
| Google Sheets / Calendar | `GOOGLE_*` in `.env` |

---

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError` | Activate `venv` and `pip install -r requirements.txt` |
| `Can't connect to MySQL` | `brew services start mysql`; check `DB_HOST` / `DB_PORT` |
| `Access denied` for user | Recreate user/grants (step 3) |
| Wrong database | `DB_NAME` must match DB you created or restored |
| Port in use | Change port in `run_app.py` or stop other Flask process |
| CSRF errors on forms | Ensure `SECRET_KEY` is set in `.env` |
| Agent tables missing | `flask db upgrade` or restore backup with full schema |

---

## Related files

- `config.py` — DB and feature flags
- `.env.test` — example test DB name (`inertia_app2025_test`)
- `run_app.py` — local dev server
- `wsgi.py` — production Gunicorn entry
- `scripts/setup_databases.sh` — create dev/staging/prod DB names
