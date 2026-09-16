# Price Updates DAG - Daily Closing Prices Fix

## Issue
Daily closing prices were not saved to `historical_price` when the Airflow scheduler was stopped or the DAG was paused.

## Schedule (UTC scheduler — `airflow/airflow.cfg` `default_timezone = utc`)
`price_updates` cron: `0 4,0 7,30 10 * * 1-5` → **09:30, 12:30, 16:00 IST** weekdays. The **16:00 IST** run persists `historical_price`.

## Operations
- Start: `bash scripts/start_airflow.sh`
- Stop: `bash scripts/stop_airflow.sh`
- Unpause: `python scripts/unpause_price_updates_dag.py` or Airflow UI
- Boot: `@reboot` entry runs `start_airflow.sh` (see crontab)

## Verify
After 4 PM IST: `airflow dags list` shows `price_updates` unpaused; task logs show `historical_saved_count` > 0.
