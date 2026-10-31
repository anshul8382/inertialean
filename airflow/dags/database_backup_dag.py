"""
Daily MySQL backup (mysqldump + gzip + retention).
Uses scripts/daily_db_backup.py (Config + .env for credentials).

Must run under the *app* venv (google-api-python-client for Drive upload),
not airflow_venv — same pattern as codebase_backup_dag.
"""
from datetime import datetime, timedelta

import os
import subprocess

from airflow import DAG
from airflow.operators.python import PythonOperator
from inertia_dag_utils import app_python, app_root

_APP_ROOT = app_root()

default_args = {
    "owner": "inertia_admin",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=15),
    "email": ["anshul@equities4wealth.com"],
}


def run_database_backup_job():
    script = os.path.join(_APP_ROOT, "scripts", "daily_db_backup.py")
    env = os.environ.copy()
    env.setdefault("BACKUP_DIR", os.path.join(_APP_ROOT, "backups"))
    # Pass through Drive settings from systemd EnvironmentFile / .env if present
    for key in (
        "CODEBASE_BACKUP_DRIVE_FOLDER_ID",
        "CODEBASE_BACKUP_DRIVE_ENABLED",
        "BACKUP_DRIVE_LATEST_NAME",
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "BACKUP_RETENTION_DAYS",
    ):
        if key not in env:
            pass
    proc = subprocess.run(
        [app_python(), script, "--app-root", _APP_ROOT],
        cwd=_APP_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=7200,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(out.strip() or "daily_db_backup.py failed")
    print(proc.stdout or "database backup ok")


with DAG(
    "database_backup_daily",
    default_args=default_args,
    description="Daily mysqldump of app MySQL DB, gzip, 30-day retention (see scripts/daily_db_backup.py)",
    # 21:00 UTC = 02:30 AM IST (next calendar day in India)
    schedule="0 21 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["backup", "operations", "database"],
) as dag:
    PythonOperator(
        task_id="run_database_backup",
        python_callable=run_database_backup_job,
        dag=dag,
    )
