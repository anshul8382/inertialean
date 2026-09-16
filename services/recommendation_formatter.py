#!/usr/bin/env python3
"""
Recommendation Formatter
Formatting utilities for recommendations (display, email, etc.)
"""

import re
from typing import Dict, List, Tuple, Optional
from models import Client, Security, Recommendation
from unified_recommendation_service import UnifiedRecommendationService
import logging

logger = logging.getLogger(__name__)


def _parse_amount_from_recommendation_notes(notes: Optional[str]) -> Optional[float]:
    """Recover rupee amount from notes written by record_recommendations (Amount: N)."""
    if not notes:
        return None
    m = re.search(r'Amount:\s*([\d,.\s]+)', str(notes), re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


class RecommendationFormatter:
    """Format recommendations for display/email"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.service = UnifiedRecommendationService()
    
    def format_for_display(self, recommendation: Dict, current_state: Dict) -> Dict:
        """Format with weights, prices, etc. for UI display"""
        # This is already handled by UnifiedRecommendationService._format_security_recommendation
        # But we can add additional formatting here if needed
        return recommendation
    
    def format_for_email(self, recommendations: List[Recommendation], client: Client) -> Tuple[List[Dict], float]:
        """
        Format recommendations from database for email template
        
        Args:
            recommendations: List of Recommendation objects from database
            client: Client object
            
        Returns:
            Tuple of (formatted_recommendations, total_investment_amount)
        """
        formatted_recs = []
        total_investment_amount = 0.0
        seen_security_ids = set()
        
        for rec in recommendations:
            security = rec.security
            if security is None and rec.security_id:
                security = Security.query.get(rec.security_id)
            if not security:
                self.logger.warning(
                    "Skipping recommendation id=%s: security_id=%s not resolvable",
                    getattr(rec, 'id', None),
                    getattr(rec, 'security_id', None),
                )
                continue
            
            # Deduplicate
            if rec.security_id in seen_security_ids:
                self.logger.warning(
                    "Skipping duplicate security_id %s (%s)",
                    rec.security_id,
                    getattr(security, 'symbol', '?'),
                )
                continue
            seen_security_ids.add(rec.security_id)
            
            # Use target_price from saved recommendation, fallback to current_price
            price_to_use = float(rec.target_price) if rec.target_price else (
                float(security.current_price) if security.current_price else 0.0
            )
            quantity = float(rec.quantity) if rec.quantity is not None else 0.0
            
            # Calculate amount based on saved quantity and target_price
            amount = quantity * price_to_use
            if amount < 1e-9:
                try:
                    ta = rec.total_amount
                    if ta is not None:
                        amount = float(ta)
                except Exception:
                    pass
            if amount < 1e-9:
                parsed = _parse_amount_from_recommendation_notes(rec.notes)
                if parsed is not None and parsed > 1e-9:
                    amount = parsed
                    if quantity < 1e-9 and price_to_use > 1e-9:
                        quantity = round(amount / price_to_use)
            
            # Normalize action: DB often stores lowercase (buy/sell/hold); email template expects BUY/SELL/HOLD
            action_lower = (rec.action or 'hold').strip().lower()
            has_trade_size = abs(amount) > 1e-9 or abs(quantity) > 1e-9

            # CRITICAL: If amount is negative, ensure action is SELL
            if amount < 0 and action_lower != 'sell':
                self.logger.warning(
                    f"Recommendation {rec.id} has negative amount ({amount}) but action is {rec.action}, treating as SELL"
                )

            # Get current holding value
            current_holding_value = 0.0
            for holding in client.holdings:
                if holding.security_id == rec.security_id:
                    current_qty = float(holding.quantity) if holding.quantity else 0.0
                    current_holding_value = current_qty * price_to_use
                    break

            if action_lower == 'sell' or amount < 0:
                display_amount = -abs(amount)
                display_action = 'SELL'
            elif action_lower == 'buy':
                display_amount = abs(amount)
                display_action = 'BUY'
            elif action_lower == 'hold' and has_trade_size:
                # Saved as HOLD but has rupee/qty size — show as BUY in client email (matches typical trade recos)
                display_amount = abs(amount)
                display_action = 'BUY'
            else:
                display_amount = abs(amount)
                display_action = 'HOLD'
            
            formatted_rec = {
                'type': 'stock',
                'symbol': security.symbol,
                'security_name': security.name,
                'asset_class': rec.asset_class.name if rec.asset_class else (
                    security.asset_class.name if security.asset_class else 'Unknown'
                ),
                'action': display_action,  # Use corrected action
                'quantity': int(abs(quantity)) if quantity else 0,  # Always positive for display
                'current_price': price_to_use,
                'amount': abs(amount),  # Display as positive in email (section indicates SELL)
                'amount_signed': display_amount,  # Keep signed version for calculations
                'current_value': current_holding_value,
                'current_allocation': 0.0,  # Will be calculated with total portfolio value
                'target_allocation': 0.0,  # Will be calculated with total portfolio value
                'notes': rec.notes or ''
            }
            formatted_recs.append(formatted_rec)
            
            # For BUY: positive amount (investment)
            # For SELL: negative amount (withdrawal)
            if display_action == 'BUY':
                total_investment_amount += abs(amount)
            elif display_action == 'SELL':
                total_investment_amount -= abs(amount)
        
        return formatted_recs, total_investment_amount
    
    def format_session_data_for_email(self, recommendations: List[Dict], client: Client) -> Tuple[List[Dict], float]:
        """
        Format recommendations from session data for email template
        
        Args:
            recommendations: List of recommendation dicts from session
            client: Client object
            
        Returns:
            Tuple of (formatted_recommendations, total_investment_amount)
        """
        formatted_recs = []
        total_investment_amount = 0.0
        seen_security_ids = set()
        
        for rec in recommendations:
            security_id = rec.get('security_id')
            if not security_id:
                self.logger.warning(f"Skipping recommendation without security_id: {rec.get('symbol', 'unknown')}")
                continue
            
            security = Security.query.get(security_id)
            if not security:
                self.logger.warning(f"Skipping recommendation with invalid security_id {security_id}")
                continue
            
            # Deduplicate
            if security_id in seen_security_ids:
                self.logger.warning(f"Skipping duplicate security_id {security_id} ({security.symbol})")
                continue
            seen_security_ids.add(security_id)
            
            # CRITICAL: Make copies of values to avoid modifying original rec
            quantity = rec.get('quantity', 0)
            amount = float(rec.get('amount', 0.0))
            action = rec.get('action', 'HOLD')
            
            # CRITICAL: If amount is negative, convert to SELL action
            # But preserve original amount for calculations
            original_amount = amount
            if amount < 0:
                action = 'SELL'
                amount = abs(amount)  # Make positive for display
                if quantity > 0:
                    quantity = -abs(quantity)  # Make quantity negative for SELL
            
            self.logger.debug(f"Formatting rec: security_id={security_id}, original_amount={original_amount}, amount={amount}, quantity={quantity}, action={action}")
            
            # Determine action from quantity if not explicitly set
            if not action or action == 'HOLD':
                if quantity > 0:
                    action = 'BUY'
                elif quantity < 0:
                    action = 'SELL'
            
            # Calculate amount if missing
            if amount == 0 and quantity != 0:
                current_price = float(rec.get('current_price', 0) or (security.current_price if security.current_price else 0))
                if current_price > 0:
                    amount = abs(quantity) * current_price
            
            current_price = float(rec.get('current_price', 0) or rec.get('target_price', 0) or (security.current_price if security.current_price else 0))
            
            # Get current holding value
            current_holding_value = 0.0
            for holding in client.holdings:
                if holding.security_id == security_id:
                    current_qty = float(holding.quantity) if holding.quantity else 0.0
                    current_holding_value = current_qty * current_price
                    break
            
            # CRITICAL: Don't skip recommendations that have been explicitly set, even if amount/quantity is 0
            # Only skip if it's truly a HOLD with no values AND wasn't explicitly set
            # If action was explicitly set to BUY or SELL, don't skip even if amount is 0
            should_skip = False
            if action == 'HOLD' and quantity == 0 and amount == 0:
                # Only skip if action wasn't explicitly provided (defaulted to HOLD)
                if not rec.get('action') or rec.get('action') == 'HOLD':
                    should_skip = True
            
            if should_skip:
                continue
            
            # CRITICAL: For SELL, amount should be negative to indicate withdrawal
            # For BUY, amount should be positive to indicate investment
            if action == 'SELL':
                # Make amount negative for SELL
                display_amount = -abs(float(amount)) if amount else 0.0
            else:
                # Keep amount positive for BUY
                display_amount = abs(float(amount)) if amount else 0.0
            
            # Preserve all original fields from the recommendation
            formatted_rec = {
                'type': 'stock',
                'symbol': rec.get('symbol') or security.symbol,
                'security_name': rec.get('security_name') or security.name,
                'asset_class': rec.get('asset_class') or (security.asset_class.name if security.asset_class else 'Unknown'),
                'action': action,
                'quantity': int(abs(quantity)) if quantity else 0,
                'current_price': current_price if current_price > 0 else (float(security.current_price) if security.current_price else 0),
                'amount': abs(float(amount)) if amount else 0.0,  # Display as positive in email (section indicates SELL)
                'amount_signed': display_amount,  # Keep signed version for calculations
                'current_value': current_holding_value,
                'current_allocation': rec.get('current_allocation', 0.0),  # Preserve original
                'target_allocation': rec.get('target_allocation', 0.0),  # Preserve original
                'future_weight': rec.get('future_weight', 0.0),  # Preserve original
                'target_weight': rec.get('target_weight', 0.0),  # Preserve original
                'current_weight': rec.get('current_weight', 0.0),  # Preserve original
                'security_id': security_id,  # CRITICAL: Preserve security_id
                'notes': rec.get('reason', rec.get('notes', ''))  # Preserve notes/reason
            }
            formatted_recs.append(formatted_rec)
            
            if action == 'BUY':
                total_investment_amount += abs(float(amount)) if amount else 0
            elif action == 'SELL':
                total_investment_amount -= abs(float(amount)) if amount else 0
        
        return formatted_recs, total_investment_amount
    
    def calculate_weights(self, security: Security, current_state: Dict, 
                          model_assignment) -> Dict:
        """Calculate current/target/future weights"""
        # This is handled by UnifiedRecommendationService._format_security_recommendation
        # But we can add additional weight calculations here if needed
        return {}


