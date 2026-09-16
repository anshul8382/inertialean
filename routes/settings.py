from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from functools import wraps
import logging
import os
import re
import sys

# Add the application directory to Python path
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from cron_manager import cron_manager
from extensions import db
from models import Client
from sqlalchemy import text

logger = logging.getLogger(__name__)
settings = Blueprint('settings', __name__)

# Error handling decorator
def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('main.dashboard'))
    return decorated_function

@settings.route('/')
@login_required
@handle_errors
def index():
    """Settings main page"""
    zoho_status = None
    try:
        from services.zoho_books_service import diagnose_connection

        zoho_status = diagnose_connection()
    except Exception as exc:
        logger.warning("Zoho diagnose failed: %s", exc)
        zoho_status = {"error": str(exc), "configured": False, "organizations": []}

    leegality_status = {
        "api_base": current_app.config.get("LEEGALITY_API_BASE")
        or os.environ.get("LEEGALITY_API_BASE")
        or "https://app1.leegality.com/api",
        "auth_token_set": bool(
            current_app.config.get("LEEGALITY_AUTH_TOKEN") or os.environ.get("LEEGALITY_AUTH_TOKEN")
        ),
        "private_salt_set": bool(
            current_app.config.get("LEEGALITY_PRIVATE_SALT") or os.environ.get("LEEGALITY_PRIVATE_SALT")
        ),
        "profile_id": current_app.config.get("LEEGALITY_PROFILE_ID")
        or os.environ.get("LEEGALITY_PROFILE_ID")
        or "",
        "firm_signer_name": current_app.config.get("LEEGALITY_FIRM_SIGNER_NAME")
        or os.environ.get("LEEGALITY_FIRM_SIGNER_NAME")
        or "",
        "firm_signer_email": current_app.config.get("LEEGALITY_FIRM_SIGNER_EMAIL")
        or os.environ.get("LEEGALITY_FIRM_SIGNER_EMAIL")
        or "",
        "invitee_order": current_app.config.get("LEEGALITY_INVITEE_ORDER")
        or os.environ.get("LEEGALITY_INVITEE_ORDER")
        or "firm_first",
    }
    onboarding_approval = {
        "advisor_work": _get_billing_config("onboarding_approve_advisor_work", "manager"),
        "manager_work": _get_billing_config("onboarding_approve_manager_work", "admin"),
    }
    proposal_email_cc = _get_billing_config(
        "proposal_email_cc",
        (os.environ.get("PROPOSAL_EMAIL_CC") or current_app.config.get("PROPOSAL_EMAIL_CC") or ""),
    )
    return render_template(
        'settings/index.html',
        zoho_status=zoho_status,
        leegality_status=leegality_status,
        onboarding_approval=onboarding_approval,
        proposal_email_cc=proposal_email_cc,
    )


def _get_billing_config(key: str, default: str = "") -> str:
    try:
        from models import BillingConfiguration

        row = BillingConfiguration.query.filter_by(config_key=key, is_active=True).first()
        if row and row.config_value is not None:
            return str(row.config_value)
    except Exception:
        pass
    return default


def _set_billing_config(key: str, value: str, description: str = "") -> None:
    from models import BillingConfiguration

    row = BillingConfiguration.query.filter_by(config_key=key).first()
    if not row:
        row = BillingConfiguration(
            config_key=key,
            config_value=value,
            config_type="string",
            description=description or key,
            is_active=True,
        )
        db.session.add(row)
    else:
        row.config_value = value
        row.is_active = True
        row.updated_at = __import__("datetime").datetime.utcnow()
    db.session.commit()


