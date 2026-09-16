"""
Two-Stage Recommendation API Module

This module provides a clean, centralized API for all recommendation-related operations.
It implements a two-stage process:
1. Asset Allocation: Portfolio-level distribution based on asset allocation model
2. Security Allocation: Security-level distribution within each asset class

Asset Class Thresholds:
- Equity: Only act if overallocation > 10%
- Fixed Income, REITs, Gold: Act immediately on any overallocation
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

class RecommendationType(Enum):
    INVESTMENT = "investment"
    WITHDRAWAL = "withdrawal"
    REBALANCE = "rebalance"

class AssetClassType(Enum):
    EQUITY = "Equity"
    FIXED_INCOME = "Fixed Income"
    GOLD = "Gold"
    REITS = "REITs"

@dataclass
class SecurityRecommendation:
    """Standardized security recommendation structure"""
    security_id: int
    symbol: str
    security_name: str
    asset_class: str
    action: str  # BUY, SELL, HOLD
    amount: float
    quantity: int
    current_quantity: int
    new_quantity: int
    current_price: float
    current_weight: float
    target_weight: float
    future_weight: float
    reason: str
    priority: int

class AssetAllocationAPI:
    """Handles portfolio-level asset allocation based on asset allocation model"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        # Import here to avoid circular imports
        from unified_recommendation_service import UnifiedRecommendationService
        self.service = UnifiedRecommendationService()
    
    def _infer_recommendation_type(self, investment_amount: float) -> str:
        """Infer recommendation type from investment amount"""
        if investment_amount > 0:
            return 'investment'
        elif investment_amount < 0:
            return 'withdrawal'
        else:
            return 'rebalance'
    
    def calculate_asset_allocation(
        self, 
        client_id: int, 
        investment_amount: float
    ) -> Dict:
        """
        Stage 1: Calculate target allocation for each asset class
        
        Args:
            client_id: Client ID
            investment_amount: Positive for investment, negative for withdrawal
            
        Returns:
            Dict with target allocations for each asset class
        """
        try:
            self.logger.info(f"Stage 1: Calculating asset allocation for client {client_id}, amount: {investment_amount}")
            
            # Determine recommendation type from amount
            recommendation_type = self._infer_recommendation_type(investment_amount)
            
            # 1. Get current portfolio state
            current_state = self._get_client_portfolio_state(client_id)
            total_portfolio_value = current_state.get('total_portfolio_value', 0)
            
            # 2. Get asset allocation model for client (same defaults/repair as UnifiedRecommendationService)
            model_assignment = self._get_client_models(client_id)
            if not model_assignment:
                return {
                    'error': 'No model assignment could be loaded for this client. Assign models on the client profile or ensure reference data (asset classes, allocation models) exists.',
                }
            if not model_assignment.asset_model:
                return {
                    'error': 'This client has no asset allocation model. Assign an asset model on the client profile.',
                }
            asset_allocation_model = model_assignment.asset_model
            if not asset_allocation_model.asset_allocations:
                return {
                    'error': f"The assigned asset allocation model has no asset class weights (model id={asset_allocation_model.id}). Fix the model in Maintenance → Reference data.",
                }

            # 3. Calculate model-covered portfolio value (alias-aware: Debt ↔ Fixed Income, etc.)
            from utils.asset_allocation import (
                action_from_required_change,
                lookup_allocation_value,
                sum_model_covered_value,
            )
            model_asset_classes = {alloc.asset_class.name for alloc in asset_allocation_model.asset_allocations}
            current_asset_allocations = current_state.get('current_asset_allocations', {})
            model_covered_value = sum_model_covered_value(current_asset_allocations, model_asset_classes)
            
            # 4. Calculate target portfolio value for model-covered assets only
            target_model_covered_value = model_covered_value + investment_amount
            
            # Debug logging
            self.logger.info(f"Portfolio Analysis - Total: ₹{total_portfolio_value:,.2f}, Model-Covered: ₹{model_covered_value:,.2f}, Investment: ₹{investment_amount:,.2f}, Target: ₹{target_model_covered_value:,.2f}")
            self.logger.info(f"Model Asset Classes: {model_asset_classes}")
            
            # 5. Calculate target allocation for each asset class
            asset_allocations = []
            
            # Debug: Log available asset class keys
            self.logger.debug(f"Available asset class keys in current_asset_allocations: {list(current_asset_allocations.keys())}")
            
            for allocation in asset_allocation_model.asset_allocations:
                asset_class = allocation.asset_class
                target_weight = allocation.allocation_percentage
                
                # Calculate target value for this asset class (based on model-covered value only)
                target_value = (float(target_weight) / 100.0) * target_model_covered_value
                
                current_value = lookup_allocation_value(asset_class.name, current_asset_allocations)
                
                # If still 0, log warning
                if current_value == 0:
                    self.logger.warning(f"No current value found for asset class '{asset_class.name}'. Available keys: {list(current_asset_allocations.keys())}")
                
                # Calculate required change
                required_change = target_value - current_value
                
                # Debug logging
                self.logger.info(f"Asset: {asset_class.name}, Current: {current_value}, Target: {target_value}, Change: {required_change}")
                
                allocation_action = action_from_required_change(required_change)
                
                self.logger.info(f"Asset: {asset_class.name}, Action: {allocation_action}")
                
                asset_allocations.append({
                    'asset_class': asset_class.name,
                    'current_value': current_value,
                    'target_value': target_value,
                    'current_weight': (current_value / model_covered_value * 100) if model_covered_value > 0 else 0,
                    'target_weight': target_weight,
                    'required_change': required_change,
                    'action': allocation_action,
                    'should_allocate': allocation_action != 'HOLD'
                })
            
            self.logger.info(f"Stage 1 complete: {len([a for a in asset_allocations if a['should_allocate']])} asset classes need allocation")
            
            return {
                'client_id': client_id,
                'investment_amount': investment_amount,
                'total_portfolio_value': total_portfolio_value,
                'model_covered_value': model_covered_value,
                'target_model_covered_value': target_model_covered_value,
                'target_portfolio_value': target_model_covered_value,  # For backward compatibility
                'asset_allocations': asset_allocations
            }
            
        except Exception as e:
            self.logger.error(f"Error in Stage 1 (Asset Allocation): {e}")
            return {'error': str(e)}
    
    def _determine_allocation_action(
        self, 
        asset_class: str, 
        current_value: float, 
        target_value: float, 
        required_change: float
    ) -> str:
        """Determine if we should allocate to this asset class based on thresholds"""
        
        # For new investments (positive required_change), allow even small amounts
        if required_change > 0:
            # Allow buy recommendations even for small amounts (minimum ₹1)
            if abs(required_change) < 1:
                return 'HOLD'
            else:
                return 'BUY'
        elif required_change < 0:
            # For sell recommendations, use higher threshold to avoid unnecessary sells
            if abs(required_change) < 1000:  # Higher threshold for withdrawals
                return 'HOLD'
            else:
                return 'SELL'
        
        # Default to HOLD if no change needed
        return 'HOLD'
    
    @staticmethod
    def _derive_asset_class(security):
        """Derive asset class from security properties (shared utils; read-only)."""
        from utils.security_asset_class import derive_asset_class
        return derive_asset_class(security)
    
    def _get_client_portfolio_state(self, client_id: int) -> Dict:
        """Get current portfolio state for a client"""
        try:
            from models import Client, Portfolio, Holding
            from datetime import datetime
            
            client = Client.query.get_or_404(client_id)
            
            # Get or create portfolio
            portfolio = Portfolio.query.filter_by(client_id=client.id, status='active').first()
            if not portfolio:
                portfolio = Portfolio(
                    client_id=client.id,
                    name=f"{client.name}'s Portfolio",
                    created_at=datetime.utcnow(),
                    status='active'
                )
                from extensions import db
                db.session.add(portfolio)
                db.session.commit()
            
            # Analyze current portfolio state
            current_holdings = {}
            total_portfolio_value = 0.0
            
            # Get current holdings - use client.holdings instead of portfolio.holdings
            for holding in client.holdings:
                if holding.security and holding.security.current_price:
                    try:
                        quantity = float(holding.quantity) if holding.quantity else 0.0
                        current_price = float(holding.security.current_price) if holding.security.current_price else 0.0
                        current_value = quantity * current_price
                        total_portfolio_value += current_value
                    except Exception as e:
                        self.logger.error(f"Error processing holding: {e}")
                        continue
                    
                    current_holdings[holding.security_id] = {
                        'security_id': holding.security_id,
                        'security_name': holding.security.name,
                        'symbol': holding.security.symbol,
                        'asset_class': self._derive_asset_class(holding.security),
                        'quantity': quantity,
                        'current_price': current_price,
                        'current_value': current_value,
                        'current_weight': 0  # Will be calculated after total is known
                    }
            
            # Calculate current weights
            for holding_data in current_holdings.values():
                if total_portfolio_value > 0:
                    holding_data['current_weight'] = (float(holding_data['current_value']) / float(total_portfolio_value)) * 100
            
            # Calculate current asset allocations
            current_asset_allocations = {}
            for holding_data in current_holdings.values():
                asset_class_name = holding_data['asset_class']
                if asset_class_name not in current_asset_allocations:
                    current_asset_allocations[asset_class_name] = 0
                current_asset_allocations[asset_class_name] += holding_data['current_value']
            
            return {
                'total_portfolio_value': total_portfolio_value,
                'current_holdings': current_holdings,
                'current_asset_allocations': current_asset_allocations,
                'current_asset_weights': {
                    asset: (float(value) / float(total_portfolio_value) * 100) if total_portfolio_value > 0 else 0
                    for asset, value in current_asset_allocations.items()
                }
            }
        except Exception as e:
            self.logger.error(f"Error getting portfolio state for client {client_id}: {e}")
            return {}
    
    def _get_client_models(self, client_id: int):
        """Resolve ModelAssignment for a client, including default creation and repair of missing models."""
        try:
            from models import Client

            client = Client.query.get(client_id)
            if not client:
                self.logger.error(f"Client {client_id} not found")
                return None
            return self.service._get_client_models(client)
        except Exception as e:
            self.logger.error(f"Error getting models for client {client_id}: {e}", exc_info=True)
            return None

