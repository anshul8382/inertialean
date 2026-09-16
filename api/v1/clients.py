"""
Client Management API v1
Comprehensive API for all client-related operations
"""
from flask import Blueprint, request, current_app
from flask_login import current_user, login_required
from api.core.response import APIResponse
# from api.core.decorators import api_auth_required, api_rate_limit
from api.core.exceptions import ValidationError, NotFoundError, ConflictError, ForbiddenError
from models import (
    Client, Lead, Agreement, ModelAssignment, AssetAllocationModel, 
    SecurityAllocationModel, ReviewSchedule, ReviewWorkflow, 
    ClientAdvisorAssignment, User
)
from extensions import db
from datetime import datetime, date
from client_status_service import ClientStatusService
from services.client_data_redaction import redact_client_dict, redact_contact_pii_dict
from services.permission_service import (
    user_can_view_client_contact_pii,
    user_can_view_sensitive_client_data,
)
from access_control import (
    can_access_client,
    get_accessible_clients,
    scope_clients_query,
    user_can_view_all_clients,
)
import logging

logger = logging.getLogger(__name__)

clients_bp = Blueprint('clients_api', __name__)


def _maybe_redact_client_payload(payload: dict) -> dict:
    """Mask contact PII for non-admin/manager; full financial redact without sensitive cap."""
    payload = redact_contact_pii_dict(
        payload, user_can_view_client_contact_pii(current_user)
    )
    return redact_client_dict(
        payload, user_can_view_sensitive_client_data(current_user)
    )

# ============================================================================
# CLIENT CRUD OPERATIONS
# ============================================================================

