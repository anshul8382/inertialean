#!/usr/bin/env python3
"""
Recommendation Service
Core business logic for recommendations
Handles: generation, movement, recalculation, redistribution
"""

from typing import Dict, List, Optional
from models import Client, Security, Recommendation, RecommendationSession, ModelAssignment
from unified_recommendation_service import UnifiedRecommendationService
from services.recommendation_data_service import RecommendationDataService
from extensions import db
import logging
import copy

logger = logging.getLogger(__name__)


class RecommendationService:
    """Core recommendation business logic"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.service = UnifiedRecommendationService()
        self.data_service = RecommendationDataService()
    
    def _safe_float_convert(self, value):
        """Safely convert any numeric type (int, float, Decimal) to float"""
        from decimal import Decimal
        if value is None:
            return 0.0
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    
    def _ensure_dict_values_float(self, rec_dict):
        """Ensure all numeric values in a dict are float (not Decimal)"""
        from decimal import Decimal
        safe_dict = {}
        for key, value in rec_dict.items():
            if isinstance(value, Decimal):
                safe_dict[key] = float(value)
            elif isinstance(value, dict):
                safe_dict[key] = self._ensure_dict_values_float(value)
            elif isinstance(value, list):
                safe_dict[key] = [self._ensure_dict_values_float(item) if isinstance(item, dict) else (float(item) if isinstance(item, Decimal) else item) for item in value]
            else:
                safe_dict[key] = value
        return safe_dict
    
    def generate_recommendations(self, client_id: int, investment_amount: float,
                                 source_type: str = 'adhoc', workflow_id: Optional[int] = None,
                                 manually_selected: List[int] = None,
                                 manually_rejected: List[int] = None) -> Dict:
        """
        Generate all sections (1, 2, 3) of recommendations
        
        Args:
            client_id: Client ID
            investment_amount: Investment amount
            source_type: 'adhoc', 'monthly_investment', 'workflow', 'withdrawal'
            workflow_id: Optional workflow ID
            manually_selected: List of security IDs user selected
            manually_rejected: List of security IDs user rejected
            
        Returns:
            Dict with sections 1, 2, 3 and asset_recommendations
        """
        result = self.service.generate_recommendations(
            client_id=client_id,
            investment_amount=investment_amount,
            source_type=source_type,
            workflow_id=workflow_id,
            manually_selected=manually_selected or [],
            manually_rejected=manually_rejected or []
        )
        
        return result
    
    def move_to_section1(self, session_id: int, security_ids: List[int], asset_class: Optional[str] = None, current_section1_data: Optional[List[Dict]] = None) -> Dict:
        """
        Move securities from Section 2/3 to Section 1 - SIMPLE INCREMENTAL APPROACH
        
        CRITICAL: When moving from sec3/sec2 to sec1:
        - Just add new stocks to sec1 (append only)
        - Do NOT remove any stocks from sec1
        - Do NOT recalculate quantities of existing stocks
        - Preserve ALL existing Section 1 values exactly as they are
        
        Flow:
        1. Load existing Section 1 from database (preserve ALL values exactly)
        2. Preserve user-edited values from current_section1_data if provided
        3. Find securities in Section 2/3
        4. Append to existing Section 1 (new stocks only)
        5. Save (incremental - only adds new, preserves existing)
        6. Regenerate Sections 2 and 3 (filtered by asset_class if provided)
        
        Args:
            session_id: RecommendationSession ID
            security_ids: List of security IDs to move
            asset_class: Optional asset class to filter Sections 2 and 3 (e.g., 'Fixed Income', 'Equity')
            current_section1_data: Optional list of current user-edited values to preserve
            
        Returns:
            Dict with updated sections 1, 2, 3
        """
        # Step 1: Load existing Section 1 - from caller (session) when provided, else from database
        rec_session = RecommendationSession.query.get(session_id)
        if not rec_session:
            return {'success': False, 'error': 'Session not found'}
        
        client = Client.query.get(rec_session.client_id)
        model_assignment = self.service._get_client_models(client) if client else None
        model_securities = {}
        if model_assignment and model_assignment.stock_model:
            for allocation in model_assignment.stock_model.security_allocations:
                model_securities[allocation.security_id] = float(allocation.allocation_percentage)
        
        portfolio = self.service._get_or_create_portfolio(client) if client else None
        current_state = self.service._analyze_current_portfolio(client, portfolio) if client and portfolio else {}
        
        used_session_s1 = bool(current_section1_data)
        if used_session_s1:
            existing_section1 = copy.deepcopy(current_section1_data)
            existing_ids = {int(r.get('security_id')) for r in existing_section1 if r.get('security_id')}
            self.logger.info(f"Move-to-section1: Using {len(existing_section1)} existing Section 1 from caller (session)")
        else:
            existing_section1 = []
            existing_ids = set()
            existing_recommendations = Recommendation.query.filter_by(session_id=session_id).all()
            # Format existing recommendations from DB - PRESERVE EXACT QUANTITIES AND AMOUNTS
            for rec in existing_recommendations:
                if rec.security:
                    security_id = rec.security_id
                    existing_ids.add(security_id)
                    
                    # CRITICAL: Convert all Decimal values to float before any arithmetic operations
                    from decimal import Decimal
                    
                    # Convert current_price - handle both Decimal and float
                    if rec.security.current_price:
                        current_price = float(rec.security.current_price) if not isinstance(rec.security.current_price, Decimal) else float(rec.security.current_price)
                    elif rec.target_price:
                        current_price = float(rec.target_price) if not isinstance(rec.target_price, Decimal) else float(rec.target_price)
                    else:
                        current_price = 0.0
                    
                    # PRESERVE EXACT SAVED VALUES - do not recalculate
                    # Ensure all values are float before multiplication
                    saved_quantity = float(rec.quantity) if rec.quantity else 0.0
                    if isinstance(rec.quantity, Decimal):
                        saved_quantity = float(rec.quantity)
                    
                    saved_target_price = float(rec.target_price) if rec.target_price else current_price
                    if isinstance(rec.target_price, Decimal):
                        saved_target_price = float(rec.target_price)
                    elif rec.target_price is None:
                        saved_target_price = current_price
                    
                    # Now safe to multiply - both are guaranteed to be float
                    saved_amount = float(saved_quantity) * float(saved_target_price) if saved_quantity and saved_target_price else 0.0
                    if rec.action == 'SELL':
                        saved_amount = -saved_amount
                    
                    # Get target_weight from model
                    target_weight_from_model = model_securities.get(security_id, 0.0)
                    
                    # Extract reason from notes
                    reason = 'Saved recommendation'
                    if rec.notes:
                        if 'Reason:' in rec.notes:
                            reason = rec.notes.split('Reason:')[1].split(',')[0].strip()
                    
                    # Get asset class
                    asset_class_name = 'Equity'  # Default
                    if rec.security and rec.security.asset_class:
                        asset_class_name = rec.security.asset_class.name
                    
                    # Build formatted_rec directly to avoid any recalculation
                    # CRITICAL: Use EXACT saved values - do not recalculate anything
                    # CRITICAL: Mark ALL existing recommendations as user_modified to preserve them
                    # This ensures they won't be recalculated when saving or moving stocks
                    formatted_rec = {
                        'security_id': security_id,
                        'symbol': rec.security.symbol if rec.security.symbol else 'N/A',
                        'security_name': rec.security.name if rec.security.name else 'N/A',
                        'asset_class': asset_class_name,
                        'quantity': saved_quantity,
                        'amount': abs(saved_amount),
                        'current_price': current_price,
                        'target_price': saved_target_price,
                        'action': rec.action,
                        'reason': reason,
                        'is_recommended': True,
                        'section': '1',
                        'is_user_modified': True,  # CRITICAL: Mark as user_modified to preserve exact values
                        'target_weight': target_weight_from_model,
                        'current_weight': 0.0,  # Will be calculated if needed, but not used for preservation
                        'future_weight': 0.0  # Will be calculated below if needed
                    }
                    
                    # Calculate current_weight from current_state if available (for display only)
                    if current_state and current_state.get('current_holdings'):
                        current_holdings = current_state.get('current_holdings', {})
                        # Support both int and string keys (session/JSON can coerce dict keys to strings)
                        holding = current_holdings.get(security_id) or current_holdings.get(str(security_id)) or {}
                        if holding:
                            formatted_rec['current_weight'] = float(holding.get('current_weight', 0.0))
                            formatted_rec['current_quantity'] = float(holding.get('quantity', 0.0))
                            formatted_rec['current_value'] = float(holding.get('current_value', 0.0))
                    
                    # Calculate future_weight ONLY if we have the data and it's needed
                    # But use the SAVED amount, not recalculated
                    if saved_amount > 0:
                        current_asset_allocations = current_state.get('current_asset_allocations', {}) if current_state else {}
                        asset_class_total = current_asset_allocations.get(asset_class_name, 0.0)
                        future_asset_class_total = asset_class_total + abs(saved_amount)
                        if future_asset_class_total > 0:
                            future_weight = (abs(saved_amount) / future_asset_class_total) * 100
                            formatted_rec['future_weight'] = round(float(future_weight), 2)
                    
                    existing_section1.append(formatted_rec)
            self.logger.info(f"Move-to-section1: Loaded {len(existing_section1)} existing securities from database (preserving exact quantities)")
        
        # Step 1.5: Preserve user-edited values if provided (override database values); skip when we already used session as base
        if current_section1_data and not used_session_s1:
            user_edited_map = {item['security_id']: item for item in current_section1_data}
            for rec in existing_section1:
                security_id = rec.get('security_id')
                if security_id in user_edited_map:
                    user_edits = user_edited_map[security_id]
                    # Preserve user-edited quantity, amount, and future_weight
                    if 'quantity' in user_edits:
                        rec['quantity'] = user_edits['quantity']
                    if 'amount' in user_edits:
                        rec['amount'] = user_edits['amount']
                    if 'future_weight' in user_edits:
                        rec['future_weight'] = user_edits['future_weight']
                    # Mark as user-modified if user provided edits
                    rec['is_user_modified'] = True
                    self.logger.debug(f"Preserved user edits for security {security_id}: qty={user_edits.get('quantity')}, amt={user_edits.get('amount')}")
        
        # Step 2: Find securities to move from Section 2/3 and append to existing
        # Get state for Sections 2 and 3
        state = self.data_service.get_recommendation_session_state(session_id)
        
        moved_securities = []
        for security_id in security_ids:
            security_id_int = int(security_id)
            
            # Skip if already in Section 1
            if security_id_int in existing_ids:
                self.logger.info(f"Security {security_id_int} already in Section 1, skipping")
                continue
            
            # Find in Section 2 or 3
            found = None
            for rec in state['section_2_hot_stocks']:
                if rec.get('security_id') == security_id_int:
                    found = rec
                    break
            
            if not found:
                for rec in state['section_3_other']:
                    if rec.get('security_id') == security_id_int:
                        found = rec
                        break
            
            if found:
                # Simple: Just append (make a copy) - preserve original values from Section 2/3
                # CRITICAL: Convert Decimal to float to avoid JSON serialization errors
                moved_rec = copy.deepcopy(found)
                
                # Use helper method to ensure all values are float
                moved_rec = self._ensure_dict_values_float(moved_rec)
                
                # Double-check: Explicitly convert all numeric fields to ensure they're float
                numeric_fields = ['amount', 'quantity', 'current_price', 'target_price', 'current_weight', 'target_weight', 'future_weight']
                for field in numeric_fields:
                    if field in moved_rec and moved_rec[field] is not None:
                        moved_rec[field] = self._safe_float_convert(moved_rec[field])
                
                moved_securities.append(moved_rec)
                self.logger.info(f"Moving security {security_id_int} from Section 2/3 to Section 1")
        
        # Step 3: Append moved securities to existing Section 1 (NO REMOVAL, NO RECALCULATION)
        updated_section1 = existing_section1 + moved_securities
        
        self.logger.info(f"Move-to-section1: {len(existing_section1)} existing (preserved) + {len(moved_securities)} moved = {len(updated_section1)} total")
        
        # Step 4: Save to database (incremental - only adds new, preserves existing)
        if rec_session:
            self.data_service.save_section1_to_db(
                session_id,
                updated_section1,
                rec_session.created_by
            )
        
        # Step 5: Regenerate Sections 2 and 3 (excluding all Section 1, filtered by asset_class)
        all_section1_ids = {r['security_id'] for r in updated_section1}
        sections_2_3 = self.data_service._generate_sections_2_3(
            state['client_id'],
            all_section1_ids,
            state['current_state'],
            state['investment_amount'],
            asset_class=asset_class
        )
        
        # Convert all Decimal values to float for JSON serialization
        from decimal import Decimal
        
        def convert_decimals(obj):
            """Recursively convert Decimal to float - handles all nested structures"""
            if isinstance(obj, Decimal):
                return float(obj)
            elif isinstance(obj, dict):
                return {k: convert_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_decimals(item) for item in obj]
            elif isinstance(obj, tuple):
                return tuple(convert_decimals(item) for item in obj)
            elif hasattr(obj, '__dict__'):
                # Handle objects with attributes
                return {k: convert_decimals(v) for k, v in obj.__dict__.items()}
            else:
                return obj
        
        # Convert all sections to ensure no Decimal values remain
        safe_section1 = convert_decimals(updated_section1)
        safe_section2 = convert_decimals(sections_2_3['section_2'])
        safe_section3 = convert_decimals(sections_2_3['section_3'])
        
        # Additional safety: Ensure common numeric fields are explicitly float
        for rec in safe_section1:
            for field in ['amount', 'quantity', 'current_price', 'target_price', 'current_weight', 'target_weight', 'future_weight']:
                if field in rec and rec[field] is not None:
                    try:
                        rec[field] = float(rec[field])
                    except (ValueError, TypeError):
                        rec[field] = 0.0
        
        return {
            'success': True,
            'section_1_recommended': safe_section1,
            'section_2_hot_stocks': safe_section2,
            'section_3_other': safe_section3,
            'moved_count': len(moved_securities)
        }
    
    def recalculate_exclusions(self, session_id: int, 
                               current_section_1: List[Dict]) -> Dict:
        """
        Recalculate after exclusions - Redistribute funds among existing Section 1 securities
        
        Args:
            session_id: RecommendationSession ID
            current_section_1: List of checked securities (only those that should remain)
            
        Returns:
            Dict with redistributed Section 1 and preserved Sections 2, 3
        """
        # Get current state
        state = self.data_service.get_recommendation_session_state(session_id)
        
        # Get client and model for redistribution
        client = Client.query.get(state['client_id'])
        model_assignment = self.service._get_client_models(client)
        
        # Redistribute funds among remaining Section 1 securities
        # Use the full investment_amount for redistribution
        redistributed_section_1 = self._redistribute_funds_among_section1(
            current_section_1,
            state['client_id'],
            state['current_state'],
            model_assignment,
            investment_amount=state['investment_amount'],
            asset_recommendations=state.get('asset_recommendations', [])
        )
        
        # Preserve Sections 2 and 3 - they should NOT be affected by recalculate
        existing_section_2 = state['section_2_hot_stocks']
        existing_section_3 = state['section_3_other']
        
        # If Sections 2 or 3 are empty, regenerate them
        # Note: asset_class filtering should be preserved from session, but we don't have it here
        # The route handler should pass it if needed
        if not existing_section_2 or not existing_section_3:
            section_1_ids = {int(rec.get('security_id')) for rec in redistributed_section_1 if rec.get('security_id')}
            sections_2_3 = self.data_service._generate_sections_2_3(
                state['client_id'],
                section_1_ids,
                state['current_state'],
                state['investment_amount'],
                asset_class=None  # Will be filtered by route handler if needed
            )
            existing_section_2 = sections_2_3['section_2']
            existing_section_3 = sections_2_3['section_3']
        
        # Save updated Section 1 to database
        rec_session = RecommendationSession.query.get(session_id)
        if rec_session:
            self.data_service.save_section1_to_db(
                session_id,
                redistributed_section_1,
                rec_session.created_by
            )
        
        return {
            'success': True,
            'section_1_recommended': redistributed_section_1,
            'section_2_hot_stocks': existing_section_2,
            'section_3_other': existing_section_3
        }
    
    def _redistribute_funds_among_section1(self, section_1_securities: List[Dict],
                                          client_id: int, current_state: Dict,
                                          model_assignment: Optional[ModelAssignment] = None,
                                          investment_amount: float = 0.0,
                                          asset_recommendations: List[Dict] = None) -> List[Dict]:
        """
        Redistribute funds among existing Section 1 securities based on allocation priority.
        
        Rules:
        1. Only redistribute among existing Section 1 securities (no new securities)
        2. Distribution follows allocation priority (hot stocks > model stocks > others)
        3. Each security gets at least 5% of total funds allocated
        4. Remaining funds distributed proportionally based on target weights and priority
        5. User-modified entries are preserved and not changed by the system.
        
        Args:
            section_1_securities: List of existing Section 1 recommendations
            client_id: Client ID
            current_state: Current portfolio state
            model_assignment: Model assignment for target weights
            
        Returns:
            Updated Section 1 recommendations with redistributed amounts
        """
        if not section_1_securities:
            return []
        
        # Separate user-modified from auto-calculated entries
        user_modified_securities = []
        auto_calculated_securities = []
        
        for rec in section_1_securities:
            # Check if user-modified (from flag or database)
            is_user_modified = rec.get('is_user_modified', False)
            if not is_user_modified:
                # Check database
                security_id = rec.get('security_id')
                if security_id:
                    db_rec = Recommendation.query.filter_by(
                        session_id=rec.get('session_id'),
                        security_id=security_id
                    ).first()
                    if db_rec and db_rec.is_user_modified:
                        is_user_modified = True
            
            if is_user_modified:
                user_modified_securities.append(rec)  # PRESERVE exactly as-is
            else:
                auto_calculated_securities.append(rec)  # Can redistribute
        
        self.logger.info(f"Redistribution: {len(user_modified_securities)} user-modified (preserved), {len(auto_calculated_securities)} auto-calculated (redistributed)")
        
        # If no auto-calculated securities, return original
        if not auto_calculated_securities:
            return section_1_securities
        
        # Calculate total funds to redistribute
        # ALWAYS use investment_amount if provided (even if 0, it means no new investment)
        # Only fall back to summing existing amounts if investment_amount is None/not provided
        from decimal import Decimal
        if investment_amount is not None and investment_amount >= 0:
            # Use full investment amount for redistribution - ensure it's float
            total_funds = self._safe_float_convert(investment_amount)
            self.logger.info(f"Redistributing full investment_amount: {total_funds:.2f} (provided: {investment_amount})")
        else:
            # Fallback: sum existing amounts from auto-calculated entries - ensure all are float
            total_funds = sum(self._safe_float_convert(rec.get('amount', 0)) for rec in auto_calculated_securities)
            self.logger.info(f"Redistributing existing amounts: {total_funds:.2f} (investment_amount not provided)")
        
        if total_funds <= 0:
            self.logger.warning(f"No funds to redistribute (investment_amount={investment_amount}, total_funds={total_funds})")
            return section_1_securities
        
        # Minimum allocation per security (5% of total)
        MIN_ALLOCATION_PERCENT = 0.05
        min_allocation_per_security = total_funds * MIN_ALLOCATION_PERCENT
        
        # Get model securities for target weights
        model_securities = {}
        if model_assignment and model_assignment.stock_model:
            for allocation in model_assignment.stock_model.security_allocations:
                model_securities[allocation.security_id] = float(allocation.allocation_percentage)
        
        # Enrich auto-calculated securities with priority and target weights
        enriched_securities = []
        for rec in auto_calculated_securities:
            security_id = rec.get('security_id')
            if not security_id:
                continue
            
            security = Security.query.get(security_id)
            if not security:
                continue
            
            # Determine priority
            priority = 999  # Default (lowest)
            is_hot_stock = getattr(security, 'is_hot_stock', False)
            target_weight = model_securities.get(security_id, 0)
            current_weight = rec.get('current_weight', 0)
            
            # Priority 1: Hot stocks underallocated
            if is_hot_stock and current_weight < target_weight * 0.6:
                priority = 1
            # Priority 2: Hot stocks allocated
            elif is_hot_stock:
                priority = 2
            # Priority 3: Model stocks underallocated
            elif target_weight > 0 and current_weight < target_weight * 0.6:
                priority = 3
            # Priority 4: Model stocks allocated
            elif target_weight > 0:
                priority = 4
            
            enriched_securities.append({
                'rec': rec,
                'security_id': security_id,
                'security': security,
                'priority': priority,
                'target_weight': target_weight,
                'current_weight': current_weight,
                'is_hot_stock': is_hot_stock,
                'current_price': self._safe_float_convert(security.current_price if security and security.current_price else rec.get('current_price', 0))
            })
        
        # Sort by priority
        enriched_securities.sort(key=lambda x: x['priority'])
        
        # Step 1: Allocate minimum 5% to each security
        allocated_amounts = {}
        remaining_funds = float(total_funds)  # Ensure float
        
        for enriched in enriched_securities:
            security_id = enriched['security_id']
            allocated_amounts[security_id] = float(min(min_allocation_per_security, remaining_funds))
            remaining_funds -= min_allocation_per_security
        
        # If we don't have enough for minimum, distribute proportionally
        if remaining_funds < 0:
            total_priority_weight = sum(1.0 / (enriched['priority'] + 1) for enriched in enriched_securities)
            for enriched in enriched_securities:
                security_id = enriched['security_id']
                priority_weight = 1.0 / (enriched['priority'] + 1)
                allocated_amounts[security_id] = float(total_funds * (priority_weight / total_priority_weight))
            remaining_funds = 0
        
        # Step 2: Distribute remaining funds based on priority and target weights
        # Use a more balanced approach that doesn't give all funds to highest priority
        if remaining_funds > 0:
            allocation_weights = {}
            total_weight = 0.0
            
            # Maximum allocation per security (30% of total funds to prevent skewing)
            MAX_ALLOCATION_PERCENT = 0.30
            max_allocation_per_security = float(total_funds) * MAX_ALLOCATION_PERCENT
            
            for enriched in enriched_securities:
                security_id = enriched['security_id']
                priority = enriched['priority']
                target_weight = enriched['target_weight']
                
                # More balanced weighting:
                # 1. Priority factor: Use a gentler curve (sqrt instead of 1/x)
                #    This reduces the dominance of highest priority stocks
                # 2. Target weight factor: Use actual target weight (normalized)
                # 3. Combine both factors with equal importance
                
                # Priority factor: sqrt(1/(priority+1)) gives gentler curve
                # Priority 1: sqrt(1/2) = 0.707
                # Priority 2: sqrt(1/3) = 0.577
                # Priority 3: sqrt(1/4) = 0.500
                # Priority 4: sqrt(1/5) = 0.447
                priority_factor = (1.0 / (priority + 1)) ** 0.5  # Square root for gentler curve
                
                # Target weight factor: Use actual target weight, minimum 0.1%
                target_weight_factor = max(target_weight, 0.1)
                
                # Combine factors: 60% priority, 40% target weight (more balanced)
                # This ensures priority matters but doesn't dominate
                weight = (0.6 * priority_factor) + (0.4 * (target_weight_factor / 100.0))
                
                allocation_weights[security_id] = weight
                total_weight += weight
            
            # Distribute remaining funds proportionally with max cap
            if total_weight > 0:
                for enriched in enriched_securities:
                    security_id = enriched['security_id']
                    weight = float(allocation_weights[security_id])
                    additional_allocation = float(remaining_funds) * (weight / float(total_weight))
                    
                    # Apply max cap to prevent any single stock from getting too much
                    # CRITICAL: Ensure all values are float before arithmetic
                    current_total = float(allocated_amounts.get(security_id, 0)) + float(additional_allocation)
                    if current_total > max_allocation_per_security:
                        additional_allocation = max(0.0, float(max_allocation_per_security) - float(allocated_amounts.get(security_id, 0)))
                    
                    allocated_amounts[security_id] = float(allocated_amounts.get(security_id, 0)) + float(additional_allocation)
            
            # If there's still remaining funds after caps, redistribute proportionally
            # Calculate how much was actually allocated vs how much we tried to allocate
            total_allocated_after_caps = sum(float(allocated_amounts.get(enriched['security_id'], 0)) for enriched in enriched_securities)
            remaining_after_caps = float(total_funds) - total_allocated_after_caps
            
            if remaining_after_caps > 0.01:  # Only redistribute if there's meaningful amount left
                # Redistribute remaining funds equally among all securities that aren't at max
                securities_not_at_max = [
                    enriched for enriched in enriched_securities
                    if allocated_amounts.get(enriched['security_id'], 0) < max_allocation_per_security
                ]
                
                if securities_not_at_max:
                    per_security = float(remaining_after_caps) / len(securities_not_at_max)
                    for enriched in securities_not_at_max:
                        security_id = enriched['security_id']
                        current_allocation = float(allocated_amounts.get(security_id, 0))
                        add_amount = min(float(per_security), float(max_allocation_per_security) - current_allocation)
                        allocated_amounts[security_id] = float(current_allocation + add_amount)
        
        # Step 3: Update auto-calculated recommendations with new amounts
        # Format properly with weights and reasons using UnifiedRecommendationService
        updated_auto_calculated = []
        for enriched in enriched_securities:
            rec = enriched['rec'].copy()
            security_id = enriched['security_id']
            security = enriched['security']
            # CRITICAL: Ensure allocated_amount is float before any operations
            allocated_amount_raw = allocated_amounts.get(security_id, 0)
            allocated_amount = self._safe_float_convert(allocated_amount_raw)
            
            # CRITICAL: Ensure current_price is float before any operations
            from decimal import Decimal
            current_price_raw = enriched['current_price']
            current_price = self._safe_float_convert(current_price_raw)
            
            # Use _format_security_recommendation to get all fields properly populated
            formatted_rec = self.service._format_security_recommendation(
                security=security,
                current_state=current_state,
                target_weight=enriched['target_weight'],
                amount=allocated_amount,
                action='BUY',
                reason='Recalculated after exclusions',
                is_recommended=True,
                section='1',
                investment_amount=investment_amount if investment_amount > 0 else total_funds,
                client_id=client_id
            )
            
            # Override with redistributed values
            # CRITICAL: Ensure allocated_amount and current_price are both float before any operations
            allocated_amount_float = self._safe_float_convert(allocated_amount)
            current_price_float = self._safe_float_convert(current_price)
            
            formatted_rec['amount'] = round(allocated_amount_float, 2)
            formatted_rec['quantity'] = int(allocated_amount_float / current_price_float) if current_price_float > 0 else 0
            # Recalculate amount based on whole-number quantity
            if current_price_float > 0 and formatted_rec['quantity'] > 0:
                formatted_rec['amount'] = round(float(formatted_rec['quantity']) * current_price_float, 2)
            
            # Ensure action is BUY
            formatted_rec['action'] = 'BUY'
            
            # Mark as NOT user-modified (system calculated)
            formatted_rec['is_user_modified'] = False
            
            # Ensure all required fields are present
            formatted_rec['security_id'] = security_id
            # CRITICAL: Ensure current_price is always float
            formatted_rec['current_price'] = self._safe_float_convert(current_price)
            formatted_rec['target_price'] = self._safe_float_convert(current_price)
            
            updated_auto_calculated.append(formatted_rec)
        
        # Combine: user-modified (preserved) + redistributed auto-calculated
        final_recommendations = user_modified_securities + updated_auto_calculated
        
        self.logger.info(f"Redistributed funds: Total={total_funds:.2f}, Auto-calculated={len(updated_auto_calculated)}, User-modified preserved={len(user_modified_securities)}")
        
        return final_recommendations
    
    def add_security_recommendation(self, session_id: int, security_id: int,
                                   action: str, quantity: float, amount: float,
                                   created_by: int) -> Dict:
        """Add user-selected security to Section 1"""
        from services.recommendation_data_service import RecommendationDataService
        
        rec_session = RecommendationSession.query.get_or_404(session_id)
        security = Security.query.get_or_404(security_id)
        
        # Create recommendation
        recommendation = Recommendation(
            client_id=rec_session.client_id,
            security_id=security_id,
            session_id=session_id,
            action=action,
            quantity=quantity,
            amount=amount,
            target_price=float(security.current_price) if security.current_price else 0,
            is_user_modified=True,
            created_by=created_by,
            notes=f"User-added recommendation"
        )
        
        db.session.add(recommendation)
        db.session.commit()
        
        return {
            'success': True,
            'recommendation_id': recommendation.id
        }
    
    def delete_security_recommendation(self, recommendation_id: int) -> bool:
        """Delete security from recommendations"""
        recommendation = Recommendation.query.get_or_404(recommendation_id)
        db.session.delete(recommendation)
        db.session.commit()
        return True
    
    def bulk_update_recommendations(self, session_id: int, updates: List[Dict]) -> Dict:
        """Bulk update multiple recommendations"""
        updated_count = 0
        
        for update in updates:
            recommendation_id = update.get('recommendation_id')
            if not recommendation_id:
                continue
            
            recommendation = Recommendation.query.get(recommendation_id)
            if not recommendation or recommendation.session_id != session_id:
                continue
            
            # Update fields
            if 'quantity' in update:
                recommendation.quantity = update['quantity']
            if 'amount' in update:
                recommendation.amount = update['amount']
            if 'action' in update:
                recommendation.action = update['action']
            if 'is_user_modified' in update:
                recommendation.is_user_modified = update['is_user_modified']
            
            updated_count += 1
        
        db.session.commit()
        
        return {
            'success': True,
            'updated_count': updated_count
        }