@settings.route('/onboarding-approvals', methods=['POST'])
@login_required
@handle_errors
def save_onboarding_approvals():
    if not (getattr(current_user, "is_admin", False) or getattr(current_user, "is_manager", False)):
        flash("Only managers or admins can change approval settings.", "error")
        return redirect(url_for("settings.index"))
    advisor_to = (request.form.get("approve_advisor_work") or "manager").strip().lower()
    manager_to = (request.form.get("approve_manager_work") or "admin").strip().lower()
    if advisor_to not in ("manager", "admin"):
        advisor_to = "manager"
    if manager_to not in ("manager", "admin"):
        manager_to = "admin"
    cc = (request.form.get("proposal_email_cc") or "").strip()
    _set_billing_config(
        "onboarding_approve_advisor_work",
        advisor_to,
        "Who approves advisor-created proposals/invoices",
    )
    _set_billing_config(
        "onboarding_approve_manager_work",
        manager_to,
        "Who approves manager-created proposals/invoices",
    )
    _set_billing_config("proposal_email_cc", cc, "Comma-separated CC for proposal emails")
    flash("Onboarding approval / proposal CC settings saved.", "success")
    return redirect(url_for("settings.index"))


@settings.route('/zoho/connect')
@login_required
@handle_errors
def zoho_connect():
    """Start Zoho Books OAuth (Server-based / Client-based app with redirect URI)."""
    if not getattr(current_user, "is_admin", False):
        flash("Admin access required to connect Zoho.", "error")
        return redirect(url_for("settings.index"))
    from secrets import token_urlsafe
    from flask import session
    from services.zoho_books_service import oauth_authorize_url

    state = token_urlsafe(24)
    session["zoho_oauth_state"] = state
    url, err = oauth_authorize_url(state=state)
    if err:
        flash(err, "error")
        return redirect(url_for("settings.index"))
    return redirect(url)


@settings.route('/zoho/callback')
@login_required
@handle_errors
def zoho_callback():
    """OAuth redirect target — exchange code, save refresh token, verify orgs."""
    if not getattr(current_user, "is_admin", False):
        flash("Admin access required.", "error")
        return redirect(url_for("settings.index"))
    from flask import session
    from services.zoho_books_service import (
        exchange_authorization_code,
        persist_refresh_token_to_env,
        diagnose_connection,
    )

    err_q = (request.args.get("error") or "").strip()
    if err_q:
        flash(f"Zoho authorization denied: {err_q}", "error")
        return redirect(url_for("settings.index"))

    state = (request.args.get("state") or "").strip()
    expected = session.pop("zoho_oauth_state", None)
    if not expected or state != expected:
        flash("Zoho OAuth state mismatch. Try Connect again.", "error")
        return redirect(url_for("settings.index"))

    code = (request.args.get("code") or "").strip()
    if not code:
        flash("Zoho did not return an authorization code.", "error")
        return redirect(url_for("settings.index"))

    payload, err = exchange_authorization_code(code)
    if err:
        flash(err, "error")
        return redirect(url_for("settings.index"))

    refresh = (payload or {}).get("refresh_token")
    if not refresh:
        flash(
            "Zoho returned no refresh_token (often if already authorized). "
            "Revoke the app in Zoho Accounts → Connected Apps, then Connect again.",
            "warning",
        )
        return redirect(url_for("settings.index"))

    persist_err = persist_refresh_token_to_env(refresh)
    if persist_err:
        flash(f"Got refresh token but could not write .env: {persist_err}", "error")
        return redirect(url_for("settings.index"))

    status = diagnose_connection()
    orgs = status.get("organizations") or []
    if orgs:
        names = ", ".join(f"{o.get('name')} ({o.get('id')})" for o in orgs[:5])
        flash(f"Zoho connected. Organizations: {names}", "success")
        org_cfg = (current_app.config.get("ZOHO_ORGANIZATION_ID") or "").strip()
        ids = {o.get("id") for o in orgs}
        if org_cfg and org_cfg not in ids:
            flash(
                f"ZOHO_ORGANIZATION_ID={org_cfg} is not in the token’s org list. "
                f"Update .env to one of: {', '.join(sorted(ids))}",
                "warning",
            )
    else:
        flash(
            status.get("error")
            or "Connected, but Zoho still returned 0 organizations for this login.",
            "warning",
        )
    return redirect(url_for("settings.index"))

