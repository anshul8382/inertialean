#!/usr/bin/env python3
"""Create missing next-cycle workflows for clients whose auto-creation failed (e.g. due to schedule_notes error).
Run from app root: python scripts/refresh_missing_workflows.py"""

import sys
import calendar
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import create_app
from extensions import db
from models import (
    Workflow, MonthlyInvestment, MonthlyInvestmentSchedule,
    User
)


def get_next_investment_date(workflow, today):
    """Compute next investment date from a completed workflow (mirrors routes/main.py logic)."""
    client = workflow.monthly_investment.client
    current_investment_date = workflow.monthly_investment.investment_date

    schedule = MonthlyInvestmentSchedule.query.filter_by(
        client_id=client.id,
        is_active=True
    ).first()

    if schedule and schedule.change_frequency_months:
        frequency_months = schedule.change_frequency_months
        next_investment_date = current_investment_date.replace(day=1)
        for _ in range(frequency_months):
            if next_investment_date.month == 12:
                next_investment_date = next_investment_date.replace(year=next_investment_date.year + 1, month=1)
            else:
                next_investment_date = next_investment_date.replace(month=next_investment_date.month + 1)
        last_day = calendar.monthrange(next_investment_date.year, next_investment_date.month)[1]
        safe_day = min(schedule.day_of_month, last_day)
        next_investment_date = next_investment_date.replace(day=safe_day)
        if next_investment_date < today:
            while next_investment_date < today:
                if next_investment_date.month == 12:
                    next_investment_date = next_investment_date.replace(year=next_investment_date.year + 1, month=1)
                else:
                    next_investment_date = next_investment_date.replace(month=next_investment_date.month + 1)
                last_day = calendar.monthrange(next_investment_date.year, next_investment_date.month)[1]
                safe_day = min(schedule.day_of_month, last_day)
                next_investment_date = next_investment_date.replace(day=safe_day)
        planned_amount = float(schedule.planned_amount)
        return next_investment_date, planned_amount, frequency_months, schedule
    else:
        next_month_date = current_investment_date.replace(day=1) + timedelta(days=32)
        next_month_date = next_month_date.replace(day=1)
        current_month = today.replace(day=1)
        next_month_allowed = current_month + timedelta(days=32)
        next_month_allowed = next_month_allowed.replace(day=1)
        if next_month_date > next_month_allowed:
            investment_date = current_month
        else:
            investment_date = max(next_month_date, current_month)
        planned_amount = float(workflow.planned_amount)
        return investment_date, planned_amount, 1, schedule


def main():
    app = create_app()
    with app.app_context():
        admin_user = User.query.first()
        if not admin_user:
            print("Error: No user found for created_by")
            return 1

        today = datetime.utcnow().date()
        completed = Workflow.query.filter_by(current_stage='COMPLETED').all()
        created = 0
        skipped = 0

        for workflow in completed:
            try:
                investment_date, planned_amount, freq_months, schedule = get_next_investment_date(workflow, today)
                client = workflow.monthly_investment.client

                existing = MonthlyInvestment.query.filter_by(
                    client_id=client.id,
                    investment_date=investment_date
                ).first()

                if existing:
                    skipped += 1
                    continue

                new_investment = MonthlyInvestment(
                    client_id=client.id,
                    portfolio_id=workflow.monthly_investment.portfolio_id,
                    planned_amount=planned_amount,
                    investment_date=investment_date,
                    status='PENDING',
                    created_by=admin_user.id
                )
                db.session.add(new_investment)
                db.session.flush()

                schedule_notes = schedule.notes if schedule and getattr(schedule, 'notes', None) else None
                new_workflow = Workflow(
                    monthly_investment_id=new_investment.id,
                    current_stage='FUNDS',
                    planned_amount=planned_amount,
                    investment_date=investment_date,
                    target_completion_date=investment_date + timedelta(days=30),
                    created_by=admin_user.id,
                    notes=f'Backfill: auto-created after workflow {workflow.id} (frequency: {freq_months} month(s))',
                    schedule_notes=schedule_notes
                )
                db.session.add(new_workflow)
                db.session.commit()
                created += 1
                print(f"  Created workflow for {client.name} - {investment_date.strftime('%B %Y')} (₹{planned_amount})")

            except Exception as e:
                db.session.rollback()
                print(f"  Error for workflow {workflow.id} (client {workflow.monthly_investment.client.name}): {e}", file=sys.stderr)

        print(f"\nDone. Created {created} missing workflow(s), skipped {skipped} (already exist).")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
