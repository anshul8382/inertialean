#!/usr/bin/env python3
"""
One-time script to create default monthly investment schedules from current month's data.
This script will:
1. Find all monthly investments for the current month
2. Create a MonthlyInvestmentSchedule for each client that doesn't already have one
3. Use the current month's planned_amount and investment_date day

Usage:
    python3 scripts/create_default_monthly_schedules.py
"""

import sys
import os
from datetime import datetime, timedelta

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from run import create_app
from app import db
from models import MonthlyInvestmentSchedule, MonthlyInvestment, User


def create_default_schedules():
    """Create default monthly investment schedules from all monthly investments in any stage"""
    app = create_app()
    
    with app.app_context():
        try:
            # Get current month start and end
            now = datetime.utcnow()
            current_month_start = now.replace(day=1).date()
            next_month = (current_month_start + timedelta(days=32)).replace(day=1)
            
            print(f"📅 Current month: {current_month_start.strftime('%Y-%m')}")
            print(f"📅 Next month: {next_month.strftime('%Y-%m')}")
            print()
            
            # Get all monthly investments (any stage, any date)
            # Group by client_id and get the most recent investment for each client
            from sqlalchemy import func
            from models import MonthlyInvestment
            
            # Get distinct clients with their most recent monthly investment
            # This subquery gets the max id (most recent) for each client
            subquery = db.session.query(
                func.max(MonthlyInvestment.id).label('max_id')
            ).group_by(MonthlyInvestment.client_id).subquery()
            
            # Get the actual investment records for those max ids
            all_investments = db.session.query(MonthlyInvestment).join(
                subquery, MonthlyInvestment.id == subquery.c.max_id
            ).all()
            
            if not all_investments:
                print("❌ No monthly investments found in the database.")
                print("   Cannot create default schedules.")
                return
            
            print(f"📊 Found {len(all_investments)} unique client(s) with monthly investments")
            print()
            
            # Get a default user ID (use first admin user or user with ID 1)
            default_user = User.query.filter_by(id=1).first()
            if not default_user:
                default_user = User.query.first()
            
            if not default_user:
                print("❌ No users found in database. Cannot create schedules.")
                return
            
            created_count = 0
            skipped_count = 0
            
            for investment in all_investments:
                # Check if schedule already exists for this client
                existing_schedule = MonthlyInvestmentSchedule.query.filter_by(
                    client_id=investment.client_id
                ).first()
                
                if existing_schedule:
                    print(f"⏭️  Skipping client ID {investment.client_id} - schedule already exists")
                    skipped_count += 1
                    continue
                
                # Create default schedule from the most recent investment
                day_of_month = investment.investment_date.day if investment.investment_date else 1
                # Cap at 28 to handle February
                day_of_month = min(day_of_month, 28)
                
                schedule = MonthlyInvestmentSchedule(
                    client_id=investment.client_id,
                    planned_amount=investment.planned_amount,
                    day_of_month=day_of_month,
                    is_active=True,
                    start_date=next_month,  # Start from next month
                    end_date=None,  # Ongoing
                    notes=f"Auto-created from monthly investment (₹{investment.planned_amount:,.2f}, date: {investment.investment_date})",
                    created_by=default_user.id
                )
                
                db.session.add(schedule)
                created_count += 1
                print(f"✅ Created schedule for client ID {investment.client_id}: "
                      f"₹{investment.planned_amount:,.2f} on day {day_of_month} "
                      f"(from investment dated {investment.investment_date})")
            
            if created_count > 0:
                db.session.commit()
                print()
                print(f"✅ Successfully created {created_count} schedule(s)")
                if skipped_count > 0:
                    print(f"⏭️  Skipped {skipped_count} client(s) that already had schedules")
            else:
                if skipped_count > 0:
                    print()
                    print(f"ℹ️  All {skipped_count} client(s) already have schedules configured.")
                else:
                    print()
                    print("⚠️  No schedules were created.")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error creating default schedules: {str(e)}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == '__main__':
    print("=" * 60)
    print("Monthly Investment Schedule Creation Script")
    print("=" * 60)
    print()
    create_default_schedules()
    print()
    print("=" * 60)
    print("Script completed.")
    print("=" * 60)

