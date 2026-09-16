"""
Task Assignment DAG
===================
Runs TaskAssignmentAgent backfill to assign open DataIntegrityIssues
that have no assignee based on TaskAssignmentRule.
Schedule: Daily after other agents (e.g. recommendation execution, data integrity).
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


def run_task_assignment_backfill():
    run_app_task('task_assignment_daily')



with DAG(
    'task_assignment_daily',
    default_args=default_args,
    description='Daily task assignment backfill (assign unassigned issues per rules)',
    schedule='30 5 * * *',  # 5:30 AM UTC (11:00 AM IST) - after rec execution (4 AM) and data integrity
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['agents', 'task_assignment', 'daily'],
) as dag:

    backfill = PythonOperator(
        task_id='run_backfill',
        python_callable=run_task_assignment_backfill,
        dag=dag,
    )
