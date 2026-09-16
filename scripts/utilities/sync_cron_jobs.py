#!/usr/bin/env python3
"""
Cron Job Sync System
Syncs database-stored cron schedules with the actual system crontab
"""

import os
import sys
import subprocess
import tempfile
from datetime import datetime

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from main import create_app
from models import CronSchedule, db

def get_current_crontab():
    """Get the current crontab content"""
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout
        else:
            return ""
    except Exception as e:
        print(f"Error getting current crontab: {e}")
        return ""

def remove_app_cron_jobs(crontab_content):
    """Remove existing app cron jobs from crontab content"""
    lines = crontab_content.split('\n')
    filtered_lines = []
    
    for line in lines:
        # Skip lines that contain our app scripts
        if ('daily_workflow_report.py' in line or 
            'daily_leads_report.py' in line or 
            'daily_monthly_investments_report.py' in line):
            continue
        filtered_lines.append(line)
    
    return '\n'.join(filtered_lines)

def generate_cron_job_line(job_id, schedule, script_path):
    """Generate a cron job line for a specific job"""
    log_file = f"logs/{job_id}.log"
    if job_id == 'daily_workflow_report':
        log_file = "daily_report.log"
    
    return f"{schedule} cd /home/inertia/app && /usr/bin/python3 {script_path} >> /home/inertia/app/{log_file} 2>&1"

def sync_cron_jobs():
    """Sync database schedules with system crontab"""
    try:
        app = create_app()
        
        with app.app_context():
            # Get all active cron schedules from database
            schedules = CronSchedule.query.filter_by(is_active=True).all()
            
            if not schedules:
                print("No active cron schedules found in database")
                return False
            
            # Get current crontab
            current_crontab = get_current_crontab()
            
            # Remove existing app cron jobs
            clean_crontab = remove_app_cron_jobs(current_crontab)
            
            # Add new cron jobs from database
            new_cron_jobs = []
            script_mapping = {
                'daily_workflow_report': '/home/inertia/app/daily_workflow_report.py',
                'daily_leads_report': '/home/inertia/app/daily_leads_report.py',
                'daily_monthly_investments_report': '/home/inertia/app/daily_monthly_investments_report.py'
            }
            
            for schedule in schedules:
                if schedule.job_id in script_mapping:
                    cron_line = generate_cron_job_line(
                        schedule.job_id, 
                        schedule.schedule, 
                        script_mapping[schedule.job_id]
                    )
                    new_cron_jobs.append(cron_line)
                    print(f"Added cron job: {schedule.job_id} - {schedule.schedule}")
            
            # Combine clean crontab with new jobs
            if clean_crontab.strip():
                new_crontab = clean_crontab.rstrip() + '\n' + '\n'.join(new_cron_jobs) + '\n'
            else:
                new_crontab = '\n'.join(new_cron_jobs) + '\n'
            
            # Write to temporary file and install
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                f.write(new_crontab)
                temp_file = f.name
            
            try:
                # Install new crontab
                result = subprocess.run(['crontab', temp_file], capture_output=True, text=True)
                if result.returncode == 0:
                    print("✅ Crontab updated successfully!")
                    print(f"Synced {len(new_cron_jobs)} cron jobs from database")
                    return True
                else:
                    print(f"❌ Error updating crontab: {result.stderr}")
                    return False
            finally:
                # Clean up temp file
                os.unlink(temp_file)
                
    except Exception as e:
        print(f"❌ Error syncing cron jobs: {e}")
        return False

def main():
    """Main function"""
    print("🔄 Syncing cron jobs from database...")
    success = sync_cron_jobs()
    
    if success:
        print("✅ Cron job sync completed successfully!")
    else:
        print("❌ Cron job sync failed!")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
