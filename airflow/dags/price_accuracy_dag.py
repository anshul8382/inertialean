"""
Weekly Airflow DAG: price accuracy scan (same logic as Hub → System → Price data accuracy → Run scan).

Requires DB tables from migrations/add_price_accuracy_tables.py (already applied if you ran the migration).

Env (optional):
  PRICE_ACCURACY_SCAN_THRESHOLD_PCT — default 10 (percent jump between consecutive historical rows).
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from inertia_dag_utils import run_app_task

default_args = {
    "owner": "inertia_admin",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=15),
    "email": ["anshul@equities4wealth.com", "service@equities4wealth.com"],
}


def _run_scan():
    run_app_task('price_accuracy_weekly')



with DAG(
    "price_accuracy_weekly",
    default_args=default_args,
    description="Weekly: historical price spikes, missing trade dates, zero closes (traded securities)",
    # Monday 09:30 IST → 04:00 UTC (scheduler timezone is usually UTC on the host)
    schedule="0 4 * * 1",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    is_paused_upon_creation=False,
    tags=["data", "prices", "integrity", "weekly"],
    doc_md="""
### Price data accuracy (weekly)

Runs `run_price_accuracy_scan()` inside Flask app context — identical to the Hub manual run.

- **Threshold**: `PRICE_ACCURACY_SCAN_THRESHOLD_PCT` env (default `10`).
- **Schedule**: Every **Monday 09:30 IST** (`0 4 * * 1` UTC); change in Airflow UI if your scheduler timezone differs.
- **Results**: Latest snapshot in DB; review at `/hub/system/price-accuracy`.
""",
) as dag:
    PythonOperator(
        task_id="price_accuracy_scan",
        python_callable=_run_scan,
        dag=dag,
    )
