"""
Data Updates DAG
Price updates from Google Sheets and Nifty price updates
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
    'email': ['anshul@equities4wealth.com', 'service@equities4wealth.com'],
}

def _parse_cron_result(result):
    """Parse (response, status_code) from cron_jobs and return (data, success)."""
    if isinstance(result, tuple) and len(result) == 2:
        resp, status_code = result
        body = resp.get_json() if hasattr(resp, 'get_json') else (resp if isinstance(resp, dict) else {})
    else:
        body = result if isinstance(result, dict) else {}
        status_code = 200
    data = body.get('data') or {}
    success = status_code == 200 and data.get('success', False)
    return data, success

def _is_market_close_ist() -> bool:
    import pytz
    from datetime import datetime

    return 16 <= datetime.now(pytz.timezone('Asia/Kolkata')).hour < 17


def run_price_update():
    run_app_task('price_updates')



def run_nifty_price_update_task():
    run_app_task('nifty_price_update')



# Price Update DAG — scheduler timezone is UTC (see airflow/airflow.cfg).
# 04:00 / 07:00 / 10:30 UTC = 09:30 / 12:30 / 16:00 IST (market close → historical_price).
with DAG(
    'price_updates',
    default_args=default_args,
    description='Security price updates from Google Sheets (9:30 AM, 12:30 PM, 4 PM IST weekdays). 4 PM saves closing prices.',
    schedule='30 4,7,10 * * 1-5',  # 04:30/07:30/10:30 UTC = 10:00/13:00/16:00 IST; 16:00 saves history
    start_date=datetime(2025, 1, 1),
    catchup=False,
    is_paused_upon_creation=False,  # Start unpaused so daily closing prices are updated
    tags=['data', 'prices', 'google-sheets', 'daily-closing'],
) as price_dag:
    
    price_update = PythonOperator(
        task_id='update_security_prices',
        python_callable=run_price_update,
        dag=price_dag,
    )
    
    nifty_update = PythonOperator(
        task_id='update_nifty_price',
        python_callable=run_nifty_price_update_task,
        dag=price_dag,
    )
    
    # Run price updates in parallel (no dependencies)
    price_update
    nifty_update
