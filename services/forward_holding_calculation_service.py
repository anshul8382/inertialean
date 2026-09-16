"""
Forward Holding Calculation Service

Calculates holding quantities by going forward from transactions and applying corporate actions chronologically.
This provides the most accurate calculation method as transactions are the source of truth.

Purpose: Calculate holdings for any client/security/date combination
Approach: Start with 0, add transactions, apply corporate actions as they occur
"""
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional
import logging
import math
import os

logger = logging.getLogger(__name__)


def _forward_verbose() -> bool:
    """Set FORWARD_HOLDING_VERBOSE=1 to emit per-trade / per-CA trace lines at DEBUG."""
    return os.environ.get("FORWARD_HOLDING_VERBOSE", "").lower() in ("1", "true", "yes", "on")


def _trace(msg: str, *args, **kwargs) -> None:
    if _forward_verbose():
        logger.debug(msg, *args, **kwargs)


def configure_forward_holding_logging() -> None:
    """Default: WARNING (quiet). FORWARD_HOLDING_VERBOSE=1 → DEBUG trace."""
    level = logging.DEBUG if _forward_verbose() else logging.WARNING
    logger.setLevel(level)


def _get_client_first_valid_date(client_id: int, as_of_date: date, db_session=None):
    """
    Earliest non-dummy transaction date for the client across ALL securities,
    on/before ``as_of_date``. This is the CLIENT-LEVEL cutoff for corporate actions
    and is identical for every security at a given as_of_date.

    Memoized per (client_id, as_of_date) on the Flask request context so the full
    client-transaction scan runs once per portfolio date instead of once per security.
    Only cached on the standard path (db_session is None); custom sessions may include
    uncommitted data and must not be shared.
    """
    from models import Transaction
    from services.cashflow_service import DUMMY_DATES

    cache_key = None
    if db_session is None:
        try:
            from flask import g
            cache_key = f'_cfvd_{client_id}_{as_of_date}'
            if not hasattr(g, '_client_first_valid_date_cache'):
                g._client_first_valid_date_cache = {}
            if cache_key in g._client_first_valid_date_cache:
                return g._client_first_valid_date_cache[cache_key]
        except RuntimeError:
            cache_key = None

    if db_session is not None:
        all_client_transactions = db_session.query(Transaction).filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date <= as_of_date
        ).order_by(Transaction.transaction_date).all()
    else:
        all_client_transactions = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date <= as_of_date
        ).order_by(Transaction.transaction_date).all()

    client_first_valid_date = None
    for txn in all_client_transactions:
        if isinstance(txn.transaction_date, str):
            txn_date = datetime.strptime(txn.transaction_date, '%Y-%m-%d').date()
        elif hasattr(txn.transaction_date, 'date'):
            txn_date = txn.transaction_date.date()
        else:
            txn_date = txn.transaction_date

        if txn_date not in DUMMY_DATES:
            if client_first_valid_date is None or txn_date < client_first_valid_date:
                client_first_valid_date = txn_date

    if cache_key is not None:
        try:
            from flask import g
            g._client_first_valid_date_cache[cache_key] = client_first_valid_date
        except RuntimeError:
            pass

    return client_first_valid_date


