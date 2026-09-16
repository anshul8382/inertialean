# `app` vs `app_test` — codebase comparison

**Paths:** `/home/inertia/app` (production / active workspace) vs `/home/inertia/app_test` (test snapshot, tree dated ~Jan 24 2026).  
**Method:** File presence + `diff -qr` on key folders (excludes `venv/`, `.git/`, `__pycache__/`, `flask_sessions/`, `uploads/`, `airflow/logs/`, `*.log`, etc.).

---

## Executive summary

| Observation | Detail |
|-------------|--------|
| **More files in `app`** | ~**799** paths exist only under `app`; ~**93** only under `app_test`. |
| **Your intuition** | Many **feature modules** exist in `app` that are **not** in `app_test` (newer development). |
| **“Base is dated” risk** | `app`’s **`routes/main.py` is ~340 lines** vs **`app_test`’s ~7,532 lines**. The small file is a **stub** used to fix import/`url_for` crashes; **`app_test` still has the real monolithic main blueprint.** |
| **Templates** | `base.html` differs in size (**525** lines in `app` vs **571** in `app_test`); **`base_clean.html`** is **same line count** (273) — may still differ in content. |

So: **`app` = newer feature modules + migrations + APIs**, but **`app_test` = fuller legacy `main` routes**. Merging should restore **`routes/main.py` from `app_test` (or git)** into `app` while keeping `app`’s new blueprints registered in `__init__.py`.

---

## 1. Files that exist only in `app` (feature-shaped)

Grouped by area; these strongly suggest **features developed after the `app_test` snapshot**.

### 1.1 New route blueprints (`routes/*.py` only in `app`)

| Module | Likely feature |
|--------|----------------|
| `account_management.py` | Account / bank statement management UI |
| `campaign_studio.py` | Campaign studio |
| `financial_planning.py` | Financial planning |
| `parrva.py` | Parrva integration |
| `portfolio_performance.py` | Portfolio performance monitoring |
| `roles.py` | Roles (RBAC-related) |
| `task_assignment_rules.py` | Admin task assignment rules |
| `tasks.py` | Ops tasks (`/tasks/`) |
| `tax_optimiser_enhanced.py` | Tax optimiser (enhanced) UI |

### 1.2 New services (`services/*.py` only in `app`)

| Module | Likely feature |
|--------|----------------|
| `bank_statement_service.py` | Bank statements |
| `capital_gains_service.py` | Capital gains (shared with tax tools) |
| `enhanced_tax_optimiser_service.py` | Enhanced tax optimiser engine |
| `fy_tax_utils.py` | FY-aware tax helpers |
| `holdings_report_service.py` | Holdings report |
| `parrva_service.py` | Parrva backend |
| `task_assignment_service.py` | Task assignment |
| `task_auto_close_service.py` | Task auto-close |
| `task_reminder_service.py` | Task reminders |
| `tax_optimiser_interactive_service.py` | Interactive tax review sessions |
| `tax_optimiser_page_service.py` | Tax optimiser page glue |
| `workflow_stage_service.py` | Workflow stage tasks/alerts |
| `ai_insights_learning_service.py` | ML / AI insights (if enabled) |

### 1.3 New API modules (`api/` only in `app`)

| Path | Likely feature |
|------|----------------|
| `api/v1/campaign_studio_api.py` | Campaign studio API |
| `api/v1/enhanced_tax_optimiser.py` | Enhanced tax optimiser REST API |
| `api/v1/finvantage_rules.py` | Finvantage rules API |
| `api/v1/claude_proxy.py`, `openai_proxy.py`, `openai_images_proxy.py`, `perplexity_proxy.py` | LLM/image proxy endpoints |

### 1.4 New agents (`agents/` only in `app`)

- `portfolio_performance_monitor.py`
- `task_assignment_agent.py`

### 1.5 New Airflow DAGs (`airflow/dags/` only in `app`)

- `portfolio_performance_dag.py`
- `scheduled_jobs_status_dag.py`
- `task_assignment_dag.py`
- `task_auto_close_dag.py`
- `task_reminders_dag.py`  
- *(plus `holdings_cycle_dag.py` present as new path vs snapshot — verify naming parity with `app_test`.)*

### 1.6 New template areas (only in `app`, samples)

- `templates/account_management/*`
- `templates/campaign_studio/*`
- `templates/financial_planning/*`
- `templates/parrva/*`
- `templates/portfolio_performance/*`
- `templates/tools/tax_optimiser_enhanced.html`, `tax_optimiser.html`, `capital_gains.html`, `holdings.html`
- `templates/email/tax_optimiser_client.html`, `task_reminder.html`
- `templates/admin/task_assignment_rules.html`, deployment/email logs
- Various **backups**: `base.html.before_app_test_*`, `base_backup.html`, etc.

### 1.7 Migrations only in `app`

Many scripts under `migrations/` (Finvantage alters, ops tasks, tax optimiser tables, task assignment seeds, financial planning, account management, etc.). Full list: run the command in §4.

---

## 2. Files that exist only in `app_test` (not in `app`)

Mostly **scripts/docs/debug** (e.g. `debug_*.py`, `demo_*.py`, `verify_*.py`, `requirements_v2.txt`, `mobile-app/`, extra `flask_session` files). These are not necessarily “features” missing from `app` — often **test-only or obsolete**.

---

## 3. Same path in both trees but **different content** (`diff -qr`)

Approximate counts:

| Directory | Files reported as **differing** |
|-----------|----------------------------------|
| `routes/` | 58 |
| `services/` | 33 |
| `api/` | 35 |
| `templates/` | 62 |
| `agents/` | 9 |
| `airflow/dags/` | 13 |
| `migrations/` | 1 |

Root files **both sides, different:** `__init__.py`, `main.py`, `models.py`, `config.py`, `review_routes.py`.

**Critical structural difference**

- **`routes/main.py`:** `app` **~340 lines** vs `app_test` **~7,532 lines** → `app` is **not** the full application main blueprint.

---

## 4. How to regenerate this report

```bash
# File sets (same exclusions as used for this doc)
listrel() {
  find "$1" -type f \
    ! -path "*/venv/*" ! -path "*/.git/*" ! -path "*/__pycache__/*" \
    ! -path "*/flask_sessions/*" ! -path "*/uploads/*" ! -path "*/airflow/logs/*" \
    ! -path "*/node_modules/*" ! -path "*/.cursor/*" \
    ! -name "*.pyc" ! -name "gunicorn.pid" ! -name "*.log" \
    -printf '%P\n' 2>/dev/null | sort -u
}

comm -23 <(listrel /home/inertia/app) <(listrel /home/inertia/app_test)) > /tmp/only_app.txt
comm -13 <(listrel /home/inertia/app) <(listrel /home/inertia/app_test)) > /tmp/only_app_test.txt

# Quick diffs
diff -qr /home/inertia/app/routes /home/inertia/app_test/routes | grep differ
```

---

## 5. Recommended next step (process)

1. **Treat `app_test/routes/main.py` (or git `main` branch / backup tag) as the canonical full `main` blueprint** for hundreds of `url_for('main.*')` links in `base.html`.
2. **Merge into `app`:** copy or three-way-merge `routes/main.py`, then re-register any **new** blueprints only present in `app`’s `__init__.py` (tasks, tax optimiser enhanced, campaign studio, etc.).
3. **Re-diff** `templates/base.html` between trees and resolve intentionally (prod nav vs test nav).

---

*This document is a mechanical diff, not a product sign-off. Feature names are inferred from filenames.*
