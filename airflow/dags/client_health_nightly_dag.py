"""
Client Health Nightly Cycle DAG
===============================
One off-peak job:

  collect observations → JSON pack → rank → LLM interpret → output wrapper
  → interpretations JSON + consolidated digest drafts

Does not create Alerts (freeze) and does not send email unless later enabled.
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


def run_client_health_nightly_cycle():
    run_app_task('client_health_nightly')



with DAG(
    "client_health_nightly",
    default_args=default_args,
    description=(
        "Nightly client health: observations JSON → LLM interpretation → "
        "output wrapper (card pack + consolidated digest drafts)"
    ),
    schedule="0 21 * * *",  # 21:00 UTC ≈ 02:30 IST — after agreement check
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["agents", "client_health", "nightly", "llm"],
) as dag:

    nightly = PythonOperator(
        task_id="run_client_health_nightly",
        python_callable=run_client_health_nightly_cycle,
        dag=dag,
    )
