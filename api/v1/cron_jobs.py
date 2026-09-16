#!/usr/bin/env python3
"""
Cron Jobs API Endpoints
Provides REST API endpoints to manually trigger all cron jobs
"""

import os
import sys
import subprocess
import traceback
from datetime import datetime
from flask import Blueprint, request, jsonify
from api.core import APIResponse, api_response
from api.core.exceptions import APIException, ValidationError

# Add the app directory to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

cron_jobs_bp = Blueprint('cron_jobs', __name__)


def _inertia_app_root() -> str:
    """Inertia repo root (parent of ``api/``). Airflow tasks often run with cwd ≠ app dir."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))


def _service_account_json_path() -> str:
    return os.path.join(_inertia_app_root(), "service_account.json")


# Cron job configurations
CRON_JOBS = {
    'daily_workflow_report': {
        'name': 'Daily Workflow Report',
        'description': 'Generates and sends daily workflow status report',
        'script': 'daily_workflow_report.py',
        'category': 'Reports',
        'schedule': '0 9 * * *',
        'log_file': 'daily_report.log',
        'recipients': ['anshul@equities4wealth.com']
    },
    'daily_leads_report': {
        'name': 'Daily Leads Report',
        'description': 'Generates and sends daily leads report bucketed by status',
        'script': 'daily_leads_report.py',
        'category': 'Reports',
        'schedule': '30 0 * * *',
        'log_file': 'logs/daily_leads_report.log',
        'recipients': ['onboarding@equities4wealth.com', 'anshul@equities4wealth.com']
    },
    'daily_monthly_investments_report': {
        'name': 'Daily Monthly Investments Report',
        'description': 'Generates and sends daily pending investments report',
        'script': 'daily_monthly_investments_report.py',
        'category': 'Reports',
        'schedule': '35 0 * * *',
        'log_file': 'logs/daily_monthly_investments_report.log',
        'recipients': ['onboarding@equities4wealth.com', 'anshul@equities4wealth.com']
    },
    'holdings_cycle_notifications': {
        'name': 'Holdings Cycle Notifications',
        'description': 'Sends weekly holdings processing reports (Monday, after Sunday processing)',
        'script': 'scripts/send_holdings_notifications.py',
        'category': 'Reports',
        'schedule': '30 21 * * 0',
        'log_file': 'logs/holdings_notifications.log',
        'recipients': ['anshul@equities4wealth.com']
    },
    'hourly_sla_check': {
        'name': 'Hourly SLA Check',
        'description': 'Checks for SLA violations and generates alerts',
        'script': 'manual_sla_check.py',
        'category': 'Monitoring',
        'schedule': '0 * * * *',
        'log_file': 'alert_system.log',
        'recipients': []
    },
    'daily_alert_report': {
        'name': 'Daily Alert Report',
        'description': 'Generates daily alert summary report',
        'script': 'alert_service.py',
        'category': 'Monitoring',
        'schedule': '0 8 * * *',
        'log_file': 'alert_report.log',
        'recipients': []
    },
    'price_update_sheets': {
        'name': 'Price Update from Google Sheets',
        'description': 'Updates security prices from Google Sheets at 9:30 AM, 12:30 PM, and 4 PM IST on weekdays. Historical prices saved only at 4 PM run.',
        'script': 'price_update_sheets.py',
        'category': 'Data Management',
        'schedule': '30 4,7,10 * * 1-5',  # UTC: ~10 AM, 1 PM, 4 PM IST (Airflow scheduler)
        'log_file': 'logs/price_update.log',
        'recipients': ['anshul@equities4wealth.com', 'service@equities4wealth.com'],
        'enabled': True
    },
    'nifty_price_check': {
        'name': 'Nifty Price Check',
        'description': 'Checks current Nifty price for calculations',
        'script': 'check_nifty_price.py',
        'category': 'Data Management',
        'schedule': 'Manual',
        'log_file': 'nifty_check.log',
        'recipients': []
    },
    'nifty_price_update': {
        'name': 'Nifty Price Update',
        'description': 'Updates Nifty benchmark price from Google Sheets at 9:30 AM, 12:30 PM, and 4 PM IST on weekdays. Historical prices saved only at 4 PM run.',
        'script': 'nifty_price_update.py',
        'category': 'Data Management',
        'schedule': '30 4,7,10 * * 1-5',  # UTC: ~10 AM, 1 PM, 4 PM IST (Airflow scheduler)
        'log_file': 'logs/nifty_update.log',
        'recipients': []
    }
}

def get_app_dir():
    """Get the application directory"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_script(script_name, timeout=300):
    """Run a Python script and return the result"""
    try:
        app_dir = get_app_dir()
        script_path = os.path.join(app_dir, script_name)
        
        if not os.path.exists(script_path):
            return {
                'success': False,
                'error': f'Script not found: {script_path}',
                'output': '',
                'return_code': 1
            }
        
        # Run the script
        result = subprocess.run(
            ['python3', script_path],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        return {
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr,
            'return_code': result.returncode
        }
        
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': f'Script timed out after {timeout} seconds',
            'output': '',
            'return_code': 1
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'output': '',
            'return_code': 1
        }

