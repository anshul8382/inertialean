from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import User
from alert_system_models import Alert, SLAConfiguration
from alert_service import AlertService
from access_control import require_manager, require_advisor_or_manager
from functools import wraps
from extensions import csrf
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import joinedload
from datetime import datetime
import logging
import sys

logger = logging.getLogger(__name__)

alerts = Blueprint('alerts', __name__)

def _can_view_all_alerts(user):
    """Admins, Ops Managers, and Managers can view all alerts."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return bool(getattr(user, 'is_manager', False) or getattr(user, 'is_ops_manager', False) or getattr(user, 'is_admin', False))

def _reactivate_expired_snoozed_alerts():
    """Automatically reactivate alerts that have passed their snooze time"""
    try:
        now = datetime.utcnow()
        expired_snoozed = Alert.query.filter(
            Alert.status == 'snoozed',
            Alert.snoozed_until.isnot(None),
            Alert.snoozed_until < now
        ).all()
        
        for alert in expired_snoozed:
            alert.status = 'active'
            alert.snoozed_until = None
        
        if expired_snoozed:
            db.session.commit()
            logger.info(f"Reactivated {len(expired_snoozed)} expired snoozed alerts")
            return len(expired_snoozed)
    except Exception as e:
        logger.error(f"Error reactivating expired snoozed alerts: {str(e)}")
        db.session.rollback()
    return 0

def _escalate_unacknowledged_alerts():
    """Automatically escalate unacknowledged (active) alerts to critical if not acknowledged within 24 hours"""
    try:
        now = datetime.utcnow()
        active_alerts = Alert.query.filter(
            Alert.status == 'active'
        ).all()
        
        escalated_count = 0
        for alert in active_alerts:
            hours_since_creation = (now - alert.created_at).total_seconds() / 3600
            if hours_since_creation >= 24 and alert.severity != 'critical':
                old_severity = alert.severity
                alert.severity = 'critical'
                # Log the severity change in notes_history
                alert._log_status_change(old_severity, 'critical', 'Auto-escalated: Alert not acknowledged within 24 hours')
                escalated_count += 1
        
        if escalated_count > 0:
            db.session.commit()
            logger.info(f"Escalated {escalated_count} unacknowledged alerts to critical")
            return escalated_count
    except Exception as e:
        logger.error(f"Error escalating unacknowledged alerts: {str(e)}")
        db.session.rollback()
    return 0

# API Routes for alerts
@alerts.route('/api/lead-alerts')
@login_required
def api_lead_alerts():
    """API endpoint to get lead-related alerts"""
    try:
        from models import Lead
        
        # Reactivate expired snoozed alerts first
        try:
            _reactivate_expired_snoozed_alerts()
        except Exception as e:
            logger.error(f"Error in reactivation: {str(e)}")
        
        # Escalate unacknowledged alerts that are over 24 hours old
        try:
            _escalate_unacknowledged_alerts()
        except Exception as e:
            logger.error(f"Error in escalation: {str(e)}")
        
        now = datetime.utcnow()
        
        # Get lead-related alerts (active, acknowledged, or snoozed that haven't expired)
        if _can_view_all_alerts(current_user):
            alerts = Alert.query.filter(
                Alert.alert_type.in_(['lead_followup', 'workflow_sla']),
                Alert.status.in_(['active', 'acknowledged', 'snoozed'])
            ).order_by(Alert.created_at.desc()).limit(10).all()
        else:
            alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.alert_type.in_(['lead_followup', 'workflow_sla']),
                Alert.status.in_(['active', 'acknowledged', 'snoozed'])
            ).order_by(Alert.created_at.desc()).limit(10).all()
        
        # Filter out snoozed alerts that haven't expired yet
        visible_alerts = []
        for alert in alerts:
            if alert.status == 'snoozed' and alert.snoozed_until and alert.snoozed_until > now:
                continue  # Skip snoozed alerts that haven't expired
            visible_alerts.append(alert)
        
        alerts_data = []
        for alert in visible_alerts:
            alert_data = {
                'id': alert.id,
                'title': alert.title,
                'description': alert.description,
                'severity': alert.severity,
                'created_at': alert.created_at.strftime('%Y-%m-%d %H:%M'),
                'alert_type': alert.alert_type,
                'status': alert.status
            }
            alerts_data.append(alert_data)
        
        return jsonify({
            'success': True,
            'alerts': alerts_data
        })
        
    except Exception as e:
        logger.error(f"Error in api_lead_alerts: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@alerts.route('/api/monthly-investment-alerts')
@login_required
def api_monthly_investment_alerts():
    """API endpoint to get monthly investment alerts"""
    try:
        from models import MonthlyInvestment, Client
        
        # Reactivate expired snoozed alerts first
        try:
            _reactivate_expired_snoozed_alerts()
        except Exception as e:
            logger.error(f"Error in reactivation: {str(e)}")
        
        # Escalate unacknowledged alerts that are over 24 hours old
        try:
            _escalate_unacknowledged_alerts()
        except Exception as e:
            logger.error(f"Error in escalation: {str(e)}")
        
        now = datetime.utcnow()
        
        # Get monthly investment related alerts (active, acknowledged, or snoozed that haven't expired)
        if _can_view_all_alerts(current_user):
            alerts = Alert.query.filter(
                Alert.alert_type.in_(['workflow_sla', 'monthly_investment']),
                Alert.status.in_(['active', 'acknowledged', 'snoozed'])
            ).order_by(Alert.created_at.desc()).limit(10).all()
        else:
            alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.alert_type.in_(['workflow_sla', 'monthly_investment']),
                Alert.status.in_(['active', 'acknowledged', 'snoozed'])
            ).order_by(Alert.created_at.desc()).limit(10).all()
        
        # Filter out snoozed alerts that haven't expired yet
        visible_alerts = []
        for alert in alerts:
            if alert.status == 'snoozed' and alert.snoozed_until and alert.snoozed_until > now:
                continue  # Skip snoozed alerts that haven't expired
            visible_alerts.append(alert)
        
        alerts_data = []
        for alert in visible_alerts:
            client_name = "Unknown"
            if alert.client_id:
                client = Client.query.get(alert.client_id)
                if client:
                    client_name = client.name
            
            alert_data = {
                'id': alert.id,
                'title': alert.title,
                'description': alert.description,
                'severity': alert.severity,
                'created_at': alert.created_at.strftime('%Y-%m-%d %H:%M'),
                'client_name': client_name,
                'alert_type': alert.alert_type,
                'status': alert.status
            }
            alerts_data.append(alert_data)
        
        return jsonify({
            'success': True,
            'alerts': alerts_data
        })
        
    except Exception as e:
        logger.error(f"Error in api_monthly_investment_alerts: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

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

@alerts.route('/')
@login_required
@handle_errors
def list_alerts():
    """List all alerts for the current user"""
    
    # Reactivate expired snoozed alerts first
    try:
        _reactivate_expired_snoozed_alerts()
    except Exception as e:
        logger.error(f"Error in reactivation: {str(e)}")
    
    # Escalate unacknowledged alerts that are over 24 hours old
    try:
        _escalate_unacknowledged_alerts()
    except Exception as e:
        logger.error(f"Error in escalation: {str(e)}")
    
    # Get filter parameters
    client_id = request.args.get('client_id', type=int)
    severity = request.args.get('severity')
    alert_type = request.args.get('alert_type')
    status = request.args.get('status')  # Get status from URL, None if not provided
    if not status:
        status = 'active'  # Default to 'active' to show only active alerts by default
    logger.info(f"Alert list request - status={status}, client_id={client_id}, severity={severity}, alert_type={alert_type}")
    
    now = datetime.utcnow()
    
    try:
        can_view_all = _can_view_all_alerts(current_user)
        
        if can_view_all:
            # Managers / Ops Managers / Admins can see all alerts
            query = Alert.query.options(joinedload(Alert.client))
            
            # Apply status filter
            # Explicit check: only apply status filter if status is provided AND not 'all'
            if status is not None and status != '' and status != 'all':
                if status == 'snoozed':
                    # When snoozed is selected, show ALL snoozed alerts (including non-expired)
                    query = query.filter_by(status='snoozed')
                else:
                    query = query.filter_by(status=status)
            
            # Apply filters
            if client_id:
                query = query.filter_by(client_id=client_id)
            if severity:
                query = query.filter_by(severity=severity)
            if alert_type:
                query = query.filter_by(alert_type=alert_type)
                
            alerts_list = query.order_by(Alert.created_at.desc()).all()
            logger.info(f"Found {len(alerts_list)} alerts for manager (status={status})")
        else:
            # Advisors can only see their assigned alerts
            query = Alert.query.options(joinedload(Alert.client)).filter_by(user_id=current_user.id)
            
            # Apply status filter
            # Explicit check: only apply status filter if status is provided AND not 'all'
            if status is not None and status != '' and status != 'all':
                if status == 'snoozed':
                    # When snoozed is selected, show ALL snoozed alerts (including non-expired)
                    query = query.filter_by(status='snoozed')
                else:
                    query = query.filter_by(status=status)
            
            # Apply filters
            if client_id:
                query = query.filter_by(client_id=client_id)
            if severity:
                query = query.filter_by(severity=severity)
            if alert_type:
                query = query.filter_by(alert_type=alert_type)
                
            alerts_list = query.order_by(Alert.created_at.desc()).all()
            logger.info(f"Found {len(alerts_list)} alerts for advisor {current_user.id} (status={status})")
    except Exception as e:
        logger.error(f"Error querying alerts: {str(e)}", exc_info=True)
        alerts_list = []
    
    # Get all clients for filter dropdown
    from models import Client
    if _can_view_all_alerts(current_user):
        clients = Client.query.order_by(func.lower(Client.name)).all()
    else:
        # Get clients assigned to this advisor
        clients = Client.query.filter_by(advisor_id=current_user.id).order_by(func.lower(Client.name)).all()
    
    # Helper function to format notes history for display
    def format_notes_history(alert):
        """Format notes_history for display in list view - shows full history"""
        result_parts = []
        
        # Add resolution notes if present
        if alert.resolution_notes:
            result_parts.append(f"✓ Resolution: {alert.resolution_notes}")
        
        # Check if notes_history exists and has content
        if not alert.notes_history or not alert.notes_history.strip():
            return '\n'.join(result_parts) if result_parts else None
        
        # Parse and format notes_history
        lines = alert.notes_history.split('\n')
        current_entry = []
        current_timestamp = None
        current_user = None
        
        for line in lines:
            line = line.strip()
            if not line:
                # Empty line - save current entry if exists
                if current_entry:
                    note_text = ' '.join(current_entry).strip()
                    if note_text:
                        if current_timestamp and current_user:
                            result_parts.append(f"{current_timestamp} - {current_user}: {note_text}")
                        elif current_timestamp:
                            result_parts.append(f"{current_timestamp}: {note_text}")
                        else:
                            result_parts.append(note_text)
                    current_entry = []
                continue
            
            # System messages start with [timestamp]
            if line.startswith('['):
                # Save any previous entry
                if current_entry:
                    note_text = ' '.join(current_entry).strip()
                    if note_text:
                        if current_timestamp and current_user:
                            result_parts.append(f"{current_timestamp} - {current_user}: {note_text}")
                        elif current_timestamp:
                            result_parts.append(f"{current_timestamp}: {note_text}")
                        else:
                            result_parts.append(note_text)
                    current_entry = []
                
                # Extract timestamp and user/message
                if ']' in line:
                    parts = line.split(']', 1)
                    timestamp = parts[0].replace('[', '').strip()
                    message = parts[1].strip()
                    current_timestamp = timestamp
                    
                    if ':' in message:
                        user_part, note_part = message.split(':', 1)
                        current_user = user_part.strip()
                        if note_part.strip():
                            current_entry.append(note_part.strip())
                        # If it's a SYSTEM message, include it directly
                        if current_user == 'SYSTEM':
                            result_parts.append(f"{current_timestamp} - System: {note_part.strip()}")
                            current_entry = []
                            current_user = None
                    else:
                        # No colon - this is just a message
                        if message.startswith('SYSTEM'):
                            result_parts.append(f"{current_timestamp} - System: {message}")
                            current_user = None
                        else:
                            current_user = message
            else:
                # This is note content
                current_entry.append(line)
        
        # Add any remaining entry
        if current_entry:
            note_text = ' '.join(current_entry).strip()
            if note_text:
                if current_timestamp and current_user:
                    result_parts.append(f"{current_timestamp} - {current_user}: {note_text}")
                elif current_timestamp:
                    result_parts.append(f"{current_timestamp}: {note_text}")
                else:
                    result_parts.append(note_text)
        
        return '\n'.join(result_parts) if result_parts else None
    
    # Add formatted notes history to each alert for template access
    notes_count = 0
    for i, alert in enumerate(alerts_list):
        try:
            formatted = format_notes_history(alert)
            # Force set the attribute using setattr to ensure it's set
            setattr(alert, 'formatted_notes', formatted)
            
            if formatted:
                notes_count += 1
                # Debug first few alerts
                if i < 3:
                    logger.info(f"Alert {alert.id}: formatted_notes set, length={len(formatted)}, preview={formatted[:100]}")
                    # Verify it's actually set
                    if not hasattr(alert, 'formatted_notes') or alert.formatted_notes != formatted:
                        logger.error(f"Alert {alert.id}: formatted_notes NOT properly set!")
            else:
                if i < 3:
                    logger.warning(f"Alert {alert.id}: formatted_notes is None (notes_history={bool(alert.notes_history)}, resolution_notes={bool(alert.resolution_notes)})")
        except Exception as e:
            logger.error(f"Error formatting notes for alert {alert.id}: {str(e)}", exc_info=True)
            setattr(alert, 'formatted_notes', None)
    
    # Debug logging
    logger.info(f"Rendering template with status={status}, alerts_count={len(alerts_list)}, alerts_with_notes={notes_count}")
    
    return render_template('alerts/list_alerts.html', 
                         alerts=alerts_list, 
                         clients=clients,
                         selected_client_id=client_id,
                         selected_severity=severity,
                         selected_alert_type=alert_type,
                         selected_status=status or 'active')

@alerts.route('/dashboard')
@login_required
@handle_errors
def alert_dashboard():
    """Alert dashboard with overview and statistics"""
    try:
        # Reactivate expired snoozed alerts first
        try:
            _reactivate_expired_snoozed_alerts()
        except Exception as e:
            logger.error(f"Error in reactivation: {str(e)}")
        
        # Escalate unacknowledged alerts that are over 24 hours old
        try:
            _escalate_unacknowledged_alerts()
        except Exception as e:
            logger.error(f"Error in escalation: {str(e)}")
        
        now = datetime.utcnow()
        
        # Get alert statistics (include active and acknowledged)
        if _can_view_all_alerts(current_user):
            total_alerts = Alert.query.filter(
                Alert.status.in_(['active', 'acknowledged'])
            ).count()
            critical_alerts = Alert.query.filter(
                Alert.status.in_(['active', 'acknowledged']),
                Alert.severity == 'critical'
            ).count()
            warning_alerts = Alert.query.filter(
                Alert.status.in_(['active', 'acknowledged']),
                Alert.severity == 'warning'
            ).count()
            info_alerts = Alert.query.filter(
                Alert.status.in_(['active', 'acknowledged']),
                Alert.severity == 'info'
            ).count()
        else:
            total_alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.status.in_(['active', 'acknowledged'])
            ).count()
            critical_alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.status.in_(['active', 'acknowledged']),
                Alert.severity == 'critical'
            ).count()
            warning_alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.status.in_(['active', 'acknowledged']),
                Alert.severity == 'warning'
            ).count()
            info_alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.status.in_(['active', 'acknowledged']),
                Alert.severity == 'info'
            ).count()
        
        # Get recent alerts (active and acknowledged)
        if _can_view_all_alerts(current_user):
            recent_alerts = Alert.query.filter(
                Alert.status.in_(['active', 'acknowledged'])
            ).order_by(Alert.created_at.desc()).limit(10).all()
        else:
            recent_alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.status.in_(['active', 'acknowledged'])
            ).order_by(Alert.created_at.desc()).limit(10).all()
        
        # Get alert breakdown by type (active and acknowledged)
        alert_types = db.session.query(
            Alert.alert_type,
            db.func.count(Alert.id).label('count')
        ).filter(
            Alert.status.in_(['active', 'acknowledged'])
        ).group_by(Alert.alert_type).all()
        
        return render_template('alerts/dashboard.html',
                             total_alerts=total_alerts,
                             critical_alerts=critical_alerts,
                             warning_alerts=warning_alerts,
                             info_alerts=info_alerts,
                             recent_alerts=recent_alerts,
                             alert_types=alert_types)
                             
    except Exception as e:
        logger.error(f"Error loading alert dashboard: {str(e)}")
        flash('Error loading alert dashboard', 'error')
        return redirect(url_for('main.dashboard'))

@alerts.route('/<int:alert_id>')
@login_required
@handle_errors
def view_alert(alert_id):
    """View alert details"""
    alert = Alert.query.options(joinedload(Alert.client)).get_or_404(alert_id)
    
    # Check if user has access to this alert
    if not _can_view_all_alerts(current_user) and alert.user_id != current_user.id:
        flash('Access denied', 'error')
        return redirect(url_for('alerts.list_alerts'))
    
    return render_template('alerts/view_alert.html', alert=alert)

@alerts.route('/<int:alert_id>/acknowledge', methods=['POST'])
@login_required
@handle_errors
def acknowledge_alert(alert_id):
    """Acknowledge an alert"""
    alert = Alert.query.get_or_404(alert_id)
    
    # Check if user has access to this alert
    if not _can_view_all_alerts(current_user) and alert.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Access denied'})
    
    if AlertService.acknowledge_alert(alert_id, current_user.id):
        flash('Alert acknowledged successfully', 'success')
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to acknowledge alert'})

@alerts.route('/<int:alert_id>/resolve', methods=['POST'])
@login_required
@handle_errors
def resolve_alert(alert_id):
    """Resolve an alert"""
    try:
        alert = Alert.query.get_or_404(alert_id)
        
        # Check if user has access to this alert
        if not _can_view_all_alerts(current_user) and alert.user_id != current_user.id:
            return jsonify({'success': False, 'error': 'Access denied'})
        
        # Handle both form data and JSON
        if request.is_json:
            data = request.get_json()
            notes = data.get('notes', '')
        else:
            notes = request.form.get('notes', '')
        
        if AlertService.resolve_alert(alert_id, current_user.id, notes):
            flash('Alert resolved successfully', 'success')
            return jsonify({'success': True})
        else:
            error_msg = f'Failed to resolve alert {alert_id}. Check logs for details.'
            logger.error(f"Failed to resolve alert {alert_id} for user {current_user.id}")
            return jsonify({'success': False, 'error': error_msg})
    except Exception as e:
        logger.error(f"Error in resolve_alert route for alert {alert_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Error resolving alert: {str(e)}'})

@alerts.route('/<int:alert_id>/escalate', methods=['POST'])
@login_required
@require_advisor_or_manager
@handle_errors
def escalate_alert(alert_id):
    """Escalate an alert"""
    # Handle both JSON and form data
    escalated_to_user_id = None
    if request.is_json:
        data = request.get_json() or {}
        escalated_to_user_id = data.get('escalated_to_user_id')
        if escalated_to_user_id:
            try:
                escalated_to_user_id = int(escalated_to_user_id)
            except (ValueError, TypeError):
                escalated_to_user_id = None
    else:
        escalated_to_user_id = request.form.get('escalated_to_user_id', type=int)
    
    # If no user specified, escalate to a manager/admin
    if not escalated_to_user_id:
        from models import User, Role
        from sqlalchemy import or_
        
        # Find a manager or admin to escalate to
        # Check both role string field and role_id relationship
        # First, get role IDs for manager/admin roles
        manager_role_ids = [r.id for r in Role.query.filter(
            or_(
                Role.name.ilike('%manager%'),
                Role.name.ilike('%admin%')
            )
        ).all()]
        
        # Find user with manager/admin role (check both string role and role_id)
        conditions = [
            User.role.in_(['manager', 'admin', 'ops_manager']),
            User.is_admin == True
        ]
        if manager_role_ids:
            conditions.append(User.role_id.in_(manager_role_ids))
        
        manager = User.query.filter(
            User.is_active == True,
            or_(*conditions)
        ).first()
        
        if manager:
            escalated_to_user_id = manager.id
        else:
            return jsonify({'success': False, 'error': 'No manager or admin found to escalate to'})
    
    if AlertService.escalate_alert(alert_id, escalated_to_user_id):
        flash('Alert escalated successfully', 'success')
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to escalate alert'})

@alerts.route('/<int:alert_id>/snooze', methods=['POST'])
@login_required
@handle_errors
def snooze_alert(alert_id):
    """Snooze an alert"""
    alert = Alert.query.get_or_404(alert_id)
    
    # Check if user has access to this alert
    if not _can_view_all_alerts(current_user) and alert.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Access denied'})
    
    # Handle both form data and JSON
    if request.is_json:
        data = request.get_json()
        hours = data.get('hours', 24)
    else:
        hours = request.form.get('hours', 24, type=int)
    
    # Validate hours: 1 to 168 hours (7 days)
    try:
        hours = int(hours)
        if hours < 1 or hours > 168:
            return jsonify({'success': False, 'error': 'Snooze hours must be between 1 and 168 (7 days)'})
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid hours value'})
    
    if AlertService.snooze_alert(alert_id, hours):
        flash(f'Alert snoozed for {hours} hours', 'success')
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to snooze alert'})

@alerts.route('/<int:alert_id>/add-note', methods=['POST'])
@login_required
@handle_errors
def add_alert_note(alert_id):
    """Add a note to an alert"""
    alert = Alert.query.get_or_404(alert_id)
    
    # Check if user has access to this alert
    if not _can_view_all_alerts(current_user) and alert.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Access denied'})
    
    # Handle both form data and JSON
    if request.is_json:
        data = request.get_json()
        note_text = data.get('note', '').strip()
    else:
        note_text = request.form.get('note', '').strip()
    
    if not note_text:
        return jsonify({'success': False, 'error': 'Note cannot be empty'})
    
    try:
        alert.add_note(current_user.id, note_text)
        db.session.commit()
        flash('Note added successfully', 'success')
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error adding note to alert: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Failed to add note'}), 500

@alerts.route('/api/alerts')
@login_required
@handle_errors
def api_get_alerts():
    """API endpoint to get alerts for dashboard"""
    try:
        if _can_view_all_alerts(current_user):
            alerts_list = AlertService.get_all_active_alerts()
        else:
            alerts_list = AlertService.get_user_alerts(current_user.id)
        
        alerts_data = []
        for alert in alerts_list:
            alerts_data.append({
                'id': alert.id,
                'title': alert.title,
                'description': alert.description,
                'severity': alert.severity,
                'alert_type': alert.alert_type,
                'created_at': alert.created_at.isoformat(),
                'age_hours': alert.age_hours,
                'is_overdue': alert.is_overdue
            })
        
        return jsonify({'alerts': alerts_data})
        
    except Exception as e:
        logger.error(f"Error in API get alerts: {str(e)}")
        return jsonify({'error': 'Failed to get alerts'}), 500

@alerts.route('/api/alert-count')
@login_required
@handle_errors
def api_get_alert_count():
    """API endpoint to get alert count for dashboard"""
    try:
        # Reactivate expired snoozed alerts first
        try:
            _reactivate_expired_snoozed_alerts()
        except Exception as e:
            logger.error(f"Error in reactivation: {str(e)}")
        
        # Escalate unacknowledged alerts that are over 24 hours old
        try:
            _escalate_unacknowledged_alerts()
        except Exception as e:
            logger.error(f"Error in escalation: {str(e)}")
        
        if _can_view_all_alerts(current_user):
            total_alerts = Alert.query.filter(
                Alert.status.in_(['active', 'acknowledged'])
            ).count()
            critical_alerts = Alert.query.filter(
                Alert.status.in_(['active', 'acknowledged']),
                Alert.severity == 'critical'
            ).count()
        else:
            total_alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.status.in_(['active', 'acknowledged'])
            ).count()
            critical_alerts = Alert.query.filter(
                Alert.user_id == current_user.id,
                Alert.status.in_(['active', 'acknowledged']),
                Alert.severity == 'critical'
            ).count()
        
        return jsonify({
            'total_alerts': total_alerts,
            'critical_alerts': critical_alerts
        })
        
    except Exception as e:
        logger.error(f"Error in API get alert count: {str(e)}")
        return jsonify({'error': 'Failed to get alert count'}), 500

@alerts.route('/sla-configuration')
@login_required
@require_manager
@handle_errors
def sla_configuration():
    """Manage SLA configurations"""
    configurations = SLAConfiguration.query.filter_by(active=True).order_by(SLAConfiguration.process_name, SLAConfiguration.process_stage).all()
    return render_template('alerts/sla_configuration.html', configurations=configurations)

@alerts.route('/sla-configuration/<int:config_id>/edit', methods=['GET', 'POST'])
@login_required
@require_manager
@handle_errors
def edit_sla_configuration(config_id):
    """Edit SLA configuration"""
    config = SLAConfiguration.query.get_or_404(config_id)
    
    if request.method == 'POST':
        try:
            config.sla_hours = request.form.get('sla_hours', type=int)
            config.alert_trigger_percentage = request.form.get('alert_trigger_percentage', type=int)
            config.email_notification = 'email_notification' in request.form
            config.sms_notification = 'sms_notification' in request.form
            config.in_app_notification = 'in_app_notification' in request.form
            config.default_assignee_role = request.form.get('default_assignee_role')
            
            db.session.commit()
            flash('SLA configuration updated successfully', 'success')
            return redirect(url_for('alerts.sla_configuration'))
            
        except Exception as e:
            logger.error(f"Error updating SLA configuration: {str(e)}")
            flash('Error updating SLA configuration', 'error')
            db.session.rollback()
    
    return render_template('alerts/edit_sla_configuration.html', config=config)

@alerts.route('/initialize-sla')
@login_required
@require_manager
@handle_errors
def initialize_sla():
    """Initialize default SLA configurations"""
    try:
        AlertService.initialize_default_sla_configurations()
        flash('Default SLA configurations initialized successfully', 'success')
    except Exception as e:
        logger.error(f"Error initializing SLA configurations: {str(e)}")
        flash('Error initializing SLA configurations', 'error')
    
    return redirect(url_for('alerts.sla_configuration'))

@alerts.route('/check-sla')
@login_required
@require_manager
@handle_errors
def check_sla():
    """Manually trigger SLA checks"""
    try:
        AlertService.check_workflow_sla()
        AlertService.check_recommendation_sla()
        # Additional SLA checks that feed into client health score
        try:
            AlertService.check_meeting_reminders()
        except Exception:
            pass
        try:
            AlertService.check_meeting_cadence_sla()
        except Exception:
            pass
        try:
            AlertService.check_invoice_payment_sla()
        except Exception:
            pass
        try:
            AlertService.check_review_sla()
        except Exception:
            pass
        try:
            AlertService.check_ticket_sla()
        except Exception:
            pass
        # Cleanup old resolved alerts (runs automatically during SLA checks)
        try:
            deleted_count = AlertService.cleanup_old_resolved_alerts(days_old=90)
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old resolved alerts during SLA check")
        except Exception as e:
            logger.warning(f"Error during alert cleanup: {str(e)}")
        flash('SLA checks completed successfully', 'success')
    except Exception as e:
        logger.error(f"Error checking SLA: {str(e)}")
        flash('Error checking SLA', 'error')
    
    return redirect(url_for('alerts.alert_dashboard'))

@alerts.route('/reset-alerts', methods=['POST'])
@login_required
@require_manager
@handle_errors
def reset_alerts():
    """Delete all old alerts and regenerate based on current SLA breaches"""
    try:
        from datetime import datetime
        
        # Get parameters
        delete_all = request.json.get('delete_all', False) if request.is_json else request.form.get('delete_all', 'false') == 'true'
        before_date = request.json.get('before_date') if request.is_json else request.form.get('before_date')
        
        deleted_count = 0
        
        if delete_all:
            # Delete all alerts
            alerts_to_delete = Alert.query.all()
            deleted_count = len(alerts_to_delete)
            for alert in alerts_to_delete:
                db.session.delete(alert)
        elif before_date:
            # Delete alerts before a specific date
            try:
                cutoff_date = datetime.strptime(before_date, '%Y-%m-%d')
                alerts_to_delete = Alert.query.filter(Alert.created_at < cutoff_date).all()
                deleted_count = len(alerts_to_delete)
                for alert in alerts_to_delete:
                    db.session.delete(alert)
            except ValueError:
                flash('Invalid date format. Use YYYY-MM-DD', 'error')
                return redirect(url_for('alerts.alert_dashboard'))
        else:
            # Default: Delete all alerts
            alerts_to_delete = Alert.query.all()
            deleted_count = len(alerts_to_delete)
            for alert in alerts_to_delete:
                db.session.delete(alert)
        
        db.session.commit()
        
        # Now regenerate alerts based on current SLA breaches
        AlertService.check_workflow_sla()
        AlertService.check_recommendation_sla()
        try:
            AlertService.check_meeting_reminders()
        except:
            pass
        try:
            AlertService.check_meeting_cadence_sla()
        except:
            pass
        try:
            AlertService.check_invoice_payment_sla()
        except:
            pass
        try:
            AlertService.check_review_sla()
        except:
            pass
        
        # Check other alert types if they exist
        try:
            AlertService.check_ticket_sla()
        except:
            pass
        
        new_alerts_count = Alert.query.filter_by(status='active').count()
        
        flash(f'Reset completed: Deleted {deleted_count} old alerts, Generated {new_alerts_count} new alerts based on current SLA breaches', 'success')
        
        if request.is_json:
            return jsonify({
                'success': True,
                'deleted_count': deleted_count,
                'new_alerts_count': new_alerts_count
            })
        else:
            return redirect(url_for('alerts.alert_dashboard'))
            
    except Exception as e:
        logger.error(f"Error resetting alerts: {str(e)}", exc_info=True)
        db.session.rollback()
        error_msg = f'Error resetting alerts: {str(e)}'
        flash(error_msg, 'error')
        
        if request.is_json:
            return jsonify({'success': False, 'error': error_msg}), 500
        else:
            return redirect(url_for('alerts.alert_dashboard'))
