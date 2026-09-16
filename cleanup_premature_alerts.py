#!/usr/bin/env python3
"""
Script to clean up premature workflow alerts.
Alerts for workflows with future investment dates (more than 7 days away) 
will be resolved as they were created too early.
"""
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import create_app
from alert_service import AlertService

def main():
    """Run the cleanup of premature alerts"""
    app = create_app()
    
    with app.app_context():
        print("=" * 70)
        print("CLEANING UP PREMATURE WORKFLOW ALERTS")
        print("=" * 70)
        print()
        print("This script will resolve alerts for workflows with investment dates")
        print("more than 2 days in the future, as these alerts were created too early.")
        print()
        
        resolved_count = AlertService.cleanup_premature_workflow_alerts()
        
        print()
        print("=" * 70)
        print(f"Cleanup completed: {resolved_count} alerts resolved")
        print("=" * 70)

if __name__ == '__main__':
    main()

