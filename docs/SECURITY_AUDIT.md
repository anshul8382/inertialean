# Security Audit & Cleanup (2026-05-26)

Summary of cleanup performed and remaining risks for the Inertia app.

## Removed (disk & attack surface)

| Category | Action |
|----------|--------|
| `backups/` | All SQL dumps, tarballs, and bundles deleted; folder kept empty with `.gitkeep` |
| `_deprecated/` | Entire legacy tree removed (~673 files; no production imports) |
| Debug API | `api/v1/test_simple.py`, `api/v1/modular_test.py` |
| Test scripts | `run_test.py`, `sync_test_prod_db.py`, `set_user_password.py`, `scripts/test_*.py`, `scripts/utilities/test_alert_acknowledge.py`, `copy_workflows_from_test_db.py`, etc. |
| Templates / env | `templates/api_test.html`, `modular_api_test.html`, `simple_api_test.html`, `.env.save`, `.env.test` |
| Backups in repo | `routes/unified_recommendations.py.bak_*`, `start_test.sh`, root `workflow_transaction_integration.py` |

**Kept:** `tests/` pytest suite (production regression tests).

## Fixed in active code

- **`config.py`:** Removed default production DB password and hardcoded Perplexity API key; use `.env` only.
- **`scripts/utilities/user_service.py`:** Removed fallback password `"Sharveen"` when welcome email fails.
- **`scripts/utilities/__init__.py`:** Removed unauthenticated `/test` and `/simple-login` routes (legacy duplicate app factory).
- **Migration/utility scripts:** Replaced embedded `!Nert!a2025$` DB credentials with `os.environ.get('DB_PASSWORD', '')`.
- **`utils/audit_logger.py`:** Logs under project `logs/` (not `/home/inertia/app/...`).

## Remaining risks (review / rotate)

1. **Secrets in `.env`** — Not committed (gitignored). If this machine or chat logs were shared, rotate DB password, `SECRET_KEY`, API keys (Perplexity, WhatsApp, email, etc.).
2. **`SECRET_KEY` default** — `config.py` still has `dev-key-please-change-in-production` if env unset; ensure production sets `SECRET_KEY`.
3. **Debug routes in blueprints** (still present; most require login):
   - `routes/clients_v2.py`: `/api/test`
   - `routes/v2_transactions_ui.py`: `/test`
   - `routes/settings.py`: `/test`, cron job test POST
   - `routes/agreements.py`: `/test-pdf-simple`
   - `api/v1/whatsapp.py`: `/test-message` (POST)
   - `routes/unified_recommendations.py`: removed public `/test-route`; CSRF test route already gone
4. **CSRF exemptions** — Several blueprints use `csrf.exempt`; audit each endpoint before exposing externally.
5. **Role hacks** — `access_control.py` / `incentive_simulator_service.py` reference a named user for sales UI; replace with proper roles when possible.
6. **`scripts/migration/backup_test_prod.sh`** — Documents test/prod backup; does not store secrets but references old backup paths.

## Unused code (not deleted)

Large active modules may still contain dead branches. Safe removal needs import/coverage analysis per module. Do not delete `tests/` or Airflow DAGs without ops sign-off.

## Recommended next steps

1. Rotate production DB and API credentials if they ever appeared in git, backups, or chat.
2. Gate or remove remaining `/test*` routes behind `FLASK_ENV=development` or admin-only.
3. Run `pytest tests/` after major deletions.
4. Use `scripts/cleanup_local_disk.sh` periodically for logs and local tarballs (see `docs/CLEANUP.md`).
