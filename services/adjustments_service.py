"""
Pure Corporate Actions Adjustment Service
No Flask dependencies, no circular imports - just pure business logic

UPDATED: Now uses quantity-only adjustments with unadjusted prices
- Quantities adjusted using quantity_adjustment_service
- Prices are ALWAYS unadjusted (raw historical prices)
"""
from datetime import datetime, date
from decimal import Decimal
from typing import List, Dict, Any, Optional, Callable
import logging

# Import new quantity adjustment service
from services.quantity_adjustment_service import (
    get_adjusted_quantity_at_date,
    get_unadjusted_historical_price
)

logger = logging.getLogger(__name__)

def get_corporate_actions(security_id: int, start_date: date, end_date: date, 
                         corporate_actions_rows: List[Any]) -> List[Dict[str, Any]]:
    """
    Get corporate actions for a security between dates
    
    Args:
        security_id: Security ID
        start_date: Start date (exclusive)
        end_date: End date (inclusive)
        corporate_actions_rows: List of CorporateAction ORM objects
    
    Returns:
        List of corporate action dictionaries
    """
    actions = []
    
    for action in corporate_actions_rows:
        if (action.security_id == security_id and 
            action.action_date > start_date and 
            action.action_date <= end_date):
            
            actions.append({
                'id': action.id,
                'security_id': action.security_id,
                'action_type': action.action_type,
                'action_date': action.action_date,
                'ratio': float(action.ratio),
                'description': action.description
            })
    
    return sorted(actions, key=lambda x: x['action_date'])

def calculate_adjustment_factor(corporate_actions: List[Dict[str, Any]]) -> float:
    """
    Calculate cumulative adjustment factor from corporate actions
    
    Args:
        corporate_actions: List of corporate action dictionaries
    
    Returns:
        Cumulative adjustment factor
    """
    if not corporate_actions:
        return 1.0
    
    factor = 1.0
    for action in corporate_actions:
        if action['action_type'] == 'SPLIT':
            factor *= action['ratio']
        elif action['action_type'] == 'BONUS':
            factor *= (1 + action['ratio'])
    
    return factor

def adjust_holdings_for_corporate_actions(holdings_rows: List[Any], 
                                         corporate_actions_rows: List[Any],
                                         cutoff_date: date,
                                         price_lookup_fn: Callable[[int, date], float]) -> List[Dict[str, Any]]:
    """
    Calculate adjusted holdings for historical dates using quantity-only adjustments
    
    NEW ARCHITECTURE:
    - ONLY adjusts quantities (reverses splits/bonuses that happened after cutoff_date)
    - Prices are ALWAYS unadjusted (raw historical prices from database)
    - Simple: quantity adjustment + unadjusted price = correct historical value
    
    Args:
        holdings_rows: List of Holding ORM objects (current holdings)
        corporate_actions_rows: List of ALL CorporateAction ORM objects
        cutoff_date: Cutoff date for adjustments
        price_lookup_fn: Function to get UNADJUSTED price for security on date
    
    Returns:
        List of adjusted holding dictionaries showing holdings as they were on cutoff_date
        
    Example:
        Current: 410 shares @ ₹1,031
        Cutoff: 2025-04-19 (before 10:1 split on 2025-06-16)
        Result: 41 shares @ ₹6,290 = ₹257,890 ✅
    """
    adjusted_holdings = []
    
    for holding in holdings_rows:
        security_id = holding.security_id
        symbol = holding.security.symbol if holding.security else f'SEC_{security_id}'
        current_quantity = float(holding.quantity)  # Current quantity (includes all historical splits/bonuses)
        
        # ✅ STEP 1: Get adjusted quantity at cutoff date (ONLY quantity adjustment)
        adjusted_quantity = get_adjusted_quantity_at_date(
            security_id, current_quantity, cutoff_date, corporate_actions_rows
        )
        
        # ✅ STEP 2: Get UNADJUSTED historical price (NO price adjustment)
        # price_lookup_fn should return raw historical price from database
        unadjusted_price = price_lookup_fn(security_id, cutoff_date)
        
        # ✅ STEP 3: Calculate value (simple multiplication!) - ensure all floats
        holding_value = float(adjusted_quantity) * float(unadjusted_price)
        cost_basis = float(adjusted_quantity) * float(unadjusted_price)  # For historical dates, price = cost
        
        adjusted_holdings.append({
            'security_id': security_id,
            'symbol': symbol,
            'quantity_at_cutoff': float(adjusted_quantity),
            'adjusted_avg_price': float(unadjusted_price),
            'adjusted_avg_cost_per_share': float(unadjusted_price),
            'adjusted_cost_total': float(cost_basis),
            'price_at_cutoff': float(unadjusted_price),
            'current_price': float(unadjusted_price),
            'unrealized_pnl': 0.0,  # For historical reconstruction, no unrealized P&L at that point
            'adjustment_factor': float(current_quantity / adjusted_quantity) if adjusted_quantity > 0 else 1.0,
            'corporate_actions': []  # Can add details if needed
        })
    
    return adjusted_holdings

