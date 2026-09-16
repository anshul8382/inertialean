"""
Read Airflow metadata (SQLite) for DAG run health; sync failure alerts into alert_system_models.Alert.
"""
from __future__ import annotations

import glob
import logging
import os
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# Human-readable fix hints (extend as you learn common failures)
DAG_FAILURE_HINTS: Dict[str, str] = {
    "price_updates": "Check Google Sheets credentials, API quota, and network from the app host. Verify price sheet IDs and that the 4 PM IST run completed (closing prices).",
    "data_integrity_daily": "Inspect task logs in Airflow for the failing task. Typical causes: MySQL connectivity, timeout on large clients, or code errors in agents — check app logs and re-run from Data Integrity UI if needed.",
    "data_integrity_weekly": "Weekly scan touches all clients; failures often mean DB load or a specific client record. Check Airflow task log traceback, then run a narrower check from /data-integrity.",
    "recommendation_execution_daily": "Usually app context or DB errors when running RecommendationExecutionMonitor. Verify FLASK_APP / PYTHONPATH and MySQL from the Airflow worker host.",
    "recommendation_execution_weekly": "Same as daily; full audit is heavier — check task duration and MySQL max_connections / timeouts.",
    "hourly_sla_check": "SLA job calls AlertService with app context. Confirm MySQL, SLAConfiguration rows, and that create_app() succeeds under Airflow.",
    "daily_alert_report": "Alert report generation failed — check email/SMTP settings and AlertService dependencies.",
    "daily_reports": "Combined daily reports (workflow, leads, investments). Check subprocess logs, email config, and that scripts exist on the scheduler host.",
    "workflow_management": "scheduler.check_upcoming / past_due imports failed or DB error. Verify `scheduler` module and client/monthly_investment data.",
    "task_assignment_daily": "TaskAssignmentAgent backfill failed — check TaskAssignmentRule setup and MySQL.",
    "task_auto_close_daily": "OpsTask auto-close logic error or DB constraint — see task log traceback.",
    "task_reminders": "Task reminder email or in-app step failed — check mail config and TaskReminderService.",
    "portfolio_performance_monthly": "Portfolio performance agent failed — check IEX/API keys if used, MySQL, and agent logs.",
    "portfolio_performance_daily": "Portfolio performance agent failed — check IEX/API keys if used, MySQL, and agent logs.",
    "issue_lifecycle_healer_daily": "Issue healer failed — inspect root-cause evaluator logs, performance API dependencies, and DB writes for issue/task/alert resolution.",
    "scheduled_jobs_status_report": "Meta: this DAG emails job status; failure is often SMTP or inability to read airflow.db path.",
    "bni_ty_notes_weekly": "BNI TY Notes email — check Flask-Mail / recipients (admin + ops_manager with email), MySQL, and that client_bni_referral / bni_ty_notes_state tables exist.",
    "start_monthly_cycle": "Monthly cycle start script failed — verify investment_cycle / holdings services and DB state for the 1st-of-month run.",
    "daily_holdings_processor": "Holdings batch processor error — check forward_holding / client batch logs and DB.",
    "send_holdings_notifications": "Notification step failed after processing — check email templates and recipient config.",
    "cycle_status_monitor": "Weekly status report — check reporting queries and email.",
    "weekly_holdings_refresh": "Full reconciliation is heavy — check timeout (DAG allows 2h), disk, and MySQL.",
}


def default_failure_hint(dag_id: str) -> str:
    return DAG_FAILURE_HINTS.get(
        dag_id,
        "Open Airflow UI → this DAG → latest run → failed task → Log. Check MySQL connectivity, .env on the scheduler, and PYTHONPATH including the app root.",
    )


