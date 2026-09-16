"""
Adjustment API for Corporate-Action-Aware Analytics

This API centralizes split/bonus adjustments and P&L calculations while preserving original transactions.
It provides adjusted holdings metrics and correct realized/unrealized P&L at a cutoff date.
"""
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import and_, desc, asc
from flask import Blueprint, request, jsonify
from models import (
    db, Client, Transaction, Security, HistoricalPrice, Holding, CorporateAction
)
from services.adjustments_service import (
    get_corporate_actions, 
    calculate_adjustment_factor,
    adjust_holdings_for_corporate_actions,
    calculate_adjusted_trades_pnl,
    get_price_for_security
)
import logging

logger = logging.getLogger(__name__)

adjustments_bp = Blueprint('adjustments', __name__)

def get_corporate_actions_from_db(security_id, start_date, end_date):
    """
    Get corporate actions from database for a security between dates
    
    Args:
        security_id: Security ID
        start_date: Start date (exclusive)
        end_date: End date (inclusive)
    
    Returns:
        List of corporate action ORM objects
    """
    # First try to get from CorporateAction table
    actions = CorporateAction.query.filter(
        CorporateAction.security_id == security_id,
        CorporateAction.action_date > start_date,
        CorporateAction.action_date <= end_date,
        CorporateAction.is_active == True
    ).order_by(CorporateAction.action_date).all()
    
    # CorporateAction table is now the ONLY source of truth
    # No fallback to Transaction table since SPLIT/BONUS transactions were removed
    
    return actions

def get_adjustment_factor(security_id, start_date, end_date):
    """Wrapper for backward compatibility"""
    return get_adjustment_factor_from_db(security_id, start_date, end_date)

def get_adjustment_factor_from_db(security_id, start_date, end_date):
    """
    Calculate cumulative adjustment factor for a security between dates using database
    
    Args:
        security_id: Security ID
        start_date: Start date (exclusive)
        end_date: End date (inclusive)
    
    Returns:
        Cumulative adjustment factor (float)
    """
    actions = get_corporate_actions_from_db(security_id, start_date, end_date)
    
    factor = 1.0
    for action in actions:
        # Handle both CorporateAction objects and converted Transaction objects
        action_type = getattr(action, 'action_type', getattr(action, 'type', None))
        ratio = getattr(action, 'ratio', getattr(action, 'quantity', 1.0))
        
        if action_type == 'SPLIT':
            # Split factor: e.g., 2.0 for 1:2 split
            factor *= float(ratio)
        elif action_type == 'BONUS':
            # Bonus factor: e.g., 1.5 for 1:2 bonus (1 + 0.5)
            factor *= (1.0 + float(ratio))
    
    return factor

def get_price(security_id, target_date, basis='adjusted'):
    """
    Get price for a security on a specific date with fallback logic
    
    Args:
        security_id: Security ID
        target_date: Target date
        basis: 'adjusted' or 'unadjusted' (currently only adjusted supported)
    
    Returns:
        Price (float) or 0.0 if not found
    """
    # Try exact date match first
    price_data = HistoricalPrice.query.filter_by(
        security_id=security_id,
        date=target_date
    ).first()
    
    if price_data:
        return float(price_data.close_price)
    
    # If no exact date, get the closest previous date
    price_data = HistoricalPrice.query.filter(
        HistoricalPrice.security_id == security_id,
        HistoricalPrice.date <= target_date
    ).order_by(desc(HistoricalPrice.date)).first()
    
    if price_data:
        logger.info(f"Using historical price for security {security_id} from {price_data.date} for date {target_date}")
        return float(price_data.close_price)
    
    # If no historical data, try current price as fallback
    security = Security.query.get(security_id)
    if security and security.current_price:
        logger.warning(f"No historical price found for security {security_id} on {target_date}, using current price")
        return float(security.current_price)
    
    logger.error(f"No price data available for security {security_id} on {target_date}")
    return 0.0

