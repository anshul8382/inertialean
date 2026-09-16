#!/usr/bin/env python3
"""
Temporary script to clean up workflow alerts.
Keeps only the latest workflow stage alert for each workflow and closes previous ones.

Usage:
    python scripts/cleanup_workflow_alerts.py
"""

import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from models import db, Workflow
from alert_system_models import Alert
from alert_service import AlertService
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cleanup_workflow_alerts():
    """Clean up workflow alerts - keep only latest stage alert per workflow"""
    app = create_app()
    
    with app.app_context():
        try:
            # Get all active workflows
            workflows = Workflow.query.filter(
                Workflow.current_stage != 'COMPLETED',
                Workflow.is_archived == False
            ).all()
            
            logger.info(f"Found {len(workflows)} active workflows to process")
            
            total_alerts_closed = 0
            workflows_processed = 0
            
            for workflow in workflows:
                # Get all active workflow_sla alerts for this workflow
                workflow_alerts = Alert.query.filter_by(
                    workflow_id=workflow.id,
                    alert_type='workflow_sla',
                    status='active'
                ).order_by(Alert.created_at.desc()).all()
                
                if not workflow_alerts:
                    continue
                
                workflows_processed += 1
                
                # Determine which alert should be kept (latest one for current stage)
                current_stage = workflow.current_stage
                current_stage_alert_subtype = f'{current_stage}_delay'
                
                # Find the latest alert for the current stage
                latest_alert = None
                for alert in workflow_alerts:
                    if alert.alert_subtype == current_stage_alert_subtype:
                        latest_alert = alert
                        break
                
                # If no alert for current stage, keep the most recent alert
                if not latest_alert:
                    latest_alert = workflow_alerts[0]  # Most recent alert
                    logger.info(f"  Workflow {workflow.id}: No alert for current stage {current_stage}, keeping most recent: {latest_alert.alert_subtype}")
                else:
                    logger.info(f"  Workflow {workflow.id}: Keeping latest alert for current stage {current_stage}: {latest_alert.alert_subtype}")
                
                # Close all other alerts
                alerts_to_close = [a for a in workflow_alerts if a.id != latest_alert.id]
                
                if alerts_to_close:
                    notes = f'Automatically closed by cleanup script: Keeping only latest stage alert ({latest_alert.alert_subtype})'
                    system_user_id = 1  # System user ID
                    
                    for alert in alerts_to_close:
                        try:
                            alert.resolve(system_user_id, notes)
                            total_alerts_closed += 1
                            logger.info(f"    Closed alert {alert.id}: {alert.alert_subtype} (created: {alert.created_at})")
                        except Exception as e:
                            logger.error(f"    Error closing alert {alert.id}: {str(e)}")
                    
                    db.session.commit()
                    logger.info(f"  Workflow {workflow.id}: Closed {len(alerts_to_close)} alert(s), kept alert {latest_alert.id}")
                else:
                    logger.info(f"  Workflow {workflow.id}: No alerts to close (only one alert exists)")
            
            logger.info(f"\n{'='*60}")
            logger.info(f"Cleanup Summary:")
            logger.info(f"  Workflows processed: {workflows_processed}")
            logger.info(f"  Total alerts closed: {total_alerts_closed}")
            logger.info(f"{'='*60}")
            
            return {
                'workflows_processed': workflows_processed,
                'alerts_closed': total_alerts_closed
            }
            
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}", exc_info=True)
            db.session.rollback()
            raise

if __name__ == '__main__':
    print("="*60)
    print("Workflow Alerts Cleanup Script")
    print("="*60)
    print("This script will:")
    print("  1. Find all active workflows")
    print("  2. For each workflow, keep only the latest stage alert")
    print("  3. Close all other workflow alerts for that workflow")
    print("="*60)
    
    response = input("\nDo you want to proceed? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("Cleanup cancelled.")
        sys.exit(0)
    
    try:
        result = cleanup_workflow_alerts()
        print(f"\n✅ Cleanup completed successfully!")
        print(f"   Workflows processed: {result['workflows_processed']}")
        print(f"   Alerts closed: {result['alerts_closed']}")
    except Exception as e:
        print(f"\n❌ Cleanup failed: {str(e)}")
        sys.exit(1)

