#!/usr/bin/env python3
"""
Create Missing Workflows
Creates basic workflows for monthly investments that don't have them
"""

import sys
import os
from datetime import datetime, timedelta

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from main import create_app
from models import MonthlyInvestment, Workflow, User, db

def create_basic_workflow_for_investment(investment, admin_user):
    """Create a basic workflow for a monthly investment"""
    
    # Calculate target completion date (7 days from investment date)
    target_date = investment.investment_date + timedelta(days=7)
    
    # Create workflow
    workflow = Workflow(
        monthly_investment_id=investment.id,
        current_stage='FUNDS',  # Start with FUNDS stage
        planned_amount=investment.planned_amount,
        actual_amount=None,
        investment_date=investment.investment_date,
        target_completion_date=target_date,
        actual_completion_date=None,
        notes=f"Auto-created workflow for investment {investment.id}",
        created_by=admin_user.id,
        is_archived=False
    )
    
    return workflow

def main():
    """Main function"""
    print("🔄 Creating missing workflows for monthly investments...")
    print("=" * 60)
    
    try:
        app = create_app()
        
        with app.app_context():
            # Get admin user (assuming user ID 1 is admin)
            admin_user = User.query.get(1)
            if not admin_user:
                print("❌ Admin user not found. Please ensure user ID 1 exists.")
                return 1
            
            # Find monthly investments without workflows
            investments_without_workflow = MonthlyInvestment.query.filter(
                MonthlyInvestment.workflow_id.is_(None)
            ).all()
            
            if not investments_without_workflow:
                print("✅ All monthly investments already have workflows!")
                return 0
            
            print(f"📊 Found {len(investments_without_workflow)} monthly investments without workflows")
            
            # Show sample investments
            print("\\n📋 Sample investments without workflows:")
            for i, investment in enumerate(investments_without_workflow[:5], 1):
                client_name = investment.client.name if investment.client else "No Client"
                print(f"  {i}. ID: {investment.id}, Client: {client_name}, Amount: {investment.planned_amount}, Date: {investment.investment_date}")
            
            if len(investments_without_workflow) > 5:
                print(f"  ... and {len(investments_without_workflow) - 5} more")
            
            # Ask for confirmation
            confirm = input(f"\\nCreate workflows for all {len(investments_without_workflow)} investments? (y/N): ").strip().lower()
            if confirm != 'y':
                print("👋 Operation cancelled")
                return 0
            
            # Create workflows for each investment
            created_count = 0
            failed_count = 0
            
            for investment in investments_without_workflow:
                try:
                    # Check if workflow already exists (double-check)
                    existing_workflow = Workflow.query.filter_by(
                        monthly_investment_id=investment.id
                    ).first()
                    
                    if existing_workflow:
                        print(f"⚠️ Workflow already exists for investment {investment.id}, skipping")
                        continue
                    
                    # Create workflow
                    workflow = create_basic_workflow_for_investment(investment, admin_user)
                    db.session.add(workflow)
                    
                    # Update the investment's workflow_id
                    investment.workflow_id = workflow.id
                    
                    created_count += 1
                    client_name = investment.client.name if investment.client else "No Client"
                    print(f"✅ Created workflow for investment {investment.id} (Client: {client_name}, Amount: {investment.planned_amount})")
                    
                except Exception as e:
                    failed_count += 1
                    print(f"❌ Failed to create workflow for investment {investment.id}: {str(e)}")
                    continue
            
            # Commit all changes
            db.session.commit()
            
            print(f"\\n📈 Summary:")
            print(f"  ✅ Workflows created: {created_count}")
            print(f"  ❌ Failed: {failed_count}")
            print(f"  📊 Total processed: {len(investments_without_workflow)}")
            
            if created_count > 0:
                print(f"\\n🎉 Successfully created {created_count} workflows!")
                
                # Verify the results
                remaining_without_workflow = MonthlyInvestment.query.filter(
                    MonthlyInvestment.workflow_id.is_(None)
                ).count()
                print(f"📋 Remaining investments without workflows: {remaining_without_workflow}")
                
                return 0
            else:
                print("⚠️ No workflows were created")
                return 1
                
    except Exception as e:
        print(f"❌ Operation failed: {str(e)}")
        db.session.rollback()
        return 1

if __name__ == "__main__":
    sys.exit(main())

