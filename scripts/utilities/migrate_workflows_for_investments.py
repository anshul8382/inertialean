#!/usr/bin/env python3
"""
Workflow Migration Script
Creates workflows for monthly investments that are missing workflow IDs
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

def create_workflow_for_investment(investment, admin_user):
    """Create a workflow for a monthly investment"""
    
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

def migrate_workflows():
    """Main migration function"""
    try:
        app = create_app()
        
        with app.app_context():
            # Get admin user (assuming user ID 1 is admin)
            admin_user = User.query.get(1)
            if not admin_user:
                print("❌ Admin user not found. Please ensure user ID 1 exists.")
                return False
            
            # Find monthly investments without workflows
            investments_without_workflow = MonthlyInvestment.query.filter(
                MonthlyInvestment.workflow_id.is_(None)
            ).all()
            
            if not investments_without_workflow:
                print("✅ All monthly investments already have workflows!")
                return True
            
            print(f"📊 Found {len(investments_without_workflow)} monthly investments without workflows")
            
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
                    workflow = create_workflow_for_investment(investment, admin_user)
                    db.session.add(workflow)
                    
                    # Update the investment's workflow_id
                    investment.workflow_id = workflow.id
                    
                    created_count += 1
                    print(f"✅ Created workflow for investment {investment.id} (Client: {investment.client.name if investment.client else 'No Client'}, Amount: {investment.planned_amount})")
                    
                except Exception as e:
                    failed_count += 1
                    print(f"❌ Failed to create workflow for investment {investment.id}: {str(e)}")
                    continue
            
            # Commit all changes
            db.session.commit()
            
            print(f"\n📈 Migration Summary:")
            print(f"  ✅ Workflows created: {created_count}")
            print(f"  ❌ Failed: {failed_count}")
            print(f"  📊 Total processed: {len(investments_without_workflow)}")
            
            if created_count > 0:
                print(f"\n🎉 Successfully migrated {created_count} workflows!")
                
                # Verify the migration
                remaining_without_workflow = MonthlyInvestment.query.filter(
                    MonthlyInvestment.workflow_id.is_(None)
                ).count()
                print(f"📋 Remaining investments without workflows: {remaining_without_workflow}")
                
                return True
            else:
                print("⚠️ No workflows were created")
                return False
                
    except Exception as e:
        print(f"❌ Migration failed: {str(e)}")
        db.session.rollback()
        return False

def verify_migration():
    """Verify the migration results"""
    try:
        app = create_app()
        
        with app.app_context():
            total_investments = MonthlyInvestment.query.count()
            investments_with_workflow = MonthlyInvestment.query.filter(
                MonthlyInvestment.workflow_id.isnot(None)
            ).count()
            investments_without_workflow = MonthlyInvestment.query.filter(
                MonthlyInvestment.workflow_id.is_(None)
            ).count()
            
            print(f"\n📊 Verification Results:")
            print(f"  Total monthly investments: {total_investments}")
            print(f"  With workflows: {investments_with_workflow}")
            print(f"  Without workflows: {investments_without_workflow}")
            print(f"  Coverage: {(investments_with_workflow/total_investments*100):.1f}%")
            
            if investments_without_workflow == 0:
                print("✅ All monthly investments now have workflows!")
            else:
                print(f"⚠️ {investments_without_workflow} investments still missing workflows")
                
    except Exception as e:
        print(f"❌ Verification failed: {str(e)}")

def main():
    """Main function"""
    print("🔄 Starting workflow migration for monthly investments...")
    print("=" * 60)
    
    # Run migration
    success = migrate_workflows()
    
    if success:
        print("\n" + "=" * 60)
        verify_migration()
        print("\n✅ Migration completed successfully!")
    else:
        print("\n❌ Migration failed!")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

