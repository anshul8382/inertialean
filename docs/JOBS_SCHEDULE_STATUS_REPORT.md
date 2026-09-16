# Jobs Schedule & Status Report

**Generated:** Friday, Feb 13, 2026 (IST)  
**Server Timezone:** Asia/Kolkata (IST)

---

## 🚨 CRITICAL: Root Cause – Airflow Not Running

**The Airflow scheduler and webserver are NOT running.** No Airflow processes were found. As a result:

- **All agent DAGs** (Data Integrity, Recommendation Execution, Portfolio Performance) – **NOT running**
- **All Airflow-scheduled jobs** – **NOT running**
- **Last successful DAG run:** Feb 10, 2026 (3 days ago)

### How to Start Airflow
```bash
cd /home/inertia/app
./scripts/start_airflow.sh
```
- Webserver: http://localhost:8081 (or your configured port)
- Login: admin / inertia2025

**Auto-start on boot:** See [AIRFLOW_SYSTEMD_SETUP.md](AIRFLOW_SYSTEMD_SETUP.md) for systemd service setup.

---

## 1. AGENTS (Scheduled via Airflow DAGs)

| Agent | DAG ID | Schedule (IST) | Status | Last Run |
|-------|--------|----------------|--------|----------|
| Data Integrity Manager (daily) | `data_integrity_daily` | Daily 8:30 AM | Unpaused | Feb 10, 2026 |
| Data Integrity Manager (weekly) | `data_integrity_weekly` | Sun 9:30 AM | Unpaused | Feb 8, 2026 |
| Recommendation Execution Monitor (daily) | `recommendation_execution_daily` | Daily 9:30 AM | Unpaused | Feb 10, 2026 |
| Recommendation Execution Monitor (weekly) | `recommendation_execution_weekly` | Sun 10:30 AM | Unpaused | Feb 8, 2026 |
| Portfolio Performance Monitor | `portfolio_performance_daily` | Daily 8:45 AM | **PAUSED** | Never |

**Note:** Agents will not run until the Airflow scheduler is started.

---

## 2. CRON JOBS (System Crontab)

| Job | Schedule (Crontab) | Description | Status |
|-----|--------------------|-------------|--------|
| Daily Workflow Report | `30 23 * * *` | 9:00 AM IST | ✅ In crontab |
| Daily Leads Report | `30 20 * * *` | 6:00 AM IST | ✅ In crontab |
| Daily Monthly Investments Report | `35 20 * * *` | 6:05 AM IST | ✅ In crontab |
| Hourly SLA Check | `0 * * * *` | Every hour | ✅ In crontab |
| Price Update (Sheets) | `0 4,7,10 * * 1-5` | 9:30 AM, 12:30 PM, 4 PM IST (weekdays) | ✅ In crontab |
| Nifty Price Update | `0 4,7,10 * * 1-5` | Same as above | ✅ In crontab |
| Start Monthly Cycle | `30 19 1 * *` | 1st of month, 1 AM IST | ✅ In crontab |
| Daily Holdings Processor | `30 16 * * *` | 2 AM IST daily | ✅ In crontab |
| Cycle Status Monitor | `30 17 * * 0` | Sun 3 AM IST | ✅ In crontab |
| Holdings Notifications | `30 16 * * *` | 2:30 AM IST daily | ✅ In crontab |

**⚠️ Timezone note:** Crontab comments say “IST converted to EDT”. If the server timezone is IST, some schedules may be incorrect. Verify cron runs match expected IST times.

---

## 3. AIRFLOW DAGs (Schedule & Pause Status)

*The daily email report (from `scripts/scheduled_jobs_status_report.py`) shows current Paused/Scheduled and Last State for all DAGs. Below is a reference; see "Currently paused DAGs" for guidance.*

