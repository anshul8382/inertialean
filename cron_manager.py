#!/usr/bin/env python3
"""
Cron Job Manager for Inertia Investment Management System
Manages all application-related cron jobs through a centralized system
"""

import os
import subprocess
import json
from datetime import datetime
from typing import List, Dict, Optional

class CronJobManager:
    """Manages cron jobs for the Inertia application"""
    
    def __init__(self):
        self.app_dir = "/home/inertia/app"
        self.cron_jobs = {
            "daily_workflow_report": {
                "name": "Daily Workflow Report",
                "description": "Generates and sends daily workflow status report",
                "script": "daily_workflow_report.py",
                "schedule": "0 9 * * *",  # 9:00 AM IST
                "log_file": "daily_report.log",
                "enabled": True,
                "category": "Reports"
            },
            "daily_leads_report": {
                "name": "Daily Leads Report", 
                "description": "Generates and sends daily leads report bucketed by status",
                "script": "daily_leads_report.py",
                "schedule": "30 0 * * *",  # 6:00 AM IST
                "log_file": "logs/daily_leads_report.log",
                "enabled": True,
                "category": "Reports"
            },
            "daily_monthly_investments_report": {
                "name": "Daily Monthly Investments Report",
                "description": "Generates and sends daily pending investments report",
                "script": "daily_monthly_investments_report.py", 
                "schedule": "35 0 * * *",  # 6:05 AM IST
                "log_file": "logs/daily_monthly_investments_report.log",
                "enabled": True,
                "category": "Reports"
            },
            "hourly_sla_check": {
                "name": "Hourly SLA Check",
                "description": "Checks for SLA violations and generates alerts",
                "script": "manual_sla_check.py",
                "schedule": "0 * * * *",  # Every hour
                "log_file": "alert_system.log",
                "enabled": True,
                "category": "Monitoring"
            },
            "daily_alert_report": {
                "name": "Daily Alert Report",
                "description": "Generates daily alert summary report",
                "script": "alert_service.py",
                "schedule": "0 8 * * *",  # 8:00 AM IST
                "log_file": "alert_report.log",
                "enabled": True,
                "category": "Monitoring",
                "python_command": True,
                "custom_command": "from alert_service import AlertService; from main import create_app; app = create_app(); app.app_context().push(); AlertService.generate_daily_alert_report()"
            }
        }
    
    def get_current_cron_jobs(self) -> List[Dict]:
        """Get all current cron jobs from the system"""
        try:
            result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
            if result.returncode != 0:
                return []
            
            cron_lines = result.stdout.strip().split('\n')
            app_jobs = []
            
            for line in cron_lines:
                if self.app_dir in line:
                    # Parse cron job line
                    parts = line.strip().split()
                    if len(parts) >= 6:
                        schedule = ' '.join(parts[:5])
                        command = ' '.join(parts[5:])
                        
                        # Extract job info
                        job_info = {
                            'schedule': schedule,
                            'command': command,
                            'raw_line': line.strip()
                        }
                        
                        # Try to match with known jobs
                        for job_id, job_config in self.cron_jobs.items():
                            if job_config['script'] in command:
                                job_info['job_id'] = job_id
                                job_info['name'] = job_config['name']
                                job_info['description'] = job_config['description']
                                job_info['category'] = job_config['category']
                                job_info['enabled'] = True
                                break
                        
                        app_jobs.append(job_info)
            
            return app_jobs
            
        except Exception as e:
            print(f"Error getting current cron jobs: {str(e)}")
            return []
    
    def add_cron_job(self, job_id: str, schedule: str) -> bool:
        """Add a cron job to the system"""
        try:
            if job_id not in self.cron_jobs:
                return False
            
            job_config = self.cron_jobs[job_id]
            
            # Create log directory if needed
            log_dir = os.path.dirname(os.path.join(self.app_dir, job_config['log_file']))
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            
            # Build cron command
            if job_config.get('custom_command'):
                # For jobs with custom Python commands
                cron_command = f"{schedule} cd {self.app_dir} && python3 -c \"{job_config['custom_command']}\" >> {self.app_dir}/{job_config['log_file']} 2>&1"
            elif job_config.get('python_command'):
                # For jobs that need to run Python code directly
                cron_command = f"{schedule} cd {self.app_dir} && python3 -c \"from {job_config['script'].replace('.py', '')} import *; main()\" >> {self.app_dir}/{job_config['log_file']} 2>&1"
            else:
                # For regular Python scripts
                cron_command = f"{schedule} cd {self.app_dir} && /usr/bin/python3 {self.app_dir}/{job_config['script']} >> {self.app_dir}/{job_config['log_file']} 2>&1"
            
            # Get current cron jobs
            current_jobs = self.get_current_cron_jobs()
            
            # Remove existing job if it exists
            self.remove_cron_job(job_id)
            
            # Add new job
            result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
            current_crontab = result.stdout if result.returncode == 0 else ""
            
            new_crontab = current_crontab.strip() + "\n" + cron_command + "\n"
            
            # Write new crontab
            with open('/tmp/new_crontab', 'w') as f:
                f.write(new_crontab)
            
            result = subprocess.run(['crontab', '/tmp/new_crontab'], capture_output=True, text=True)
            
            # Clean up
            if os.path.exists('/tmp/new_crontab'):
                os.remove('/tmp/new_crontab')
            
            return result.returncode == 0
            
        except Exception as e:
            print(f"Error adding cron job {job_id}: {str(e)}")
            return False
    
    def remove_cron_job(self, job_id: str) -> bool:
        """Remove a cron job from the system"""
        try:
            if job_id not in self.cron_jobs:
                return False
            
            job_config = self.cron_jobs[job_id]
            
            # Get current cron jobs
            result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
            if result.returncode != 0:
                return True  # No crontab exists, so job is already removed
            
            cron_lines = result.stdout.strip().split('\n')
            filtered_lines = []
            
            for line in cron_lines:
                # Keep lines that don't contain this job's script
                if job_config['script'] not in line:
                    filtered_lines.append(line)
            
            # Write filtered crontab
            new_crontab = '\n'.join(filtered_lines) + '\n'
            
            with open('/tmp/filtered_crontab', 'w') as f:
                f.write(new_crontab)
            
            result = subprocess.run(['crontab', '/tmp/filtered_crontab'], capture_output=True, text=True)
            
            # Clean up
            if os.path.exists('/tmp/filtered_crontab'):
                os.remove('/tmp/filtered_crontab')
            
            return result.returncode == 0
            
        except Exception as e:
            print(f"Error removing cron job {job_id}: {str(e)}")
            return False
    
    def update_cron_job_schedule(self, job_id: str, new_schedule: str) -> bool:
        """Update the schedule of an existing cron job"""
        try:
            # Remove existing job
            if not self.remove_cron_job(job_id):
                return False
            
            # Add job with new schedule
            return self.add_cron_job(job_id, new_schedule)
            
        except Exception as e:
            print(f"Error updating cron job schedule {job_id}: {str(e)}")
            return False
    
    def enable_cron_job(self, job_id: str) -> bool:
        """Enable a cron job"""
        try:
            if job_id not in self.cron_jobs:
                return False
            
            job_config = self.cron_jobs[job_id]
            return self.add_cron_job(job_id, job_config['schedule'])
            
        except Exception as e:
            print(f"Error enabling cron job {job_id}: {str(e)}")
            return False
    
    def disable_cron_job(self, job_id: str) -> bool:
        """Disable a cron job"""
        try:
            return self.remove_cron_job(job_id)
            
        except Exception as e:
            print(f"Error disabling cron job {job_id}: {str(e)}")
            return False
    
    def get_job_status(self, job_id: str) -> Dict:
        """Get the current status of a cron job"""
        try:
            if job_id not in self.cron_jobs:
                return {'enabled': False, 'error': 'Job not found'}
            
            job_config = self.cron_jobs[job_id]
            current_jobs = self.get_current_cron_jobs()
            
            # Check if job is currently active
            is_enabled = any(job.get('job_id') == job_id for job in current_jobs)
            
            # Get last log entry
            log_file = os.path.join(self.app_dir, job_config['log_file'])
            last_log = None
            if os.path.exists(log_file):
                try:
                    with open(log_file, 'r') as f:
                        lines = f.readlines()
                        if lines:
                            last_log = lines[-1].strip()
                except:
                    pass
            
            return {
                'enabled': is_enabled,
                'schedule': job_config['schedule'],
                'last_log': last_log,
                'log_file': job_config['log_file']
            }
            
        except Exception as e:
            return {'enabled': False, 'error': str(e)}
    
    def get_all_jobs_status(self) -> Dict:
        """Get status of all cron jobs"""
        try:
            current_jobs = self.get_current_cron_jobs()
            status = {}
            
            for job_id, job_config in self.cron_jobs.items():
                is_enabled = any(job.get('job_id') == job_id for job in current_jobs)
                
                # Get last log entry
                log_file = os.path.join(self.app_dir, job_config['log_file'])
                last_log = None
                if os.path.exists(log_file):
                    try:
                        with open(log_file, 'r') as f:
                            lines = f.readlines()
                            if lines:
                                last_log = lines[-1].strip()
                    except:
                        pass
                
                status[job_id] = {
                    'name': job_config['name'],
                    'description': job_config['description'],
                    'category': job_config['category'],
                    'schedule': job_config['schedule'],
                    'enabled': is_enabled,
                    'last_log': last_log,
                    'log_file': job_config['log_file']
                }
            
            return status
            
        except Exception as e:
            print(f"Error getting all jobs status: {str(e)}")
            return {}
    
    def test_cron_job(self, job_id: str) -> Dict:
        """Test run a cron job manually"""
        try:
            if job_id not in self.cron_jobs:
                return {'success': False, 'error': 'Job not found'}
            
            job_config = self.cron_jobs[job_id]
            script_path = os.path.join(self.app_dir, job_config['script'])
            
            if not os.path.exists(script_path):
                return {'success': False, 'error': f'Script not found: {script_path}'}
            
            # Run the script
            result = subprocess.run(
                ['python3', script_path],
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            return {
                'success': result.returncode == 0,
                'output': result.stdout,
                'error': result.stderr,
                'return_code': result.returncode
            }
            
        except subprocess.TimeoutExpired:
            return {'success': False, 'error': 'Job timed out after 5 minutes'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_schedule_description(self, schedule: str) -> str:
        """Convert cron schedule to human readable description"""
        try:
            parts = schedule.split()
            if len(parts) != 5:
                return "Invalid schedule"
            
            minute, hour, day, month, weekday = parts
            
            if minute == "*" and hour == "*" and day == "*" and month == "*" and weekday == "*":
                return "Every minute"
            elif minute != "*" and hour == "*" and day == "*" and month == "*" and weekday == "*":
                return f"Every {minute} minutes"
            elif minute != "*" and hour != "*" and day == "*" and month == "*" and weekday == "*":
                return f"Every day at {hour}:{minute.zfill(2)}"
            elif minute != "*" and hour != "*" and day != "*" and month == "*" and weekday == "*":
                return f"Every {day} of month at {hour}:{minute.zfill(2)}"
            else:
                return f"Custom: {schedule}"
                
        except:
            return "Invalid schedule"

# Global instance
cron_manager = CronJobManager()