@clients_bp.route('/', methods=['GET'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=30)
def list_clients():
    """
    List all clients with optional filtering and pagination
    
    Query Parameters:
        - page: Page number (default: 1)
        - per_page: Items per page (default: 20, max: 100)
        - search: Search by name or email
        - risk_profile: Filter by risk profile
        - advisor_id: Filter by advisor
        - status: Filter by client status
    """
    try:
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        search = request.args.get('search', '').strip()
        risk_profile = request.args.get('risk_profile', '').strip()
        advisor_id = request.args.get('advisor_id', type=int)
        status = request.args.get('status', '').strip()
        show_inactive = request.args.get('show_inactive', 'false').lower() == 'true'
        is_active_param = request.args.get('is_active')
        
        # Build query — advisors only see assigned clients; admin/manager see all
        query = scope_clients_query(Client.query)
        
        if is_active_param is not None:
            query = query.filter(Client.is_active == (is_active_param.lower() == 'true'))
        elif not show_inactive:
            query = query.filter(Client.is_active == True)
        
        # Apply filters
        if search:
            query = query.filter(
                (Client.name.contains(search)) | 
                (Client.email.contains(search))
            )
        
        if risk_profile:
            query = query.filter(Client.risk_profile == risk_profile)
        
        # advisor_id filter only for admin/manager (cannot widen advisor scope)
        if advisor_id and user_can_view_all_clients():
            query = query.filter(Client.advisor_id == advisor_id)
        
        # Get total count
        total = query.count()
        
        # Apply pagination
        clients = query.offset((page - 1) * per_page).limit(per_page).all()
        
        # Optional: compute status as-of a date (for month-end snapshots)
        as_of = None
        as_of_str = request.args.get('as_of') or request.args.get('as_of_date')
        if as_of_str:
            try:
                as_of = datetime.fromisoformat(as_of_str.replace('Z', '+00:00'))
                if as_of.tzinfo is not None:
                    as_of = as_of.astimezone(tz=None).replace(tzinfo=None)
            except Exception:
                as_of = None

        # Get client statuses
        client_statuses = {}
        try:
            for client in clients:
                status_data = ClientStatusService.calculate_client_status(client.id, as_of=as_of)
                if 'error' not in status_data:
                    client_statuses[client.id] = status_data
        except Exception as e:
            logger.warning(f"Could not fetch client statuses: {str(e)}")
        
        # Format response data
        clients_data = []
        for client in clients:
            client_data = {
                'id': client.id,
                'name': client.name,
                'email': client.email,
                'phone': client.phone,
                'address': client.address,
                'risk_profile': client.risk_profile,
                'risk_profile_updated_at': client.risk_profile_updated_at.isoformat() if client.risk_profile_updated_at else None,
                'created_at': client.created_at.isoformat() if client.created_at else None,
                'advisor_id': client.advisor_id,
                'advisor_name': client.advisor.name if client.advisor else None,
                'is_active': bool(client.is_active),
                'health_status': client_statuses.get(client.id, {}).get('status', 'unknown'),
                'status': client_statuses.get(client.id, {}).get('status', 'unknown'),
                'health_score': client_statuses.get(client.id, {}).get('score', 0),
                'active_alerts': client_statuses.get(client.id, {}).get('details', {}).get('total_active_alerts', 0)
            }
            clients_data.append(_maybe_redact_client_payload(client_data))
        
        return APIResponse.paginated(
            data=clients_data,
            page=page,
            per_page=per_page,
            total=total,
            message=f"Retrieved {len(clients_data)} clients"
        )
        
    except Exception as e:
        logger.error(f"Error listing clients: {str(e)}")
        raise

@clients_bp.route('/<int:client_id>', methods=['GET'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=60)
def get_client(client_id):
    """
    Get detailed client information including status and portfolio summary
    """
    try:
        client = Client.query.get_or_404(client_id)
        if not can_access_client(client_id):
            raise ForbiddenError("You can only access your assigned clients.")
        
        # Get client status
        client_status = None
        try:
            client_status = ClientStatusService.calculate_client_status(client_id)
            if 'error' in client_status:
                client_status = None
        except Exception as e:
            logger.warning(f"Could not fetch client status for {client_id}: {str(e)}")
        
        # Get latest model assignment (some clients may have multiple rows historically)
        model_assignment = (
            ModelAssignment.query.filter_by(client_id=client_id)
            .order_by(ModelAssignment.assigned_at.is_(None), ModelAssignment.assigned_at.desc(), ModelAssignment.id.desc())
            .first()
        )
        
        # Get review schedule
        review_schedule = ReviewSchedule.query.filter_by(client_id=client_id, is_active=True).first()
        
        # Get advisor assignment
        advisor_assignment = ClientAdvisorAssignment.query.filter_by(
            client_id=client_id, is_active=True
        ).first()
        
        # Get lead information
        lead = Lead.query.filter_by(client_id=client_id).first()
        
        # Get active agreement
        active_agreement = None
        if lead:
            # Consider signed, completed, and active agreements as valid
            active_agreement = Agreement.query.filter(
                Agreement.lead_id == lead.id,
                Agreement.status.in_(['active', 'signed', 'completed'])
            ).order_by(Agreement.created_at.desc()).first()
        
        # Prefer per-asset-class Equity security model (if configured) for display.
        equity_security_model = None
        equity_security_model_id = None
        if model_assignment:
            try:
                from models import AssetClass, ModelAssignmentSecurityModel

                equity_ac = AssetClass.query.filter_by(name="Equity").first()
                if equity_ac:
                    row = (
                        ModelAssignmentSecurityModel.query.options(db.joinedload(ModelAssignmentSecurityModel.security_model))
                        .filter_by(model_assignment_id=model_assignment.id, asset_class_id=equity_ac.id)
                        .order_by(ModelAssignmentSecurityModel.id.desc())
                        .first()
                    )
                    if row and row.security_model:
                        equity_security_model = row.security_model.name
                        equity_security_model_id = row.security_model.id
            except Exception as e:
                logger.debug("Equity per-asset-class model lookup skipped: %s", e)

        # Format response
        client_data = {
            'id': client.id,
            'name': client.name,
            'email': client.email,
            'phone': client.phone,
            'address': client.address,
            'risk_profile': client.risk_profile,
            'risk_profile_updated_at': client.risk_profile_updated_at.isoformat() if client.risk_profile_updated_at else None,
            'date_of_birth': client.date_of_birth.isoformat() if client.date_of_birth else None,
            'created_at': client.created_at.isoformat() if client.created_at else None,
            'user_id': client.user_id,
            'advisor_id': client.advisor_id,
            'is_active': bool(client.is_active),
            'advisor': {
                'id': client.advisor.id,
                'name': client.advisor.name
            } if client.advisor else None,
            'status': client_status,
            'model_assignment': {
                'id': model_assignment.id,
                'asset_model_id': model_assignment.asset_model_id,
                'asset_model_name': model_assignment.asset_model.name if model_assignment and model_assignment.asset_model else None,
                'stock_model_id': model_assignment.stock_model_id,
                'stock_model_name': model_assignment.stock_model.name if model_assignment and model_assignment.stock_model else None,
                # Back-compat alias used by some screens
                'security_model_name': model_assignment.stock_model.name if model_assignment and model_assignment.stock_model else None,
                # Preferred Equity per-asset-class model (if present)
                'equity_security_model_id': equity_security_model_id,
                'equity_security_model_name': equity_security_model,
                'assigned_at': model_assignment.assigned_at.isoformat() if model_assignment else None
            } if model_assignment else None,
            'review_schedule': {
                'id': review_schedule.id,
                'frequency': review_schedule.frequency,
                'next_review_date': review_schedule.next_review_date.isoformat() if review_schedule and review_schedule.next_review_date else None,
                'is_active': review_schedule.is_active if review_schedule else None
            } if review_schedule else None,
            'advisor_assignment': {
                'id': advisor_assignment.id,
                'advisor_id': advisor_assignment.advisor_id,
                'advisor_name': advisor_assignment.advisor.name if advisor_assignment and advisor_assignment.advisor else None,
                'assigned_at': advisor_assignment.assigned_at.isoformat() if advisor_assignment else None
            } if advisor_assignment else None,
            'lead': {
                'id': lead.id,
                'name': lead.name,
                'status': lead.status,
                'created_at': lead.created_at.isoformat() if lead else None
            } if lead else None,
            'active_agreement': {
                'id': active_agreement.id,
                'status': active_agreement.status,
                'effective_date': active_agreement.effective_date.isoformat() if active_agreement and active_agreement.effective_date else None,
                'billing_frequency': active_agreement.billing_frequency if active_agreement else None,
                'billing_status': active_agreement.billing_status if active_agreement else None
            } if active_agreement else None
        }
        
        return _maybe_redact_client_payload(client_data)
        
    except Exception as e:
        logger.error(f"Error getting client {client_id}: {str(e)}")
        raise

@clients_bp.route('/', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=10)
def create_client():
    """
    Create a new client
    
    Required fields:
        - name: Client name
        - email: Client email (must be unique)
        - phone: Client phone number
    
    Optional fields:
        - address: Client address
        - risk_profile: Risk profile (conservative, moderate, aggressive)
        - date_of_birth: Date of birth
        - advisor_id: Assigned advisor ID
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'email', 'phone']
        for field in required_fields:
            if not data.get(field):
                raise ValidationError(f"Field '{field}' is required")
        
        # Check if email already exists
        existing_client = Client.query.filter_by(email=data['email']).first()
        if existing_client:
            raise ConflictError(f"Client with email '{data['email']}' already exists")
        
        # Create new client
        client = Client(
            name=data['name'],
            email=data['email'],
            phone=data['phone'],
            address=data.get('address'),
            risk_profile=data.get('risk_profile'),
            date_of_birth=datetime.strptime(data['date_of_birth'], '%Y-%m-%d').date() if data.get('date_of_birth') else None,
            user_id=current_user.id,
            advisor_id=data.get('advisor_id'),
            is_active=bool(data.get('is_active', True)),
            created_at=datetime.utcnow()
        )
        
        db.session.add(client)
        db.session.commit()
        
        return APIResponse.success(
            data={
                'id': client.id,
                'name': client.name,
                'email': client.email,
                'phone': client.phone,
                'address': client.address,
                'risk_profile': client.risk_profile,
                'is_active': bool(client.is_active),
                'created_at': client.created_at.isoformat()
            },
            message="Client created successfully",
            status_code=201
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating client: {str(e)}")
        raise

@clients_bp.route('/<int:client_id>', methods=['PUT'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=30)
def update_client(client_id):
    """
    Update client information
    
    All fields are optional - only provided fields will be updated
    """
    try:
        client = Client.query.get_or_404(client_id)
        data = request.get_json()
        
        # Update fields if provided
        if 'name' in data:
            client.name = data['name']
        if 'email' in data:
            # Check if email already exists for another client
            existing_client = Client.query.filter_by(email=data['email']).filter(Client.id != client_id).first()
            if existing_client:
                raise ConflictError(f"Client with email '{data['email']}' already exists")
            client.email = data['email']
        if 'phone' in data:
            client.phone = data['phone']
        if 'address' in data:
            client.address = data['address']
        if 'risk_profile' in data:
            client.risk_profile = data['risk_profile']
            client.risk_profile_updated_at = datetime.utcnow()
        if 'date_of_birth' in data:
            client.date_of_birth = datetime.strptime(data['date_of_birth'], '%Y-%m-%d').date() if data['date_of_birth'] else None
        if 'advisor_id' in data:
            client.advisor_id = data['advisor_id']
        if 'is_active' in data:
            client.is_active = bool(data['is_active'])
        
        db.session.commit()
        
        return APIResponse.success(
            data={
                'id': client.id,
                'name': client.name,
                'email': client.email,
                'phone': client.phone,
                'address': client.address,
                'risk_profile': client.risk_profile,
                'risk_profile_updated_at': client.risk_profile_updated_at.isoformat() if client.risk_profile_updated_at else None,
                'date_of_birth': client.date_of_birth.isoformat() if client.date_of_birth else None,
                'advisor_id': client.advisor_id,
                'is_active': bool(client.is_active),
            },
            message="Client updated successfully"
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating client {client_id}: {str(e)}")
        raise

@clients_bp.route('/<int:client_id>', methods=['DELETE'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=5)
def delete_client(client_id):
    """
    Delete a client (soft delete - marks as inactive)
    """
    try:
        client = Client.query.get_or_404(client_id)
        
        # Check if client has active portfolios or holdings
        if client.portfolios:
            raise ConflictError("Cannot delete client with active portfolios")
        
        if client.holdings:
            raise ConflictError("Cannot delete client with holdings")
        
        client.is_active = False
        db.session.commit()
        
        return APIResponse.success(
            message="Client marked as inactive successfully"
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting client {client_id}: {str(e)}")
        raise

# ============================================================================
# CLIENT STATUS AND HEALTH
# ============================================================================

@clients_bp.route('/<int:client_id>/status', methods=['GET'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=60)
def get_client_status(client_id):
    """
    Get client health status and SLA violations
    """
    try:
        client = Client.query.get_or_404(client_id)
        
        # Get client status
        as_of = None
        as_of_str = request.args.get('as_of') or request.args.get('as_of_date')
        if as_of_str:
            try:
                # Accept ISO strings like 2025-12-01 or 2025-12-01T00:00:00Z
                as_of = datetime.fromisoformat(as_of_str.replace('Z', '+00:00'))
                # Use naive UTC for internal computations (consistent with existing code)
                if as_of.tzinfo is not None:
                    as_of = as_of.astimezone(tz=None).replace(tzinfo=None)
            except Exception:
                as_of = None

        client_status = ClientStatusService.calculate_client_status(client_id, as_of=as_of)
        
        if 'error' in client_status:
            raise NotFoundError(f"Could not calculate status for client {client_id}")
        
        return client_status
        
    except Exception as e:
        logger.error(f"Error getting client status {client_id}: {str(e)}")
        raise

@clients_bp.route('/status', methods=['GET'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=30)
def get_all_clients_status():
    """
    Get status for accessible clients (for dashboard/overview).
    Admin/manager: all clients; advisors: assigned only.
    """
    try:
        clients = get_accessible_clients()
        
        # Optional: compute status as-of a date (for month-end snapshots)
        as_of = None
        as_of_str = request.args.get('as_of') or request.args.get('as_of_date')
        if as_of_str:
            try:
                as_of = datetime.fromisoformat(as_of_str.replace('Z', '+00:00'))
                if as_of.tzinfo is not None:
                    as_of = as_of.astimezone(tz=None).replace(tzinfo=None)
            except Exception:
                as_of = None

        # Calculate status for each client
        client_statuses = {}
        for client in clients:
            try:
                status_data = ClientStatusService.calculate_client_status(client.id, as_of=as_of)
                if 'error' not in status_data:
                    client_statuses[client.id] = status_data
            except Exception as e:
                logger.warning(f"Could not fetch status for client {client.id}: {str(e)}")
                continue
        
        return {
            'total_clients': len(clients),
            'clients_with_status': len(client_statuses),
            'client_statuses': client_statuses
        }
        
    except Exception as e:
        logger.error(f"Error getting all clients status: {str(e)}")
        raise

# ============================================================================
# CLIENT MODEL ASSIGNMENT
# ============================================================================

@clients_bp.route('/<int:client_id>/model-assignment', methods=['GET'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=60)
def get_client_model_assignment(client_id):
    """
    Get client's model assignment
    """
    try:
        client = Client.query.get_or_404(client_id)
        
        model_assignment = (
            ModelAssignment.query.filter_by(client_id=client_id)
            .order_by(ModelAssignment.assigned_at.is_(None), ModelAssignment.assigned_at.desc(), ModelAssignment.id.desc())
            .first()
        )
        
        if not model_assignment:
            return {
                'client_id': client_id,
                'model_assignment': None,
                'available_models': {
                    'asset_models': [
                        {
                            'id': model.id,
                            'name': model.name,
                            'description': model.description
                        } for model in AssetAllocationModel.query.all()
                    ],
                    'security_models': [
                        {
                            'id': model.id,
                            'name': model.name,
                            'description': model.description
                        } for model in SecurityAllocationModel.query.all()
                    ]
                }
            }
        
        return {
            'client_id': client_id,
            'model_assignment': {
                'id': model_assignment.id,
                'asset_model_id': model_assignment.asset_model_id,
                'asset_model': {
                    'id': model_assignment.asset_model.id,
                    'name': model_assignment.asset_model.name,
                    'description': model_assignment.asset_model.description
                } if model_assignment.asset_model else None,
                'stock_model_id': model_assignment.stock_model_id,
                'stock_model': {
                    'id': model_assignment.stock_model.id,
                    'name': model_assignment.stock_model.name,
                    'description': model_assignment.stock_model.description
                } if model_assignment.stock_model else None,
                # Back-compat aliases
                'security_model_id': model_assignment.stock_model_id,
                'security_model': {
                    'id': model_assignment.stock_model.id,
                    'name': model_assignment.stock_model.name,
                    'description': model_assignment.stock_model.description
                } if model_assignment.stock_model else None,
                'assigned_at': model_assignment.assigned_at.isoformat() if model_assignment.assigned_at else None,
                'assigned_by': model_assignment.assigned_by
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting model assignment for client {client_id}: {str(e)}")
        raise

@clients_bp.route('/<int:client_id>/model-assignment', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=10)
def assign_client_model(client_id):
    """
    Assign or update client's model assignment
    
    Required fields:
        - asset_model_id: Asset allocation model ID
        - security_model_id: Security allocation model ID
    """
    try:
        client = Client.query.get_or_404(client_id)
        data = request.get_json()
        
        # Validate required fields
        if not data.get('asset_model_id'):
            raise ValidationError("asset_model_id is required")
        if not data.get('security_model_id'):
            raise ValidationError("security_model_id is required")
        
        # Check if models exist
        asset_model = AssetAllocationModel.query.get(data['asset_model_id'])
        if not asset_model:
            raise NotFoundError(f"Asset model {data['asset_model_id']} not found")
        
        security_model = SecurityAllocationModel.query.get(data['security_model_id'])
        if not security_model:
            raise NotFoundError(f"Security model {data['security_model_id']} not found")
        
        # Check if assignment already exists
        existing_assignment = ModelAssignment.query.filter_by(client_id=client_id).first()
        
        if existing_assignment:
            # Update existing assignment
            existing_assignment.asset_model_id = data['asset_model_id']
            existing_assignment.security_model_id = data['security_model_id']
            existing_assignment.assigned_at = datetime.utcnow()
            existing_assignment.assigned_by = current_user.id
        else:
            # Create new assignment
            model_assignment = ModelAssignment(
                client_id=client_id,
                asset_model_id=data['asset_model_id'],
                security_model_id=data['security_model_id'],
                assigned_at=datetime.utcnow(),
                assigned_by=current_user.id
            )
            db.session.add(model_assignment)
        
        db.session.commit()
        
        return APIResponse.success(
            message="Model assignment updated successfully"
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error assigning model to client {client_id}: {str(e)}")
        raise

# ============================================================================
# CLIENT REVIEW SCHEDULE
# ============================================================================

@clients_bp.route('/<int:client_id>/review-schedule', methods=['GET'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=60)
def get_client_review_schedule(client_id):
    """
    Get client's review schedule
    """
    try:
        client = Client.query.get_or_404(client_id)
        
        review_schedule = ReviewSchedule.query.filter_by(client_id=client_id, is_active=True).first()
        
        if not review_schedule:
            return {
                'client_id': client_id,
                'review_schedule': None
            }
        
        # Get next review workflow
        next_review_workflow = ReviewWorkflow.query.filter_by(
            schedule_id=review_schedule.id
        ).filter(
            ReviewWorkflow.status.in_(['initiated', 'sent', 'meeting'])
        ).order_by(ReviewWorkflow.review_date.asc()).first()
        
        return {
            'client_id': client_id,
            'review_schedule': {
                'id': review_schedule.id,
                'frequency': review_schedule.frequency,
                'next_review_date': review_schedule.next_review_date.isoformat() if review_schedule.next_review_date else None,
                'is_active': review_schedule.is_active,
                'created_at': review_schedule.created_at.isoformat(),
                'next_workflow': {
                    'id': next_review_workflow.id,
                    'status': next_review_workflow.status,
                    'review_date': next_review_workflow.review_date.isoformat() if next_review_workflow else None
                } if next_review_workflow else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting review schedule for client {client_id}: {str(e)}")
        raise

@clients_bp.route('/<int:client_id>/review-schedule', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=10)
def set_client_review_schedule(client_id):
    """
    Set or update client's review schedule
    
    Required fields:
        - frequency: Review frequency (monthly, quarterly, half_yearly, yearly)
        - next_review_date: Next review date (YYYY-MM-DD)
    """
    try:
        client = Client.query.get_or_404(client_id)
        data = request.get_json()
        
        # Validate required fields
        if not data.get('frequency'):
            raise ValidationError("frequency is required")
        if not data.get('next_review_date'):
            raise ValidationError("next_review_date is required")
        
        # Validate frequency
        valid_frequencies = ['monthly', 'quarterly', 'half_yearly', 'yearly']
        if data['frequency'] not in valid_frequencies:
            raise ValidationError(f"frequency must be one of: {', '.join(valid_frequencies)}")
        
        # Parse next review date
        try:
            next_review_date = datetime.strptime(data['next_review_date'], '%Y-%m-%d').date()
        except ValueError:
            raise ValidationError("next_review_date must be in YYYY-MM-DD format")
        
        # Check if schedule already exists
        existing_schedule = ReviewSchedule.query.filter_by(client_id=client_id, is_active=True).first()
        
        if existing_schedule:
            # Update existing schedule
            existing_schedule.frequency = data['frequency']
            existing_schedule.next_review_date = next_review_date
            existing_schedule.updated_at = datetime.utcnow()
        else:
            # Create new schedule
            review_schedule = ReviewSchedule(
                client_id=client_id,
                frequency=data['frequency'],
                next_review_date=next_review_date,
                is_active=True,
                created_at=datetime.utcnow()
            )
            db.session.add(review_schedule)
        
        db.session.commit()
        
        return APIResponse.success(
            message="Review schedule updated successfully"
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error setting review schedule for client {client_id}: {str(e)}")
        raise

# ============================================================================
# CLIENT ADVISOR ASSIGNMENT
# ============================================================================

@clients_bp.route('/<int:client_id>/advisor-assignment', methods=['GET'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=60)
def get_client_advisor_assignment(client_id):
    """
    Get client's advisor assignment
    """
    try:
        client = Client.query.get_or_404(client_id)
        
        advisor_assignment = ClientAdvisorAssignment.query.filter_by(
            client_id=client_id, is_active=True
        ).first()
        
        if not advisor_assignment:
            return {
                'client_id': client_id,
                'advisor_assignment': None,
                'available_advisors': [
                    {
                        'id': advisor.id,
                        'name': advisor.name,
                        'email': advisor.email
                    } for advisor in User.query.filter_by(role='advisor').all()
                ]
            }
        
        return {
            'client_id': client_id,
            'advisor_assignment': {
                'id': advisor_assignment.id,
                'advisor_id': advisor_assignment.advisor_id,
                'advisor': {
                    'id': advisor_assignment.advisor.id,
                    'name': advisor_assignment.advisor.name,
                    'email': advisor_assignment.advisor.email
                },
                'assigned_at': advisor_assignment.assigned_at.isoformat(),
                'assigned_by': advisor_assignment.assigned_by,
                'is_active': advisor_assignment.is_active,
                'notes': advisor_assignment.notes
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting advisor assignment for client {client_id}: {str(e)}")
        raise

@clients_bp.route('/<int:client_id>/advisor-assignment', methods=['POST'])
# @api_auth_required
# @api_rate_limit(requests_per_minute=10)
def assign_client_advisor(client_id):
    """
    Assign or update client's advisor
    
    Required fields:
        - advisor_id: Advisor user ID
    Optional fields:
        - notes: Assignment notes
    """
    try:
        client = Client.query.get_or_404(client_id)
        data = request.get_json()
        
        # Validate required fields
        if not data.get('advisor_id'):
            raise ValidationError("advisor_id is required")
        
        # Check if advisor exists
        advisor = User.query.get(data['advisor_id'])
        if not advisor:
            raise NotFoundError(f"Advisor {data['advisor_id']} not found")
        
        # Deactivate existing assignment
        existing_assignment = ClientAdvisorAssignment.query.filter_by(
            client_id=client_id, is_active=True
        ).first()
        
        if existing_assignment:
            existing_assignment.is_active = False
        
        # Create new assignment
        advisor_assignment = ClientAdvisorAssignment(
            client_id=client_id,
            advisor_id=data['advisor_id'],
            assigned_by=current_user.id,
            is_active=True,
            notes=data.get('notes'),
            assigned_at=datetime.utcnow()
        )
        db.session.add(advisor_assignment)
        
        # Update client's advisor_id
        client.advisor_id = data['advisor_id']
        
        db.session.commit()
        
        return APIResponse.success(
            message="Advisor assignment updated successfully"
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error assigning advisor to client {client_id}: {str(e)}")
        raise

# ============================================================================
# CLIENT UPLOAD
# ============================================================================

@clients_bp.route('/preview', methods=['POST'])
@login_required
def preview_client_file():
    """Preview client file before saving"""
    try:
        from flask import jsonify
        import pandas as pd
        from werkzeug.utils import secure_filename
        from decimal import Decimal
        
        if 'client_file' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No file provided'
            }), 400
        
        file = request.files['client_file']
        if file.filename == '':
            return jsonify({
                'success': False,
                'error': 'No file selected'
            }), 400
        
        # Check file extension
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            return jsonify({
                'success': False,
                'error': 'Please upload an Excel (.xlsx, .xls) or CSV file'
            }), 400
        
        # Read file
        try:
            if filename.lower().endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Error reading file: {str(e)}'
            }), 400
        
        if df.empty:
            return jsonify({
                'success': False,
                'error': 'File is empty'
            }), 400
        
        # Column mapping (case-insensitive)
        column_mapping = {
            'name': ['name', 'client name', 'client_name'],
            'email': ['email', 'e-mail', 'email address'],
            'phone': ['phone', 'phone number', 'mobile', 'contact'],
            'whatsapp_number': ['whatsapp', 'whatsapp number', 'whatsapp_number'],
            'address': ['address'],
            'risk_profile': ['risk profile', 'risk_profile', 'risk'],
            'date_of_birth': ['date of birth', 'date_of_birth', 'dob', 'birth date'],
            'starting_aua': ['starting aua', 'starting_aua', 'aua'],
            'designation': ['designation', 'title', 'position'],
            'linkedin_profile_url': ['linkedin', 'linkedin profile', 'linkedin_profile_url', 'linkedin url'],
            'date_of_joining': ['date of joining', 'date_of_joining', 'joining date'],
            'type_of_engagement': ['type of engagement', 'type_of_engagement', 'engagement'],
            'portfolio_inherited': ['portfolio inherited', 'portfolio_inherited', 'inherited'],
            'company_name': ['company name', 'company_name', 'company'],
            'industry': ['industry']
        }
        
        # Find actual column names
        df_columns_lower = {col.lower().strip(): col for col in df.columns}
        mapped_columns = {}
        for key, possible_names in column_mapping.items():
            for name in possible_names:
                if name.lower() in df_columns_lower:
                    mapped_columns[key] = df_columns_lower[name.lower()]
                    break
        
        # Log column mapping for debugging
        logger.info(f"Client upload preview - File columns: {list(df.columns)}")
        logger.info(f"Client upload preview - Mapped columns: {mapped_columns}")
        
        # Validate required columns
        if 'name' not in mapped_columns or 'email' not in mapped_columns:
            available_columns = ', '.join(df.columns)
            return jsonify({
                'success': False,
                'error': f'File must contain "Name" and "Email" columns. Found columns: {available_columns}',
                'file_columns': list(df.columns)
            }), 400
        
        # Process data
        errors = []
        processed_data = []
        
        for idx, row in df.iterrows():
            row_num = idx + 2  # Excel row number (1-indexed, +1 for header)
            row_errors = []
            
            # Get required fields - use safe access
            try:
                name_col = mapped_columns.get('name')
                if name_col and name_col in row.index:
                    name_val = row[name_col]
                    name = str(name_val).strip() if pd.notna(name_val) and name_val else ''
                else:
                    name = ''
                    if not name_col:
                        row_errors.append('Name column not found in file')
            except Exception as e:
                name = ''
                row_errors.append(f'Error reading name: {str(e)}')
            
            try:
                email_col = mapped_columns.get('email')
                if email_col and email_col in row.index:
                    email_val = row[email_col]
                    email = str(email_val).strip() if pd.notna(email_val) and email_val else ''
                else:
                    email = ''
                    if not email_col:
                        row_errors.append('Email column not found in file')
            except Exception as e:
                email = ''
                row_errors.append(f'Error reading email: {str(e)}')
            
            # Validate required fields
            if not name:
                if 'Name column not found' not in '; '.join(row_errors):
                    row_errors.append('Name is required')
            if not email:
                if 'Email column not found' not in '; '.join(row_errors):
                    row_errors.append('Email is required')
            elif '@' not in email:
                row_errors.append('Invalid email format')
            
            # Check if client exists (for update vs create indication)
            # Don't add as error - we'll update existing clients
            will_update = False
            if email and '@' in email:
                try:
                    existing = Client.query.filter_by(email=email).first()
                    if existing:
                        will_update = True
                except Exception as e:
                    logger.debug(f"Error checking existing email for row {row_num}: {str(e)}")
                    # Don't fail on check errors, just log
            
            # Parse optional fields
            phone = None
            if mapped_columns.get('phone') and mapped_columns['phone'] in row.index:
                try:
                    phone_val = row[mapped_columns['phone']]
                    if pd.notna(phone_val):
                        phone = str(phone_val).strip() if phone_val else None
                except:
                    pass
            
            whatsapp_number = None
            if mapped_columns.get('whatsapp_number') and mapped_columns['whatsapp_number'] in row.index:
                try:
                    whatsapp_val = row[mapped_columns['whatsapp_number']]
                    if pd.notna(whatsapp_val):
                        whatsapp_number = str(whatsapp_val).strip() if whatsapp_val else None
                except:
                    pass
            
            address = None
            if mapped_columns.get('address') and mapped_columns['address'] in row.index:
                try:
                    address_val = row[mapped_columns['address']]
                    if pd.notna(address_val):
                        address = str(address_val).strip() if address_val else None
                except:
                    pass
            
            risk_profile = None
            if mapped_columns.get('risk_profile') and mapped_columns['risk_profile'] in row.index:
                try:
                    risk_val = row[mapped_columns['risk_profile']]
                    if pd.notna(risk_val):
                        risk_profile = str(risk_val).strip().lower()
                        if risk_profile not in ['conservative', 'moderate', 'aggressive']:
                            risk_profile = None
                except:
                    pass
            
            # Parse dates
            date_of_birth = None
            if mapped_columns.get('date_of_birth') and mapped_columns['date_of_birth'] in row.index:
                try:
                    dob_val = row[mapped_columns['date_of_birth']]
                    if pd.notna(dob_val):
                        if isinstance(dob_val, str):
                            # Try different date formats
                            for fmt in ['%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y']:
                                try:
                                    date_of_birth = datetime.strptime(dob_val.strip(), fmt).date()
                                    break
                                except:
                                    continue
                        else:
                            date_of_birth = dob_val.date() if hasattr(dob_val, 'date') else None
                except Exception as e:
                    logger.debug(f"Error parsing date_of_birth for row {row_num}: {str(e)}")
                    pass
            
            date_of_joining = None
            if mapped_columns.get('date_of_joining') and mapped_columns['date_of_joining'] in row.index:
                try:
                    doj_val = row[mapped_columns['date_of_joining']]
                    if pd.notna(doj_val):
                        if isinstance(doj_val, str):
                            for fmt in ['%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y']:
                                try:
                                    date_of_joining = datetime.strptime(doj_val.strip(), fmt).date()
                                    break
                                except:
                                    continue
                        else:
                            date_of_joining = doj_val.date() if hasattr(doj_val, 'date') else None
                except Exception as e:
                    logger.debug(f"Error parsing date_of_joining for row {row_num}: {str(e)}")
                    pass
            
            # Parse numeric fields
            starting_aua = None
            if mapped_columns.get('starting_aua') and mapped_columns['starting_aua'] in row.index:
                try:
                    aua_val = row[mapped_columns['starting_aua']]
                    if pd.notna(aua_val):
                        starting_aua = float(aua_val)
                except Exception as e:
                    logger.debug(f"Error parsing starting_aua for row {row_num}: {str(e)}")
                    pass
            
            # Parse boolean
            portfolio_inherited = False
            if mapped_columns.get('portfolio_inherited') and mapped_columns['portfolio_inherited'] in row.index:
                try:
                    val = row[mapped_columns['portfolio_inherited']]
                    if pd.notna(val):
                        val_str = str(val).strip().upper()
                        portfolio_inherited = val_str in ['TRUE', 'YES', '1', 'Y']
                except Exception as e:
                    logger.debug(f"Error parsing portfolio_inherited for row {row_num}: {str(e)}")
                    pass
            
            # Get other fields
            designation = None
            if mapped_columns.get('designation') and mapped_columns['designation'] in row.index:
                try:
                    desig_val = row[mapped_columns['designation']]
                    if pd.notna(desig_val):
                        designation = str(desig_val).strip() if desig_val else None
                except:
                    pass
            
            linkedin_profile_url = None
            if mapped_columns.get('linkedin_profile_url') and mapped_columns['linkedin_profile_url'] in row.index:
                try:
                    linkedin_val = row[mapped_columns['linkedin_profile_url']]
                    if pd.notna(linkedin_val):
                        linkedin_profile_url = str(linkedin_val).strip() if linkedin_val else None
                except:
                    pass
            
            type_of_engagement = None
            if mapped_columns.get('type_of_engagement') and mapped_columns['type_of_engagement'] in row.index:
                try:
                    engagement_val = row[mapped_columns['type_of_engagement']]
                    if pd.notna(engagement_val):
                        type_of_engagement = str(engagement_val).strip() if engagement_val else None
                except:
                    pass
            
            company_name = None
            if mapped_columns.get('company_name') and mapped_columns['company_name'] in row.index:
                try:
                    company_val = row[mapped_columns['company_name']]
                    if pd.notna(company_val):
                        company_name = str(company_val).strip() if company_val else None
                except:
                    pass
            
            industry = None
            if mapped_columns.get('industry') and mapped_columns['industry'] in row.index:
                try:
                    industry_val = row[mapped_columns['industry']]
                    if pd.notna(industry_val):
                        industry = str(industry_val).strip() if industry_val else None
                except:
                    pass
            
            # Always add row to processed_data (even if it has errors) so it can be displayed in preview
            row_data = {
                'row': row_num,
                'name': name or '',
                'email': email or '',
                'phone': phone or '',
                'whatsapp_number': whatsapp_number or '',
                'address': address or '',
                'risk_profile': risk_profile or '',
                'date_of_birth': date_of_birth.isoformat() if date_of_birth else None,
                'starting_aua': starting_aua or 0,
                'designation': designation or '',
                'linkedin_profile_url': linkedin_profile_url or '',
                'date_of_joining': date_of_joining.isoformat() if date_of_joining else None,
                'type_of_engagement': type_of_engagement or '',
                'portfolio_inherited': portfolio_inherited,
                'company_name': company_name or '',
                'industry': industry or '',
                'will_update': will_update  # Flag to indicate if this will update existing client
            }
            processed_data.append(row_data)
            
            # Add to errors list if there are validation errors
            if row_errors:
                errors.append({
                    'row': row_num,
                    'message': '; '.join(row_errors)
                })
        
        # Log summary for debugging
        logger.info(f"Client upload preview: {len(processed_data)} valid, {len(errors)} errors out of {len(df)} total rows")
        if errors and len(errors) > 0:
            logger.warning(f"First 5 errors: {errors[:5]}")
        
        return jsonify({
            'success': True,
            'total_rows': len(df),
            'valid_rows': len(processed_data),
            'error_rows': len(errors),
            'preview_data': processed_data,
            'errors': errors[:100] if len(errors) > 100 else errors,  # Limit errors to first 100 for response
            'total_errors': len(errors),
            'file_columns': list(df.columns),  # Include file columns for debugging
            'mapped_columns': mapped_columns  # Include mapped columns for debugging
        })
        
    except Exception as e:
        logger.error(f"Error previewing client file: {str(e)}")
        from flask import jsonify
        return jsonify({
            'success': False,
            'error': f'Error processing file: {str(e)}'
        }), 500

@clients_bp.route('/upload', methods=['POST'])
@login_required
def upload_client_file():
    """Upload and process client file"""
    try:
        from flask import jsonify
        import pandas as pd
        from werkzeug.utils import secure_filename
        from decimal import Decimal
        
        if 'client_file' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No file provided'
            }), 400
        
        file = request.files['client_file']
        if file.filename == '':
            return jsonify({
                'success': False,
                'error': 'No file selected'
            }), 400
        
        # Check file extension
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(('.xlsx', '.xls', '.csv')):
            return jsonify({
                'success': False,
                'error': 'Please upload an Excel (.xlsx, .xls) or CSV file'
            }), 400
        
        # Read file
        try:
            if filename.lower().endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Error reading file: {str(e)}'
            }), 400
        
        if df.empty:
            return jsonify({
                'success': False,
                'error': 'File is empty'
            }), 400
        
        # Use same column mapping as preview
        column_mapping = {
            'name': ['name', 'client name', 'client_name'],
            'email': ['email', 'e-mail', 'email address'],
            'phone': ['phone', 'phone number', 'mobile', 'contact'],
            'whatsapp_number': ['whatsapp', 'whatsapp number', 'whatsapp_number'],
            'address': ['address'],
            'risk_profile': ['risk profile', 'risk_profile', 'risk'],
            'date_of_birth': ['date of birth', 'date_of_birth', 'dob', 'birth date'],
            'starting_aua': ['starting aua', 'starting_aua', 'aua'],
            'designation': ['designation', 'title', 'position'],
            'linkedin_profile_url': ['linkedin', 'linkedin profile', 'linkedin_profile_url', 'linkedin url'],
            'date_of_joining': ['date of joining', 'date_of_joining', 'joining date'],
            'type_of_engagement': ['type of engagement', 'type_of_engagement', 'engagement'],
            'portfolio_inherited': ['portfolio inherited', 'portfolio_inherited', 'inherited'],
            'company_name': ['company name', 'company_name', 'company'],
            'industry': ['industry']
        }
        
        # Find actual column names
        df_columns_lower = {col.lower().strip(): col for col in df.columns}
        mapped_columns = {}
        for key, possible_names in column_mapping.items():
            for name in possible_names:
                if name.lower() in df_columns_lower:
                    mapped_columns[key] = df_columns_lower[name.lower()]
                    break
        
        # Validate required columns
        if 'name' not in mapped_columns or 'email' not in mapped_columns:
            return jsonify({
                'success': False,
                'error': 'File must contain "Name" and "Email" columns'
            }), 400
        
        # Process and save clients
        saved_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []
        
        for idx, row in df.iterrows():
            row_num = idx + 2
            row_errors = []
            
            # Get required fields - use safe access
            try:
                name_col = mapped_columns.get('name')
                if name_col and name_col in row.index:
                    name_val = row[name_col]
                    name = str(name_val).strip() if pd.notna(name_val) and name_val else ''
                else:
                    name = ''
            except Exception as e:
                name = ''
                errors.append({
                    'row': row_num,
                    'message': f'Error reading name: {str(e)}'
                })
                continue
            
            try:
                email_col = mapped_columns.get('email')
                if email_col and email_col in row.index:
                    email_val = row[email_col]
                    email = str(email_val).strip() if pd.notna(email_val) and email_val else ''
                else:
                    email = ''
            except Exception as e:
                email = ''
                errors.append({
                    'row': row_num,
                    'message': f'Error reading email: {str(e)}'
                })
                continue
            
            if not name or not email or '@' not in email:
                errors.append({
                    'row': row_num,
                    'message': 'Invalid name or email'
                })
                continue
            
            # Check if client exists - if so, update instead of create
            existing_client = None
            try:
                existing_client = Client.query.filter_by(email=email).first()
            except Exception as e:
                logger.debug(f"Error checking existing email for row {row_num}: {str(e)}")
                # Continue anyway - will try to create new
            
            # Parse all fields (same logic as preview)
            phone = None
            if mapped_columns.get('phone') and mapped_columns['phone'] in row.index:
                try:
                    phone_val = row[mapped_columns['phone']]
                    if pd.notna(phone_val):
                        phone_str = str(phone_val).strip() if phone_val else None
                        # Validate phone - should be reasonable length and look like a phone number
                        # Phone field in DB is String(20), so truncate if too long
                        # Also check if it looks like an address (contains common address keywords)
                        if phone_str:
                            address_keywords = ['plot', 'street', 'road', 'avenue', 'lane', 'pune', 'mumbai', 'delhi', 'bangalore', '|']
                            is_likely_address = any(keyword.lower() in phone_str.lower() for keyword in address_keywords)
                            if is_likely_address or len(phone_str) > 50:
                                # This is likely address data in phone column - skip it
                                phone = None
                                logger.warning(f"Row {row_num}: Phone field contains address-like data, skipping: {phone_str[:50]}")
                            else:
                                # Truncate to 20 characters (DB limit)
                                phone = phone_str[:20] if len(phone_str) > 20 else phone_str
                except Exception as e:
                    logger.debug(f"Error parsing phone for row {row_num}: {str(e)}")
                    pass
            
            whatsapp_number = None
            if mapped_columns.get('whatsapp_number') and mapped_columns['whatsapp_number'] in row.index:
                try:
                    whatsapp_val = row[mapped_columns['whatsapp_number']]
                    if pd.notna(whatsapp_val):
                        whatsapp_number = str(whatsapp_val).strip() if whatsapp_val else None
                except:
                    pass
            
            address = None
            if mapped_columns.get('address') and mapped_columns['address'] in row.index:
                try:
                    address_val = row[mapped_columns['address']]
                    if pd.notna(address_val):
                        address = str(address_val).strip() if address_val else None
                except:
                    pass
            
            risk_profile = None
            if mapped_columns.get('risk_profile') and mapped_columns['risk_profile'] in row.index:
                try:
                    risk_val = row[mapped_columns['risk_profile']]
                    if pd.notna(risk_val):
                        risk_profile = str(risk_val).strip().lower()
                        if risk_profile not in ['conservative', 'moderate', 'aggressive']:
                            risk_profile = None
                except:
                    pass
            
            date_of_birth = None
            if mapped_columns.get('date_of_birth') and mapped_columns['date_of_birth'] in row.index:
                try:
                    dob_val = row[mapped_columns['date_of_birth']]
                    if pd.notna(dob_val):
                        if isinstance(dob_val, str):
                            for fmt in ['%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y']:
                                try:
                                    date_of_birth = datetime.strptime(dob_val.strip(), fmt).date()
                                    break
                                except:
                                    continue
                        else:
                            date_of_birth = dob_val.date() if hasattr(dob_val, 'date') else None
                except:
                    pass
            
            date_of_joining = None
            if mapped_columns.get('date_of_joining') and mapped_columns['date_of_joining'] in row.index:
                try:
                    doj_val = row[mapped_columns['date_of_joining']]
                    if pd.notna(doj_val):
                        if isinstance(doj_val, str):
                            for fmt in ['%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y']:
                                try:
                                    date_of_joining = datetime.strptime(doj_val.strip(), fmt).date()
                                    break
                                except:
                                    continue
                        else:
                            date_of_joining = doj_val.date() if hasattr(doj_val, 'date') else None
                except:
                    pass
            
            starting_aua = None
            if mapped_columns.get('starting_aua') and mapped_columns['starting_aua'] in row.index:
                try:
                    aua_val = row[mapped_columns['starting_aua']]
                    if pd.notna(aua_val):
                        starting_aua = Decimal(str(aua_val))
                except:
                    starting_aua = Decimal('0')
            
            portfolio_inherited = False
            if mapped_columns.get('portfolio_inherited') and mapped_columns['portfolio_inherited'] in row.index:
                try:
                    val = row[mapped_columns['portfolio_inherited']]
                    if pd.notna(val):
                        val_str = str(val).strip().upper()
                        portfolio_inherited = val_str in ['TRUE', 'YES', '1', 'Y']
                except:
                    pass
            
            designation = None
            if mapped_columns.get('designation') and mapped_columns['designation'] in row.index:
                try:
                    desig_val = row[mapped_columns['designation']]
                    if pd.notna(desig_val):
                        designation = str(desig_val).strip() if desig_val else None
                except:
                    pass
            
            linkedin_profile_url = None
            if mapped_columns.get('linkedin_profile_url') and mapped_columns['linkedin_profile_url'] in row.index:
                try:
                    linkedin_val = row[mapped_columns['linkedin_profile_url']]
                    if pd.notna(linkedin_val):
                        linkedin_profile_url = str(linkedin_val).strip() if linkedin_val else None
                except:
                    pass
            
            type_of_engagement = None
            if mapped_columns.get('type_of_engagement') and mapped_columns['type_of_engagement'] in row.index:
                try:
                    engagement_val = row[mapped_columns['type_of_engagement']]
                    if pd.notna(engagement_val):
                        type_of_engagement = str(engagement_val).strip() if engagement_val else None
                except:
                    pass
            
            company_name = None
            if mapped_columns.get('company_name') and mapped_columns['company_name'] in row.index:
                try:
                    company_val = row[mapped_columns['company_name']]
                    if pd.notna(company_val):
                        company_name = str(company_val).strip() if company_val else None
                except:
                    pass
            
            industry = None
            if mapped_columns.get('industry') and mapped_columns['industry'] in row.index:
                try:
                    industry_val = row[mapped_columns['industry']]
                    if pd.notna(industry_val):
                        industry = str(industry_val).strip() if industry_val else None
                except:
                    pass
            
            # Create or update client
            try:
                if existing_client:
                    # Check if any changes need to be made
                    has_changes = False
                    
                    # Always update name (required field)
                    if existing_client.name != name:
                        existing_client.name = name
                        has_changes = True
                    
                    # Update other fields only if column was present in file AND value changed
                    if mapped_columns.get('phone') and mapped_columns['phone'] in row.index:
                        new_phone = phone or ''
                        existing_phone = existing_client.phone or ''
                        if new_phone != existing_phone:
                            # Truncate phone to 20 characters (DB limit)
                            if phone and len(phone) > 20:
                                phone = phone[:20]
                            existing_client.phone = phone
                            has_changes = True
                    
                    if mapped_columns.get('whatsapp_number') and mapped_columns['whatsapp_number'] in row.index:
                        new_whatsapp = whatsapp_number or ''
                        existing_whatsapp = existing_client.whatsapp_number or ''
                        if new_whatsapp != existing_whatsapp:
                            existing_client.whatsapp_number = whatsapp_number
                            has_changes = True
                    
                    if mapped_columns.get('address') and mapped_columns['address'] in row.index:
                        new_address = address or ''
                        existing_address = existing_client.address or ''
                        if new_address != existing_address:
                            existing_client.address = address
                            has_changes = True
                    
                    if mapped_columns.get('risk_profile') and mapped_columns['risk_profile'] in row.index:
                        new_risk = risk_profile or ''
                        existing_risk = existing_client.risk_profile or ''
                        if new_risk != existing_risk:
                            existing_client.risk_profile = risk_profile
                            has_changes = True
                    
                    if mapped_columns.get('date_of_birth') and mapped_columns['date_of_birth'] in row.index:
                        if date_of_birth != existing_client.date_of_birth:
                            existing_client.date_of_birth = date_of_birth
                            has_changes = True
                    
                    if mapped_columns.get('starting_aua') and mapped_columns['starting_aua'] in row.index:
                        new_aua = starting_aua if starting_aua is not None else Decimal('0')
                        existing_aua = existing_client.starting_aua or Decimal('0')
                        if new_aua != existing_aua:
                            existing_client.starting_aua = new_aua
                            has_changes = True
                    
                    if mapped_columns.get('designation') and mapped_columns['designation'] in row.index:
                        new_designation = designation or ''
                        existing_designation = existing_client.designation or ''
                        if new_designation != existing_designation:
                            existing_client.designation = designation
                            has_changes = True
                    
                    if mapped_columns.get('linkedin_profile_url') and mapped_columns['linkedin_profile_url'] in row.index:
                        new_linkedin = linkedin_profile_url or ''
                        existing_linkedin = existing_client.linkedin_profile_url or ''
                        if new_linkedin != existing_linkedin:
                            existing_client.linkedin_profile_url = linkedin_profile_url
                            has_changes = True
                    
                    if mapped_columns.get('date_of_joining') and mapped_columns['date_of_joining'] in row.index:
                        if date_of_joining != existing_client.date_of_joining:
                            existing_client.date_of_joining = date_of_joining
                            has_changes = True
                    
                    if mapped_columns.get('type_of_engagement') and mapped_columns['type_of_engagement'] in row.index:
                        new_engagement = type_of_engagement or ''
                        existing_engagement = existing_client.type_of_engagement or ''
                        if new_engagement != existing_engagement:
                            existing_client.type_of_engagement = type_of_engagement
                            has_changes = True
                    
                    if mapped_columns.get('portfolio_inherited') and mapped_columns['portfolio_inherited'] in row.index:
                        if portfolio_inherited != existing_client.portfolio_inherited:
                            existing_client.portfolio_inherited = portfolio_inherited
                            has_changes = True
                    
                    if mapped_columns.get('company_name') and mapped_columns['company_name'] in row.index:
                        new_company = company_name or ''
                        existing_company = existing_client.company_name or ''
                        if new_company != existing_company:
                            existing_client.company_name = company_name
                            has_changes = True
                    
                    if mapped_columns.get('industry') and mapped_columns['industry'] in row.index:
                        new_industry = industry or ''
                        existing_industry = existing_client.industry or ''
                        if new_industry != existing_industry:
                            existing_client.industry = industry
                            has_changes = True
                    
                    # Only count as saved if there were actual changes
                    if has_changes:
                        updated_count += 1
                        saved_count += 1
                    else:
                        skipped_count += 1
                        # If no changes, skip this row (don't increment saved_count)
                else:
                    # Create new client
                    # Truncate phone to 20 characters if needed
                    if phone and len(phone) > 20:
                        phone = phone[:20]
                    # Truncate address to 200 characters if needed (check model for actual limit)
                    if address and len(address) > 200:
                        address = address[:200]
                    
                    client = Client(
                        name=name,
                        email=email,
                        phone=phone,
                        whatsapp_number=whatsapp_number,
                        address=address,
                        risk_profile=risk_profile,
                        date_of_birth=date_of_birth,
                        starting_aua=starting_aua or Decimal('0'),
                        designation=designation,
                        linkedin_profile_url=linkedin_profile_url,
                        date_of_joining=date_of_joining,
                        type_of_engagement=type_of_engagement,
                        portfolio_inherited=portfolio_inherited,
                        company_name=company_name,
                        industry=industry,
                        user_id=current_user.id,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(client)
                    saved_count += 1  # New clients are always saved
            except Exception as e:
                # Rollback this specific row's changes but continue with other rows
                db.session.rollback()
                error_msg = str(e)
                # Extract more readable error message
                if 'Data too long' in error_msg:
                    field_match = None
                    if 'phone' in error_msg.lower():
                        field_match = 'phone'
                    elif 'address' in error_msg.lower():
                        field_match = 'address'
                    if field_match:
                        error_msg = f'{field_match.capitalize()} data too long (max 20 chars for phone, 200 for address). Row skipped.'
                
                errors.append({
                    'row': row_num,
                    'message': f'Error {"updating" if existing_client else "creating"} client: {error_msg}'
                })
                logger.warning(f"Row {row_num} error (skipped): {error_msg}")
                continue  # Skip this row and continue with next
        
        # Commit all successful changes at once
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error committing client upload: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Error committing changes: {str(e)}'
            }), 500
        
        created_count = saved_count - updated_count  # New clients created
        
        return jsonify({
            'success': True,
            'saved_count': saved_count,
            'created_count': created_count,  # New clients created
            'updated_count': updated_count,  # Existing clients updated (with changes)
            'skipped_count': skipped_count,  # Existing clients with no changes
            'error_count': len(errors),
            'errors': errors
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error uploading client file: {str(e)}")
        from flask import jsonify
        return jsonify({
            'success': False,
            'error': f'Error processing file: {str(e)}'
        }), 500


# ============================================================================
# ADVISORY AUA EXCLUSIONS (billing + practice dashboard; not portfolio/reviews)
# ============================================================================

@clients_bp.route('/<int:client_id>/advisory-exclusions', methods=['GET'])
@login_required
def list_advisory_exclusions(client_id):
    """List securities excluded from advisory AUA for a client."""
    Client.query.get_or_404(client_id)
    if not can_access_client(client_id):
        raise ForbiddenError('Access denied')

    from services.holding_advisory_scope_service import list_exclusions

    return APIResponse.success(
        data={
            'client_id': client_id,
            'exclusions': list_exclusions(client_id),
        }
    )


@clients_bp.route('/<int:client_id>/advisory-exclusions/<int:security_id>', methods=['PUT'])
@login_required
def set_advisory_exclusion(client_id, security_id):
    """
    Mark or clear advisory AUA exclusion for one holding.

    Body: { "excluded": true|false, "reason": "optional" }
    """
    client = Client.query.get_or_404(client_id)
    if not can_access_client(client_id):
        raise ForbiddenError('Access denied')

    payload = request.get_json(silent=True) or {}
    if 'excluded' not in payload:
        raise ValidationError('excluded is required (true or false)')

    excluded = bool(payload.get('excluded'))
    reason = payload.get('reason')

    from services.holding_advisory_scope_service import set_exclusion
    from services.audit_service import log_audit_event

    result = set_exclusion(
        client_id,
        security_id,
        excluded=excluded,
        reason=reason,
        user_id=current_user.id,
    )

    log_audit_event(
        'client.advisory_exclusion_updated',
        client_id=client_id,
        resource_type='security',
        resource_id=str(security_id),
        details={
            'excluded': excluded,
            'reason': reason,
            'client_name': client.name,
        },
        commit=False,
    )
    db.session.commit()

    return APIResponse.success(data=result)

