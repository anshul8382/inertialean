# DB migration and test process

Canonical workflow for schema changes and database environments.

**Related:** `docs/DB_CUTOVER_REGISTRY.md` (deferred features), `docs/LOCAL_SETUP.md`, `docs/DEPLOY_WORKFLOW.md`, `scripts/utilities/clone_mysql_database.py`.

---

## Databases

| Name | Where | Role |
|------|--------|------|
| **`inertia_app2025_dev`** | **Local Mac** (`127.0.0.1:3306`) | Day-to-day development. All SQL / migration work happens here first. |
| **`inertia_app2025`** | Server (prod) | Live production data. |
| **`inertia_app2025_test`** | Server (same MySQL as prod) | QA / parallel check. Schema kept in sync with prod via the same migrations; **data** refreshed from prod after cutover. |

### Hard gate (Mac / Cursor)

| From Mac | Allowed? |
|----------|----------|
| App / migrations / one-offs with `DB_NAME=inertia_app2025` (tunnel or remote) | **No** |
| Point local `.env` at tunneled prod for “just checking” | **No** |
| **Read-only** dump/copy prod → download → refresh **`inertia_app2025_dev`** | **Yes** |
| Run migrations on prod via **SSH on the server** (or `db-cutover-prod` remote) | **Yes** (cutover only, after approval) |
| Refresh server `_test` from prod **on the server** (`--data-only`) | **Yes** |

Local MySQL helper: `./scripts/start_local_mysql.sh` (datadir under `.local/`). Day-to-day: always local `_dev`.

---

## Phase 1 — Develop and test locally

1. Ensure local MySQL is up and `.env` points at local `_dev`:

   ```env
   DB_HOST=127.0.0.1
   DB_PORT=3306
   DB_NAME=inertia_app2025_dev
   DB_USER=inertia_admin
   DB_PASSWORD=local_inertia_dev
   ```

2. Author additive scripts only: `migrations/add_*.py` with `checkfirst=True` (never rely on `flask db upgrade` for prod).
3. Run the script against **local** `_dev`:

   ```bash
   python migrations/add_<feature>.py
   ```

4. Exercise the app + pytest against local `_dev`.
5. If prod schema still lags after deploy of code, gate with `DEFER_DB_FEATURES` and document in `docs/DB_CUTOVER_REGISTRY.md`.

---

## Phase 2 — Cutover: apply schema to server DBs

When ready to productionize schema (after backup):

1. Deploy code (`full-prod` / server pull) if not already live.
2. Run the **same** migration script(s) on **both** server databases (order matters if scripts depend on each other):

   ```bash
   # ON THE SERVER only (SSH), from /opt/Inertia2026v1 — never from Mac against tunneled prod
   DB_NAME=inertia_app2025_test ./venv/bin/python migrations/add_<feature>.py
   DB_NAME=inertia_app2025      ./venv/bin/python migrations/add_<feature>.py
   ```

3. Clear relevant `DEFER_DB_FEATURES` on **prod** `.env` if gated; restart `inertia-2026v1`.
4. Mark the feature **Completed** in `docs/DB_CUTOVER_REGISTRY.md`.

**Rule:** Shared / production schema changes always land on **`inertia_app2025_test` and `inertia_app2025`**. Do not migrate only prod and leave test behind.

Deployment agent `db-cutover-prod` should apply the configured `prod_migrations` list to **both** DBs (see agent config / CURSOR notes).

---

## Phase 3 — After migrations: refresh test **data** from prod

Once both server DBs share the same structure:

- Refresh **`inertia_app2025_test` with prod data only** (do **not** drop/recreate schema).
- Structure must already match; this step only replaces row data for shared tables.

```bash
# Preferred: ON THE SERVER (same host for source and target)
python3 scripts/utilities/clone_mysql_database.py \
  --source inertia_app2025 --target inertia_app2025_test --data-only
```

**Refresh local `_dev` from prod (Mac allowed path):** dump/copy from prod is **read-only**; load only into local `inertia_app2025_dev`. Do not open a writable app session against prod. Prefer dump-on-server → download → import local (see `docs/LOCAL_SETUP.md` / clone helpers).

### Test-only schema extras

If `inertia_app2025_test` has **test-only** tables/columns (not on prod):

- Still use **`--data-only`**.
- Shared tables: data replaced from prod (common columns only).
- Test-only tables: **left untouched**.
- Extra columns on test: preserved; prod columns are refreshed.

Do **not** full-clone (drop/recreate) test in that case — that would wipe test-only structure.

---

## What not to do

| Avoid | Why |
|-------|-----|
| **Any** Mac script/app against live **prod** MySQL | Accidental writes; hard gate |
| Develop against tunneled **prod** | Same — use local `_dev` only |
| Run cutover migrations from Mac via tunnel | Run **on server** instead |
| `flask db upgrade` / full Alembic on prod | Unsafe; use `migrations/add_*.py` |
| Migrate only `inertia_app2025` and skip `_test` | Test drifts from prod schema |
| Full schema clone of test **after** cutover when structure already matches | Prefer data-only; preserves test-only objects |
| Full clone when test has test-only DDL | Destroys those objects |

---

## Quick checklist (per schema feature)

- [ ] Migration script written and run on **local** `inertia_app2025_dev`
- [ ] App + tests green on local
- [ ] Registry / `DEFER_DB_FEATURES` updated if needed
- [ ] Code deployed
- [ ] Same migration run on **`inertia_app2025_test`** then **`inertia_app2025`**
- [ ] Prod defer cleared + service restarted
- [ ] **Data-only** refresh of `_test` from prod (or skip if not needed)
