# Security audit (VAPT) report

Generated: 2026-09-16T17:08:39.928363+00:00
Agent: `security_audit_agent` v1.0.0

## Summary

| Severity | Count |
|----------|-------|
| critical | 5 |
| high | 49 |
| medium | 13 |
| low | 0 |
| info | 0 |
| **Total** | **67** |

## Threat themes

- **EXTERNAL** — External attackers / public internet exposure (62 findings)
- **INTERNAL** — Insider misuse / employee data theft (5 findings)
- **COMPLIANCE** — SEBI / financial-data regulatory standards (0 findings)
- **CONFIG** — Misconfiguration & operational hardening (0 findings)

## Findings

### `SEC-EXT-001` — Hardcoded credential found (Production DB password literal)

**Severity:** critical • **Theme:** EXTERNAL • **Category:** hardcoded_secret • **CWE:** CWE-798 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:335`

A literal credential is checked into source. Anyone with repo or backup access (employees, contractors, CI logs) can read it.

```
(re.compile(r"!Nert!a2025\$?"), "Production DB password literal"),
```

**Fix:** Move the value to `.env` and read via `os.environ`. Rotate the credential immediately since it has been exposed in git history. Add the file to `.gitignore` if it was a secrets file.

### `SEC-EXT-001` — Hardcoded credential found (Production DB password literal)

**Severity:** critical • **Theme:** EXTERNAL • **Category:** hardcoded_secret • **CWE:** CWE-798 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/audit_sebi_compliance.py:123`

A literal credential is checked into source. Anyone with repo or backup access (employees, contractors, CI logs) can read it.

```
hardcoded = _grep_files(r"!Nert!a2025|password\s*=\s*['\"]Sharveen['\"]", "scripts/**/*.py")
```

**Fix:** Move the value to `.env` and read via `os.environ`. Rotate the credential immediately since it has been exposed in git history. Add the file to `.gitignore` if it was a secrets file.

### `SEC-EXT-001` — Hardcoded credential found (Production DB password literal)