def get_log_content(log_file, lines=100):
    """Get the last N lines from a log file"""
    try:
        app_dir = get_app_dir()
        log_path = os.path.join(app_dir, log_file)
        
        if not os.path.exists(log_path):
            return f"Log file not found: {log_path}"
        
        with open(log_path, 'r') as f:
            all_lines = f.readlines()
            return ''.join(all_lines[-lines:])
            
    except Exception as e:
        return f"Error reading log file: {str(e)}"

@cron_jobs_bp.route('/cron-jobs', methods=['GET'])
def get_cron_jobs():
    """Get list of all available cron jobs"""
    try:
        jobs_list = []
        
        for job_id, config in CRON_JOBS.items():
            job_info = {
                'id': job_id,
                'name': config['name'],
                'description': config['description'],
                'category': config['category'],
                'schedule': config['schedule'],
                'log_file': config['log_file'],
                'recipients': config['recipients']
            }
            jobs_list.append(job_info)
        
        return APIResponse.success(
            data=jobs_list,
            message=f"Found {len(jobs_list)} cron jobs"
        )
        
    except Exception as e:
        raise APIException(f"Error getting cron jobs: {str(e)}")

@cron_jobs_bp.route('/cron-jobs/<job_id>', methods=['GET'])
def get_cron_job(job_id):
    """Get details of a specific cron job"""
    try:
        if job_id not in CRON_JOBS:
            raise APIException(f"Cron job '{job_id}' not found", status_code=404)
        
        config = CRON_JOBS[job_id]
        
        # Get log content
        log_content = get_log_content(config['log_file'], 50)
        
        # Get recipients and schedule from database
        from main import create_app
        from models import ReportRecipient, CronSchedule, db
        
        app = create_app()
        with app.app_context():
            recipients = ReportRecipient.query.filter_by(job_id=job_id, is_active=True).all()
            recipient_emails = [r.email for r in recipients]
            
            # Get schedule from database, fallback to config if not found
            cron_schedule = CronSchedule.query.filter_by(job_id=job_id, is_active=True).first()
            schedule = cron_schedule.schedule if cron_schedule else config['schedule']
        
        job_info = {
            'id': job_id,
            'name': config['name'],
            'description': config['description'],
            'category': config['category'],
            'schedule': schedule,
            'log_file': config['log_file'],
            'recipients': recipient_emails,
            'recent_logs': log_content
        }
        
        return APIResponse.success(
            data=job_info,
            message=f"Details for cron job '{job_id}'"
        )
        
    except APIException:
        raise
    except Exception as e:
        raise APIException(f"Error getting cron job details: {str(e)}")

@cron_jobs_bp.route('/cron-jobs/<job_id>/schedule', methods=['PUT'])
def update_cron_job_schedule(job_id):
    """Update the schedule for a specific cron job"""
    try:
        if job_id not in CRON_JOBS:
            raise APIException(f"Cron job '{job_id}' not found", status_code=404)
        
        data = request.get_json()
        if not data or 'schedule' not in data:
            raise APIException("Schedule is required", status_code=400)
        
        new_schedule = data['schedule'].strip()
        description = data.get('description', '').strip()
        
        # Basic cron validation (5 fields: minute hour day month weekday)
        schedule_parts = new_schedule.split()
        if len(schedule_parts) != 5:
            raise APIException("Invalid cron format. Must have 5 fields: minute hour day month weekday", status_code=400)
        
        # Update or create schedule in database
        from main import create_app
        from models import CronSchedule, db
        
        app = create_app()
        with app.app_context():
            cron_schedule = CronSchedule.query.filter_by(job_id=job_id).first()
            
            if cron_schedule:
                cron_schedule.schedule = new_schedule
                if description:
                    cron_schedule.description = description
                cron_schedule.updated_at = datetime.utcnow()
            else:
                cron_schedule = CronSchedule(
                    job_id=job_id,
                    schedule=new_schedule,
                    description=description or f"Schedule for {job_id}",
                    is_active=True
                )
                db.session.add(cron_schedule)
            
            db.session.commit()
        
        # Trigger cron job sync to update system crontab
        try:
            import subprocess
            script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'sync_cron_jobs.py')
            subprocess.run(['python3', script_path], capture_output=True, text=True)
        except Exception as sync_error:
            print(f"Warning: Failed to sync cron jobs after schedule update: {sync_error}")
        
        return APIResponse.success(
            data={'schedule': new_schedule, 'description': description},
            message=f"Schedule updated for cron job '{job_id}' and system crontab synced"
        )
        
    except APIException:
        raise
    except Exception as e:
        raise APIException(f"Error updating cron job schedule: {str(e)}", status_code=500)

