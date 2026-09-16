#!/usr/bin/env python3
"""
Workflow Cleanup Script for Monthly Investment Cycles

This script handles the cleanup of completed workflows and prepares the system
for the next month's investment cycle.
"""

import os
import sys
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add the app directory to the Python path (this file lives in scripts/utilities/)
_here = os.path.dirname(os.path.abspath(__file__))
_app_root = os.path.abspath(os.path.join(_here, "..", ".."))
for _p in (_app_root, _here):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from models import db, Workflow, WorkflowAction, MonthlyInvestment
from flask import Flask
from extensions import db as db_ext

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'your-secret-key'
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or (
    f"mysql+pymysql://{os.environ.get('DB_USER', 'inertia_admin')}:"
    f"{os.environ.get('DB_PASSWORD', '')}@"
    f"{os.environ.get('DB_HOST', '127.0.0.1')}:{os.environ.get('DB_PORT', '3306')}/"
    f"{os.environ.get('DB_NAME', 'inertia_app2025')}"
)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db_ext.init_app(app)
    return app

def cleanup_completed_workflows(days_old=30):
    """
    Archive or clean up completed workflows that are older than specified days.
    
    Args:
        days_old (int): Number of days after which completed workflows should be cleaned up
    """
    app = create_app()
    
    with app.app_context():
        try:
            # Calculate the cutoff date
            cutoff_date = datetime.utcnow() - timedelta(days=days_old)
            
            # Find completed workflows older than the cutoff date
            old_completed_workflows = Workflow.query.filter(
                Workflow.current_stage == 'COMPLETED',
                Workflow.updated_at < cutoff_date
            ).all()
            
            print(f"Found {len(old_completed_workflows)} completed workflows older than {days_old} days")
            
            for workflow in old_completed_workflows:
                print(f"Cleaning up workflow {workflow.id} for investment {workflow.monthly_investment_id}")
                
                # Archive workflow actions (optional - you might want to keep them for audit)
                # For now, we'll just mark the workflow as archived
                workflow.is_archived = True
                workflow.archived_at = datetime.utcnow()
                
                # Update the monthly investment status if needed
                if workflow.monthly_investment:
                    investment = workflow.monthly_investment
                    if investment.status == 'COMPLETED':
                        # You might want to change this to 'ARCHIVED' or keep as 'COMPLETED'
                        pass
            
            db.session.commit()
            print(f"Successfully cleaned up {len(old_completed_workflows)} workflows")
            
        except Exception as e:
            print(f"Error during cleanup: {e}")
            db.session.rollback()

def prepare_next_month_cycle():
    """
    Prepare the system for the next month's investment cycle.
    This includes:
    1. Archiving completed workflows for the current month
    2. Creating new monthly investments for recurring clients
    3. Initializing new workflows for the next month
    4. Generating reports for the previous month
    """
    app = create_app()
    
    with app.app_context():
        try:
            # Get the current date and calculate next month
            current_date = datetime.utcnow()
            current_month_start = current_date.replace(day=1)
            next_month = current_date.replace(day=1) + timedelta(days=32)
            next_month = next_month.replace(day=1)
            
            print(f"Preparing for next month cycle: {next_month.strftime('%Y-%m')}")
            
            # Step 1: Archive completed workflows for current month
            print("Step 1: Archiving completed workflows for current month...")
            completed_workflows = Workflow.query.filter(
                Workflow.current_stage == 'COMPLETED',
                Workflow.updated_at >= current_month_start,
                Workflow.updated_at < next_month
            ).all()
            
            archived_count = 0
            for workflow in completed_workflows:
                workflow.is_archived = True
                workflow.archived_at = datetime.utcnow()
                workflow.archived_reason = 'Monthly cycle completion'
                archived_count += 1
                print(f"  - Archived workflow {workflow.id} for client {workflow.monthly_investment.client.name}")
            
            # Step 2: Find clients who had completed workflows this month
            clients_with_completed_workflows = set()
            for workflow in completed_workflows:
                if workflow.monthly_investment and workflow.monthly_investment.client:
                    clients_with_completed_workflows.add(workflow.monthly_investment.client)
            
            # Step 3: Create new monthly investments for next month
            print(f"Step 2: Creating new monthly investments for {len(clients_with_completed_workflows)} clients...")
            new_investments_created = 0
            
            for client in clients_with_completed_workflows:
                # Check if client already has a pending investment for next month
                existing_investment = MonthlyInvestment.query.filter(
                    MonthlyInvestment.client_id == client.id,
                    MonthlyInvestment.investment_date >= next_month,
                    MonthlyInvestment.status.in_(['PENDING', 'IN_PROGRESS'])
                ).first()
                
                if not existing_investment:
                    # Get the last completed investment amount for this client
                    last_completed_investment = MonthlyInvestment.query.join(Workflow).filter(
                        MonthlyInvestment.client_id == client.id,
                        Workflow.current_stage == 'COMPLETED'
                    ).order_by(MonthlyInvestment.investment_date.desc()).first()
                    
                    planned_amount = last_completed_investment.planned_amount if last_completed_investment else 50000  # Default amount
                    
                    from services.monthly_investment_portfolio_service import (
                        get_or_create_portfolio_for_client,
                    )

                    portfolio = get_or_create_portfolio_for_client(client.id, 1)
                    new_investment = MonthlyInvestment(
                        client_id=client.id,
                        portfolio_id=portfolio.id,
                        planned_amount=planned_amount,
                        investment_date=next_month,
                        status='PENDING',
                        created_by=1  # Default user ID
                    )
                    db.session.add(new_investment)
                    db.session.flush()  # Get the ID
                    
                    # Create new workflow
                    new_workflow = Workflow(
                        monthly_investment_id=new_investment.id,
                        current_stage='FUNDS',
                        planned_amount=planned_amount,
                        investment_date=next_month,
                        target_completion_date=next_month + timedelta(days=30),
                        created_by=1  # Default user ID
                    )
                    db.session.add(new_workflow)
                    new_investments_created += 1
                    print(f"  - Created new investment for {client.name}: ₹{planned_amount:,.2f}")
            
            # Step 4: Handle clients with pending workflows (not completed)
            print("Step 3: Checking pending workflows...")
            pending_workflows = Workflow.query.filter(
                Workflow.current_stage != 'COMPLETED',
                Workflow.is_archived.is_(None)
            ).all()
            
            for workflow in pending_workflows:
                if workflow.monthly_investment:
                    print(f"  - Pending workflow for {workflow.monthly_investment.client.name}: {workflow.current_stage}")
            
            # Commit all changes
            db.session.commit()
            
            print(f"Next month cycle preparation completed!")
            print(f"  - Archived {archived_count} completed workflows")
            print(f"  - Created {new_investments_created} new monthly investments")
            print(f"  - {len(pending_workflows)} pending workflows remain active")
            
        except Exception as e:
            print(f"Error preparing next month cycle: {e}")
            db.session.rollback()

