"""
Corporate Actions Management API

This API provides endpoints for managing corporate actions like splits, bonuses, dividends.
It centralizes corporate action data and provides efficient lookup for adjustment calculations.
"""
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import and_, desc, asc
from flask import Blueprint, request, jsonify
from models import (
    db, Security, CorporateAction
)
from api.core.response import APIResponse
from api.core.exceptions import ValidationError, NotFoundError
import logging

logger = logging.getLogger(__name__)

corporate_actions_bp = Blueprint('corporate_actions', __name__)

# ============================================================================
# CORPORATE ACTIONS API ENDPOINTS
# ============================================================================

@corporate_actions_bp.route('/', methods=['GET'])
def get_corporate_actions():
    """
    Get all corporate actions with optional filtering
    
    Query Parameters:
        - security_id: Filter by security ID
        - action_type: Filter by action type (SPLIT, BONUS, DIVIDEND)
        - start_date: Filter actions from this date
        - end_date: Filter actions to this date
        - is_active: Filter by active status (true/false)
        - page: Page number for pagination
        - per_page: Items per page
    """
    try:
        # Get query parameters
        security_id = request.args.get('security_id', type=int)
        action_type = request.args.get('action_type')
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        is_active = request.args.get('is_active', 'true').lower() == 'true'
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        
        # Build query
        query = CorporateAction.query
        
        # Apply filters
        if security_id:
            query = query.filter(CorporateAction.security_id == security_id)
        
        if action_type:
            query = query.filter(CorporateAction.action_type == action_type)
        
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                query = query.filter(CorporateAction.action_date >= start_date)
            except ValueError:
                raise ValidationError("Invalid start_date format. Use YYYY-MM-DD")
        
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                query = query.filter(CorporateAction.action_date <= end_date)
            except ValueError:
                raise ValidationError("Invalid end_date format. Use YYYY-MM-DD")
        
        if is_active is not None:
            query = query.filter(CorporateAction.is_active == is_active)
        
        # Order by date (newest first)
        query = query.order_by(desc(CorporateAction.action_date))
        
        # Apply pagination
        pagination = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Format response
        actions = []
        for action in pagination.items:
            security = Security.query.get(action.security_id)
            actions.append({
                'id': action.id,
                'security_id': action.security_id,
                'symbol': security.symbol if security else 'Unknown',
                'action_type': action.action_type,
                'action_date': action.action_date.isoformat(),
                'ratio': float(action.ratio),
                'description': action.description,
                'source': action.source,
                'is_active': action.is_active,
                'created_at': action.created_at.isoformat(),
                'updated_at': action.updated_at.isoformat()
            })
        
        return APIResponse.success(
            data={
                'actions': actions,
                'pagination': {
                    'page': pagination.page,
                    'per_page': pagination.per_page,
                    'total': pagination.total,
                    'pages': pagination.pages,
                    'has_next': pagination.has_next,
                    'has_prev': pagination.has_prev
                }
            },
            message=f"Retrieved {len(actions)} corporate actions"
        )
        
    except Exception as e:
        logger.error(f"Error getting corporate actions: {str(e)}")
        raise

@corporate_actions_bp.route('/<int:action_id>', methods=['GET'])
def get_corporate_action(action_id):
    """
    Get a specific corporate action by ID
    """
    try:
        action = CorporateAction.query.get_or_404(action_id)
        security = Security.query.get(action.security_id)
        
        data = {
            'id': action.id,
            'security_id': action.security_id,
            'symbol': security.symbol if security else 'Unknown',
            'action_type': action.action_type,
            'action_date': action.action_date.isoformat(),
            'ratio': float(action.ratio),
            'description': action.description,
            'source': action.source,
            'is_active': action.is_active,
            'created_at': action.created_at.isoformat(),
            'updated_at': action.updated_at.isoformat()
        }
        
        return APIResponse.success(
            data=data,
            message=f"Retrieved corporate action {action_id}"
        )
        
    except Exception as e:
        logger.error(f"Error getting corporate action {action_id}: {str(e)}")
        raise

