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
    # Lean default: writable under /opt (create once: sudo mkdir + chown anshul)
    env.setdefault("CODEBASE_BACKUP_ROOT", "/opt/inertia_codebase_backups")
    env.setdefault("CODEBASE_BACKUP_SOURCE", _APP_ROOT)
    # Pass through Drive settings from systemd EnvironmentFile / .env if present
    for key in (
        "CODEBASE_BACKUP_DRIVE_FOLDER_ID",
        "CODEBASE_BACKUP_DRIVE_ENABLED",
        "BACKUP_DRIVE_CODE_LATEST_NAME",
        "GOOGLE_SERVICE_ACCOUNT_FILE",
    ):
        if key not in env:
            # already in env from EnvironmentFile / .env when set
            pass
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
