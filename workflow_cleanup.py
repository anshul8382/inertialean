#!/usr/bin/env python3
"""
Workflow Cleanup Script for Monthly Investment Cycles

This script handles the cleanup of completed workflows and prepares the system
for the next month's investment cycle.
"""

import os
import sys
import calendar
from datetime import datetime, timedelta, date
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add the app directory to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models import db, Workflow, WorkflowAction, MonthlyInvestment
from main import create_app as create_inertia_app

def create_app():
    """
    Create a Flask app using the standard application factory.

    IMPORTANT: Do not hardcode credentials here. This script should use the same
    environment-driven configuration as the main app.
    """
    return create_inertia_app()

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
    1. Checking if current month's cycle is complete
    2. Archiving completed workflows for the current month
    3. Creating new monthly investments for recurring clients
    4. Initializing new workflows for the next month
    5. Generating reports for the previous month
    
    IMPORTANT: This should only be run MANUALLY after verifying that the current
    month's cycle is complete. It will NOT run if there are incomplete workflows.
    """
    app = create_app()
    
    with app.app_context():
        try:
            # Get the current date and calculate target month
            current_date = datetime.utcnow()
            current_month_start = current_date.replace(day=1)
            
            # The target month is the CURRENT month (not next month)
            # If we're in December, we're preparing December workflows for clients who completed November
            # So target_month should be December (current month), not January (next month)
            target_month = current_date.replace(day=1)
            target_month_date = target_month.date() if isinstance(target_month, datetime) else target_month
            
            # Calculate previous month (the month we look for completed workflows)
            # If we're in December, we look for November completions
            # Actually, we want to look at the PREVIOUS month's completions to create next month's workflows
            if current_month_start.month == 1:
                previous_month_start = date(current_month_start.year - 1, 12, 1)
            else:
                previous_month_start = date(current_month_start.year, current_month_start.month - 1, 1)
            
            print("="*70)
            print(f"PREPARING FOR MONTH CYCLE: {target_month_date.strftime('%Y-%m')}")
            print(f"Looking for completed workflows from: {previous_month_start.strftime('%Y-%m')}")
            print("="*70)
            print()
            
            # Step 0: Check for ALL pending workflows (not just current month)
            print("Step 0: Checking for ALL pending workflows...")
            from sqlalchemy import or_
            all_pending_workflows = Workflow.query.filter(
                Workflow.current_stage != 'COMPLETED',
                or_(
                    Workflow.is_archived.is_(None),
                    Workflow.is_archived == False
                )
            ).all()
            
            # Separate current month and older pending workflows
            current_month_pending = [wf for wf in all_pending_workflows 
                                    if wf.updated_at and wf.updated_at >= current_month_start]
            older_pending = [wf for wf in all_pending_workflows 
                           if wf not in current_month_pending]
            
            if all_pending_workflows:
                print(f"⚠️  WARNING: Found {len(all_pending_workflows)} pending workflows!")
                print()
                
                if current_month_pending:
                    print(f"📅 Pending workflows from CURRENT MONTH ({len(current_month_pending)}):")
                    for wf in current_month_pending[:10]:
                        if wf.monthly_investment and wf.monthly_investment.client:
                            updated_date = wf.updated_at.strftime('%Y-%m-%d') if wf.updated_at else 'N/A'
                            print(f"  - Workflow {wf.id}: {wf.monthly_investment.client.name} - Stage: {wf.current_stage} (Updated: {updated_date})")
                    if len(current_month_pending) > 10:
                        print(f"  ... and {len(current_month_pending) - 10} more")
                    print()
                
                if older_pending:
                    print(f"📅 Pending workflows from PREVIOUS MONTHS ({len(older_pending)}):")
                    for wf in older_pending[:10]:
                        if wf.monthly_investment and wf.monthly_investment.client:
                            updated_date = wf.updated_at.strftime('%Y-%m-%d') if wf.updated_at else 'N/A'
                            print(f"  - Workflow {wf.id}: {wf.monthly_investment.client.name} - Stage: {wf.current_stage} (Updated: {updated_date})")
                    if len(older_pending) > 10:
                        print(f"  ... and {len(older_pending) - 10} more")
                    print()
                
                if current_month_pending:
                    print("❌ CANNOT PROCEED: Current month's cycle is not complete!")
                    print("Please complete all workflows from the current month before preparing the next month.")
                    print("="*70)
                    return False, {'error': 'Current month cycle is not complete. Please complete all workflows from the current month before preparing the next month.'}
                else:
                    print("⚠️  WARNING: There are pending workflows from previous months.")
                    print("New workflows will NOT be created for clients with pending workflows.")
                    print()
            else:
                print("✅ No pending workflows found!")
                print()
            
            # Step 1: Find and archive completed workflows from PREVIOUS month
            # We're preparing for next month, so we find workflows that STARTED (investment_date) in the previous month
            # This finds workflows that STARTED in the previous month, not just completed in previous month
            print(f"Step 1: Finding completed workflows that STARTED in {previous_month_start.strftime('%Y-%m')}...")
            previous_month_end = current_month_start - timedelta(days=1)
            previous_month_end_date = previous_month_end.date() if isinstance(previous_month_end, datetime) else previous_month_end
            
            # Convert previous_month_start to date if it's datetime
            previous_month_start_date = previous_month_start.date() if isinstance(previous_month_start, datetime) else previous_month_start
            
            # Find completed workflows with investment_date in previous month (regardless of archived status)
            # We need to find these clients even if workflows are already archived
            all_completed_workflows_prev_month = Workflow.query.join(MonthlyInvestment).filter(
                Workflow.current_stage == 'COMPLETED',
                MonthlyInvestment.investment_date >= previous_month_start_date,
                MonthlyInvestment.investment_date <= previous_month_end_date
            ).all()
            
            # Only archive workflows that aren't already archived
            completed_workflows = [wf for wf in all_completed_workflows_prev_month 
                                 if wf.is_archived is None or wf.is_archived == False]
            
            archived_count = 0
            for workflow in completed_workflows:
                workflow.is_archived = True
                workflow.archived_at = datetime.utcnow()
                workflow.archived_reason = 'Monthly cycle completion'
                archived_count += 1
                print(f"  - Archived workflow {workflow.id} for client {workflow.monthly_investment.client.name}")
            
            # Step 2: Find clients who had completed workflows in previous month
            # Use ALL completed workflows (including archived) to find clients
            # This ensures we find clients even if their workflows were already archived
            clients_with_completed_workflows = set()
            for workflow in all_completed_workflows_prev_month:
                if workflow.monthly_investment and workflow.monthly_investment.client:
                    clients_with_completed_workflows.add(workflow.monthly_investment.client)
            
            # Step 2b: Also include clients with active monthly investment schedules
            # These are clients who should have recurring investments even if they didn't complete one this month
            from models import MonthlyInvestmentSchedule, Client
            active_schedules = MonthlyInvestmentSchedule.query.filter(
                MonthlyInvestmentSchedule.is_active == True,
                MonthlyInvestmentSchedule.start_date <= target_month_date,
                db.or_(
                    MonthlyInvestmentSchedule.end_date.is_(None),
                    MonthlyInvestmentSchedule.end_date >= target_month_date
                )
            ).all()
            
            clients_with_active_schedules = set()
            for schedule in active_schedules:
                client = Client.query.get(schedule.client_id)
                if client:
                    clients_with_active_schedules.add(client)
            
            # Combine both sets: clients with completed workflows OR clients with active schedules
            clients_to_process = clients_with_completed_workflows.union(clients_with_active_schedules)
            
            # Step 3: Create new monthly investments for next month
            # Get list of clients with pending workflows for CURRENT/NEXT month (not older months)
            # Only exclude clients who have pending workflows for the month we're preparing for
            clients_with_pending_workflows_current = set()
            current_month_start_date = current_month_start.date() if isinstance(current_month_start, datetime) else current_month_start
            for wf in all_pending_workflows:
                if wf.monthly_investment and wf.monthly_investment.client:
                    # Check if the pending workflow is for current month or next month
                    if wf.investment_date:
                        if isinstance(wf.investment_date, datetime):
                            wf_date = wf.investment_date.date()
                        elif isinstance(wf.investment_date, date):
                            wf_date = wf.investment_date
                        else:
                            continue
                        # If workflow is for current month or next month, exclude the client
                        if wf_date >= current_month_start_date:
                            clients_with_pending_workflows_current.add(wf.monthly_investment.client.id)
            
            print(f"Step 2: Creating new monthly investments...")
            print(f"  - Clients with completed workflows this month: {len(clients_with_completed_workflows)}")
            print(f"  - Clients with active schedules: {len(clients_with_active_schedules)}")
            print(f"  - Total clients to process: {len(clients_to_process)}")
            print(f"⚠️  Excluding {len(clients_with_pending_workflows_current)} clients with pending workflows for current/next month")
            print()
            new_investments_created = 0
            skipped_due_to_pending = 0
            
            for client in clients_to_process:
                # Check if client already has a workflow for target month
                # If they do, skip creating a duplicate
                # Calculate end of target month for date range
                if target_month_date.month == 12:
                    target_month_end = date(target_month_date.year + 1, 1, 1)
                else:
                    target_month_end = date(target_month_date.year, target_month_date.month + 1, 1)
                
                existing_workflow_for_target_month = Workflow.query.join(MonthlyInvestment).filter(
                    MonthlyInvestment.client_id == client.id,
                    MonthlyInvestment.investment_date >= target_month_date,
                    MonthlyInvestment.investment_date < target_month_end,
                    Workflow.current_stage != 'COMPLETED',
                    db.or_(
                        Workflow.is_archived.is_(None),
                        Workflow.is_archived == False
                    )
                ).first()
                
                if existing_workflow_for_target_month:
                    # Client already has a workflow for target month, skip
                    print(f"  ⏭️  Skipping {client.name}: Already has workflow for {target_month_date.strftime('%Y-%m')}")
                    continue
                
                # Check if client already has a pending investment for target month (without workflow)
                # target_month_end already calculated above
                existing_investment = MonthlyInvestment.query.filter(
                    MonthlyInvestment.client_id == client.id,
                    MonthlyInvestment.investment_date >= target_month_date,
                    MonthlyInvestment.investment_date < target_month_end,
                    MonthlyInvestment.status.in_(['PENDING', 'IN_PROGRESS'])
                ).outerjoin(Workflow).filter(Workflow.id.is_(None)).first()
                
                if not existing_investment:
                    # Priority 1: Check if client has an active monthly investment schedule
                    # MonthlyInvestmentSchedule already imported above
                    schedule = MonthlyInvestmentSchedule.query.filter_by(
                        client_id=client.id,
                        is_active=True
                    ).first()
                    
                    if schedule:
                        # Check if schedule is valid for target month
                        today = datetime.utcnow().date()
                        if schedule.start_date <= target_month_date and (schedule.end_date is None or schedule.end_date >= target_month_date):
                            # Check if investment is actually due based on frequency
                            from services.investment_cycle import is_due_change
                            
                            # Check if target month is due based on frequency
                            # We need to check if the target month date (on the scheduled day) is due
                            last_day = calendar.monthrange(target_month_date.year, target_month_date.month)[1]
                            safe_day = min(schedule.day_of_month, last_day)
                            check_date = target_month_date.replace(day=safe_day)
                            
                            # Only create if this month is actually due based on frequency
                            if is_due_change(schedule, check_date):
                                planned_amount = float(schedule.planned_amount)
                                # Use the day_of_month to set the investment date
                                investment_date = check_date
                                print(f"  - Using schedule for {client.name}: ₹{planned_amount:,.2f} on day {safe_day} (frequency: {schedule.change_frequency_months} month(s))")
                            else:
                                # This month is not due based on frequency - skip
                                print(f"  ⏭️  Skipping {client.name}: Target month {target_month_date.strftime('%Y-%m')} is not due based on frequency ({schedule.change_frequency_months} month(s))")
                                continue
                        else:
                            # Schedule is not active for target month, fall back to last completed
                            planned_amount = None
                    else:
                        planned_amount = None
                    
                    # Priority 2: Fall back to last completed investment amount if no schedule
                    if planned_amount is None:
                        last_completed_investment = MonthlyInvestment.query.join(Workflow).filter(
                            MonthlyInvestment.client_id == client.id,
                            Workflow.current_stage == 'COMPLETED'
                        ).order_by(MonthlyInvestment.investment_date.desc()).first()
                        
                        if last_completed_investment:
                            planned_amount = float(last_completed_investment.planned_amount)
                            # Use first day of target month as default
                            investment_date = target_month_date
                            print(f"  - Using last completed amount for {client.name}: ₹{planned_amount:,.2f} (no schedule)")
                        else:
                            # No completed investment found - skip this client
                            print(f"  ⏭️  Skipping {client.name}: No schedule and no completed investment found")
                            continue
                    
                    # Validate amount against schedule rules before creating
                    from services.investment_cycle import validate_amount_for_schedule
                    if not validate_amount_for_schedule(planned_amount, schedule):
                        print(f"  - Skipping {client.name}: Amount {planned_amount} not allowed by schedule rules")
                        continue
                    
                    # Create new monthly investment (portfolio_id required — see monthly_investment table)
                    from services.monthly_investment_portfolio_service import (
                        get_or_create_portfolio_for_client,
                    )

                    portfolio = get_or_create_portfolio_for_client(client.id, 1)
                    new_investment = MonthlyInvestment(
                        client_id=client.id,
                        portfolio_id=portfolio.id,
                        planned_amount=planned_amount,
                        investment_date=investment_date,
                        status='PENDING',
                        created_by=1  # Default user ID
                    )
                    db.session.add(new_investment)
                    db.session.flush()  # Get the ID
                    
                    # Create new workflow (use FUNDS stage to match auto-creation logic)
                    new_workflow = Workflow(
                        monthly_investment_id=new_investment.id,
                        current_stage='FUNDS',
                        planned_amount=planned_amount,
                        investment_date=investment_date,
                        target_completion_date=investment_date + timedelta(days=30),
                        created_by=1  # Default user ID
                    )
                    db.session.add(new_workflow)
                    new_investments_created += 1
                    print(f"  - Created new investment for {client.name}: ₹{planned_amount:,.2f}")
            
            # Step 3b: Create workflows for existing monthly investments without workflows
            print()
            print("Step 3b: Creating workflows for existing monthly investments without workflows...")
            if target_month_date.month == 12:
                target_month_end = date(target_month_date.year + 1, 1, 1)
            else:
                target_month_end = date(target_month_date.year, target_month_date.month + 1, 1)
            investments_without_workflows = MonthlyInvestment.query.filter(
                MonthlyInvestment.investment_date >= target_month_date,
                MonthlyInvestment.investment_date < target_month_end,
                MonthlyInvestment.status.in_(['PENDING', 'IN_PROGRESS'])
            ).outerjoin(Workflow).filter(Workflow.id.is_(None)).all()
            
            workflows_created_for_existing = 0
            for investment in investments_without_workflows:
                # Only skip if client has pending workflows for current/next month
                if investment.client_id in clients_with_pending_workflows_current:
                    print(f"  ⏭️  Skipping investment {investment.id} for client {investment.client.name}: Client has pending workflow(s) for current/next month")
                    continue
                
                # Create workflow for this investment
                new_workflow = Workflow(
                    monthly_investment_id=investment.id,
                    current_stage='FUNDS',
                    planned_amount=investment.planned_amount,
                    investment_date=investment.investment_date,
                    target_completion_date=investment.investment_date + timedelta(days=30),
                    created_by=1  # Default user ID
                )
                db.session.add(new_workflow)
                workflows_created_for_existing += 1
                print(f"  - Created workflow for existing investment {investment.id} (Client: {investment.client.name}, Date: {investment.investment_date})")
            
            # Step 4: Summary of pending workflows
            print("Step 4: Summary of pending workflows...")
            if all_pending_workflows:
                print(f"  📋 Total pending workflows: {len(all_pending_workflows)}")
                print()
                print("  Clients with pending workflows (new workflows NOT created):")
                clients_with_pending = {}
                for wf in all_pending_workflows:
                    if wf.monthly_investment and wf.monthly_investment.client:
                        client_name = wf.monthly_investment.client.name
                        if client_name not in clients_with_pending:
                            clients_with_pending[client_name] = []
                        updated_date = wf.updated_at.strftime('%Y-%m-%d') if wf.updated_at else 'N/A'
                        clients_with_pending[client_name].append({
                            'id': wf.id,
                            'stage': wf.current_stage,
                            'updated': updated_date
                        })
                
                for client_name, workflows in sorted(clients_with_pending.items()):
                    print(f"    - {client_name}: {len(workflows)} pending workflow(s)")
                    for wf_info in workflows:
                        print(f"        • Workflow {wf_info['id']}: Stage {wf_info['stage']} (Updated: {wf_info['updated']})")
            else:
                print("  ✅ No pending workflows")
            
            # Commit all changes
            db.session.commit()
            
            # Prepare summary statistics
            summary_stats = {
                'archived_workflows': archived_count,
                'new_investments_created': new_investments_created,
                'workflows_created_for_existing': workflows_created_for_existing,
                'skipped_due_to_pending': skipped_due_to_pending,
                'total_pending_workflows': len(all_pending_workflows),
                'clients_with_completed_workflows': len(clients_with_completed_workflows),
                'clients_with_active_schedules': len(clients_with_active_schedules),
                'total_clients_processed': len(clients_to_process),
                'target_month': target_month_date.strftime('%Y-%m'),
                'previous_month': previous_month_start.strftime('%Y-%m')
            }
            
            print()
            print("="*70)
            print(f"✅ NEXT MONTH CYCLE PREPARATION COMPLETED!")
            print("="*70)
            print(f"  - Archived {archived_count} completed workflows")
            print(f"  - Created {new_investments_created} new monthly investments with workflows")
            print(f"  - Created {workflows_created_for_existing} workflows for existing investments")
            print(f"  - Skipped {skipped_due_to_pending} clients (have pending workflows)")
            print(f"  - {len(all_pending_workflows)} pending workflows remain active")
            if all_pending_workflows:
                print()
                print("  ⚠️  IMPORTANT: Clients with pending workflows must complete them")
                print("     before new workflows can be created for them.")
            print("="*70)
            return True, summary_stats
            
        except Exception as e:
            print(f"❌ Error preparing next month cycle: {e}")
            db.session.rollback()
            return False, {'error': str(e)}

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