@cron_jobs_bp.route('/cron-jobs/sync', methods=['POST'])
def sync_cron_jobs():
    """Sync database schedules with system crontab"""
    try:
        # Import and run the sync script
        import subprocess
        import os
        
        script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'sync_cron_jobs.py')
        
        result = subprocess.run(['python3', script_path], capture_output=True, text=True)
        
        if result.returncode == 0:
            return APIResponse.success(
                data={'output': result.stdout},
                message="Cron jobs synced successfully with system crontab"
            )
        else:
            raise APIException(f"Failed to sync cron jobs: {result.stderr}", status_code=500)
        
    except Exception as e:
        raise APIException(f"Error syncing cron jobs: {str(e)}", status_code=500)

@cron_jobs_bp.route('/cron-jobs/<job_id>/run', methods=['POST'])
def run_cron_job(job_id):
    """Manually run a specific cron job"""
    try:
        if job_id not in CRON_JOBS:
            raise APIException(f"Cron job '{job_id}' not found", status_code=404)
        
        config = CRON_JOBS[job_id]
        
        # Special handling for price update from sheets
        if job_id == 'price_update_sheets':
            return run_price_update_sheets()
        
        # Special handling for Nifty price update
        if job_id == 'nifty_price_update':
            return run_nifty_price_update()
        
        # Run the script
        result = run_script(config['script'])
        
        response_data = {
            'job_id': job_id,
            'job_name': config['name'],
            'started_at': datetime.now().isoformat(),
            'success': result['success'],
            'return_code': result['return_code'],
            'output': result['output'],
            'error': result['error']
        }
        
        if result['success']:
            message = f"Cron job '{job_id}' executed successfully"
        else:
            message = f"Cron job '{job_id}' failed with return code {result['return_code']}"
        
        return APIResponse.success(
            data=response_data,
            message=message,
            status_code=200 if result['success'] else 500
        )
        
    except APIException:
        raise
    except Exception as e:
        raise APIException(f"Error running cron job: {str(e)}")

@cron_jobs_bp.route('/cron-jobs/<job_id>/logs', methods=['GET'])
def get_cron_job_logs(job_id):
    """Get logs for a specific cron job"""
    try:
        if job_id not in CRON_JOBS:
            raise APIException(f"Cron job '{job_id}' not found", status_code=404)
        
        config = CRON_JOBS[job_id]
        lines = request.args.get('lines', 100, type=int)
        
        log_content = get_log_content(config['log_file'], lines)
        
        return APIResponse.success(
            data={
                'job_id': job_id,
                'log_file': config['log_file'],
                'lines_requested': lines,
                'log_content': log_content
            },
            message=f"Logs for cron job '{job_id}'"
        )
        
    except APIException:
        raise
    except Exception as e:
        raise APIException(f"Error getting cron job logs: {str(e)}")

