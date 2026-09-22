# DB cutover registry

Track **schema-dependent features** that are **gated in code** until production MySQL is updated with **additive-only** scripts (not `flask db upgrade` / full Alembic chain).

**Day-to-day process (local `_dev` → migrate `_test` + prod → data-only refresh `_test`):**  
**`docs/DB_MIGRATION_AND_TEST_PROCESS.md`**.

**Deployment agent:** Before live prod deploy or DB work, read this file and run  
`python3 .local/deployment-agent/deploy_agent.py db-cutover-status`.  
At cutover: `db-cutover-prod` (see `.local/deployment-agent/CURSOR_AGENT.md`).

**Policy:** Develop SQL on **local** `inertia_app2025_dev`. At cutover, apply the same `migrations/add_*.py` to **`inertia_app2025_test` and `inertia_app2025`**. Then refresh `_test` with **prod data only**. Never `flask db upgrade` on prod.

---

## Environment switch

| Variable | Default (local / prod until cutover) | After cutover |
|----------|--------------------------------------|---------------|
| `DEFER_DB_FEATURES` | `audit_log` | *(empty)* |

**Content intelligence (`ic_*` tables):** Not required for Campaign Studio tag/match flows. Tags and send log live in existing `campaign_studio_campaign.payload` JSON (`contentIntelligence` key). Person matching reads `client` / `lead`. Optional dedicated tables (`ic_persons`, `ic_content`, `ic_send_log`) are deferred — do **not** run `inertia_content.storage.Database.create_tables()` on shared prod until a cutover window.

```bash
# .env — current (defer DB audit writes; no commit errors)
DEFER_DB_FEATURES=audit_log

# .env — after audit_log table exists on prod
DEFER_DB_FEATURES=
```

Config: `config.Config.DEFER_DB_FEATURES`  
Logic: `services/db_cutover.py` → `audit_log_writes_enabled()`

---

## Active deferrals (code patched)

### 1. `audit_log` — DB-backed audit trail

| Item | Detail |
|------|--------|
| **Status** | Deferred (default) |
| **User impact** | None for normal UI. Exports and logins work; **audit rows are not stored** in MySQL. |
| **Compliance impact** | SEBI-style **DB audit trail** incomplete until cutover. File logs (`utils/audit_logger.py`, `audit_trail_service`) may still apply elsewhere. |
| **Code gate** | `services/audit_service.py` — `log_audit_event()` / `log_data_export()` return early when deferred |
| **Call sites** | No per-file comments needed; all routes use `audit_service` (e.g. `routes/auth.py`, `routes/main.py`, `routes/agreements.py`, `routes/invoices.py`, `routes/account_management.py`, `routes/main_sections/uploads.py`, `api/v1/auth.py`, …) |
| **Migration** | `migrations/add_audit_log_table.py` |
| **Model** | `models/audit_log.py` |

**Restricted actions (not persisted to `audit_log` while deferred):**

- Login success / failure (`routes/auth.py`)
- Bulk exports: practice analytics Excel, client CSV, agreements PDF/DOCX, account CSV, corporate-actions sample, document download, invoice import CSVs, 2FA backup code views
- Client list/view audit hooks that call `log_audit_event`
- API auth events via `api/v1/auth.py`

**After cutover:**

1. Backup prod DB  
2. `python migrations/add_audit_log_table.py` on prod  
3. Set `DEFER_DB_FEATURES=` in prod `.env`  
4. Restart app  
5. Remove or shorten `DB CUTOVER` blocks in `audit_service.py` if desired (optional; gate is harmless if left)

---

## Code fixes (no DB) — already deployed in branch

| Issue | Fix | Notes |
|-------|-----|-------|
| `current_user` undefined in maintenance mobile nav | `templates/partials/mobile/macros.html` — `include … with context` | Not a DB issue |
| `clients_v2` add client | `from routes.forms import ClientForm` | Not a DB issue |
| Practice analytics export masking | `services/pii_masking.py`, `protected_export_service.py` | Not a DB issue |

---

## Optional modules (blueprint / NAV flags)

These are **disabled at startup** if import/registration fails (`NAV_*_ENABLED` in `__init__.py`). They may need their own `add_*.py` scripts on prod before enabling.

