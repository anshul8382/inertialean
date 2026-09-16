"""
Forward Holding Calculation API

Provides endpoints for calculating holdings using forward calculation from transactions and corporate actions.
This is the most accurate method as it starts from the source of truth (transactions) and applies corporate actions chronologically.
"""
from flask import Blueprint, request, jsonify
from datetime import datetime, date
import logging

from services.forward_holding_calculation_service import (
    get_holding_quantity_by_date,
    audit_holdings_accuracy,
    get_client_portfolio_by_date
)

logger = logging.getLogger(__name__)

# Create blueprint
forward_calculation_bp = Blueprint('forward_calculation', __name__, url_prefix='/forward_calculation')


@forward_calculation_bp.route('/holding/<int:client_id>/<int:security_id>', methods=['GET'])
def get_holding_by_date(client_id, security_id):
    """
    Calculate holding quantity for a specific client/security/date using forward calculation
    
    Query Parameters:
        as_of_date: Date to calculate holdings for (YYYY-MM-DD format)
    
    Returns:
        JSON with holding details calculated forward from transactions
    """
    try:
        # Get as_of_date from query parameters
        as_of_date_str = request.args.get('as_of_date')
        if not as_of_date_str:
            return jsonify({'error': 'as_of_date parameter is required'}), 400
        
        try:
            as_of_date = datetime.strptime(as_of_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        logger.info(f"Forward calculation request: Client {client_id}, Security {security_id}, Date {as_of_date}")
        
        # Calculate holding using forward method
        result = get_holding_quantity_by_date(client_id, security_id, as_of_date)
        
        return jsonify({
            'success': True,
            'client_id': client_id,
            'security_id': security_id,
            'as_of_date': as_of_date_str,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error in forward holding calculation: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@forward_calculation_bp.route('/portfolio/<int:client_id>', methods=['GET'])
def get_portfolio_by_date(client_id):
    """
    Calculate entire client portfolio using forward calculation
    
    Query Parameters:
        as_of_date: Date to calculate portfolio for (YYYY-MM-DD format)
    
    Returns:
        JSON with complete portfolio calculated forward from transactions
    """
    try:
        # Get as_of_date from query parameters
        as_of_date_str = request.args.get('as_of_date')
        if not as_of_date_str:
            return jsonify({'error': 'as_of_date parameter is required'}), 400
        
        try:
            as_of_date = datetime.strptime(as_of_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        logger.info(f"Forward portfolio calculation request: Client {client_id}, Date {as_of_date}")
        
        # Calculate portfolio using forward method
        result = get_client_portfolio_by_date(client_id, as_of_date)
        
        return jsonify({
            'success': True,
            'client_id': client_id,
            'as_of_date': as_of_date_str,
            'result': result
        })
        
    except Exception as e:
        logger.error(f"Error in forward portfolio calculation: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@forward_calculation_bp.route('/audit/<int:client_id>', methods=['GET'])
def audit_client_holdings(client_id):
    """
    Audit client holdings against forward calculation
    
    Query Parameters:
        as_of_date: Date to audit holdings for (YYYY-MM-DD format)
    
    Returns:
        JSON with discrepancies found between holdings table and forward calculation
    """
    try:
        # Get as_of_date from query parameters
        as_of_date_str = request.args.get('as_of_date')
        if not as_of_date_str:
            return jsonify({'error': 'as_of_date parameter is required'}), 400
        
        try:
            as_of_date = datetime.strptime(as_of_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        logger.info(f"Holdings audit request: Client {client_id}, Date {as_of_date}")
        
        # Audit holdings against forward calculation
        discrepancies = audit_holdings_accuracy(client_id, as_of_date)
        
        return jsonify({
            'success': True,
            'client_id': client_id,
            'as_of_date': as_of_date_str,
            'total_discrepancies': len(discrepancies),
            'discrepancies': discrepancies
        })
        
    except Exception as e:
        logger.error(f"Error in holdings audit: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@forward_calculation_bp.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint for forward calculation service
    """
    return jsonify({
        'status': 'healthy',
        'service': 'forward_holding_calculation',
        'version': '1.0.0'
    })
