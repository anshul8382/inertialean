"""
Task Auto-Close DAG
===================
Runs daily at 5 PM IST to close OpsTasks whose underlying work is done.
Examples:
- Task about sending recommendations → close when recommendations already sent (workflow at NOTIFY+)
- Task linked to ReviewWorkflow → close when review workflow is closed
- Task linked to resolved DataIntegrityIssue → close
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import sys
import os

# Add app directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from inertia_dag_utils import run_app_task

default_args = {
    'owner': 'inertia_admin',
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
    'email': ['anshul@equities4wealth.com'],
}


def run_task_auto_close():
    run_app_task('task_auto_close_daily')



with DAG(
    'task_auto_close_daily',
    default_args=default_args,
    description='Daily 5 PM IST: close OpsTasks whose work is done (recos sent, review closed)',
    schedule='30 11 * * *',  # 11:30 UTC = 5:00 PM IST
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['tasks', 'auto_close', 'daily'],
) as dag:

    auto_close = PythonOperator(
        task_id='close_completed_tasks',
        python_callable=run_task_auto_close,
        dag=dag,
    )
