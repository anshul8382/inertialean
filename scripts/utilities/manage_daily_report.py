#!/usr/bin/env python3
"""
Daily Workflow Report Management Script
Provides easy commands to test, view logs, and manage the daily report.
"""

import sys
import os
import subprocess
from datetime import datetime

def show_help():
    """Show help information"""
    print("""
Daily Workflow Report Management

Usage: python3 manage_daily_report.py [command]

Commands:
    test        - Test the daily report (send immediately)
    logs        - View the latest report logs
    status      - Check cron job status
    setup       - Setup the cron job
    remove      - Remove the cron job
    help        - Show this help message

Examples:
    python3 manage_daily_report.py test
    python3 manage_daily_report.py logs
    python3 manage_daily_report.py status
    """)

def test_report():
    """Test the daily report"""
    print("🧪 Testing daily workflow report...")
    try:
        result = subprocess.run(['python3', 'daily_workflow_report.py'], 
                              capture_output=True, text=True, cwd=os.getcwd())
        print(result.stdout)
        if result.stderr:
            print("Errors:", result.stderr)
    except Exception as e:
        print(f"❌ Error testing report: {e}")

def view_logs():
    """View the latest report logs"""
    log_file = "daily_report.log"
    if os.path.exists(log_file):
        print(f"📋 Latest logs from {log_file}:")
        print("-" * 50)
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()
                # Show last 20 lines
                for line in lines[-20:]:
                    print(line.rstrip())
        except Exception as e:
            print(f"❌ Error reading logs: {e}")
    else:
        print("📝 No log file found. Run a test first.")

def check_status():
    """Check cron job status"""
    print("🔍 Checking cron job status...")
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        if 'daily_workflow_report.py' in result.stdout:
            print("✅ Daily report cron job is active")
            for line in result.stdout.split('\n'):
                if 'daily_workflow_report.py' in line:
                    print(f"📅 Schedule: {line.strip()}")
        else:
            print("❌ Daily report cron job not found")
    except Exception as e:
        print(f"❌ Error checking status: {e}")

def setup_cron():
    """Setup the cron job"""
    print("⚙️ Setting up cron job...")
    try:
        subprocess.run(['./setup_daily_report_cron.sh'], check=True)
    except Exception as e:
        print(f"❌ Error setting up cron job: {e}")

def remove_cron():
    """Remove the cron job"""
    print("🗑️ Removing cron job...")
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        if result.returncode == 0:
            # Remove the daily report line
            lines = result.stdout.split('\n')
            filtered_lines = [line for line in lines if 'daily_workflow_report.py' not in line]
            
            # Write back the filtered crontab
            temp_file = '/tmp/crontab_temp'
            with open(temp_file, 'w') as f:
                f.write('\n'.join(filtered_lines))
            
            subprocess.run(['crontab', temp_file], check=True)
            os.remove(temp_file)
            print("✅ Cron job removed successfully")
        else:
            print("❌ No existing crontab found")
    except Exception as e:
        print(f"❌ Error removing cron job: {e}")

def main():
    """Main function"""
    if len(sys.argv) < 2:
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == 'test':
        test_report()
    elif command == 'logs':
        view_logs()
    elif command == 'status':
        check_status()
    elif command == 'setup':
        setup_cron()
    elif command == 'remove':
        remove_cron()
    elif command == 'help':
        show_help()
    else:
        print(f"❌ Unknown command: {command}")
        show_help()

if __name__ == "__main__":
    main()

