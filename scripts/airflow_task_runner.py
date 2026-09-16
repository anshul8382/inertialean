#!/usr/bin/env python3
"""
Run named Airflow tasks inside the *app* venv (Flask / Mail / DB available).

Usage:
  /opt/Inertia2026v1/venv/bin/python scripts/airflow_task_runner.py data_integrity_daily
"""
from __future__ import annotations

import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.chdir(ROOT)


def _app():
    from main import create_app

    return create_app()


def task_data_integrity_daily():
    from agents.data_integrity_manager import DataIntegrityManager
    from services.ops_digest_email_service import send_data_integrity_open_issues_digests
    from services.task_auto_close_service import close_completed_ops_tasks

    app = _app()
    with app.app_context():
        agent = DataIntegrityManager()
        run_id = agent.run_incremental(days=7)
        print(f"data_integrity_daily ok run_id={run_id}")
        digest = send_data_integrity_open_issues_digests()
        print(f"di_open_issues_digest {digest}")
        closed = close_completed_ops_tasks()
        print(f"OpsTask auto-close: {closed}")


def task_data_integrity_weekly():
    from agents.data_integrity_manager import DataIntegrityManager
    from services.ops_digest_email_service import send_data_integrity_open_issues_digests
    from services.task_auto_close_service import close_completed_ops_tasks

    app = _app()
    with app.app_context():
        agent = DataIntegrityManager()
        run_id = agent.run_full_audit()
        print(f"data_integrity_weekly ok run_id={run_id}")
        digest = send_data_integrity_open_issues_digests()
        print(f"di_open_issues_digest {digest}")
        closed = close_completed_ops_tasks()
        print(f"OpsTask auto-close: {closed}")


def task_recommendation_execution_daily():
    from agents.recommendation_execution_monitor import RecommendationExecutionMonitor
    from services.task_auto_close_service import close_completed_ops_tasks

    app = _app()
    with app.app_context():
        agent = RecommendationExecutionMonitor()
        run_id = agent.run_incremental(days=7)
        print(f"recommendation_execution_daily ok run_id={run_id}")
        closed = close_completed_ops_tasks()
        print(f"OpsTask auto-close: {closed}")


def task_recommendation_execution_weekly():
    from agents.recommendation_execution_monitor import RecommendationExecutionMonitor
    from services.task_auto_close_service import close_completed_ops_tasks

    app = _app()
    with app.app_context():
        agent = RecommendationExecutionMonitor()
        run_id = agent.run_full_audit()
        print(f"recommendation_execution_weekly ok run_id={run_id}")
        closed = close_completed_ops_tasks()
        print(f"OpsTask auto-close: {closed}")


def task_portfolio_performance_monthly():
    from agents.portfolio_performance_monitor import PortfolioPerformanceMonitor
    from services.task_auto_close_service import close_completed_ops_tasks

    app = _app()
    with app.app_context():
        agent = PortfolioPerformanceMonitor()
        run_id = agent.run_full_audit()
        print(f"portfolio_performance_monthly ok run_id={run_id}")
        closed = close_completed_ops_tasks()
        print(f"OpsTask auto-close: {closed}")


def task_task_reminders():
    from services.task_reminder_service import process_task_reminders as do_process

    app = _app()
    with app.app_context():
        count = do_process(app)
        print(f"task_reminders sent={count}")


def task_task_assignment_daily():
    from agents import TaskAssignmentAgent
    from services.task_auto_close_service import close_completed_ops_tasks

    app = _app()
    with app.app_context():
        agent = TaskAssignmentAgent()
        updated, tasks_created = agent.run_backfill()
        print(f"task_assignment_daily assigned={updated} ops_tasks={tasks_created}")
        closed = close_completed_ops_tasks()
        print(f"OpsTask auto-close: {closed}")


def task_task_auto_close_daily():
    from services.task_auto_close_service import close_completed_ops_tasks

    app = _app()
    with app.app_context():
        closed = close_completed_ops_tasks()
        print(f"task_auto_close_daily closed={closed}")


def task_issue_lifecycle_healer_daily():
    from services.issue_healer_service import run_issue_healer

    app = _app()
    with app.app_context():
        summary = run_issue_healer()
        print(f"issue_lifecycle_healer_daily {summary}")


def task_workflow_upcoming():
    import scheduler as sched_mod

    app = _app()
    with app.app_context():
        sched_mod.check_upcoming_investments()
        print("workflow upcoming ok")


def task_workflow_past_due():
    import scheduler as sched_mod

    app = _app()
    with app.app_context():
        sched_mod.check_past_due_investments()
        print("workflow past_due ok")


