"""
Daily: refresh tax optimiser T+N follow-up draft HTML with prevailing prices.
"""
from datetime import datetime, timedelta
import os

from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

_APP_ROOT = os.environ.get("INERTIA_APP_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_VENV_PYTHON = os.environ.get("INERTIA_VENV_PYTHON") or os.path.join(
    _APP_ROOT, "venv", "bin", "python"
)

default_args = {
    "owner": "inertia",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="tax_optimiser_followup_refresh",
    default_args=default_args,
    description="Refresh tax optimiser buy-back follow-up draft prices",
    schedule="0 7 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["tax", "ops"],
) as dag:
    BashOperator(
        task_id="run_followup_refresh",
        bash_command=f"cd {_APP_ROOT} && {_VENV_PYTHON} scripts/tax_optimiser_followup_refresh.py",
    )
