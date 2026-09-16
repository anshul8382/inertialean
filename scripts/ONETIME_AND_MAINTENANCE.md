# One-time fixes and maintenance scripts

Quick reference for **ad-hoc** scripts under `app/scripts/`.  
Run from **`/home/inertia/app`** unless noted otherwise.

**Safe defaults:** Prefer `--dry-run` first; use `--execute` / `--apply` only after review.  
Keep JSON backups produced by repair scripts until production is verified.

---

## Recommendation action / trade recording (May 2025)

| Script | Purpose | Status |
|--------|---------|--------|
| [`repair_recommendation_actions.py`](repair_recommendation_actions.py) | Fix rows where `action=HOLD` but amount/qty imply BUY/SELL. Transactional `--execute`, JSON backup, `--rollback`. | **Already run on prod DB** (1,178 rows). Keep script for rollback / rare re-run. |

```bash
python scripts/repair_recommendation_actions.py --dry-run
python scripts/repair_recommendation_actions.py --execute
python scripts/repair_recommendation_actions.py --rollback backups/recommendation_action_repair_YYYYMMDD_HHMMSS.json
python scripts/repair_recommendation_actions.py --client-id 154   # optional scope
```

**Production app code (ongoing behaviour, not one-time):**

| File | Role |
|------|------|
| `services/recommendation_trade_normalizer.py` | Derive BUY/SELL/HOLD from quantity change + amount only (ignores badge). |
| `routes/unified_recommendations.py` | `record_recommendations` uses normalizer on save. |
| `services/recommendation_data_service.py` | `save_section1_to_db` uses normalizer. |
| `templates/unified_recommendations/security_distribution.html` | Record UI: `deriveActionFromTrade()`, not badge. |
| `routes/recommended_trades.py` + `templates/recommended_trades/client_trades.html` | Show HOLD + BUY/SELL; optional per-client repair on page load. |
| `models.py` | `client_trade_list_sql_filters()`, `is_hold_line`. |

Rollback backups (server-local, not in git): `backups/recommendation_action_repair_*.json`

---

## Alerts and workflow

| Script | Purpose |
|--------|---------|
| [`close_duplicate_alerts.py`](close_duplicate_alerts.py) | Resolve duplicate workflow SLA alerts (`--dry-run` / `--apply`). |
| [`cleanup_workflow_alerts.py`](cleanup_workflow_alerts.py) | Clean up workflow-related alerts. |
| [`cleanup_workflow_sla_issue_unification.py`](cleanup_workflow_sla_issue_unification.py) | SLA / issue unification cleanup. |
| [`reconcile_alerts_tasks.py`](reconcile_alerts_tasks.py) | Reconcile alerts vs tasks. |
| [`reconcile_issue_lifecycle.py`](reconcile_issue_lifecycle.py) | Issue lifecycle reconciliation. |
| [`backfill_unassigned_alerts.py`](backfill_unassigned_alerts.py) | Backfill unassigned alerts. |
| [`backfill_workflow_stage_alerts_and_tasks.py`](backfill_workflow_stage_alerts_and_tasks.py) | Backfill workflow stage alerts/tasks. |
| [`dedupe_superseded_recommendation_match_once.py`](dedupe_superseded_recommendation_match_once.py) | Dedupe superseded recommendation matches. |
| [`dry_run_alert_task_pipeline.py`](dry_run_alert_task_pipeline.py) | Dry-run alert/task pipeline. |

---

## Recommendations / trades (other)

| Script | Purpose |
|--------|---------|
| [`process_executed_recommendations.py`](process_executed_recommendations.py) | Process executed recommendations (batch). |

---

## Data backfill and integrity

| Script | Purpose |
|--------|---------|
| [`backfill_monthly_investment_portfolio_id.py`](backfill_monthly_investment_portfolio_id.py) | Backfill `portfolio_id` on monthly investments. |
| [`backfill_issue_monthly_investment_id.py`](backfill_issue_monthly_investment_id.py) | Link issues to monthly investments. |
| [`backfill_historical_prices_from_sheets.py`](backfill_historical_prices_from_sheets.py) | Historical prices from sheets. |
| [`sync_historical_prices_from_sheets.py`](sync_historical_prices_from_sheets.py) | Sync historical prices from sheets. |
| [`find_historical_price_gaps.py`](find_historical_price_gaps.py) | Report price gaps. |
| [`report_missing_historical_data.py`](report_missing_historical_data.py) | Report missing historical data. |
| [`fix_transaction_integrity.py`](fix_transaction_integrity.py) | Fix transaction integrity issues. |
| [`populate_client_fields_from_cashflow.py`](populate_client_fields_from_cashflow.py) | Populate client fields from cashflows. |
| [`scan_agreement_special_notes.py`](scan_agreement_special_notes.py) | Dry-run: compare `special_note` text to structured billing fields ([doc](../docs/AGREEMENT_BILLING_SCRIPTS.md)). |
| [`backfill_billing_from_special_notes.py`](backfill_billing_from_special_notes.py) | Fill **empty** billing fields from special notes; fix PDF paths (`--apply`, `--pdf-only`). |

---

## Workflows and monthly investments

| Script | Purpose |
|--------|---------|
| [`fix_missing_workflows.py`](fix_missing_workflows.py) | Create/fix missing workflows. |
| [`refresh_missing_workflows.py`](refresh_missing_workflows.py) | Refresh missing workflows. |
| [`check_client_workflows.py`](check_client_workflows.py) | Audit client workflows. |
| [`start_monthly_cycle.py`](start_monthly_cycle.py) | Start monthly investment cycle. |
| [`create_default_monthly_schedules.py`](create_default_monthly_schedules.py) | Default monthly schedules. |

---

## Integrations and cron helpers

| Script | Purpose |
|--------|---------|
| [`sync_google_calendar_backfill.py`](sync_google_calendar_backfill.py) | Google Calendar backfill. |
| [`sync_amfi_mutual_fund_nav.py`](sync_amfi_mutual_fund_nav.py) | AMFI MF NAV sync. |
| [`daily_db_backup.py`](daily_db_backup.py) | DB backup helper. |
| [`codebase_backup.py`](codebase_backup.py) | Codebase backup. |
| [`cron_run_with_alert.py`](cron_run_with_alert.py) | Wrap cron job with alert on failure. |
| [`scheduled_jobs_status_report.py`](scheduled_jobs_status_report.py) | Scheduled jobs status. |

---

## DB / schema migrations (run with care)

Under `scripts/migration/` — review each SQL/Python file before running on production.

---

## Utilities

One-off utilities live in [`scripts/utilities/`](utilities/) (models, holdings, alerts, etc.).  
Deprecated copies may exist under `_deprecated/scripts/`.

---

## Adding a new one-time script

1. Add `scripts/your_script.py` with `--dry-run` and `--execute` (or `--apply`) where possible.  
2. Document it in the **appropriate section above** with one-line purpose and example command.  
3. Do **not** commit `backups/`, `.venv/`, `gunicorn.pid`, or `tmp/` output.
