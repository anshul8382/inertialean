"""
Daily report DAG: status of all Airflow scheduled jobs.
Runs once daily and emails a summary of DAG schedule/run status.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import sys
import os
from inertia_dag_utils import app_python, app_root

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

default_args = {
    'owner': 'inertia_admin',
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
    'email': ['anshul@equities4wealth.com'],
}


def run_scheduled_jobs_status_report():
    """Generate and email the scheduled jobs status report."""
    import subprocess

    root = app_root()
    script_path = os.path.join(root, "scripts", "scheduled_jobs_status_report.py")
    result = subprocess.run(
        [app_python(), script_path],
        capture_output=True,
        text=True,
        cwd=root,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"scheduled_jobs_status_report failed: {result.stderr or result.stdout}")


with DAG(
    'scheduled_jobs_status_report',
    default_args=default_args,
    description='Daily report: status of all scheduled Airflow jobs (DAGs)',
    schedule='30 3 * * *',  # 9:00 AM IST (03:30 UTC) daily
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['report', 'daily', 'scheduled-jobs', 'monitoring'],
) as dag:
    report_task = PythonOperator(
        task_id='generate_and_send_report',
        python_callable=run_scheduled_jobs_status_report,
        dag=dag,
    )
