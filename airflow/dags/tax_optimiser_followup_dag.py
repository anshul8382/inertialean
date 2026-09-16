"""
Daily: refresh tax optimiser T+N follow-up draft HTML with prevailing prices.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from inertia_dag_utils import run_app_script

default_args = {
    "owner": "inertia",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def _run_followup_refresh():
    run_app_script("scripts/tax_optimiser_followup_refresh.py")


with DAG(
    dag_id="tax_optimiser_followup_refresh",
    default_args=default_args,
    description="Refresh tax optimiser buy-back follow-up draft prices",
    schedule="0 7 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["tax", "ops"],
) as dag:
    PythonOperator(
        task_id="run_followup_refresh",
        python_callable=_run_followup_refresh,
    )
