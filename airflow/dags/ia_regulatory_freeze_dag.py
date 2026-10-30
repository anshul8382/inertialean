"""
Auto-freeze completed IA statement periods (prior quarter, half, and FY year).
Schedule: 02:00 IST on the 1st of each month (20:30 UTC previous day).
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


def run_ia_regulatory_freeze():
    run_app_task("ia_regulatory_freeze")


with DAG(
    "ia_regulatory_freeze",
    default_args=default_args,
    description="Freeze completed quarterly/half-yearly/yearly IA regulatory statements",
    schedule="30 20 1 * *",  # 20:30 UTC on 1st ≈ 02:00 IST on 1st
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["regulatory", "monthly", "ia_statement"],
) as dag:
    PythonOperator(
        task_id="freeze_due_ia_periods",
        python_callable=run_ia_regulatory_freeze,
        dag=dag,
    )
