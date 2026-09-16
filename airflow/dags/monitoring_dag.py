"""
Monitoring DAG
Hourly SLA checks and daily alert reports
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

def run_hourly_sla_check():
    run_app_task('hourly_sla_check')



def run_daily_alert_report():
    run_app_task('daily_alert_report')


def run_review_workflow_daily_report():
    run_app_task('review_workflow_daily_report')



# SLA checks (name is historical — not hourly). Twice daily ≈ 05:30 + 15:30 IST.
with DAG(
    'hourly_sla_check',
    default_args=default_args,
    description='SLA checks twice daily (~05:30 and ~15:30 IST): workflows, recs, meetings, invoices, reviews, tickets',
    schedule='0 0,10 * * *',  # 00:00 + 10:00 UTC ≈ 05:30 + 15:30 IST
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['monitoring', 'sla', 'daily'],
) as sla_dag:
    
    sla_check = PythonOperator(
        task_id='check_sla_violations',
        python_callable=run_hourly_sla_check,
        dag=sla_dag,
    )

# Daily alert email: per-advisor + consolidated manager/admin (~05:30 IST).
with DAG(
    'daily_alert_report',
    default_args=default_args,
    description='Per-advisor + consolidated daily alert emails (~05:30 IST)',
    schedule='0 0 * * *',  # 00:00 UTC ≈ 05:30 IST — same window as daily_reports
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['monitoring', 'alerts', 'daily'],
) as alert_dag:
    
    alert_report = PythonOperator(
        task_id='generate_alert_report',
        python_callable=run_daily_alert_report,
        dag=alert_dag,
    )

# Open ReviewWorkflows: per client-advisor + consolidated (~05:30 IST).
with DAG(
    'review_workflow_daily_report',
    default_args=default_args,
    description='Per-advisor + consolidated open client-review emails (~05:30 IST)',
    schedule='0 0 * * *',
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['monitoring', 'reviews', 'daily'],
) as review_report_dag:

    PythonOperator(
        task_id='generate_review_workflow_report',
        python_callable=run_review_workflow_daily_report,
        dag=review_report_dag,
    )
