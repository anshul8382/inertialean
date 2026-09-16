"""
Quantity Adjustment Service - Pure business logic for corporate action quantity adjustments
NO Flask dependencies, NO price adjustments - ONLY quantity calculations

Purpose: Calculate historical quantities by reversing corporate actions
Approach: Store unadjusted data, adjust quantities dynamically for historical dates
"""
from datetime import date
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


def get_corporate_actions_after_date(security_id: int, start_date: date, end_date: date, 
                                     corporate_actions_rows: List[Any]) -> List[Dict[str, Any]]:
    """
    Get corporate actions for a security that happened AFTER start_date
    
    Includes MERGER actions where this security is the target (receiving security)
    
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
                'description': action.description,
                # For MERGER actions
                'source_security_id': getattr(action, 'source_security_id', None),
                'source_quantity': float(action.source_quantity) if hasattr(action, 'source_quantity') and action.source_quantity else 0.0
            })
    
    return sorted(actions, key=lambda x: x['action_date'])


def calculate_quantity_adjustment_factor(corporate_actions: List[Dict[str, Any]]) -> float:
    """
    Calculate cumulative adjustment factor from corporate actions
    
    Args:
        corporate_actions: List of corporate action dictionaries
    
    Returns:
        Cumulative adjustment factor
        
    Examples:
        - 2:1 SPLIT → factor = 2.0
        - 1:1 BONUS → factor = 2.0 (1 + 1)
        - 10:1 SPLIT → factor = 10.0
        - MERGER → handled separately in get_adjusted_quantity_at_date()
    """
    if not corporate_actions:
        return 1.0
    
    factor = 1.0
    for action in corporate_actions:
        if action['action_type'] == 'SPLIT':
            factor *= action['ratio']
        elif action['action_type'] == 'BONUS':
            factor *= (1 + action['ratio'])
        # Note: MERGER is NOT included in factor calculation
        # MERGER is handled separately because it affects multiple securities
    
    return factor


def get_adjusted_quantity_at_date(security_id: int, current_quantity: float, 
                                  target_date: date, corporate_actions_rows: List[Any]) -> float:
    """
    Get adjusted quantity for a security at a historical date
    
    This reverses corporate actions that happened AFTER the target date to get
    the actual quantity that existed on that date.
    
    Supports: SPLIT, BONUS, MERGER/CONVERSION
    
    Args:
        security_id: Security ID
        current_quantity: Current quantity (includes all historical splits/bonuses/mergers)
        target_date: Target historical date
        corporate_actions_rows: List of ALL CorporateAction ORM objects
    
    Returns:
        Adjusted quantity (float) as it was on target_date
        
    Examples:
        - SPLIT: Current 410 shares, 10:1 split after date → 410 / 10 = 41 shares
        - MERGER: Current 771 shares, received 412 from merger after date → 771 - 412 = 359 shares
    """
    # Special handling for 2000-01-01 snapshot date
    # This date represents a holdings snapshot, not a calculated state
    # No corporate action adjustments should be applied
    if target_date == date(2000, 1, 1):
        logger.info(f"Special date 2000-01-01 detected for security {security_id}, returning current quantity unchanged: {current_quantity}")
        return current_quantity
    # Get corporate actions that happened AFTER target_date
    actions_after = get_corporate_actions_after_date(
        security_id, target_date, date.today(), corporate_actions_rows
    )
    
    if not actions_after:
        # No corporate actions after target date, quantity is unchanged
        logger.debug(f"No corporate actions after {target_date} for security {security_id}, quantity unchanged: {current_quantity}")
        return current_quantity
    
    adjusted_quantity = current_quantity
    
    # Process each action in chronological order (oldest first)
    for action in actions_after:
        if action['action_type'] in ['SPLIT', 'BONUS']:
            # For SPLIT/BONUS: divide by factor to reverse
            if action['action_type'] == 'SPLIT':
                factor = action['ratio']
            else:  # BONUS
                factor = 1 + action['ratio']
            
            adjusted_quantity = adjusted_quantity / factor if factor > 0 else adjusted_quantity
            logger.debug(f"  {action['action_type']} {action['ratio']}:1 on {action['action_date']}: "
                        f"{adjusted_quantity * factor:.2f} → {adjusted_quantity:.2f}")
        
        elif action['action_type'] == 'MERGER':
            # For MERGER: only adjust if this security is the SOURCE of the merger
            # If this security is the TARGET (receiving security), no adjustment needed
            source_security_id = action.get('source_security_id')
            if source_security_id and source_security_id != security_id:
                # This security is the TARGET of the merger, not the source
                # No adjustment needed - it didn't lose any shares
                logger.info(f"  MERGER on {action['action_date']}: This security is TARGET, no adjustment needed")
                continue
            
            # This security is the SOURCE of the merger - add back the converted quantity
            source_qty = action.get('source_quantity', 0)
            converted_qty = source_qty * action['ratio']  # Quantity that was converted
            
            adjusted_quantity = adjusted_quantity + converted_qty
            logger.info(f"  MERGER on {action['action_date']}: This security was SOURCE, "
                       f"adding back converted quantity: {adjusted_quantity - converted_qty:.2f} → {adjusted_quantity:.2f}")
    
    logger.info(f"Adjusted quantity for security {security_id} on {target_date}: "
               f"Current={current_quantity}, Adjusted={adjusted_quantity:.2f}, Actions={len(actions_after)}")
    
    return adjusted_quantity


def get_unadjusted_historical_price(security_id: int, target_date: date, 
                                    historical_prices_rows: List[Any],
                                    current_price_fn) -> float:
    """
    Get UNADJUSTED historical price for a security on a specific date
    
    NO corporate action adjustments - just raw historical price from database
    
    Args:
        security_id: Security ID
        target_date: Target date
        historical_prices_rows: List of HistoricalPrice ORM objects
        current_price_fn: Fallback function to get current price
    
    Returns:
        Unadjusted price (float)
        
    Example:
        Target date: 2025-04-19
        Historical price in DB: ₹6,290
        Result: ₹6,290 (NO adjustment even if split happened after!)
    """
    # Try exact date match first
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
            logger.info(f"Using historical price for security {security_id} from {closest_price.date} for {target_date}")
        else:
            # Fallback to current price
            unadjusted_price = current_price_fn(security_id)
            logger.warning(f"No historical price found for security {security_id} on {target_date}, using current price")
    
    return unadjusted_price if unadjusted_price else 0.0


def adjust_quantity_for_portfolio_reconstruction(standing_date: date, target_date: date, 
                                                  security_id: int, current_quantity: float,
                                                  trade_type: str, trade_quantity: float) -> float:
    """
    Quantity Adjustment API for Portfolio Reconstruction
    
    This API handles corporate actions when rolling back transactions in portfolio reconstruction.
    It adjusts quantity between two dates and then processes the trade.
    
    Args:
        standing_date: Last successfully completed rollback date (more recent)
        target_date: Trade date being processed (older)
        security_id: Security ID
        current_quantity: Quantity as of standing_date (after previous rollbacks)
        trade_type: 'BUY' or 'SELL'
        trade_quantity: Trade quantity to process
    
    Returns:
        Adjusted quantity as of target_date (after processing trade and corporate actions)
        
    Algorithm:
        1. Check for corporate actions between target_date and standing_date
        2. If found, adjust current_quantity for those actions (divide for SPLIT, etc.)
        3. Apply the trade (subtract for BUY rollback, add for SELL rollback)
        4. Return the resulting quantity as of target_date
        
    Example (NAUKRI):
        Standing date: 2025-08-04, Current qty: 225, Target date: 2025-04-02, Trade: BUY 2
        Corporate action: 5:1 split on 2025-05-07 (between dates)
        Step 1: Adjust for split: 225 ÷ 5 = 45
        Step 2: Process BUY rollback: 45 - 2 = 43
        Result: 43 shares as of 2025-04-02
    """
    # Special handling for 2000-01-01 snapshot date
    if target_date == date(2000, 1, 1):
        logger.info(f"Special date 2000-01-01 detected for security {security_id}, no corporate action adjustment")
        # Still process trade but no corporate action adjustment
        if trade_type == 'BUY':
            return current_quantity - trade_quantity
        else:  # SELL
            return current_quantity + trade_quantity
    
    # Read corporate actions table internally
    from models import CorporateAction
    corporate_actions_rows = CorporateAction.query.filter_by(is_active=True).all()
    
    # Get corporate actions between target_date and standing_date
    actions_in_period = []
    for action in corporate_actions_rows:
        if (action.security_id == security_id and 
            action.action_date > target_date and 
            action.action_date <= standing_date):
            actions_in_period.append(action)
    
    # Sort by date descending (newest first) - we're going backwards
    actions_in_period.sort(key=lambda x: x.action_date, reverse=True)
    
    logger.info(f"Quantity adjustment API called: standing_date={standing_date}, target_date={target_date}, "
               f"security_id={security_id}, current_qty={current_quantity}, trade={trade_type} {trade_quantity}")
    logger.info(f"Found {len(actions_in_period)} corporate actions between {target_date} and {standing_date}")
    
    # Step 1: Adjust for corporate actions (reverse them to go back in time)
    adjusted_quantity = current_quantity
    
    for action in actions_in_period:
        if action.action_type == 'SPLIT':
            # Reverse split: divide by ratio to get pre-split quantity
            factor = float(action.ratio)
            old_qty = adjusted_quantity
            adjusted_quantity = adjusted_quantity / factor
            logger.info(f"  Reversed SPLIT {factor}:1 on {action.action_date}: {old_qty:.2f} → {adjusted_quantity:.2f}")
            
        elif action.action_type == 'BONUS':
            # Reverse bonus: calculate pre-bonus quantity
            bonus_ratio = float(action.ratio)
            old_qty = adjusted_quantity
            adjusted_quantity = adjusted_quantity / (1 + bonus_ratio)
            logger.info(f"  Reversed BONUS {bonus_ratio}:1 on {action.action_date}: {old_qty:.2f} → {adjusted_quantity:.2f}")
            
        elif action.action_type == 'MERGER':
            # For MERGER: only adjust if this security is the SOURCE of the merger
            # If this security is the TARGET (receiving security), no adjustment needed
            source_security_id = action.source_security_id
            if source_security_id and source_security_id != security_id:
                # This security is the TARGET of the merger, not the source
                # No adjustment needed - it didn't lose any shares
                logger.info(f"  MERGER on {action.action_date}: This security is TARGET, no adjustment needed")
                continue
            
            # This security is the SOURCE of the merger - add back the converted quantity
            source_qty = float(action.source_quantity) if action.source_quantity else 0.0
            conversion_ratio = float(action.ratio)
            converted_qty = source_qty * conversion_ratio
            old_qty = adjusted_quantity
            adjusted_quantity = adjusted_quantity + converted_qty
            logger.info(f"  Reversed MERGER on {action.action_date}: {old_qty:.2f} → {adjusted_quantity:.2f}")
    
    # Step 2: Process trade (rollback means reverse the trade)
    if trade_type == 'BUY':
        # Rolling back a BUY means we had fewer shares before
        final_quantity = adjusted_quantity - trade_quantity
        logger.info(f"  Processed BUY rollback: {adjusted_quantity:.2f} - {trade_quantity} = {final_quantity:.2f}")
    else:  # SELL
        # Rolling back a SELL means we had more shares before
        final_quantity = adjusted_quantity + trade_quantity
        logger.info(f"  Processed SELL rollback: {adjusted_quantity:.2f} + {trade_quantity} = {final_quantity:.2f}")
    
    logger.info(f"Quantity adjustment result: {current_quantity:.2f} (standing) → {final_quantity:.2f} (target)")
    
    return final_quantity


def calculate_portfolio_value_at_date(holdings_rows: List[Any], target_date: date,
                                     corporate_actions_rows: List[Any],
                                     historical_prices_rows: List[Any],
                                     current_price_fn) -> Dict[str, Any]:
    """
    Calculate complete portfolio value at a historical date
    
    Uses:
    - get_adjusted_quantity_at_date() for quantities
    - get_unadjusted_historical_price() for prices
    
    Args:
        holdings_rows: Current holdings (includes all historical corporate actions)
        target_date: Target historical date
        corporate_actions_rows: All corporate actions
        historical_prices_rows: All historical prices
        current_price_fn: Fallback for current prices
    
    Returns:
        Dictionary with portfolio state at target_date
    """
    portfolio_holdings = []
    total_value = 0.0
    
    for holding in holdings_rows:
        security_id = holding.security_id
        symbol = holding.security.symbol if holding.security else f'SEC_{security_id}'
        current_quantity = float(holding.quantity)
        
        # Get adjusted quantity at target date
        adjusted_quantity = get_adjusted_quantity_at_date(
            security_id, current_quantity, target_date, corporate_actions_rows
        )
        
        # Get unadjusted historical price
        historical_price = get_unadjusted_historical_price(
            security_id, target_date, historical_prices_rows, current_price_fn
        )
        
        # Calculate value (simple multiplication - no adjustments needed!)
        holding_value = adjusted_quantity * historical_price
        
        portfolio_holdings.append({
            'security_id': security_id,
            'symbol': symbol,
            'quantity': adjusted_quantity,
            'price': historical_price,
            'value': holding_value
        })
        
        total_value += holding_value
    
    logger.info(f"Portfolio value at {target_date}: ₹{total_value:,.2f} ({len(portfolio_holdings)} holdings)")
    
    return {
        'date': target_date,
        'holdings': portfolio_holdings,
        'total_value': total_value,
        'holdings_count': len(portfolio_holdings)
    }