def run_price_update_sheets():
    """Special handler for price update from Google Sheets.

    Business rule: Update current prices on every run. Persist a dated
    HistoricalPrice row only during the 16:00–16:59 IST hour, using the
    IST calendar date (not the server default timezone).
    """
    try:
        from contextlib import nullcontext

        from flask import current_app, has_app_context
        from main import create_app
        from models import db, Security, HistoricalPrice, ReportRecipient
        from extensions import db
        from flask_mail import Message
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        from datetime import datetime, date
        import pytz

        # Avoid a second create_app() when called from CLI/Airflow (duplicate Session table / metadata).
        if has_app_context():
            app_ctx = nullcontext()
            cfg_app = current_app
        else:
            _app = create_app()
            app_ctx = _app.app_context()
            cfg_app = _app

        with app_ctx:
            # Setup Google Sheets
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                _service_account_json_path(), scope
            )
            client = gspread.authorize(creds)
            
            # Open spreadsheet
            spreadsheet_key = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
            spreadsheet = client.open_by_key(spreadsheet_key)
            worksheet = spreadsheet.worksheet('Prices')
            
            # Get all data
            data = worksheet.get_all_records()
            
            updated_count = 0
            historical_saved_count = 0
            not_found_count = 0
            skipped_count = 0
            errors = []
            
            # Historical row date = Indian trading session date; do not use date.today() (often UTC on servers).
            hist_date = None
            should_save_historical = False
            try:
                ist = pytz.timezone('Asia/Kolkata')
                now_ist = datetime.now(tz=ist)
                hist_date = now_ist.date()
                # Any run in the 16:xx IST hour counts as market close (strict hour==16 missed jobs at e.g. 16:03).
                should_save_historical = 16 <= now_ist.hour < 17
            except Exception:
                # Fail-safe: no dated history row if TZ handling fails
                pass

            for row in data:
                try:
                    symbol = row.get('Symbol', '').strip().upper()
                    price = row.get('Price')
                    
                    if symbol and price:
                        # Validate price value
                        try:
                            if isinstance(price, str):
                                price = price.strip()
                                if price in ['#N/A', '#N/A N/A', '#NA', '-1.#IND', '-1.#QNAN', '-#N/A N/A', '-#QNAN', '1.#IND', '1.#QNAN', '#DIV/0!', '#REF!', '#NAME?', '#NULL!', '#VALUE!', '#NUM!', 'N/A', '']:
                                    skipped_count += 1
                                    continue
                            
                            price_float = float(price)
                            
                            if price_float <= 0 or price_float > 1000000:
                                skipped_count += 1
                                continue
                                
                        except (ValueError, TypeError):
                            skipped_count += 1
                            continue
                        
                        # Find security in database
                        security = Security.query.filter_by(symbol=symbol).first()
                        
                        if security:
                            # Update current price
                            security.current_price = price_float
                            security.last_updated = datetime.utcnow()
                            updated_count += 1
                            
                            # Persist HistoricalPrice ONLY at 16:00 IST (market close)
                            if should_save_historical and hist_date:
                                try:
                                    existing_historical = HistoricalPrice.query.filter_by(
                                        security_id=security.id,
                                        date=hist_date
                                    ).first()
                                    
                                    if existing_historical:
                                        if existing_historical.source in ['cron', 'googlefinance']:
                                            existing_historical.close_price = price_float
                                            existing_historical.source = 'cron'
                                            existing_historical.confidence = 0.99
                                            existing_historical.updated_at = datetime.utcnow()
                                            historical_saved_count += 1  # Count updates too
                                    else:
                                        historical_price = HistoricalPrice(
                                            security_id=security.id,
                                            date=hist_date,
                                            close_price=price_float,
                                            source='cron',
                                            confidence=0.99
                                        )
                                        db.session.add(historical_price)
                                        historical_saved_count += 1
                                except Exception as hist_error:
                                    errors.append(f"Historical save for {symbol}: {str(hist_error)}")
                        else:
                            not_found_count += 1
                    else:
                        skipped_count += 1
                        
                except Exception as e:
                    errors.append(f"Row {symbol}: {str(e)}")
            
            # Commit changes
            db.session.commit()
            
            # Notify via email
            try:
                from extensions import mail
                # Collect recipients for this job
                recipients = [r.email for r in ReportRecipient.query.filter_by(job_id='price_update_sheets', is_active=True).all()]
                if not recipients:
                    # Fallback to configured default receiver
                    recipients = ['anshul@equities4wealth.com']
                    # If MAIL_DEFAULT_SENDER is set and different, include it too
                    default_sender = cfg_app.config.get('MAIL_DEFAULT_SENDER')
                    if default_sender and default_sender not in recipients:
                        recipients.append(default_sender)
                if recipients:
                    ist = pytz.timezone('Asia/Kolkata')
                    now_ist = datetime.now(tz=ist)
                    subject = f"Prices updated ({now_ist.strftime('%Y-%m-%d %H:%M IST')})"
                    lines = [
                        f"Success: {True}",
                        f"Securities updated: {updated_count}",
                        f"Historical saved (16:00 IST run only): {historical_saved_count}",
                        f"Skipped: {skipped_count}",
                        f"Errors: {0 if not errors else len(errors)}",
                    ]
                    if should_save_historical:
                        lines.append("Note: This was the market close run; datewise prices persisted.")
                    msg = Message(subject=subject, recipients=recipients, body='\n'.join(lines))
                    mail.send(msg)
            except Exception:
                # Do not fail the job if email send fails
                pass

            response_data = {
                'job_id': 'price_update_sheets',
                'job_name': 'Price Update from Google Sheets (Enhanced)',
                'started_at': datetime.now().isoformat(),
                'success': True,
                'updated_count': updated_count,
                'historical_saved_count': historical_saved_count,
                'not_found_count': not_found_count,
                'skipped_count': skipped_count,
                'errors': errors[:10],  # Limit errors to first 10
                'total_errors': len(errors),
                'historical_saved_at_close_ist': should_save_historical
            }
            
            return APIResponse.success(
                data=response_data,
                message=f"Price update completed: {updated_count} current prices updated, {historical_saved_count} historical prices saved, {not_found_count} not found, {skipped_count} skipped"
            )
            
    except Exception as e:
        return APIResponse.error(
            message=f"Price update failed: {str(e)}",
            status_code=500,
            details={
                'job_id': 'price_update_sheets',
                'job_name': 'Price Update from Google Sheets',
                'started_at': datetime.now().isoformat(),
            },
        )

@cron_jobs_bp.route('/cron-jobs/categories', methods=['GET'])
def get_cron_job_categories():
    """Get all cron job categories with job counts"""
    try:
        categories = {}
        
        for job_id, config in CRON_JOBS.items():
            category = config['category']
            if category not in categories:
                categories[category] = {
                    'name': category,
                    'jobs': [],
                    'count': 0
                }
            
            categories[category]['jobs'].append({
                'id': job_id,
                'name': config['name'],
                'description': config['description']
            })
            categories[category]['count'] += 1
        
        return APIResponse.success(
            data=list(categories.values()),
            message=f"Found {len(categories)} cron job categories"
        )
        
    except Exception as e:
        raise APIException(f"Error getting cron job categories: {str(e)}")

