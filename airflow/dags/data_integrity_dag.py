"""
Data Integrity Manager DAG
==========================
Schedules the Data Integrity Manager agent to run:
- Daily incremental checks (last 7 days)
- Weekly full audits (all clients, all history)
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


def run_daily_integrity_check():
    run_app_task('data_integrity_daily')



def run_weekly_full_audit():
    run_app_task('data_integrity_weekly')



# Daily Incremental Check DAG
with DAG(
    'data_integrity_daily',
    default_args=default_args,
    description='Daily incremental data integrity check (last 7 days)',
    schedule='30 23 * * *',  # 23:30 UTC ≈ 05:00 IST — before daily_reports (05:30 IST)
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['agents', 'data_integrity', 'daily'],
) as daily_dag:
    
    daily_check = PythonOperator(
        task_id='run_incremental_check',
        python_callable=run_daily_integrity_check,
        dag=daily_dag,
    )


# Weekly Full Audit DAG
with DAG(
    'data_integrity_weekly',
    default_args=default_args,
    description='Weekly full data integrity audit (all clients, all history)',
    schedule='0 4 * * 0',  # 4:00 AM UTC Sunday (9:30 AM IST)
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['agents', 'data_integrity', 'weekly'],
) as weekly_dag:
    
    weekly_audit = PythonOperator(
        task_id='run_full_audit',
        python_callable=run_weekly_full_audit,
        dag=weekly_dag,
    )
