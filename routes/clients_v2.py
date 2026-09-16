"""
Clients Routes v2 - Using Simple Service Classes

This demonstrates how to use service classes instead of direct database calls
for better organization and maintainability.
"""

from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime
import logging

# Import the simple service
from services.client_service import ClientService

logger = logging.getLogger(__name__)

# Create blueprint
clients_v2_bp = Blueprint('clients_v2', __name__)

_VALID_CLIENT_ACTIVITY = frozenset({'active', 'inactive', 'all'})


def _client_activity_from_request():
    """Resolve list filter: active (default), inactive, or all."""
    activity = (request.args.get('activity') or '').strip().lower()
    if activity in _VALID_CLIENT_ACTIVITY:
        return activity
    if request.args.get('show_inactive', 'false').lower() == 'true':
        return 'all'
    return 'active'


def _apply_client_activity_filter(filters, activity):
    if activity == 'active':
        filters['active_only'] = True
    elif activity == 'inactive':
        filters['is_active'] = False


@clients_v2_bp.before_request
def _clients_v2_enforce_client_access():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            return render_template('500.html'), 500
    return decorated_function

@clients_v2_bp.route('/')
@login_required
@handle_errors
def list_clients():
    """
    List all clients using ClientService instead of direct database calls.
    
    This demonstrates the benefits of using service classes:
    - Clean separation of concerns
    - Consistent error handling
    - Easy to test and maintain
    - No import issues
    """
    try:
        activity_filter = _client_activity_from_request()
        filters = {}
        if request.args.get('search'):
            filters['search'] = request.args.get('search')
        if request.args.get('risk_profile'):
            filters['risk_profile'] = request.args.get('risk_profile')
        _apply_client_activity_filter(filters, activity_filter)
        
        clients = ClientService.get_clients(filters)
        
        # Get additional data (same as v1)
        from models import AssetAllocationModel, SecurityAllocationModel, AssetClass
        asset_models = {m.id: m for m in AssetAllocationModel.query.all()}
        security_models = {m.id: m for m in SecurityAllocationModel.query.all()}
        asset_classes = AssetClass.query.all()
        
        # Health scores for listed clients only (batched; skip with ?skip_status=1)
        client_statuses = {}
        if request.args.get('skip_status', 'false').lower() != 'true':
            try:
                from client_status_service import ClientStatusService
                client_ids = [c.id for c in clients]
                status_data = ClientStatusService.get_clients_status(client_ids)
                client_statuses = {row['client_id']: row for row in status_data}
            except Exception as e:
                logger.error(f"Could not fetch client statuses: {str(e)}")
        
        return render_template('clients/clients.html', 
                             clients=clients, 
                             asset_models=asset_models, 
                             security_models=security_models,
                             asset_classes=asset_classes,
                             client_statuses=client_statuses,
                             activity_filter=activity_filter)
        
    except Exception as e:
        logger.error(f"Error in list_clients: {str(e)}")
        flash('Error loading clients. Please try again.', 'error')
        return render_template('clients/clients.html', 
                             clients=[], 
                             asset_models={}, 
                             security_models={},
                             asset_classes=[],
                             client_statuses={},
                             activity_filter='active')

@clients_v2_bp.route('/api/')
@login_required
@handle_errors
def list_clients_api():
    """
    API endpoint for clients using ClientService.
    Returns JSON data for frontend JavaScript consumption.
    """
    try:
        activity_filter = _client_activity_from_request()
        filters = {}
        if request.args.get('search'):
            filters['search'] = request.args.get('search')
        if request.args.get('risk_profile'):
            filters['risk_profile'] = request.args.get('risk_profile')
        if request.args.get('advisor_id'):
            filters['advisor_id'] = request.args.get('advisor_id')
        _apply_client_activity_filter(filters, activity_filter)
        
        clients = ClientService.get_clients(filters)
        
        client_statuses = {}
        try:
            from client_status_service import ClientStatusService
            status_data = ClientStatusService.get_clients_status([c.id for c in clients])
            client_statuses = {row['client_id']: row for row in status_data}
        except Exception as e:
            logger.warning(f"Could not fetch client statuses: {str(e)}")
        
        # Format response data (same format as v1 API)
        clients_data = []
        for client in clients:
            health_status = client_statuses.get(client.id, {}).get('status', 'unknown')
            client_data = {
                'id': client.id,
                'name': client.name,
                'email': client.email,
                'phone': client.phone,
                'address': client.address,
                'risk_profile': client.risk_profile,
                'is_active': bool(getattr(client, 'is_active', True)),
                'created_at': client.created_at.isoformat() if client.created_at else None,
                'user_id': client.user_id,
                'advisor_id': client.advisor_id,
                'health_status': health_status,
                'status': health_status,
                'health_score': client_statuses.get(client.id, {}).get('score', 0),
                'active_alerts': client_statuses.get(client.id, {}).get('details', {}).get('total_active_alerts', 0),
            }
            clients_data.append(client_data)
        
        return jsonify({
            'success': True,
            'data': clients_data,
            'total': len(clients_data)
        })
        
    except Exception as e:
        logger.error(f"Error in list_clients_api: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error loading clients: {str(e)}'
        }), 500

