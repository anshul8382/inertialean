"""
Holdings API - Portfolio Holdings Management
"""
import logging
from datetime import datetime
from flask import Blueprint, request
from sqlalchemy import func, desc

from api.core.response import APIResponse
# from api.core.decorators import api_auth_required, api_rate_limit
from api.core.exceptions import ValidationError, NotFoundError
from models import db, Client, Security, Holding, Transaction
from routes.main import update_holdings_from_transactions
from api.v1.adjustments import calculate_adjusted_holdings

logger = logging.getLogger(__name__)

# Create blueprint
holdings_bp = Blueprint('holdings', __name__)

# ============================================================================
# HOLDINGS API ENDPOINTS
# ============================================================================

@holdings_bp.route('/<int:client_id>/holdings', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_client_holdings(client_id):
    """
    Get all holdings for a specific client
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        include_zero = request.args.get('include_zero', 'false').lower() == 'true'
        sort_by = request.args.get('sort_by', 'current_value')
        sort_order = request.args.get('sort_order', 'desc')
        basis = request.args.get('basis', 'unadjusted').lower()
        cutoff_date_str = request.args.get('cutoff_date')
        
        # Parse cutoff date if provided
        cutoff_date = None
        if cutoff_date_str:
            try:
                cutoff_date = datetime.strptime(cutoff_date_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValidationError("Invalid cutoff_date format. Use YYYY-MM-DD")
        
        # Use adjusted holdings if basis is adjusted
        if basis == 'adjusted':
            try:
                cutoff_date = cutoff_date or datetime.now().date()
                adjusted_holdings = calculate_adjusted_holdings(client_id, cutoff_date)
                
                # Convert to holdings format for consistency
                holdings_data = []
                for holding in adjusted_holdings:
                    holdings_data.append({
                        'id': holding['security_id'],  # Use security_id as ID for adjusted holdings
                        'client_id': client_id,
                        'security_id': holding['security_id'],
                        'symbol': holding['symbol'],
                        'quantity': holding['quantity_at_cutoff'],
                        'average_price': holding['adjusted_avg_cost_per_share'],
                        'current_price': holding['price_at_cutoff'],
                        'current_value': holding['quantity_at_cutoff'] * holding['price_at_cutoff'],
                        'unrealized_pnl': holding['unrealized_pnl'],
                        'basis': 'adjusted',
                        'cutoff_date': cutoff_date.isoformat()
                    })
                
                # Filter out zero holdings if requested
                if not include_zero:
                    holdings_data = [h for h in holdings_data if h['quantity'] > 0]
                
                # Apply sorting
                reverse = sort_order == 'desc'
                if sort_by == 'current_value':
                    holdings_data.sort(key=lambda x: x['current_value'], reverse=reverse)
                elif sort_by == 'unrealized_pnl':
                    holdings_data.sort(key=lambda x: x['unrealized_pnl'], reverse=reverse)
                elif sort_by == 'quantity':
                    holdings_data.sort(key=lambda x: x['quantity'], reverse=reverse)
                
                # Apply pagination
                total = len(holdings_data)
                start = (page - 1) * per_page
                end = start + per_page
                paginated_holdings = holdings_data[start:end]
                
                return APIResponse.success(
                    data={
                        'holdings': paginated_holdings,
                        'pagination': {
                            'page': page,
                            'per_page': per_page,
                            'total': total,
                            'pages': (total + per_page - 1) // per_page
                        },
                        'basis': 'adjusted',
                        'cutoff_date': cutoff_date.isoformat()
                    },
                    message=f"Adjusted holdings for client {client_id}"
                )
                
            except Exception as e:
                logger.warning(f"Failed to get adjusted holdings, falling back to regular holdings: {str(e)}")
                # Fall back to regular holdings
        
        # Regular holdings query
        query = Holding.query.filter_by(client_id=client_id)
        
        # Filter out zero holdings if requested
        if not include_zero:
            query = query.filter(Holding.quantity > 0)
        
        # Apply sorting - current_value is calculated dynamically, so we'll sort after fetching
        # For now, we'll sort by quantity as a fallback for current_value sorting
        if sort_by == 'current_value':
            # We'll sort by quantity as a proxy, then sort properly after calculating current_value
            if sort_order == 'desc':
                query = query.order_by(desc(Holding.quantity))
            else:
                query = query.order_by(Holding.quantity)
        elif sort_by == 'quantity':
            if sort_order == 'desc':
                query = query.order_by(desc(Holding.quantity))
            else:
                query = query.order_by(Holding.quantity)
        elif sort_by == 'security_name':
            query = query.join(Security).order_by(
                desc(Security.name) if sort_order == 'desc' else Security.name
            )
        else:
            query = query.order_by(desc(Holding.current_value))
        
        # Get paginated results
        holdings = query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
        
        # Prepare response data
        holdings_data = []
        total_value = 0
        
        for holding in holdings.items:
            security = Security.query.get(holding.security_id)
            if security:
                # Calculate current_value from quantity * current_price
                quantity = float(holding.quantity) if holding.quantity else 0
                current_price = float(security.current_price) if security.current_price else 0
                current_value = quantity * current_price
                
                # Calculate total cost from quantity * average_price
                average_price = float(holding.average_price) if holding.average_price else 0
                total_cost = quantity * average_price
                
                # Calculate unrealized P&L
                unrealized_pnl = current_value - total_cost
                unrealized_pnl_percent = (unrealized_pnl / total_cost * 100) if total_cost > 0 else 0
                
                holding_data = {
                    'id': holding.id,
                    'client_id': holding.client_id,
                    'security_id': holding.security_id,
                    'security_name': security.name,
                    'security_symbol': security.symbol,
                    'quantity': quantity,
                    'average_price': average_price,
                    'current_price': current_price,
                    'total_cost': total_cost,
                    'current_value': current_value,
                    'unrealized_pnl': unrealized_pnl,
                    'unrealized_pnl_percent': unrealized_pnl_percent,
                    'last_updated': holding.updated_at.isoformat() if holding.updated_at else None
                }
                holdings_data.append(holding_data)
                total_value += current_value
        
        # Sort by current_value if requested (since it's calculated dynamically)
        if sort_by == 'current_value':
            holdings_data.sort(key=lambda x: x['current_value'], reverse=(sort_order == 'desc'))
        
        # Calculate summary
        summary = {
            'total_holdings': holdings.total,
            'total_value': total_value,
            'total_cost': sum(h['total_cost'] for h in holdings_data),
            'total_unrealized_pnl': sum(h['unrealized_pnl'] for h in holdings_data),
            'page': page,
            'per_page': per_page,
            'pages': holdings.pages
        }
        
        response_data = {
            'holdings': holdings_data,
            'summary': summary
        }
        
        return APIResponse.success(
            data=response_data,
            message=f"Retrieved {len(holdings_data)} holdings for client {client_id}"
        )
        
    except Exception as e:
        logger.error(f"Error retrieving holdings for client {client_id}: {str(e)}")
        raise

@holdings_bp.route('/<int:client_id>/holdings/<int:holding_id>', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_holding(client_id, holding_id):
    """
    Get a specific holding for a client
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get holding
        holding = Holding.query.filter_by(
            id=holding_id, 
            client_id=client_id
        ).first_or_404()
        
        # Get security details
        security = Security.query.get(holding.security_id)
        if not security:
            raise NotFoundError(f"Security {holding.security_id} not found")
        
        # Prepare response data
        holding_data = {
            'id': holding.id,
            'client_id': holding.client_id,
            'security_id': holding.security_id,
            'security_name': security.name,
            'security_symbol': security.symbol,
            'quantity': float(holding.quantity),
            'average_price': float(holding.average_price) if holding.average_price else 0,
            'current_price': float(holding.current_price) if holding.current_price else 0,
            'total_cost': float(holding.total_cost) if holding.total_cost else 0,
            'current_value': float(holding.current_value) if holding.current_value else 0,
            'unrealized_pnl': float(holding.unrealized_pnl) if holding.unrealized_pnl else 0,
            'unrealized_pnl_percent': float(holding.unrealized_pnl_percent) if holding.unrealized_pnl_percent else 0,
            'last_updated': holding.last_updated.isoformat() if holding.last_updated else None
        }
        
        return APIResponse.success(
            data=holding_data,
            message=f"Retrieved holding {holding_id} for client {client_id}"
        )
        
    except Exception as e:
        logger.error(f"Error retrieving holding {holding_id} for client {client_id}: {str(e)}")
        raise

@holdings_bp.route('/<int:client_id>/holdings/refresh', methods=['POST'])
# @api_auth_required
# @api_rate_limit
def refresh_holdings(client_id):
    """
    Refresh/update holdings for a client by recalculating from transactions
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Update holdings using existing function
        result = update_holdings_from_transactions_api(client_id)
        
        if not result['success']:
            raise Exception(f"Holdings refresh failed: {result['message']}")
        
        return APIResponse.success(
            data=result['data'],
            message=f"Holdings refreshed successfully for client {client_id}"
        )
        
    except Exception as e:
        logger.error(f"Error refreshing holdings for client {client_id}: {str(e)}")
        raise

@holdings_bp.route('/<int:client_id>/holdings/debug', methods=['GET'])
def debug_holdings(client_id):
    """Debug endpoint to check holdings data"""
    try:
        holdings = Holding.query.filter_by(client_id=client_id).all()
        ebb_holdings = [h for h in holdings if h.security_id in [2128, 2129]]
        
        debug_data = []
        for holding in ebb_holdings:
            security = Security.query.get(holding.security_id)
            current_price = float(security.current_price) if security and security.current_price else 0.0
            current_value = float(holding.quantity) * current_price
            
            debug_data.append({
                'holding_id': holding.id,
                'security_id': holding.security_id,
                'security_name': security.name if security else 'None',
                'security_symbol': security.symbol if security else 'None',
                'quantity': float(holding.quantity),
                'average_price': float(holding.average_price),
                'current_price': current_price,
                'current_value': current_value
            })
        
        return APIResponse.success(
            data={'debug_data': debug_data, 'total_holdings': len(holdings)},
            message=f"Debug data for client {client_id}"
        )
    except Exception as e:
        logger.error(f"Error in debug endpoint: {str(e)}")
        return APIResponse.error(f"Debug failed: {str(e)}")

@holdings_bp.route('/<int:client_id>/holdings/summary', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_holdings_summary(client_id):
    """
    Get holdings summary/analytics for a client
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get all holdings for the client
        holdings = Holding.query.filter_by(client_id=client_id).all()
        logger.info(f"Retrieved {len(holdings)} holdings for client {client_id}")
        
        # Debug: Check for EBBETF holdings
        ebb_holdings = [h for h in holdings if h.security_id in [2128, 2129]]
        logger.info(f"EBBETF holdings found: {len(ebb_holdings)}")
        for h in ebb_holdings:
            logger.info(f"  EBBETF holding: ID={h.id}, Security ID={h.security_id}, Quantity={h.quantity}")
        
        if not holdings:
            return APIResponse.success(
                data={
                    'total_holdings': 0,
                    'total_value': 0,
                    'total_cost': 0,
                    'total_unrealized_pnl': 0,
                    'total_unrealized_pnl_percent': 0,
                    'top_holdings': [],
                    'wealth_creators': [],
                    'sector_breakdown': {}
                },
                message=f"No holdings found for client {client_id}"
            )
        
        # Import PriceService for dynamic calculations
        from services.price_service import PriceService
        
        # Calculate totals dynamically
        total_value = 0.0
        total_cost = 0.0
        total_unrealized_pnl = 0.0
        
        holdings_with_calculations = []
        
        for holding in holdings:
            # Get security and current price (current prices are already post-split, no adjustment needed)
            from services.price_service import PriceService
            security = Security.query.get(holding.security_id)
            if security:
                price_data = PriceService.get_price(holding.security_id, target_date=None, use_adjusted=False)
                if price_data and price_data.is_valid and price_data.price:
                    current_price = float(price_data.price)
                else:
                    current_price = float(security.current_price) if security.current_price else 0.0
            else:
                current_price = 0.0
            
            # Debug logging for EBBETF securities
            if holding.security_id in [2128, 2129]:  # EBBETF0430, EBBETF0431
                logger.info(f"EBBETF Debug - Security ID: {holding.security_id}, Quantity: {holding.quantity}, Current Price: {current_price}, Calculated Value: {float(holding.quantity) * current_price}")
            
            # Calculate values
            quantity = float(holding.quantity)
            average_price = float(holding.average_price)
            
            current_value = quantity * current_price
            total_cost_holding = quantity * average_price
            unrealized_pnl = current_value - total_cost_holding
            unrealized_pnl_percent = (unrealized_pnl / total_cost_holding * 100) if total_cost_holding > 0 else 0.0
            
            # Add to totals
            total_value += current_value
            total_cost += total_cost_holding
            total_unrealized_pnl += unrealized_pnl
            
            # Store calculated values for sorting
            holding.calculated_current_value = current_value
            holding.calculated_total_cost = total_cost_holding
            holding.calculated_unrealized_pnl = unrealized_pnl
            holding.calculated_unrealized_pnl_percent = unrealized_pnl_percent
            
            holdings_with_calculations.append(holding)
        
        total_unrealized_pnl_percent = (total_unrealized_pnl / total_cost * 100) if total_cost > 0 else 0.0
        
        # Get top 5 holdings by value
        top_holdings = []
        sorted_holdings = sorted(holdings_with_calculations, key=lambda h: h.calculated_current_value, reverse=True)
        logger.info(f"Top 5 holdings by value:")
        for i, holding in enumerate(sorted_holdings[:5]):
            security = Security.query.get(holding.security_id)
            logger.info(f"  {i+1}. Security ID: {holding.security_id}, Value: {holding.calculated_current_value}, Security: {security.name if security else 'None'}")
            if security:
                top_holdings.append({
                    'security_name': security.name,
                    'security_symbol': security.symbol,
                    'quantity': float(holding.quantity),
                    'current_value': holding.calculated_current_value,
                    'unrealized_pnl_percent': holding.calculated_unrealized_pnl_percent
                })
        
        # Get top 5 wealth creators (positive P&L)
        wealth_creators = []
        positive_pnl_holdings = [h for h in holdings_with_calculations if h.calculated_unrealized_pnl > 0]
        for holding in sorted(positive_pnl_holdings, key=lambda h: h.calculated_unrealized_pnl, reverse=True)[:5]:
            security = Security.query.get(holding.security_id)
            if security:
                wealth_creators.append({
                    'security_name': security.name,
                    'security_symbol': security.symbol,
                    'quantity': float(holding.quantity),
                    'current_value': holding.calculated_current_value,
                    'unrealized_pnl': holding.calculated_unrealized_pnl,
                    'unrealized_pnl_percent': holding.calculated_unrealized_pnl_percent
                })
        
        # Prepare response data
        summary_data = {
            'total_holdings': len(holdings),
            'total_value': total_value,
            'total_cost': total_cost,
            'total_unrealized_pnl': total_unrealized_pnl,
            'total_unrealized_pnl_percent': total_unrealized_pnl_percent,
            'top_holdings': top_holdings,
            'wealth_creators': wealth_creators,
            'sector_breakdown': {}  # TODO: Implement sector breakdown if needed
        }
        
        return APIResponse.success(
            data=summary_data,
            message=f"Holdings summary for client {client_id}"
        )
        
    except Exception as e:
        logger.error(f"Error getting holdings summary for client {client_id}: {str(e)}")
        raise

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def update_holdings_from_transactions_api(client_id):
    """
    Wrapper for the existing update_holdings_from_transactions function
    Returns API-friendly response format
    """
    try:
        # Call the existing function
        result = update_holdings_from_transactions(client_id)
        
        # Return API-friendly response
        return {
            'success': True,
            'message': 'Holdings updated successfully',
            'data': {
                'client_id': client_id,
                'updated_at': datetime.utcnow().isoformat(),
                'result': result
            }
        }
    except Exception as e:
        logger.error(f"Error updating holdings for client {client_id}: {str(e)}")
        return {
            'success': False,
            'message': f'Failed to update holdings: {str(e)}',
            'data': None
        }