class BaseSecurityHandler:
    """Base class for asset class-specific security allocation logic"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        from unified_recommendation_service import UnifiedRecommendationService
        self.service = UnifiedRecommendationService()
    
    def calculate_security_recommendations(
        self, 
        client_id: int, 
        asset_class: str, 
        required_change: float, 
        target_value: float
    ) -> List[Dict]:
        """Calculate security recommendations for this asset class"""
        pass

class EquitySecurityHandler(BaseSecurityHandler):
    """Equity-specific security allocation logic"""
    
    def calculate_security_recommendations(self, client_id, asset_class, required_change, target_value, current_asset_value):
        """Equity-specific logic - simplified approach like Fixed Income"""
        try:
            self.logger.info(f"Calculating equity security recommendations for client {client_id}, change: {required_change}")
            self.logger.info(f"Parameters - asset_class: {asset_class}, current_asset_value: {current_asset_value}, target_value: {target_value}")
            
            from models import Client, AssetClass, SecurityAllocationModel, Security, Holding
            
            # Get client and current holdings
            client = Client.query.get_or_404(client_id)
            current_holdings = Holding.query.filter_by(client_id=client_id).all()
            
            # Always recalculate equity total using derived asset class to ensure accuracy
            # This ensures we're using the same equity total calculation as the asset allocation screen
            recalculated_equity_total = 0.0
            for holding in current_holdings:
                if holding.security and holding.security.current_price:
                    derived_ac = self._derive_asset_class(holding.security)
                    if derived_ac == asset_class:  # Match the asset class we're processing
                        quantity = float(holding.quantity) if holding.quantity else 0.0
                        current_price = float(holding.security.current_price) if holding.security.current_price else 0.0
                        recalculated_equity_total += quantity * current_price
            
            # Always use the recalculated value to ensure consistency
            if recalculated_equity_total > 0:
                if abs(recalculated_equity_total - current_asset_value) > 100:  # More than ₹100 difference
                    self.logger.warning(f"current_asset_value mismatch: passed={current_asset_value}, recalculated={recalculated_equity_total}. Using recalculated value.")
                current_asset_value = recalculated_equity_total
                self.logger.info(f"Using recalculated {asset_class} total: {current_asset_value} (was passed: {current_asset_value})")
            else:
                self.logger.warning(f"Could not recalculate {asset_class} total, using passed value: {current_asset_value}")
            
            self.logger.info(f"EQUITY CALCULATION DEBUG: asset_class={asset_class}, current_asset_value={current_asset_value}, recalculated_total={recalculated_equity_total}")
            
            # Get Equity asset class
            equity_asset_class = AssetClass.query.filter_by(name='Equity').first()
            if not equity_asset_class:
                self.logger.warning("Equity asset class not found in database")
                return []
            
            # Get Equity security model
            equity_model = SecurityAllocationModel.query.filter_by(asset_class_id=equity_asset_class.id).first()
            if not equity_model or not equity_model.security_allocations:
                self.logger.warning("No Equity security model or allocations found")
                return []
            
            recommendations = []
            
            # For Equity: compute per-security delta vs target value
            model_security_ids = set(a.security_id for a in equity_model.security_allocations)
            for allocation in equity_model.security_allocations:
                security = Security.query.get(allocation.security_id)
                if not security:
                    continue
                
                # Calculate target value for this security based on security weight and asset target_value
                security_target_value = (float(allocation.allocation_percentage) / 100.0) * float(target_value)
                
                # Get current holding
                current_holding = next((h for h in current_holdings if h.security_id == security.id), None)
                current_quantity = float(current_holding.quantity) if current_holding else 0.0
                current_price = float(security.current_price)
                current_value = current_quantity * current_price if current_holding else 0.0
                
                # Compute delta to target and derive signed quantity change
                delta_value = security_target_value - current_value
                quantity_change = 0
                new_quantity = int(current_quantity)
                action = 'HOLD'
                if current_price > 0:
                    quantity_change = int(round(delta_value / current_price))
                    if quantity_change > 0:
                        new_quantity = int(current_quantity) + quantity_change
                        action = 'BUY'
                    elif quantity_change < 0:
                        # Cap sell to available quantity
                        sell_qty = min(abs(quantity_change), int(current_quantity))
                        quantity_change = -sell_qty
                        new_quantity = int(current_quantity) + quantity_change
                        action = 'SELL'

                # Amount should be quantity_change * current_price (signed)
                actual_amount = quantity_change * current_price
                
                # Recommend only if there is a quantity change
                if quantity_change != 0:
                    calculated_weight = round(float((current_value / float(current_asset_value) * 100) if current_asset_value > 0 else 0), 1)
                    self.logger.info(f"WEIGHT CALC: {security.symbol} - value={current_value}, equity_total={current_asset_value}, weight={calculated_weight}%")
                    recommendations.append({
                        'security_id': security.id,
                        'security_name': security.name,
                        'symbol': security.symbol,
                        'asset_class': 'Equity',
                        'amount': round(float(actual_amount), 1),
                        'quantity': int(quantity_change),
                        'current_quantity': int(current_quantity),
                        'new_quantity': int(new_quantity),
                        'current_price': round(current_price, 1),
                        'action': action,
                        'reason': f'Target {round(float(allocation.allocation_percentage), 1)}% of asset class',
                        'priority': 1,
                        'current_weight': calculated_weight,
                        'target_weight': round(float(allocation.allocation_percentage), 1),
                        'future_weight': round(float(((new_quantity * current_price) / float(target_value) * 100) if target_value > 0 else 0), 1)
                    })

            # Additionally: for any Equity holding NOT in the model, propose SELL of full holding
            for holding in current_holdings:
                try:
                    if not holding.security:
                        continue
                    if holding.security.asset_class and holding.security.asset_class.name != 'Equity':
                        continue
                    if holding.security_id in model_security_ids:
                        continue
                    current_quantity = float(holding.quantity) if holding.quantity else 0.0
                    if current_quantity <= 0:
                        continue
                    current_price = float(holding.security.current_price) if holding.security.current_price else 0.0
                    current_value = current_quantity * current_price
                    quantity_change = -int(current_quantity)
                    new_quantity = 0
                    actual_amount = quantity_change * current_price
                    recommendations.append({
                        'security_id': holding.security_id,
                        'security_name': holding.security.name,
                        'symbol': holding.security.symbol,
                        'asset_class': 'Equity',
                        'amount': round(float(actual_amount), 1),
                        'quantity': int(quantity_change),
                        'current_quantity': int(current_quantity),
                        'new_quantity': int(new_quantity),
                        'current_price': round(current_price, 1),
                        'action': 'SELL',
                        'reason': 'Not in Equity model - propose exit',
                        'priority': 1,
                        'current_weight': round(float((current_value / float(current_asset_value) * 100) if current_asset_value > 0 else 0), 1),
                        'target_weight': 0.0,
                        'future_weight': 0.0
                    })
                except Exception as e:
                    self.logger.error(f"Error creating non-model SELL for holding {getattr(holding, 'security_id', 'unknown')}: {e}")
            
            self.logger.info(f"Equity: Generated {len(recommendations)} recommendations")
            return recommendations
            
        except Exception as e:
            self.logger.error(f"Error calculating equity recommendations: {e}")
            return []

class FixedIncomeSecurityHandler(BaseSecurityHandler):
    """Fixed Income-specific security allocation logic"""
    
    def calculate_security_recommendations(self, client_id, asset_class, required_change, target_value, current_asset_value):
        """Fixed Income-specific logic - Clean API approach"""
        try:
            self.logger.info(f"Calculating Fixed Income security recommendations for client {client_id}, change: {required_change}")
            
            from models import Client, AssetClass, SecurityAllocationModel, Security, Holding
            
            # Get client and current holdings
            client = Client.query.get_or_404(client_id)
            current_holdings = Holding.query.filter_by(client_id=client_id).all()
            
            # Get Fixed Income asset class
            fixed_income_asset_class = AssetClass.query.filter_by(name='Fixed Income').first()
            if not fixed_income_asset_class:
                self.logger.warning("Fixed Income asset class not found in database")
                return []
            
            # Get Fixed Income security model
            fixed_income_model = SecurityAllocationModel.query.filter_by(asset_class_id=fixed_income_asset_class.id).first()
            if not fixed_income_model or not fixed_income_model.security_allocations:
                self.logger.warning("No Fixed Income security model or allocations found")
                return []
            
            from utils.asset_allocation import action_from_required_change

            class_action = action_from_required_change(float(required_change or 0))
            if class_action == 'HOLD':
                self.logger.info("Fixed Income: required_change ~0, skipping default SELL/BUY rows")
                return []

            recommendations = []
            
            # For Fixed Income: Simple logic based on required change
            for allocation in fixed_income_model.security_allocations:
                security = Security.query.get(allocation.security_id)
                if not security:
                    continue
                
                # Calculate target amount based on model weight
                target_amount = (float(allocation.allocation_percentage) / 100.0) * abs(float(required_change))
                
                # Get current holding
                current_holding = next((h for h in current_holdings if h.security_id == security.id), None)
                current_quantity = float(current_holding.quantity) if current_holding else 0.0
                current_price = float(security.current_price)
                current_value = current_quantity * current_price if current_holding else 0.0
                
                if class_action == 'BUY':
                    quantity_to_buy = round(target_amount / current_price) if current_price else 0
                    new_quantity = current_quantity + quantity_to_buy
                    quantity_change = new_quantity - current_quantity  # positive
                    action = 'BUY'
                else:
                    quantity_to_sell = round(target_amount / current_price) if current_price else 0
                    actual_sell_qty = min(quantity_to_sell, int(current_quantity))
                    new_quantity = max(0, int(current_quantity) - actual_sell_qty)
                    quantity_change = new_quantity - int(current_quantity)  # negative or zero
                    action = 'SELL'

                # Amount derived from signed quantity_change
                actual_amount = quantity_change * current_price
                
                # Always recommend if there's a target amount, even if quantity change is small
                if target_amount > 0:  # Only recommend if there's a target amount
                    recommendations.append({
                        'security_id': security.id,
                        'security_name': security.name,
                        'symbol': security.symbol,
                        'asset_class': 'Fixed Income',
                        'amount': round(float(actual_amount), 1),
                        'quantity': int(quantity_change),
                        'current_quantity': int(current_quantity),
                        'new_quantity': int(new_quantity),
                        'current_price': round(current_price, 1),
                        'action': action,
                        'reason': f'Adjust Fixed Income allocation by {round(float(allocation.allocation_percentage), 1)}%',
                        'priority': 1,
                        'current_weight': round(float((current_value / float(current_asset_value) * 100) if current_asset_value > 0 else 0), 1),
                        'target_weight': round(float(allocation.allocation_percentage), 1),
                        'future_weight': round(float(((new_quantity * current_price) / float(target_value) * 100) if target_value > 0 else 0), 1)
                    })
            
            self.logger.info(f"Fixed Income: Generated {len(recommendations)} recommendations")
            return recommendations
            
        except Exception as e:
            self.logger.error(f"Error calculating Fixed Income recommendations: {e}")
            return []

class REITSecurityHandler(BaseSecurityHandler):
    """REIT-specific security allocation logic"""
    
    def calculate_security_recommendations(self, client_id, asset_class, required_change, target_value, current_asset_value):
        """REIT-specific logic - Clean API approach"""
        try:
            self.logger.info(f"Calculating REIT security recommendations for client {client_id}, change: {required_change}")
            
            from models import Client, AssetClass, SecurityAllocationModel, Security, Holding
            
            # Get client and current holdings
            client = Client.query.get_or_404(client_id)
            current_holdings = Holding.query.filter_by(client_id=client_id).all()
            
            # Get REIT asset class
            reit_asset_class = AssetClass.query.filter_by(name='REITs').first()
            if not reit_asset_class:
                self.logger.warning("REITs asset class not found in database")
                return []
            
            # Get REIT security model
            reit_model = SecurityAllocationModel.query.filter_by(asset_class_id=reit_asset_class.id).first()
            if not reit_model or not reit_model.security_allocations:
                self.logger.warning("No REIT security model or allocations found")
                return []
            
            recommendations = []
            
            # For REITs: Simple logic based on required change
            for allocation in reit_model.security_allocations:
                security = Security.query.get(allocation.security_id)
                if not security:
                    continue
                
                # Calculate target amount based on model weight
                target_amount = (float(allocation.allocation_percentage) / 100.0) * abs(float(required_change))
                
                # Get current holding
                current_holding = next((h for h in current_holdings if h.security_id == security.id), None)
                current_quantity = float(current_holding.quantity) if current_holding else 0.0
                current_price = float(security.current_price)
                current_value = current_quantity * current_price if current_holding else 0.0
                
                if required_change > 0:  # Need to buy more REITs
                    quantity_to_buy = round(target_amount / current_price)
                    new_quantity = current_quantity + quantity_to_buy
                    quantity_change = new_quantity - current_quantity
                    action = 'BUY'
                    actual_amount = quantity_to_buy * current_price
                else:  # Need to sell REITs
                    quantity_to_sell = round(target_amount / current_price)
                    new_quantity = max(0, current_quantity - quantity_to_sell)
                    quantity_change = new_quantity - current_quantity
                    action = 'SELL'
                    actual_amount = -quantity_to_sell * current_price  # Make negative for sell
                
                # Compute signed quantity_change and amount consistently
                if required_change > 0:
                    quantity_change = new_quantity - current_quantity
                    action = 'BUY'
                else:
                    # sell path: derive actual sell qty and new qty already computed
                    quantity_change = new_quantity - current_quantity  # negative or zero
                    action = 'SELL'

                actual_amount = quantity_change * current_price

                if quantity_change != 0:  # Only recommend if there's a change
                    recommendations.append({
                        'security_id': security.id,
                        'security_name': security.name,
                        'symbol': security.symbol,
                        'asset_class': 'REITs',
                        'amount': round(float(actual_amount), 1),
                        'quantity': int(quantity_change),
                        'current_quantity': int(current_quantity),
                        'new_quantity': int(new_quantity),
                        'current_price': round(current_price, 1),
                        'action': action,
                        'reason': f'Adjust REIT allocation by {round(float(allocation.allocation_percentage), 1)}%',
                        'priority': 1,
                        'current_weight': round(float((current_value / float(current_asset_value) * 100) if current_asset_value > 0 else 0), 1),
                        'target_weight': round(float(allocation.allocation_percentage), 1),
                        'future_weight': round(float(((new_quantity * current_price) / float(target_value) * 100) if target_value > 0 else 0), 1)
                    })
            
            self.logger.info(f"REITs: Generated {len(recommendations)} recommendations")
            return recommendations
            
        except Exception as e:
            self.logger.error(f"Error calculating REIT recommendations: {e}")
            return []

class GoldSecurityHandler(BaseSecurityHandler):
    """Gold-specific security allocation logic"""
    
    def calculate_security_recommendations(self, client_id, asset_class, required_change, target_value, current_asset_value):
        """Gold-specific logic - Clean API approach"""
        try:
            self.logger.info(f"Calculating Gold security recommendations for client {client_id}, change: {required_change}")
            
            from models import Client, AssetClass, SecurityAllocationModel, Security, Holding
            
            # Get client and current holdings
            client = Client.query.get_or_404(client_id)
            current_holdings = Holding.query.filter_by(client_id=client_id).all()
            
            # Get Gold asset class
            gold_asset_class = AssetClass.query.filter_by(name='Gold').first()
            if not gold_asset_class:
                self.logger.warning("Gold asset class not found in database")
                return []
            
            # Get Gold security model
            gold_model = SecurityAllocationModel.query.filter_by(asset_class_id=gold_asset_class.id).first()
            if not gold_model or not gold_model.security_allocations:
                self.logger.warning("No Gold security model or allocations found")
                return []
            
            recommendations = []
            
            # For Gold: Simple logic - if we need to buy more, recommend buying the Gold securities in the model
            if required_change > 0:  # Need to buy more Gold
                for allocation in gold_model.security_allocations:
                    security = Security.query.get(allocation.security_id)
                    if not security:
                        continue
                    
                    # Calculate how much to buy based on model weight
                    target_amount = (float(allocation.allocation_percentage) / 100.0) * float(required_change)
                    
                    # Get current holding
                    current_holding = next((h for h in current_holdings if h.security_id == security.id), None)
                    current_quantity = float(current_holding.quantity) if current_holding else 0.0
                    current_price = float(security.current_price)
                    current_value = current_quantity * current_price if current_holding else 0.0
                    
                    # Calculate new quantity needed (round to integer)
                    quantity_to_buy = round(target_amount / current_price)
                    new_quantity = current_quantity + quantity_to_buy
                    quantity_change = quantity_to_buy
                    
                    # Amount derived from signed quantity_change
                    actual_amount = quantity_change * current_price
                    
                    if quantity_change > 0:  # Only recommend if we need to buy more
                        recommendations.append({
                            'security_id': security.id,
                            'security_name': security.name,
                            'symbol': security.symbol,
                            'asset_class': 'Gold',
                            'amount': round(float(actual_amount), 1),
                            'quantity': int(quantity_change),
                            'current_quantity': int(current_quantity),
                            'new_quantity': int(new_quantity),
                            'current_price': round(current_price, 1),
                            'action': 'BUY',
                            'reason': f'Increase Gold allocation by {round(float(allocation.allocation_percentage), 1)}%',
                            'priority': 1,
                            'current_weight': round(float((current_value / float(current_asset_value) * 100) if current_asset_value > 0 else 0), 1),
                            'target_weight': round(float(allocation.allocation_percentage), 1),
                            'future_weight': round(float(((new_quantity * current_price) / float(target_value) * 100) if target_value > 0 else 0), 1)
                        })
            
            self.logger.info(f"Gold: Generated {len(recommendations)} recommendations")
            return recommendations
            
        except Exception as e:
            self.logger.error(f"Error calculating Gold recommendations: {e}")
            return []

class SecurityAllocationAPI:
    """Handles security-level allocation within each asset class"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        reit_handler = REITSecurityHandler()
        self.asset_handlers = {
            'Equity': EquitySecurityHandler(),
            'Fixed Income': FixedIncomeSecurityHandler(),
            'REITs': reit_handler,
            'REIT': reit_handler,
            'Gold': GoldSecurityHandler(),
        }

    def _resolve_handler_key(self, asset_class: str) -> str:
        if not asset_class:
            return asset_class
        normalized = asset_class.strip()
        reit_aliases = {'REIT', 'reits', 'Reits', 'reit', 'REITs'}
        if normalized in reit_aliases:
            return 'REITs'
        return normalized
    
    def generate_security_recommendations(
        self,
        client_id: int,
        asset_class: str,
        current_asset_value: float,
        change_amount: float
    ) -> Dict:
        """
        Standalone method to generate security recommendations for a specific asset class
        
        Args:
            client_id: Client ID
            asset_class: Asset class name ('Equity', 'Fixed Income', 'Gold', 'REITs')
            current_asset_value: Current value of the asset class
            change_amount: Amount to change (positive for buy, negative for sell)
            
        Returns:
            Dict with security recommendations for the specified asset class
        """
        try:
            self.logger.info(f"Generating security recommendations for client {client_id}, asset class: {asset_class}, current: {current_asset_value}, change: {change_amount}")
            
            # Calculate target value from current value + change amount
            target_value = current_asset_value + change_amount
            normalized_asset_class = self._resolve_handler_key(asset_class)
            
            # Create a mock asset allocation for this specific asset class
            asset_allocation = {
                'asset_class': normalized_asset_class,
                'target_value': target_value,
                'current_value': current_asset_value,
                'required_change': change_amount,
                'should_allocate': True  # Always allocate for standalone calls
            }
            
            # Use the existing logic
            result = self.calculate_security_allocations(client_id, [asset_allocation])
            
            # Get recommendations using normalized asset class key (result uses normalized key)
            security_recs = result.get('security_recommendations', {}).get(normalized_asset_class, [])
            
            return {
                'success': True,
                'asset_class': asset_class,
                'current_asset_value': current_asset_value,
                'change_amount': change_amount,
                'target_value': target_value,
                'security_recommendations': security_recs,
                'total_recommendations': len(security_recs)
            }
            
        except Exception as e:
            self.logger.error(f"Error generating security recommendations: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def calculate_security_allocations(
        self, 
        client_id: int, 
        asset_allocations: List[Dict]
    ) -> Dict:
        """
        Stage 2: Calculate security-level recommendations for each asset class
        
        Args:
            client_id: Client ID
            asset_allocations: Output from Stage 1
            
        Returns:
            Dict with security recommendations for each asset class
        """
        try:
            self.logger.info(f"Stage 2: Calculating security allocations for client {client_id}")
            
            security_recommendations = {}
            total_securities = 0
            
            for asset_allocation in asset_allocations:
                asset_class = self._resolve_handler_key(asset_allocation['asset_class'])
                required_change = asset_allocation['required_change']
                
                if not asset_allocation['should_allocate']:
                    self.logger.info(f"Skipping {asset_class} - no allocation needed")
                    continue
                
                # Get the appropriate handler for this asset class
                handler = self.asset_handlers.get(asset_class)
                if not handler:
                    self.logger.warning(f"No handler found for asset class: {asset_class}")
                    continue
                
                # Calculate security-level recommendations
                recommendations = handler.calculate_security_recommendations(
                    client_id=client_id,
                    asset_class=asset_class,
                    required_change=required_change,
                    target_value=asset_allocation['target_value'],
                    current_asset_value=asset_allocation['current_value']
                )
                
                security_recommendations[asset_class] = recommendations
                total_securities += len(recommendations)
                
                self.logger.info(f"Generated {len(recommendations)} recommendations for {asset_class}")
            
            self.logger.info(f"Stage 2 complete: {total_securities} total security recommendations")
            
            return {
                'success': True,
                'security_recommendations': security_recommendations,
                'total_recommendations': total_securities
            }
            
        except Exception as e:
            self.logger.error(f"Error in Stage 2 (Security Allocation): {e}", exc_info=True)
            import traceback
            self.logger.error(f"Traceback: {traceback.format_exc()}")
            return {'success': False, 'error': str(e)}

