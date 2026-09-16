from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from models import db, MonthlyInvestment, Workflow, User
from flask import current_app

def create_workflow_for_investment(investment, admin_user):
    """Create a workflow for a monthly investment"""
    if not investment.workflow:  # Only create if workflow doesn't exist
        workflow = Workflow(
            monthly_investment=investment,
            current_stage='FUNDS',
            planned_amount=investment.planned_amount,
            investment_date=investment.investment_date,
            target_completion_date=investment.investment_date,
            created_by=admin_user.id
        )
        db.session.add(workflow)
        db.session.commit()
        current_app.logger.info(f"Created workflow for investment {investment.id}")

def check_upcoming_investments():
    """Check for investments due in 2-3 days and create workflows"""
    with current_app.app_context():
        # Get admin user (first user in the system)
        admin_user = User.query.first()
        if not admin_user:
            current_app.logger.error("No admin user found for workflow creation")
            return

        # Get investments due in 2-3 days
        today = datetime.now().date()
        start_date = today + timedelta(days=2)
        end_date = today + timedelta(days=3)
        
        upcoming_investments = MonthlyInvestment.query.filter(
            MonthlyInvestment.investment_date.between(start_date, end_date),
            MonthlyInvestment.status == 'PENDING'
        ).all()

        for investment in upcoming_investments:
            create_workflow_for_investment(investment, admin_user)

def init_scheduler(app):
    """Initialize the scheduler"""
    scheduler = BackgroundScheduler()
    
    # Schedule the check_upcoming_investments job to run daily at 9 AM
    scheduler.add_job(
        func=check_upcoming_investments,
        trigger=CronTrigger(hour=9, minute=0),
        id='check_upcoming_investments',
        name='Check for upcoming investments and create workflows',
        replace_existing=True
    )
    
    scheduler.start()
    app.logger.info("Scheduler started")
    return scheduler 