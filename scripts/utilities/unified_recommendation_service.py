#!/usr/bin/env python3
"""
Unified Recommendation Service
Consolidates all recommendation systems into one intelligent, asset-first approach
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from models import (
    Client, Portfolio, Security, Holding, ModelAssignment, 
    AssetAllocationModel, SecurityAllocationModel, Recommendation
)
from extensions import db
import logging

logger = logging.getLogger(__name__)

class UnifiedRecommendationService:
    """
    Unified recommendation service that handles all recommendation scenarios:
    - Monthly investment workflows
    - Ad-hoc recommendations
    - Withdrawals
    - Rebalancing
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def generate_recommendations(
        self, 
        client_id: int, 
        investment_amount: float, 
        source_type: str = 'adhoc',
        workflow_id: Optional[int] = None
    ) -> Dict:
        """
        Generate unified recommendations for any scenario
        
        Args:
            client_id: Client ID
            investment_amount: Amount (positive for investment, negative for withdrawal)
            source_type: 'monthly_investment', 'adhoc', 'workflow', 'withdrawal'
            workflow_id: Optional workflow ID for tracking
        
        Returns:
            Dict containing asset and security recommendations
        """
        try:
            client = Client.query.get_or_404(client_id)
            
            # Get client's portfolio and models
            portfolio = self._get_or_create_portfolio(client)
            model_assignment = self._get_client_models(client)
            
            if not model_assignment:
                return {
                    'error': 'No models assigned to this client',
                    'asset_recommendations': [],
                    'security_recommendations': []
                }
            
            # Step 1: Calculate current portfolio state
            current_state = self._analyze_current_portfolio(client, portfolio)
            
            # Step 2: Generate asset-level recommendations
            asset_recommendations = self._calculate_asset_recommendations(
                client, model_assignment, current_state, investment_amount
            )
            
            # Step 3: Generate security-level recommendations
            security_recommendations = self._calculate_security_recommendations(
                client, model_assignment, current_state, asset_recommendations, investment_amount
            )
            
            return {
                'client': client,
                'portfolio': portfolio,
                'current_state': current_state,
                'asset_recommendations': asset_recommendations,
                'security_recommendations': security_recommendations,
                'investment_amount': investment_amount,
                'source_type': source_type,
                'workflow_id': workflow_id,
                'total_recommendation_value': sum(rec['amount'] for rec in security_recommendations)
            }
            
        except Exception as e:
            self.logger.error(f"Error generating recommendations: {str(e)}")
            return {
                'error': f'Error generating recommendations: {str(e)}',
                'asset_recommendations': [],
                'security_recommendations': []
            }
    
    def _get_or_create_portfolio(self, client: Client) -> Portfolio:
        """Get or create client's portfolio"""
        portfolio = Portfolio.query.filter_by(client_id=client.id, status='active').first()
        if not portfolio:
            portfolio = Portfolio(
                client_id=client.id,
                name=f"{client.name}'s Portfolio",
                created_at=datetime.utcnow(),
                status='active'
            )
            db.session.add(portfolio)
            db.session.commit()
        return portfolio
    
    def _get_client_models(self, client: Client) -> Optional[ModelAssignment]:
        """Get client's assigned models"""
        return ModelAssignment.query.filter_by(client_id=client.id).order_by(
            ModelAssignment.assigned_at.desc()
        ).first()
    
    def _analyze_current_portfolio(self, client: Client, portfolio: Portfolio) -> Dict:
        """Analyze current portfolio state"""
        current_holdings = {}
        total_portfolio_value = 0.0
        
        self.logger.debug(f"Analyzing portfolio for client {client.id}")
        
        # Get current holdings - use client.holdings instead of portfolio.holdings
        for holding in client.holdings:
            if holding.security and holding.security.current_price:
                try:
                    quantity = float(holding.quantity) if holding.quantity else 0.0
                    current_price = float(holding.security.current_price) if holding.security.current_price else 0.0
                    current_value = quantity * current_price
                    total_portfolio_value += current_value
                    
                    self.logger.debug(f"Holding: {holding.security.symbol}, Qty: {quantity}, Price: {current_price}, Value: {current_value}")
                except Exception as e:
                    self.logger.error(f"Error processing holding: {e}")
                    continue
                
                current_holdings[holding.security_id] = {
                    'security_id': holding.security_id,
                    'security_name': holding.security.name,
                    'symbol': holding.security.symbol,
                    'asset_class': holding.security.asset_class.name if holding.security.asset_class else 'Unknown',
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
    
    def _calculate_asset_recommendations(
        self, 
        client: Client, 
        model_assignment: ModelAssignment, 
        current_state: Dict, 
        investment_amount: float
    ) -> List[Dict]:
        """Calculate asset-level recommendations"""
        asset_recommendations = []
        
        if not model_assignment.asset_model:
            return asset_recommendations
        
        total_portfolio_value = current_state['total_portfolio_value']
        total_value_after_investment = total_portfolio_value + investment_amount
        current_asset_weights = current_state['current_asset_weights']
        
        # Get target asset allocations from model
        target_asset_allocations = {}
        for allocation in model_assignment.asset_model.asset_allocations:
            target_asset_allocations[allocation.asset_class.name] = float(allocation.allocation_percentage)
        
        # Calculate required changes for each asset class
        for asset_class_name, target_weight in target_asset_allocations.items():
            current_weight = current_asset_weights.get(asset_class_name, 0)
            current_value = current_state['current_asset_allocations'].get(asset_class_name, 0)
            
            self.logger.debug(f"Processing asset class: {asset_class_name}")
            self.logger.debug(f"  target_weight: {target_weight} (type: {type(target_weight)})")
            self.logger.debug(f"  total_value_after_investment: {total_value_after_investment} (type: {type(total_value_after_investment)})")
            
            # Calculate target value after investment
            target_value = (float(total_value_after_investment) * float(target_weight) / 100.0)
            
            # Calculate required change
            required_change = target_value - float(current_value)
            
            # Determine if change is significant (more than 1% of portfolio)
            significant_change = abs(required_change) > (float(total_portfolio_value) * 0.01)
            
            if significant_change or investment_amount != 0:
                asset_recommendations.append({
                    'asset_class': asset_class_name,
                    'current_value': current_value,
                    'current_weight': current_weight,
                    'target_value': target_value,
                    'target_weight': target_weight,
                    'required_change': required_change,
                    'action': 'BUY' if required_change > 0 else 'SELL',
                    'priority': 1 if required_change > 0 else 2,  # Buy recommendations first
                    'significant_change': significant_change
                })
        
        # Sort by priority and absolute change amount
        asset_recommendations.sort(key=lambda x: (x['priority'], abs(x['required_change'])), reverse=True)
        
        return asset_recommendations
    
    def _calculate_security_recommendations(
        self, 
        client: Client, 
        model_assignment: ModelAssignment, 
        current_state: Dict, 
        asset_recommendations: List[Dict], 
        investment_amount: float
    ) -> List[Dict]:
        """Calculate security-level recommendations with proper prioritization"""
        security_recommendations = []
        
        if not model_assignment.stock_model:
            return security_recommendations
        
        # Get all relevant securities and categorize them
        all_securities = self._get_all_relevant_securities(current_state['current_holdings'], model_assignment)
        current_holdings = current_state['current_holdings']
        total_portfolio_value = current_state['total_portfolio_value']
        
        # Categorize securities
        categorized_securities = self._categorize_securities(
            all_securities, current_holdings, model_assignment, total_portfolio_value
        )
        
        if investment_amount > 0:
            # Investment scenario - prioritize buys
            self.logger.debug("Allocating buy recommendations...")
            security_recommendations = self._allocate_buy_recommendations(
                categorized_securities, asset_recommendations, investment_amount
            )
        else:
            # Withdrawal scenario - prioritize sells
            self.logger.debug("Allocating sell recommendations...")
            security_recommendations = self._allocate_sell_recommendations(
                categorized_securities, abs(investment_amount)
            )
        
        # Filter to only significant recommendations
        significant_recommendations = self._filter_significant_recommendations(
            security_recommendations, total_portfolio_value
        )
        
        return significant_recommendations
    
    def _categorize_securities(
        self, 
        all_securities: List[Security], 
        current_holdings: Dict, 
        model_assignment: ModelAssignment, 
        total_portfolio_value: float
    ) -> Dict:
        """Categorize securities based on current vs target weights"""
        categorized = {
            'hot_stocks_underallocated': [],
            'hot_stocks_allocated': [],
            'model_stocks_underallocated': [],
            'model_stocks_overallocated': [],
            'model_stocks_removed': [],
            'other_stocks': []
        }
        
        # Get model securities
        model_securities = {}
        for allocation in model_assignment.stock_model.security_allocations:
            model_securities[allocation.security_id] = float(allocation.allocation_percentage)
        
        for security in all_securities:
            current_holding = current_holdings.get(security.id, {
                'current_value': 0,
                'current_weight': 0,
                'quantity': 0
            })
            
            current_weight = current_holding['current_weight']
            target_weight = model_securities.get(security.id, 0)
            is_hot_stock = security.is_hot_stock
            is_in_model = security.id in model_securities
            
            # Calculate allocation ratio
            if target_weight > 0:
                allocation_ratio = current_weight / target_weight if target_weight > 0 else 0
                is_underallocated = allocation_ratio < 0.6  # 40% tolerance (more conservative)
                is_overallocated = allocation_ratio > 1.2   # 20% over allocation (more conservative)
            else:
                allocation_ratio = 0
                is_underallocated = False
                is_overallocated = current_weight > 0.03  # 3% threshold for non-model stocks
            
            security_data = {
                'security_id': security.id,
                'security_name': security.name,
                'symbol': security.symbol,
                'asset_class': security.asset_class.name if security.asset_class else 'Unknown',
                'current_weight': current_weight,
                'target_weight': target_weight,
                'allocation_ratio': allocation_ratio,
                'current_value': current_holding['current_value'],
                'quantity': current_holding['quantity']
            }
            
            # Categorize based on requirements
            if is_hot_stock:
                if is_underallocated:
                    categorized['hot_stocks_underallocated'].append(security_data)
                else:
                    categorized['hot_stocks_allocated'].append(security_data)
            elif is_in_model:
                if is_underallocated:
                    categorized['model_stocks_underallocated'].append(security_data)
                elif is_overallocated:
                    categorized['model_stocks_overallocated'].append(security_data)
            else:
                # Stock not in model
                if current_weight > 0:
                    categorized['model_stocks_removed'].append(security_data)
                else:
                    categorized['other_stocks'].append(security_data)
        
        return categorized
    
    def _get_all_relevant_securities(self, current_holdings: Dict, model_assignment: ModelAssignment) -> List[Security]:
        """Get all securities that should be included in recommendations"""
        from models import Security
        
        # Get all securities that the client currently holds
        held_security_ids = list(current_holdings.keys())
        
        # Get all securities from the model
        model_security_ids = []
        if model_assignment and model_assignment.stock_model:
            model_security_ids = [allocation.security_id for allocation in model_assignment.stock_model.security_allocations]
        
        # Get hot stocks
        hot_stock_ids = [security.id for security in Security.query.filter_by(is_hot_stock=True).all()]
        
        # Combine all relevant security IDs
        all_relevant_ids = list(set(held_security_ids + model_security_ids + hot_stock_ids))
        
        # Get the actual security objects
        return Security.query.filter(Security.id.in_(all_relevant_ids)).all()
    
    def _filter_significant_recommendations(
        self, 
        security_recommendations: List[Dict], 
        total_portfolio_value: float
    ) -> List[Dict]:
        """Filter recommendations to only include significant changes"""
        MIN_AMOUNT_PERCENTAGE = 0.02  # 2% of portfolio value
        MIN_WEIGHT_CHANGE_PERCENTAGE = 0.2  # 0.2% weight change minimum
        
        significant_recommendations = []
        
        for rec in security_recommendations:
            amount = rec.get('amount', 0)
            current_weight = rec.get('current_weight', 0)
            target_weight = rec.get('target_weight', 0)
            weight_change = abs(target_weight - current_weight)
            
            # Calculate amount as percentage of portfolio
            amount_percentage = (amount / total_portfolio_value * 100) if total_portfolio_value > 0 else 0
            
            # Include if amount is significant OR weight change is significant
            if amount_percentage >= MIN_AMOUNT_PERCENTAGE or weight_change >= MIN_WEIGHT_CHANGE_PERCENTAGE:
                significant_recommendations.append(rec)
        
        # Sort by priority and amount percentage (highest first)
        significant_recommendations.sort(key=lambda x: (x.get('priority', 999), -(x.get('amount', 0) / total_portfolio_value * 100) if total_portfolio_value > 0 else 0))
        
        # Limit to top 8 recommendations to avoid too many transactions
        return significant_recommendations[:8]
    
    def _allocate_buy_recommendations(
        self, 
        categorized_securities: Dict, 
        asset_recommendations: List[Dict], 
        investment_amount: float
    ) -> List[Dict]:
        """Allocate buy recommendations with proper prioritization"""
        recommendations = []
        remaining_amount = investment_amount
        
        # 1. MUST include hot stocks if underallocated
        hot_stocks_underallocated = categorized_securities['hot_stocks_underallocated']
        for stock_data in hot_stocks_underallocated:
            if remaining_amount <= 0:
                break
            
            security_id = stock_data['security_id']
            symbol = stock_data['symbol']
            target_weight = stock_data['target_weight']
            current_weight = stock_data['current_weight']
            
            # Get security object for price calculation
            security = Security.query.get(security_id)
            if not security or not security.current_price:
                continue
            
            # Calculate required amount to reach target
            required_amount = self._calculate_required_amount_to_target(
                security, target_weight, current_weight
            )
            
            # Allocate up to the required amount or remaining amount
            allocation_amount = min(required_amount, remaining_amount)
            
            if allocation_amount > 0:
                try:
                    # Calculate quantity as integer (whole number)
                    quantity = int(allocation_amount / float(security.current_price)) if security.current_price else 0
                except Exception as e:
                    self.logger.error(f"Error calculating quantity for {symbol}: {e}")
                    quantity = 0
                recommendations.append({
                    'security_id': security_id,
                    'security_name': stock_data['security_name'],
                    'symbol': symbol,
                    'asset_class': stock_data['asset_class'],
                    'amount': allocation_amount,
                    'quantity': quantity,
                    'action': 'BUY',
                    'reason': 'Hot stock underallocated',
                    'priority': 1,
                    'current_weight': current_weight,
                    'target_weight': target_weight
                })
                remaining_amount -= allocation_amount
        
        # 2. CAN include hot stocks even if allocated
        hot_stocks_allocated = categorized_securities['hot_stocks_allocated']
        for stock_data in hot_stocks_allocated:
            if remaining_amount <= 0:
                break
            
            security_id = stock_data['security_id']
            symbol = stock_data['symbol']
            # Allocate a smaller amount for already allocated hot stocks
            allocation_amount = min(remaining_amount * 0.1, remaining_amount)  # 10% of remaining
            
            # Get security object for price calculation
            security = Security.query.get(security_id)
            if not security or not security.current_price:
                continue
            
            if allocation_amount > 0:
                try:
                    # Calculate quantity as integer (whole number)
                    quantity = int(allocation_amount / float(security.current_price)) if security.current_price else 0
                except Exception as e:
                    self.logger.error(f"Error calculating quantity for {symbol}: {e}")
                    quantity = 0
                recommendations.append({
                    'security_id': security_id,
                    'security_name': stock_data['security_name'],
                    'symbol': symbol,
                    'asset_class': stock_data['asset_class'],
                    'amount': allocation_amount,
                    'quantity': quantity,
                    'action': 'BUY',
                    'reason': 'Hot stock additional allocation',
                    'priority': 2,
                    'current_weight': stock_data['current_weight'],
                    'target_weight': stock_data['target_weight']
                })
                remaining_amount -= allocation_amount
        
        # 3. Include underallocated model stocks
        model_stocks_underallocated = categorized_securities['model_stocks_underallocated']
        for stock_data in model_stocks_underallocated:
            if remaining_amount <= 0:
                break
            
            security_id = stock_data['security_id']
            symbol = stock_data['symbol']
            target_weight = stock_data['target_weight']
            current_weight = stock_data['current_weight']
            
            # Get security object for price calculation
            security = Security.query.get(security_id)
            if not security or not security.current_price:
                continue
            
            # Calculate required amount to reach target
            required_amount = self._calculate_required_amount_to_target(
                security, target_weight, current_weight
            )
            
            # Allocate proportionally to remaining amount
            allocation_amount = min(required_amount, remaining_amount)
            
            if allocation_amount > 0:
                try:
                    # Calculate quantity as integer (whole number)
                    quantity = int(allocation_amount / float(security.current_price)) if security.current_price else 0
                except Exception as e:
                    self.logger.error(f"Error calculating quantity for {symbol}: {e}")
                    quantity = 0
                recommendations.append({
                    'security_id': security_id,
                    'security_name': stock_data['security_name'],
                    'symbol': symbol,
                    'asset_class': stock_data['asset_class'],
                    'amount': allocation_amount,
                    'quantity': quantity,
                    'action': 'BUY',
                    'reason': 'Model stock underallocated',
                    'priority': 3,
                    'current_weight': current_weight,
                    'target_weight': target_weight
                })
                remaining_amount -= allocation_amount
        
        return recommendations
    
    def _allocate_sell_recommendations(
        self, 
        categorized_securities: Dict, 
        withdrawal_amount: float
    ) -> List[Dict]:
        """Allocate sell recommendations based on criteria"""
        recommendations = []
        remaining_amount = withdrawal_amount
        
        # 1. SELL stocks removed from model completely
        model_stocks_removed = categorized_securities['model_stocks_removed']
        for stock_data in model_stocks_removed:
            if remaining_amount <= 0:
                break
            
            security_id = stock_data['security_id']
            symbol = stock_data['symbol']
            current_value = stock_data['current_value']
            
            # Get security object for price calculation
            security = Security.query.get(security_id)
            if not security or not security.current_price:
                continue
            
            # Sell entire holding if removed from model
            sell_amount = min(current_value, remaining_amount)
            
            if sell_amount > 0:
                try:
                    # Calculate quantity as integer (whole number)
                    quantity = int(sell_amount / float(security.current_price)) if security.current_price else 0
                except Exception as e:
                    self.logger.error(f"Error calculating quantity for {symbol}: {e}")
                    quantity = 0
                recommendations.append({
                    'security_id': security_id,
                    'security_name': stock_data['security_name'],
                    'symbol': symbol,
                    'asset_class': stock_data['asset_class'],
                    'amount': sell_amount,
                    'quantity': quantity,
                    'action': 'SELL',
                    'reason': 'Stock removed from model',
                    'priority': 1,
                    'current_weight': stock_data['current_weight'],
                    'target_weight': 0
                })
                remaining_amount -= sell_amount
        
        # 2. SELL stocks with significant overallocation (>50% over target)
        model_stocks_overallocated = categorized_securities['model_stocks_overallocated']
        for stock_data in model_stocks_overallocated:
            if remaining_amount <= 0:
                break
            
            security_id = stock_data['security_id']
            symbol = stock_data['symbol']
            target_weight = stock_data['target_weight']
            current_weight = stock_data['current_weight']
            
            # Get security object for price calculation
            security = Security.query.get(security_id)
            if not security or not security.current_price:
                continue
            
            # Only suggest sell if >50% over target
            if stock_data['allocation_ratio'] > 1.5:
                excess_amount = self._calculate_excess_amount(
                    security, target_weight, current_weight
                )
                
                sell_amount = min(excess_amount, remaining_amount)
                
                if sell_amount > 0:
                    try:
                        # Calculate quantity as integer (whole number)
                        quantity = int(sell_amount / float(security.current_price)) if security.current_price else 0
                    except Exception as e:
                        self.logger.error(f"Error calculating quantity for {symbol}: {e}")
                        quantity = 0
                    recommendations.append({
                        'security_id': security_id,
                        'security_name': stock_data['security_name'],
                        'symbol': symbol,
                        'asset_class': stock_data['asset_class'],
                        'amount': sell_amount,
                        'quantity': quantity,
                        'action': 'SELL',
                        'reason': f'Significant overallocation ({stock_data["allocation_ratio"]:.1f}x target)',
                        'priority': 2,
                        'current_weight': current_weight,
                        'target_weight': target_weight
                    })
                    remaining_amount -= sell_amount
        
        return recommendations
    
    def _calculate_required_amount_to_target(
        self, 
        security: Security, 
        target_weight: float, 
        current_weight: float
    ) -> float:
        """Calculate amount needed to reach target weight"""
        # This would need to be calculated based on total portfolio value
        # For now, return a simplified calculation
        if target_weight <= current_weight:
            return 0
        
        # Calculate the additional amount needed
        weight_difference = float(target_weight) - float(current_weight)
        # This is a simplified calculation - in practice, you'd need total portfolio value
        return weight_difference * 1000  # Placeholder calculation
    
    def _calculate_excess_amount(
        self, 
        security: Security, 
        target_weight: float, 
        current_weight: float
    ) -> float:
        """Calculate excess amount for overallocated securities"""
        if current_weight <= target_weight:
            return 0
        
        weight_excess = float(current_weight) - float(target_weight)
        # This is a simplified calculation - in practice, you'd need total portfolio value
        return weight_excess * 1000  # Placeholder calculation
    
    def save_recommendations(
        self, 
        client_id: int, 
        security_recommendations: List[Dict], 
        user_id: int,
        workflow_id: Optional[int] = None
    ) -> bool:
        """Save recommendations to database"""
        try:
            for rec in security_recommendations:
                recommendation = Recommendation(
                    security_id=rec['security_id'],
                    client_id=client_id,
                    action=rec['action'],
                    quantity=rec['quantity'],
                    target_price=rec.get('target_price', 0),
                    status='pending',
                    expiry_date=datetime.utcnow().replace(day=datetime.utcnow().day + 7),
                    created_by=user_id,
                    notes=f"Amount: ₹{rec['amount']}, Reason: {rec['reason']}, Workflow: {workflow_id}"
                )
                db.session.add(recommendation)
            
            db.session.commit()
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving recommendations: {str(e)}")
            db.session.rollback()
            return False
