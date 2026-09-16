"""
BNI TY Notes — weekly email for RECOS_GENERATED and/or recommendation sent_at (per client, BNI lookup).
Schedule: Wednesday 4:00 PM IST (10:30 UTC).
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
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
    "email": ["anshul@equities4wealth.com"],
}


def run_bni_ty_notes():
    run_app_task('bni_ty_notes_weekly')



with DAG(
    "bni_ty_notes_weekly",
    default_args=default_args,
    description="Weekly BNI TY Notes email (RECOS_GENERATED, Wed 4 PM IST)",
    schedule="30 10 * * 3",  # Wed 10:30 UTC = 4:00 PM IST
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["reports", "weekly", "bni"],
) as dag:
    PythonOperator(
        task_id="bni_ty_notes_email",
        python_callable=run_bni_ty_notes,
        dag=dag,
    )