class RecommendationAPI:
    """Main API that orchestrates the two-stage process"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.asset_allocation_api = AssetAllocationAPI()
        self.security_allocation_api = SecurityAllocationAPI()
    
    def generate_recommendations(
        self, 
        client_id: int, 
        investment_amount: float
    ) -> Dict:
        """
        Generate complete recommendations using two-stage process
        
        Args:
            client_id: Client ID
            investment_amount: Positive for investment, negative for withdrawal
            
        Returns:
            Complete recommendation response
        """
        try:
            self.logger.info(f"Generating recommendations for client {client_id}, amount: {investment_amount}")
            
            # Stage 1: Asset Allocation
            asset_allocation_result = self.asset_allocation_api.calculate_asset_allocation(
                client_id, investment_amount
            )
            
            if asset_allocation_result.get('error'):
                return {
                    'success': False,
                    'error': f"Stage 1 failed: {asset_allocation_result['error']}"
                }
            
            # Stage 2: Security Allocation
            security_recommendations = self.security_allocation_api.calculate_security_allocations(
                client_id, asset_allocation_result['asset_allocations']
            )
            
            if security_recommendations.get('error'):
                return {
                    'success': False,
                    'error': f"Stage 2 failed: {security_recommendations['error']}"
                }
            
            # Calculate totals
            total_allocated = sum(
                sum(rec.get('amount', 0) for rec in recs)
                for recs in security_recommendations['security_recommendations'].values()
            )
            
            return {
                'success': True,
                'client_id': client_id,
                'investment_amount': investment_amount,
                'asset_allocation': asset_allocation_result,
                'security_recommendations': security_recommendations['security_recommendations'],
                'total_allocated': total_allocated,
                'remaining_amount': investment_amount - total_allocated,
                'total_recommendations': security_recommendations['total_recommendations']
            }
            
        except Exception as e:
            self.logger.error(f"Error generating recommendations: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_client_portfolio_state(self, client_id: int) -> Dict:
        """Get current portfolio state for a client"""
        return self.asset_allocation_api._get_client_portfolio_state(client_id)
    
    def generate_asset_allocation_only(
        self,
        client_id: int,
        investment_amount: float
    ) -> Dict:
        """
        Generate only asset-level recommendations (Stage 1 only)
        
        Args:
            client_id: Client ID
            investment_amount: Positive for investment, negative for withdrawal
            
        Returns:
            Asset allocation recommendations only
        """
        try:
            self.logger.info(f"Generating asset allocation for client {client_id}, amount: {investment_amount}")
            
            result = self.asset_allocation_api.calculate_asset_allocation(
                client_id, investment_amount
            )
            
            # calculate_asset_allocation returns {'error': '...'} on failure (still HTTP 200 from helper)
            err = (result or {}).get('error')
            if err:
                return {
                    'success': False,
                    'error': err,
                    'client_id': client_id,
                    'investment_amount': investment_amount,
                    'asset_allocations': [],
                }
            
            return {
                'success': True,
                'client_id': client_id,
                'investment_amount': investment_amount,
                'asset_allocations': result.get('asset_allocations', []),
                'total_portfolio_value': result.get('total_portfolio_value', 0),
                'target_portfolio_value': result.get('target_portfolio_value', 0)
            }
            
        except Exception as e:
            self.logger.error(f"Error generating asset allocation: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def generate_security_recommendations_only(
        self,
        client_id: int,
        asset_class: str,
        current_asset_value: float,
        change_amount: float
    ) -> Dict:
        """
        Generate only security-level recommendations for a specific asset class
        
        Args:
            client_id: Client ID
            asset_class: Asset class name ('Equity', 'Fixed Income', 'Gold', 'REITs')
            current_asset_value: Current value of the asset class
            change_amount: Amount to change (positive for buy, negative for sell)
            
        Returns:
            Security recommendations for the specified asset class only
        """
        return self.security_allocation_api.generate_security_recommendations(
            client_id, asset_class, current_asset_value, change_amount
        )
    
    def save_recommendations(
        self,
        client_id: int,
        recommendations: Dict,
        user_id: int,
        workflow_id: Optional[int] = None
    ) -> bool:
        """Save recommendations to the database"""
        try:
            # Flatten all security recommendations
            all_security_recommendations = []
            for asset_class, recs in recommendations.get('security_recommendations', {}).items():
                all_security_recommendations.extend(recs)
            
            # Use existing service to save
            from unified_recommendation_service import UnifiedRecommendationService
            service = UnifiedRecommendationService()
            return service.save_recommendations(
                client_id=client_id,
                security_recommendations=all_security_recommendations,
                user_id=user_id,
                workflow_id=workflow_id
            )
            
        except Exception as e:
            self.logger.error(f"Error saving recommendations: {e}")
            return False

# Global instance for easy access
recommendation_api = RecommendationAPI()