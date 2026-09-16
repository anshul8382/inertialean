"""
Daily Reports DAG
Generates and sends daily reports: workflow status, leads, and monthly investments.

Runs scripts with the *app* venv (not airflow_venv), so Flask/Mail/DB imports work.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
import os
import subprocess
import sys

_APP_ROOT = os.environ.get("INERTIA_APP_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_VENV_PYTHON = os.environ.get("INERTIA_VENV_PYTHON") or os.path.join(
    _APP_ROOT, "venv", "bin", "python"
)

default_args = {
    "owner": "inertia_admin",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email": ["anshul@equities4wealth.com"],
}


def _run_app_script(script_name: str) -> None:
    script_path = os.path.join(_APP_ROOT, script_name)
    if not os.path.isfile(_VENV_PYTHON):
        raise FileNotFoundError(f"App venv python not found: {_VENV_PYTHON}")
    if not os.path.isfile(script_path):
        raise FileNotFoundError(f"Report script not found: {script_path}")
    env = os.environ.copy()
    env.setdefault("INERTIA_APP_DIR", _APP_ROOT)
    # Ensure EMAIL_SOURCE_TAG / MAIL_* from .env are visible if systemd did not load .env
    dotenv = os.path.join(_APP_ROOT, ".env")
    if os.path.isfile(dotenv):
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv, override=False)
            for key in (
                "EMAIL_SOURCE_TAG",
                "MAIL_SERVER",
                "MAIL_PORT",
                "MAIL_USERNAME",
                "MAIL_PASSWORD",
                "MAIL_DEFAULT_SENDER",
                "MAIL_USE_TLS",
                "DATABASE_URL",
                "SQLALCHEMY_DATABASE_URI",
            ):
                val = os.environ.get(key)
                if val:
                    env[key] = val
        except Exception:
            pass
    result = subprocess.run(
        [_VENV_PYTHON, script_path],
        cwd=_APP_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{script_name} failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}"
        )


def run_daily_workflow_report():
    _run_app_script("daily_workflow_report.py")


def run_daily_leads_report():
    _run_app_script("daily_leads_report.py")


def run_daily_monthly_investments_report():
    _run_app_script("daily_monthly_investments_report.py")


with DAG(
    "daily_reports",
    default_args=default_args,
    description="Daily reports: workflow, leads, and monthly investments",
    schedule='0 0 * * *',  # 00:00 UTC ≈ 05:30 IST (after data_integrity_daily ~05:00 IST)
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["reports", "daily"],
) as dag:
    PythonOperator(
        task_id="daily_workflow_report",
        python_callable=run_daily_workflow_report,
    )
    PythonOperator(
        task_id="daily_leads_report",
        python_callable=run_daily_leads_report,
    )
    PythonOperator(
        task_id="daily_monthly_investments_report",
        python_callable=run_daily_monthly_investments_report,
    )
