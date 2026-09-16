"""
Client Data Integrity nightly DAG
=================================
Rolls up G8 + open DI issues + prices, builds **deterministic playbook**
attention analysis, writes var/client_data_integrity/nightly_snapshot.json.

LLM is not used here (too slow on CPU overnight). Interactive guided chat
on the DI page still calls local Ollama in real time when the user asks.
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


def run_client_data_integrity_nightly_cycle():
    run_app_task('client_data_integrity_nightly')



with DAG(
    "client_data_integrity_nightly",
    default_args=default_args,
    description=(
        "Nightly DI roll-up + playbook attention (no LLM) → snapshot for advisors"
    ),
    schedule="30 15 * * *",  # 15:30 UTC ≈ 21:00 IST — start of overnight window
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["agents", "data_integrity", "nightly"],
) as dag:

    nightly = PythonOperator(
        task_id="run_client_data_integrity_nightly",
        python_callable=run_client_data_integrity_nightly_cycle,
        dag=dag,
    )
