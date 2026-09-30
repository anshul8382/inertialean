"""
Review cashflow projection — weekly Sunday email (Anshul only).
Schedule: Sunday 08:00 IST (02:30 UTC).
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
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "email": ["anshul@equities4wealth.com"],
}


def run_review_billing_projection_weekly():
    run_app_task("review_billing_projection_weekly")


with DAG(
    "review_billing_projection_weekly",
    default_args=default_args,
    description="Sunday review cashflow projection email (12 months, Anshul only)",
    schedule="30 2 * * 0",  # Sun 02:30 UTC ≈ 08:00 IST
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["reports", "weekly", "reviews", "billing"],
) as dag:
    PythonOperator(
        task_id="review_billing_projection_email",
        python_callable=run_review_billing_projection_weekly,
        dag=dag,
    )
