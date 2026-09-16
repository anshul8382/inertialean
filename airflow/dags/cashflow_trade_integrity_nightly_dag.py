"""
Cashflow ↔ trade integrity nightly DAG
======================================
Scans active clients (lifetime net + series gate), writes
var/cashflow_trade_integrity/nightly_snapshot.json for client-details badges.

Suggest-only — does not mutate cashflows or trades.
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
    "retry_delay": timedelta(minutes=15),
    "email": ["anshul@equities4wealth.com"],
}


def run_cashflow_trade_integrity_nightly():
    run_app_task('cashflow_trade_integrity_nightly')



with DAG(
    "cashflow_trade_integrity_nightly",
    default_args=default_args,
    description=(
        "Nightly cashflow↔trade integrity scan (net + series) → badge snapshot JSON"
    ),
    schedule="45 20 * * *",  # 20:45 UTC ≈ 02:15 IST — before client_health_nightly
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["agents", "cashflow", "nightly", "integrity"],
) as dag:

    nightly = PythonOperator(
        task_id="run_cashflow_trade_integrity_nightly",
        python_callable=run_cashflow_trade_integrity_nightly,
        dag=dag,
    )