@settings.route('/test')
@login_required
@handle_errors
def test():
    """Test route for settings"""
    return jsonify({'success': True, 'message': 'Settings blueprint is working'})

@settings.route('/cron-jobs')
@login_required
@handle_errors
def cron_jobs():
    """Cron jobs management page"""
    # Get all cron jobs status
    jobs_status = cron_manager.get_all_jobs_status()
    
    # Group jobs by category
    jobs_by_category = {}
    for job_id, job_info in jobs_status.items():
        category = job_info.get('category', 'Other')
        if category not in jobs_by_category:
            jobs_by_category[category] = []
        jobs_by_category[category].append({
            'id': job_id,
            **job_info
        })
    
    return render_template('settings/cron_jobs.html', 
                         jobs_by_category=jobs_by_category,
                         cron_manager=cron_manager)

@settings.route('/cron-jobs/<job_id>/toggle', methods=['POST'])
@login_required
@handle_errors
def toggle_cron_job(job_id):
    """Enable or disable a cron job"""
    try:
        logger.info(f"Toggle request received for job: {job_id}")
        logger.info(f"Request headers: {dict(request.headers)}")
        logger.info(f"Request form: {request.form}")
        
        # Get current status
        status = cron_manager.get_job_status(job_id)
        logger.info(f"Current status for {job_id}: {status}")
        
        if status.get('enabled', False):
            # Disable job
            logger.info(f"Disabling job: {job_id}")
            success = cron_manager.disable_cron_job(job_id)
            if success:
                flash(f'Cron job "{job_id}" has been disabled.', 'success')
            else:
                flash(f'Failed to disable cron job "{job_id}".', 'error')
        else:
            # Enable job
            logger.info(f"Enabling job: {job_id}")
            success = cron_manager.enable_cron_job(job_id)
            if success:
                flash(f'Cron job "{job_id}" has been enabled.', 'success')
            else:
                flash(f'Failed to enable cron job "{job_id}".', 'error')
        
        logger.info(f"Toggle result for {job_id}: {success}")
        return jsonify({'success': success})
        
    except Exception as e:
        logger.error(f"Error toggling cron job {job_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@settings.route('/cron-jobs/<job_id>/update-schedule', methods=['POST'])
@login_required
@handle_errors
def update_cron_schedule(job_id):
    """Update the schedule of a cron job"""
    try:
        new_schedule = request.form.get('schedule')
        if not new_schedule:
            return jsonify({'success': False, 'error': 'Schedule is required'})
        
        success = cron_manager.update_cron_job_schedule(job_id, new_schedule)
        
        if success:
            flash(f'Schedule updated for cron job "{job_id}".', 'success')
        else:
            flash(f'Failed to update schedule for cron job "{job_id}".', 'error')
        
        return jsonify({'success': success})
        
    except Exception as e:
        logger.error(f"Error updating cron schedule for {job_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@settings.route('/cron-jobs/<job_id>/test', methods=['POST'])
@login_required
@handle_errors
def test_cron_job(job_id):
    """Test run a cron job manually"""
    try:
        logger.info(f"Test request received for job: {job_id}")
        logger.info(f"Request form data: {request.form}")
        logger.info(f"Request headers: {dict(request.headers)}")
        
        result = cron_manager.test_cron_job(job_id)
        logger.info(f"Test result for {job_id}: {result}")
        
        if result.get('success', False):
            flash(f'Test run for "{job_id}" completed successfully.', 'success')
        else:
            flash(f'Test run for "{job_id}" failed: {result.get("error", "Unknown error")}', 'error')
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error testing cron job {job_id}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@settings.route('/cron-jobs/status')
@login_required
@handle_errors
def get_cron_status():
    """Get current status of all cron jobs (AJAX endpoint)"""
    try:
        jobs_status = cron_manager.get_all_jobs_status()
        return jsonify({'success': True, 'jobs': jobs_status})
        
    except Exception as e:
        logger.error(f"Error getting cron status: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@settings.route('/cron-jobs/logs/<job_id>')
@login_required
@handle_errors
def view_cron_logs(job_id):
    """View logs for a specific cron job"""
    try:
        status = cron_manager.get_job_status(job_id)
        log_file = status.get('log_file')
        
        if not log_file:
            flash('Log file not found for this job.', 'error')
            return redirect(url_for('settings.cron_jobs'))
        
        log_path = os.path.join('/home/inertia/app', log_file)
        
        if not os.path.exists(log_path):
            flash('Log file does not exist.', 'error')
            return redirect(url_for('settings.cron_jobs'))
        
        # Read last 100 lines of log file
        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()
                recent_logs = lines[-100:] if len(lines) > 100 else lines
        except Exception as e:
            recent_logs = [f"Error reading log file: {str(e)}"]
        
        return render_template('settings/cron_logs.html', 
                             job_id=job_id,
                             logs=recent_logs,
                             log_file=log_file)
        
    except Exception as e:
        logger.error(f"Error viewing cron logs for {job_id}: {str(e)}")
        flash(f'Error viewing logs: {str(e)}', 'error')
        return redirect(url_for('settings.cron_jobs'))

def _server_timezone_display():
    """IANA or TZ env + offset for the process (true server-side, not browser Intl)."""
    from datetime import datetime

    now = datetime.now().astimezone()
    ti = now.tzinfo
    iana = getattr(ti, "key", None) if ti else None
    tz_env = (os.environ.get("TZ") or "").strip()
    if iana:
        return iana
    if tz_env:
        return tz_env
    if now.tzname():
        return f"{now.tzname()} {now.strftime('%z')}".strip()
    return str(ti) if ti else "Unknown"


def _system_info_app_defaults():
    """Static app metadata for system info page (always passed so template never misses keys)."""
    return {
        "app_directory": "/home/inertia/app",
        "log_directory": "/home/inertia/app/logs",
        "database": "MySQL",
        "email_server": "65-254-81-55.cprapid.com:587",
        "server_timezone": _server_timezone_display(),
    }


@settings.route('/system-info')
@login_required
@handle_errors
def system_info():
    """System information page"""
    app_info = _system_info_app_defaults()
    try:
        import platform

        import psutil

        disk_pct = float(psutil.disk_usage("/").percent)
        system_info = {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "cpu_count": psutil.cpu_count(),
            "memory_total": f"{psutil.virtual_memory().total / (1024**3):.2f} GB",
            "memory_available": f"{psutil.virtual_memory().available / (1024**3):.2f} GB",
            "disk_usage_percent": round(disk_pct, 1),
            "disk_usage_display": f"{disk_pct:.1f}%",
        }

        return render_template(
            "settings/system_info.html",
            system_info=system_info,
            app_info=app_info,
        )

    except ImportError:
        flash("psutil module not available. Install with: pip install psutil", "warning")
        return render_template(
            "settings/system_info.html",
            system_info={},
            app_info=app_info,
        )
    except Exception as e:
        logger.error(f"Error getting system info: {str(e)}")
        flash(f"Error getting system information: {str(e)}", "error")
        return render_template(
            "settings/system_info.html",
            system_info={},
            app_info=app_info,
        )


_LOG_FILENAME_SAFE = re.compile(r"^[\w][\w.-]{0,254}$")


def _resolved_application_logs_dir():
    """Directory for on-disk application logs (same tree as gunicorn / app loggers)."""
    configured = current_app.config.get("APPLICATION_LOG_DIR")
    if configured:
        return os.path.realpath(str(configured))
    return os.path.realpath(os.path.join(current_app.root_path, "logs"))


def _safe_log_file_path(logs_dir_real, name):
    """Resolve name to a file under logs_dir_real only."""
    if not name or not _LOG_FILENAME_SAFE.match(name):
        return None
    path = os.path.realpath(os.path.join(logs_dir_real, name))
    prefix = logs_dir_real if logs_dir_real.endswith(os.sep) else logs_dir_real + os.sep
    if not path.startswith(prefix):
        return None
    return path if os.path.isfile(path) else None


@settings.route("/application-logs")
@login_required
@handle_errors
def application_logs():
    """List log files under the application logs directory (admin only)."""
    if not getattr(current_user, "is_admin", False):
        flash("Admin access required.", "error")
        return redirect(url_for("main.dashboard"))
    logs_dir = _resolved_application_logs_dir()
    entries = []
    if os.path.isdir(logs_dir):
        try:
            for fn in sorted(os.listdir(logs_dir)):
                fp = os.path.join(logs_dir, fn)
                if os.path.isfile(fp):
                    try:
                        sz = os.path.getsize(fp)
                    except OSError:
                        sz = None
                    entries.append({"name": fn, "size_bytes": sz})
        except OSError as e:
            logger.error("application_logs listdir %s: %s", logs_dir, e)
            flash(f"Could not read log directory: {e}", "error")
    return render_template(
        "settings/application_logs.html",
        logs_dir=logs_dir,
        entries=entries,
    )


@settings.route("/application-logs/tail")
@login_required
@handle_errors
def application_log_tail():
    """Tail of a single log file under the application logs directory (admin only)."""
    if not getattr(current_user, "is_admin", False):
        flash("Admin access required.", "error")
        return redirect(url_for("main.dashboard"))
    name = (request.args.get("file") or "").strip()
    nlines = request.args.get("lines", type=int) or 200
    nlines = min(max(nlines, 20), 2000)
    logs_dir = _resolved_application_logs_dir()
    path = _safe_log_file_path(logs_dir, name)
    if not path:
        flash("Invalid or missing log file.", "error")
        return redirect(url_for("settings.application_logs"))
    try:
        with open(path, "rb") as f:
            raw = f.readlines()
        tail = raw[-nlines:] if len(raw) > nlines else raw
        text = b"".join(tail).decode("utf-8", errors="replace")
    except OSError as e:
        logger.error("application_log_tail read %s: %s", path, e)
        flash(f"Could not read log: {e}", "error")
        text = ""
    return render_template(
        "settings/application_log_tail.html",
        filename=name,
        content=text,
        lines_shown=nlines,
        file_size=os.path.getsize(path) if os.path.isfile(path) else None,
    )


@settings.route('/holdings-cycle')
@login_required
@handle_errors
def holdings_cycle():
    """Holdings cycle management page"""
    try:
        from datetime import datetime
        
        current_month = datetime.now().strftime("%Y-%m")
        
        # Get cycle statistics
        result = db.session.execute(text("""
            SELECT 
                status,
                COUNT(*) as count,
                SUM(mismatches_found) as total_mismatches
            FROM monthly_holdings_cycle 
            WHERE cycle_id = :cycle_id
            GROUP BY status
        """), {"cycle_id": current_month})
        
        stats = {}
        for row in result:
            stats[row[0]] = {
                'count': row[1],
                'mismatches': row[2] or 0
            }
        
        # Get clients with mismatches
        mismatch_result = db.session.execute(text("""
            SELECT 
                mhc.client_id,
                mhc.mismatches_found,
                mhc.status,
                mhc.processed_at,
                c.name as client_name,
                c.email as client_email
            FROM monthly_holdings_cycle mhc
            JOIN client c ON mhc.client_id = c.id
            WHERE mhc.cycle_id = :cycle_id 
            AND mhc.mismatches_found > 0
            ORDER BY mhc.mismatches_found DESC
            LIMIT 50
        """), {"cycle_id": current_month})
        
        mismatch_clients = []
        for row in mismatch_result:
            mismatch_clients.append({
                'client_id': row[0],
                'client_name': row[4],
                'client_email': row[5],
                'mismatches': row[1],
                'status': row[2],
                'processed_at': row[3]
            })
        
        return render_template('settings/holdings_cycle.html',
                             current_month=current_month,
                             stats=stats,
                             mismatch_clients=mismatch_clients)
        
    except Exception as e:
        logger.error(f"Error loading holdings cycle page: {str(e)}")
        flash(f'Error loading holdings cycle: {str(e)}', 'error')
        return redirect(url_for('settings.index'))
