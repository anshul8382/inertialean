from functools import partial

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from flask import current_app, has_app_context
from sqlalchemy import func

from models import db, MonthlyInvestment, Workflow, User, MonthlyInvestmentSchedule


def create_workflow_for_investment(investment, admin_user):
    """Create a workflow for a monthly investment"""
    if not investment.workflow:  # Only create if workflow doesn't exist
        schedule = MonthlyInvestmentSchedule.query.filter_by(
            client_id=investment.client_id,
            is_active=True,
        ).first()

        schedule_notes = schedule.notes if schedule and getattr(schedule, "notes", None) else None
        workflow = Workflow(
            monthly_investment=investment,
            current_stage="FUNDS",
            planned_amount=investment.planned_amount,
            investment_date=investment.investment_date,
            target_completion_date=investment.investment_date,
            created_by=admin_user.id,
            notes=schedule_notes,
        )
        db.session.add(workflow)
        db.session.commit()
        current_app.logger.info("Created workflow for investment %s", investment.id)


def _pending_status_clause():
    """DB may store 'pending' or 'PENDING' (legacy)."""
    return func.lower(MonthlyInvestment.status) == "pending"


def _run_check_upcoming_investments():
    """Body: must run inside Flask app_context."""
    admin_user = User.query.first()
    if not admin_user:
        current_app.logger.error("No admin user found for workflow creation")
        return

    today = datetime.now().date()
    start_date = today + timedelta(days=2)
    end_date = today + timedelta(days=3)

    upcoming_investments = (
        MonthlyInvestment.query.filter(
            MonthlyInvestment.investment_date.between(start_date, end_date),
            _pending_status_clause(),
        ).all()
    )

    for investment in upcoming_investments:
        create_workflow_for_investment(investment, admin_user)


def _run_check_past_due_investments():
    """Body: must run inside Flask app_context."""
    admin_user = User.query.first()
    if not admin_user:
        current_app.logger.error("No admin user found for workflow creation")
        return

    today = datetime.now().date()

    past_due_investments = (
        MonthlyInvestment.query.outerjoin(
            Workflow,
            Workflow.monthly_investment_id == MonthlyInvestment.id,
        )
        .filter(
            MonthlyInvestment.investment_date < today,
            _pending_status_clause(),
            Workflow.id.is_(None),
        )
        .all()
    )

    current_app.logger.info(
        "Found %s past-due investments without workflows", len(past_due_investments)
    )

    for investment in past_due_investments:
        create_workflow_for_investment(investment, admin_user)


def check_upcoming_investments(app=None):
    """
    Check for investments due in 2-3 days and create workflows.
    Uses existing app context when present (Airflow DAG); otherwise pushes one.
    If ``app`` is passed (e.g. from init_scheduler), uses that app for APScheduler jobs.
    """
    from main import create_app

    if has_app_context():
        _run_check_upcoming_investments()
    elif app is not None:
        with app.app_context():
            _run_check_upcoming_investments()
    else:
        with create_app().app_context():
            _run_check_upcoming_investments()


def check_past_due_investments(app=None):
    """
    Past-due pending investments with no workflow — create workflows.
    Same context rules as ``check_upcoming_investments``.
    """
    from main import create_app

    if has_app_context():
        _run_check_past_due_investments()
    elif app is not None:
        with app.app_context():
            _run_check_past_due_investments()
    else:
        with create_app().app_context():
            _run_check_past_due_investments()


def init_scheduler(app):
    """Initialize APScheduler. Jobs run inside ``app`` context (avoids bare current_app)."""
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        func=partial(check_upcoming_investments, app),
        trigger=CronTrigger(hour=9, minute=0),
        id="check_upcoming_investments",
        name="Check for upcoming investments and create workflows",
        replace_existing=True,
    )

    scheduler.start()
    app.logger.info("Scheduler started")
    return scheduler