| NAV flag | Typical migration script(s) |
|----------|-----------------------------|
| `NAV_TASKS_ENABLED` | `migrations/add_ops_task_table.py`, related ops_task / Google columns scripts |
| `NAV_ACCOUNT_MANAGEMENT_ENABLED` | `migrations/add_account_management_tables.py` |
| `NAV_CAMPAIGN_STUDIO_ENABLED` | `migrations/add_campaign_studio_campaign_table.py` |
| `NAV_FINANCIAL_PLANNING_ENABLED` | `migrations/add_financial_planning_tables.py` + `FINANCIAL_PLANNING_ENABLED=true` |
| `NAV_TASK_ASSIGNMENT_RULES_ENABLED` | `migrations/add_task_assignment_rule.py` |
| `NAV_AGENTS_DASHBOARD_ENABLED` | agent / integrity tables (see agents prod check doc) |
| `NAV_ENHANCED_REVIEW_ENABLED` | various review / tax optimiser scripts |
| WhatsApp Groups monitoring (`/whatsapp/groups`) | `migrations/add_whatsapp_groups_tables.py` + set `WHATSAPP_GROUPS_ENABLED=true` |

Confirm each table exists on prod (`SHOW TABLES`) before relying on a module.

### WhatsApp Groups (`whatsapp_groups`, message columns)

| Item | Detail |
|------|--------|
| **Status** | Optional — UI works without migration but shows “run migration”; group sync/inbox need table |
| **Migration** | `python migrations/add_whatsapp_groups_tables.py` |
| **Env** | `WHATSAPP_GROUPS_ENABLED=true` after migration; subscribe `group_*` webhook fields in Meta |
| **Docs** | `docs/WHATSAPP_GROUPS_MONITORING.md` |

---

## Additive migration scripts (candidates for cutover batch)

Run **only** after backup. Prefer scripts that use `create(..., checkfirst=True)` or explicit `IF NOT EXISTS`. Review each file before prod.

**Confirmed for this deferral:**

- `migrations/add_audit_log_table.py`

**Other standalone scripts in `migrations/` (review before batch):**

- `add_2fa_fields.py`, `add_2fa_policy_fields.py`
- `add_account_management_tables.py`
- `add_asset_class_to_recommendations.py`
- `add_asset_class_tax_ltcg_duration_and_seed_classes.py`
- `add_bni_ty_notes_tables.py`
- `add_campaign_studio_campaign_table.py`
- `add_client_is_active.py`
- `add_client_new_fields.py`, `add_client_notes_fields.py`
- `add_client_secondary_emails.py` — **run before using secondary-email CC on prod**
- `add_company_industry_fields.py`
- `add_financial_planning_tables.py`
- `add_google_calendar_ops_task_columns.py`
- `add_hot_stock_rating.py`
- `add_lead_id_to_alerts.py`
- `add_market_commentary_table.py`
- `add_meeting_participants_and_calendar.py`
- `add_monthly_holdings_cycle.py`
- `add_monthly_salary_incentive_columns.py` — **run with attendance salary detail deploy**
- `add_ops_task_table.py` (+ `add_ops_task_*` link/calendar/tasks columns)
- `add_price_accuracy_tables.py`
- `add_recommendation_session.py`
- `add_review_workflow_assigned_to.py`
- `add_task_assignment_rule.py`
- `add_whatsapp_groups_tables.py`
- `add_tax_optimiser_*.py` (multiple; run in dependency order per script headers)
- `add_user_feedback_tables.py`, `add_user_modification_tracking.py`

**Avoid on prod until planned:**

- `migrations/versions/*` (Alembic chain) — may assume full migration history
- `scripts/migration/migrate_database.sh --full` — overwrites data

---

## Cutover checklist (single maintenance window)

1. [ ] Full MySQL backup  
2. [ ] Run reviewed `migrations/add_*.py` scripts (start with `add_audit_log_table.py`)  
3. [ ] `SHOW TABLES LIKE 'audit_log';` — verify  
4. [ ] Set `DEFER_DB_FEATURES=` in prod `.env`  
5. [ ] Deploy latest code (masking, template fixes, `db_cutover` gate)  
6. [ ] Restart gunicorn / Flask  
7. [ ] Smoke test: login, export practice analytics, open `/maintenance/reviews`  
8. [ ] Confirm `audit_log` rows appear: `SELECT * FROM audit_log ORDER BY id DESC LIMIT 5;`  
9. [ ] Update this doc — move `audit_log` to **Completed** section  

---

## Completed deferrals

*(Move items here after cutover and verification.)*

| Feature | Completed date | Notes |
|---------|----------------|-------|
| — | — | — |

---

## Client secondary emails (`client.secondary_emails`)

**Feature:** Store comma-separated CC addresses on the client; pre-fill recommendation email CC.  
**Schema change:** Add nullable `TEXT` column on `client`.

