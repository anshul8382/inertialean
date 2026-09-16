from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import ServiceTicket, Client, User
from alert_system_models import Alert, SLAConfiguration
from routes.forms import ServiceTicketForm, ServiceTicketUpdateForm, ServiceTicketSnoozeForm, ServiceTicketUnsnoozeForm
from access_control import get_accessible_clients_ordered
from datetime import datetime, timedelta
from functools import wraps
from contextlib import contextmanager
import logging
import time

logger = logging.getLogger(__name__)
tickets_bp = Blueprint('tickets', __name__)


def _ticket_accessible_clients():
    return get_accessible_clients_ordered()


def _wake_expired_snoozes():
    """Best-effort: if a snoozed ticket's snooze time has passed, unsnooze it."""
    now = datetime.utcnow()
    expired = ServiceTicket.query.filter(
        ServiceTicket.status == 'snoozed',
        ServiceTicket.snoozed_until.isnot(None),
        ServiceTicket.snoozed_until <= now
    ).all()

    if not expired:
        return 0

    for t in expired:
        t.status = t.pre_snooze_status or 'open'
        t.pre_snooze_status = None
        t.snoozed_until = None
        t.snoozed_at = None
        t.snoozed_by = None
        t.snooze_reason = None
        t.updated_at = now

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Don't break pages if wakeup fails; it's a convenience.
        logger.exception("Failed to wake expired snoozed tickets")
        return 0

    return len(expired)

def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            try:
                db.session.rollback()
            except Exception as rollback_error:
                logger.error(f"Error during rollback: {str(rollback_error)}")
            logger.error(f"Error in {f.__name__}: {str(e)}", exc_info=True)
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('main.dashboard'))
    return decorated_function

@contextmanager
def db_transaction():
    """Context manager for database transactions with automatic rollback on error"""
    try:
        yield db.session
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Transaction failed, rolled back: {str(e)}")
        raise

def generate_ticket_number():
    """Generate unique ticket number with race condition protection"""
    today = datetime.utcnow()
    prefix = f"TKT-{today.strftime('%Y%m%d')}"
    
    # Use database-level count to avoid race conditions
    # Retry logic for duplicate detection
    max_retries = 10
    for attempt in range(max_retries):
        count = ServiceTicket.query.filter(
            ServiceTicket.ticket_number.like(f'{prefix}%')
        ).count()
        ticket_number = f"{prefix}-{count + 1:04d}"
        
        # Check if this number already exists (race condition protection)
        existing = ServiceTicket.query.filter_by(ticket_number=ticket_number).first()
        if not existing:
            return ticket_number
        
        # If duplicate, wait a tiny bit and retry
        time.sleep(0.01 * (attempt + 1))
    
    # Fallback: use timestamp for uniqueness
    return f"{prefix}-{int(datetime.utcnow().timestamp())}"

def get_sla_for_alert_type(alert_type, priority):
    """Get SLA configuration for alert type and priority"""
    # Map priority to process_stage
    priority_map = {
        'critical': 'critical',
        'high': 'high',
        'medium': 'medium',
        'low': 'low'
    }
    process_stage = priority_map.get(priority, 'medium')
    
    sla_config = SLAConfiguration.query.filter_by(
        process_name='service_ticket',
        process_stage=process_stage,
        active=True
    ).first()
    
    if not sla_config:
        # Default SLA based on priority
        default_sla = {
            'critical': 4,    # 4 hours
            'high': 24,       # 24 hours
            'medium': 72,      # 72 hours
            'low': 168        # 7 days
        }
        return default_sla.get(priority, 72)
    
    return sla_config.sla_hours