@cron_jobs_bp.route('/cron-jobs/run-all', methods=['POST'])
def run_all_cron_jobs():
    """Run all cron jobs (use with caution)"""
    try:
        results = []
        
        for job_id in CRON_JOBS.keys():
            try:
                if job_id == 'price_update_sheets':
                    # Special handling for price update
                    result = run_price_update_sheets()
                    results.append({
                        'job_id': job_id,
                        'success': True,
                        'message': 'Price update completed'
                    })
                elif job_id == 'nifty_price_update':
                    # Special handling for Nifty price update
                    result = run_nifty_price_update()
                    results.append({
                        'job_id': job_id,
                        'success': True,
                        'message': 'Nifty price update completed'
                    })
                else:
                    config = CRON_JOBS[job_id]
                    result = run_script(config['script'])
                    results.append({
                        'job_id': job_id,
                        'success': result['success'],
                        'message': f"Script executed with return code {result['return_code']}"
                    })
            except Exception as e:
                results.append({
                    'job_id': job_id,
                    'success': False,
                    'message': f"Error: {str(e)}"
                })
        
        successful = sum(1 for r in results if r['success'])
        total = len(results)
        
        return APIResponse.success(
            data={
                'results': results,
                'summary': {
                    'total_jobs': total,
                    'successful': successful,
                    'failed': total - successful
                }
            },
            message=f"Executed {total} cron jobs: {successful} successful, {total - successful} failed"
        )
        
    except Exception as e:
        raise APIException(f"Error running all cron jobs: {str(e)}")

def run_nifty_price_update():
    """Special handler for Nifty price update from Google Sheets"""
    try:
        from contextlib import nullcontext

        from flask import current_app, has_app_context
        from main import create_app
        from models import db, Benchmark, BenchmarkData
        from datetime import datetime, date
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        # Match run_price_update_sheets: avoid second create_app when Airflow already pushed context.
        if has_app_context():
            app_ctx = nullcontext()
            cfg_app = current_app
        else:
            _app = create_app()
            app_ctx = _app.app_context()
            cfg_app = _app

        with app_ctx:
            # Ensure Nifty benchmark exists
            benchmark = Benchmark.query.filter_by(id=1).first()
            if not benchmark:
                benchmark = Benchmark(
                    id=1,
                    name='NIFTY 50',
                    symbol='^NSEI',
                    description='Nifty 50 Index - Benchmark for Indian equity market',
                    is_active=True
                )
                db.session.add(benchmark)
                db.session.commit()
            
            methods_tried = []
            method_used = None
            errors = []
            
            # Method 1: Try Google Sheets (primary method)
            try:
                # Setup Google Sheets
                scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
                creds = ServiceAccountCredentials.from_json_keyfile_name(
                    _service_account_json_path(), scope
                )
                client = gspread.authorize(creds)
                
                # Open spreadsheet
                spreadsheet_key = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
                spreadsheet = client.open_by_key(spreadsheet_key)
                worksheet = spreadsheet.worksheet('Nifty')
                
                # Get the latest Nifty data
                data = worksheet.get_all_records()
                if data:
                    # Find the most recent valid entry by parsing dates
                    # (Data might be in reverse chronological order)
                    valid_entries = []
                    for row in data:
                        date_str = row.get('Date', '')
                        # Try to find a valid price column (handle #VALUE! errors)
                        price_value = None
                        for key, value in row.items():
                            if key != 'Date' and value and str(value) not in ['#N/A', '#VALUE!', '#N/A N/A', '#NA', '']:
                                try:
                                    price_value = float(value)
                                    if price_value > 0:
                                        break
                                except (ValueError, TypeError):
                                    continue
                    
                        if price_value and price_value > 0 and date_str:
                            # Parse date
                            try:
                                # Handle different date formats
                                if '/' in date_str:
                                    date_obj = datetime.strptime(date_str, '%m/%d/%Y').date()
                                elif '-' in date_str:
                                    # Try multiple date formats
                                    try:
                                        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                                    except ValueError:
                                        date_obj = datetime.strptime(date_str, '%d-%m-%Y').date()
                                else:
                                    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                                
                                valid_entries.append({
                                    'date': date_obj,
                                    'price': price_value,
                                    'row': row
                                })
                            except ValueError:
                                continue
                    
                    if valid_entries:
                        # Sort by date descending to get most recent
                        valid_entries.sort(key=lambda x: x['date'], reverse=True)
                        latest_entry = valid_entries[0]
                        price_value = latest_entry['price']
                        date_value = latest_entry['date']
                        latest_row = latest_entry['row']
                    else:
                        price_value = None
                        date_value = None
                    
                    if price_value and price_value > 0:
                        # Use the date from the sheet entry
                        data_date = date_value if date_value else date.today()
                        today = date.today()
                        
                        # Save/update for the sheet's data_date
                        existing_data = BenchmarkData.query.filter_by(
                            benchmark_id=benchmark.id,
                            date=data_date
                        ).first()
                        if existing_data:
                            existing_data.price = price_value
                            existing_data.updated_at = datetime.utcnow()
                        else:
                            new_data = BenchmarkData(
                                benchmark_id=benchmark.id,
                                date=data_date,
                                price=price_value,
                                created_at=datetime.utcnow()
                            )
                            db.session.add(new_data)
                        
                        # If sheet date is before today, also upsert today's row so "current" price is for today
                        applied_date = data_date
                        if data_date < today:
                            today_row = BenchmarkData.query.filter_by(
                                benchmark_id=benchmark.id,
                                date=today
                            ).first()
                            if today_row:
                                today_row.price = price_value
                                today_row.updated_at = datetime.utcnow()
                            else:
                                db.session.add(BenchmarkData(
                                    benchmark_id=benchmark.id,
                                    date=today,
                                    price=price_value,
                                    created_at=datetime.utcnow()
                                ))
                            applied_date = today
                        
                        db.session.commit()
                        method_used = 'Google Sheets'
                        methods_tried.append('Google Sheets: Success')
                        
                        # Email: show applied date (today when we carried forward)
                        date_note = f"Source date (sheet): {data_date.isoformat()}" if data_date < today else ""
                        try:
                            from extensions import mail
                            from flask_mail import Message
                            from models import ReportRecipient
                            recipients = [r.email for r in ReportRecipient.query.filter_by(job_id='nifty_price_update', is_active=True).all()]
                            if not recipients:
                                recipients = ['anshul@equities4wealth.com']
                                default_sender = cfg_app.config.get('MAIL_DEFAULT_SENDER')
                                if default_sender and default_sender not in recipients:
                                    recipients.append(default_sender)
                            if recipients:
                                subject = f"Nifty price updated ({applied_date.isoformat()})"
                                body = f"Success: True\nPrice: ₹{price_value:,.2f}\nMethod: {method_used}\nDate: {applied_date.isoformat()}"
                                if date_note:
                                    body += f"\n{date_note}"
                                mail.send(Message(subject=subject, recipients=recipients, body=body))
                        except Exception:
                            pass

                        response_data = {
                            'job_id': 'nifty_price_update',
                            'job_name': 'Nifty Price Update',
                            'started_at': datetime.now().isoformat(),
                            'success': True,
                            'nifty_price': price_value,
                            'method_used': method_used,
                            'methods_tried': methods_tried,
                            'benchmark_id': benchmark.id,
                            'date': applied_date.isoformat(),
                        }
                        if data_date < today:
                            response_data['source_date'] = data_date.isoformat()
                        return APIResponse.success(
                            data=response_data,
                            message=f"Nifty price updated successfully: ₹{price_value:,.2f} for date {applied_date} (via {method_used})"
                        )
                    else:
                        methods_tried.append('Google Sheets: No valid price found')
                        errors.append('No valid price found in Google Sheets')
                else:
                    methods_tried.append('Google Sheets: No data found')
                    errors.append('No data found in Google Sheets')
                    
            except Exception as e:
                methods_tried.append(f'Google Sheets: Error - {str(e)}')
                errors.append(f'Google Sheets error: {str(e)}')
            
            # If Google Sheets failed, return error
            response_data = {
                'job_id': 'nifty_price_update',
                'job_name': 'Nifty Price Update',
                'started_at': datetime.now().isoformat(),
                'success': False,
                'methods_tried': methods_tried,
                'errors': errors
            }
            
            return APIResponse.success(
                data=response_data,
                message=f"Failed to update Nifty price. Methods tried: {', '.join(methods_tried)}",
                status_code=500
            )

    except Exception as e:
        return APIResponse.success(
            data={
                'job_id': 'nifty_price_update',
                'job_name': 'Nifty Price Update',
                'started_at': datetime.now().isoformat(),
                'success': False,
                'error': str(e)
            },
            message=f"Nifty price update failed: {str(e)}",
            status_code=500
        )

