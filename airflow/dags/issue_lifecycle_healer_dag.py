"""
Issue Lifecycle Healer DAG
==========================
Runs a daily resolver pass that closes issue/task/alert chains when root cause is cleared.
This DAG does not create new issues; it only resolves stale open lifecycle rows.
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
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "email": ["anshul@equities4wealth.com"],
}


def run_issue_lifecycle_healer():
    run_app_task('issue_lifecycle_healer_daily')



with DAG(
    "issue_lifecycle_healer_daily",
    default_args=default_args,
    description="Daily resolver for cleared issue/task/alert chains (incl. DUPLICATES)",
    schedule="15 12 * * *",  # 12:15 UTC (~5:45 PM IST)
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["issues", "healer", "daily"],
) as dag:
    healer = PythonOperator(
        task_id="run_issue_lifecycle_healer",
        python_callable=run_issue_lifecycle_healer,
        dag=dag,
    )