def calculate_adjusted_trades_pnl(trades_rows: List[Any], 
                                 corporate_actions_rows: List[Any],
                                 cutoff_date: date,
                                 matching: str = 'fifo',
                                 price_lookup_fn: Callable[[int, date], float] = None) -> List[Dict[str, Any]]:
    """
    Calculate adjusted P&L for trades using FIFO matching
    
    Args:
        trades_rows: List of Transaction ORM objects
        corporate_actions_rows: List of CorporateAction ORM objects
        cutoff_date: Cutoff date for calculations
        matching: Matching method ('fifo')
        price_lookup_fn: Function to get price for security on date
    
    Returns:
        List of trade P&L dictionaries
    """
    if not price_lookup_fn:
        raise ValueError("price_lookup_fn is required")
    
    # Group trades by security
    trades_by_security = {}
    for trade in trades_rows:
        if trade.transaction_date <= cutoff_date:
            if trade.security_id not in trades_by_security:
                trades_by_security[trade.security_id] = []
            trades_by_security[trade.security_id].append(trade)
    
    trades_pnl = []
    
    for security_id, trades in trades_by_security.items():
        # Get corporate actions for this security
        corporate_actions = get_corporate_actions(
            security_id, date(2000, 1, 1), cutoff_date, corporate_actions_rows
        )
        
        # Calculate adjustment factor
        adjustment_factor = calculate_adjustment_factor(corporate_actions)
        
        # Apply FIFO matching
        buy_trades = [t for t in trades if t.type == 'BUY']
        sell_trades = [t for t in trades if t.type == 'SELL']
        
        # Sort by date
        buy_trades.sort(key=lambda x: x.transaction_date)
        sell_trades.sort(key=lambda x: x.transaction_date)
        
        # Apply adjustments to quantities and prices
        adjusted_buy_trades = []
        for trade in buy_trades:
            adjusted_buy_trades.append({
                'date': trade.transaction_date,
                'quantity': float(trade.quantity) * adjustment_factor,
                'price': float(trade.price) / adjustment_factor if adjustment_factor > 0 else float(trade.price),
                'original_quantity': float(trade.quantity),
                'original_price': float(trade.price)
            })
        
        adjusted_sell_trades = []
        for trade in sell_trades:
            adjusted_sell_trades.append({
                'date': trade.transaction_date,
                'quantity': float(trade.quantity) * adjustment_factor,
                'price': float(trade.price) / adjustment_factor if adjustment_factor > 0 else float(trade.price),
                'original_quantity': float(trade.quantity),
                'original_price': float(trade.price)
            })
        
        # FIFO matching
        remaining_buys = adjusted_buy_trades.copy()
        total_pnl = 0.0
        
        for sell_trade in adjusted_sell_trades:
            sell_quantity = sell_trade['quantity']
            sell_price = sell_trade['price']
            
            while sell_quantity > 0 and remaining_buys:
                buy_trade = remaining_buys[0]
                buy_quantity = buy_trade['quantity']
                buy_price = buy_trade['price']
                
                if buy_quantity <= sell_quantity:
                    # Use entire buy trade
                    pnl = (sell_price - buy_price) * buy_quantity
                    total_pnl += pnl
                    sell_quantity -= buy_quantity
                    remaining_buys.pop(0)
                else:
                    # Use partial buy trade
                    pnl = (sell_price - buy_price) * sell_quantity
                    total_pnl += pnl
                    buy_trade['quantity'] -= sell_quantity
                    sell_quantity = 0
        
        trades_pnl.append({
            'security_id': security_id,
            'symbol': trades[0].security.symbol if trades and trades[0].security else f'SEC_{security_id}',
            'total_pnl': total_pnl,
            'adjustment_factor': adjustment_factor,
            'corporate_actions': corporate_actions,
            'buy_trades_count': len(buy_trades),
            'sell_trades_count': len(sell_trades)
        })
    
    return trades_pnl

