"""
Price sheet suppress — overnight cycles
=======================================
Paginated Google Sheets verification of price-accuracy findings.
Writes firm-wide suppress JSON under var/price_accuracy/.

Runs every 20 minutes in the evening UTC window so batches finish overnight
without one giant Sheets call. DI nightly should run after this window.

No LLM. Does not mutate cashflows/trades; may ack SPIKE rows in DB.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from inertia_dag_utils import run_app_task

default_args = {
    "owner": "inertia_admin",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email": ["anshul@equities4wealth.com"],
}


def run_price_sheet_suppress_cycle():
    run_app_task('price_sheet_suppress_cycles')



with DAG(
    "price_sheet_suppress_cycles",
    default_args=default_args,
    description=(
        "Paginated sheet-vs-DB price verify → suppress JSON + symbol leftovers queue"
    ),
    # Every 20 min, 18:00–23:40 UTC (≈ 23:30–05:10 IST) — overnight cycles with gaps
    schedule="*/20 18-23 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["agents", "price_accuracy", "nightly", "sheets"],
) as dag:

    cycle = PythonOperator(
        task_id="run_price_sheet_suppress_cycle",
        python_callable=run_price_sheet_suppress_cycle,
        dag=dag,
    )