**Severity:** critical • **Theme:** EXTERNAL • **Category:** hardcoded_secret • **CWE:** CWE-798 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/run_full_codebase_audit.py:56`

A literal credential is checked into source. Anyone with repo or backup access (employees, contractors, CI logs) can read it.

```
hardcoded_pw = _grep_count(r"!Nert!a2025|password.*=.*['\"]Sharveen")
```

**Fix:** Move the value to `.env` and read via `os.environ`. Rotate the credential immediately since it has been exposed in git history. Add the file to `.gitignore` if it was a secrets file.

### `SEC-EXT-001` — Hardcoded credential found (Production DB password literal)

**Severity:** critical • **Theme:** EXTERNAL • **Category:** hardcoded_secret • **CWE:** CWE-798 • **Location:** `.deploy_preserve/vps_code_snapshot/tests/test_security_audit_agent.py:67`

A literal credential is checked into source. Anyone with repo or backup access (employees, contractors, CI logs) can read it.

```
"scripts/dump.py": "DB_PASSWORD = '!Nert!a2025'\n",
```

**Fix:** Move the value to `.env` and read via `os.environ`. Rotate the credential immediately since it has been exposed in git history. Add the file to `.gitignore` if it was a secrets file.

### `SEC-EXT-001` — Hardcoded credential found (Production DB password literal)

**Severity:** critical • **Theme:** EXTERNAL • **Category:** hardcoded_secret • **CWE:** CWE-798 • **Location:** `.deploy_preserve/vps_code_snapshot/tests/test_security_audit_agent.py:231`

A literal credential is checked into source. Anyone with repo or backup access (employees, contractors, CI logs) can read it.

```
"scripts/dump.py": "DB_PASSWORD = '!Nert!a2025'\n",
```

**Fix:** Move the value to `.env` and read via `os.environ`. Rotate the credential immediately since it has been exposed in git history. Add the file to `.gitignore` if it was a secrets file.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/migrations/add_monthly_salary_incentive_columns.py:73`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/migrations/add_whatsapp_groups_tables.py:46`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(text(f"ALTER TABLE whatsapp_messages {clause}"))
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/migrations/add_whatsapp_groups_tables.py:54`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/migrations/fix_lead_onboarding_cascade_delete.py:44`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(text(f"ALTER TABLE `{table_name}` DROP FOREIGN KEY `{fk_name}`"))
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/migrations/fix_lead_onboarding_cascade_delete.py:45`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
db.session.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:113`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SELECT {col_list} FROM `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:176`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
dc.execute(f"DELETE FROM `{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:214`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
cur.execute(f"DROP DATABASE IF EXISTS `{target}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:215`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
cur.execute(
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:243`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SHOW CREATE TABLE `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:245`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
dc.execute(f"DROP TABLE IF EXISTS `{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:247`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SELECT COUNT(*) FROM `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:251`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SELECT * FROM `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:275`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SHOW CREATE VIEW `{source}`.`{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:279`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
dc.execute(f"DROP VIEW IF EXISTS `{name}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:295`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
sc.execute(f"SHOW CREATE {show} `{source}`.`{rname}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/utilities/clone_mysql_database.py:300`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
dc.execute(f"DROP {show} IF EXISTS `{rname}`")
```

**Fix:** Use parameter binding: `db.session.execute(text('SELECT ... WHERE id = :id'), {'id': value})`. Never concatenate or f-string user data into SQL.

### `SEC-EXT-002` — Possible SQL injection: f-string passed to execute()

**Severity:** high • **Theme:** EXTERNAL • **Category:** sql_injection • **CWE:** CWE-89 • **Location:** `.deploy_preserve/vps_code_snapshot/services/security_merge_service.py:410`

Constructing SQL with an f-string interpolates Python expressions directly into the query. If any value comes from user input, this is a SQL injection.

```
moved = db.session.execute(
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

### `SEC-EXT-003` — Dangerous call: subprocess shell=True

**Severity:** high • **Theme:** EXTERNAL • **Category:** dangerous_call • **CWE:** CWE-94 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:433`

Shell command injection risk. If any input reaches this call, remote code execution is possible.

```
# ---- 3) EXTERNAL: dangerous eval / exec / shell=True ----
```

**Fix:** Replace with safe alternatives: use `ast.literal_eval` instead of `eval`, `json.loads` / `yaml.safe_load` instead of `pickle`/`yaml.load`, and pass argument lists to `subprocess.run([...], shell=False)` instead of shell strings.

### `SEC-EXT-003` — Dangerous call: eval()

**Severity:** high • **Theme:** EXTERNAL • **Category:** dangerous_call • **CWE:** CWE-94 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:436`

Python expression evaluation. If any input reaches this call, remote code execution is possible.

```
(re.compile(r"\beval\s*\("),  "eval()", "Python expression evaluation"),
```

**Fix:** Replace with safe alternatives: use `ast.literal_eval` instead of `eval`, `json.loads` / `yaml.safe_load` instead of `pickle`/`yaml.load`, and pass argument lists to `subprocess.run([...], shell=False)` instead of shell strings.

### `SEC-EXT-003` — Dangerous call: exec()

**Severity:** high • **Theme:** EXTERNAL • **Category:** dangerous_call • **CWE:** CWE-94 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:437`

Dynamic code execution. If any input reaches this call, remote code execution is possible.

```
(re.compile(r"\bexec\s*\("),  "exec()", "Dynamic code execution"),
```

**Fix:** Replace with safe alternatives: use `ast.literal_eval` instead of `eval`, `json.loads` / `yaml.safe_load` instead of `pickle`/`yaml.load`, and pass argument lists to `subprocess.run([...], shell=False)` instead of shell strings.

### `SEC-EXT-003` — Dangerous call: yaml.load (without SafeLoader)

**Severity:** high • **Theme:** EXTERNAL • **Category:** dangerous_call • **CWE:** CWE-94 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:439`

Unsafe YAML load. If any input reaches this call, remote code execution is possible.

```
(re.compile(r"\byaml\.load\s*\((?![^)]*Loader\s*=)"), "yaml.load (without SafeLoader)", "Unsafe YAML load"),
```

**Fix:** Replace with safe alternatives: use `ast.literal_eval` instead of `eval`, `json.loads` / `yaml.safe_load` instead of `pickle`/`yaml.load`, and pass argument lists to `subprocess.run([...], shell=False)` instead of shell strings.

### `SEC-EXT-003` — Dangerous call: subprocess shell=True

**Severity:** high • **Theme:** EXTERNAL • **Category:** dangerous_call • **CWE:** CWE-94 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:440`

Shell command injection risk. If any input reaches this call, remote code execution is possible.

```
(re.compile(r"shell\s*=\s*True"), "subprocess shell=True", "Shell command injection risk"),
```

**Fix:** Replace with safe alternatives: use `ast.literal_eval` instead of `eval`, `json.loads` / `yaml.safe_load` instead of `pickle`/`yaml.load`, and pass argument lists to `subprocess.run([...], shell=False)` instead of shell strings.

### `SEC-EXT-003` — Dangerous call: os.system()

**Severity:** high • **Theme:** EXTERNAL • **Category:** dangerous_call • **CWE:** CWE-94 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:441`

Shell command injection risk. If any input reaches this call, remote code execution is possible.

```
(re.compile(r"\bos\.system\s*\("), "os.system()", "Shell command injection risk"),
```

**Fix:** Replace with safe alternatives: use `ast.literal_eval` instead of `eval`, `json.loads` / `yaml.safe_load` instead of `pickle`/`yaml.load`, and pass argument lists to `subprocess.run([...], shell=False)` instead of shell strings.

### `SEC-EXT-003` — Dangerous call: eval()

**Severity:** high • **Theme:** EXTERNAL • **Category:** dangerous_call • **CWE:** CWE-94 • **Location:** `.deploy_preserve/vps_code_snapshot/tests/test_security_audit_agent.py:88`

Python expression evaluation. If any input reaches this call, remote code execution is possible.

```
"services/dangerous.py": "def run(x):\n    return eval(x)\n",
```

**Fix:** Replace with safe alternatives: use `ast.literal_eval` instead of `eval`, `json.loads` / `yaml.safe_load` instead of `pickle`/`yaml.load`, and pass argument lists to `subprocess.run([...], shell=False)` instead of shell strings.

### `SEC-EXT-007b` — TLS certificate verification disabled

**Severity:** high • **Theme:** EXTERNAL • **Category:** tls_verify_off • **CWE:** CWE-295 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:690`

`verify=False` allows man-in-the-middle attacks on outbound HTTPS calls.

```
# SSL verify=False
```

**Fix:** Remove `verify=False`. If the target uses a private CA, pass `verify='/path/to/ca.pem'`.

### `SEC-EXT-007b` — TLS certificate verification disabled

**Severity:** high • **Theme:** EXTERNAL • **Category:** tls_verify_off • **CWE:** CWE-295 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:696`

`verify=False` allows man-in-the-middle attacks on outbound HTTPS calls.

```
if "verify=False" in text:
```

**Fix:** Remove `verify=False`. If the target uses a private CA, pass `verify='/path/to/ca.pem'`.

### `SEC-EXT-007b` — TLS certificate verification disabled

**Severity:** high • **Theme:** EXTERNAL • **Category:** tls_verify_off • **CWE:** CWE-295 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:708`

`verify=False` allows man-in-the-middle attacks on outbound HTTPS calls.

```
description="`verify=False` allows man-in-the-middle attacks on outbound HTTPS calls.",
```

**Fix:** Remove `verify=False`. If the target uses a private CA, pass `verify='/path/to/ca.pem'`.

### `SEC-EXT-007b` — TLS certificate verification disabled

**Severity:** high • **Theme:** EXTERNAL • **Category:** tls_verify_off • **CWE:** CWE-295 • **Location:** `.deploy_preserve/vps_code_snapshot/agents/security_audit_agent.py:709`

`verify=False` allows man-in-the-middle attacks on outbound HTTPS calls.

```
fix="Remove `verify=False`. If the target uses a private CA, pass `verify='/path/to/ca.pem'`.",
```

**Fix:** Remove `verify=False`. If the target uses a private CA, pass `verify='/path/to/ca.pem'`.

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

### `SEC-EXT-004` — Jinja `|safe` used in `.deploy_preserve/vps_code_snapshot/airflow/email_templates/failure_content.html` (1 occurrence)

**Severity:** medium • **Theme:** EXTERNAL • **Category:** xss • **CWE:** CWE-79 • **Location:** `.deploy_preserve/vps_code_snapshot/airflow/email_templates/failure_content.html:12`

`|safe` disables Jinja auto-escaping. If any rendered value originates from user-controlled input (client name, lead note, free-text fields), an attacker can inject HTML/JS. JSON-only `tojson|safe` patterns are generally fine.

```
L12: <pre style="background:#f5f5f5;padding:12px;overflow:auto;max-height:400px;">{{ exception_html | safe }}</pre>
```

**Fix:** Drop `|safe` and let Jinja auto-escape; for JSON-to-JS contexts use the existing `tojson_safe` filter; for trusted CMS/markdown, sanitize with `bleach.clean(value, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)` before rendering. Document each remaining `|safe` use in this file with a `{# SAFE: reason #}` comment.

### `SEC-EXT-004` — Jinja `|safe` used in `airflow/email_templates/failure_content.html` (1 occurrence)

**Severity:** medium • **Theme:** EXTERNAL • **Category:** xss • **CWE:** CWE-79 • **Location:** `airflow/email_templates/failure_content.html:13`

`|safe` disables Jinja auto-escaping. If any rendered value originates from user-controlled input (client name, lead note, free-text fields), an attacker can inject HTML/JS. JSON-only `tojson|safe` patterns are generally fine.

```
L13: <pre style="background:#f5f5f5;padding:12px;overflow:auto;max-height:400px;">{{ exception_html | safe }}</pre>
```

**Fix:** Drop `|safe` and let Jinja auto-escape; for JSON-to-JS contexts use the existing `tojson_safe` filter; for trusted CMS/markdown, sanitize with `bleach.clean(value, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)` before rendering. Document each remaining `|safe` use in this file with a `{# SAFE: reason #}` comment.

### `SEC-EXT-005` — CSRF protection disabled on a route/blueprint

**Severity:** medium • **Theme:** EXTERNAL • **Category:** csrf_exempt • **CWE:** CWE-352 • **Location:** `.deploy_preserve/vps_code_snapshot/__init__.py:507`

`csrf.exempt` removes CSRF protection. Browsers will send the user's session cookie automatically, so a forged form on another site can perform this action.

```
csrf.exempt(enhanced_tax_optimiser_bp)  # JSON API; session not used for mutations
```

**Fix:** Re-enable CSRF for any session-authenticated endpoint. For API endpoints used by mobile/JWT clients, leave exempt but ensure the route is registered under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or v2 equivalent). Add an entry to the documented allowlist.

### `SEC-EXT-005` — CSRF protection disabled on a route/blueprint

**Severity:** medium • **Theme:** EXTERNAL • **Category:** csrf_exempt • **CWE:** CWE-352 • **Location:** `.deploy_preserve/vps_code_snapshot/__init__.py:512`

`csrf.exempt` removes CSRF protection. Browsers will send the user's session cookie automatically, so a forged form on another site can perform this action.

```
csrf.exempt(public_contact_bp)  # public_contact_bp: API-key website form
```

**Fix:** Re-enable CSRF for any session-authenticated endpoint. For API endpoints used by mobile/JWT clients, leave exempt but ensure the route is registered under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or v2 equivalent). Add an entry to the documented allowlist.

### `SEC-EXT-005` — CSRF protection disabled on a route/blueprint

**Severity:** medium • **Theme:** EXTERNAL • **Category:** csrf_exempt • **CWE:** CWE-352 • **Location:** `.deploy_preserve/vps_code_snapshot/__init__.py:517`

`csrf.exempt` removes CSRF protection. Browsers will send the user's session cookie automatically, so a forged form on another site can perform this action.

```
csrf.exempt(auth_api_bp)  # auth_api_bp: JWT login/2FA only
```

**Fix:** Re-enable CSRF for any session-authenticated endpoint. For API endpoints used by mobile/JWT clients, leave exempt but ensure the route is registered under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or v2 equivalent). Add an entry to the documented allowlist.

### `SEC-EXT-005` — CSRF protection disabled on a route/blueprint

**Severity:** medium • **Theme:** EXTERNAL • **Category:** csrf_exempt • **CWE:** CWE-352 • **Location:** `.deploy_preserve/vps_code_snapshot/__init__.py:522`

`csrf.exempt` removes CSRF protection. Browsers will send the user's session cookie automatically, so a forged form on another site can perform this action.

```
csrf.exempt(leegality_bp)  # leegality_bp: HMAC mac webhook from Leegality
```

**Fix:** Re-enable CSRF for any session-authenticated endpoint. For API endpoints used by mobile/JWT clients, leave exempt but ensure the route is registered under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or v2 equivalent). Add an entry to the documented allowlist.

### `SEC-EXT-005` — CSRF protection disabled on a route/blueprint

**Severity:** medium • **Theme:** EXTERNAL • **Category:** csrf_exempt • **CWE:** CWE-352 • **Location:** `.deploy_preserve/vps_code_snapshot/api/v2/transactions.py:1204`

`csrf.exempt` removes CSRF protection. Browsers will send the user's session cookie automatically, so a forged form on another site can perform this action.

```
csrf.exempt(transactions_v2_bp)
```

**Fix:** Re-enable CSRF for any session-authenticated endpoint. For API endpoints used by mobile/JWT clients, leave exempt but ensure the route is registered under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or v2 equivalent). Add an entry to the documented allowlist.

### `SEC-EXT-005` — CSRF protection disabled on a route/blueprint

**Severity:** medium • **Theme:** EXTERNAL • **Category:** csrf_exempt • **CWE:** CWE-352 • **Location:** `.deploy_preserve/vps_code_snapshot/tests/test_security_audit_agent.py:119`

`csrf.exempt` removes CSRF protection. Browsers will send the user's session cookie automatically, so a forged form on another site can perform this action.

```
"@csrf.exempt\n"
```

**Fix:** Re-enable CSRF for any session-authenticated endpoint. For API endpoints used by mobile/JWT clients, leave exempt but ensure the route is registered under `/api/*` and protected by `api/v1/auth_guard.enforce_v1_api_auth` (or v2 equivalent). Add an entry to the documented allowlist.

### `SEC-CMP-004` — Database backup file in working tree: `.deploy_preserve/vps_code_snapshot/scripts/migration/backup_inertia_app2025_20250915_134823.sql`

**Severity:** medium • **Theme:** INTERNAL • **Category:** backup_in_tree • **Location:** `.deploy_preserve/vps_code_snapshot/scripts/migration/backup_inertia_app2025_20250915_134823.sql`

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

### `SEC-INT-004` — Potential sensitive value in a log statement

**Severity:** medium • **Theme:** INTERNAL • **Category:** log_pii_leak • **CWE:** CWE-532 • **Location:** `.deploy_preserve/vps_code_snapshot/tests/test_security_audit_agent.py:193`

Log files can be read by internal users with shell access, shared with third parties for debugging, or ingested by SaaS log services. Secrets and PII inside logs are then effectively leaked.

```
"    logger.info(f'login with password={pw}')\n"
```

**Fix:** Never log password / token / api_key / PAN values. Log a constant marker (e.g. `password=***`) and at most the first/last 2 characters of identifiers. Add a redaction filter to `logging.Logger`.

---
Run again: `python3 scripts/run_security_audit.py --write-report`