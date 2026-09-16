"""Shared helpers for Airflow DAGs on Lean / BigRock.

Always execute app code with the *application* venv, not airflow_venv
(Airflow 3 workers do not have Flask-Mail / app deps).
"""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional, Sequence


def app_root() -> str:
    return os.environ.get("INERTIA_APP_DIR") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def app_python() -> str:
    root = app_root()
    return os.environ.get("INERTIA_VENV_PYTHON") or os.path.join(
        root, "venv", "bin", "python"
    )


def run_app_argv(argv: Sequence[str], *, label: Optional[str] = None) -> None:
    py = app_python()
    root = app_root()
    if not os.path.isfile(py):
        raise FileNotFoundError(f"App venv python not found: {py}")
    cmd: List[str] = [py, *argv]
    env = os.environ.copy()
    env.setdefault("INERTIA_APP_DIR", root)
    dotenv = os.path.join(root, ".env")
    if os.path.isfile(dotenv):
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv, override=False)
            for key, val in os.environ.items():
                if key.startswith(("MAIL_", "EMAIL_", "DATABASE", "SQLALCHEMY", "SECRET")):
                    env[key] = val
        except Exception:
            pass
    result = subprocess.run(
        cmd,
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    name = label or " ".join(argv[:2])
    if result.returncode != 0:
        raise RuntimeError(
            f"{name} failed (exit {result.returncode}):\n"
            f"stdout:\n{(result.stdout or '')[-4000:]}\n"
            f"stderr:\n{(result.stderr or '')[-4000:]}"
        )


def run_app_script(rel_script: str, *script_args: str) -> None:
    root = app_root()
    path = os.path.join(root, *rel_script.split("/"))
    run_app_argv([path, *script_args], label=rel_script)


def run_app_task(task_name: str) -> None:
    """Run a named task via scripts/airflow_task_runner.py (app venv)."""
    run_app_script("scripts/airflow_task_runner.py", task_name)
