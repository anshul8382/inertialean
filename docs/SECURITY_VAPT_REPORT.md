# Security audit (VAPT) report

Generated: 2026-09-25T12:40:19.146632+00:00
Agent: `security_audit_agent` v1.0.0

## Summary

| Severity | Count |
|----------|-------|
| critical | 0 |
| high | 21 |
| medium | 16 |
| low | 0 |
| info | 0 |
| **Total** | **37** |

## Threat themes

- **EXTERNAL** — External attackers / public internet exposure (24 findings)
- **INTERNAL** — Insider misuse / employee data theft (13 findings)
- **COMPLIANCE** — SEBI / financial-data regulatory standards (0 findings)
- **CONFIG** — Misconfiguration & operational hardening (0 findings)

## Findings

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `migrations/add_client_google_drive_folder.py:33`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}"))
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `migrations/add_monthly_salary_incentive_columns.py:73`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `migrations/add_whatsapp_groups_tables.py:46`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(text(f"ALTER TABLE whatsapp_messages {clause}"))
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `migrations/add_whatsapp_groups_tables.py:54`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `migrations/fix_lead_onboarding_cascade_delete.py:44`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(text(f"ALTER TABLE `{table_name}` DROP FOREIGN KEY `{fk_name}`"))
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `migrations/fix_lead_onboarding_cascade_delete.py:45`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:113`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SELECT {col_list} FROM `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:176`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
dc.execute(f"DELETE FROM `{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:214`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
cur.execute(f"DROP DATABASE IF EXISTS `{target}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:215`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
cur.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:243`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SHOW CREATE TABLE `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:245`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
dc.execute(f"DROP TABLE IF EXISTS `{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:247`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SELECT COUNT(*) FROM `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:251`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SELECT * FROM `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:275`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SHOW CREATE VIEW `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:279`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
dc.execute(f"DROP VIEW IF EXISTS `{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:295`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SHOW CREATE {show} `{source}`.`{rname}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `scripts/utilities/clone_mysql_database.py:300`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
dc.execute(f"DROP {show} IF EXISTS `{rname}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `services/security_merge_service.py:410`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
moved = db.session.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-009` — File upload without `secure_filename` / path normalization

**Severity:** high • **Theme:** EXTERNAL • **Category:** path_traversal • **CWE:** CWE-22 • **Location:** `api/v1/public_contact.py:156`

Saving an uploaded file using its original filename (`document.filename`) allows path traversal (`../../etc/passwd`) and overwrite of arbitrary files the process can write.

```
uploaded = request.files.get(key)
```

**Fix:** Always wrap with `werkzeug.utils.secure_filename(name)`, validate the extension against an allowlist (e.g. `{'.pdf', '.png', '.jpg', '.csv'}`), and store under a UUID-based path inside a dedicated `instance/uploads/` directory that is **not** served as static content.

### `SEC-EXT-009` — File upload without `secure_filename` / path normalization

**Severity:** high • **Theme:** EXTERNAL • **Category:** path_traversal • **CWE:** CWE-22 • **Location:** `routes/leads.py:371`

Saving an uploaded file using its original filename (`document.filename`) allows path traversal (`../../etc/passwd`) and overwrite of arbitrary files the process can write.

```
files = [f for f in request.files.getlist('document') if f and f.filename]
```

**Fix:** Always wrap with `werkzeug.utils.secure_filename(name)`, validate the extension against an allowlist (e.g. `{'.pdf', '.png', '.jpg', '.csv'}`), and store under a UUID-based path inside a dedicated `instance/uploads/` directory that is **not** served as static content.

### `SEC-EXT-004` — Jinja `|safe` used in `airflow/email_templates/failure_content.html` (1 occurrence)

**Severity:** medium • **Theme:** EXTERNAL • **Category:** xss • **CWE:** CWE-79 • **Location:** `airflow/email_templates/failure_content.html:13`

`|safe` disables Jinja auto-escaping. If any rendered value originates from user-controlled input (client name, lead note, free-text fields), an attacker can inject HTML/JS. JSON-only `tojson|safe` patterns are generally fine.

```
L13: <pre style="background:#f5f5f5;padding:12px;overflow:auto;max-height:400px;">{{ exception_html | safe }}</pre>
```

**Fix:** Drop `|safe` and let Jinja auto-escape; for JSON-to-JS contexts use the existing `tojson_safe` filter; for trusted CMS/markdown, sanitize with `bleach.clean(value, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)` before rendering. Document each remaining `|safe` use in this file with a `{# SAFE: reason #}` comment.

### `SEC-EXT-005` — CSRF protection disabled on a route/blueprint

**Severity:** medium • **Theme:** EXTERNAL • **Category:** csrf_exempt • **CWE:** CWE-352 • **Location:** `api/v1/notification_centre.py:56`

`csrf.exempt` removes CSRF protection. Browsers will send the user's session cookie automatically, so a forged form on another site can perform this action.

```
@csrf.exempt
```

**Fix:** Re-enable CSRF for any session-authenticated endpoint. For API endpoints used by mobile/JWT clients, leave exempt but ensure the route is registered under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or v2 equivalent). Add an entry to the documented allowlist.

### `SEC-EXT-005` — CSRF protection disabled on a route/blueprint

**Severity:** medium • **Theme:** EXTERNAL • **Category:** csrf_exempt • **CWE:** CWE-352 • **Location:** `api/v1/notification_centre.py:117`

`csrf.exempt` removes CSRF protection. Browsers will send the user's session cookie automatically, so a forged form on another site can perform this action.

```
@csrf.exempt
```

**Fix:** Re-enable CSRF for any session-authenticated endpoint. For API endpoints used by mobile/JWT clients, leave exempt but ensure the route is registered under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or v2 equivalent). Add an entry to the documented allowlist.

### `SEC-CMP-004` — Database backup file in working tree: `.deploy_preserve/provision_20260921_193809/db.sql.gz`

**Severity:** medium • **Theme:** INTERNAL • **Category:** backup_in_tree • **Location:** `.deploy_preserve/provision_20260921_193809/db.sql.gz`

SQL dumps inside the repo or working tree get copied to laptops, containers, and CI artifacts — multiplying the blast radius of a breach.

**Fix:** Move backups to an off-host bucket (S3 / GCS) with object-lock + server-side encryption. Keep `backups/` in `.gitignore`. Enforce 30-day rotation via `scripts/cleanup_local_disk.sh` (already provided) or scheduled job.

### `SEC-CMP-004` — Database backup file in working tree: `.deploy_preserve/vps_code_snapshot/scripts/migration/backup_inertia_app2025_20250915_134823.sql`

**Severity:** medium • **Theme:** INTERNAL • **Category:** backup_in_tree • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/migration/backup_inertia_app2025_20250915_134823.sql`

SQL dumps inside the repo or working tree get copied to laptops, containers, and CI artifacts — multiplying the blast radius of a breach.

**Fix:** Move backups to an off-host bucket (S3 / GCS) with object-lock + server-side encryption. Keep `backups/` in `.gitignore`. Enforce 30-day rotation via `scripts/cleanup_local_disk.sh` (already provided) or scheduled job.

### `SEC-CMP-004` — Database backup file in working tree: `backups/inertia_app2025_20260922_210003.sql.gz`

**Severity:** medium • **Theme:** INTERNAL • **Category:** backup_in_tree • **Location:** `backups/inertia_app2025_20260922_210003.sql.gz`

SQL dumps inside the repo or working tree get copied to laptops, containers, and CI artifacts — multiplying the blast radius of a breach.

**Fix:** Move backups to an off-host bucket (S3 / GCS) with object-lock + server-side encryption. Keep `backups/` in `.gitignore`. Enforce 30-day rotation via `scripts/cleanup_local_disk.sh` (already provided) or scheduled job.

### `SEC-INT-002` — Export/download endpoint `download_invoice_pdf` is not audit-logged

**Severity:** medium • **Theme:** INTERNAL • **Category:** missing_audit_on_export • **CWE:** CWE-778 • **Location:** `routes/invoices.py:615`

Functions named export/download/csv/excel/backup typically return client data in bulk. Without an audit log entry, an internal user downloading the entire client base is invisible.

```
def download_invoice_pdf(
```

**Fix:** Call `services.audit_service.log_audit_event('bulk_export', resource_type='<entity>', details={'rows': n, 'filters': ...})` before returning the response. Include `client_id` when scope is single-client.

### `SEC-INT-002` — Export/download endpoint `download_invoice_portfolio_excel` is not audit-logged

**Severity:** medium • **Theme:** INTERNAL • **Category:** missing_audit_on_export • **CWE:** CWE-778 • **Location:** `routes/invoices.py:822`

Functions named export/download/csv/excel/backup typically return client data in bulk. Without an audit log entry, an internal user downloading the entire client base is invisible.

```
def download_invoice_portfolio_excel(
```

**Fix:** Call `services.audit_service.log_audit_event('bulk_export', resource_type='<entity>', details={'rows': n, 'filters': ...})` before returning the response. Include `client_id` when scope is single-client.

### `SEC-INT-002` — Export/download endpoint `download_lead_proposal` is not audit-logged

**Severity:** medium • **Theme:** INTERNAL • **Category:** missing_audit_on_export • **CWE:** CWE-778 • **Location:** `routes/leads.py:1339`

Functions named export/download/csv/excel/backup typically return client data in bulk. Without an audit log entry, an internal user downloading the entire client base is invisible.

```
def download_lead_proposal(
```

**Fix:** Call `services.audit_service.log_audit_event('bulk_export', resource_type='<entity>', details={'rows': n, 'filters': ...})` before returning the response. Include `client_id` when scope is single-client.

### `SEC-INT-002` — Export/download endpoint `download_regulatory_client_master_archive` is not audit-logged

**Severity:** medium • **Theme:** INTERNAL • **Category:** missing_audit_on_export • **CWE:** CWE-778 • **Location:** `routes/main.py:1721`

Functions named export/download/csv/excel/backup typically return client data in bulk. Without an audit log entry, an internal user downloading the entire client base is invisible.

```
def download_regulatory_client_master_archive(
```

**Fix:** Call `services.audit_service.log_audit_event('bulk_export', resource_type='<entity>', details={'rows': n, 'filters': ...})` before returning the response. Include `client_id` when scope is single-client.

### `SEC-INT-004` — Potential sensitive value in a log statement

**Severity:** medium • **Theme:** INTERNAL • **Category:** log_pii_leak • **CWE:** CWE-532 • **Location:** `services/lead_kyc_profile_service.py:133`

Log files can be read by internal users with shell access, shared with third parties for debugging, or ingested by SaaS log services. Secrets and PII inside logs are then effectively leaked.

```
logger.debug("KYC→agreement PAN sync skipped", exc_info=True)
```

**Fix:** Never log password / token / api_key / PAN values. Log a constant marker (e.g. `password=***`) and at most the first/last 2 characters of identifiers. Add a redaction filter to `logging.Logger`.

### `SEC-INT-004` — Potential sensitive value in a log statement

**Severity:** medium • **Theme:** INTERNAL • **Category:** log_pii_leak • **CWE:** CWE-532 • **Location:** `services/regulatory_client_master_service.py:174`

Log files can be read by internal users with shell access, shared with third parties for debugging, or ingested by SaaS log services. Secrets and PII inside logs are then effectively leaked.

```
logger.debug("PAN PDF extract failed agreement=%s: %s", agreement.id, exc)
```

**Fix:** Never log password / token / api_key / PAN values. Log a constant marker (e.g. `password=***`) and at most the first/last 2 characters of identifiers. Add a redaction filter to `logging.Logger`.

### `SEC-INT-004` — Potential sensitive value in a log statement

**Severity:** medium • **Theme:** INTERNAL • **Category:** log_pii_leak • **CWE:** CWE-532 • **Location:** `services/regulatory_client_master_service.py:231`

Log files can be read by internal users with shell access, shared with third parties for debugging, or ingested by SaaS log services. Secrets and PII inside logs are then effectively leaked.

```
logger.debug("KYC PAN lookup skipped: %s", exc)
```

**Fix:** Never log password / token / api_key / PAN values. Log a constant marker (e.g. `password=***`) and at most the first/last 2 characters of identifiers. Add a redaction filter to `logging.Logger`.

### `SEC-INT-004` — Potential sensitive value in a log statement

**Severity:** medium • **Theme:** INTERNAL • **Category:** log_pii_leak • **CWE:** CWE-532 • **Location:** `services/regulatory_identity_capture_service.py:44`

Log files can be read by internal users with shell access, shared with third parties for debugging, or ingested by SaaS log services. Secrets and PII inside logs are then effectively leaked.

```
logger.debug("KYC PAN read failed lead=%s", lead_id, exc_info=True)
```

**Fix:** Never log password / token / api_key / PAN values. Log a constant marker (e.g. `password=***`) and at most the first/last 2 characters of identifiers. Add a redaction filter to `logging.Logger`.

### `SEC-INT-004` — Potential sensitive value in a log statement

**Severity:** medium • **Theme:** INTERNAL • **Category:** log_pii_leak • **CWE:** CWE-532 • **Location:** `services/regulatory_identity_capture_service.py:177`

Log files can be read by internal users with shell access, shared with third parties for debugging, or ingested by SaaS log services. Secrets and PII inside logs are then effectively leaked.

```
logger.debug("KYC PAN backfill skipped lead=%s", lead_id, exc_info=True)
```

**Fix:** Never log password / token / api_key / PAN values. Log a constant marker (e.g. `password=***`) and at most the first/last 2 characters of identifiers. Add a redaction filter to `logging.Logger`.

### `SEC-INT-005` — `routes/client_google_drive.py` has 3 client-scoped route(s) without an explicit access check

**Severity:** medium • **Theme:** INTERNAL • **Category:** missing_client_scope • **CWE:** CWE-639 • **Location:** `routes/client_google_drive.py:77`

Routes that take `<int:client_id>` should verify the current user can see that client. Without it, an advisor can read other advisors' clients by guessing IDs.

```
L77: @client_google_drive_bp.route(; L113: @client_google_drive_bp.route(; L154: @client_google_drive_bp.route(
```

**Fix:** Add `@client_access_required` (from `access_control`) to each route, OR add a `before_request` hook on this blueprint that calls `can_access_client(client_id)` and aborts 403 if not. Also call `services.audit_service.log_audit_event('client_view', client_id=client_id)` so internal access is recorded for SEBI.

---
Run again: `python3 scripts/run_security_audit.py --write-report`