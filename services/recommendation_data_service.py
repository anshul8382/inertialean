#!/usr/bin/env python3
"""
Recommendation Data Service
Single source of truth for recommendation data access
Database-first approach - eliminates session/DB confusion
"""

from typing import Dict, List, Optional
from datetime import datetime
from models import (
    Client, Recommendation, RecommendationSession, 
    AssetClass, AssetClassDistribution, Security
)
from extensions import db
from unified_recommendation_service import UnifiedRecommendationService
import logging
import json
import re

logger = logging.getLogger(__name__)


class RecommendationDataService:
    """
    Single source of truth for recommendation data
    Always loads from database (RecommendationSession)
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.service = UnifiedRecommendationService()
    
    def get_recommendation_session_state(self, session_id: int) -> Dict:
        """
        Get complete state of a RecommendationSession from database
        Returns unified structure with all sections
        
        Args:
            session_id: RecommendationSession ID
            
        Returns:
            Dict with:
            - session_id
            - client_id
            - investment_amount
            - section_1_recommended: List of saved recommendations
            - section_2_hot_stocks: Generated hot stocks
            - section_3_other: Generated other securities
            - asset_recommendations: Asset class allocations
            - current_state: Portfolio state
            - user_actions: User selections/rejections
        """
        rec_session = RecommendationSession.query.get_or_404(session_id)
        
        # Load Section 1 from database (all asset classes)
        section_1 = self._load_section1_from_db(session_id)
        
        # Load asset allocations from AssetClassDistribution
        asset_recommendations = self._load_asset_allocations(session_id)
        
        # Get current portfolio state
        client = Client.query.get(rec_session.client_id)
        if not client:
            raise ValueError(f"Client {rec_session.client_id} not found")
        
        portfolio = self.service._get_or_create_portfolio(client)
        current_state = self.service._analyze_current_portfolio(client, portfolio)
        
        # Generate Sections 2 and 3 if needed (exclude Section 1)
        section_1_ids = {r['security_id'] for r in section_1}
        sections_2_3 = self._generate_sections_2_3(
            rec_session.client_id,
            section_1_ids,
            current_state,
            float(rec_session.investment_amount or 0)
        )
        
        return {
            'session_id': session_id,
            'client_id': rec_session.client_id,
            'investment_amount': float(rec_session.investment_amount or 0),
            'source_type': rec_session.session_type or 'adhoc',
            'section_1_recommended': section_1,
            'section_2_hot_stocks': sections_2_3['section_2'],
            'section_3_other': sections_2_3['section_3'],
            'asset_recommendations': asset_recommendations,
            'current_state': current_state,
            'user_actions': {
                'manually_selected': [r['security_id'] for r in section_1],
                'manually_rejected': []  # Can be loaded from session notes if needed
            }
        }
    
    def _load_section1_from_db(self, session_id: int) -> List[Dict]:
        """Load Section 1 recommendations from database (all asset classes)"""
        recommendations = Recommendation.query.filter_by(session_id=session_id).all()
        
        # Get model assignment for target weights
        rec_session = RecommendationSession.query.get(session_id)
        if not rec_session:
            return []
        
        client = Client.query.get(rec_session.client_id)
        if not client:
            return []
        
        model_assignment = self.service._get_client_models(client)
        model_securities = {}
        if model_assignment and model_assignment.stock_model:
            for allocation in model_assignment.stock_model.security_allocations:
                model_securities[allocation.security_id] = float(allocation.allocation_percentage)
        
        # Get current state for weight calculations
        portfolio = self.service._get_or_create_portfolio(client)
        current_state = self.service._analyze_current_portfolio(client, portfolio)
        
        # Format for display
        formatted = []
        for rec in recommendations:
            if rec.security:
                # CRITICAL: Convert all Decimal values to float
                from decimal import Decimal
                
                # CRITICAL: Convert all Decimal values to float before any arithmetic
                from decimal import Decimal
                
                # Convert current_price - handle Decimal types
                if rec.security.current_price:
                    current_price = float(rec.security.current_price) if not isinstance(rec.security.current_price, Decimal) else float(rec.security.current_price)
                elif rec.target_price:
                    current_price = float(rec.target_price) if not isinstance(rec.target_price, Decimal) else float(rec.target_price)
                else:
                    current_price = 0.0
                
                amount = 0.0
                # Convert quantity - handle Decimal types
                if rec.quantity:
                    quantity_value = float(rec.quantity) if not isinstance(rec.quantity, Decimal) else float(rec.quantity)
                else:
                    quantity_value = 0.0
                
                # CRITICAL: Handle both positive and negative quantities
                # If quantity is negative, it's a SELL (even if action says BUY - fix the action)
                # If action is SELL, amount should be negative
                if quantity_value and rec.target_price:
                    # CRITICAL: Ensure target_price is float before multiplication
                    target_price_float = float(rec.target_price) if not isinstance(rec.target_price, Decimal) else float(rec.target_price)
                    calculated_amount = quantity_value * target_price_float
                    if rec.action == 'SELL' or quantity_value < 0:
                        amount = -abs(calculated_amount)  # Negative for SELL
                        # Ensure quantity is negative for SELL in the formatted output
                        if quantity_value > 0 and rec.action == 'SELL':
                            quantity_value = -quantity_value
                    else:
                        amount = abs(calculated_amount)  # Positive for BUY
                
                # Get target_weight from model
                target_weight_from_model = model_securities.get(rec.security_id, 0.0)
                
                # Extract reason from notes
                reason = 'Saved recommendation'
                if rec.notes:
                    if 'Reason:' in rec.notes:
                        reason = rec.notes.split('Reason:')[1].split(',')[0].strip()
                    elif 'Asset Class:' in rec.notes:
                        asset_class_name = rec.notes.split('Asset Class:')[1].split(',')[0].strip()
                        reason = f"Recommended for {asset_class_name}"
                
                # Get asset class
                asset_class_name = 'Equity'  # Default
                if rec.asset_class:
                    asset_class_name = rec.asset_class.name
                elif rec.security and rec.security.asset_class:
                    asset_class_name = rec.security.asset_class.name
                
                # Determine action from quantity sign if action is incorrect
                action = rec.action
                if quantity_value < 0:
                    action = 'SELL'
                elif quantity_value > 0:
                    action = 'BUY'
                else:
                    action = 'HOLD'
                
                # CRITICAL: Build formatted_rec directly from saved values - NO RECALCULATION
                # This ensures exact values are preserved when loading Section 1
                # IMPORTANT: For display, make quantity negative for SELL to match frontend expectations
                display_quantity = quantity_value
                if action == 'SELL' and quantity_value > 0:
                    display_quantity = -quantity_value  # Make negative for SELL in frontend
                
                formatted_rec = {
                    'recommendation_id': rec.id,  # Add recommendation_id for delete operations
                    'security_id': rec.security_id,
                    'symbol': rec.security.symbol if rec.security.symbol else 'N/A',
                    'security_name': rec.security.name if rec.security.name else 'N/A',
                    'asset_class': asset_class_name,
                    'quantity': display_quantity,  # Negative for SELL to match frontend
                    'amount': amount,  # Preserve sign - negative for SELL, positive for BUY
                    'current_price': current_price,
                    'target_price': float(rec.target_price) if rec.target_price else (float(current_price) if not isinstance(current_price, Decimal) else float(current_price)),
                    'action': rec.action,
                    'reason': reason,
                    'is_recommended': True,
                    'section': '1',
                    'is_user_modified': rec.is_user_modified if rec.is_user_modified else True,  # Mark as user_modified to preserve
                    'target_weight': target_weight_from_model,
                    'current_weight': 0.0,
                    'future_weight': 0.0
                }
                
                # Calculate current_weight from current_state if available (within-class only)
                if current_state and current_state.get('current_holdings'):
                    from services.recommendation_multi_asset_service import (
                        class_total_from_state,
                        weight_within_class,
                    )
                    current_holdings = current_state.get('current_holdings', {})
                    holding = (
                        current_holdings.get(rec.security_id)
                        or current_holdings.get(str(rec.security_id))
                        or {}
                    )
                    if holding:
                        current_value = float(holding.get('current_value', 0.0) or 0)
                        formatted_rec['current_quantity'] = float(holding.get('quantity', 0.0) or 0)
                        formatted_rec['current_value'] = current_value
                        in_class = holding.get('current_weight_in_class')
                        if in_class is not None:
                            formatted_rec['current_weight'] = float(in_class)
                        else:
                            ac_total = class_total_from_state(
                                formatted_rec.get('asset_class'), current_state
                            )
                            formatted_rec['current_weight'] = weight_within_class(
                                current_value, ac_total
                            )
                
                # Recalculate future_weight if needed (within-class)
                if formatted_rec.get('future_weight', 0) == 0 and amount != 0:
                    from services.recommendation_multi_asset_service import (
                        class_total_from_state,
                        future_weight_within_class,
                    )
                    asset_class_name = formatted_rec.get('asset_class', 'Equity')
                    asset_class_total = class_total_from_state(asset_class_name, current_state)
                    current_value = float(formatted_rec.get('current_value') or 0)
                    formatted_rec['future_weight'] = round(
                        float(future_weight_within_class(
                            current_value, amount, asset_class_total, amount
                        )),
                        2,
                    )
                
                # Ensure asset_class is set and normalized
                if 'asset_class' not in formatted_rec or not formatted_rec.get('asset_class'):
                    if rec.asset_class:
                        asset_class_name = rec.asset_class.name
                    elif rec.security and rec.security.asset_class:
                        asset_class_name = rec.security.asset_class.name
                    else:
                        asset_class_name = 'Equity'
                    
                    # Normalize asset class name
                    if asset_class_name in ['Debt', 'FIXED INCOME']:
                        asset_class_name = 'Fixed Income'
                    elif asset_class_name in ['REIT/InvIT', 'REIT/INVIT', 'REITS']:
                        asset_class_name = 'REITs'
                    
                    formatted_rec['asset_class'] = asset_class_name
                else:
                    # Normalize existing asset_class
                    existing_asset_class = formatted_rec.get('asset_class', '')
                    if existing_asset_class in ['Debt', 'FIXED INCOME']:
                        formatted_rec['asset_class'] = 'Fixed Income'
                    elif existing_asset_class in ['REIT/InvIT', 'REIT/INVIT', 'REITS']:
                        formatted_rec['asset_class'] = 'REITs'
                
                formatted.append(formatted_rec)
        
        return formatted
    
    def _load_asset_allocations(self, session_id: int) -> List[Dict]:
        """Load asset allocations from AssetClassDistribution"""
        asset_distributions = AssetClassDistribution.query.filter_by(session_id=session_id).all()
        
        if asset_distributions:
            asset_recommendations = []
            for dist in asset_distributions:
                asset_recommendations.append({
                    'asset_class': dist.asset_class.name,
                    'current_value': float(dist.allocated_amount or 0) - float(dist.required_change or 0),
                    'current_weight': float(dist.current_weight or 0),
                    'target_value': float(dist.allocated_amount or 0),
                    'target_weight': float(dist.target_weight or 0),
                    'required_change': float(dist.required_change or 0),
                    'action': 'BUY' if dist.required_change and dist.required_change > 0 else 'SELL' if dist.required_change and dist.required_change < 0 else 'HOLD',
                    'in_model': True
                })
            return asset_recommendations
        
        # Fallback: Try to load from session notes
        rec_session = RecommendationSession.query.get(session_id)
        if rec_session and rec_session.notes:
            match = re.search(r'ASSET_ALLOCATIONS:(.+)', rec_session.notes)
            if match:
                try:
                    asset_recommendations = json.loads(match.group(1))
                    return asset_recommendations
                except json.JSONDecodeError:
                    pass
        
        return []
    
    def _generate_sections_2_3(self, client_id: int, section_1_ids: set,
                                current_state: Dict, investment_amount: float,
                                asset_class: Optional[str] = None) -> Dict:
        """Generate Sections 2 and 3 excluding Section 1 securities
        
        Args:
            asset_class: Optional asset class to filter by (e.g., 'Fixed Income', 'Equity').
                        If provided, only securities matching this asset class will be returned.
        """
        section_2 = self.service._get_all_hot_stocks_formatted(
            client_id,
            current_state,
            investment_amount,
            excluded_security_ids=list(section_1_ids),
            asset_class=asset_class
        )
        
        # Get Section 2 IDs for exclusion from Section 3
        section_2_ids = {s['security_id'] for s in section_2}
        all_excluded_ids = list(section_1_ids.union(section_2_ids))
        
        client = Client.query.get(client_id)
        model_assignment = self.service._get_client_models(client)
        
        section_3 = self.service._get_all_other_securities_formatted(
            client_id,
            list(section_1_ids),
            list(section_2_ids),
            current_state,
            model_assignment,
            excluded_security_ids=all_excluded_ids,
            investment_amount=investment_amount,
            asset_class=asset_class
        )
        
        return {
            'section_2': section_2,
            'section_3': section_3
        }
    
    def save_section1_to_db(self, session_id: int, section1_recommendations: List[Dict],
                            created_by: int) -> bool:
        """
        Save Section 1 recommendations to database - INCREMENTAL
        Simple: For each recommendation in the list, update if exists, create if new
        Does NOT delete any existing recommendations - purely incremental
        
        Args:
            session_id: RecommendationSession ID
            section1_recommendations: List of recommendation dicts (existing + new)
            created_by: User ID who created/modified
            
        Returns:
            bool: Success status
        """
        rec_session = RecommendationSession.query.get_or_404(session_id)
        
        # Group by asset class for summary
        by_asset_class = {}
        for rec in section1_recommendations:
            asset_class = rec.get('asset_class', 'Equity')
            if asset_class not in by_asset_class:
                by_asset_class[asset_class] = []
            by_asset_class[asset_class].append(rec)
        
        # Simple incremental save: For each recommendation, update or create
        # We do NOT delete anything - this is purely additive/updating
        saved_count = 0
        for rec in section1_recommendations:
            security_id = rec.get('security_id')
            if not security_id:
                continue
            
            asset_class = rec.get('asset_class', 'Equity')
            asset_class_obj = AssetClass.query.filter_by(name=asset_class).first()
            
            # Get target_price (use from rec, or current_price, or security's current_price)
            # CRITICAL: Ensure all values are converted to float to avoid Decimal issues
            from decimal import Decimal
            target_price_raw = rec.get('target_price') or rec.get('current_price')
            if not target_price_raw:
                security = Security.query.get(security_id)
                if security and security.current_price:
                    target_price_raw = security.current_price
                else:
                    target_price_raw = 0.0
            
            # Convert to float safely - handle all possible types
            from decimal import Decimal
            if target_price_raw is None:
                target_price = 0.0
            elif isinstance(target_price_raw, Decimal):
                target_price = float(target_price_raw)
            elif isinstance(target_price_raw, (int, float)):
                target_price = float(target_price_raw)
            else:
                try:
                    target_price = float(target_price_raw)
                except (ValueError, TypeError):
                    target_price = 0.0
            
            # Get quantity - ensure it's converted to float
            quantity_raw = rec.get('quantity', 0)
            if quantity_raw is None:
                quantity = 0.0
            elif isinstance(quantity_raw, Decimal):
                quantity = float(quantity_raw)
            elif isinstance(quantity_raw, (int, float)):
                quantity = float(quantity_raw)
            else:
                try:
                    quantity = float(quantity_raw)
                except (ValueError, TypeError):
                    quantity = 0.0
            amount_raw = rec.get('amount', 0)
            amount_float = float(amount_raw) if not isinstance(amount_raw, Decimal) else float(amount_raw)

            from services.recommendation_trade_normalizer import resolve_trade_action_and_quantity

            action, trade_qty = resolve_trade_action_and_quantity(
                action=rec.get('action'),
                quantity=quantity,
                amount=amount_float,
                target_price=target_price,
                current_quantity=rec.get('current_quantity'),
                new_quantity=rec.get('new_quantity'),
            )
            quantity = float(trade_qty)

            if amount_float:
                calculated_amount = abs(amount_float)
            elif quantity and target_price:
                calculated_amount = float(quantity) * float(target_price)
            else:
                calculated_amount = 0.0

            # Check if recommendation already exists
            existing = Recommendation.query.filter_by(
                session_id=session_id,
                security_id=security_id
            ).first()
            
            if existing:
                # Always update with the values from rec - save_section1_to_db is called when
                # the caller explicitly wants to persist this data (e.g. after recalculate or save).
                # The rec dict contains the current state to save - never preserve stale DB values.
                # CRITICAL: Ensure all values are float (not Decimal) before assigning to database model
                existing.quantity = float(quantity) if not isinstance(quantity, Decimal) else float(quantity)
                existing.target_price = float(target_price) if not isinstance(target_price, Decimal) else float(target_price)
                existing.action = action
                existing.is_user_modified = rec.get('is_user_modified', False)
                existing.asset_class_id = asset_class_obj.id if asset_class_obj else None
                # Update notes
                reason = rec.get('reason', 'Saved recommendation')
                calculated_amount_float = float(calculated_amount) if not isinstance(calculated_amount, Decimal) else float(calculated_amount)
                existing.notes = f"Reason: {reason}, Asset Class: {asset_class}, Amount: {calculated_amount_float}"
                saved_count += 1
            else:
                # Create new recommendation
                reason = rec.get('reason', 'Saved recommendation')
                # CRITICAL: Ensure all numeric values are float (not Decimal) before creating database object
                quantity_float = float(quantity) if not isinstance(quantity, Decimal) else float(quantity)
                target_price_float = float(target_price) if not isinstance(target_price, Decimal) else float(target_price)
                calculated_amount_float = float(calculated_amount) if not isinstance(calculated_amount, Decimal) else float(calculated_amount)
                
                recommendation = Recommendation(
                    client_id=rec_session.client_id,
                    security_id=security_id,
                    session_id=session_id,
                    asset_class_id=asset_class_obj.id if asset_class_obj else None,
                    quantity=quantity_float,
                    target_price=target_price_float,
                    action=action,
                    is_user_modified=rec.get('is_user_modified', False),
                    created_by=created_by,
                    notes=f"Reason: {reason}, Asset Class: {asset_class}, Amount: {calculated_amount_float}"
                )
                db.session.add(recommendation)
                saved_count += 1
        
        # Update AssetClassDistribution summaries
        self._update_asset_class_distributions(session_id, by_asset_class)
        
        try:
            db.session.commit()
            self.logger.info(f"Incremental save: Updated/created {saved_count} recommendations in session {session_id} (total in list: {len(section1_recommendations)})")
            return True
        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Error saving recommendations: {e}")
            raise
    
    def _update_asset_class_distributions(self, session_id: int, by_asset_class: Dict):
        """Update AssetClassDistribution summaries"""
        for asset_class, recommendations in by_asset_class.items():
            asset_class_obj = AssetClass.query.filter_by(name=asset_class).first()
            if not asset_class_obj:
                continue
            
            dist = AssetClassDistribution.query.filter_by(
                session_id=session_id,
                asset_class_id=asset_class_obj.id
            ).first()
            
            if dist:
                # Update existing
                dist.security_count = len(recommendations)
                dist.total_recommended_amount = sum(float(r.get('amount', 0)) for r in recommendations)
                dist.updated_at = datetime.now()
            else:
                # Create new if doesn't exist
                # Get from asset_recommendations if available
                rec_session = RecommendationSession.query.get(session_id)
                asset_recommendations = self._load_asset_allocations(session_id)
                asset_rec = next((r for r in asset_recommendations if r.get('asset_class') == asset_class), None)
                
                if asset_rec:
                    dist = AssetClassDistribution(
                        session_id=session_id,
                        asset_class_id=asset_class_obj.id,
                        target_weight=asset_rec.get('target_weight', 0),
                        current_weight=asset_rec.get('current_weight', 0),
                        allocated_amount=asset_rec.get('target_value', 0),
                        required_change=asset_rec.get('required_change', 0),
                        security_count=len(recommendations),
                        total_recommended_amount=sum(
                            float(r.get('quantity', 0)) * float(r.get('target_price') or r.get('current_price', 0))
                            for r in recommendations
                        )
                    )
                    db.session.add(dist)
    
    def delete_recommendation_session(self, session_id: int) -> bool:
        """Delete a recommendation session and all related records"""
        try:
            rec_session = RecommendationSession.query.get_or_404(session_id)
            
            # Delete all recommendations (cascade should handle this)
            Recommendation.query.filter_by(session_id=session_id).delete()
            
            # Delete asset distributions
            AssetClassDistribution.query.filter_by(session_id=session_id).delete()
            
            # Delete session
            db.session.delete(rec_session)
            db.session.commit()
            
            self.logger.info(f"Deleted recommendation session {session_id}")
            return True
        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Error deleting session: {e}")
            raise

