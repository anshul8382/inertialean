"""
Portfolio Performance Monitor DAG
=================================
Schedules the Portfolio Performance Monitor agent to run monthly:
- Compare each client's portfolio/equity XIRR with benchmark; open issues when underperforming.
- Monthly cadence avoids daily noise when resolved issues would re-open while math is unchanged;
  a new run still flags persistent underperformance the following month.
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
    'retries': 2,
    'retry_delay': timedelta(minutes=10),
    'email': ['anshul@equities4wealth.com'],
}


def run_portfolio_performance_scan():
    run_app_task('portfolio_performance_monthly')



with DAG(
    'portfolio_performance_monthly',
    default_args=default_args,
    description='Monthly portfolio vs benchmark scan (underperformance reporting)',
    schedule='15 3 1 * *',  # 1st of month 03:15 UTC (~8:45 AM IST)
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=['agents', 'portfolio_performance', 'monthly'],
) as dag:

    scan = PythonOperator(
        task_id='run_performance_scan',
        python_callable=run_portfolio_performance_scan,
        dag=dag,
    )