@tickets_bp.route('/tickets')
@login_required
@handle_errors
def list_tickets():
    """List all service tickets"""
    # Ensure expired snoozes reappear automatically
    _wake_expired_snoozes()

    status = request.args.get('status', 'all')
    priority = request.args.get('priority', 'all')
    
    query = ServiceTicket.query
    
    if status != 'all':
        query = query.filter(ServiceTicket.status == status)
    if priority != 'all':
        query = query.filter(ServiceTicket.priority == priority)
    
    tickets = query.order_by(ServiceTicket.created_at.desc()).all()
    
    # Get statistics - optimized queries
    total_tickets = ServiceTicket.query.count()
    open_tickets = ServiceTicket.query.filter(
        ServiceTicket.status.in_(['open', 'in_progress'])
    ).count()
    critical_tickets = ServiceTicket.query.filter(
        ServiceTicket.priority == 'critical',
        ServiceTicket.status.in_(['open', 'in_progress'])
    ).count()
    
    # Filter overdue tickets in database, not in Python
    overdue_tickets = ServiceTicket.query.filter(
        ServiceTicket.status.in_(['open', 'in_progress']),
        ServiceTicket.sla_deadline < datetime.utcnow()
    ).count()
    
    return render_template('tickets/list.html',
                         tickets=tickets,
                         status=status,
                         priority=priority,
                         total_tickets=total_tickets,
                         open_tickets=open_tickets,
                         critical_tickets=critical_tickets,
                         overdue_tickets=overdue_tickets)

@tickets_bp.route('/tickets/new', methods=['GET', 'POST'])
@login_required
@handle_errors
def new_ticket():
    """Create a new service ticket"""
    form = ServiceTicketForm()
    
    # Populate assigned_to dropdown
    form.assigned_to.choices = [(0, 'Unassigned')] + [(u.id, u.username) for u in User.query.filter_by(is_active=True).all()]
    
    # Get client_id from query params if available
    from access_control import can_access_client

    client_id = request.args.get('client_id')
    if client_id:
        try:
            client_id = int(client_id)
            if client_id <= 0:
                raise ValueError("Invalid client ID")
            if not can_access_client(client_id):
                flash("Access denied. You can only access your assigned clients.", "error")
                return redirect(url_for('tickets.list_tickets'))
            client = Client.query.get(client_id)
            if not client:
                flash('Client not found.', 'error')
                return redirect(url_for('clients_v2.list_clients'))
        except (ValueError, TypeError):
            flash('Invalid client ID.', 'error')
            return redirect(url_for('tickets.list_tickets'))
    else:
        client = None
        client_id = None
    
    if form.validate_on_submit():
        # Get client_id from form or URL param
        submitted_client_id = request.form.get('client_id')
        if submitted_client_id:
            try:
                client_id = int(submitted_client_id)
                if client_id <= 0:
                    raise ValueError("Invalid client ID")
            except (ValueError, TypeError):
                flash('Invalid client ID.', 'error')
                return render_template('tickets/new.html', form=form, client=client, clients=_ticket_accessible_clients())
        elif client_id:
            client_id = int(client_id)
        else:
            flash('Client is required.', 'error')
            return render_template('tickets/new.html', form=form, client=client, clients=_ticket_accessible_clients())

        if not can_access_client(int(client_id)):
            flash("Access denied. You can only access your assigned clients.", "error")
            return render_template('tickets/new.html', form=form, client=None, clients=_ticket_accessible_clients())
        
        # Use transaction context manager
        try:
            with db_transaction():
                # Generate ticket number
                ticket_number = generate_ticket_number()
                
                # Get SLA for alert type and priority
                sla_hours = get_sla_for_alert_type(form.alert_type.data, form.priority.data)
                sla_deadline = datetime.utcnow() + timedelta(hours=sla_hours)
                
                # Create ticket
                ticket = ServiceTicket(
                    ticket_number=ticket_number,
                    client_id=int(client_id),
                    title=form.title.data,
                    description=form.description.data,
                    alert_type=form.alert_type.data,
                    priority=form.priority.data,
                    sla_hours=sla_hours,
                    sla_deadline=sla_deadline,
                    assigned_to=form.assigned_to.data if form.assigned_to.data else None,
                    created_by=current_user.id
                )
                
                db.session.add(ticket)
                db.session.flush()  # Get ticket ID without committing
                
                # Create alert for this ticket (in same transaction)
                try:
                    alert = Alert(
                        alert_type='service_ticket',
                        alert_subtype=form.alert_type.data,
                        severity=form.priority.data,
                        client_id=int(client_id),
                        title=f'Service Ticket: {ticket_number}',
                        description=f'{form.title.data} - {form.description.data[:200]}',
                        sla_timeline=sla_hours,
                        user_id=form.assigned_to.data if form.assigned_to.data else None
                    )
                    db.session.add(alert)
                    logger.info(f"Created alert for ticket {ticket_number}")
                except Exception as e:
                    logger.error(f"Error creating alert for ticket: {str(e)}")
                    # Don't fail ticket creation if alert creation fails
                    # But transaction will rollback if we raise
                
                # Transaction commits here if no exception
                
        except Exception as e:
            logger.error(f"Error creating ticket: {str(e)}", exc_info=True)
            flash('Failed to create ticket. Please try again.', 'error')
            return render_template('tickets/new.html', form=form, client=client, clients=_ticket_accessible_clients())
        
        flash(f'Ticket {ticket_number} created successfully.', 'success')
        
        if client_id:
            return redirect(url_for('clients.client_details', client_id=client_id))
        return redirect(url_for('tickets.list_tickets'))
    
    # GET request - show form
    clients = _ticket_accessible_clients()
    return render_template('tickets/new.html', form=form, client=client, clients=clients)

