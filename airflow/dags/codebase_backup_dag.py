"""
Weekly local tarball of the application codebase + retention prune (Sunday).
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
import os
import subprocess
import sys
from inertia_dag_utils import app_python, app_root

_APP_ROOT = app_root()

default_args = {
    "owner": "inertia_admin",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "email": ["anshul@equities4wealth.com"],
}


def run_codebase_backup_job():
    script = os.path.join(_APP_ROOT, "scripts", "codebase_backup.py")
    env = os.environ.copy()
    if "CODEBASE_BACKUP_ROOT" not in env:
        env.setdefault(
            "CODEBASE_BACKUP_ROOT",
            os.path.join(os.path.dirname(_APP_ROOT), "inertia_codebase_backups"),
        )
    env.setdefault("CODEBASE_BACKUP_SOURCE", _APP_ROOT)
    proc = subprocess.run(
        [app_python(), script],
        cwd=_APP_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=7200,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "codebase_backup.py failed")
    print(proc.stdout)


with DAG(
    "codebase_backup_daily",
    default_args=default_args,
    description="Weekly local tarball of application codebase + retention prune (Sunday)",
    schedule="15 2 * * 0",  # Sunday 02:15 UTC ≈ 07:45 IST (weekly; was daily)
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["backup", "codebase", "weekly"],
) as dag:
    PythonOperator(
        task_id="run_codebase_backup",
        python_callable=run_codebase_backup_job,
    )
