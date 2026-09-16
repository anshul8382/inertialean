"""
Workflow Management DAG
Checks for upcoming investments and creates workflows
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
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
    'email': ['anshul@equities4wealth.com'],
}

def run_upcoming_workflow_job():
    run_app_task('workflow_upcoming')



def run_past_due_workflow_job():
    run_app_task('workflow_past_due')



with DAG(
    'workflow_management',
    default_args=default_args,
    description='Check upcoming and past-due investments, create workflows',
    schedule='0 3 * * *',  # 9:00 AM IST (3:30 AM UTC)
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['workflow', 'investments', 'daily'],
) as dag:
    
    upcoming_check = PythonOperator(
        task_id='check_upcoming_investments',
        python_callable=run_upcoming_workflow_job,
        dag=dag,
    )
    
    past_due_check = PythonOperator(
        task_id='check_past_due_investments',
        python_callable=run_past_due_workflow_job,
        dag=dag,
    )
    
    # Run checks sequentially
    upcoming_check >> past_due_check
