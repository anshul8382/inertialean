# Security audit (VAPT) report

Generated: 2026-09-15T17:26:01.186319+00:00
Agent: `security_audit_agent` v1.0.0

## Summary

| Severity | Count |
|----------|-------|
| critical | 0 |
| high | 20 |
| medium | 3 |
| low | 0 |
| info | 0 |
| **Total** | **23** |

## Threat themes

- **EXTERNAL** — External attackers / public internet exposure (20 findings)
- **INTERNAL** — Insider misuse / employee data theft (3 findings)
- **COMPLIANCE** — SEBI / financial-data regulatory standards (0 findings)
- **CONFIG** — Misconfiguration & operational hardening (0 findings)

## Findings

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

---
Run again: `python3 scripts/run_security_audit.py --write-report`