@cron_jobs_bp.route('/nifty-price/quick-update', methods=['POST'])
def quick_nifty_price_update():
    """
    Quick manual update with current known Nifty price
    """
    try:
        from main import create_app
        from models import Benchmark, BenchmarkData
        from datetime import datetime, date
        
        app = create_app()
        
        with app.app_context():
            # Ensure Nifty benchmark exists
            benchmark = Benchmark.query.filter_by(id=1).first()
            if not benchmark:
                benchmark = Benchmark(
                    id=1,
                    name='NIFTY 50',
                    symbol='^NSEI',
                    description='Nifty 50 Index - Benchmark for Indian equity market',
                    is_active=True
                )
                db.session.add(benchmark)
                db.session.commit()
            
            # Use current known Nifty price (October 2, 2025)
            current_nifty_price = 24894.25
            
            # Create or update benchmark data
            today = date.today()
            
            # Check if we already have data for today
            existing_data = BenchmarkData.query.filter_by(
                benchmark_id=benchmark.id,
                date=today
            ).first()
            
            if existing_data:
                old_price = existing_data.price
                existing_data.price = current_nifty_price
                existing_data.updated_at = datetime.utcnow()
            else:
                new_data = BenchmarkData(
                    benchmark_id=benchmark.id,
                    date=today,
                    price=current_nifty_price,
                    created_at=datetime.utcnow()
                )
                db.session.add(new_data)
            
            db.session.commit()
            
            return APIResponse.success(
                data={
                    'nifty_price': current_nifty_price,
                    'date': today.isoformat(),
                    'method': 'Quick Update',
                    'benchmark_id': benchmark.id
                },
                message=f"Nifty price quick updated: ₹{current_nifty_price:,.2f}"
            )
            
    except Exception as e:
        return APIResponse.error(
            message=f"Quick update failed: {str(e)}",
            status_code=500
        )