def generate_monthly_report(month=None, year=None):
    """
    Generate a comprehensive monthly report for completed workflows.
    
    Args:
        month (int): Month number (1-12), defaults to current month
        year (int): Year, defaults to current year
    """
    app = create_app()
    
    with app.app_context():
        try:
            if month is None:
                month = datetime.utcnow().month
            if year is None:
                year = datetime.utcnow().year
            
            # Find all workflows completed in the specified month
            start_date = datetime(year, month, 1)
            if month == 12:
                end_date = datetime(year + 1, 1, 1)
            else:
                end_date = datetime(year, month + 1, 1)
            
            completed_workflows = Workflow.query.filter(
                Workflow.current_stage == 'COMPLETED',
                Workflow.updated_at >= start_date,
                Workflow.updated_at < end_date
            ).all()
            
            print(f"Monthly Report for {year}-{month:02d}")
            print(f"Total completed workflows: {len(completed_workflows)}")
            
            total_amount = 0
            for workflow in completed_workflows:
                if workflow.monthly_investment:
                    amount = workflow.monthly_investment.planned_amount or 0
                    total_amount += float(amount)
                    print(f"  - {workflow.monthly_investment.client.name}: ₹{amount:,.2f}")
            
            print(f"Total amount: ₹{total_amount:,.2f}")
            
        except Exception as e:
            print(f"Error generating monthly report: {e}")

def main():
    """Main function to run cleanup operations"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Workflow Cleanup and Management')
    parser.add_argument('--cleanup', action='store_true', 
                       help='Clean up old completed workflows')
    parser.add_argument('--days', type=int, default=30,
                       help='Number of days after which to clean up workflows (default: 30)')
    parser.add_argument('--prepare-next-month', action='store_true',
                       help='Prepare for next month cycle')
    parser.add_argument('--monthly-report', action='store_true',
                       help='Generate monthly report')
    parser.add_argument('--month', type=int, help='Month for report (1-12)')
    parser.add_argument('--year', type=int, help='Year for report')
    
    args = parser.parse_args()
    
    if args.cleanup:
        print("Starting workflow cleanup...")
        cleanup_completed_workflows(args.days)
    
    if args.prepare_next_month:
        print("Preparing next month cycle...")
        prepare_next_month_cycle()
    
    if args.monthly_report:
        print("Generating monthly report...")
        generate_monthly_report(args.month, args.year)
    
    if not any([args.cleanup, args.prepare_next_month, args.monthly_report]):
        print("No action specified. Use --help for options.")

if __name__ == "__main__":
    main()
