# Local workspace cleanup

Last run: 2026-05-25

## What was cleaned automatically

| Item | Action | Approx. space saved |
|------|--------|---------------------|
| `.venv/` | Removed (duplicate of `venv/`) | ~900 MB |
| `logs/gunicorn_*.log`, large test logs | Removed (gitignored; recreated on run) | ~800 MB |
| `backups/*.sql.gz` | Kept **6 newest**; removed 42 older dumps | ~1 GB |
| `backups/*.tar.gz`, `*.bundle` | Removed code archives | varies |
| `__pycache__/` | Cleared under project (not `venv/`) | small |
| `flask_sessions/` | Cleared session files | small |
| `airflow/*.db-shm`, `*.db-wal` | Removed local WAL files | small |

**After cleanup (approx.):** `backups/` ~677 MB · `logs/` ~84 MB · `venv/` ~304 MB · `_deprecated/` ~38 MB

## Already cleaned earlier

- Template/route `*.backup`, `*.before_app_test_*`
- `routes/unified_recommendations.py.new`
- Root `asset_class_backup_*.json`, `.env.save`
- `_deprecated/cleanup_unused_2026-01-26/`

## Optional — your choice

| Item | Notes |
|------|--------|
| `_deprecated/` (~38 MB) | Legacy code; not imported by app. Safe to delete if you don't need reference. |
| `logs/` remainder | Audit trails, `api_audit.log`, job logs — delete old subfolders if disk tight |
| `venv/` | Recreate with `pip install -r requirements.txt` after delete |

## Re-run cleanup script

```bash
bash scripts/cleanup_local_disk.sh
```

## Do not commit

- `.env` (secrets)
- `backups/*.sql.gz`, `logs/`
- `venv/`, `flask_sessions/`

## Local dev workflow (unchanged)

```bash
# Terminal 1
ssh -L 3307:127.0.0.1:3306 root@66.116.199.231

# Terminal 2
cd "/Users/anshulkhare/Downloads/app 2"
source venv/bin/activate
python run_app.py
```
