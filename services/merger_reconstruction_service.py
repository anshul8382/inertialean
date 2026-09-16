"""
Merger Reconstruction Service - Handles merger impacts for portfolio reconstruction

Purpose: Identify mergers affecting a client's portfolio between two dates and provide
         structured data for portfolio reconstruction.

This service extracts and reuses logic from corporate action impact analysis,
making it available for both UI monitoring and portfolio reconstruction.
"""
from datetime import date
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def get_merger_impact_for_reconstruction(
    client_id: int,
    target_date: date,
    standing_date: date
) -> List[Dict[str, Any]]:
    """
    Get merger impacts for portfolio reconstruction between two dates
    
    Identifies mergers that occurred between target_date and standing_date,
    and calculates their impact on the client's portfolio.
    
    Args:
        client_id: Client ID
        target_date: Start date (older date)
        standing_date: End date (newer date)
    
    Returns:
        List of merger impact dictionaries:
        [
            {
                'action_id': int,
                'action_type': 'MERGER',
                'action_date': date,
                'source_security_id': int,
                'source_symbol': str,
                'source_quantity': float,  # Quantity before merger
                'source_avg_price': float,
                'target_security_id': int,
                'target_symbol': str,
                'converted_quantity': float,  # What source converts to
                'conversion_ratio': float,
                'client_id': int
            },
            ...
        ]
    """
    try:
        from models import CorporateAction, Security, Transaction
        
        # Find all MERGER actions between target_date and standing_date
        merger_actions = CorporateAction.query.filter(
            CorporateAction.action_type == 'MERGER',
            CorporateAction.is_active == True,
            CorporateAction.action_date >= target_date,
            CorporateAction.action_date <= standing_date
        ).all()
        
        logger.info(f"Found {len(merger_actions)} merger actions between {target_date} and {standing_date}")
        
        merger_impacts = []
        
        for action in merger_actions:
            source_security_id = getattr(action, 'source_security_id', None)
            if not source_security_id:
                logger.warning(f"Merger action {action.id} has no source_security_id")
                continue
            
            # Get security information
            source_security = Security.query.get(source_security_id)
            target_security = Security.query.get(action.security_id)
            
            if not source_security or not target_security:
                logger.warning(f"Missing security info for merger {action.id}")
                continue
            
            # Check if client has transactions in source security before merger
            source_transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == source_security_id,
                Transaction.transaction_date <= action.action_date
            ).order_by(Transaction.transaction_date).all()
            
            if not source_transactions:
                # Client has no transactions in source security
                continue
            
            # Calculate net quantity and average price from transactions
            source_net_qty = 0.0
            total_source_cost = 0.0
            
            for txn in source_transactions:
                if txn.type == 'BUY':
                    source_net_qty += float(txn.quantity)
                    total_source_cost += float(txn.quantity) * float(txn.price)
                elif txn.type == 'SELL':
                    source_net_qty -= float(txn.quantity)
                    total_source_cost -= float(txn.quantity) * float(txn.price)
            
            # Only include if there's a net positive quantity
            if source_net_qty <= 0:
                continue
            
            # Calculate average price
            source_avg_price = total_source_cost / source_net_qty if source_net_qty > 0 else 0.0
            
            # Calculate converted quantity
            conversion_ratio = float(action.ratio)
            converted_quantity = source_net_qty * conversion_ratio
            
            merger_impact = {
                'action_id': action.id,
                'action_type': 'MERGER',
                'action_date': action.action_date,
                'source_security_id': source_security_id,
                'source_symbol': source_security.symbol,
                'source_quantity': source_net_qty,
                'source_avg_price': source_avg_price,
                'target_security_id': action.security_id,
                'target_symbol': target_security.symbol,
                'converted_quantity': converted_quantity,
                'conversion_ratio': conversion_ratio,
                'client_id': client_id
            }
            
            merger_impacts.append(merger_impact)
            
            logger.info(f"Merger impact: {source_security.symbol} ({source_net_qty:.2f}) → "
                       f"{target_security.symbol} ({converted_quantity:.2f}) on {action.action_date}")
        
        logger.info(f"Returning {len(merger_impacts)} merger impacts for client {client_id}")
        return merger_impacts
        
    except Exception as e:
        logger.error(f"Error in get_merger_impact_for_reconstruction: {str(e)}")
        return []