| Item | Value |
|------|--------|
| Migration | `migrations/add_client_secondary_emails.py` |
| Deploy helper | `scripts/deployment/run_client_secondary_emails_migration.sh` |
| Raw SQL | `migrations/sql/add_client_secondary_emails.sql` |
| Model | `models.py` → `Client.secondary_emails` |

**Prod steps (run after git sync, before or with app restart):**

```bash
cd /opt/Inertia2026v1
bash scripts/deployment/run_client_secondary_emails_migration.sh
# or:
sudo -u inertia ./venv/bin/python3 migrations/add_client_secondary_emails.py
sudo -u inertia ./venv/bin/python3 migrations/add_client_secondary_emails.py --verify
```

**Verify in MySQL:**

```sql
SHOW COLUMNS FROM client LIKE 'secondary_emails';
```

**Note:** Code referencing `Client.secondary_emails` will error on client queries until this column exists. Run migration in the same deploy window as commit `03ef675+`.

---

## Monthly salary incentives (`monthly_salary.sales_incentive` / `internal_incentive`)

**Feature:** Admin salary detail — base stipend formula + sales/internal incentives + claims.  
**Schema change:** Two additive `NUMERIC(10,2)` columns on existing `monthly_salary` (default `0.00`).

| Item | Value |
|------|--------|
| Migration | `migrations/add_monthly_salary_incentive_columns.py` |
| Deploy helper | `scripts/deployment/run_monthly_salary_incentive_migration.sh` |
| Model | `models.py` → `MonthlySalary.sales_incentive`, `MonthlySalary.internal_incentive` |
| Service | `services/attendance_salary_service.py` |
| UI | `/attendance/admin/salary-detail/<user_id>/<year>/<month>` |

**Prod steps (same deploy window as attendance salary-detail code):**

```bash
cd /opt/Inertia2026v1
bash scripts/deployment/run_monthly_salary_incentive_migration.sh
# or:
sudo -u inertia ./venv/bin/python3 migrations/add_monthly_salary_incentive_columns.py
sudo -u inertia ./venv/bin/python3 migrations/add_monthly_salary_incentive_columns.py --verify
```

**Verify in MySQL:**

```sql
SHOW COLUMNS FROM monthly_salary LIKE '%incentive%';
```

**Note:** Listing/calculating `MonthlySalary` will fail until these columns exist. Run migration before or immediately after deploying the attendance salary-detail commit. No `DEFER_DB_FEATURES` gate (same pattern as secondary emails).

**Added:** 2026-08-02

---

## Fixed-fee billing configuration (no schema change)

**Feature:** Fixed-fee advisory model — stores annual fee and escalation % as `AgreementVariables` rows.  
**Schema change required:** None — uses existing `agreement_variables` table (key-value store).  
**Variable names added:**

| `variable_name` | `variable_type` | Meaning |
|---|---|---|
| `fixed_annual_fee` | `billing_config` | Total fee for one full year (₹), before escalation |
| `fixed_fee_escalation_pct` | `billing_config` | Annual escalation %, applied once per completed year from `agreement.signed_date` |

**Calculation rule:** `current_annual_fee = base × (1 + esc%)^years_completed`  
where `years_completed = floor((billing_date − start_date).days / 365)`.  
Escalation is **not prorated** — it steps up only after each completed year.  
Period invoice = `current_annual_fee / frequency_divisor` (yearly=1, half-yearly=2, quarterly=4).

**Status:** ✅ Live — no migration, no gating flag required.  
**Added:** 2026-06-02 · `billing_calculation_service.py` → `_calculate_fixed_fee_billing()`

---

## Role-based menu access + client-data capabilities (no schema change)

**Feature:** Navbar driven by `nav_registry.py`; restricted roles use `RolePermission` route rows as an allowlist. Client PII/financial fields masked when `capability:client_data_sensitive` is absent.

**Schema change required:** None — reuses `role_permission.route_endpoint` (including `capability:*` keys).

**Seeds (additive, idempotent):**

| Script | Purpose |
|--------|---------|
| `migrations/seed_research_role.py` | Creates `research` role + menu allowlist + `client_data_ops` |
| `migrations/seed_permission_capabilities.py` | Grants `client_data_ops` / `client_data_sensitive` to system roles |

**Status:** Pending deploy seed run on prod.  
**Added:** 2026-07-02

---

## Lead onboarding tables (risk quiz + proposals)