@corporate_actions_bp.route('/', methods=['POST'])
def create_corporate_action():
    """
    Create a new corporate action
    
    Request Body:
        - security_id: Security ID
        - action_type: SPLIT, BONUS, DIVIDEND, etc.
        - action_date: Date of the corporate action
        - ratio: Split ratio, bonus ratio, etc.
        - description: Optional description
        - source: Source of the data (default: MANUAL)
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['security_id', 'action_type', 'action_date', 'ratio']
        for field in required_fields:
            if field not in data:
                raise ValidationError(f"Missing required field: {field}")
        
        # Validate security exists
        security = Security.query.get(data['security_id'])
        if not security:
            raise NotFoundError(f"Security with ID {data['security_id']} not found")
        
        # Parse action date
        try:
            action_date = datetime.strptime(data['action_date'], '%Y-%m-%d').date()
        except ValueError:
            raise ValidationError("Invalid action_date format. Use YYYY-MM-DD")
        
        # Validate action type
        valid_types = ['SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS', 'MERGER']
        if data['action_type'] not in valid_types:
            raise ValidationError(f"Invalid action_type. Must be one of: {', '.join(valid_types)}")
        
        # Validate ratio
        try:
            ratio = Decimal(str(data['ratio']))
            if ratio <= 0:
                raise ValidationError("Ratio must be positive")
        except (ValueError, TypeError):
            raise ValidationError("Invalid ratio format")
        
        # Check for duplicate
        existing = CorporateAction.query.filter(
            CorporateAction.security_id == data['security_id'],
            CorporateAction.action_date == action_date,
            CorporateAction.action_type == data['action_type']
        ).first()
        
        if existing:
            raise ValidationError(f"Corporate action already exists for {security.symbol} on {action_date}")
        
        # Create corporate action
        action = CorporateAction(
            security_id=data['security_id'],
            action_type=data['action_type'],
            action_date=action_date,
            ratio=ratio,
            description=data.get('description', ''),
            source=data.get('source', 'MANUAL'),
            is_active=data.get('is_active', True)
        )
        
        db.session.add(action)
        db.session.commit()
        
        logger.info(f"Created corporate action: {security.symbol} - {action.action_type} {action.ratio} on {action.action_date}")
        
        # Automatically update holdings for all clients holding this security
        try:
            from services.data.corporate_action_service import CorporateActionService
            update_result = CorporateActionService.update_holdings_for_security_after_corporate_action(data['security_id'])
            logger.info(f"Holdings updated for {update_result.get('clients_updated', 0)} clients after creating corporate action")
        except Exception as e:
            # Log error but don't fail the corporate action creation
            logger.error(f"Failed to update holdings after corporate action creation: {str(e)}")
        
        return APIResponse.success(
            data={
                'id': action.id,
                'security_id': action.security_id,
                'symbol': security.symbol,
                'action_type': action.action_type,
                'action_date': action.action_date.isoformat(),
                'ratio': float(action.ratio),
                'description': action.description,
                'source': action.source,
                'is_active': action.is_active
            },
            message=f"Created corporate action for {security.symbol}"
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating corporate action: {str(e)}")
        raise

@corporate_actions_bp.route('/<int:action_id>', methods=['PUT'])
def update_corporate_action(action_id):
    """
    Update an existing corporate action
    """
    try:
        action = CorporateAction.query.get_or_404(action_id)
        data = request.get_json()
        
        # Update fields
        if 'action_type' in data:
            valid_types = ['SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS', 'MERGER']
            if data['action_type'] not in valid_types:
                raise ValidationError(f"Invalid action_type. Must be one of: {', '.join(valid_types)}")
            action.action_type = data['action_type']
        
        if 'action_date' in data:
            try:
                action.action_date = datetime.strptime(data['action_date'], '%Y-%m-%d').date()
            except ValueError:
                raise ValidationError("Invalid action_date format. Use YYYY-MM-DD")
        
        if 'ratio' in data:
            try:
                ratio = Decimal(str(data['ratio']))
                if ratio <= 0:
                    raise ValidationError("Ratio must be positive")
                action.ratio = ratio
            except (ValueError, TypeError):
                raise ValidationError("Invalid ratio format")
        
        if 'description' in data:
            action.description = data['description']
        
        if 'source' in data:
            action.source = data['source']
        
        if 'is_active' in data:
            action.is_active = bool(data['is_active'])
        
        action.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        security = Security.query.get(action.security_id)
        logger.info(f"Updated corporate action: {security.symbol if security else 'Unknown'} - {action.action_type} {action.ratio}")
        
        return APIResponse.success(
            data={
                'id': action.id,
                'security_id': action.security_id,
                'symbol': security.symbol if security else 'Unknown',
                'action_type': action.action_type,
                'action_date': action.action_date.isoformat(),
                'ratio': float(action.ratio),
                'description': action.description,
                'source': action.source,
                'is_active': action.is_active
            },
            message=f"Updated corporate action {action_id}"
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating corporate action {action_id}: {str(e)}")
        raise

@corporate_actions_bp.route('/<int:action_id>', methods=['DELETE'])
def delete_corporate_action(action_id):
    """
    Delete a corporate action (soft delete by setting is_active=False)
    """
    try:
        action = CorporateAction.query.get_or_404(action_id)
        
        # Soft delete
        action.is_active = False
        action.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        security = Security.query.get(action.security_id)
        logger.info(f"Deleted corporate action: {security.symbol if security else 'Unknown'} - {action.action_type} {action.ratio}")
        
        return APIResponse.success(
            message=f"Deleted corporate action {action_id}"
        )
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting corporate action {action_id}: {str(e)}")
        raise

@corporate_actions_bp.route('/securities/<int:security_id>', methods=['GET'])
def get_security_corporate_actions(security_id):
    """
    Get all corporate actions for a specific security
    """
    try:
        security = Security.query.get_or_404(security_id)
        
        actions = CorporateAction.query.filter(
            CorporateAction.security_id == security_id,
            CorporateAction.is_active == True
        ).order_by(desc(CorporateAction.action_date)).all()
        
        action_list = []
        for action in actions:
            action_list.append({
                'id': action.id,
                'action_type': action.action_type,
                'action_date': action.action_date.isoformat(),
                'ratio': float(action.ratio),
                'description': action.description,
                'source': action.source,
                'created_at': action.created_at.isoformat()
            })
        
        return APIResponse.success(
            data={
                'security_id': security_id,
                'symbol': security.symbol,
                'actions': action_list
            },
            message=f"Retrieved {len(action_list)} corporate actions for {security.symbol}"
        )
        
    except Exception as e:
        logger.error(f"Error getting corporate actions for security {security_id}: {str(e)}")
        raise

@corporate_actions_bp.route('/check/<int:security_id>', methods=['GET'])
def check_security_corporate_actions(security_id):
    """
    Check if a security has corporate actions (for optimization)
    
    Query Parameters:
        - cutoff_date: Check actions up to this date (default: today)
    """
    try:
        security = Security.query.get_or_404(security_id)
        
        cutoff_date_str = request.args.get('cutoff_date')
        if cutoff_date_str:
            try:
                cutoff_date = datetime.strptime(cutoff_date_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValidationError("Invalid cutoff_date format. Use YYYY-MM-DD")
        else:
            cutoff_date = date.today()
        
        # Check for corporate actions
        has_actions = CorporateAction.query.filter(
            CorporateAction.security_id == security_id,
            CorporateAction.action_date <= cutoff_date,
            CorporateAction.is_active == True
        ).first() is not None
        
        # Get total factor if actions exist
        total_factor = 1.0
        if has_actions:
            actions = CorporateAction.query.filter(
                CorporateAction.security_id == security_id,
                CorporateAction.action_date <= cutoff_date,
                CorporateAction.is_active == True
            ).order_by(CorporateAction.action_date).all()
            
            for action in actions:
                if action.action_type == 'SPLIT':
                    total_factor *= float(action.ratio)
                elif action.action_type == 'BONUS':
                    total_factor *= (1.0 + float(action.ratio))
        
        return APIResponse.success(
            data={
                'security_id': security_id,
                'symbol': security.symbol,
                'has_corporate_actions': has_actions,
                'total_factor': total_factor,
                'cutoff_date': cutoff_date.isoformat()
            },
            message=f"Corporate action check for {security.symbol}"
        )
        
    except Exception as e:
        logger.error(f"Error checking corporate actions for security {security_id}: {str(e)}")
        raise
