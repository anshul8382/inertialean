"""
Recommendation Execution Monitor DAG
====================================
Schedules the Recommendation Execution Monitor agent to run:
- Daily checks for active workflows (incremental)
- Weekly full audit (all active workflows)
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
    'retry_delay': timedelta(minutes=10),
    'email': ['anshul@equities4wealth.com'],
}


def run_daily_execution_check():
    run_app_task('recommendation_execution_daily')



def run_weekly_full_audit():
    run_app_task('recommendation_execution_weekly')



# Daily Incremental Check DAG
with DAG(
    'recommendation_execution_daily',
    default_args=default_args,
    description='Daily recommendation execution check (active workflows)',
    schedule='0 4 * * *',  # 4:00 AM UTC (9:30 AM IST)
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['agents', 'recommendation_execution', 'daily'],
) as daily_dag:
    
    daily_check = PythonOperator(
        task_id='run_daily_check',
        python_callable=run_daily_execution_check,
        dag=daily_dag,
    )


# Weekly Full Audit DAG
with DAG(
    'recommendation_execution_weekly',
    default_args=default_args,
    description='Weekly full recommendation execution audit (all active workflows)',
    schedule='0 5 * * 0',  # 5:00 AM UTC Sunday (10:30 AM IST)
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['agents', 'recommendation_execution', 'weekly'],
) as weekly_dag:
    
    weekly_audit = PythonOperator(
        task_id='run_full_audit',
        python_callable=run_weekly_full_audit,
        dag=weekly_dag,
    )
