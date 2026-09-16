# Run lean first → then git

## What “everything to run + keep data” means here

| Need | In lean? |
|------|----------|
| App code (routes, services, api, templates, models) | Yes |
| `requirements.txt` (trimmed, no Ollama pip pkg) | Yes |
| `.env.example` (AI/Ollama off) | Yes — copy to `.env` |
| 2FA code + backup_codes UI | Yes |
| Client docs (`uploads/`, `static/agreements`, `static/uploads`) | Yes (~84 MB local copy) |
| MySQL data | **Not in folder** — use local/prod DB via `.env` |
| Secrets | Create `.env` yourself (never commit) |

## 1. First run (local)

**Copy-paste sheet** (local + later VPS): `docs/RUN_COMMANDS_LOCAL_AND_SERVER.md`  
On Mac via deployment agent: `python3 .local/deployment-agent/deploy_agent.py run-commands`

Use **local MySQL only** (`127.0.0.1:3306`, `inertia_app2025_dev`). Do not tunnel prod for this. On the new VPS you will create a **new** DB and copy prod data later.

```bash
cd /Users/anshulkhare/Downloads/Inertia2026-lean
ln -sfn "/Users/anshulkhare/Downloads/app 2/.local" .local
bash scripts/start_local_mysql.sh

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp "/Users/anshulkhare/Downloads/app 2/.env" .env
chmod 600 .env
# Confirm: DB_HOST=127.0.0.1 DB_PORT=3306 DB_NAME=inertia_app2025_dev
# ENABLE_AI_SERVICES=false  OLLAMA_ENABLED=false  FLASK_ENV=development

export FLASK_ENV=development
python -c "from main import create_app; from config import DevelopmentConfig; from extensions import db; app=create_app(DevelopmentConfig)
with app.app_context():
    db.session.execute(db.text('SELECT 1')); print('DB OK')"
python run_app.py
# http://127.0.0.1:5001/
```

Smoke: login, open a client, open an agreement/upload, confirm 2FA screens load.

## 2. If it works → new git history

```bash
cd /Users/anshulkhare/Downloads/Inertia2026-lean
git init
git remote add origin git@github.com:anshul8382/inertia.git   # or new empty repo
git checkout -b clean-vps-lean
git add -A
# Ensure .env is NOT staged
git status
git commit -m "Initial lean tree: no Ollama-by-default, trimmed requirements, client docs included"
# Push to a NEW branch or NEW repo — do not force-push over main until ready
git push -u origin clean-vps-lean
```

Prefer a **new branch** (or new repo) so `main` / current prod branch stays untouched while you track lean changes.

## 3. Then track changes

Work only in this tree (or open it as the Cursor workspace).  
Commit normally on `clean-vps-lean`.  
When stable, merge/PR into main and deploy with `build_clean_vps_package.sh`.

## requirements.txt edits (summary)

- Dropped unused clutter (`sed`, extra metadata pins)
- **No** `ollama` PyPI package (never was; HTTP via requests only if enabled)
- **anthropic** optional — hybrid report uses lazy import; install only if AI on
- Kept Flask stack, MySQL, 2FA (`pyotp`/`qrcode`), PDF (`weasyprint`/`reportlab`), pandas, gunicorn
- Added explicit `httpx` (hybrid report)