def get_price_for_security(security_id: int, target_date: date, 
                          historical_prices_rows: List[Any],
                          current_prices_fn: Callable[[int], float],
                          corporate_actions_rows: List[Any] = None,
                          adjust_for_actions: bool = True) -> float:
    """
    Get price for security on target date with fallback logic and corporate action adjustment
    Uses daily cron job maintained historical prices directly from database
    
    IMPORTANT: Historical prices in database are UNADJUSTED.
    This function adjusts them for splits/bonuses that occurred AFTER the target date.
    
    Args:
        security_id: Security ID
        target_date: Target date
        historical_prices_rows: List of HistoricalPrice ORM objects
        current_prices_fn: Function to get current price for security
        corporate_actions_rows: List of CorporateAction ORM objects (optional)
        adjust_for_actions: Whether to adjust price for corporate actions after target_date
    
    Returns:
        Price for the security on target date (adjusted if requested)
    """
    # First try to get exact date
    unadjusted_price = None
    for price_data in historical_prices_rows:
        if price_data.security_id == security_id and price_data.date == target_date:
            unadjusted_price = float(price_data.close_price)
            break
    
    # If no exact date, get the closest previous date
    if unadjusted_price is None:
        closest_price = None
        for price_data in historical_prices_rows:
            if (price_data.security_id == security_id and 
                price_data.date <= target_date):
                if closest_price is None or price_data.date > closest_price.date:
                    closest_price = price_data
        
        if closest_price:
            unadjusted_price = float(closest_price.close_price)
        else:
            # Fallback to current price
            unadjusted_price = current_prices_fn(security_id)
    
    # If no adjustment needed or no corporate actions provided, return unadjusted price
    if not adjust_for_actions or corporate_actions_rows is None:
        return unadjusted_price
    
    # ✅ ADJUST PRICE FOR CORPORATE ACTIONS AFTER TARGET DATE
    # Get corporate actions that occurred AFTER the target date
    actions_after_target = get_corporate_actions(
        security_id, target_date, date.today(), corporate_actions_rows
    )
    
    if not actions_after_target:
        # No corporate actions after target date, return unadjusted price
        return unadjusted_price
    
    # Calculate adjustment factor for actions after target date
    adjustment_factor = calculate_adjustment_factor(actions_after_target)
    
    # Adjust the historical price
    # If there was a 2:1 split after target date, the old price should be divided by 2
    adjusted_price = unadjusted_price / adjustment_factor if adjustment_factor > 0 else unadjusted_price
    
    logger.info(f"Adjusted price for security {security_id} on {target_date}: "
               f"Unadjusted={unadjusted_price:.2f}, Factor={adjustment_factor:.2f}, "
               f"Adjusted={adjusted_price:.2f}, Actions={len(actions_after_target)}")
    
    return adjusted_price