| Item | Detail |
|------|--------|
| **Status** | ✅ Cutover complete (2026-09-09) — schema on local `_dev`, `inertia_app2025_test`, and `inertia_app2025`; `_test` data-only refreshed from prod |
| **Tables** | `risk_assessment_submission`, `lead_proposal`, `lead_kyc_profile` |
| **Migration** | `migrations/add_lead_onboarding_tables.py` + `add_lead_kyc_profile_table.py` + `fix_lead_onboarding_cascade_delete.py` |
| **Models** | `models/lead_onboarding.py` |
| **Public URL** | `/risk-assessment` (optional `?t=` signed lead token) |

**Cutover done (run order applied to `_test` then prod):**

1. `python migrations/add_lead_onboarding_tables.py`
2. `python migrations/add_lead_kyc_profile_table.py`
3. `python migrations/fix_lead_onboarding_cascade_delete.py`
4. Data-only refresh of `inertia_app2025_test` from prod
5. **Still required:** deploy/pull code on server to tip `b210b6d` (or later) and restart `inertia-2026v1` — SSH from this agent failed; run on server Terminal.

**Prod env (Zoho / Leegality / mail):**

- Zoho: HTTPS `ZOHO_REDIRECT_URI`, Server-based OAuth client, Connect from Settings, matching org ID.
  Invoice push stays **draft** until Zoho email succeeds; otherwise ask manager to send from Zoho.
  Mark paid requires Zoho sync when Zoho is configured.
- Leegality: live `LEEGALITY_*` on server (Settings shows status only); restart after change.
- Proposal client email uses `MAIL_USERNAME` / `MAIL_PASSWORD`; CC via Settings or `PROPOSAL_EMAIL_CC`.
- WhatsApp copy: edit [`docs/onboarding_whatsapp_playbook.md`](docs/onboarding_whatsapp_playbook.md) (system selects stage; UI is editable).

**Process gates (code):** Proposal and invoice require approval before client send (configurable in Settings).  
**Convert:** early — activates client for first recommendation; KYC/agreement/payment stay on open checklist until closed.

**Operational note:** invoice portfolio snapshots should stay outside public
`static/` and be served only through authenticated invoice routes.

**Added:** 2026-09-05

### Finding notification centre

| Item | Detail |
|------|--------|
| **Status** | Local/dev ready; gate until table exists on each host |
| **Table** | `finding_notification_decision` |
| **Migration** | `migrations/add_finding_notification_decision.py` |
| **Model** | `models/finding_notification_decision.py` |
| **Code gate** | `services/db_cutover.py` → `finding_notification_enabled()` |
| **Service / UI** | `services/notification_centre_service.py`, `/notifications`, `/api/v1/notifications*` |
| **User impact while deferred / missing table** | Bell returns empty; page shows “waiting on schema”. Live findings still exist elsewhere. |
| **Notes** | Do **not** add `finding_notification` to `DEFER_DB_FEATURES` on local after migration. On prod, run migration then leave defer list without this key (or omit — feature enables when table exists). |

---

### Peer messages (notification bell)

| Item | Detail |
|------|--------|
| **Status** | Local/dev MVP; gate until table exists on each host |
| **Table** | `user_peer_message` |
| **Migration** | `migrations/add_user_peer_message.py` |
| **Model** | `models/user_peer_message.py` |
| **Code gate** | `services/db_cutover.py` → `peer_messages_enabled()` |
| **Service / UI** | `services/peer_message_service.py`, Messages tab + compose on `/notifications`, `/api/v1/notifications/message` |
| **User impact while deferred / missing table** | Compose hidden; Messages tab absent; send API returns `peer_messages_disabled`. Findings centre still works independently. |
| **Notes** | Feature key `peer_message`. Do not add to `DEFER_DB_FEATURES` on local after migration. |

---

### Lead KYC profile

| Item | Detail |
|------|--------|
| **Table** | `lead_kyc_profile` |
| **Migration** | `migrations/add_lead_kyc_profile_table.py` |
| **Notes** | Manual PAN / Aadhaar last4 / CKYC number capture. CERSAI API not connected. Lead delete must CASCADE (see `migrations/fix_lead_onboarding_cascade_delete.py`). |

---

## Adding a new deferral

1. Add feature key to `services/db_cutover.py` `_REGISTRY`  
2. Gate writes in **one** service (avoid commenting dozens of routes)  
3. Add `migrations/add_<feature>_table.py` with `checkfirst=True`  
4. Document restricted behaviour in this file  
5. Default include in `DEFER_DB_FEATURES` only if prod lacks the table today  
