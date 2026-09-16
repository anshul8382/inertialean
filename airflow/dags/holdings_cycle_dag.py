"""
Holdings Cycle Management DAG
Manages monthly holdings cycle: starting cycles, weekly Sunday processing, status monitoring, and Monday notifications
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import sys
import os
from inertia_dag_utils import app_python, app_root

# App root: .../airflow/dags/this_file → repo root (works on BigRock and Lean VPS)
_APP_ROOT = app_root()
sys.path.insert(0, _APP_ROOT)

default_args = {
    'owner': 'inertia_admin',
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
    'email': ['anshul@equities4wealth.com'],
}


def _run_script(rel_path: str, label: str) -> None:
    import subprocess

    script_path = os.path.join(_APP_ROOT, *rel_path.split("/"))
    result = subprocess.run(
        [app_python(), script_path],
        capture_output=True,
        text=True,
        cwd=_APP_ROOT,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise Exception(f"{label} failed: {result.stderr}")


def run_start_monthly_cycle():
    """Start a new monthly cycle for all clients"""
    _run_script("scripts/start_monthly_cycle.py", "start_monthly_cycle")


def run_weekly_holdings_processor():
    """Process all clients in the current monthly_holdings_cycle (weekly Sunday run)."""
    _run_script("scripts/daily_holdings_processor.py", "daily_holdings_processor")


def run_cycle_status_monitor():
    """Generate cycle status reports"""
    _run_script("scripts/cycle_status_monitor.py", "cycle_status_monitor")


def run_weekly_holdings_refresh():
    """Refresh all client holdings using forward calculation (full reconciliation)"""
    _run_script("refresh_all_holdings.py", "refresh_all_holdings")


def run_send_holdings_notifications():
    """Send weekly holdings processing reports (Monday, after Sunday run)"""
    _run_script("scripts/send_holdings_notifications.py", "send_holdings_notifications")

# Monthly Cycle Start DAG - Runs on 1st of every month at 1 AM IST (7:30 PM EDT previous day = 19:30 UTC previous day)
with DAG(
    'start_monthly_cycle',
    default_args=default_args,
    description='Start new monthly holdings cycle on 1st of month',
    schedule='30 19 1 * *',  # 1st of month at 7:30 PM EDT = 1 AM IST next day
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['holdings', 'monthly', 'cycle'],
) as dag_start:
    PythonOperator(
        task_id='start_monthly_cycle',
        python_callable=run_start_monthly_cycle,
    )

with DAG(
    'daily_holdings_processor',
    default_args=default_args,
    description='Weekly holdings processing (Sunday)',
    schedule='30 20 * * 6',  # Saturday 20:30 UTC = Sunday 2:00 AM IST
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['holdings', 'weekly'],
) as dag_weekly_proc:
    PythonOperator(
        task_id='process_holdings',
        python_callable=run_weekly_holdings_processor,
    )

with DAG(
    'cycle_status_monitor',
    default_args=default_args,
    description='Cycle status reports',
    schedule='30 17 * * 0',  # Sunday at 5:30 PM EDT = 3 AM IST next day
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['holdings', 'monitor'],
) as dag_status:
    PythonOperator(
        task_id='cycle_status',
        python_callable=run_cycle_status_monitor,
    )

with DAG(
    'send_holdings_notifications',
    default_args=default_args,
    description='Holdings notification emails',
    schedule='30 21 * * 0',  # Sunday 21:00 UTC = Monday 2:30 AM IST
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['holdings', 'email'],
) as dag_notify:
    PythonOperator(
        task_id='send_notifications',
        python_callable=run_send_holdings_notifications,
    )

with DAG(
    'weekly_holdings_refresh',
    default_args=default_args,
    description='Full forward holdings reconciliation',
    schedule='30 18 * * 6',  # Saturday 6:30 PM UTC = Sunday midnight IST
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['holdings', 'refresh'],
) as dag_refresh:
    PythonOperator(
        task_id='refresh_holdings',
        python_callable=run_weekly_holdings_refresh,
    )
