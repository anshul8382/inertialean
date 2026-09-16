#!/usr/bin/env python3
"""Fix missing workflows for specific clients.

- Creates workflows for investments that have no workflow (Baban, Anjali, Ankit)
- Creates Feb investment + workflow for clients missing them (Madhuri, Supriya)

Run from app root: python scripts/fix_missing_workflows.py
"""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import create_app
from extensions import db
from models import (
    Client,
    MonthlyInvestment,
    MonthlyInvestmentSchedule,
    Workflow,
    User,
)


def main():
    app = create_app()
    with app.app_context():
        admin_user = User.query.first()
        if not admin_user:
            print("Error: No user found for created_by")
            return 1

        today = date.today()
        target_month = today.replace(day=1)
        if target_month.month == 12:
            target_month_end = date(target_month.year + 1, 1, 1)
        else:
            target_month_end = date(target_month.year, target_month.month + 1, 1)

        created_workflows = 0
        created_investments = 0

        # 1. Create workflows for investments that don't have one
        investments_without_wf = (
            MonthlyInvestment.query.filter(
                MonthlyInvestment.investment_date >= target_month,
                MonthlyInvestment.investment_date < target_month_end,
                MonthlyInvestment.status.in_(['PENDING', 'IN_PROGRESS']),
            )
            .outerjoin(Workflow)
            .filter(Workflow.id.is_(None))
            .all()
        )

        for inv in investments_without_wf:
            wf = Workflow(
                monthly_investment_id=inv.id,
                current_stage='FUNDS',
                planned_amount=inv.planned_amount,
                investment_date=inv.investment_date,
                target_completion_date=inv.investment_date + timedelta(days=30),
                created_by=admin_user.id,
            )
            db.session.add(wf)
            created_workflows += 1
            print(f"  Created workflow for MI {inv.id} ({inv.client.name}, {inv.investment_date})")

        # 2. Create investment + workflow for Madhuri Pokharna, Supriya Dhumle
        for name in ['Madhuri Pokharna', 'Supriya Dhumle']:
            client = Client.query.filter(Client.name.ilike(name)).first()
            if not client:
                print(f"  Skip {name}: client not found")
                continue

            # Check if Feb investment already exists
            existing = MonthlyInvestment.query.filter(
                MonthlyInvestment.client_id == client.id,
                MonthlyInvestment.investment_date >= target_month,
                MonthlyInvestment.investment_date < target_month_end,
            ).first()
            if existing:
                print(f"  Skip {name}: Feb investment already exists (MI {existing.id})")
                continue

            schedule = MonthlyInvestmentSchedule.query.filter_by(
                client_id=client.id,
                is_active=True,
            ).first()
            if not schedule:
                print(f"  Skip {name}: no active schedule")
                continue

            # Get portfolio_id from last completed investment
            last_completed = (
                Workflow.query.join(MonthlyInvestment)
                .filter(
                    MonthlyInvestment.client_id == client.id,
                    Workflow.current_stage == 'COMPLETED',
                )
                .order_by(MonthlyInvestment.investment_date.desc())
                .first()
            )
            if not last_completed or not last_completed.monthly_investment:
                print(f"  Skip {name}: no completed investment to get portfolio_id")
                continue

            portfolio_id = last_completed.monthly_investment.portfolio_id
            planned_amount = float(schedule.planned_amount)
            investment_date = target_month.replace(day=min(schedule.day_of_month, 28))

            inv = MonthlyInvestment(
                client_id=client.id,
                portfolio_id=portfolio_id,
                planned_amount=planned_amount,
                investment_date=investment_date,
                status='PENDING',
                created_by=admin_user.id,
            )
            db.session.add(inv)
            db.session.flush()
            created_investments += 1

            schedule_notes = schedule.notes if schedule and getattr(schedule, 'notes', None) else None
            wf = Workflow(
                monthly_investment_id=inv.id,
                current_stage='FUNDS',
                planned_amount=planned_amount,
                investment_date=investment_date,
                target_completion_date=investment_date + timedelta(days=30),
                created_by=admin_user.id,
                schedule_notes=schedule_notes,
            )
            db.session.add(wf)
            created_workflows += 1
            print(f"  Created MI {inv.id} + workflow for {client.name} (₹{planned_amount:,.0f} on {investment_date})")

        db.session.commit()
        print()
        print(f"Done. Created {created_investments} investment(s), {created_workflows} workflow(s).")
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