def match_fifo(client_id, security_id, cutoff_date):
    """
    Match BUY and SELL transactions using FIFO method up to cutoff date
    
    Args:
        client_id: Client ID
        security_id: Security ID
        cutoff_date: Cutoff date (inclusive)
    
    Returns:
        Tuple of (matched_lots, remaining_inventory)
    """
    # Get all transactions for the security up to cutoff date
    transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.security_id == security_id,
        Transaction.transaction_date <= cutoff_date
    ).order_by(Transaction.transaction_date).all()
    
    # Separate BUY and SELL transactions
    buy_lots = []
    sell_transactions = []
    
    for tx in transactions:
        if tx.type == 'BUY':
            buy_lots.append({
                'tx_id': tx.id,
                'date': tx.transaction_date,
                'quantity': float(tx.quantity),
                'price': float(tx.price),
                'remaining_qty': float(tx.quantity)  # Track remaining quantity
            })
        elif tx.type == 'SELL':
            sell_transactions.append({
                'tx_id': tx.id,
                'date': tx.transaction_date,
                'quantity': float(tx.quantity),
                'price': float(tx.price)
            })
    
    # Apply FIFO matching
    matched_lots = []
    
    for sell in sell_transactions:
        remaining_sell_qty = sell['quantity']
        
        for buy_lot in buy_lots:
            if remaining_sell_qty <= 0:
                break
            if buy_lot['remaining_qty'] <= 0:
                continue
            
            # Calculate matched quantity
            matched_qty = min(remaining_sell_qty, buy_lot['remaining_qty'])
            
            if matched_qty > 0:
                matched_lots.append({
                    'sell_tx_id': sell['tx_id'],
                    'sell_date': sell['date'],
                    'sell_price': sell['price'],
                    'buy_tx_id': buy_lot['tx_id'],
                    'buy_date': buy_lot['date'],
                    'buy_price': buy_lot['price'],
                    'matched_qty': matched_qty
                })
                
                # Update remaining quantities
                remaining_sell_qty -= matched_qty
                buy_lot['remaining_qty'] -= matched_qty
    
    # Calculate remaining inventory
    remaining_inventory = []
    for buy_lot in buy_lots:
        if buy_lot['remaining_qty'] > 0:
            remaining_inventory.append({
                'tx_id': buy_lot['tx_id'],
                'date': buy_lot['date'],
                'quantity': buy_lot['remaining_qty'],
                'price': buy_lot['price']
            })
    
    return matched_lots, remaining_inventory

def calculate_adjusted_holdings(client_id, cutoff_date):
    """
    Calculate adjusted holdings at cutoff date
    
    Args:
        client_id: Client ID
        cutoff_date: Cutoff date
    
    Returns:
        List of adjusted holdings per security
    """
    # Get current holdings from database
    current_holdings = Holding.query.filter_by(client_id=client_id).all()
    
    # ✅ NEW ARCHITECTURE: Get ALL corporate actions for QUANTITY adjustments only
    # No price adjustments - prices are always unadjusted
    all_corporate_actions = CorporateAction.query.filter(
        CorporateAction.is_active == True
    ).all()  # No date filter - quantity adjustment service needs all actions
    
    # Get all historical prices
    all_historical_prices = HistoricalPrice.query.all()
    
    # ✅ NEW ARCHITECTURE: Price lookup returns UNADJUSTED historical prices
    # Quantity adjustment is handled separately by adjust_holdings_for_corporate_actions()
    from services.quantity_adjustment_service import get_unadjusted_historical_price
    
    def price_lookup_fn(security_id, target_date):
        return get_unadjusted_historical_price(
            security_id, 
            target_date, 
            all_historical_prices, 
            lambda sid: Security.query.get(sid).current_price if Security.query.get(sid) else 0.0
        )
        # Returns RAW historical price - NO adjustments!
    
    # Use pure service to calculate adjusted holdings
    adjusted_holdings = adjust_holdings_for_corporate_actions(
        current_holdings, 
        all_corporate_actions, 
        cutoff_date, 
        price_lookup_fn
    )
    
    return adjusted_holdings

def calculate_adjusted_trades_pnl(client_id, cutoff_date):
    """
    Calculate adjusted P&L for trades up to cutoff date using pure service
    
    Args:
        client_id: Client ID
        cutoff_date: Cutoff date
    
    Returns:
        List of adjusted trade P&L records
    """
    # Get all trades for the client
    all_trades = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.transaction_date <= cutoff_date
    ).all()
    
    # Get all corporate actions
    all_corporate_actions = CorporateAction.query.filter(
        CorporateAction.action_date <= cutoff_date,
        CorporateAction.is_active == True
    ).all()
    
    # Create price lookup function
    def price_lookup_fn(security_id, target_date):
        return get_price_for_security(security_id, target_date, 
                                    HistoricalPrice.query.all(),
                                    lambda sid: Security.query.get(sid).current_price if Security.query.get(sid) else 0.0)
    
    # Use pure service to calculate adjusted trades P&L
    from services.adjustments_service import calculate_adjusted_trades_pnl as service_calculate_adjusted_trades_pnl
    trades_pnl = service_calculate_adjusted_trades_pnl(
        all_trades, 
        all_corporate_actions, 
        cutoff_date, 
        'fifo', 
        price_lookup_fn
    )
    
    return trades_pnl

