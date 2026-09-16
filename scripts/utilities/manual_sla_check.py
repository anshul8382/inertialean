#!/usr/bin/env python3
"""
Manual SLA Check Script
Run this script via cron job to check for SLA violations and create alerts
"""

import sys
import os
from datetime import datetime, timedelta

# Add the app directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from alert_service import AlertService
from models import db
from main import create_app

def run_manual_sla_check():
    """Run SLA checks manually - can be called via cron job"""
    try:
        print(f"Starting manual SLA check at {datetime.now()}")
        
        app = create_app()
        with app.app_context():
            # Check workflow SLAs
            workflow_alerts = AlertService.check_workflow_sla()
            print(f"Workflow SLA check completed. Alerts created: {len(workflow_alerts) if workflow_alerts else 0}")
            
            # Check recommendation SLAs
            recommendation_alerts = AlertService.check_recommendation_sla()
            print(f"Recommendation SLA check completed. Alerts created: {len(recommendation_alerts) if recommendation_alerts else 0}")
            
            # Check meeting reminders
            AlertService.check_meeting_reminders()
            print("Meeting reminder check completed")
            
            # Generate daily alert report (only if it's 8 AM)
            if datetime.now().hour == 8:
                AlertService.generate_daily_alert_report()
                print("Daily alert report generated")
            
            print(f"SLA check completed successfully at {datetime.now()}")
        
    except Exception as e:
        print(f"Error during SLA check: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_manual_sla_check()