@clients_v2_bp.route('/<int:client_id>')
@login_required
def client_details(client_id):
    """Delegate to clients blueprint: client_details.html needs the full context only v1 builds."""
    return redirect(url_for('clients.client_details', client_id=client_id))

@clients_v2_bp.route('/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
@handle_errors
def edit_client(client_id):
    """
    Edit client using ClientService instead of direct database calls.
    
    This demonstrates:
    - Service-based data access
    - Consistent error handling
    - Clean separation of concerns
    """
    try:
        # Use service instead of direct DB calls
        client = ClientService.get_client(client_id)
        
        if not client:
            flash('Client not found.', 'error')
            return redirect(url_for('clients_v2.list_clients'))
        
        if request.method == 'POST':
            # Get form data
            client_data = {
                'name': request.form.get('name'),
                'email': request.form.get('email'),
                'phone': request.form.get('phone'),
                'address': request.form.get('address'),
                'risk_profile': request.form.get('risk_profile')
            }
            
            # Use service to update client
            updated_client = ClientService.update_client(client_id, client_data)
            
            if updated_client:
                flash('Client updated successfully!', 'success')
                return redirect(url_for('clients.client_details', client_id=client_id))
            else:
                flash('Error updating client.', 'error')
        
        # Get additional data for form
        from models import AssetAllocationModel, SecurityAllocationModel, AssetClass
        asset_models = AssetAllocationModel.query.all()
        security_models = SecurityAllocationModel.query.all()
        asset_classes = AssetClass.query.all()
        
        return render_template('clients/edit_client.html', 
                             client=client,
                             asset_models=asset_models,
                             security_models=security_models,
                             asset_classes=asset_classes)
        
    except Exception as e:
        logger.error(f"Error in edit_client for client {client_id}: {str(e)}")
        flash('Error loading client for editing. Please try again.', 'error')
        return redirect(url_for('clients_v2.list_clients'))

@clients_v2_bp.route('/add', methods=['GET', 'POST'])
@login_required
@handle_errors
def add_client():
    """
    Add new client using ClientService.
    
    This demonstrates:
    - Service-based data creation
    - Consistent error handling
    - Clean separation of concerns
    """
    from routes.forms import ClientForm
    
    form = ClientForm()
    if form.validate_on_submit():
        # Check if client with this email already exists using service
        existing_clients = ClientService.get_clients({'search': form.email.data})
        if any(client.email == form.email.data for client in existing_clients):
            flash(f'A client with email "{form.email.data}" already exists. Please use a different email address.', 'error')
            return render_template('clients/add_client.html', form=form)
        
        try:
            from services.client_email_helpers import format_email_list, parse_email_list, validate_email_list

            secondary_valid, secondary_invalid = validate_email_list(
                parse_email_list(form.secondary_emails.data if hasattr(form, "secondary_emails") else "")
            )
            if secondary_invalid:
                flash(f"Invalid secondary email(s): {', '.join(secondary_invalid)}", "error")
                return render_template("clients/add_client.html", form=form)

            client_data = {
                'name': form.name.data,
                'email': form.email.data,
                'secondary_emails': format_email_list(secondary_valid) or None,
                'phone': form.phone.data,
                'address': form.address.data,
                'risk_profile': form.risk_profile.data,
                'user_id': current_user.id
            }
            
            client = ClientService.create_client(client_data)
            flash('Client added successfully!', 'success')
            return redirect(url_for('clients_v2.list_clients'))
            
        except Exception as e:
            if "Duplicate entry" in str(e) and "email" in str(e):
                flash(f'A client with email "{form.email.data}" already exists. Please use a different email address.', 'error')
            else:
                flash(f'Error adding client: {str(e)}', 'error')
            return render_template('clients/add_client.html', form=form)
    
    return render_template('clients/add_client.html', form=form)