@tickets_bp.route('/tickets/<int:ticket_id>')
@login_required
@handle_errors
def view_ticket(ticket_id):
    """View a specific service ticket"""
    if ticket_id <= 0:
        flash('Invalid ticket ID.', 'error')
        return redirect(url_for('tickets.list_tickets'))
    
    ticket = ServiceTicket.query.get_or_404(ticket_id)
    # If the snooze expired, wake it up before rendering
    if getattr(ticket, 'snooze_expired', False):
        try:
            with db_transaction():
                ticket.status = ticket.pre_snooze_status or 'open'
                ticket.pre_snooze_status = None
                ticket.snoozed_until = None
                ticket.snoozed_at = None
                ticket.snoozed_by = None
                ticket.snooze_reason = None
                ticket.updated_at = datetime.utcnow()
                db.session.add(ticket)
        except Exception:
            logger.exception("Failed to wake expired snooze for ticket %s", ticket_id)

    snooze_form = ServiceTicketSnoozeForm()
    unsnooze_form = ServiceTicketUnsnoozeForm()
    return render_template('tickets/view.html', ticket=ticket, snooze_form=snooze_form, unsnooze_form=unsnooze_form)

@tickets_bp.route('/tickets/<int:ticket_id>/edit', methods=['GET', 'POST'])
@login_required
@handle_errors
def edit_ticket(ticket_id):
    """Edit a service ticket"""
    if ticket_id <= 0:
        flash('Invalid ticket ID.', 'error')
        return redirect(url_for('tickets.list_tickets'))
    
    ticket = ServiceTicket.query.get_or_404(ticket_id)
    # If the snooze expired, wake it up before editing
    if getattr(ticket, 'snooze_expired', False):
        try:
            with db_transaction():
                ticket.status = ticket.pre_snooze_status or 'open'
                ticket.pre_snooze_status = None
                ticket.snoozed_until = None
                ticket.snoozed_at = None
                ticket.snoozed_by = None
                ticket.snooze_reason = None
                ticket.updated_at = datetime.utcnow()
                db.session.add(ticket)
        except Exception:
            logger.exception("Failed to wake expired snooze for ticket %s", ticket_id)

    form = ServiceTicketUpdateForm(obj=ticket)
    
    # Populate assigned_to dropdown
    form.assigned_to.choices = [(0, 'Unassigned')] + [(u.id, u.username) for u in User.query.filter_by(is_active=True).all()]
    
    if form.validate_on_submit():
        try:
            with db_transaction():
                ticket.status = form.status.data
                ticket.priority = form.priority.data
                ticket.assigned_to = form.assigned_to.data if form.assigned_to.data else None
                ticket.resolution_notes = form.resolution_notes.data
                ticket.updated_at = datetime.utcnow()
                
                # Update resolved_at if status changed to resolved
                if form.status.data in ['resolved', 'closed'] and ticket.status not in ['resolved', 'closed']:
                    ticket.resolved_at = datetime.utcnow()
                    ticket.resolved_by = current_user.id
                
                # Update closed_at if status changed to closed
                if form.status.data == 'closed' and ticket.status != 'closed':
                    ticket.closed_at = datetime.utcnow()
                
                # Update SLA breach status
                if ticket.sla_deadline and datetime.utcnow() > ticket.sla_deadline:
                    ticket.sla_breached = True
                
                db.session.add(ticket)
                
                # Update associated alert
                try:
                    alert = Alert.query.filter_by(
                        alert_type='service_ticket',
                        client_id=ticket.client_id
                    ).filter(
                        Alert.description.like(f'%{ticket.ticket_number}%')
                    ).first()
                    
                    if alert:
                        if form.status.data in ['resolved', 'closed']:
                            alert.resolve(current_user.id, form.resolution_notes.data)
                        else:
                            alert.status = 'active'  # Reactivate if needed
                        db.session.add(alert)
                except Exception as e:
                    logger.error(f"Error updating alert for ticket: {str(e)}")
                    # Don't fail ticket update if alert update fails
                
                # Transaction commits here
                
        except Exception as e:
            logger.error(f"Error updating ticket: {str(e)}", exc_info=True)
            flash('Failed to update ticket. Please try again.', 'error')
            return render_template('tickets/edit.html', form=form, ticket=ticket)
        
        flash('Ticket updated successfully.', 'success')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))
    
    return render_template('tickets/edit.html', form=form, ticket=ticket)

