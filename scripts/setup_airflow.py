#!/usr/bin/env python3
"""
Setup Airflow for Inertia Investment Management System
"""
import os
import subprocess
import sys


def _resolve_airflow_cmd(airflow_home: str) -> str:
    app_root = os.path.dirname(airflow_home)
    candidates = [
        os.environ.get("AIRFLOW_CMD"),
        os.path.join(os.environ["AIRFLOW_VENV"], "bin", "airflow")
        if os.environ.get("AIRFLOW_VENV")
        else None,
        os.path.join(app_root, "airflow_venv", "bin", "airflow"),
        "/opt/Inertia2026v1/airflow_venv/bin/airflow",
        "/home/anshul/airflow_venv/bin/airflow",
        "/home/inertia/airflow_venv/bin/airflow",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "/home/inertia/airflow_venv/bin/airflow"


def setup_airflow():
    """Set up Airflow environment"""
    print("🚀 Setting up Apache Airflow...")

    airflow_home = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "airflow"
    )
    os.environ["AIRFLOW_HOME"] = airflow_home
    airflow_cmd = _resolve_airflow_cmd(airflow_home)
    constraints = os.environ.get(
        "AIRFLOW_CONSTRAINTS_URL",
        "https://raw.githubusercontent.com/apache/airflow/constraints-3.0.6/constraints-3.9.txt",
    )

    os.makedirs(airflow_home, exist_ok=True)
    os.makedirs(os.path.join(airflow_home, "dags"), exist_ok=True)
    os.makedirs(os.path.join(airflow_home, "logs"), exist_ok=True)
    os.makedirs(os.path.join(airflow_home, "plugins"), exist_ok=True)
    os.makedirs(os.path.join(airflow_home, "email_templates"), exist_ok=True)

    print(f"✅ Airflow home set to: {airflow_home}")
    print(f"   Using airflow binary: {airflow_cmd}")

    print("\n📊 Initializing Airflow database (Airflow 3: db migrate)...")
    env = {
        **os.environ,
        "AIRFLOW_HOME": airflow_home,
        "AIRFLOW_CONFIG": os.path.join(airflow_home, "airflow.cfg"),
        "AIRFLOW__CORE__EXECUTOR": "SequentialExecutor",
        "AIRFLOW__CORE__DAGS_FOLDER": os.path.join(airflow_home, "dags"),
        "AIRFLOW__LOGGING__BASE_LOG_FOLDER": os.path.join(airflow_home, "logs"),
    }
    try:
        subprocess.run(
            [airflow_cmd, "db", "migrate"],
            check=True,
            cwd="/tmp",
            env=env,
        )
        print("✅ Airflow database migrated")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error initializing database: {e}")
        return False
    except FileNotFoundError:
        print(
            f"❌ Airflow not found at {airflow_cmd}. Install with:\n"
            f"   python3 -m venv {os.path.dirname(airflow_home)}/airflow_venv\n"
            f"   pip install 'apache-airflow==3.0.6' apache-airflow-providers-fab "
            f'--constraint "{constraints}"'
        )
        return False

    print("\n👤 Creating Airflow admin user...")
    try:
        subprocess.run(
            [
                airflow_cmd,
                "users",
                "create",
                "--username",
                "admin",
                "--firstname",
                "Admin",
                "--lastname",
                "User",
                "--role",
                "Admin",
                "--email",
                "anshul@equities4wealth.com",
                "--password",
                "inertia2025",
            ],
            check=True,
            cwd="/tmp",
            env=env,
        )
        print("✅ Admin user created (username: admin, password: inertia2025)")
    except subprocess.CalledProcessError:
        print("⚠️  Admin user may already exist, skipping...")

    app_root = os.path.dirname(airflow_home)
    print("\n✅ Airflow setup complete!")
    print("\n📝 Next steps:")
    print(f"1. Start: cd {app_root} && ./scripts/start_airflow.sh")
    print("2. UI via SSH tunnel to 127.0.0.1:8080")
    print("3. Login: admin / inertia2025")
    return True


if __name__ == "__main__":
    sys.exit(0 if setup_airflow() else 1)