@cron_jobs_bp.route('/nifty-price/update', methods=['POST'])
def update_nifty_price():
    """
    Update Nifty price from Google Sheets
    Falls back to manual update if Google Sheets fails
    """
    try:
        from main import create_app
        from models import db, Benchmark, BenchmarkData
        from datetime import datetime, date
        
        app = create_app()
        
        with app.app_context():
            # Ensure Nifty benchmark exists
            benchmark = Benchmark.query.filter_by(id=1).first()
            if not benchmark:
                benchmark = Benchmark(
                    id=1,
                    name='NIFTY 50',
                    symbol='^NSEI',
                    description='Nifty 50 Index - Benchmark for Indian equity market',
                    is_active=True
                )
                db.session.add(benchmark)
                db.session.commit()
            
            methods_tried = []
            nifty_price = None
            nifty_date = None
            method_used = None
            errors = []
            
            # Method 1: Try Google Sheets (primary method)
            try:
                import gspread
                from oauth2client.service_account import ServiceAccountCredentials
                import pandas as pd
                
                # Setup Google Sheets
                scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
                creds = ServiceAccountCredentials.from_json_keyfile_name(
                    _service_account_json_path(), scope
                )
                client = gspread.authorize(creds)
                
                # Open spreadsheet
                spreadsheet_key = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
                spreadsheet = client.open_by_key(spreadsheet_key)
                worksheet = spreadsheet.worksheet('Nifty')
                
                # Get the latest Nifty data
                data = worksheet.get_all_records()
                if data:
                    # Find the most recent valid entry by parsing dates
                    # Process all rows and find the one with the latest date
                    valid_entries = []
                    for row in data:
                        date_str = row.get('Date', '')
                        # Try to find a valid price column (handle #VALUE! errors)
                        price_value = None
                        for key, value in row.items():
                            if key != 'Date' and value and str(value) not in ['#N/A', '#VALUE!', '#N/A N/A', '#NA', '']:
                                try:
                                    price_value = float(value)
                                    if price_value > 0:
                                        break
                                except (ValueError, TypeError):
                                    continue
                        
                        if price_value and price_value > 0 and date_str:
                            # Parse date
                            try:
                                # Handle different date formats
                                if '/' in date_str:
                                    date_obj = datetime.strptime(date_str, '%m/%d/%Y').date()
                                elif '-' in date_str:
                                    # Try multiple date formats
                                    try:
                                        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                                    except ValueError:
                                        date_obj = datetime.strptime(date_str, '%d-%m-%Y').date()
                                else:
                                    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                                
                                valid_entries.append({
                                    'date': date_obj,
                                    'price': price_value,
                                    'row': row
                                })
                            except ValueError:
                                # Skip rows with unparseable dates
                                continue
                    
                    if valid_entries:
                        # Sort by date descending to get most recent (latest date first)
                        valid_entries.sort(key=lambda x: x['date'], reverse=True)
                        latest_entry = valid_entries[0]
                        price_value = latest_entry['price']
                        date_value = latest_entry['date']
                        
                        nifty_price = price_value
                        nifty_date = date_value
                        method_used = 'Google Sheets'
                        methods_tried.append({
                            'method': 'Google Sheets',
                            'success': True,
                            'price': nifty_price,
                            'error': None,
                            'date': date_value.isoformat()
                        })
                    else:
                        methods_tried.append({
                            'method': 'Google Sheets',
                            'success': False,
                            'price': None,
                            'error': 'No valid price data found with valid dates'
                        })
                else:
                    methods_tried.append({
                        'method': 'Google Sheets',
                        'success': False,
                        'price': None,
                        'error': 'No data in worksheet'
                    })
                    
            except Exception as e:
                methods_tried.append({
                    'method': 'Google Sheets',
                    'success': False,
                    'price': None,
                    'error': str(e)
                })
                errors.append(f"Google Sheets: {str(e)}")
            
            # If we got a price, save it to database
            if nifty_price is not None:
                # Use the date from the sheet entry, or today's date as fallback
                data_date = nifty_date if nifty_date else date.today()
                
                # Check if we already have data for this date
                existing_data = BenchmarkData.query.filter_by(
                    benchmark_id=benchmark.id,
                    date=data_date
                ).first()
                
                if existing_data:
                    # Update existing record
                    old_price = existing_data.price
                    existing_data.price = nifty_price
                    existing_data.updated_at = datetime.utcnow()
                else:
                    # Create new record
                    new_data = BenchmarkData(
                        benchmark_id=benchmark.id,
                        date=data_date,
                        price=nifty_price,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(new_data)
                
                db.session.commit()
                
                response_data = {
                    'success': True,
                    'nifty_price': nifty_price,
                    'method_used': method_used,
                    'date': today.isoformat(),
                    'benchmark_id': benchmark.id,
                    'methods_tried': methods_tried,
                    'total_methods': len(methods_tried),
                    'successful_methods': len([m for m in methods_tried if m['success']])
                }
                
                return APIResponse.success(
                    data=response_data,
                    message=f"Nifty price updated successfully: ₹{nifty_price:,.2f} (via {method_used})"
                )
            else:
                # Google Sheets failed - suggest manual update
                response_data = {
                    'success': False,
                    'nifty_price': None,
                    'method_used': None,
                    'methods_tried': methods_tried,
                    'total_methods': len(methods_tried),
                    'successful_methods': 0,
                    'errors': errors,
                    'suggestion': 'Use manual update endpoint or fix Google Sheets data'
                }
                
                return APIResponse.error(
                    message="Failed to retrieve Nifty price from Google Sheets. Use manual update or fix the spreadsheet data.",
                    status_code=500,
                    data=response_data
                )
                
    except Exception as e:
        return APIResponse.success(
            data={
                'success': False,
                'error': str(e),
                'nifty_price': None,
                'method_used': None
            },
            message=f"Error updating Nifty price: {str(e)}",
            status_code=500
        )

@cron_jobs_bp.route('/nifty-price/manual-update', methods=['POST'])
def manual_nifty_price_update():
    """
    Manually update Nifty price when APIs fail
    """
    try:
        from main import create_app
        from models import db, Benchmark, BenchmarkData
        from datetime import datetime, date
        
        app = create_app()
        
        with app.app_context():
            # Get price from request
            data = request.get_json()
            if not data or 'price' not in data:
                return APIResponse.error(
                    message="Price is required",
                    status_code=400
                )
            
            try:
                nifty_price = float(data['price'])
            except (ValueError, TypeError):
                return APIResponse.error(
                    message="Invalid price format",
                    status_code=400
                )
            
            # Ensure Nifty benchmark exists
            benchmark = Benchmark.query.filter_by(id=1).first()
            if not benchmark:
                benchmark = Benchmark(
                    id=1,
                    name='NIFTY 50',
                    symbol='^NSEI',
                    description='Nifty 50 Index - Benchmark for Indian equity market',
                    is_active=True
                )
                db.session.add(benchmark)
                db.session.commit()
            
            # Save to database
            today = date.today()
            existing_data = BenchmarkData.query.filter_by(
                benchmark_id=benchmark.id,
                date=today
            ).first()
            
            if existing_data:
                # Update existing record
                existing_data.price = nifty_price
            else:
                # Create new record
                new_data = BenchmarkData(
                    benchmark_id=benchmark.id,
                    date=today,
                    price=nifty_price,
                    created_at=datetime.utcnow()
                )
                db.session.add(new_data)
            
            db.session.commit()
            
            return APIResponse.success(
                data={
                    'nifty_price': nifty_price,
                    'date': today.isoformat(),
                    'method': 'Manual Update',
                    'benchmark_id': benchmark.id
                },
                message=f"Nifty price manually updated: ₹{nifty_price:,.2f}"
            )
            
    except Exception as e:
        return APIResponse.error(
            message=f"Error updating Nifty price: {str(e)}",
            status_code=500
        )

@cron_jobs_bp.route('/nifty-price/current', methods=['GET'])
def get_current_nifty_price():
    """Get the current Nifty price from database"""
    try:
        from main import create_app
        from models import Benchmark, BenchmarkData
        
        app = create_app()
        
        with app.app_context():
            # Get Nifty benchmark
            benchmark = Benchmark.query.filter_by(id=1).first()
            if not benchmark:
                return APIResponse.success(
                    data=None,
                    message="NIFTY 50 benchmark not found",
                    status_code=404
                )
            
            # Get the latest benchmark data
            latest_data = BenchmarkData.query.filter_by(benchmark_id=benchmark.id)\
                .order_by(BenchmarkData.date.desc()).first()
            
            if not latest_data:
                return APIResponse.success(
                    data=None,
                    message="No Nifty price data found",
                    status_code=404
                )
            
            # Get some recent data points
            recent_data = BenchmarkData.query.filter_by(benchmark_id=benchmark.id)\
                .order_by(BenchmarkData.date.desc()).limit(10).all()
            
            response_data = {
                'benchmark': {
                    'id': benchmark.id,
                    'name': benchmark.name,
                    'symbol': benchmark.symbol
                },
                'current_price': float(latest_data.price),
                'current_date': latest_data.date.isoformat(),
                'last_updated': latest_data.created_at.isoformat() if latest_data.created_at else None,
                'recent_prices': [
                    {
                        'date': data.date.isoformat(),
                        'price': float(data.price)
                    }
                    for data in recent_data
                ],
                'total_records': BenchmarkData.query.filter_by(benchmark_id=benchmark.id).count()
            }
            
            return APIResponse.success(
                data=response_data,
                message=f"Current Nifty price: ₹{float(latest_data.price):,.2f}"
            )
            
    except Exception as e:
        return APIResponse.success(
            data=None,
            message=f"Error getting current Nifty price: {str(e)}",
            status_code=500
        )