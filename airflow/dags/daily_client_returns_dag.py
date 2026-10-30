"""
Daily client returns digest — Anshul only (HTML + Excel).
Schedule: 07:00 IST (01:30 UTC) every day.
"""
from datetime import datetime, timedelta
import os
import sys

from airflow import DAG
from airflow.operators.python import PythonOperator

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


def run_daily_client_returns():
    run_app_task("daily_client_returns")


with DAG(
    "daily_client_returns",
    default_args=default_args,
    description="Daily client returns matrix email (Anshul only, HTML+Excel)",
    schedule="30 1 * * *",  # 01:30 UTC ≈ 07:00 IST
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["reports", "daily", "returns"],
) as dag:
    PythonOperator(
        task_id="daily_client_returns_email",
        python_callable=run_daily_client_returns,
        dag=dag,
    )