def get_factors_used(client_id, cutoff_date):
    """
    Get corporate action factors used for each security
    
    Args:
        client_id: Client ID
        cutoff_date: Cutoff date
    
    Returns:
        Dictionary of factors per security
    """
    # Get all securities for the client
    securities = db.session.query(Security).join(Transaction).filter(
        Transaction.client_id == client_id,
        Transaction.transaction_date <= cutoff_date
    ).distinct().all()
    
    factors_used = {}
    
    for security in securities:
        security_factors = []
        
        # Get all corporate actions for this security from CorporateAction table
        actions = CorporateAction.query.filter(
            CorporateAction.security_id == security.id,
            CorporateAction.action_date <= cutoff_date,
            CorporateAction.is_active == True
        ).order_by(CorporateAction.action_date).all()
        
        cumulative_factor = 1.0
        for action in actions:
            if action.action_type == 'SPLIT':
                factor = float(action.ratio)
            elif action.action_type == 'BONUS':
                factor = 1.0 + float(action.ratio)
            else:
                continue  # Skip MERGER actions for factor calculation
            
            cumulative_factor *= factor
            
            security_factors.append({
                'date': action.action_date.isoformat(),
                'type': action.action_type,
                'ratio': float(action.ratio),
                'factor': factor,
                'cumulative_factor': round(cumulative_factor, 6)
            })
        
        if security_factors:
            factors_used[security.symbol] = security_factors
    
    return factors_used

@adjustments_bp.route('/<int:client_id>', methods=['GET'])
def get_adjustments(client_id):
    """
    Get adjusted holdings and P&L calculations for a client at cutoff date
    
    Query Parameters:
        - cutoff_date (required): YYYY-MM-DD format
        - matching (optional): fifo|lifo (default: fifo)
        - basis (optional): adjusted|unadjusted (default: adjusted)
        - include_audit (optional): true|false (default: true)
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get query parameters
        cutoff_date_str = request.args.get('cutoff_date')
        if not cutoff_date_str:
            return jsonify({
                'success': False,
                'message': 'cutoff_date parameter is required (YYYY-MM-DD format)'
            }), 400
        
        try:
            cutoff_date = datetime.strptime(cutoff_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({
                'success': False,
                'message': 'Invalid cutoff_date format. Use YYYY-MM-DD'
            }), 400
        
        matching = request.args.get('matching', 'fifo').lower()
        if matching not in ['fifo', 'lifo']:
            return jsonify({
                'success': False,
                'message': 'matching must be fifo or lifo'
            }), 400
        
        basis = request.args.get('basis', 'adjusted').lower()
        if basis not in ['adjusted', 'unadjusted']:
            return jsonify({
                'success': False,
                'message': 'basis must be adjusted or unadjusted'
            }), 400
        
        include_audit = request.args.get('include_audit', 'true').lower() == 'true'
        
        logger.info(f"Adjustment calculation for client {client_id} at {cutoff_date}")
        
        # Calculate adjusted holdings
        adjusted_holdings = calculate_adjusted_holdings(client_id, cutoff_date)
        
        # Calculate adjusted trades P&L
        trades_pnl = calculate_adjusted_trades_pnl(client_id, cutoff_date)
        
        # Get factors used
        factors_used = get_factors_used(client_id, cutoff_date)
        
        # Calculate summary totals
        total_invested_cost_adjusted = sum(h['adjusted_cost_total'] for h in adjusted_holdings)
        total_market_value_adjusted = sum(h['quantity_at_cutoff'] * h['price_at_cutoff'] for h in adjusted_holdings)
        total_realized_pnl = sum(t['realized_pnl'] for t in trades_pnl)
        total_unrealized_pnl = sum(h['unrealized_pnl'] for h in adjusted_holdings)
        
        # Prepare response
        response_data = {
            'client_id': client_id,
            'client_name': client.name,
            'cutoff_date': cutoff_date.isoformat(),
            'matching': matching,
            'basis': basis,
            'summary': {
                'total_invested_cost_adjusted': round(total_invested_cost_adjusted, 2),
                'total_market_value_adjusted': round(total_market_value_adjusted, 2),
                'total_realized_pnl_to_date': round(total_realized_pnl, 2),
                'total_unrealized_pnl_at_cutoff': round(total_unrealized_pnl, 2),
                'total_pnl': round(total_realized_pnl + total_unrealized_pnl, 2)
            },
            'holdings_adjusted': adjusted_holdings,
            'trades_pnl_adjusted': trades_pnl,
            'factors_used': factors_used
        }
        
        # Add audit information if requested
        if include_audit:
            response_data['audit'] = {
                'calculation_timestamp': datetime.utcnow().isoformat(),
                'price_lookup_method': 'HistoricalPrice with closest-previous fallback',
                'corporate_actions_source': 'CorporateAction table',
                'reconstruction_method': 'PortfolioReconstructionService',
                'securities_processed': len(adjusted_holdings),
                'trades_processed': len(trades_pnl)
            }
        
        return jsonify({
            'success': True,
            'message': f'Adjustment calculation completed for {client.name}',
            'data': response_data
        })
        
    except Exception as e:
        logger.error(f"Error in adjustment calculation for client {client_id}: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Adjustment calculation failed: {str(e)}'
        }), 500
