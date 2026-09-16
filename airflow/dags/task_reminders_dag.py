"""
Task Reminders DAG
Processes due task reminders - creates in-app alerts and sends emails
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import sys
import os

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


def process_task_reminders():
    run_app_task('task_reminders')



with DAG(
    'task_reminders',
    default_args=default_args,
    description='Process task reminders - in-app alerts and emails',
    schedule='*/15 * * * *',  # Every 15 minutes
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['tasks', 'reminders', 'daily_ops'],
) as dag:

    reminder_task = PythonOperator(
        task_id='process_task_reminders',
        python_callable=process_task_reminders,
        dag=dag,
    )