| DAG ID | Schedule | Paused? | Last Run | Last State |
|--------|----------|---------|----------|------------|
| daily_alert_report | 8:00 AM IST daily | No | Feb 10 | success |
| daily_reports | Midnight UTC (6 AM IST) | **Yes** | — | — |
| daily_holdings_processor | 2 AM IST daily | **Yes** | — | — |
| cycle_status_monitor | Sun 3 AM IST | **Yes** | — | — |
| send_holdings_notifications | 2:30 AM IST daily | **Yes** | — | — |
| data_integrity_daily | 8:30 AM IST daily | No | Feb 10 | success |
| data_integrity_weekly | Sun 9:30 AM IST | No | Feb 8 | success |
| recommendation_execution_daily | 9:30 AM IST daily | No | Feb 10 | success |
| recommendation_execution_weekly | Sun 10:30 AM IST | No | Feb 8 | success |
| portfolio_performance_daily | 8:45 AM IST daily | **Yes** | — | — |
| price_updates | 10 AM, 1 PM, 4 PM IST (Mon–Fri) | No | Feb 10 | success |
| workflow_management | 9:00 AM IST daily | No | Feb 10 | success |
| start_monthly_cycle | 1st of month, 1 AM IST | No | Feb 3 | success |
| scheduled_jobs_status_report | 9:00 AM IST daily | No | Feb 10 | success |
| codebase_backup_daily | 7:45 AM IST (02:15 UTC) | — | — | — |
| database_backup_daily | 2:30 AM IST (21:00 UTC) | — | — | — |

### Paused vs failing
**"Paused" in the report means the DAG is turned off in Airflow** (scheduler will not trigger it). It is **not** failing—the job is simply disabled. To run it, unpause in the Airflow UI or run:
```bash
airflow dags unpause <dag_id>
```
Failed runs show as **Last State: Failed** in the report; paused DAGs may show no last run or an old run.

### Currently paused DAGs (as of last report)
- **Holdings (often duplicated by cron):** `daily_holdings_processor`, `send_holdings_notifications`, `weekly_holdings_refresh` — unpause only if you want Airflow to run these instead of cron.
- **Tasks:** `task_assignment_daily`, `task_auto_close_daily`, `task_reminders` — unpause if you want task assignment backfill, auto-close, and reminders to run on schedule.

To unpause the task-related DAGs (recommended if not using cron for them):
```bash
export AIRFLOW_HOME=/home/inertia/app/airflow
airflow dags unpause task_assignment_daily
airflow dags unpause task_auto_close_daily
airflow dags unpause task_reminders
```

---

## 4. FLASK APP SCHEDULER (APScheduler)

- **Location:** `scheduler.py` – `check_upcoming_investments` (daily 9 AM)
- **Status:** Not initialized in `main.py` – **not running**
- **Duplicate:** Same logic is in `workflow_management` DAG (Airflow). Cron also runs workflow-related jobs.

---

## 5. SUMMARY & ACTIONS

### Immediate Actions
1. **Start Airflow:**
   ```bash
   ./scripts/start_airflow.sh
   ```
2. **Optionally unpause DAGs** if you want them to run via Airflow:
   ```bash
   # From app directory with AIRFLOW_HOME set
   airflow dags unpause daily_reports
   airflow dags unpause daily_holdings_processor
   airflow dags unpause cycle_status_monitor
   airflow dags unpause send_holdings_notifications
   airflow dags unpause portfolio_performance_daily
   ```

### Architecture Overview
- **Cron:** Reports, SLA checks, price updates, holdings cycle (still active)
- **Airflow DAGs:** Agent runs, workflow management, some reports (requires Airflow scheduler)
- **Flask scheduler:** Not active; logic moved to Airflow

### Dual execution risk
Cron and Airflow can run the same jobs (e.g. price updates, reports). The migration doc (`CRON_TO_DAG_MIGRATION_STATUS.md`) recommends disabling cron entries for jobs that are fully handled by Airflow once DAGs are verified.
