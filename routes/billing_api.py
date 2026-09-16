"""
Billing API Routes
Provides REST API endpoints for billing calculations and invoice management
"""

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging

from billing_calculation_service import BillingCalculationService
from invoice_generation_service import InvoiceGenerationService
from services.billing_manager_service import BillingManagerService
from models import Agreement, Client, Invoice, BillingConfiguration

logger = logging.getLogger(__name__)

# Create blueprint
billing_api = Blueprint('billing_api', __name__, url_prefix='/api/billing')


@billing_api.before_request
def _billing_api_enforce_client_access():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


@billing_api.route('/manager/plan', methods=['POST'])
@login_required
def manager_plan():
    """
    On-request billing manager plan.

    Request Body (all optional):
    {
        "as_of_date": "2026-04-10",
        "weekly_capacity": 5,
        "value_weight": 0.7,
        "overdue_weight": 0.3
    }
    """
    try:
        from access_control import get_accessible_clients

        data = request.get_json(silent=True) or {}

        as_of_date = None
        as_of_date_str = data.get('as_of_date')
        if as_of_date_str:
            try:
                as_of_date = datetime.strptime(as_of_date_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Invalid as_of_date format. Use YYYY-MM-DD'}), 400

        weekly_capacity = data.get('weekly_capacity', 5)
        value_weight = float(data.get('value_weight', 0.7))
        overdue_weight = float(data.get('overdue_weight', 0.3))
        allowed_ids = [c.id for c in get_accessible_clients()]

        plan = BillingManagerService.run_plan(
            as_of_date=as_of_date,
            weekly_capacity=weekly_capacity,
            value_weight=value_weight,
            overdue_weight=overdue_weight,
            accessible_client_ids=allowed_ids,
        )
        return jsonify({'success': True, 'data': plan})
    except Exception as e:
        logger.error(f"Error in manager_plan API: {str(e)}", exc_info=True)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/calculate/<int:agreement_id>', methods=['GET'])
@login_required
def calculate_billing(agreement_id):
    """
    Calculate billing amount for an agreement
    
    Query Parameters:
    - billing_date (optional): Date for billing calculation (YYYY-MM-DD)
    
    Returns:
    - JSON with billing calculation details
    """
    try:
        # Get billing date from query parameters
        billing_date_str = request.args.get('billing_date')
        billing_date = None
        
        if billing_date_str:
            try:
                billing_date = datetime.strptime(billing_date_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Invalid billing_date format. Use YYYY-MM-DD'}), 400
        
        # Calculate billing amount
        result = BillingCalculationService.calculate_billing_amount(agreement_id, billing_date)
        
        if 'error' in result:
            return jsonify(result), 400
            
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        logger.error(f"Error in calculate_billing API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/calculate', methods=['POST'])
@login_required
def calculate_billing_bulk():
    """
    Calculate billing amounts for multiple agreements
    
    Request Body:
    {
        "agreement_ids": [1, 2, 3],
        "billing_date": "2025-01-01" (optional)
    }
    
    Returns:
    - JSON with billing calculations for all agreements
    """
    try:
        data = request.get_json()
        
        if not data or 'agreement_ids' not in data:
            return jsonify({'error': 'agreement_ids is required'}), 400
            
        agreement_ids = data['agreement_ids']
        if not isinstance(agreement_ids, list):
            return jsonify({'error': 'agreement_ids must be a list'}), 400
            
        # Get billing date
        billing_date_str = data.get('billing_date')
        billing_date = None
        
        if billing_date_str:
            try:
                billing_date = datetime.strptime(billing_date_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Invalid billing_date format. Use YYYY-MM-DD'}), 400
        
        # Calculate billing for each agreement
        results = []
        for agreement_id in agreement_ids:
            result = BillingCalculationService.calculate_billing_amount(agreement_id, billing_date)
            results.append({
                'agreement_id': agreement_id,
                'result': result
            })
            
        return jsonify({
            'success': True,
            'data': results
        })
        
    except Exception as e:
        logger.error(f"Error in calculate_billing_bulk API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/calculate/<int:agreement_id>/preview', methods=['GET'])
@login_required
def preview_billing(agreement_id):
    """
    Preview billing calculation without creating invoice
    
    Query Parameters:
    - billing_date (optional): Date for billing calculation (YYYY-MM-DD)
    
    Returns:
    - JSON with billing preview details
    """
    try:
        # Get billing date from query parameters
        billing_date_str = request.args.get('billing_date')
        billing_date = None
        
        if billing_date_str:
            try:
                billing_date = datetime.strptime(billing_date_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Invalid billing_date format. Use YYYY-MM-DD'}), 400
        
        # Calculate billing amount
        result = BillingCalculationService.calculate_billing_amount(agreement_id, billing_date)
        
        if 'error' in result:
            return jsonify(result), 400
            
        # Add preview-specific information
        result['preview'] = True
        result['invoice_number'] = f"PREVIEW-{agreement_id}-{billing_date or date.today()}"
        result['due_date'] = (billing_date or date.today()).isoformat()
        
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        logger.error(f"Error in preview_billing API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/invoice/generate', methods=['POST'])
@login_required
def generate_invoice():
    """
    Generate invoice for an agreement
    
    Request Body:
    {
        "agreement_id": 1,
        "billing_date": "2025-01-01" (optional)
    }
    
    Returns:
    - JSON with generated invoice details
    """
    try:
        data = request.get_json()
        
        if not data or 'agreement_id' not in data:
            return jsonify({'error': 'agreement_id is required'}), 400
            
        agreement_id = data['agreement_id']
        
        # Get billing date
        billing_date_str = data.get('billing_date')
        billing_date = None
        
        if billing_date_str:
            try:
                billing_date = datetime.strptime(billing_date_str, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Invalid billing_date format. Use YYYY-MM-DD'}), 400
        
        # Generate invoice
        result = InvoiceGenerationService.generate_invoice(
            agreement_id, billing_date, current_user.id
        )
        
        if 'error' in result:
            return jsonify(result), 400
            
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        logger.error(f"Error in generate_invoice API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/invoice/<int:invoice_id>', methods=['GET'])
@login_required
def get_invoice(invoice_id):
    """
    Get invoice details by ID
    
    Returns:
    - JSON with invoice details
    """
    try:
        result = InvoiceGenerationService.get_invoice(invoice_id)
        
        if 'error' in result:
            return jsonify(result), 404
            
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        logger.error(f"Error in get_invoice API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/invoice/client/<int:client_id>', methods=['GET'])
@login_required
def get_client_invoices(client_id):
    """
    Get all invoices for a client
    
    Query Parameters:
    - status (optional): Filter by invoice status
    - limit (optional): Limit number of results (default: 50)
    - offset (optional): Offset for pagination (default: 0)
    
    Returns:
    - JSON with list of invoices
    """
    try:
        # Get query parameters
        status = request.args.get('status')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        # Build query
        query = Invoice.query.filter_by(client_id=client_id)
        
        if status:
            query = query.filter_by(status=status)
            
        # Apply pagination
        invoices = query.order_by(Invoice.invoice_date.desc()).offset(offset).limit(limit).all()
        
        # Convert to JSON
        invoice_list = []
        for invoice in invoices:
            invoice_list.append({
                'invoice_id': invoice.id,
                'invoice_number': invoice.invoice_number,
                'invoice_date': invoice.invoice_date.isoformat(),
                'due_date': invoice.due_date.isoformat(),
                'total_amount': float(invoice.total_amount),
                'status': invoice.status,
                'paid_date': invoice.paid_date.isoformat() if invoice.paid_date else None
            })
            
        return jsonify({
            'success': True,
            'data': {
                'invoices': invoice_list,
                'total_count': query.count(),
                'limit': limit,
                'offset': offset
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_client_invoices API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/invoice/<int:invoice_id>/status', methods=['PUT'])
@login_required
def update_invoice_status(invoice_id):
    """
    Update invoice status
    
    Request Body:
    {
        "status": "paid",
        "payment_reference": "TXN123456" (optional),
        "payment_method": "bank_transfer" (optional)
    }
    
    Returns:
    - JSON with success status
    """
    try:
        data = request.get_json()
        
        if not data or 'status' not in data:
            return jsonify({'error': 'status is required'}), 400
            
        status = data['status']
        valid_statuses = ['draft', 'sent', 'paid', 'overdue', 'cancelled']
        
        if status not in valid_statuses:
            return jsonify({'error': f'Invalid status. Must be one of: {valid_statuses}'}), 400
            
        # Update invoice status
        success = InvoiceGenerationService.update_invoice_status(
            invoice_id, 
            status,
            data.get('payment_reference'),
            data.get('payment_method')
        )
        
        if not success:
            return jsonify({'error': 'Failed to update invoice status'}), 400
            
        return jsonify({
            'success': True,
            'message': f'Invoice status updated to {status}'
        })
        
    except Exception as e:
        logger.error(f"Error in update_invoice_status API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/schedule/<int:agreement_id>', methods=['GET'])
@login_required
def get_billing_schedule(agreement_id):
    """
    Get billing schedule for an agreement
    
    Returns:
    - JSON with billing schedule details
    """
    try:
        result = BillingCalculationService.get_billing_schedule(agreement_id)
        
        if 'error' in result:
            return jsonify(result), 404
            
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        logger.error(f"Error in get_billing_schedule API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/config', methods=['GET'])
@login_required
def get_billing_config():
    """
    Get billing configuration settings
    
    Returns:
    - JSON with billing configuration
    """
    try:
        configs = BillingConfiguration.query.filter_by(is_active=True).all()
        
        config_dict = {}
        for config in configs:
            # Convert value based on type
            if config.config_type == 'number':
                try:
                    config_dict[config.config_key] = float(config.config_value)
                except ValueError:
                    config_dict[config.config_key] = config.config_value
            elif config.config_type == 'boolean':
                config_dict[config.config_key] = config.config_value.lower() == 'true'
            else:
                config_dict[config.config_key] = config.config_value
                
        return jsonify({
            'success': True,
            'data': config_dict
        })
        
    except Exception as e:
        logger.error(f"Error in get_billing_config API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/agreements/active', methods=['GET'])
@login_required
def get_active_agreements():
    """
    Get all active agreements with billing information
    
    Query Parameters:
    - limit (optional): Limit number of results (default: 50)
    - offset (optional): Offset for pagination (default: 0)
    
    Returns:
    - JSON with list of active agreements
    """
    try:
        # Get query parameters
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        # Get active agreements
        agreements = Agreement.query.filter_by(status='signed').offset(offset).limit(limit).all()
        
        # Convert to JSON
        agreement_list = []
        for agreement in agreements:
            client = agreement.lead.client if agreement.lead and agreement.lead.client else None
            
            agreement_list.append({
                'agreement_id': agreement.id,
                'client_id': client.id if client else None,
                'client_name': client.name if client else 'Unknown',
                'effective_date': agreement.effective_date.isoformat() if hasattr(agreement, 'effective_date') and agreement.effective_date else None,
                'billing_frequency': getattr(agreement, 'billing_frequency', None),
                'billing_status': getattr(agreement, 'billing_status', None),
                'signed_date': agreement.signed_date.isoformat() if agreement.signed_date else None
            })
            
        return jsonify({
            'success': True,
            'data': {
                'agreements': agreement_list,
                'total_count': Agreement.query.filter_by(status='signed').count(),
                'limit': limit,
                'offset': offset
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_active_agreements API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@billing_api.route('/history/<int:client_id>', methods=['GET'])
@login_required
def get_billing_history(client_id):
    """
    Get billing history for a client
    
    Returns:
    - JSON with billing history including invoices and billing calculations
    """
    try:
        # Get client
        client = Client.query.get_or_404(client_id)
        
        # Get client's lead and agreement
        from models import Lead, Agreement
        lead = Lead.query.filter_by(client_id=client_id).first()
        
        if not lead:
            return jsonify({'error': 'No lead found for this client'}), 404
            
        # Get all agreements for this lead
        agreements = Agreement.query.filter_by(lead_id=lead.id).all()
        
        if not agreements:
            return jsonify({'error': 'No agreements found for this client'}), 404
        
        # Get all invoices for these agreements
        agreement_ids = [agreement.id for agreement in agreements]
        invoices = Invoice.query.filter(Invoice.agreement_id.in_(agreement_ids)).order_by(Invoice.invoice_date.desc()).all()
        
        # Format response
        billing_history = []
        
        # Add invoices
        for invoice in invoices:
            billing_history.append({
                'type': 'invoice',
                'date': invoice.invoice_date.isoformat(),
                'amount': float(invoice.total_amount),
                'status': invoice.status,
                'invoice_number': invoice.invoice_number,
                'due_date': invoice.due_date.isoformat(),
                'agreement_id': invoice.agreement_id
            })
        
        # Add billing calculations for agreements without recent invoices
        for agreement in agreements:
            if agreement.status in ['signed', 'completed', 'active']:
                # Check if there's a recent invoice (within last 6 months)
                recent_invoice = Invoice.query.filter(
                    Invoice.agreement_id == agreement.id,
                    Invoice.invoice_date >= datetime.now().date() - timedelta(days=180)
                ).first()
                
                if not recent_invoice:
                    # Calculate potential billing
                    billing_calc = BillingCalculationService.calculate_billing_amount(agreement.id)
                    if 'error' not in billing_calc and billing_calc.get('total_amount', 0) > 0:
                        billing_history.append({
                            'type': 'calculated',
                            'date': datetime.now().date().isoformat(),
                            'amount': float(billing_calc.get('total_amount', 0)),
                            'status': 'pending',
                            'invoice_number': None,
                            'due_date': None,
                            'agreement_id': agreement.id
                        })
        
        # Sort by date (newest first)
        billing_history.sort(key=lambda x: x['date'], reverse=True)
        
        return jsonify({
            'success': True,
            'data': {
                'client_id': client_id,
                'client_name': client.name,
                'billing_history': billing_history,
                'total_invoices': len([item for item in billing_history if item['type'] == 'invoice']),
                'total_amount': sum(item['amount'] for item in billing_history)
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_billing_history API: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500










