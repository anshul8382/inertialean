# Inertia2026-lean — what this tree is

Created from `app 2` for **clean VPS deploy** (no cPanel, no Ollama on server).

**Location:** `/Users/anshulkhare/Downloads/Inertia2026-lean`  
**Source:** `/Users/anshulkhare/Downloads/app 2`  
**Size:** ~36 MB (source working tree was ~3.9 GB with venv/logs/mobile)

## Kept (in use / needed to run)

- `routes/`, `api/`, `services/`, `models/`, `models.py`, `agents/`, `airflow/dags/`
- `templates/`, `static/` (no client agreement PDFs)
- `migrations/`, `scripts/` (ops scripts; backup *tarballs* excluded)
- `config/`, `deployment/`, `.cursor/`, `docs/`
- `apps/capacitor-shell/` sources only (no Android/iOS build trees / node_modules)
- Clean VPS package scripts under `scripts/deployment/` + `deployment/clean-vps-package/`

## Removed / excluded from copy

| Excluded | Why |
|----------|-----|
| `venv/`, `.venv/`, `.local/` | Rebuild on target |
| `apps/mobile/` (Expo) | Not required for web/VPS; Phase-1 scaffold |
| `apps/capacitor-shell` native builds / node_modules | Huge; rebuild if needed |
| `_deprecated/` | Legacy |
| `backups/`, `dist/`, `*.tar.gz`, `*.zip` | Artifacts |
| `logs/`, `*.log`, gunicorn logs | Runtime noise |
| `static/agreements/*.pdf`, client `uploads/` | **Now included** in lean (~84 MB from local app 2); refresh from prod at cutover |
| Root analysis `*.md` clutter | Docs live under `docs/` |
| `.env`, `service_account.json` | **Secrets — never ship in lean tree** |
| `frontend/`, `backend/`, `shared/`, legacy `mobile/` | Unused parallel trees if present |

## Ollama / AI changes (lean)

| Change | Detail |
|--------|--------|
| `services/ollama_service.py` | Default **off**; needs `ENABLE_AI_SERVICES` + `OLLAMA_ENABLED` + `OLLAMA_BASE_URL` |
| `config.py` | `OLLAMA_BASE_URL` default empty; `LLM_FALLBACK_TO_OLLAMA` default **false** |
| `services/llm_fallback_service.py` | Fallback default **false** |
| `services/assistant_llm.py` | `local_ai_available()` requires Ollama enabled |
| `nav_registry.py` + `templates/base.html` | Assistant hub / Ask INERTIA / SQL assistant **removed** from nav; Help → User manual only |

Deterministic DI / client-health playbooks still work without LLM.

## Client documents (included)

Synced from local `app 2` into lean (~**84 MB**, ~**165 files**):

- `uploads/`
- `static/agreements/`
- `static/uploads/`

**Production** may still have slightly different files under `/opt/Inertia2026v1` — at cutover, `rsync` from the **live** server into the VPS (or into lean again) so nothing is missing.

## 2FA (included — do not strip)

| Piece | Path |
|-------|------|
| Routes | `routes/two_factor.py` |
| Services | `two_factor_service.py`, `two_factor_enforcement.py`, `two_factor_policy_service.py` |
| UI | `templates/two_factor/` including **`backup_codes.html`** |
| Migrations | `add_2fa_fields.py`, `add_2fa_policy_fields.py`, `enforce_mandatory_2fa_all_users.py` |

User **2FA backup codes themselves** live in the **MySQL database**, not as files. Copying code is enough for the feature; restore the DB (or run with prod DB) to keep existing codes/secrets.

## Honest limit: “only modules in use”

This is a **deploy-oriented slim tree**, not a full dead-code elimination of every unused Python file (that needs import-graph + runtime coverage). Next pass can delete specific unused routes after a week on the new VPS.

## Next

1. Follow **`RUN_FIRST.md`**: venv → pip → `.env` → `create_app` smoke  
2. If OK → `git init` / branch `clean-vps-lean` → push and track  
3. See `docs/ENV_SECURITY_LEAN.md` for `.env` hardening  
4. **Cutover refresh:** `docs/PROD_CUTOVER_COPY_LIST.md` (uploads, agreements, DB dump from `/opt/Inertia2026v1`)  
5. Package: `bash scripts/deployment/build_clean_vps_package.sh`