def task_hourly_sla_check():
    from manual_sla_check import run_manual_sla_check

    run_manual_sla_check()
    print("hourly_sla_check ok")


def task_daily_alert_report():
    from services.ops_digest_email_service import send_alert_advisor_digests

    app = _app()
    with app.app_context():
        result = send_alert_advisor_digests()
        print(f"daily_alert_report {result}")


def task_review_workflow_daily_report():
    from services.ops_digest_email_service import send_review_workflow_daily_digests

    app = _app()
    with app.app_context():
        result = send_review_workflow_daily_digests()
        print(f"review_workflow_daily_report {result}")


def task_price_updates():
    from api.v1.cron_jobs import run_price_update_sheets

    result = run_price_update_sheets()
    print(f"price_updates result={result}")


def task_nifty_price_update():
    from api.v1.cron_jobs import run_nifty_price_update

    result = run_nifty_price_update()
    print(f"nifty_price_update result={result}")


def task_price_accuracy_weekly():
    from services.price_accuracy_service import run_price_accuracy_scan

    threshold = float(os.environ.get("PRICE_ACCURACY_SCAN_THRESHOLD_PCT", "10") or "10")
    app = _app()
    with app.app_context():
        summary = run_price_accuracy_scan(threshold_pct=threshold)
        print(f"price_accuracy_weekly {summary}")


def task_price_sheet_suppress_cycles():
    from services.price_accuracy_sheet_suppress_service import run_sheet_suppress_cycle

    app = _app()
    with app.app_context():
        summary = run_sheet_suppress_cycle()
        print(f"price_sheet_suppress_cycles {summary}")


def task_client_health_nightly():
    from services.client_health_nightly_service import run_client_health_nightly

    app = _app()
    with app.app_context():
        summary = run_client_health_nightly()
        print(f"client_health_nightly {summary}")


def task_cashflow_trade_integrity_nightly():
    from services.cashflow_trade_nightly_service import run_cashflow_trade_nightly

    app = _app()
    with app.app_context():
        summary = run_cashflow_trade_nightly()
        print(f"cashflow_trade_integrity_nightly {summary}")


def task_client_data_integrity_nightly():
    from services.client_data_integrity_nightly_service import (
        run_client_data_integrity_nightly,
    )

    app = _app()
    with app.app_context():
        summary = run_client_data_integrity_nightly()
        print(f"client_data_integrity_nightly {summary}")


def task_bni_ty_notes_weekly():
    from services.bni_ty_notes_service import run_bni_ty_notes_email

    app = _app()
    with app.app_context():
        result = run_bni_ty_notes_email(dry_run=False)
        print(result)
        if result.get("error") == "no_recipients":
            raise RuntimeError(
                "BNI TY Notes: no admin/ops_manager recipients with email configured"
            )

TASKS = {
    "data_integrity_daily": task_data_integrity_daily,
    "data_integrity_weekly": task_data_integrity_weekly,
    "recommendation_execution_daily": task_recommendation_execution_daily,
    "recommendation_execution_weekly": task_recommendation_execution_weekly,
    "portfolio_performance_monthly": task_portfolio_performance_monthly,
    "task_reminders": task_task_reminders,
    "task_assignment_daily": task_task_assignment_daily,
    "task_auto_close_daily": task_task_auto_close_daily,
    "issue_lifecycle_healer_daily": task_issue_lifecycle_healer_daily,
    "workflow_upcoming": task_workflow_upcoming,
    "workflow_past_due": task_workflow_past_due,
    "hourly_sla_check": task_hourly_sla_check,
    "daily_alert_report": task_daily_alert_report,
    "review_workflow_daily_report": task_review_workflow_daily_report,
    "price_updates": task_price_updates,
    "nifty_price_update": task_nifty_price_update,
    "price_accuracy_weekly": task_price_accuracy_weekly,
    "price_sheet_suppress_cycles": task_price_sheet_suppress_cycles,
    "client_health_nightly": task_client_health_nightly,
    "cashflow_trade_integrity_nightly": task_cashflow_trade_integrity_nightly,
    "client_data_integrity_nightly": task_client_data_integrity_nightly,
    "bni_ty_notes_weekly": task_bni_ty_notes_weekly,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in TASKS:
        print("Usage: airflow_task_runner.py <task_name>")
        print("Tasks:", ", ".join(sorted(TASKS)))
        return 2
    name = sys.argv[1]
    print(f"Running task={name}")
    TASKS[name]()
    print(f"Done task={name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
