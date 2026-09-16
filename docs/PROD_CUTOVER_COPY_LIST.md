# Prod cutover — what to copy from live

**Live app root:** `/opt/Inertia2026v1`  
**Legacy (do not use as source of truth):** `/home/inertia/app`

Once lean runs locally/on the new VPS, **refresh** these from prod before users switch.

---

## Must copy (app works + data intact)

| From prod | To (lean / new VPS app dir) | Notes |
|-----------|----------------------------|--------|
| `uploads/` | `uploads/` | Client docs, screenshots, Excel, etc. |
| `static/agreements/` | `static/agreements/` | Generated/signed agreement PDFs |
| `static/uploads/` | `static/uploads/` | Incl. `static/uploads/agreements/` if present |
| **MySQL dump** of `inertia_app2025` (and `_test` if needed) | Restore into new MySQL | **Not a folder** — see dump commands below |
| `.env` values (or recreate from `.env.example`) | `.env` on new host | Prefer **recreate + rotate** `SECRET_KEY`; copy mail/DB/integration secrets carefully over SSH only |

### Optional but often needed

| From prod | To | Notes |
|-----------|-----|--------|
| `instance/` (if used for jobs/cache) | `instance/` | Skip huge junk; copy if app references it |
| `service_account.json` or Google creds path | Outside git, restricted perms | Only if Sheets/Calendar still used |
| `config/gunicorn_parallel_v2026.py` tweaks | Keep lean’s unit + match bind port | Compare if you customized workers |

### Do **not** copy

| Skip | Why |
|------|-----|
| `venv/`, `.venv/` | Recreate with `pip install -r requirements.txt` |
| `logs/`, `*.log`, `gunicorn_*.log` | Noise |
| `airflow/logs/`, `airflow.db*` | Recreate if you run Airflow |
| `.git/` from server (if dirty) | Use lean/git branch |
| Ollama models / `/usr/share/ollama` | Not on clean VPS |
| `/backup`, `inertia_codebase_backups`, `*.tar.gz`, `*.zip` | Disk bloat |
| cPanel/`public_html` / mail | Out of scope for app cutover |

---

## rsync examples (from your Mac, after SSH works)

```bash
# Set these
LIVE=anshul@OLD_OR_STILL_BIGROCK_IP
LIVE_APP=/opt/Inertia2026v1
DEST=/opt/Inertia2026v1          # on new VPS
# or DEST=/Users/anshulkhare/Downloads/Inertia2026-lean  # refresh local lean

# Documents only
rsync -aHAX --progress \
  "$LIVE:$LIVE_APP/uploads/" \
  "$DEST/uploads/"

rsync -aHAX --progress \
  "$LIVE:$LIVE_APP/static/agreements/" \
  "$DEST/static/agreements/"

rsync -aHAX --progress \
  "$LIVE:$LIVE_APP/static/uploads/" \
  "$DEST/static/uploads/"
```

If both servers are remote, run rsync **on the new VPS** pulling from old:

```bash
rsync -aHAX --progress root@66.116.199.231:/opt/Inertia2026v1/uploads/ /opt/Inertia2026v1/uploads/
rsync -aHAX --progress root@66.116.199.231:/opt/Inertia2026v1/static/agreements/ /opt/Inertia2026v1/static/agreements/
rsync -aHAX --progress root@66.116.199.231:/opt/Inertia2026v1/static/uploads/ /opt/Inertia2026v1/static/uploads/
```

---

## Database (required for “keep the data”)

On **old** server:

```bash
mysqldump -u ROOT_OR_APP_USER -p \
  --single-transaction --routines --triggers \
  inertia_app2025 > /tmp/inertia_app2025_$(date +%F).sql
# optional:
# mysqldump ... inertia_app2025_test > /tmp/inertia_app2025_test_$(date +%F).sql
```

Copy dump to new VPS, then:

```bash
mysql -u ... -p -e "CREATE DATABASE IF NOT EXISTS inertia_app2025 CHARACTER SET utf8mb4;"
mysql -u ... -p inertia_app2025 < /tmp/inertia_app2025_YYYY-MM-DD.sql
```

**2FA backup codes** and all client rows live here — file copy alone is not enough.

---

## Minimal cutover checklist

1. Lean app starts (login works against a DB).  
2. Refresh **three doc trees** from `/opt/Inertia2026v1`.  
3. Refresh **MySQL** dump/restore.  
4. Point `.env` at new DB; `FORCE_2FA_FOR_ALL_USERS=true`; AI/Ollama off.  
5. Smoke: client detail, one agreement PDF, one upload, 2FA.  
6. DNS only after that.

---

## After first workable lean

Re-run the **doc rsync + DB dump** once more immediately before DNS cutover so nothing created on BigRock in the meantime is missing.
