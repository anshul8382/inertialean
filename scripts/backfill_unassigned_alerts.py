#!/usr/bin/env python3
"""
Backfill script to assign all unassigned alerts to the ops manager.
Run this script to fix existing alerts that have user_id = NULL.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from alert_system_models import Alert
from alert_service import AlertService
from models import db
from datetime import datetime

def backfill_unassigned_alerts():
    """Assign all unassigned alerts to ops manager"""
    app = create_app()
    
    with app.app_context():
        try:
            # Get ops manager user ID
            ops_manager_id = AlertService._get_ops_manager_user_id()
            
            if not ops_manager_id:
                print("ERROR: Could not find ops manager user. Please ensure there is at least one user with 'manager' role.")
                return
            
            # Find all unassigned active alerts
            unassigned_alerts = Alert.query.filter(
                Alert.user_id.is_(None),
                Alert.status == 'active'
            ).all()
            
            print(f"Found {len(unassigned_alerts)} unassigned active alerts")
            
            if len(unassigned_alerts) == 0:
                print("No unassigned alerts to fix.")
                return
            
            # Update all unassigned alerts
            updated_count = 0
            for alert in unassigned_alerts:
                alert.user_id = ops_manager_id
                updated_count += 1
                print(f"  Assigned alert {alert.id} ({alert.alert_type}/{alert.alert_subtype}) to ops manager")
            
            # Commit changes
            db.session.commit()
            print(f"\n✓ Successfully assigned {updated_count} alerts to ops manager (user_id: {ops_manager_id})")
            
            # Also check for resolved/acknowledged alerts that might be unassigned
            unassigned_other = Alert.query.filter(
                Alert.user_id.is_(None),
                Alert.status.in_(['resolved', 'acknowledged'])
            ).count()
            
            if unassigned_other > 0:
                print(f"\nNote: There are {unassigned_other} resolved/acknowledged alerts that are also unassigned.")
                print("   These were not updated, but you can run this script again with different filters if needed.")
            
        except Exception as e:
            db.session.rollback()
            print(f"ERROR: Failed to backfill alerts: {str(e)}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

if __name__ == '__main__':
    print("=" * 60)
    print("Backfill Unassigned Alerts Script")
    print("=" * 60)
    print(f"Started at: {datetime.now()}")
    print()
    
    backfill_unassigned_alerts()
    
    print()
    print("=" * 60)
    print("Backfill completed!")
    print("=" * 60)

