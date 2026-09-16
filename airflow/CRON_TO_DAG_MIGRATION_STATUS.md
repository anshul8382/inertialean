# Cron Jobs to Airflow DAG Migration Status

## Summary
- **Total Cron Jobs in Crontab**: 10
- **Migrated to Airflow**: 10 ✅ **ALL MIGRATED**
- **Missing from Airflow**: 0
- **DAGs Paused**: 5 (not running - need to unpause)
- **DAGs Active**: 7 (3 agent DAGs + 4 new holdings cycle DAGs)

---

## ✅ Migrated to Airflow (but some are PAUSED)

### 1. Daily Reports (daily_reports DAG)
**Status**: ⚠️ **PAUSED** - Not running  
**Cron Schedule**: 
- daily_workflow_report: `30 23 * * *` (11:30 PM EDT = 9:00 AM IST)
- daily_leads_report: `30 20 * * *` (8:30 PM EDT = 6:00 AM IST)
- daily_monthly_investments_report: `35 20 * * *` (8:35 PM EDT = 6:05 AM IST)

**Airflow Schedule**: `0 0 * * *` (Midnight UTC = 6:00 AM IST)  
**Issue**: All 3 reports run at same time in Airflow, but cron has different times

**Coverage**: ✅ All 3 reports covered

### 2. Price Updates (price_updates DAG)
**Status**: ⚠️ Was PAUSED - Run `python scripts/unpause_price_updates_dag.py` to unpause  
**Cron Schedule**: 3 times daily on weekdays  
**Airflow Schedule**: `30 4,7,10 * * 1-5` (10 AM, 1 PM, **4 PM IST** - market close) ✅ **FIXED**

**Coverage**: ✅ price_update_sheets + nifty_price_update
**Critical**: 4 PM IST run saves daily closing prices to `historical_price` table

### 3. Hourly SLA Check (hourly_sla_check DAG)
**Status**: ⚠️ **PAUSED** - Not running  
**Cron Schedule**: `0 * * * *` (Every hour)  
**Airflow Schedule**: `0 * * * *` ✅ **CORRECT**

**Coverage**: ✅ hourly_sla_check

### 4. Workflow Management (workflow_management DAG)
**Status**: ⚠️ **PAUSED** - Not running  
**Cron Schedule**: Not in crontab (new feature)  
**Airflow Schedule**: `0 3 * * *` (9:00 AM IST)

**Coverage**: ✅ check_upcoming_investments + check_past_due_investments

### 5. Daily Alert Report (daily_alert_report DAG)
**Status**: ⚠️ **PAUSED** - Not running  
**Cron Schedule**: Not in crontab (may be manual)  
**Airflow Schedule**: `0 2 * * *` (8:00 AM IST)

**Coverage**: ✅ daily_alert_report

---

## ✅ Migrated to Airflow (Newly Created)

### 1. start_monthly_cycle
**Cron Schedule**: `30 19 1 * *` (1st of month at 7:30 PM EDT = 1 AM IST)  
**Airflow Schedule**: `30 19 1 * *` ✅ **CORRECT**  
**Script**: `scripts/start_monthly_cycle.py`  
**Status**: ✅ **CREATED IN AIRFLOW** - DAG: `start_monthly_cycle`

### 2. daily_holdings_processor
**Cron Schedule**: `30 16 * * *` (4:30 PM EDT = 2 AM IST daily)  
**Airflow Schedule**: `30 16 * * *` ✅ **CORRECT**  
**Script**: `scripts/daily_holdings_processor.py`  
**Status**: ✅ **CREATED IN AIRFLOW** - DAG: `daily_holdings_processor`

### 3. cycle_status_monitor
**Cron Schedule**: `30 17 * * 0` (5:30 PM EDT Saturday = 3 AM IST Sunday)  
**Airflow Schedule**: `30 17 * * 0` ✅ **CORRECT**  
**Script**: `scripts/cycle_status_monitor.py`  
**Status**: ✅ **CREATED IN AIRFLOW** - DAG: `cycle_status_monitor`

### 4. send_holdings_notifications
**Cron Schedule**: `30 16 * * *` (4:30 PM EDT = 2:30 AM IST daily)  
**Airflow Schedule**: `30 16 * * *` ✅ **CORRECT**  
**Script**: `scripts/send_holdings_notifications.py`  
**Status**: ✅ **CREATED IN AIRFLOW** - DAG: `send_holdings_notifications`

---

## 🔄 Active DAGs (Agent DAGs)

### 1. data_integrity_daily
**Status**: ✅ **ACTIVE** (Not paused)  
**Schedule**: Daily

### 2. data_integrity_weekly
**Status**: ✅ **ACTIVE** (Not paused)  
**Schedule**: Weekly

### 3. recommendation_execution_daily
**Status**: ✅ **ACTIVE** (Not paused)  
**Schedule**: Daily

### 4. task_auto_close_daily
**Status**: ✅ **CREATED** (new DAG)  
**Schedule**: `30 11 * * *` (5:00 PM IST daily)  
**Purpose**: Close OpsTasks when underlying work is done (e.g. recommendations sent → close "send recos" task; review closed → close review task)  
**Script**: `services/task_auto_close_service.close_completed_ops_tasks()`

---

## ⚠️ Critical Issues

1. **All migrated DAGs are PAUSED** - They won't run until unpaused
2. **4 cron jobs still missing** - Monthly holdings cycle system not migrated
3. **Schedule mismatch** - daily_reports DAG runs all reports at same time (should be different times)
4. **Dual execution risk** - If cron jobs are still active AND DAGs are unpaused, jobs will run twice

---

## 📋 Recommendations

1. **Unpause DAGs** if you want to use Airflow instead of cron
2. **Create missing DAGs** for holdings cycle scripts
3. **Fix daily_reports schedule** to match cron times
4. **Disable cron jobs** once DAGs are verified working
5. **Test thoroughly** before disabling cron jobs

---

## Next Steps

1. Create `holdings_cycle_dag.py` for the 4 missing scripts
2. Fix `daily_reports_dag.py` schedule to run reports at correct times
3. Unpause DAGs once verified
4. Remove cron jobs from crontab after testing