def get_merger_impact_for_client_security(
    client_id: int,
    security_id: int,
    target_date: date,
    standing_date: date
) -> Optional[Dict[str, Any]]:
    """
    Get merger impact for a specific client and security
    
    Useful for checking if a specific security was affected by a merger
    during the reconstruction period.
    
    Args:
        client_id: Client ID
        security_id: Security ID to check
        target_date: Start date (older date)
        standing_date: End date (newer date)
    
    Returns:
        Merger impact dictionary if found, None otherwise
    """
    try:
        from models import CorporateAction, Security, Transaction
        
        # Check if this security was the target of any merger
        merger_actions = CorporateAction.query.filter(
            CorporateAction.action_type == 'MERGER',
            CorporateAction.is_active == True,
            CorporateAction.security_id == security_id,  # This security is the target
            CorporateAction.action_date >= target_date,
            CorporateAction.action_date <= standing_date
        ).all()
        
        for action in merger_actions:
            source_security_id = getattr(action, 'source_security_id', None)
            if not source_security_id:
                continue
            
            # Check if client has transactions in source security
            source_transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == source_security_id,
                Transaction.transaction_date <= action.action_date
            ).all()
            
            if source_transactions:
                # Calculate impact
                source_net_qty = 0.0
                total_source_cost = 0.0
                
                for txn in source_transactions:
                    if txn.type == 'BUY':
                        source_net_qty += float(txn.quantity)
                        total_source_cost += float(txn.quantity) * float(txn.price)
                    elif txn.type == 'SELL':
                        source_net_qty -= float(txn.quantity)
                        total_source_cost -= float(txn.quantity) * float(txn.price)
                
                if source_net_qty > 0:
                    source_security = Security.query.get(source_security_id)
                    target_security = Security.query.get(security_id)
                    
                    if source_security and target_security:
                        return {
                            'action_id': action.id,
                            'action_type': 'MERGER',
                            'action_date': action.action_date,
                            'source_security_id': source_security_id,
                            'source_symbol': source_security.symbol,
                            'source_quantity': source_net_qty,
                            'source_avg_price': total_source_cost / source_net_qty if source_net_qty > 0 else 0.0,
                            'target_security_id': security_id,
                            'target_symbol': target_security.symbol,
                            'converted_quantity': source_net_qty * float(action.ratio),
                            'conversion_ratio': float(action.ratio),
                            'client_id': client_id
                        }
        
        return None
        
    except Exception as e:
        logger.error(f"Error in get_merger_impact_for_client_security: {str(e)}")
        return None


def get_source_securities_for_reconstruction(
    client_id: int,
    target_date: date,
    standing_date: date
) -> List[Dict[str, Any]]:
    """
    Get source securities that need to be added to portfolio reconstruction
    
    This is a simplified version that returns only the source securities
    that should be included in the reconstruction starting point.
    
    Args:
        client_id: Client ID
        target_date: Start date (older date)
        standing_date: End date (newer date)
    
    Returns:
        List of source security dictionaries:
        [
            {
                'security_id': int,
                'symbol': str,
                'quantity': float,  # Quantity at target_date
                'average_price': float,
                'merger_action_id': int,
                'merger_date': date
            },
            ...
        ]
    """
    merger_impacts = get_merger_impact_for_reconstruction(client_id, target_date, standing_date)
    
    source_securities = []
    for impact in merger_impacts:
        source_securities.append({
            'security_id': impact['source_security_id'],
            'symbol': impact['source_symbol'],
            'quantity': impact['source_quantity'],
            'average_price': impact['source_avg_price'],
            'merger_action_id': impact['action_id'],
            'merger_date': impact['action_date']
        })
    
    return source_securities
