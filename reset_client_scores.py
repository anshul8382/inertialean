#!/usr/bin/env python3
"""
Script to reset all client health scores to 100 by acknowledging all active alerts
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import create_app
from models import db, Client
from alert_system_models import Alert
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reset_client_scores():
    """Acknowledge all active alerts to reset client health scores to 100"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Resetting Client Health Scores to 100 ===")
        
        # Get all active alerts
        active_alerts = Alert.query.filter_by(status='active').all()
        
        print(f"Found {len(active_alerts)} active alerts")
        
        if len(active_alerts) == 0:
            print("✅ No active alerts found. All client scores are already at 100.")
            return
        
        # Get system user ID (use ops manager or admin, or default to 1)
        from models import User
        system_user = User.query.filter(
            (User.is_admin == True) | (User.role == 'manager')
        ).first()
        
        system_user_id = system_user.id if system_user else 1
        
        print(f"Using system user ID: {system_user_id}")
        
        # Acknowledge all active alerts
        acknowledged_count = 0
        for alert in active_alerts:
            try:
                alert.acknowledge(system_user_id)
                acknowledged_count += 1
            except Exception as e:
                logger.error(f"Error acknowledging alert {alert.id}: {str(e)}")
        
        try:
            db.session.commit()
            print(f"✅ Successfully acknowledged {acknowledged_count} alerts")
            print(f"✅ All client health scores should now be at 100 (or close to it)")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error committing changes: {str(e)}")
            print(f"❌ Error: {str(e)}")

if __name__ == '__main__':
    reset_client_scores()