def get_holding_quantity_by_date(client_id: int, security_id: int, as_of_date: date, db_session=None) -> Dict[str, Any]:
    """
    Calculate holding quantity for a client/security as of a specific date
    using forward calculation from transactions + corporate actions
    
    Args:
        client_id: Client ID
        security_id: Security ID  
        as_of_date: Date to calculate holdings for
        db_session: Optional database session to use for queries (for uncommitted transactions)
        
    Returns:
        {
            'quantity': float,
            'average_price': float,
            'total_cost': float,
            'transactions_processed': int,
            'corporate_actions_applied': int,
            'calculation_method': 'forward',
            'as_of_date': date
        }
    """
    try:
        from models import Transaction, CorporateAction
        
        # Use provided session or fall back to global query
        if db_session is not None:
            # Use session.query() for uncommitted transaction visibility
            transactions = db_session.query(Transaction).filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.transaction_date <= as_of_date
            ).order_by(Transaction.transaction_date).all()
        else:
            # Use global query for committed transactions
            transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.transaction_date <= as_of_date
            ).order_by(Transaction.transaction_date).all()
        
        _trace(
            "Forward calculation: Client %s, Security %s, Date %s, Found %s transactions",
            client_id,
            security_id,
            as_of_date,
            len(transactions),
        )
        
        # CRITICAL FIX: Find first valid transaction date at CLIENT level (across ALL securities)
        # This is the cutoff date for corporate actions - should be same for all securities.
        # Memoized per (client_id, as_of_date) so the full client-transaction scan runs once
        # per portfolio date instead of once per security.
        client_first_valid_date = _get_client_first_valid_date(client_id, as_of_date, db_session)
        
        # Step 2: Get corporate actions up to as_of_date
        # PROTECTION: Only apply corporate actions after CLIENT-LEVEL first valid transaction date
        # This ensures no corporate actions are applied between 2000-01-01 and the client's first real trade
        # First valid trade (of any security) marks the cutoff for ALL corporate actions
        if client_first_valid_date:
            if db_session is not None:
                # Get corporate actions where this security is the target
                corporate_actions = db_session.query(CorporateAction).filter(
                    CorporateAction.security_id == security_id,
                    CorporateAction.action_date > client_first_valid_date,  # Apply cutoff to ALL actions
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
                
                # Also get MERGER actions where this security is the SOURCE (to deduct merged quantity)
                source_merger_actions = db_session.query(CorporateAction).filter(
                    CorporateAction.source_security_id == security_id,
                    CorporateAction.action_type == 'MERGER',
                    CorporateAction.action_date > client_first_valid_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
            else:
                # Get corporate actions where this security is the target
                corporate_actions = CorporateAction.query.filter(
                    CorporateAction.security_id == security_id,
                    CorporateAction.action_date > client_first_valid_date,  # Apply cutoff to ALL actions
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
                
                # Also get MERGER actions where this security is the SOURCE (to deduct merged quantity)
                source_merger_actions = CorporateAction.query.filter(
                    CorporateAction.source_security_id == security_id,
                    CorporateAction.action_type == 'MERGER',
                    CorporateAction.action_date > client_first_valid_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
            
            # Combine both lists
            corporate_actions = list(corporate_actions) + list(source_merger_actions)
            
            if _forward_verbose():
                logger.debug(
                    "Client %s first valid date: %s; %s corporate actions for security %s",
                    client_id,
                    client_first_valid_date,
                    len(corporate_actions),
                    security_id,
                )
                for ca in corporate_actions:
                    source_id = getattr(ca, "source_security_id", None)
                    _trace(
                        "  CA %s %s on %s security_id=%s source=%s ratio=%s",
                        ca.id,
                        ca.action_type,
                        ca.action_date,
                        ca.security_id,
                        source_id,
                        ca.ratio,
                    )
        else:
            corporate_actions = []
            _trace("No valid transactions for client %s, skipping corporate actions", client_id)
        
        # Step 3: Forward calculation
        # Track total quantity bought and total cost for accurate cost basis calculation
        total_quantity_bought = 0.0
        total_cost_bought = 0.0
        final_quantity = 0.0
        
        # Process all events chronologically
        all_events = []
        
        # Add transactions
        for txn in transactions:
            # Normalize transaction date to ensure it's a date object
            if isinstance(txn.transaction_date, str):
                txn_date = datetime.strptime(txn.transaction_date, '%Y-%m-%d').date()
            elif hasattr(txn.transaction_date, 'date'):
                txn_date = txn.transaction_date.date()
            else:
                txn_date = txn.transaction_date
            all_events.append(('transaction', txn_date, txn))
        
        # Add corporate actions
        for ca in corporate_actions:
            # Convert action_date to date for comparison (handle both date and datetime objects)
            # Also handle string dates that might come from database
            if isinstance(ca.action_date, str):
                ca_date = datetime.strptime(ca.action_date, '%Y-%m-%d').date()
            elif hasattr(ca.action_date, 'date'):
                ca_date = ca.action_date.date()
            else:
                ca_date = ca.action_date
            all_events.append(('corporate_action', ca_date, ca))
            # Log DEMERGER actions specifically
            if ca.action_type == 'DEMERGER' and _forward_verbose():
                source_id = getattr(ca, 'source_security_id', None)
                _trace(
                    "Added DEMERGER CA %s date %s security_id=%s source=%s ratio=%s (calc for %s)",
                    ca.id,
                    ca_date,
                    ca.security_id,
                    source_id,
                    ca.ratio,
                    security_id,
                )
        
        # Sort by date, then by event type priority
        # Priority: SELL transactions (0) -> Other transactions (1) -> Corporate actions (2)
        # This ensures SELL transactions are processed BEFORE corporate actions on the same date
        # This is critical: if a SELL and SPLIT/BONUS happen on the same date, SELL must happen first
        def get_event_priority(event_type, event):
            if event_type == 'transaction':
                if event.type == 'SELL':
                    return 0  # SELL must happen first
                else:
                    return 1  # BUY transactions
            else:
                return 2  # Corporate actions
        
        all_events.sort(key=lambda x: (x[1], get_event_priority(x[0], x[2])))
        
        # Process events chronologically
        for event_type, event_date, event in all_events:
            if event_type == 'transaction':
                # Apply transaction
                if event.type == 'BUY':
                    buy_quantity = float(event.quantity)
                    buy_price = float(event.price)
                    
                    # Track buy transactions for cost calculation
                    total_quantity_bought += buy_quantity
                    total_cost_bought += buy_quantity * buy_price
                    final_quantity += buy_quantity
                    
                    _trace(
                        "  %s: BUY %s @ ₹%s → qty %s cost ₹%s",
                        event_date,
                        event.quantity,
                        event.price,
                        total_quantity_bought,
                        total_cost_bought,
                    )
                    
                elif event.type == 'SELL':
                    sell_quantity = float(event.quantity)
                    
                    # Only reduce quantity, don't touch cost
                    final_quantity -= sell_quantity
                    
                    _trace(
                        "  %s: SELL %s @ ₹%s → Final Qty: %s",
                        event_date,
                        event.quantity,
                        event.price,
                        final_quantity,
                    )
                    
            elif event_type == 'corporate_action':
                # Apply corporate action
                if event.action_type == 'SPLIT':
                    old_quantity = final_quantity
                    split_ratio = float(event.ratio)
                    final_quantity *= split_ratio
                    # FIX: total_quantity_bought must also be adjusted by the same split ratio
                    # This ensures the final average price calculation is correct (cost basis / adjusted quantity)
                    total_quantity_bought *= split_ratio
                    _trace(
                        "  %s: SPLIT %s:1 → %s → %s",
                        event_date,
                        split_ratio,
                        old_quantity,
                        final_quantity,
                    )
                elif event.action_type == 'BONUS':
                    old_quantity = final_quantity
                    bonus_ratio = float(event.ratio)
                    # FIX: Use final_quantity (current holdings) not total_quantity_bought
                    # Bonus is based on shares currently held, not total shares ever bought
                    # This ensures SELL transactions are properly accounted for before bonus calculation
                    bonus_shares = math.floor(final_quantity * bonus_ratio)  # Floor to previous integer
                    final_quantity += bonus_shares
                    # FIX: total_quantity_bought must be updated to include bonus shares for correct average price calculation
                    # Note: Bonus shares have zero cost, so total_cost_bought remains unchanged
                    total_quantity_bought += bonus_shares
                    _trace(
                        "  %s: BONUS %s:1 → %s → %s (+%s shares)",
                        event_date,
                        bonus_ratio,
                        old_quantity,
                        final_quantity,
                        bonus_shares,
                    )
                elif event.action_type == 'MERGER':
                    source_security_id = getattr(event, 'source_security_id', None)
                    event_target_security_id = getattr(event, 'security_id', None)
                    
                    # Convert to int for reliable comparison (handle None, SQLAlchemy types, etc.)
                    source_security_id_int = int(source_security_id) if source_security_id is not None else None
                    event_target_security_id_int = int(event_target_security_id) if event_target_security_id is not None else None
                    security_id_int = int(security_id) if security_id is not None else None
                    
                    # Log merger details for debugging
                    _trace(f"  {event_date}: MERGER event - security_id={security_id_int} (type: {type(security_id)}), "
                               f"event.security_id={event_target_security_id_int} (type: {type(event_target_security_id)}), "
                               f"source_security_id={source_security_id_int} (type: {type(source_security_id)}), ratio={event.ratio}")
                    
                    # Check if this security is the SOURCE of the merger (being merged away)
                    if source_security_id_int is not None and source_security_id_int == security_id_int:
                        # This security is being merged INTO another security
                        # Deduct the current quantity (all shares are merged away)
                        # final_quantity at this point already contains the quantity before the merger
                        old_quantity = final_quantity
                        source_qty = final_quantity  # Quantity to be deducted
                        
                        # Deduct the source quantity (all shares are merged away)
                        final_quantity = 0.0  # All shares merged, so quantity becomes 0
                        
                        # Adjust cost basis: remove source cost proportionally
                        # Calculate cost per share
                        if total_quantity_bought > 0:
                            cost_per_share = total_cost_bought / total_quantity_bought
                            source_cost = source_qty * cost_per_share
                        else:
                            source_cost = 0.0
                        
                        total_cost_bought -= source_cost
                        total_quantity_bought -= source_qty
                        
                        _trace(f"  {event_date}: MERGER (SOURCE) → {source_qty:.2f} shares of source "
                                   f"deducted from holdings (merged into target security {event_target_security_id_int}), final_quantity now: {final_quantity:.2f}")
                    
                    # Check if this security is the TARGET of the merger (receiving merged shares)
                    elif event_target_security_id_int is not None and event_target_security_id_int == security_id_int and source_security_id_int is not None:
                        # This security is receiving merged shares from source_security_id
                        # Get the source security quantity BEFORE the merger is applied
                        # Calculate up to the day before merger to get quantity before conversion
                        try:
                            from datetime import timedelta
                            source_calc_date = event_date - timedelta(days=1)
                            source_result = get_holding_quantity_by_date(client_id, source_security_id_int, source_calc_date, db_session)
                            source_qty = source_result.get('quantity', 0.0)
                            
                            _trace(f"  {event_date}: MERGER (TARGET) → Getting source quantity as of {source_calc_date} (day before merger): {source_qty:.2f}")
                            
                            if source_qty > 0:
                                # Ratio = shares of target per share of source
                                converted_qty = math.floor(source_qty * float(event.ratio))  # Floor to previous integer
                                
                                old_quantity = final_quantity
                                final_quantity += converted_qty
                                
                                # Adjust cost basis: add source cost to target
                                source_cost = source_result.get('total_cost', 0.0)
                                total_cost_bought += source_cost
                                total_quantity_bought += converted_qty
                                
                                _trace(f"  {event_date}: MERGER (TARGET) → {source_qty:.2f} shares of source (ID: {source_security_id_int}) "
                                           f"converted to {converted_qty:.2f} shares of target (ratio: {event.ratio}), final_quantity now: {final_quantity:.2f}")
                            else:
                                # Expected when client did not hold the source before merger — not a UI warning.
                                _trace(
                                    "  %s: MERGER (TARGET) → Source security %s has 0 quantity, no conversion",
                                    event_date,
                                    source_security_id_int,
                                )
                        except Exception as merge_error:
                            logger.error(f"  {event_date}: MERGER (TARGET) → Error getting source quantity: {str(merge_error)}", exc_info=True)
                            # Don't update quantity if we can't get source quantity
                    
                    else:
                        # Merger event doesn't match this security - shouldn't happen but log it
                        logger.warning(f"  {event_date}: MERGER event doesn't match security {security_id_int} "
                                     f"(event.security_id={event_target_security_id_int}, source_security_id={source_security_id_int}) - skipping")
                
                elif event.action_type == 'DEMERGER':
                    # DEMERGER: security_id is demerged FROM source_security_id
                    # Ratio = shares of demerged entity per share of original
                    # Example: ratio = 0.1 means 10 shares of original = 1 share of demerged
                    event_target_security_id = getattr(event, 'security_id', None)
                    source_security_id = getattr(event, 'source_security_id', None)
                    
                    # Convert to int for reliable comparison
                    event_target_security_id_int = int(event_target_security_id) if event_target_security_id is not None else None
                    security_id_int = int(security_id) if security_id is not None else None
                    
                    _trace(f" Processing DEMERGER event on {event_date}: "
                               f"security_id={security_id_int} (calculating for), "
                               f"event.security_id={event_target_security_id_int} (demerged entity), "
                               f"source_security_id={source_security_id} (original), ratio={event.ratio}")
                    
                    # Check if this security is the TARGET (demerged entity)
                    if event_target_security_id_int is not None and event_target_security_id_int == security_id_int:
                        _trace(f" DEMERGER matches - this security {security_id_int} is the demerged entity (TARGET)")
                        if source_security_id:
                            source_security_id_int = int(source_security_id)
                            # Get the original security quantity (use day before to avoid recursion issues)
                            from datetime import timedelta
                            source_calc_date = event_date - timedelta(days=1)
                            _trace(f" Getting source security {source_security_id_int} quantity as of {source_calc_date} (day before demerger)")
                            
                            source_result = get_holding_quantity_by_date(
                                client_id, source_security_id_int, source_calc_date, db_session
                            )
                            source_qty = source_result.get('quantity', 0.0)
                            
                            _trace(f" Source security {source_security_id_int} has {source_qty:.2f} shares as of {source_calc_date}")
                            
                            # Ratio = shares of demerged entity per share of original
                            demerged_qty = math.floor(source_qty * float(event.ratio))  # Floor to previous integer
                            
                            old_quantity = final_quantity
                            final_quantity += demerged_qty
                            
                            # ✅ CORRECT: Calculate cost basis based on price difference (pre-demerger - post-demerger)
                            # This is the standard accounting treatment for demergers
                            from services.price_service import PriceService
                            
                            # Get RELIANCE price BEFORE demerger (day before)
                            pre_demerger_date = source_calc_date  # Already calculated as event_date - 1 day
                            pre_price_data = PriceService.get_price(
                                source_security_id_int,
                                pre_demerger_date,
                                use_adjusted=False,  # Use unadjusted price for accurate comparison
                                allow_fallback=True
                            )
                            pre_price = pre_price_data.price if pre_price_data and pre_price_data.is_valid else 0.0
                            
                            # Get RELIANCE price AFTER demerger (on demerger date or day after)
                            post_demerger_date = event_date
                            post_price_data = PriceService.get_price(
                                source_security_id_int,
                                post_demerger_date,
                                use_adjusted=False,
                                allow_fallback=True
                            )
                            # If price not available on demerger date, try day after
                            if not post_price_data or not post_price_data.is_valid:
                                post_price_data = PriceService.get_price(
                                    source_security_id_int,
                                    post_demerger_date + timedelta(days=1),
                                    use_adjusted=False,
                                    allow_fallback=True
                                )
                            post_price = post_price_data.price if post_price_data and post_price_data.is_valid else 0.0
                            
                            # Calculate price difference (value demerged per share)
                            price_difference = pre_price - post_price
                            
                            if price_difference > 0 and pre_price > 0:
                                # Cost basis per demerged share = price difference
                                cost_per_demerged_share = price_difference
                                
                                # Total cost for demerged entity
                                demerged_cost = demerged_qty * cost_per_demerged_share
                                
                                _trace(f"  {event_date}: DEMERGER (TARGET) cost allocation - "
                                           f"Source (ID: {source_security_id_int}) pre: ₹{pre_price:.2f}, post: ₹{post_price:.2f}, "
                                           f"difference: ₹{price_difference:.2f}, "
                                           f"Demerged qty: {demerged_qty}, "
                                           f"Total demerged cost: ₹{demerged_cost:.2f}")
                            else:
                                # Fallback: if prices unavailable, log warning and use minimal cost (0)
                                logger.warning(f"  {event_date}: Could not determine price difference for demerger. "
                                             f"Source (ID: {source_security_id_int}) Pre: ₹{pre_price:.2f}, Post: ₹{post_price:.2f}. "
                                             f"Using fallback: demerged_cost = 0")
                                demerged_cost = 0.0
                            
                            total_cost_bought += demerged_cost
                            total_quantity_bought += demerged_qty
                            
                            _trace(f"  {event_date}: DEMERGER (TARGET) → {source_qty:.2f} shares of original (ID: {source_security_id_int}) "
                                       f"demerged to {demerged_qty:.2f} shares of demerged entity "
                                       f"(ratio: {event.ratio}), final_quantity: {old_quantity:.2f} → {final_quantity:.2f}")
                        else:
                            logger.warning(f"  {event_date}: DEMERGER event has no source_security_id - skipping")
                    # ✅ NEW: Handle when processing the SOURCE security (RELIANCE) - reduce its cost basis
                    elif source_security_id_int is not None and source_security_id_int == security_id_int:
                        _trace(f" DEMERGER matches - this security {security_id_int} is the SOURCE (RELIANCE)")
                        # Calculate the cost that was allocated to the demerged entity
                        from services.price_service import PriceService
                        
                        # Get RELIANCE price BEFORE demerger (day before)
                        pre_demerger_date = event_date - timedelta(days=1)
                        pre_price_data = PriceService.get_price(
                            security_id_int,
                            pre_demerger_date,
                            use_adjusted=False,
                            allow_fallback=True
                        )
                        pre_price = pre_price_data.price if pre_price_data and pre_price_data.is_valid else 0.0
                        
                        # Get RELIANCE price AFTER demerger (on demerger date or day after)
                        post_demerger_date = event_date
                        post_price_data = PriceService.get_price(
                            security_id_int,
                            post_demerger_date,
                            use_adjusted=False,
                            allow_fallback=True
                        )
                        if not post_price_data or not post_price_data.is_valid:
                            post_price_data = PriceService.get_price(
                                security_id_int,
                                post_demerger_date + timedelta(days=1),
                                use_adjusted=False,
                                allow_fallback=True
                            )
                        post_price = post_price_data.price if post_price_data and post_price_data.is_valid else 0.0
                        
                        # Calculate price difference (value demerged per share)
                        price_difference = pre_price - post_price
                        
                        if price_difference > 0 and pre_price > 0:
                            # Get quantity held at the time of demerger
                            # Use final_quantity at this point (before demerger cost reduction)
                            if total_quantity_bought > 0 and final_quantity > 0:
                                # Calculate demerged quantity based on ratio
                                demerged_qty = math.floor(final_quantity * float(event.ratio))
                                
                                # Cost allocated to demerged entity = demerged_qty * price_difference
                                # This is the amount that should be removed from RELIANCE's cost basis
                                cost_allocated_to_demerged = demerged_qty * price_difference
                                
                                # Reduce RELIANCE's cost basis by the allocated amount
                                total_cost_bought -= cost_allocated_to_demerged
                                
                                _trace(f"  {event_date}: DEMERGER (SOURCE) → Reduced cost basis by ₹{cost_allocated_to_demerged:.2f} "
                                           f"(allocated to demerged entity). "
                                           f"Pre: ₹{pre_price:.2f}, Post: ₹{post_price:.2f}, "
                                           f"Difference: ₹{price_difference:.2f}, Demerged qty: {demerged_qty}")
                            else:
                                logger.warning(f"  {event_date}: DEMERGER (SOURCE) → Cannot calculate cost reduction: "
                                             f"total_quantity_bought={total_quantity_bought}, final_quantity={final_quantity}")
                        else:
                            logger.warning(f"  {event_date}: DEMERGER (SOURCE) → Could not determine price difference. "
                                         f"Pre: ₹{pre_price:.2f}, Post: ₹{post_price:.2f}. Skipping cost reduction.")
                    else:
                        logger.warning(f"  {event_date}: DEMERGER event doesn't match security {security_id_int} "
                                      f"(event.security_id={event_target_security_id_int}, source_security_id={source_security_id_int}) - skipping")
        
        # Calculate final cost and average price based on remaining quantity
        if total_quantity_bought > 0 and final_quantity > 0:
            # Scale the cost proportionally to remaining quantity
            final_cost = total_cost_bought * (final_quantity / total_quantity_bought)
            final_average_price = final_cost / final_quantity
        else:
            final_cost = 0.0
            final_average_price = 0.0
        
        result = {
            'quantity': final_quantity,
            'average_price': final_average_price,
            'total_cost': final_cost,
            'transactions_processed': len(transactions),
            'corporate_actions_applied': len(corporate_actions),
            'calculation_method': 'forward',
            'as_of_date': as_of_date
        }
        
        _trace(
            "Forward calculation result: %s shares @ ₹%s",
            final_quantity,
            final_average_price,
        )
        return result
        
    except Exception as e:
        logger.error(f"Error in forward calculation: {str(e)}")
        return {
            'quantity': 0.0,
            'average_price': 0.0,
            'total_cost': 0.0,
            'transactions_processed': 0,
            'corporate_actions_applied': 0,
            'calculation_method': 'forward',
            'as_of_date': as_of_date,
            'error': str(e)
        }


def _normalize_date(d) -> date:
    """Normalise a transaction_date / action_date to a plain date object."""
    if isinstance(d, str):
        return datetime.strptime(d, '%Y-%m-%d').date()
    if hasattr(d, 'date'):
        return d.date()
    return d


def get_client_portfolio_values_at_dates(
    client_id: int,
    dates: List[date],
) -> Dict[date, Dict[str, Any]]:
    """
    Efficiently compute the total portfolio VALUE at multiple dates in one pass.

    Instead of calling get_client_portfolio_by_date N times (each of which
    triggers a full transaction scan per security), this function:
      1. Fetches ALL client transactions once.
      2. Fetches ALL relevant corporate actions once (batched over all securities).
      3. Walks forward through events per security, snapshotting at each date.
      4. Batch-fetches historical prices over the requested date range.

    Designed for TWR calculation.  Only returns total_value per date —
    not the full holdings breakdown.

    Securities with MERGER / DEMERGER corporate actions fall back to the
    existing get_holding_quantity_by_date() so correctness is preserved.

    Args:
        client_id: Client ID
        dates: List of dates to compute portfolio value for

    Returns:
        {date: {'total_value': float}}
    """
    if not dates:
        return {}

    try:
        from models import Transaction, CorporateAction, Security as SecurityModel, HistoricalPrice
        from services.cashflow_service import DUMMY_DATES

        sorted_dates = sorted(set(dates))
        max_date = sorted_dates[-1]

        # ── 1. Fetch ALL client transactions once ──────────────────────────────
        all_txns = Transaction.query.filter(
            Transaction.client_id == client_id
        ).order_by(Transaction.transaction_date, Transaction.id).all()

        if not all_txns:
            return {d: {'total_value': 0.0} for d in sorted_dates}

        # ── 2. Compute client_first_valid_date once ────────────────────────────
        client_first_valid_date = None
        for txn in all_txns:
            txn_date = _normalize_date(txn.transaction_date)
            if txn_date not in DUMMY_DATES:
                if client_first_valid_date is None or txn_date < client_first_valid_date:
                    client_first_valid_date = txn_date

        if not client_first_valid_date:
            return {d: {'total_value': 0.0} for d in sorted_dates}

        # ── 3. Group transactions by security ──────────────────────────────────
        txns_by_security: Dict[int, list] = {}
        for txn in all_txns:
            txns_by_security.setdefault(txn.security_id, []).append(txn)

        security_ids = list(txns_by_security.keys())

        # ── 4. Fetch all corporate actions in TWO bulk queries ─────────────────
        all_cas = CorporateAction.query.filter(
            CorporateAction.security_id.in_(security_ids),
            CorporateAction.action_date > client_first_valid_date,
            CorporateAction.action_date <= max_date,
            CorporateAction.is_active == True
        ).order_by(CorporateAction.action_date).all()

        # Also merger actions where this security is the SOURCE
        source_cas = CorporateAction.query.filter(
            CorporateAction.source_security_id.in_(security_ids),
            CorporateAction.action_type == 'MERGER',
            CorporateAction.action_date > client_first_valid_date,
            CorporateAction.action_date <= max_date,
            CorporateAction.is_active == True
        ).order_by(CorporateAction.action_date).all()

        # Identify complex securities (MERGER / DEMERGER) — fall back for these
        complex_security_ids: set = set()
        for ca in all_cas + source_cas:
            if ca.action_type in ('MERGER', 'DEMERGER'):
                complex_security_ids.add(ca.security_id)
                if getattr(ca, 'source_security_id', None):
                    complex_security_ids.add(ca.source_security_id)

        # Group corporate actions by security_id
        cas_by_security: Dict[int, list] = {}
        for ca in all_cas:
            if ca.security_id not in complex_security_ids:
                cas_by_security.setdefault(ca.security_id, []).append(ca)

        # ── 5. Forward-walk per security (simple actions only) ────────────────
        quantity_snapshots: Dict[int, Dict[date, float]] = {}

        for security_id, sec_txns in txns_by_security.items():
            if security_id in complex_security_ids:
                quantity_snapshots[security_id] = None  # fallback marker
                continue

            sec_cas = cas_by_security.get(security_id, [])

            # Build + sort event list
            events = []
            for txn in sec_txns:
                events.append(('transaction', _normalize_date(txn.transaction_date), txn))
            for ca in sec_cas:
                events.append(('corporate_action', _normalize_date(ca.action_date), ca))

            def _event_sort_key(ev):
                ev_type, ev_date, ev_obj = ev
                if ev_type == 'transaction':
                    return (ev_date, 0 if ev_obj.type == 'SELL' else 1)
                return (ev_date, 2)

            events.sort(key=_event_sort_key)

            # Walk forward, snapshot at each requested date
            final_quantity = 0.0
            total_qty_bought = 0.0
            event_idx = 0
            snapshots: Dict[date, float] = {}

            for target_date in sorted_dates:
                # Consume all events up to and including target_date
                while event_idx < len(events) and events[event_idx][1] <= target_date:
                    ev_type, ev_date, ev_obj = events[event_idx]
                    if ev_type == 'transaction':
                        if ev_obj.type == 'BUY':
                            qty = float(ev_obj.quantity)
                            final_quantity += qty
                            total_qty_bought += qty
                        elif ev_obj.type == 'SELL':
                            final_quantity -= float(ev_obj.quantity)
                    elif ev_type == 'corporate_action':
                        if ev_obj.action_type == 'SPLIT':
                            ratio = float(ev_obj.ratio)
                            final_quantity *= ratio
                            total_qty_bought *= ratio
                        elif ev_obj.action_type == 'BONUS':
                            bonus = math.floor(final_quantity * float(ev_obj.ratio))
                            final_quantity += bonus
                            total_qty_bought += bonus
                    event_idx += 1

                snapshots[target_date] = max(0.0, final_quantity)

            quantity_snapshots[security_id] = snapshots

        # ── 6. Fallback: complex securities use existing per-date function ─────
        for security_id in complex_security_ids:
            if security_id not in txns_by_security:
                continue
            snaps: Dict[date, float] = {}
            for d in sorted_dates:
                result = get_holding_quantity_by_date(client_id, security_id, d)
                snaps[d] = result.get('quantity', 0.0)
            quantity_snapshots[security_id] = snaps

        # ── 7. Batch-fetch historical prices ──────────────────────────────────
        min_price_date = sorted_dates[0] - timedelta(days=3)
        price_records = HistoricalPrice.query.filter(
            HistoricalPrice.security_id.in_(security_ids),
            HistoricalPrice.date >= min_price_date,
            HistoricalPrice.date <= max_date
        ).all()

        price_map: Dict[int, Dict[date, float]] = {}
        for pr in price_records:
            pr_date = _normalize_date(pr.date)
            if pr.close_price:
                price_map.setdefault(pr.security_id, {})[pr_date] = float(pr.close_price)

        # Cache current prices as fallback
        current_price_map: Dict[int, float] = {}
        for sec in SecurityModel.query.filter(
            SecurityModel.id.in_(security_ids)
        ).all():
            if sec.current_price:
                current_price_map[sec.id] = float(sec.current_price)

        def _get_price(sec_id: int, as_of: date) -> float:
            sec_prices = price_map.get(sec_id, {})
            for days_back in range(4):
                check = as_of - timedelta(days=days_back)
                if check in sec_prices:
                    return sec_prices[check]
            return current_price_map.get(sec_id, 0.0)

        # ── 8. Compute total_value per date ───────────────────────────────────
        result: Dict[date, Dict[str, Any]] = {}
        for d in sorted_dates:
            total_value = 0.0
            for security_id, snaps in quantity_snapshots.items():
                if snaps is None:
                    continue
                qty = snaps.get(d, 0.0)
                if qty > 0:
                    total_value += qty * _get_price(security_id, d)
            result[d] = {'total_value': total_value}

        logger.info(
            f"get_client_portfolio_values_at_dates: client={client_id}, "
            f"dates={len(sorted_dates)}, securities={len(security_ids)}, "
            f"complex_fallback={len(complex_security_ids)}"
        )
        return result

    except Exception as e:
        logger.error(f"Error in get_client_portfolio_values_at_dates: {str(e)}", exc_info=True)
        return {d: {'total_value': 0.0} for d in dates}


def get_holding_quantity_by_date_with_steps(client_id: int, security_id: int, as_of_date: date, db_session=None) -> Dict[str, Any]:
    """
    Calculate holding quantity with detailed step-by-step log
    This is the same as get_holding_quantity_by_date but also returns detailed steps
    
    Args:
        client_id: Client ID
        security_id: Security ID  
        as_of_date: Date to calculate holdings for
        db_session: Optional database session to use for queries (for uncommitted transactions)
        
    Returns:
        {
            'quantity': float,
            'average_price': float,
            'total_cost': float,
            'transactions_processed': int,
            'corporate_actions_applied': int,
            'calculation_method': 'forward',
            'as_of_date': date,
            'steps': List[Dict]  # Detailed step-by-step log
        }
    """
    try:
        from models import Transaction, CorporateAction
        from services.cashflow_service import DUMMY_DATES
        
        # Use provided session or fall back to global query
        if db_session is not None:
            transactions = db_session.query(Transaction).filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.transaction_date <= as_of_date
            ).order_by(Transaction.transaction_date).all()
        else:
            transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.transaction_date <= as_of_date
            ).order_by(Transaction.transaction_date).all()
        
        _trace(
            "Forward calculation with steps: client=%s security=%s date=%s txns=%s",
            client_id,
            security_id,
            as_of_date,
            len(transactions),
        )
        
        # Find first valid transaction date at CLIENT level
        client_first_valid_date = None
        
        if db_session is not None:
            all_client_transactions = db_session.query(Transaction).filter(
                Transaction.client_id == client_id,
                Transaction.transaction_date <= as_of_date
            ).order_by(Transaction.transaction_date).all()
        else:
            all_client_transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.transaction_date <= as_of_date
            ).order_by(Transaction.transaction_date).all()
        
        for txn in all_client_transactions:
            if isinstance(txn.transaction_date, str):
                txn_date = datetime.strptime(txn.transaction_date, '%Y-%m-%d').date()
            elif hasattr(txn.transaction_date, 'date'):
                txn_date = txn.transaction_date.date()
            else:
                txn_date = txn.transaction_date
            
            if txn_date not in DUMMY_DATES:
                if client_first_valid_date is None or txn_date < client_first_valid_date:
                    client_first_valid_date = txn_date
        
        # Get corporate actions
        if client_first_valid_date:
            if db_session is not None:
                corporate_actions = db_session.query(CorporateAction).filter(
                    CorporateAction.security_id == security_id,
                    CorporateAction.action_date > client_first_valid_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
                
                source_merger_actions = db_session.query(CorporateAction).filter(
                    CorporateAction.source_security_id == security_id,
                    CorporateAction.action_type == 'MERGER',
                    CorporateAction.action_date > client_first_valid_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
            else:
                corporate_actions = CorporateAction.query.filter(
                    CorporateAction.security_id == security_id,
                    CorporateAction.action_date > client_first_valid_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
                
                source_merger_actions = CorporateAction.query.filter(
                    CorporateAction.source_security_id == security_id,
                    CorporateAction.action_type == 'MERGER',
                    CorporateAction.action_date > client_first_valid_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
            
            corporate_actions = list(corporate_actions) + list(source_merger_actions)
            
            # Log details of all corporate actions found (especially DEMERGER)
            _trace("Found %s corporate actions for security %s (with_steps)", len(corporate_actions), security_id)
            for ca in corporate_actions:
                source_id = getattr(ca, 'source_security_id', None)
                _trace(
                    "  CA %s %s on %s security_id=%s source=%s ratio=%s",
                    ca.id,
                    ca.action_type,
                    ca.action_date,
                    ca.security_id,
                    source_id,
                    ca.ratio,
                )
        else:
            corporate_actions = []
        
        _trace(
            "with_steps: %s transactions, %s corporate actions",
            len(transactions),
            len(corporate_actions),
        )
        
        # Forward calculation with step tracking
        total_quantity_bought = 0.0
        total_cost_bought = 0.0
        final_quantity = 0.0
        steps = []
        step_number = 0
        
        # Process all events chronologically
        all_events = []
        
        for txn in transactions:
            if isinstance(txn.transaction_date, str):
                txn_date = datetime.strptime(txn.transaction_date, '%Y-%m-%d').date()
            elif hasattr(txn.transaction_date, 'date'):
                txn_date = txn.transaction_date.date()
            else:
                txn_date = txn.transaction_date
            all_events.append(('transaction', txn_date, txn))
        
        for ca in corporate_actions:
            if isinstance(ca.action_date, str):
                ca_date = datetime.strptime(ca.action_date, '%Y-%m-%d').date()
            elif hasattr(ca.action_date, 'date'):
                ca_date = ca.action_date.date()
            else:
                ca_date = ca.action_date
            all_events.append(('corporate_action', ca_date, ca))
            # Log DEMERGER actions specifically
            if ca.action_type == 'DEMERGER':
                source_id = getattr(ca, 'source_security_id', None)
                _trace(f" Added DEMERGER event (with_steps) - CA ID {ca.id}, date {ca_date}, "
                           f"security_id={ca.security_id}, source_security_id={source_id}, "
                           f"ratio={ca.ratio}, calculating for security_id={security_id}")
        
        # Sort by date, then by event type priority
        # Priority: SELL transactions (0) -> Other transactions (1) -> Corporate actions (2)
        # This ensures SELL transactions are processed BEFORE corporate actions on the same date
        # This is critical: if a SELL and SPLIT/BONUS happen on the same date, SELL must happen first
        def get_event_priority(event_type, event):
            if event_type == 'transaction':
                if event.type == 'SELL':
                    return 0  # SELL must happen first
                else:
                    return 1  # BUY transactions
            else:
                return 2  # Corporate actions
        
        all_events.sort(key=lambda x: (x[1], get_event_priority(x[0], x[2])))
        
        # Process events chronologically
        for event_type, event_date, event in all_events:
            qty_before = final_quantity
            # Calculate cost before: proportional to quantity
            cost_before = total_cost_bought * (final_quantity / total_quantity_bought) if total_quantity_bought > 0 and final_quantity > 0 else 0.0
            avg_price_before = cost_before / qty_before if qty_before > 0 else 0.0
            
            if event_type == 'transaction':
                # Increment step number for transactions (always added)
                step_number += 1
                if event.type == 'BUY':
                    buy_quantity = float(event.quantity)
                    buy_price = float(event.price)
                    
                    total_quantity_bought += buy_quantity
                    total_cost_bought += buy_quantity * buy_price
                    final_quantity += buy_quantity
                    
                    logger.debug(f"  {event_date}: BUY {event.quantity} @ ₹{event.price} → Total Bought: {total_quantity_bought:.2f}, Total Cost: ₹{total_cost_bought:.2f}, Final Qty: {final_quantity:.2f}")
                    
                elif event.type == 'SELL':
                    sell_quantity = float(event.quantity)
                    final_quantity -= sell_quantity
                    
                    logger.debug(f"  {event_date}: SELL {event.quantity} @ ₹{event.price} → Final Qty: {final_quantity:.2f} (cost unchanged)")
                
                # Calculate values after transaction
                cost_after = total_cost_bought * (final_quantity / total_quantity_bought) if total_quantity_bought > 0 and final_quantity > 0 else 0.0
                avg_price_after = cost_after / final_quantity if final_quantity > 0 else 0.0
                
                steps.append({
                    'step_number': step_number,
                    'date': event_date.strftime('%Y-%m-%d'),
                    'event_type': 'transaction',
                    'event_description': f"{event.type.upper()} {event.quantity} shares @ ₹{event.price}",
                    'quantity_before': qty_before,
                    'quantity_after': final_quantity,
                    'average_price_before': avg_price_before,
                    'average_price_after': avg_price_after,
                    'total_cost_before': cost_before,
                    'total_cost_after': cost_after,
                    'details': {
                        'transaction_id': event.id,
                        'type': event.type
                    }
                })
                
            elif event_type == 'corporate_action':
                # Track if corporate action was actually applied (for step logging)
                ca_applied = False
                
                if event.action_type == 'SPLIT':
                    old_quantity = final_quantity
                    split_ratio = float(event.ratio)
                    final_quantity *= split_ratio
                    total_quantity_bought *= split_ratio
                    ca_applied = True
                    _trace(f"  {event_date}: SPLIT {split_ratio}:1 → {old_quantity:.2f} → {final_quantity:.2f}")
                    
                elif event.action_type == 'BONUS':
                    old_quantity = final_quantity
                    bonus_ratio = float(event.ratio)
                    # FIX: Use final_quantity (current holdings) not total_quantity_bought
                    # Bonus is based on shares currently held, not total shares ever bought
                    # This ensures SELL transactions are properly accounted for before bonus calculation
                    bonus_shares = math.floor(final_quantity * bonus_ratio)
                    final_quantity += bonus_shares
                    total_quantity_bought += bonus_shares
                    ca_applied = True
                    _trace(f"  {event_date}: BONUS {bonus_ratio}:1 → {old_quantity:.2f} → {final_quantity:.2f} (added {bonus_shares:.2f} bonus shares)")
                    
                elif event.action_type == 'MERGER':
                    source_security_id = getattr(event, 'source_security_id', None)
                    event_target_security_id = getattr(event, 'security_id', None)
                    
                    # Convert to int for reliable comparison (handle None, SQLAlchemy types, etc.)
                    source_security_id_int = int(source_security_id) if source_security_id is not None else None
                    event_target_security_id_int = int(event_target_security_id) if event_target_security_id is not None else None
                    security_id_int = int(security_id) if security_id is not None else None
                    
                    # Log merger details for debugging
                    _trace(f"  {event_date}: MERGER event - security_id={security_id_int} (type: {type(security_id)}), "
                               f"event.security_id={event_target_security_id_int} (type: {type(event_target_security_id)}), "
                               f"source_security_id={source_security_id_int} (type: {type(source_security_id)}), ratio={event.ratio}")
                    
                    # Check if this security is the SOURCE of the merger (being merged away)
                    if source_security_id_int is not None and source_security_id_int == security_id_int:
                        # This security is being merged INTO another security (SOURCE)
                        old_quantity = final_quantity
                        source_qty = final_quantity
                        
                        final_quantity = 0.0
                        
                        if total_quantity_bought > 0:
                            cost_per_share = total_cost_bought / total_quantity_bought
                            source_cost = source_qty * cost_per_share
                        else:
                            source_cost = 0.0
                        
                        total_cost_bought -= source_cost
                        total_quantity_bought -= source_qty
                        ca_applied = True
                        
                        _trace(f"  {event_date}: MERGER (SOURCE) → {source_qty:.2f} shares merged into target security {event_target_security_id_int}, final_quantity now: {final_quantity:.2f}")
                    
                    # Check if this security is the TARGET of the merger (receiving merged shares)
                    elif event_target_security_id_int is not None and event_target_security_id_int == security_id_int and source_security_id_int is not None:
                        # This security is receiving merged shares (TARGET)
                        # Get the source security quantity BEFORE the merger is applied
                        # Calculate up to the day before merger to get quantity before conversion
                        try:
                            from datetime import timedelta
                            source_calc_date = event_date - timedelta(days=1)
                            source_result = get_holding_quantity_by_date(client_id, source_security_id_int, source_calc_date, db_session)
                            source_qty = source_result.get('quantity', 0.0)
                            
                            _trace(f"  {event_date}: MERGER (TARGET) → Getting source quantity as of {source_calc_date} (day before merger): {source_qty:.2f}")
                            
                            if source_qty > 0:
                                converted_qty = math.floor(source_qty * float(event.ratio))
                                
                                old_quantity = final_quantity
                                final_quantity += converted_qty
                                
                                source_cost = source_result.get('total_cost', 0.0)
                                total_cost_bought += source_cost
                                total_quantity_bought += converted_qty
                                ca_applied = True
                                
                                _trace(f"  {event_date}: MERGER (TARGET) → {source_qty:.2f} shares of source (ID: {source_security_id_int}) converted to {converted_qty:.2f} shares of target, final_quantity now: {final_quantity:.2f}")
                            else:
                                _trace(
                                    "  %s: MERGER (TARGET) → Source security %s has 0 quantity, no conversion",
                                    event_date,
                                    source_security_id_int,
                                )
                        except Exception as merge_error:
                            logger.error(f"  {event_date}: MERGER (TARGET) → Error getting source quantity: {str(merge_error)}", exc_info=True)
                            # Don't update quantity if we can't get source quantity
                    
                    else:
                        # Merger event doesn't match this security - shouldn't happen but log it
                        logger.warning(f"  {event_date}: MERGER event doesn't match security {security_id_int} "
                                     f"(event.security_id={event_target_security_id_int}, source_security_id={source_security_id_int}) - skipping")
                
                elif event.action_type == 'DEMERGER':
                    event_target_security_id = getattr(event, 'security_id', None)
                    source_security_id = getattr(event, 'source_security_id', None)
                    
                    # Convert to int for reliable comparison
                    event_target_security_id_int = int(event_target_security_id) if event_target_security_id is not None else None
                    security_id_int = int(security_id) if security_id is not None else None
                    
                    _trace(f" Processing DEMERGER event on {event_date}: "
                               f"security_id={security_id_int} (calculating for), "
                               f"event.security_id={event_target_security_id_int} (demerged entity), "
                               f"source_security_id={source_security_id} (original), ratio={event.ratio}")
                    
                    # Check if this security is the TARGET (demerged entity)
                    if event_target_security_id_int is not None and event_target_security_id_int == security_id_int:
                        _trace(f" DEMERGER matches - this security {security_id_int} is the demerged entity (TARGET)")
                        if source_security_id:
                            source_security_id_int = int(source_security_id)
                            # Use the base function (without steps) to avoid infinite recursion
                            # Get source quantity as of day before demerger
                            from datetime import timedelta
                            source_calc_date = event_date - timedelta(days=1)
                            _trace(f" Getting source security {source_security_id_int} quantity as of {source_calc_date} (day before demerger)")
                            
                            source_result = get_holding_quantity_by_date(
                                client_id, source_security_id_int, source_calc_date, db_session
                            )
                            source_qty = source_result.get('quantity', 0.0)
                            
                            _trace(f" Source security {source_security_id_int} has {source_qty:.2f} shares as of {source_calc_date}")
                            
                            demerged_qty = math.floor(source_qty * float(event.ratio))
                            
                            old_quantity = final_quantity
                            final_quantity += demerged_qty
                            
                            # ✅ CORRECT: Calculate cost basis based on price difference (pre-demerger - post-demerger)
                            # This is the standard accounting treatment for demergers
                            from services.price_service import PriceService
                            
                            # Get RELIANCE price BEFORE demerger (day before)
                            pre_demerger_date = source_calc_date  # Already calculated as event_date - 1 day
                            pre_price_data = PriceService.get_price(
                                source_security_id_int,
                                pre_demerger_date,
                                use_adjusted=False,  # Use unadjusted price for accurate comparison
                                allow_fallback=True
                            )
                            pre_price = pre_price_data.price if pre_price_data and pre_price_data.is_valid else 0.0
                            
                            # Get RELIANCE price AFTER demerger (on demerger date or day after)
                            post_demerger_date = event_date
                            post_price_data = PriceService.get_price(
                                source_security_id_int,
                                post_demerger_date,
                                use_adjusted=False,
                                allow_fallback=True
                            )
                            # If price not available on demerger date, try day after
                            if not post_price_data or not post_price_data.is_valid:
                                post_price_data = PriceService.get_price(
                                    source_security_id_int,
                                    post_demerger_date + timedelta(days=1),
                                    use_adjusted=False,
                                    allow_fallback=True
                                )
                            post_price = post_price_data.price if post_price_data and post_price_data.is_valid else 0.0
                            
                            # Calculate price difference (value demerged per share)
                            price_difference = pre_price - post_price
                            
                            if price_difference > 0 and pre_price > 0:
                                # Cost basis per demerged share = price difference
                                cost_per_demerged_share = price_difference
                                
                                # Total cost for demerged entity
                                demerged_cost = demerged_qty * cost_per_demerged_share
                                
                                _trace(f"  {event_date}: DEMERGER (TARGET) cost allocation - "
                                           f"Source (ID: {source_security_id_int}) pre: ₹{pre_price:.2f}, post: ₹{post_price:.2f}, "
                                           f"difference: ₹{price_difference:.2f}, "
                                           f"Demerged qty: {demerged_qty}, "
                                           f"Total demerged cost: ₹{demerged_cost:.2f}")
                            else:
                                # Fallback: if prices unavailable, log warning and use minimal cost (0)
                                logger.warning(f"  {event_date}: Could not determine price difference for demerger. "
                                             f"Source (ID: {source_security_id_int}) Pre: ₹{pre_price:.2f}, Post: ₹{post_price:.2f}. "
                                             f"Using fallback: demerged_cost = 0")
                                demerged_cost = 0.0
                            
                            total_cost_bought += demerged_cost
                            total_quantity_bought += demerged_qty
                            ca_applied = True
                            
                            _trace(f"  {event_date}: DEMERGER (TARGET) → {source_qty:.2f} shares of original (ID: {source_security_id_int}) "
                                       f"demerged to {demerged_qty:.2f} shares, final_quantity: {old_quantity:.2f} → {final_quantity:.2f}")
                        else:
                            logger.warning(f"  {event_date}: DEMERGER event has no source_security_id - skipping")
                            ca_applied = False
                    # ✅ NEW: Handle when processing the SOURCE security (RELIANCE) - reduce its cost basis
                    elif source_security_id_int is not None and source_security_id_int == security_id_int:
                        _trace(f" DEMERGER matches - this security {security_id_int} is the SOURCE (RELIANCE)")
                        # Calculate the cost that was allocated to the demerged entity
                        from services.price_service import PriceService
                        
                        # Get RELIANCE price BEFORE demerger (day before)
                        pre_demerger_date = event_date - timedelta(days=1)
                        pre_price_data = PriceService.get_price(
                            security_id_int,
                            pre_demerger_date,
                            use_adjusted=False,
                            allow_fallback=True
                        )
                        pre_price = pre_price_data.price if pre_price_data and pre_price_data.is_valid else 0.0
                        
                        # Get RELIANCE price AFTER demerger (on demerger date or day after)
                        post_demerger_date = event_date
                        post_price_data = PriceService.get_price(
                            security_id_int,
                            post_demerger_date,
                            use_adjusted=False,
                            allow_fallback=True
                        )
                        if not post_price_data or not post_price_data.is_valid:
                            post_price_data = PriceService.get_price(
                                security_id_int,
                                post_demerger_date + timedelta(days=1),
                                use_adjusted=False,
                                allow_fallback=True
                            )
                        post_price = post_price_data.price if post_price_data and post_price_data.is_valid else 0.0
                        
                        # Calculate price difference (value demerged per share)
                        price_difference = pre_price - post_price
                        
                        if price_difference > 0 and pre_price > 0:
                            # Get quantity held at the time of demerger
                            # Use final_quantity at this point (before demerger cost reduction)
                            if total_quantity_bought > 0 and final_quantity > 0:
                                # Calculate demerged quantity based on ratio
                                demerged_qty = math.floor(final_quantity * float(event.ratio))
                                
                                # Cost allocated to demerged entity = demerged_qty * price_difference
                                cost_allocated_to_demerged = demerged_qty * price_difference
                                
                                # Reduce RELIANCE's cost basis by the allocated amount
                                total_cost_bought -= cost_allocated_to_demerged
                                ca_applied = True
                                
                                _trace(f"  {event_date}: DEMERGER (SOURCE) → Reduced cost basis by ₹{cost_allocated_to_demerged:.2f} "
                                           f"(allocated to demerged entity). "
                                           f"Pre: ₹{pre_price:.2f}, Post: ₹{post_price:.2f}, "
                                           f"Difference: ₹{price_difference:.2f}, Demerged qty: {demerged_qty}")
                            else:
                                logger.warning(f"  {event_date}: DEMERGER (SOURCE) → Cannot calculate cost reduction: "
                                             f"total_quantity_bought={total_quantity_bought}, final_quantity={final_quantity}")
                                ca_applied = False
                        else:
                            logger.warning(f"  {event_date}: DEMERGER (SOURCE) → Could not determine price difference. "
                                         f"Pre: ₹{pre_price:.2f}, Post: ₹{post_price:.2f}. Skipping cost reduction.")
                            ca_applied = False
                    else:
                        logger.warning(f"  {event_date}: DEMERGER event doesn't match security {security_id_int} "
                                      f"(event.security_id={event_target_security_id_int}, source_security_id={source_security_id_int}) - skipping")
                        ca_applied = False
                        ca_applied = False
                
                # Only add step if corporate action was actually applied
                if ca_applied:
                    # Increment step number only when we actually add a step
                    step_number += 1
                    
                    # Calculate values after corporate action
                    cost_after = total_cost_bought * (final_quantity / total_quantity_bought) if total_quantity_bought > 0 and final_quantity > 0 else 0.0
                    avg_price_after = cost_after / final_quantity if final_quantity > 0 else 0.0
                    
                    steps.append({
                        'step_number': step_number,
                        'date': event_date.strftime('%Y-%m-%d'),
                        'event_type': 'corporate_action',
                        'event_description': f"{event.action_type} - {event.description or ''}",
                        'quantity_before': qty_before,
                        'quantity_after': final_quantity,
                        'average_price_before': avg_price_before,
                        'average_price_after': avg_price_after,
                        'total_cost_before': cost_before,
                        'total_cost_after': cost_after,
                        'details': {
                            'action_type': event.action_type,
                            'action_id': event.id,
                            'ratio': float(event.ratio) if event.ratio else None,
                            'source_security_id': getattr(event, 'source_security_id', None)
                        }
                    })
        
        # Calculate final cost and average price
        if total_quantity_bought > 0 and final_quantity > 0:
            final_cost = total_cost_bought * (final_quantity / total_quantity_bought)
            final_average_price = final_cost / final_quantity
        else:
            final_cost = 0.0
            final_average_price = 0.0
        
        result = {
            'quantity': final_quantity,
            'average_price': final_average_price,
            'total_cost': final_cost,
            'transactions_processed': len(transactions),
            'corporate_actions_applied': len(corporate_actions),
            'calculation_method': 'forward',
            'as_of_date': as_of_date,
            'steps': steps
        }
        
        _trace(
            "Forward calculation with steps result: %s shares @ ₹%s (%s steps)",
            final_quantity,
            final_average_price,
            len(steps),
        )
        return result
        
    except Exception as e:
        logger.error(f"Error in forward calculation with steps: {str(e)}")
        return {
            'quantity': 0.0,
            'average_price': 0.0,
            'total_cost': 0.0,
            'transactions_processed': 0,
            'corporate_actions_applied': 0,
            'calculation_method': 'forward',
            'as_of_date': as_of_date,
            'steps': [],
            'error': str(e)
        }


def audit_holdings_accuracy(client_id: int, as_of_date: date) -> List[Dict[str, Any]]:
    """
    Audit holdings table against forward calculation
    
    Args:
        client_id: Client ID
        as_of_date: Date to audit holdings for
        
    Returns:
        List of discrepancies found
    """
    try:
        from models import Holding
        
        discrepancies = []
        
        # Get all holdings for the client
        holdings = Holding.query.filter_by(client_id=client_id).all()
        
        logger.info(f"Auditing {len(holdings)} holdings for client {client_id} as of {as_of_date}")
        
        for holding in holdings:
            forward_result = get_holding_quantity_by_date(
                client_id, holding.security_id, as_of_date
            )
            
            holdings_qty = float(holding.quantity)
            forward_qty = forward_result['quantity']
            difference = forward_qty - holdings_qty
            
            if abs(difference) > 0.01:  # Tolerance of 0.01
                discrepancies.append({
                    'security_id': holding.security_id,
                    'security_symbol': holding.security.symbol if holding.security else 'Unknown',
                    'holdings_table_qty': holdings_qty,
                    'forward_calc_qty': forward_qty,
                    'difference': difference,
                    'holdings_table_price': float(holding.average_price),
                    'forward_calc_price': forward_result['average_price']
                })
                logger.warning(f"Discrepancy found: {holding.security.symbol if holding.security else 'Unknown'} - "
                             f"Holdings: {holdings_qty:.2f}, Forward: {forward_qty:.2f}, Diff: {difference:.2f}")
        
        logger.info(f"Audit complete: {len(discrepancies)} discrepancies found out of {len(holdings)} holdings")
        return discrepancies
        
    except Exception as e:
        logger.error(f"Error in holdings audit: {str(e)}")
        return []


def get_client_portfolio_by_date(client_id: int, as_of_date: date, db_session=None) -> Dict[str, Any]:
    """
    Calculate entire client portfolio using forward calculation
    
    Args:
        client_id: Client ID
        as_of_date: Date to calculate portfolio for
        db_session: Optional database session to use for queries (for uncommitted transactions)
        
    Returns:
        Portfolio summary with all holdings. Pricing uses ``HistoricalPrice.close`` on
        ``as_of_date`` or the three prior calendar days. If no bar is found, ``current_price``
        and ``current_value`` are 0, ``logger.error`` records a data-gap (we do not substitute
        ``Security.current_price`` for dated valuations).
    """
    # Per-request cache: multiple analytics functions call this with the same
    # (client_id, as_of_date) within a single HTTP request. The cache is scoped
    # to the Flask request via `g` so it is always fresh across requests.
    # Only cache when db_session is None (standard path); custom sessions may
    # include uncommitted data that must not be shared.
    _cache_key = None
    if db_session is None:
        try:
            from flask import g
            _cache_key = f'_pcache_{client_id}_{as_of_date}'
            if not hasattr(g, '_portfolio_cache'):
                g._portfolio_cache = {}
            if _cache_key in g._portfolio_cache:
                _trace("Portfolio cache HIT: client=%s, date=%s", client_id, as_of_date)
                return g._portfolio_cache[_cache_key]
        except RuntimeError:
            # Outside request context (scripts, tests) — skip cache silently
            _cache_key = None

    try:
        from models import Transaction
        
        # Get all securities this client has ever traded
        # Filter out dummy dates to find securities with real transactions
        from services.cashflow_service import DUMMY_DATES
        
        # Use provided session or fall back to global query
        if db_session is not None:
            # Use session.query() for uncommitted transaction visibility
            securities_traded = db_session.query(Transaction).filter(
                Transaction.client_id == client_id,
                Transaction.transaction_date <= as_of_date
            ).with_entities(Transaction.security_id).distinct().all()
        else:
            # Use global query for committed transactions
            securities_traded = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.transaction_date <= as_of_date
            ).with_entities(Transaction.security_id).distinct().all()
        
        security_ids = [int(s[0]) for s in securities_traded]
        _trace(
            "Portfolio calculation for client %s: %s securities",
            client_id,
            len(security_ids),
        )

        # P5: batch-fetch historical prices (one query vs up to 4 per security)
        from models import Security, HistoricalPrice

        price_map = {}
        if security_ids:
            min_price_date = as_of_date - timedelta(days=3)
            price_records = HistoricalPrice.query.filter(
                HistoricalPrice.security_id.in_(security_ids),
                HistoricalPrice.date >= min_price_date,
                HistoricalPrice.date <= as_of_date,
            ).all()
            for pr in price_records:
                pr_date = _normalize_date(pr.date)
                if pr.close_price:
                    price_map.setdefault(pr.security_id, {})[pr_date] = float(pr.close_price)

        def _historical_price_for(security_id: int) -> Optional[float]:
            sec_prices = price_map.get(security_id, {})
            for days_back in range(4):
                lookback_date = as_of_date - timedelta(days=days_back)
                if lookback_date in sec_prices:
                    return sec_prices[lookback_date]
            return None

        portfolio = {
            'client_id': client_id,
            'as_of_date': as_of_date,
            'holdings': [],
            'total_value': 0.0,
            'total_cost': 0.0,
            'calculation_method': 'forward'
        }
        
        for security_id in security_ids:
            result = get_holding_quantity_by_date(client_id, security_id, as_of_date, db_session)
            
            if result['quantity'] > 0:
                security = Security.query.get(security_id)
                historical_price = _historical_price_for(security_id)
                if historical_price is not None:
                    _trace(
                        "Historical price %s: ₹%s on/before %s",
                        security.symbol if security else security_id,
                        historical_price,
                        as_of_date,
                    )
                
                if not historical_price:
                    sym = security.symbol if security else str(security_id)
                    logger.error(
                        "DATA GAP (correct this): no HistoricalPrice.close within 3 calendar days on/before %s "
                        "for security_id=%s symbol=%s client_id=%s — not using Security.current_price (invalid for past dates). "
                        "Holding value treated as 0 until prices are loaded.",
                        as_of_date.isoformat(),
                        security_id,
                        sym,
                        client_id,
                    )
                    as_of_price = 0.0
                    price_source = "missing_historical"
                else:
                    as_of_price = historical_price
                    price_source = "historical"

                current_value = result["quantity"] * as_of_price
                
                # Get asset class and sector information - use Security.asset_class.name directly (no overwriting)
                asset_class_name = 'Equity'  # Default fallback
                sector = 'Unknown'
                
                if security and security.asset_class:
                    asset_class_name = security.asset_class.name  # Use actual asset class from DB (Debt/REIT/etc, not overwritten to "ETF")
                if security and security.meta_data:
                    try:
                        import json
                        meta = json.loads(security.meta_data)
                        sector = meta.get('sector', 'Unknown')
                    except:
                        pass
                
                holding = {
                    'security_id': security_id,
                    'symbol': security.symbol if security else 'Unknown',
                    'name': security.name if security else 'Unknown',
                    'quantity': result['quantity'],
                    'average_price': result['average_price'],
                    'current_price': as_of_price,
                    'current_value': current_value,
                    'total_cost': result['total_cost'],
                    'unrealized_pnl': current_value - result['total_cost'],
                    'asset_class': asset_class_name,  # Now uses real asset class from Security table
                    'sector': sector,
                    'price_source': price_source,
                    'price_missing': price_source == 'missing_historical',
                }
                
                portfolio['holdings'].append(holding)
                portfolio['total_value'] += current_value
                portfolio['total_cost'] += result['total_cost']
        
        portfolio['unrealized_pnl'] = portfolio['total_value'] - portfolio['total_cost']
        portfolio['unrealized_pnl_percent'] = (portfolio['unrealized_pnl'] / portfolio['total_cost'] * 100) if portfolio['total_cost'] > 0 else 0.0
        
        # Data gaps: missing historical prices (no longer substituted with current_price)
        warnings = []
        for holding in portfolio['holdings']:
            if holding.get('price_source') == 'missing_historical':
                symbol = holding.get('symbol', 'Unknown')
                warnings.append({
                    'type': 'missing_historical_price',
                    'severity': 'error',
                    'message': (
                        f"Add HistoricalPrice for {symbol} (security_id={holding.get('security_id')}) "
                        f"on or before {as_of_date.isoformat()}; value shown as 0 (current_price not used for this date)."
                    ),
                    'security_id': holding.get('security_id'),
                    'symbol': symbol,
                })
        
        if warnings:
            portfolio['warnings'] = warnings
        
        # Sort holdings by symbol
        portfolio['holdings'].sort(key=lambda x: x.get('symbol', '').upper())
        
        _trace(
            "Portfolio as-of client=%s date=%s: %s holdings, value ₹%s, warnings=%s",
            client_id,
            as_of_date,
            len(portfolio["holdings"]),
            portfolio["total_value"],
            len(warnings),
        )

        # Store in per-request cache for subsequent calls with same args
        if _cache_key is not None:
            try:
                from flask import g
                if not hasattr(g, '_portfolio_cache'):
                    g._portfolio_cache = {}
                g._portfolio_cache[_cache_key] = portfolio
            except RuntimeError:
                pass

        return portfolio
        
    except Exception as e:
        logger.error(f"Error in portfolio calculation: {str(e)}")
        return {
            'client_id': client_id,
            'as_of_date': as_of_date,
            'holdings': [],
            'total_value': 0.0,
            'total_cost': 0.0,
            'calculation_method': 'forward',
            'error': str(e)
        }