def _app_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tail_airflow_task_log(
    airflow_home: str,
    dag_id: str,
    run_id: str,
    task_id: str,
    max_lines: int = 15,
    max_chars: int = 2500,
) -> str:
    """Return the tail of the latest attempt log for a task, if present under AIRFLOW_HOME/logs."""
    if not airflow_home or not os.path.isdir(airflow_home):
        return ""
    base = os.path.join(airflow_home, "logs", f"dag_id={dag_id}", f"run_id={run_id}", f"task_id={task_id}")
    attempts = sorted(glob.glob(os.path.join(base, "attempt=*.log")))
    if not attempts:
        return ""
    path = attempts[-1]
    try:
        with open(path, "r", errors="replace") as fh:
            lines = fh.readlines()
        tail = "".join(lines[-max_lines:]).strip()
        if len(tail) > max_chars:
            tail = tail[-max_chars:]
        return tail
    except OSError as e:
        logger.debug("Could not read Airflow log %s: %s", path, e)
        return ""


def resolve_metadata_db_path(config_path: Optional[str] = None) -> str:
    if config_path:
        return config_path
    env = os.environ.get("AIRFLOW_METADATA_DB_PATH") or os.environ.get("AIRFLOW_HOME")
    if env and env.endswith(".db"):
        return env
    if env:
        return os.path.join(env, "airflow.db")
    return os.path.join(_app_root(), "airflow", "airflow.db")


def _connect_readonly(path: str) -> Optional[sqlite3.Connection]:
    if not path or not os.path.isfile(path):
        logger.warning("Airflow metadata DB not found at %s", path)
        return None
    try:
        uri = f"file:{path}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as e:
        logger.error("Cannot open Airflow DB %s: %s", path, e)
        return None


def _dag_run_date_column(conn: sqlite3.Connection) -> str:
    cur = conn.execute("PRAGMA table_info(dag_run)")
    cols = {row[1] for row in cur.fetchall()}
    if "logical_date" in cols:
        return "logical_date"
    if "execution_date" in cols:
        return "execution_date"
    return "start_date"


def _dag_schedule_columns(conn: sqlite3.Connection) -> Tuple[str, str]:
    cur = conn.execute("PRAGMA table_info(dag)")
    cols = {row[1] for row in cur.fetchall()}
    desc = "timetable_description" if "timetable_description" in cols else "schedule_interval"
    # post-migration uses timetable_summary instead of schedule_interval
    if "timetable_summary" in cols:
        summary = "timetable_summary"
    else:
        summary = "schedule_interval" if "schedule_interval" in cols else desc
    return desc, summary


def _dag_is_active_sql(conn: sqlite3.Connection) -> str:
    """SQL expression for DAG active-ish flag. Airflow 2 had ``is_active``; AF3 uses ``is_stale`` only."""
    cur = conn.execute("PRAGMA table_info(dag)")
    cols = {row[1] for row in cur.fetchall()}
    if "is_active" in cols:
        return "is_active"
    if "is_stale" in cols:
        return "(CASE WHEN is_stale = 0 THEN 1 ELSE 0 END)"
    return "1"


def _dag_next_dagrun_sql(conn: sqlite3.Connection) -> str:
    cur = conn.execute("PRAGMA table_info(dag)")
    cols = {row[1] for row in cur.fetchall()}
    if "next_dagrun" in cols:
        return "next_dagrun"
    if "next_dagrun_create_after" in cols:
        return "next_dagrun_create_after"
    return "NULL"