@tickets_bp.route('/tickets/<int:ticket_id>/delete', methods=['POST'])
@login_required
@handle_errors
def delete_ticket(ticket_id):
    """Delete a service ticket"""
    if ticket_id <= 0:
        flash('Invalid ticket ID.', 'error')
        return redirect(url_for('tickets.list_tickets'))
    
    ticket = ServiceTicket.query.get_or_404(ticket_id)
    
    try:
        with db_transaction():
            db.session.delete(ticket)
            # Transaction commits here
    except Exception as e:
        logger.error(f"Error deleting ticket: {str(e)}", exc_info=True)
        flash('Failed to delete ticket. Please try again.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))
    
    flash('Ticket deleted successfully.', 'success')
    return redirect(url_for('tickets.list_tickets'))


@tickets_bp.route('/tickets/<int:ticket_id>/snooze', methods=['POST'])
@login_required
@handle_errors
def snooze_ticket(ticket_id):
    """Snooze (pause) a service ticket until a specified time."""
    if ticket_id <= 0:
        flash('Invalid ticket ID.', 'error')
        return redirect(url_for('tickets.list_tickets'))

    ticket = ServiceTicket.query.get_or_404(ticket_id)
    form = ServiceTicketSnoozeForm()

    if not form.validate_on_submit():
        flash('Invalid snooze request. Please check the date/time.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    # Disallow snoozing resolved/closed/cancelled tickets
    if ticket.status in ['resolved', 'closed', 'cancelled']:
        flash('Cannot snooze a resolved/closed/cancelled ticket.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    snoozed_until = form.snoozed_until.data
    if snoozed_until and snoozed_until <= datetime.utcnow():
        flash('Snooze until must be in the future.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    try:
        with db_transaction():
            if ticket.status != 'snoozed':
                ticket.pre_snooze_status = ticket.status
            ticket.status = 'snoozed'
            ticket.snoozed_until = snoozed_until
            ticket.snoozed_at = datetime.utcnow()
            ticket.snoozed_by = current_user.id
            ticket.snooze_reason = form.snooze_reason.data
            ticket.updated_at = datetime.utcnow()
            db.session.add(ticket)
    except Exception:
        logger.exception("Failed to snooze ticket %s", ticket_id)
        flash('Failed to snooze ticket. Please try again.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    flash('Ticket snoozed successfully.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))


@tickets_bp.route('/tickets/<int:ticket_id>/unsnooze', methods=['POST'])
@login_required
@handle_errors
def unsnooze_ticket(ticket_id):
    """Unsnooze a ticket immediately."""
    if ticket_id <= 0:
        flash('Invalid ticket ID.', 'error')
        return redirect(url_for('tickets.list_tickets'))

    ticket = ServiceTicket.query.get_or_404(ticket_id)
    form = ServiceTicketUnsnoozeForm()

    if not form.validate_on_submit():
        flash('Invalid request.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    if ticket.status != 'snoozed':
        flash('Ticket is not snoozed.', 'info')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    try:
        with db_transaction():
            ticket.status = ticket.pre_snooze_status or 'open'
            ticket.pre_snooze_status = None
            ticket.snoozed_until = None
            ticket.snoozed_at = None
            ticket.snoozed_by = None
            ticket.snooze_reason = None
            ticket.updated_at = datetime.utcnow()
            db.session.add(ticket)
    except Exception:
        logger.exception("Failed to unsnooze ticket %s", ticket_id)
        flash('Failed to unsnooze ticket. Please try again.', 'error')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))

    flash('Ticket unsnoozed.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket_id))