def fetch_dag_overview(
    metadata_db_path: Optional[str] = None,
    airflow_home: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    One row per DAG from `dag` plus latest `dag_run` state and failed task ids for that run.
    """
    path = resolve_metadata_db_path(metadata_db_path)
    conn = _connect_readonly(path)
    if not conn:
        return []

    try:
        desc_col, summary_col = _dag_schedule_columns(conn)
        date_col = _dag_run_date_column(conn)
        is_active_sql = _dag_is_active_sql(conn)
        next_dagrun_sql = _dag_next_dagrun_sql(conn)

        dcur = conn.execute(
            f"""
            SELECT dag_id, is_paused, {is_active_sql}, COALESCE({desc_col}, ''), COALESCE({summary_col}, ''), {next_dagrun_sql}
            FROM dag
            ORDER BY dag_id
            """
        )
        dags = []
        for row in dcur.fetchall():
            dags.append(
                {
                    "dag_id": row[0],
                    "is_paused": bool(row[1]),
                    "is_active": bool(row[2]),
                    "timetable_description": row[3] or "",
                    "timetable_summary": row[4] or "",
                    "next_dagrun": row[5],
                }
            )

        # Latest run per DAG by activity time (then id). Avoid MAX(id)-only quirks.
        rcur = conn.execute(
            f"""
            SELECT dr.dag_id, dr.run_id, dr.{date_col}, dr.start_date, dr.end_date, dr.state
            FROM dag_run dr
            WHERE dr.id = (
                SELECT d2.id FROM dag_run d2
                WHERE d2.dag_id = dr.dag_id
                ORDER BY COALESCE(d2.end_date, d2.start_date, d2.{date_col}, '') DESC,
                         d2.id DESC
                LIMIT 1
            )
            """
        )
        best: Dict[str, Tuple] = {}
        for row in rcur.fetchall():
            best[row[0]] = row

        # Most recent completed run (success/failed) when latest is still queued/running
        ccur = conn.execute(
            f"""
            SELECT dr.dag_id, dr.run_id, dr.{date_col}, dr.start_date, dr.end_date, dr.state
            FROM dag_run dr
            WHERE lower(COALESCE(dr.state, '')) IN ('success', 'failed')
              AND dr.id = (
                  SELECT d2.id FROM dag_run d2
                  WHERE d2.dag_id = dr.dag_id
                    AND lower(COALESCE(d2.state, '')) IN ('success', 'failed')
                  ORDER BY COALESCE(d2.end_date, d2.start_date, d2.{date_col}, '') DESC,
                           d2.id DESC
                  LIMIT 1
              )
            """
        )
        last_completed: Dict[str, Tuple] = {row[0]: row for row in ccur.fetchall()}

        failed_tasks_by_key: Dict[Tuple[str, str], List[str]] = {}
        for dag_id, run_row in best.items():
            run_id = run_row[1]
            st = (run_row[5] or "").lower()
            if st != "failed":
                continue
            tcur = conn.execute(
                """
                SELECT task_id FROM task_instance
                WHERE dag_id = ? AND run_id = ? AND state = 'failed'
                ORDER BY task_id
                """,
                (dag_id, run_id),
            )
            failed_tasks_by_key[(dag_id, run_id)] = [r[0] for r in tcur.fetchall()]

        for d in dags:
            did = d["dag_id"]
            br = best.get(did)
            if not br:
                d["last_run_id"] = None
                d["last_logical_date"] = None
                d["last_start_date"] = None
                d["last_end_date"] = None
                d["last_state"] = None
                d["failed_task_ids"] = []
                d["suggestion"] = ""
                d["last_completed_state"] = None
                d["last_completed_run_id"] = None
                d["last_completed_end_date"] = None
                continue
            _, run_id, logical_d, start_d, end_d, state = br
            d["last_run_id"] = run_id
            d["last_logical_date"] = logical_d
            d["last_start_date"] = start_d
            d["last_end_date"] = end_d
            d["last_state"] = (state or "").lower()
            d["failed_task_ids"] = failed_tasks_by_key.get((did, run_id), [])
            lc = last_completed.get(did)
            if lc:
                d["last_completed_state"] = (lc[5] or "").lower()
                d["last_completed_run_id"] = lc[1]
                d["last_completed_end_date"] = lc[4]
            else:
                d["last_completed_state"] = None
                d["last_completed_run_id"] = None
                d["last_completed_end_date"] = None
            st = d["last_state"]
            d["log_excerpt"] = ""
            if st == "failed":
                d["suggestion"] = default_failure_hint(did)
                if d["failed_task_ids"]:
                    d["suggestion"] += f" Failed tasks: {', '.join(d['failed_task_ids'])}."
                home = airflow_home
                if not home and path:
                    home = os.path.dirname(path)
                if home and d["failed_task_ids"] and run_id:
                    d["log_excerpt"] = tail_airflow_task_log(
                        home, did, run_id, d["failed_task_ids"][0]
                    )
            else:
                d["suggestion"] = ""

        return dags
    except sqlite3.Error as exc:
        logger.error("Airflow metadata DB query failed (%s): %s", path, exc, exc_info=True)
        return []
    finally:
        conn.close()


def build_airflow_runs_template_context(
    app_config: Mapping[str, Any],
    *,
    sync_result: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Shared kwargs for hub/airflow_runs.html (used by Hub and /airflow/scheduled-jobs)."""
    path = app_config.get("AIRFLOW_METADATA_DB_PATH")
    home = app_config.get("AIRFLOW_HOME")
    airflow_ui = app_config.get("AIRFLOW_UI_BASE_URL") or ""
    dag_rows = fetch_dag_overview(path, airflow_home=home)
    failed_count = sum(1 for d in dag_rows if (d.get("last_state") or "").lower() == "failed")
    success_count = sum(1 for d in dag_rows if (d.get("last_state") or "").lower() == "success")
    other_count = max(0, len(dag_rows) - failed_count - success_count)
    return {
        "dag_rows": dag_rows,
        "airflow_ui_base": airflow_ui,
        "failed_count": failed_count,
        "success_count": success_count,
        "other_count": other_count,
        "metadata_db_path": path,
        "sync_result": sync_result,
        "status_refreshed_note": (
            "Status is read live from Airflow’s metadata DB on every page load. "
            "Manual script runs outside Airflow do not clear a red badge — trigger the DAG "
            "(or wait for the next schedule) so the latest run becomes success."
        ),
    }


def sync_airflow_failure_alerts(
    db_session,
    acting_user_id: int,
    metadata_db_path: Optional[str] = None,
    airflow_home: Optional[str] = None,
) -> Dict[str, int]:
    """
    Create/update active alerts for DAGs whose latest run failed; resolve when latest run succeeds.
    """
    try:
        from alert_system_models import Alert
    except Exception:
        logger.warning("alert_system_models.Alert not available; skip sync")
        return {"created": 0, "updated": 0, "resolved": 0, "skipped": 1}

    rows = fetch_dag_overview(metadata_db_path, airflow_home=airflow_home)
    created = updated = resolved = 0

    for d in rows:
        dag_id = d["dag_id"]
        state = (d.get("last_state") or "").lower()
        run_id = d.get("last_run_id") or ""
        title = f"Airflow failure: {dag_id}"[:200]

        if state == "success":
            q = Alert.query.filter_by(
                alert_type="system",
                alert_subtype="airflow_dag",
                status="active",
                title=title,
            )
            for alert in q.all():
                alert.resolve(acting_user_id, notes="Latest Airflow run succeeded (auto-cleared from Hub).")
                resolved += 1
            continue

        if state != "failed" or not run_id:
            continue

        marker = f"airflow_run_id:{run_id}"
        failed_tasks = d.get("failed_task_ids") or []
        hint = default_failure_hint(dag_id)
        desc_lines = [
            f"{marker}",
            f"dag_id: {dag_id}",
            f"run_id: {run_id}",
            f"logical_date: {d.get('last_logical_date')}",
            f"start: {d.get('last_start_date')} end: {d.get('last_end_date')}",
        ]
        if failed_tasks:
            desc_lines.append("failed_tasks: " + ", ".join(failed_tasks))
        desc_lines.append("")
        desc_lines.append("Suggested actions:")
        desc_lines.append(hint)
        excerpt = (d.get("log_excerpt") or "").strip()
        if excerpt:
            desc_lines.append("")
            desc_lines.append("--- Log tail (first failed task) ---")
            desc_lines.append(excerpt[:8000])
        description = "\n".join(desc_lines)

        existing = Alert.query.filter_by(
            alert_type="system",
            alert_subtype="airflow_dag",
            status="active",
            title=title,
        ).first()

        if existing:
            if marker in (existing.description or ""):
                continue
            existing.description = description
            existing.severity = "warning"
            updated += 1
        else:
            db_session.add(
                Alert(
                    alert_type="system",
                    alert_subtype="airflow_dag",
                    severity="warning",
                    status="active",
                    client_id=None,
                    user_id=None,
                    title=title,
                    description=description,
                )
            )
            created += 1

    db_session.commit()
    return {"created": created, "updated": updated, "resolved": resolved, "skipped": 0}
