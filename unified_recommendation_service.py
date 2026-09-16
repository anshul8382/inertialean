#!/usr/bin/env python3
"""
Unified Recommendation Service
Consolidates all recommendation systems into one intelligent, asset-first approach
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from models import (
    Client, Portfolio, Security, Holding, ModelAssignment, 
    AssetAllocationModel, AssetAllocation, SecurityAllocationModel, 
    AssetClass, User, Recommendation
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
        workflow_id: Optional[int] = None,
        manually_selected: List[int] = None,
        manually_rejected: List[int] = None,
        asset_recommendations_override: Optional[List[Dict]] = None
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
                    'security_recommendations': [],
                    'section_1_recommended': [],
                    'section_2_hot_stocks': [],
                    'section_3_other': []
                }
            
            # Step 1: Calculate current portfolio state
            current_state = self._analyze_current_portfolio(client, portfolio)
            # Add client_id to current_state for use in formatting recommendations
            current_state['client_id'] = client.id
            
            # Step 2: Generate asset-level recommendations (or use override)
            # If user has modified asset allocation amounts, we must respect those amounts when
            # generating security-level recommendations.
            if asset_recommendations_override is not None:
                asset_recommendations = asset_recommendations_override
            else:
                asset_recommendations = self._calculate_asset_recommendations(
                    client, model_assignment, current_state, investment_amount
                )
            
            # Step 3: Generate security-level recommendations (Section 1)
            # Pass user selections and rejections to respect user choices
            section_1_recommended = self._calculate_security_recommendations(
                client, model_assignment, current_state, asset_recommendations, investment_amount,
                manually_selected=manually_selected or [],
                manually_rejected=manually_rejected or []
            )
            
            # Format Section 1 recommendations with unified structure
            formatted_section_1 = []
            formatted_security_ids = set()  # Track which securities we've formatted
            
            # Get target weights from security allocation model for fallback
            model_securities = {}
            if model_assignment and model_assignment.stock_model:
                for allocation in model_assignment.stock_model.security_allocations:
                    model_securities[allocation.security_id] = float(allocation.allocation_percentage)
            
            for rec in section_1_recommended:
                security = Security.query.get(rec['security_id'])
                if security:
                    # Get target_weight from rec, or fallback to model if missing
                    target_weight = rec.get('target_weight', 0)
                    if target_weight == 0 and security.id in model_securities:
                        target_weight = model_securities[security.id]
                        self.logger.debug(f"Retrieved target_weight from model for {security.symbol}: {target_weight}%")
                    
                    formatted = self._format_security_recommendation(
                        security=security,
                        current_state=current_state,
                        target_weight=target_weight,
                        amount=rec.get('amount', 0),
                        action=rec.get('action', 'HOLD'),
                        reason=rec.get('reason', ''),
                        is_recommended=True,
                        section='recommended',
                        investment_amount=investment_amount,
                        client_id=client.id
                    )
                    formatted_section_1.append(formatted)
                    formatted_security_ids.add(rec['security_id'])
            
            # CRITICAL: Ensure ALL manually_selected securities are in Section 1, even if allocation failed
            if manually_selected:
                for security_id in manually_selected:
                    if security_id in formatted_security_ids:
                        continue  # Already included
                    
                    security = Security.query.get(security_id)
                    if not security:
                        self.logger.warning(f"Manually selected security {security_id} not found in database")
                        continue
                    
                    # Get current holding data
                    current_holdings = current_state.get('current_holdings', {}) if current_state else {}
                    holding = current_holdings.get(security_id, {})
                    
                    # Get target weight from model
                    target_weight = 0
                    if model_assignment and model_assignment.stock_model:
                        for allocation in model_assignment.stock_model.security_allocations:
                            if allocation.security_id == security_id:
                                target_weight = float(allocation.allocation_percentage)
                                break
                    
                    # Format and add to Section 1 (even if amount is 0)
                    formatted = self._format_security_recommendation(
                        security=security,
                        current_state=current_state,
                        target_weight=target_weight,
                        amount=0.0,  # Default to 0 if not allocated
                        action='BUY',
                        reason='User selected',
                        is_recommended=True,
                        section='recommended',
                        investment_amount=investment_amount,
                        client_id=client.id
                    )
                    formatted_section_1.append(formatted)
                    self.logger.info(f"Added manually selected security {security_id} ({security.symbol}) to Section 1 (was missing from allocation)")
            
            # Get Section 2: All hot stocks (excluding Section 1, manually_selected, and manually_rejected)
            recommended_ids = [r['security_id'] for r in formatted_section_1]
            
            # Combine all exclusions: Section 1 + manually_selected + manually_rejected
            all_exclusions = set(recommended_ids)
            if manually_selected:
                all_exclusions.update(manually_selected)
                self.logger.info(f"Excluding {len(manually_selected)} manually_selected securities from Sections 2 & 3")
            if manually_rejected:
                all_exclusions.update(manually_rejected)
                self.logger.info(f"Excluding {len(manually_rejected)} manually_rejected securities from Sections 2 & 3")
            
            self.logger.info(f"Section 1 has {len(formatted_section_1)} securities with IDs: {recommended_ids[:10]}")
            self.logger.info(f"Total exclusions for Sections 2 & 3: {len(all_exclusions)} (Section 1: {len(recommended_ids)}, manually_selected: {len(manually_selected) if manually_selected else 0}, manually_rejected: {len(manually_rejected) if manually_rejected else 0})")
            
            section_2_hot_stocks = self._get_all_hot_stocks_formatted(
                client_id, current_state, investment_amount, excluded_security_ids=list(all_exclusions)
            )
            self.logger.info(f"Section 2 generated with {len(section_2_hot_stocks)} hot stocks")
            
            # Get Section 3: Other securities (excluding Section 1, Section 2, manually_selected, and manually_rejected)
            hot_stock_ids = [s['security_id'] for s in section_2_hot_stocks]
            self.logger.info(f"Section 2 has {len(section_2_hot_stocks)} hot stocks with IDs: {hot_stock_ids[:10]}")
            section_3_other = self._get_all_other_securities_formatted(
                client_id, recommended_ids, hot_stock_ids, current_state, model_assignment,
                excluded_security_ids=list(all_exclusions),
                investment_amount=investment_amount
            )
            self.logger.info(f"Section 3 generated with {len(section_3_other)} other securities")
            
            return {
                # Do not include SQLAlchemy client/portfolio — they break jsonify/make_json_safe (ORM cycles).
                'client_id': client.id,
                'portfolio_id': portfolio.id,
                'current_state': current_state,
                'asset_recommendations': asset_recommendations,
                'security_recommendations': formatted_section_1,  # Keep for backward compatibility
                'section_1_recommended': formatted_section_1,
                'section_2_hot_stocks': section_2_hot_stocks,
                'section_3_other': section_3_other,
                'investment_amount': investment_amount,
                'source_type': source_type,
                'workflow_id': workflow_id,
                'total_recommendation_value': sum(rec['amount'] for rec in formatted_section_1)
            }
            
        except Exception as e:
            self.logger.error(f"Error generating recommendations: {str(e)}")
            return {
                'error': f'Error generating recommendations: {str(e)}',
                'asset_recommendations': [],
                'security_recommendations': [],
                'section_1_recommended': [],
                'section_2_hot_stocks': [],
                'section_3_other': []
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
        """Get client's assigned models, creating default assignment if none exists
        
        This method ensures that every client has a model assignment by creating
        a default one with 100% Equity asset allocation and Inertia Core Portfolio
        if no assignment exists.
        
        Also validates that existing assignments have both asset_model and stock_model.
        If either is missing, it fixes the assignment by creating defaults.
        
        Returns ModelAssignment or None only if critical database entities are missing.
        """
        model_assignment = ModelAssignment.query.filter_by(client_id=client.id).order_by(
            ModelAssignment.assigned_at.desc()
        ).first()
        
        # If no model assignment exists, create a default one
        if not model_assignment:
            self.logger.info(f"No model assignment found for client {client.id}, creating default assignment")
            model_assignment = self._create_default_model_assignment(client)
            
            # If creation failed, log error but don't raise exception
            # The calling code should handle None appropriately
            if not model_assignment:
                self.logger.error(f"Failed to create default model assignment for client {client.id}. "
                               f"Client will not be able to generate recommendations until models are assigned.")
        else:
            # Validate that the assignment has both asset_model and stock_model
            needs_fix = False
            
            # Check if asset_model is missing
            if not model_assignment.asset_model_id or not model_assignment.asset_model:
                self.logger.warning(f"Client {client.id} has model assignment but asset_model is missing "
                                  f"(asset_model_id={model_assignment.asset_model_id}). Fixing...")
                needs_fix = True
            
            # Check if stock_model is missing
            if not model_assignment.stock_model_id or not model_assignment.stock_model:
                self.logger.warning(f"Client {client.id} has model assignment but stock_model is missing "
                                  f"(stock_model_id={model_assignment.stock_model_id}). Fixing...")
                needs_fix = True
            
            # If either model is missing, fix the assignment
            if needs_fix:
                self.logger.info(f"Fixing incomplete model assignment for client {client.id}")
                fixed_assignment = self._fix_incomplete_model_assignment(client, model_assignment)
                if fixed_assignment:
                    model_assignment = fixed_assignment
                else:
                    self.logger.error(f"Failed to fix model assignment for client {client.id}. "
                                    f"Creating new default assignment...")
                    # Delete the broken assignment and create a new one
                    db.session.delete(model_assignment)
                    db.session.commit()
                    model_assignment = self._create_default_model_assignment(client)
        
        return model_assignment
    
    def _fix_incomplete_model_assignment(self, client: Client, model_assignment: ModelAssignment) -> Optional[ModelAssignment]:
        """Fix an incomplete model assignment by adding missing asset_model or stock_model"""
        try:
            # Get or create admin user
            admin_user = User.query.filter_by(is_active=True).first()
            if not admin_user:
                admin_user = User.query.first()
                if not admin_user:
                    self.logger.error("No users found in database - cannot fix model assignment")
                    return None
            
            # Find or create Equity asset class
            equity_asset_class = AssetClass.query.filter_by(name='Equity').first()
            if not equity_asset_class:
                equity_asset_class = AssetClass.query.filter(
                    db.func.lower(AssetClass.name) == 'equity'
                ).first()
            
            if not equity_asset_class:
                self.logger.error("Equity asset class not found - cannot fix model assignment")
                return None
            
            # Fix missing asset_model
            if not model_assignment.asset_model_id or not model_assignment.asset_model:
                # Find or create 100% Equity asset allocation model
                asset_model = AssetAllocationModel.query.join(AssetAllocation).filter(
                    AssetAllocation.asset_class_id == equity_asset_class.id,
                    AssetAllocation.allocation_percentage == 100
                ).first()
                
                if not asset_model:
                    self.logger.info("Creating default 100% Equity asset allocation model for fix")
                    asset_model = AssetAllocationModel(
                        name='Default 100% Equity',
                        description='Default asset allocation model with 100% Equity allocation',
                        risk_profile='Moderate',
                        created_by=admin_user.id,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    )
                    db.session.add(asset_model)
                    db.session.flush()
                    
                    asset_allocation = AssetAllocation(
                        asset_class_id=equity_asset_class.id,
                        model_id=asset_model.id,
                        allocation_percentage=100,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow()
                    )
                    db.session.add(asset_allocation)
                
                model_assignment.asset_model_id = asset_model.id
                self.logger.info(f"Fixed asset_model for client {client.id}: {asset_model.name} (ID: {asset_model.id})")
            
            # Fix missing stock_model
            if not model_assignment.stock_model_id or not model_assignment.stock_model:
                # Find "Inertia Core Portfolio" or most popular equity portfolio
                stock_model = None
                
                # Try multiple variations
                search_variations = [
                    'Inertia Core Portfolio',
                    'inertia core portfolio',
                    'Inertia Core',
                    'inertia core'
                ]
                
                for variation in search_variations:
                    stock_model = SecurityAllocationModel.query.filter_by(name=variation).first()
                    if stock_model:
                        break
                
                # If not found, try case-insensitive
                if not stock_model:
                    stock_model = SecurityAllocationModel.query.filter(
                        db.func.lower(SecurityAllocationModel.name) == 'inertia core portfolio'
                    ).first()
                
                # If still not found, try partial match
                if not stock_model:
                    stock_model = SecurityAllocationModel.query.filter(
                        SecurityAllocationModel.name.ilike('%Inertia Core%')
                    ).first()
                
                # If still not found, find most popular
                if not stock_model:
                    from sqlalchemy import func
                    most_popular = db.session.query(
                        SecurityAllocationModel.id,
                        SecurityAllocationModel.name,
                        func.count(ModelAssignment.id).label('assignment_count')
                    ).outerjoin(
                        ModelAssignment,
                        ModelAssignment.stock_model_id == SecurityAllocationModel.id
                    ).filter(
                        SecurityAllocationModel.asset_class_id == equity_asset_class.id
                    ).group_by(
                        SecurityAllocationModel.id,
                        SecurityAllocationModel.name
                    ).order_by(
                        func.count(ModelAssignment.id).desc()
                    ).first()
                    
                    if most_popular:
                        stock_model = SecurityAllocationModel.query.get(most_popular.id)
                    else:
                        stock_model = SecurityAllocationModel.query.filter_by(
                            asset_class_id=equity_asset_class.id
                        ).first()
                
                if stock_model:
                    model_assignment.stock_model_id = stock_model.id
                    self.logger.info(f"Fixed stock_model for client {client.id}: {stock_model.name} (ID: {stock_model.id})")
                else:
                    self.logger.error("No equity security allocation model found - cannot fix stock_model")
                    return None
            
            db.session.commit()
            self.logger.info(f"Successfully fixed model assignment for client {client.id}")
            return model_assignment
            
        except Exception as e:
            self.logger.error(f"Error fixing model assignment for client {client.id}: {str(e)}", exc_info=True)
            db.session.rollback()
            return None
    
    def _create_default_model_assignment(self, client: Client) -> Optional[ModelAssignment]:
        """Create a default model assignment with 100% Equity asset allocation and Inertia Core Portfolio
        
        This method ensures all clients have a model assignment by:
        1. Creating a 100% Equity asset allocation model if needed
        2. Finding "Inertia Core Portfolio" (or most popular equity portfolio) as the equity portfolio
        3. Creating the model assignment
        
        Returns ModelAssignment or None if critical database entities are missing.
        """
        try:
            # Get or create admin user for model creation
            admin_user = User.query.filter_by(is_active=True).first()
            if not admin_user:
                # Try to get any user as last resort
                admin_user = User.query.first()
                if not admin_user:
                    self.logger.error("No users found in database - cannot create default model assignment")
                    return None
            
            # Find or create Equity asset class
            equity_asset_class = AssetClass.query.filter_by(name='Equity').first()
            if not equity_asset_class:
                # Try case-insensitive search
                equity_asset_class = AssetClass.query.filter(
                    db.func.lower(AssetClass.name) == 'equity'
                ).first()
            
            if not equity_asset_class:
                self.logger.error("Equity asset class not found in database - cannot create default model assignment")
                return None
            
            # Find existing 100% Equity asset allocation model
            asset_model = AssetAllocationModel.query.join(AssetAllocation).filter(
                AssetAllocation.asset_class_id == equity_asset_class.id,
                AssetAllocation.allocation_percentage == 100
            ).first()
            
            # If not found, create one
            if not asset_model:
                self.logger.info("Creating default 100% Equity asset allocation model")
                
                asset_model = AssetAllocationModel(
                    name='Default 100% Equity',
                    description='Default asset allocation model with 100% Equity allocation',
                    risk_profile='Moderate',
                    created_by=admin_user.id,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.session.add(asset_model)
                db.session.flush()  # Get the ID
                
                # Create 100% Equity allocation
                asset_allocation = AssetAllocation(
                    asset_class_id=equity_asset_class.id,
                    model_id=asset_model.id,
                    allocation_percentage=100,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.session.add(asset_allocation)
                self.logger.info(f"Created default asset allocation model: {asset_model.name} (ID: {asset_model.id})")
            
            # Find "Inertia Core Portfolio" security allocation model
            # Try multiple variations of the name
            stock_model = None
            search_variations = [
                'Inertia Core Portfolio',
                'inertia core portfolio',
                'Inertia Core',
                'inertia core'
            ]
            
            for variation in search_variations:
                stock_model = SecurityAllocationModel.query.filter_by(name=variation).first()
                if stock_model:
                    self.logger.info(f"Found equity portfolio by exact match: {stock_model.name} (ID: {stock_model.id})")
                    break
            
            # If not found, try case-insensitive search
            if not stock_model:
                stock_model = SecurityAllocationModel.query.filter(
                    db.func.lower(SecurityAllocationModel.name) == 'inertia core portfolio'
                ).first()
                if stock_model:
                    self.logger.info(f"Found equity portfolio by case-insensitive match: {stock_model.name} (ID: {stock_model.id})")
            
            # If still not found, try partial match
            if not stock_model:
                stock_model = SecurityAllocationModel.query.filter(
                    SecurityAllocationModel.name.ilike('%Inertia Core%')
                ).first()
                if stock_model:
                    self.logger.info(f"Found equity portfolio by partial match: {stock_model.name} (ID: {stock_model.id})")
            
            # If still not found, find the most popular equity portfolio
            if not stock_model:
                self.logger.info("Inertia Core Portfolio not found, finding most popular equity portfolio")
                # Query for security models with most assignments
                from sqlalchemy import func
                most_popular = db.session.query(
                    SecurityAllocationModel.id,
                    SecurityAllocationModel.name,
                    func.count(ModelAssignment.id).label('assignment_count')
                ).outerjoin(
                    ModelAssignment, 
                    ModelAssignment.stock_model_id == SecurityAllocationModel.id
                ).filter(
                    SecurityAllocationModel.asset_class_id == equity_asset_class.id
                ).group_by(
                    SecurityAllocationModel.id,
                    SecurityAllocationModel.name
                ).order_by(
                    func.count(ModelAssignment.id).desc()
                ).first()
                
                if most_popular:
                    stock_model = SecurityAllocationModel.query.get(most_popular.id)
                    self.logger.info(f"Found most popular equity portfolio: {stock_model.name} (ID: {stock_model.id}) with {most_popular.assignment_count} assignments")
                    # Verify if it's Inertia Core
                    if 'inertia core' in stock_model.name.lower():
                        self.logger.info(f"Confirmed: Most popular portfolio is Inertia Core ({stock_model.name})")
                else:
                    # Last resort: get any equity security model
                    stock_model = SecurityAllocationModel.query.filter_by(
                        asset_class_id=equity_asset_class.id
                    ).first()
                    if stock_model:
                        self.logger.info(f"Using first available equity portfolio: {stock_model.name} (ID: {stock_model.id})")
            
            if not stock_model:
                self.logger.error("No equity security allocation model found in database - cannot create default model assignment")
                return None
            
            # Create model assignment
            model_assignment = ModelAssignment(
                client_id=client.id,
                asset_model_id=asset_model.id,
                stock_model_id=stock_model.id,
                assigned_at=datetime.utcnow(),
                assigned_by=admin_user.id
            )
            db.session.add(model_assignment)
            db.session.commit()
            
            self.logger.info(f"Created default model assignment for client {client.id}: Asset Model={asset_model.name}, Stock Model={stock_model.name}")
            return model_assignment
            
        except Exception as e:
            self.logger.error(f"Error creating default model assignment for client {client.id}: {str(e)}", exc_info=True)
            db.session.rollback()
            return None
    
    @staticmethod
    def _derive_asset_class(security):
        """Derive asset class from security properties (shared utils; read-only)."""
        from utils.security_asset_class import derive_asset_class
        return derive_asset_class(security)
    
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
                    average_price = float(holding.average_price) if holding.average_price else 0.0
                    # CRITICAL: Ensure both values are float before multiplication
                    from decimal import Decimal
                    quantity_float = float(quantity) if not isinstance(quantity, Decimal) else float(quantity)
                    current_price_float = float(current_price) if not isinstance(current_price, Decimal) else float(current_price)
                    current_value = quantity_float * current_price_float
                    total_portfolio_value += current_value
                    
                    self.logger.debug(f"Holding: {holding.security.symbol}, Qty: {quantity}, Price: {current_price}, Value: {current_value}")
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
                    'average_price': average_price,
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

        from services.recommendation_multi_asset_service import class_total_from_state, weight_within_class

        analyzed_state = {
            'current_asset_allocations': current_asset_allocations,
        }
        for holding_data in current_holdings.values():
            ac_total = class_total_from_state(holding_data.get('asset_class'), analyzed_state)
            holding_data['current_weight_in_class'] = weight_within_class(
                holding_data.get('current_value', 0), ac_total
            )
        
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
            self.logger.error(f"Client {client.id} has model assignment (ID: {model_assignment.id}) but asset_model is None. "
                            f"asset_model_id={model_assignment.asset_model_id}. "
                            f"This should have been fixed by _get_client_models().")
            return asset_recommendations
        
        # Check if asset_model has any allocations
        if not model_assignment.asset_model.asset_allocations:
            self.logger.warning(f"Client {client.id} has asset_model (ID: {model_assignment.asset_model.id}, "
                              f"name: {model_assignment.asset_model.name}) but it has no asset_allocations. "
                              f"Returning empty recommendations.")
            return asset_recommendations
        
        total_portfolio_value = current_state['total_portfolio_value']
        current_asset_weights = current_state['current_asset_weights']
        current_asset_allocations = current_state['current_asset_allocations']
        
        # Get target asset allocations from model
        target_asset_allocations = {}
        model_asset_classes = set()
        for allocation in model_assignment.asset_model.asset_allocations:
            target_asset_allocations[allocation.asset_class.name] = float(allocation.allocation_percentage)
            model_asset_classes.add(allocation.asset_class.name)
        
        from utils.asset_allocation import (
            action_from_required_change,
            build_non_model_asset_row,
            lookup_allocation_value,
            normalize_model_asset_row,
            portfolio_class_in_model,
            sum_model_covered_value,
        )

        # Calculate model-covered portfolio value (alias-aware)
        model_covered_value = sum_model_covered_value(current_asset_allocations, model_asset_classes)
        
        # Calculate target model-covered value (add investment)
        target_model_covered_value = model_covered_value + investment_amount
        
        # Process ALL assets (both model and non-model)
        # CRITICAL FIX: Include model asset classes even if client has no holdings
        # This ensures new clients without holdings still see all asset classes from their model
        all_asset_classes = set(current_asset_allocations.keys())
        # Add all model asset classes to ensure they're included even with 0 holdings
        all_asset_classes.update(model_asset_classes)
        
        for asset_class_name in all_asset_classes:
            in_model = asset_class_name in model_asset_classes or portfolio_class_in_model(
                asset_class_name, model_asset_classes
            )
            if in_model and asset_class_name not in model_asset_classes:
                # Portfolio key aliases to model class (e.g. Debt → Fixed Income): skip duplicate row
                continue

            current_weight = current_asset_weights.get(asset_class_name, 0)
            current_value = current_asset_allocations.get(asset_class_name, 0)
            
            # Check if this asset class is in the allocation model
            if asset_class_name in model_asset_classes:
                current_value = lookup_allocation_value(asset_class_name, current_asset_allocations)
                total_pv = float(total_portfolio_value or 0)
                current_weight = (float(current_value) / total_pv * 100) if total_pv > 0 else 0.0
                # Apply model allocation percentages
                target_weight = target_asset_allocations[asset_class_name]
                
                self.logger.debug(f"Processing MODEL asset class: {asset_class_name}")
                self.logger.debug(f"  target_weight: {target_weight} (type: {type(target_weight)})")
                self.logger.debug(f"  target_model_covered_value: {target_model_covered_value} (type: {type(target_model_covered_value)})")
                
                # Calculate target value based on model-covered value only
                target_value = (float(target_model_covered_value) * float(target_weight) / 100.0)
                
                # Calculate required change
                # If there's an investment amount, ensure it's allocated according to target weights
                required_change = target_value - float(current_value)
                
                # If investment_amount > 0 and required_change is 0 (portfolio already at target),
                # allocate investment amount according to target weights
                if investment_amount > 0 and abs(required_change) < 0.01:
                    # Allocate investment amount proportionally to target weight
                    required_change = (float(investment_amount) * float(target_weight) / 100.0)
                    # Recalculate target_value to include this allocation
                    target_value = float(current_value) + required_change
                    self.logger.debug(f"  Portfolio at target, allocating {required_change} from investment to {asset_class_name}")
                
                # Determine if change is significant (more than 1% of portfolio)
                # For clients without holdings (total_portfolio_value == 0), always include model asset classes
                if total_portfolio_value > 0:
                    significant_change = abs(required_change) > (float(total_portfolio_value) * 0.01)
                else:
                    # For new clients without holdings, always show model asset classes
                    significant_change = True
                
                # Include asset recommendation if:
                # 1. Change is significant, OR
                # 2. There's an investment amount, OR
                # 3. Client has no holdings (to show asset allocation table)
                if significant_change or investment_amount != 0 or total_portfolio_value == 0:
                    row = normalize_model_asset_row({
                        'asset_class': asset_class_name,
                        'current_value': current_value,
                        'current_weight': current_weight,
                        'target_value': target_value,
                        'target_weight': target_weight,
                        'required_change': required_change,
                        'action': action_from_required_change(required_change),
                        'priority': 1 if required_change > 0 else 2,  # Buy recommendations first
                        'significant_change': significant_change,
                        'in_model': True,
                    })
                    asset_recommendations.append(row)
            else:
                # Outside model — target is 0% (do not copy current into target; totals stay ≤ 100%)
                self.logger.debug(f"Processing NON-MODEL asset class: {asset_class_name}")
                row = build_non_model_asset_row(
                    asset_class_name, current_value, current_weight
                )
                row['priority'] = 3
                row['significant_change'] = False
                asset_recommendations.append(row)
        
        # Sort by priority and absolute change amount
        asset_recommendations.sort(key=lambda x: (x['priority'], abs(x['required_change'])), reverse=True)
        
        return asset_recommendations
    
    def _calculate_security_recommendations(
        self, 
        client: Client, 
        model_assignment: ModelAssignment, 
        current_state: Dict, 
        asset_recommendations: List[Dict], 
        investment_amount: float,
        manually_selected: List[int] = None,
        manually_rejected: List[int] = None
    ) -> List[Dict]:
        """Calculate security-level recommendations with proper prioritization
        
        Args:
            manually_selected: List of security IDs that user manually selected (must be included first)
            manually_rejected: List of security IDs that user rejected (must be excluded)
        """
        security_recommendations = []
        manually_selected = manually_selected or []
        manually_rejected = manually_rejected or []
        
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
        
        # Add total_portfolio_value to categorized for use in calculations
        categorized_securities['total_portfolio_value'] = total_portfolio_value
        
        if investment_amount > 0:
            # Investment scenario - prioritize buys
            self.logger.debug("Allocating buy recommendations...")
            self.logger.info(f"User selections: {manually_selected}, User rejections: {manually_rejected}")
            # Add current_holdings to categorized_securities for use in allocation
            categorized_securities['current_holdings'] = current_state.get('current_holdings', {})
            security_recommendations = self._allocate_buy_recommendations(
                categorized_securities, asset_recommendations, investment_amount,
                current_state=current_state,
                manually_selected=manually_selected, manually_rejected=manually_rejected,
                model_assignment=model_assignment
            )
        else:
            # Withdrawal scenario - prioritize sells
            self.logger.debug("Allocating sell recommendations...")
            security_recommendations = self._allocate_sell_recommendations(
                categorized_securities, abs(investment_amount),
                manually_selected=manually_selected, manually_rejected=manually_rejected
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
        
        self.logger.info(f"Loaded {len(model_securities)} securities from model with target weights")
        if len(model_securities) > 0:
            sample_ids = list(model_securities.keys())[:3]
            for sid in sample_ids:
                self.logger.debug(f"  Security {sid}: {model_securities[sid]}%")
        
        for security in all_securities:
            current_holding = current_holdings.get(security.id, {
                'current_value': 0,
                'current_weight': 0,
                'quantity': 0
            })
            
            current_weight = current_holding['current_weight']
            target_weight = model_securities.get(security.id, 0)
            
            # Log if security is in model but target_weight is 0 (shouldn't happen)
            if security.id in model_securities and target_weight == 0:
                self.logger.warning(f"Security {security.id} ({security.symbol}) is in model_securities but target_weight is 0")
            elif security.id not in model_securities:
                self.logger.debug(f"Security {security.id} ({security.symbol}) not in model_securities")
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
        investment_amount: float,
        current_state: Dict = None,
        manually_selected: List[int] = None,
        manually_rejected: List[int] = None,
        model_assignment = None
    ) -> List[Dict]:
        """Allocate buy recommendations with proper prioritization using unified logic
        
        Args:
            manually_selected: List of security IDs that user manually selected (priority 0)
            manually_rejected: List of security IDs that user rejected (excluded from allocation)
        """
        recommendations = []
        manually_selected = manually_selected or []
        manually_rejected = set(manually_rejected or [])
        
        total_portfolio_value = categorized_securities.get('total_portfolio_value', 0.0)
        total_future_portfolio = total_portfolio_value + investment_amount
        
        # Step 1: Add user-selected securities to categorized list with priority 0
        user_selected_list = []
        if manually_selected:
            self.logger.info(f"Adding {len(manually_selected)} user-selected securities with priority 0")
            for security_id in manually_selected:
                security = Security.query.get(security_id)
                if not security:
                    self.logger.warning(f"User-selected security {security_id} not found in database, skipping")
                    continue
                
                # Allow user-selected securities even without current_price (like hot stocks)
                # They will appear with amount 0.0 for user to set manually
                has_price = security.current_price and security.current_price > 0
                
                # Get security data from categorized securities if available
                security_data = None
                for category in ['hot_stocks_underallocated', 'hot_stocks_allocated', 
                               'model_stocks_underallocated', 'model_stocks_overallocated']:
                    for stock in categorized_securities.get(category, []):
                        if stock['security_id'] == security_id:
                            security_data = stock.copy()
                            break
                    if security_data:
                        break
                
                # If not in categorized, get from current holdings or use defaults
                if not security_data:
                    current_holdings = current_state.get('current_holdings', {}) if current_state else {}
                    holding = current_holdings.get(security_id, {})
                    
                    # Try to get target_weight from model_assignment
                    target_weight = 0
                    if model_assignment and model_assignment.stock_model:
                        for allocation in model_assignment.stock_model.security_allocations:
                            if allocation.security_id == security_id:
                                target_weight = float(allocation.allocation_percentage)
                                break
                    
                    security_data = {
                        'security_id': security_id,
                        'symbol': security.symbol,
                        'security_name': security.name,
                        'asset_class': security.asset_class.name if security.asset_class else 'Unknown',
                        'current_weight': holding.get('current_weight', 0),
                        'target_weight': target_weight,
                        'current_price': security.current_price if has_price else None
                    }
                
                # Mark as user-selected with priority 0
                security_data['priority'] = 0
                security_data['reason'] = 'User selected'
                security_data['has_price'] = has_price  # Track if price is available
                user_selected_list.append(security_data)
                
                if not has_price:
                    self.logger.info(f"User-selected security {security_id} ({security.symbol}) has no current_price, will appear with amount 0.0 for manual entry")
        
        categorized_securities['user_selected'] = user_selected_list
        
        # Step 2: Filter out rejected securities from ALL categories
        for category in ['user_selected', 'hot_stocks_underallocated', 'hot_stocks_allocated',
                        'model_stocks_underallocated', 'model_stocks_overallocated']:
            if category in categorized_securities:
                categorized_securities[category] = [
                    s for s in categorized_securities[category]
                    if s['security_id'] not in manually_rejected
                ]
        
        # Step 3: Create unified list with priorities and sort
        all_securities_to_process = []
        
        # Priority 0: User-selected
        for stock_data in categorized_securities.get('user_selected', []):
            stock_data['priority'] = 0
            all_securities_to_process.append(stock_data)
        
        # Priority 1: Hot stocks underallocated
        for stock_data in categorized_securities.get('hot_stocks_underallocated', []):
            stock_data['priority'] = 1
            stock_data['reason'] = 'Hot stock underallocated'
            all_securities_to_process.append(stock_data)
        
        # Priority 2: Hot stocks allocated (additional allocation)
        for stock_data in categorized_securities.get('hot_stocks_allocated', []):
            stock_data['priority'] = 2
            stock_data['reason'] = 'Hot stock additional allocation'
            all_securities_to_process.append(stock_data)
        
        # Priority 3: Model stocks underallocated
        for stock_data in categorized_securities.get('model_stocks_underallocated', []):
            stock_data['priority'] = 3
            stock_data['reason'] = 'Model stock underallocated'
            all_securities_to_process.append(stock_data)
        
        # Sort by priority (0 = highest, 3 = lowest)
        all_securities_to_process.sort(key=lambda x: x.get('priority', 999))

        # Step 4: Select the final Section 1 list first (then allocate equitably)
        # This avoids the "first 1-2 names consume all funds" behavior.
        processed_security_ids = set()
        selected = []

        desired_count = max(8, len(categorized_securities.get('user_selected', [])))

        for stock_data in all_securities_to_process:
            security_id = stock_data.get('security_id')
            if not security_id or security_id in processed_security_ids:
                continue
            if security_id in manually_rejected:
                continue

            priority = int(stock_data.get('priority', 999) or 999)
            target_weight = float(stock_data.get('target_weight', 0) or 0)

            security = Security.query.get(security_id)
            if not security:
                continue

            has_price = stock_data.get('has_price', (security.current_price and security.current_price > 0))

            # Include user-selected even without price (will be amount=0)
            if priority == 0 and not has_price:
                selected.append((stock_data, security, False))
                processed_security_ids.add(security_id)
                continue

            # For non-user-selected: require price
            if not has_price:
                continue

            # Keep previous skip behavior for categories that need target_weight
            if priority in (1, 3) and target_weight <= 0:
                continue

            selected.append((stock_data, security, True))
            processed_security_ids.add(security_id)

            if len(selected) >= desired_count:
                break

        if not selected:
            return []

        # Split into priced vs unpriced (unpriced are user-selected placeholders)
        priced = [(sd, sec) for (sd, sec, ok_price) in selected if ok_price]
        unpriced = [(sd, sec) for (sd, sec, ok_price) in selected if not ok_price]

        # Allocate funds across priced securities in a relatively even way:
        # - minimum 5% each (if possible)
        # - maximum 30% each
        # - remaining distributed by a gentle priority + target-weight blend
        total_funds = float(investment_amount or 0.0)
        if total_funds <= 0 or not priced:
            # Return user-selected placeholders only
            for stock_data, security in unpriced:
                recommendations.append({
                    'security_id': stock_data.get('security_id'),
                    'security_name': stock_data.get('security_name', security.name),
                    'symbol': stock_data.get('symbol', security.symbol),
                    'asset_class': stock_data.get('asset_class', 'Unknown'),
                    'amount': 0.0,
                    'quantity': 0,
                    'action': 'BUY',
                    'reason': stock_data.get('reason', 'User selected'),
                    'priority': int(stock_data.get('priority', 0) or 0),
                    'current_weight': float(stock_data.get('current_weight', 0) or 0),
                    'target_weight': float(stock_data.get('target_weight', 0) or 0),
                    'current_price': None
                })
            return recommendations

        MIN_ALLOCATION_PERCENT = 0.05
        MAX_ALLOCATION_PERCENT = 0.30

        min_alloc = total_funds * MIN_ALLOCATION_PERCENT
        max_alloc = total_funds * MAX_ALLOCATION_PERCENT

        allocated = {}
        remaining = total_funds

        # First pass: minimum allocation each
        for stock_data, security in priced:
            sid = stock_data.get('security_id')
            amt = min(min_alloc, remaining)
            allocated[sid] = float(amt)
            remaining -= float(min_alloc)

        # If not enough for minimums, redistribute proportionally by priority
        if remaining < 0:
            total_priority_weight = 0.0
            weights = {}
            for stock_data, _security in priced:
                pr = float(int(stock_data.get('priority', 999) or 999) + 1)
                w = 1.0 / pr
                weights[stock_data.get('security_id')] = w
                total_priority_weight += w
            for sid, w in weights.items():
                allocated[sid] = float(total_funds) * (w / total_priority_weight) if total_priority_weight > 0 else (float(total_funds) / len(priced))
            remaining = 0.0

        # Second pass: distribute remaining by blended weights with cap
        if remaining > 0:
            wsum = 0.0
            weights = {}
            for stock_data, _security in priced:
                pr = float(int(stock_data.get('priority', 999) or 999) + 1)
                priority_factor = (1.0 / pr) ** 0.5  # gentle curve
                tw = float(stock_data.get('target_weight', 0) or 0)
                target_weight_factor = max(tw, 0.1)
                w = (0.6 * priority_factor) + (0.4 * (target_weight_factor / 100.0))
                sid = stock_data.get('security_id')
                weights[sid] = w
                wsum += w

            if wsum > 0:
                for stock_data, _security in priced:
                    sid = stock_data.get('security_id')
                    add = remaining * (weights.get(sid, 0.0) / wsum)
                    new_total = allocated.get(sid, 0.0) + add
                    if new_total > max_alloc:
                        add = max(0.0, max_alloc - allocated.get(sid, 0.0))
                    allocated[sid] = allocated.get(sid, 0.0) + float(add)

        # Build recommendations (whole-share rounding)
        for stock_data, security in unpriced:
            recommendations.append({
                'security_id': stock_data.get('security_id'),
                'security_name': stock_data.get('security_name', security.name),
                'symbol': stock_data.get('symbol', security.symbol),
                'asset_class': stock_data.get('asset_class', 'Unknown'),
                'amount': 0.0,
                'quantity': 0,
                'action': 'BUY',
                'reason': stock_data.get('reason', 'User selected'),
                'priority': int(stock_data.get('priority', 0) or 0),
                'current_weight': float(stock_data.get('current_weight', 0) or 0),
                'target_weight': float(stock_data.get('target_weight', 0) or 0),
                'current_price': None
            })

        priced_recs = []
        for stock_data, security in priced:
            sid = stock_data.get('security_id')
            symbol = stock_data.get('symbol', security.symbol)
            current_weight = float(stock_data.get('current_weight', 0) or 0)
            target_weight = float(stock_data.get('target_weight', 0) or 0)
            priority = int(stock_data.get('priority', 999) or 999)
            price = float(security.current_price) if security.current_price else 0.0
            desired_amt = float(allocated.get(sid, 0.0))
            qty = int(desired_amt / price) if price > 0 else 0
            actual_amt = float(qty) * price if qty > 0 else 0.0
            rec = {
                'security_id': sid,
                'security_name': stock_data.get('security_name', security.name),
                'symbol': symbol,
                'asset_class': stock_data.get('asset_class', 'Unknown'),
                'amount': actual_amt,
                'quantity': qty,
                'action': 'BUY',
                'reason': stock_data.get('reason', 'Recommended'),
                'priority': priority,
                'current_weight': current_weight,
                'target_weight': target_weight,
                'current_price': security.current_price
            }
            priced_recs.append({
                'rec': rec,
                'price': price,
                'priority': priority
            })
            recommendations.append(rec)

        # Allocate any remaining funds that were lost due to whole-share rounding.
        allocated_total = sum(r.get('amount', 0.0) for r in recommendations)
        remaining_after_rounding = max(0.0, float(total_funds) - float(allocated_total))

        if remaining_after_rounding > 0 and priced_recs:
            # Prefer lower price first, then higher priority
            priced_recs.sort(key=lambda x: (x['price'] or float('inf'), x['priority']))
            min_price = min((r['price'] for r in priced_recs if r['price'] > 0), default=0.0)

            # Distribute one share at a time to use remaining funds
            while remaining_after_rounding >= min_price and min_price > 0:
                allocated_any = False
                for pr in priced_recs:
                    price = pr['price'] or 0.0
                    if price <= 0:
                        continue
                    if remaining_after_rounding >= price:
                        pr['rec']['quantity'] = int(pr['rec'].get('quantity', 0)) + 1
                        pr['rec']['amount'] = float(pr['rec']['quantity']) * price
                        remaining_after_rounding -= price
                        allocated_any = True
                if not allocated_any:
                    break

        self.logger.info(
            f"Equitable allocation complete: {len(recommendations)} recommendations across {len(priced)} priced securities. "
            f"Allocated={sum(r.get('amount', 0.0) for r in recommendations):.2f}, Remaining={remaining_after_rounding:.2f}"
        )
        return recommendations
    
    def _allocate_sell_recommendations(
        self, 
        categorized_securities: Dict, 
        withdrawal_amount: float,
        manually_selected: List[int] = None,
        manually_rejected: List[int] = None
    ) -> List[Dict]:
        """Allocate sell recommendations based on criteria"""
        recommendations = []
        remaining_amount = withdrawal_amount
        manually_selected = set(manually_selected or [])
        manually_rejected = set(manually_rejected or [])
        
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
                    
                    # Recalculate actual sell_amount based on integer quantity
                    actual_sell_amount = float(quantity) * float(security.current_price) if quantity > 0 else 0.0
                    
                except Exception as e:
                    self.logger.error(f"Error calculating quantity for {symbol}: {e}")
                    quantity = 0
                    actual_sell_amount = 0.0
                
                if actual_sell_amount > 0:
                    recommendations.append({
                        'security_id': security_id,
                        'security_name': stock_data['security_name'],
                        'symbol': symbol,
                        'asset_class': stock_data['asset_class'],
                        'amount': actual_sell_amount,  # This is now quantity * price
                        'quantity': quantity,
                        'action': 'SELL',
                        'reason': 'Stock removed from model',
                        'priority': 1,
                        'current_weight': stock_data['current_weight'],
                        'target_weight': 0
                    })
                    remaining_amount -= actual_sell_amount
        
        # 2. SELL stocks with significant overallocation (>50% over target)
        # 4. Handle overallocated stocks (unless rejected)
        model_stocks_overallocated = categorized_securities['model_stocks_overallocated']
        for stock_data in model_stocks_overallocated:
            if remaining_amount <= 0:
                break
            
            security_id = stock_data['security_id']
            
            # Skip if rejected by user
            if security_id in manually_rejected:
                continue
            
            # Skip if already added as user selection
            if security_id in manually_selected:
                continue
            symbol = stock_data['symbol']
            target_weight = stock_data['target_weight']
            current_weight = stock_data['current_weight']
            
            # Get security object for price calculation
            security = Security.query.get(security_id)
            if not security or not security.current_price:
                continue
            
            # Only suggest sell if >50% over target
            if stock_data['allocation_ratio'] > 1.5:
                total_portfolio_value = categorized_securities.get('total_portfolio_value', 0.0)
                excess_amount = self._calculate_excess_amount(
                    security, target_weight, current_weight, total_portfolio_value
                )
                
                sell_amount = min(excess_amount, remaining_amount)
                
                if sell_amount > 0:
                    try:
                        # Calculate quantity as integer (whole number)
                        quantity = int(sell_amount / float(security.current_price)) if security.current_price else 0
                        
                        # Recalculate actual sell_amount based on integer quantity
                        actual_sell_amount = float(quantity) * float(security.current_price) if quantity > 0 else 0.0
                        
                    except Exception as e:
                        self.logger.error(f"Error calculating quantity for {symbol}: {e}")
                        quantity = 0
                        actual_sell_amount = 0.0
                    
                    if actual_sell_amount > 0:
                        recommendations.append({
                            'security_id': security_id,
                            'security_name': stock_data['security_name'],
                            'symbol': symbol,
                            'asset_class': stock_data['asset_class'],
                            'amount': actual_sell_amount,  # This is now quantity * price
                            'quantity': quantity,
                            'action': 'SELL',
                            'reason': f'Significant overallocation ({stock_data["allocation_ratio"]:.1f}x target)',
                            'priority': 2,
                            'current_weight': current_weight,
                            'target_weight': target_weight
                        })
                        remaining_amount -= actual_sell_amount
        
        return recommendations
    
    def _calculate_required_amount_to_target(
        self, 
        security: Security, 
        target_weight: float, 
        current_weight: float,
        total_portfolio_value_after_investment: float
    ) -> float:
        """Calculate amount needed to reach target weight based on portfolio value after investment"""
        if target_weight <= current_weight:
            return 0
        
        # Calculate target value based on portfolio value AFTER investment
        target_value = (float(target_weight) / 100.0) * float(total_portfolio_value_after_investment)
        
        # Calculate based on target weight of final portfolio
        # Target value = target_weight% of final portfolio (after investment)
        # Current value = current_weight% of base portfolio (before investment)
        # Amount needed = Target value - Current value
        
        # Since we don't know exact base portfolio, we approximate:
        # If current_weight is X% and target is Y%, and final portfolio is P_final
        # We estimate: base_portfolio ≈ P_final - investment_estimate
        # For allocation purposes, we can use a simpler approach:
        # required_amount ≈ (Y% - X%) * P_final / (1 - X%/100)
        # But to keep it simple and conservative:
        estimated_current_portfolio = total_portfolio_value_after_investment * 0.9  # Conservative estimate
        current_value = (float(current_weight) / 100.0) * estimated_current_portfolio if estimated_current_portfolio > 0 else 0
        
        # Return the difference needed (this will be rounded to whole shares later)
        return max(0, target_value - current_value)
    
    def _calculate_excess_amount(
        self, 
        security: Security, 
        target_weight: float, 
        current_weight: float,
        total_portfolio_value: float
    ) -> float:
        """Calculate excess amount for overallocated securities"""
        if current_weight <= target_weight:
            return 0
        
        # Calculate excess value based on portfolio value
        current_value = (float(current_weight) / 100.0) * float(total_portfolio_value)
        target_value = (float(target_weight) / 100.0) * float(total_portfolio_value)
        
        return max(0, current_value - target_value)
    
    def _format_security_recommendation(
        self,
        security: Security,
        current_state: Dict,
        target_weight: float = 0.0,
        amount: float = 0.0,
        action: str = 'HOLD',
        reason: str = '',
        is_recommended: bool = False,
        section: str = 'other',
        investment_amount: float = 0.0,
        client_id: int = None
    ) -> Dict:
        """Format a security recommendation with unified structure for all sections"""
        current_holdings = current_state.get('current_holdings', {})
        total_portfolio_value = current_state.get('total_portfolio_value', 0.0)
        
        # ALWAYS try to retrieve target_weight from security allocation model if available
        # This ensures we have the correct value even if it was passed as 0
        client_id_to_use = current_state.get('client_id') or client_id
        
        if client_id_to_use:
            try:
                client_obj = Client.query.get(client_id_to_use)
                if client_obj:
                    model_assignment = self._get_client_models(client_obj)
                    if model_assignment and model_assignment.stock_model:
                        for allocation in model_assignment.stock_model.security_allocations:
                            if allocation.security_id == security.id:
                                retrieved_weight = float(allocation.allocation_percentage)
                                # Only override if we successfully retrieved a non-zero value, or if current value is 0
                                if retrieved_weight > 0 or target_weight == 0:
                                    target_weight = retrieved_weight
                                    if retrieved_weight > 0:
                                        self.logger.debug(f"Retrieved target_weight from model for {security.symbol} (id={security.id}): {target_weight}%")
                                    break
                    else:
                        if target_weight == 0:
                            self.logger.debug(f"No model_assignment or stock_model found for client {client_id_to_use}, security {security.symbol}")
                else:
                    if target_weight == 0:
                        self.logger.warning(f"Client {client_id_to_use} not found in database")
            except Exception as e:
                if target_weight == 0:
                    self.logger.warning(f"Could not retrieve target_weight from model for {security.symbol} (id={security.id}): {e}", exc_info=True)
        else:
            if target_weight == 0:
                self.logger.debug(f"No client_id available to retrieve target_weight for {security.symbol} (id={security.id})")
        
        # NOTE: `current_holdings` is sometimes stored in session / JSON, which can coerce integer
        # dict keys into strings. Support both to avoid incorrectly treating held securities as 0%.
        holding_data = (
            current_holdings.get(security.id)
            or current_holdings.get(str(security.id))
            or {
            'current_value': 0.0,
            'current_weight': 0.0,
            'quantity': 0.0,
            'current_price': 0.0,
            'average_price': 0.0
            }
        )
        
        current_value = holding_data.get('current_value', 0.0)
        current_weight = holding_data.get('current_weight', 0.0)
        current_quantity = holding_data.get('quantity', 0.0)
        average_price = holding_data.get('average_price', 0.0)
        
        # Get current price - CRITICAL: Ensure it's always float
        from decimal import Decimal
        if security.current_price:
            current_price = float(security.current_price) if not isinstance(security.current_price, Decimal) else float(security.current_price)
        else:
            current_price = 0.0
        
        # CRITICAL: If current_value is 0 but we have quantity and price, recalculate current_value
        # This handles cases where the stock exists in portfolio but current_value wasn't set correctly
        if current_value == 0.0 and current_quantity > 0 and current_price > 0:
            # CRITICAL: Ensure both values are float before multiplication
            from decimal import Decimal
            current_quantity_float = float(current_quantity) if not isinstance(current_quantity, Decimal) else float(current_quantity)
            current_price_float = float(current_price) if not isinstance(current_price, Decimal) else float(current_price)
            current_value = current_quantity_float * current_price_float
            self.logger.debug(f"Recalculated current_value for {security.symbol}: {current_quantity} * {current_price} = {current_value}")
        
        # Fallback: Calculate price from value and quantity if price is missing
        if current_price == 0 and current_value > 0 and current_quantity > 0:
            # Ensure both are float before division
            current_value_float = float(current_value) if not isinstance(current_value, Decimal) else float(current_value)
            current_quantity_float = float(current_quantity) if not isinstance(current_quantity, Decimal) else float(current_quantity)
            current_price = current_value_float / current_quantity_float
        
        # Calculate quantity from signed trade amount (Section 1: negative qty = SELL)
        quantity = 0
        amount_float = float(amount) if amount is not None else 0.0
        if isinstance(amount, Decimal):
            amount_float = float(amount)
        explicit_action = (action or 'HOLD').strip().upper()
        if explicit_action == 'SELL' and amount_float > 0:
            amount_float = -abs(amount_float)
        elif explicit_action == 'BUY' and amount_float < 0:
            amount_float = abs(amount_float)

        if current_price > 0 and amount_float != 0:
            current_price_float = float(current_price) if not isinstance(current_price, Decimal) else float(current_price)
            quantity = int(round(amount_float / current_price_float))
            amount_float = float(quantity) * current_price_float
        amount = amount_float

        if quantity > 0:
            action = 'BUY'
        elif quantity < 0:
            action = 'SELL'
        elif explicit_action in ('BUY', 'SELL'):
            action = explicit_action
        
        # Calculate current_weight WITHIN the asset class, not as percentage of total portfolio
        # Get the asset class for this security using derived asset class logic
        asset_class_name = self._derive_asset_class(security)
        current_asset_allocations = current_state.get('current_asset_allocations', {})
        # Resolve asset_class_total with normalized aliases (Fixed Income <-> Debt, REITs <-> REIT/InvIT)
        # so weights populate even when display names differ from derived names
        asset_class_total = float(current_asset_allocations.get(asset_class_name, 0) or 0)
        if asset_class_total == 0 and current_asset_allocations:
            if asset_class_name in ('Fixed Income', 'Debt'):
                other = 'Debt' if asset_class_name == 'Fixed Income' else 'Fixed Income'
                asset_class_total = float(current_asset_allocations.get(other, 0) or 0)
            elif asset_class_name in ('REITs', 'REIT/InvIT', 'REIT/INVIT'):
                for k in ('REITs', 'REIT/InvIT', 'REIT/INVIT'):
                    if k != asset_class_name:
                        v = float(current_asset_allocations.get(k, 0) or 0)
                        if v > 0:
                            asset_class_total = v
                            break
        
        # Recalculate current_weight as percentage within the asset class (never portfolio %).
        from services.recommendation_multi_asset_service import weight_within_class

        in_class = holding_data.get('current_weight_in_class')
        if in_class is not None:
            current_weight = float(in_class)
        else:
            current_weight = weight_within_class(current_value, asset_class_total)
            self.logger.debug(
                f"Weight calc for {security.symbol}: value={current_value}, "
                f"{asset_class_name}_total={asset_class_total}, weight={current_weight}%"
            )

        # Calculate unrealized P/L based on holding average price (so Section 1 always shows P/L)
        unrealized_pl = 0.0
        unrealized_pl_percent = 0.0
        try:
            avg_price_float = float(average_price) if average_price is not None and average_price != '' else 0.0
            if isinstance(average_price, Decimal):
                avg_price_float = float(average_price)
            quantity_float = float(current_quantity) if current_quantity is not None and current_quantity != '' else 0.0
            if isinstance(current_quantity, Decimal):
                quantity_float = float(current_quantity)
            # When average_price is missing/zero but we have value and quantity, use implied avg price so we can show 0% P/L
            if avg_price_float <= 0 and current_value > 0 and quantity_float > 0:
                current_value_f = float(current_value) if not isinstance(current_value, Decimal) else float(current_value)
                avg_price_float = current_value_f / quantity_float
            cost_basis = avg_price_float * quantity_float
            if cost_basis > 0:
                current_value_f = float(current_value) if not isinstance(current_value, Decimal) else float(current_value)
                unrealized_pl = current_value_f - cost_basis
                unrealized_pl_percent = (unrealized_pl / cost_basis) * 100
        except Exception:
            unrealized_pl = 0.0
            unrealized_pl_percent = 0.0
        
        # Calculate future weight WITHIN the asset class, not as percentage of total portfolio
        # CRITICAL: future denominator should reflect the *asset-class level* investment delta,
        # not just this security's amount. This ensures weights dilute correctly even when a security's qty change is 0.
        future_value = current_value + amount

        asset_class_investment_by_class = current_state.get('asset_class_investment_by_class', {}) if current_state else {}
        asset_class_investment = 0.0
        try:
            # Try exact key match first
            asset_class_investment = float(asset_class_investment_by_class.get(asset_class_name, 0.0) or 0.0)
            # Backward compatibility / naming normalization
            if asset_class_investment == 0.0 and asset_class_name == 'Debt':
                asset_class_investment = float(asset_class_investment_by_class.get('Fixed Income', 0.0) or 0.0)
            if asset_class_investment == 0.0 and asset_class_name in ['REIT/InvIT', 'REIT/INVIT']:
                asset_class_investment = float(asset_class_investment_by_class.get('REITs', 0.0) or 0.0)
        except Exception:
            asset_class_investment = 0.0

        # Future asset class total = current asset class total + asset-class investment delta
        future_asset_class_total = asset_class_total + asset_class_investment
        
        # Calculate future quantity: current_quantity + quantity_change
        # For SELL (negative quantity), future_quantity = current_quantity - abs(quantity)
        future_quantity = current_quantity + quantity  # quantity is signed (negative for SELL)
        
        # Check if selling more than available (negative future quantity) - this is a warning condition
        overselling = action == 'SELL' and future_quantity < 0
        
        # Calculate future weight (can be negative if overselling)
        # For SELL trades: if quantity becomes 0 or negative, future weight should be negative (not 0)
        if action == 'SELL' and future_quantity <= 0:
            # Overselling: show negative future weight
            # Calculate normally - if future_value is negative, weight will be negative
            if future_asset_class_total > 0:
                # Normal case: asset class total is still positive
                future_weight = (future_value / future_asset_class_total) * 100
            elif future_asset_class_total < 0:
                # Asset class total became negative - use absolute value for denominator
                future_weight = (future_value / abs(future_asset_class_total)) * 100
            else:
                future_weight = 0.0
            self.logger.debug(f"SELL trade: future_quantity={future_quantity} <= 0, setting future_weight={future_weight}% for {security.symbol}, future_value={future_value}, future_asset_class_total={future_asset_class_total}")
        elif future_asset_class_total > 0 and future_quantity != 0:
            # Calculate weight based on future value and future asset class total
            # This can be negative if we're selling more than we have
            future_weight = (future_value / future_asset_class_total) * 100
            self.logger.debug(f"Future weight calc for {security.symbol}: future_value={future_value}, future_{asset_class_name}_total={future_asset_class_total}, future_quantity={future_quantity}, future_weight={future_weight}%")
        else:
            future_weight = 0.0
            self.logger.debug(f"Setting future_weight=0 for {security.symbol} (future_value={future_value}, future_quantity={future_quantity}, future_asset_class_total={future_asset_class_total})")

        defer_ltcg_advisory = None
        if action == 'SELL' and client_id_to_use:
            try:
                from services.tax_optimiser_strategy_engine import sell_timing_tax_advisory_for_security

                defer_ltcg_advisory = sell_timing_tax_advisory_for_security(
                    int(client_id_to_use), int(security.id), as_of=date.today()
                )
            except Exception as ex:
                self.logger.debug("sell timing tax advisory skipped for %s: %s", security.symbol, ex)
        if defer_ltcg_advisory and defer_ltcg_advisory not in (reason or ""):
            reason = (reason.strip() + " — " if reason and str(reason).strip() else "") + defer_ltcg_advisory

        return {
            'security_id': security.id,
            'security_name': security.name,
            'symbol': security.symbol,
            'asset_class': asset_class_name,  # Use derived asset class for consistency
            'current_weight': round(float(current_weight), 2),
            'target_weight': round(float(target_weight), 2),
            'future_weight': round(float(future_weight), 2),
            'current_value': round(float(current_value), 2),  # Use recalculated value if it was 0
            'amount': round(float(amount), 2),
            'quantity': int(quantity),
            'current_quantity': int(current_quantity) if current_quantity else 0,
            'current_price': round(float(current_price), 2),
            'average_price': round(float(average_price), 2) if average_price else 0.0,
            'unrealized_pl': round(float(unrealized_pl), 2),
            'unrealized_pl_percent': round(float(unrealized_pl_percent), 2),
            'action': action,
            'reason': reason,
            'defer_ltcg_advisory': defer_ltcg_advisory,
            'is_recommended': is_recommended,
            'is_hot_stock': security.is_hot_stock if hasattr(security, 'is_hot_stock') else False,
            'section': section
        }
    
    def _get_all_hot_stocks_formatted(
        self,
        client_id: int,
        current_state: Dict,
        investment_amount: float,
        excluded_security_ids: List[int] = None,
        asset_recommendations: List[Dict] = None,
        asset_class: str = None
    ) -> List[Dict]:
        """Get all hot stocks in unified format for Section 2
        
        Args:
            asset_class: Optional asset class to filter by (e.g., 'Fixed Income', 'Equity'). 
                        If provided, only hot stocks matching this asset class will be returned.
        """
        from models import Security
        
        excluded_ids = set(excluded_security_ids or [])  # Use set for faster lookup
        
        # Get all hot stocks
        hot_stocks = Security.query.filter_by(is_hot_stock=True).all()
        self.logger.info(f"Found {len(hot_stocks)} hot stocks in database. Excluding {len(excluded_ids)} securities (Section 1 + manually_selected + manually_rejected)")
        
        # Filter by asset class if specified
        if asset_class:
            filtered_hot_stocks = []
            for security in hot_stocks:
                derived_asset_class = self._derive_asset_class(security)
                # Normalize asset class names for comparison
                if asset_class == 'Fixed Income' and derived_asset_class in ['Debt', 'Fixed Income', 'FIXED INCOME']:
                    filtered_hot_stocks.append(security)
                elif asset_class == 'REITs' and derived_asset_class in ['REITs', 'REIT', 'REIT/InvIT', 'REIT/INVIT']:
                    filtered_hot_stocks.append(security)
                elif derived_asset_class == asset_class:
                    filtered_hot_stocks.append(security)
            hot_stocks = filtered_hot_stocks
            self.logger.info(f"Filtered to {len(hot_stocks)} hot stocks matching asset class '{asset_class}'")
        
        # Get model assignments to check target weights
        client = Client.query.get(client_id)
        model_assignment = self._get_client_models(client)
        model_securities = {}
        if model_assignment and model_assignment.stock_model:
            for allocation in model_assignment.stock_model.security_allocations:
                model_securities[allocation.security_id] = float(allocation.allocation_percentage)
        
        hot_stocks_list = []
        total_portfolio_value = current_state.get('total_portfolio_value', 0.0)
        available_amount = max(0, investment_amount)  # Only positive investments
        skipped_no_price = 0
        skipped_excluded = 0
        
        for security in hot_stocks:
            if security.id in excluded_ids:
                skipped_excluded += 1
                continue
            
            # Allow hot stocks even without price - show them with amount 0 for review
            # They can be moved to Section 1 and user can set price/amount manually
            if not security.current_price or security.current_price <= 0:
                skipped_no_price += 1
                # Still include but with 0 amount - user can set manually
                current_price = 0.0
            else:
                current_price = float(security.current_price)
            
            target_weight = model_securities.get(security.id, 0.0)
            
            # Calculate suggested amount for hot stocks (proportional to available investment)
            # Default: 5% of investment per hot stock, or amount needed to reach target
            suggested_amount = 0.0
            if current_price > 0 and available_amount > 0:
                if target_weight > 0:
                    # Calculate amount needed to reach target
                    current_holdings = current_state.get('current_holdings', {})
                    holding = current_holdings.get(security.id, {})
                    current_weight = holding.get('current_weight', 0.0)
                    
                    if current_weight < target_weight:
                        # Calculate required amount
                        target_value = (target_weight / 100.0) * (total_portfolio_value + investment_amount)
                        current_value = holding.get('current_value', 0.0)
                        suggested_amount = max(0, target_value - current_value)
                        suggested_amount = min(suggested_amount, available_amount * 0.2)  # Max 20% per hot stock
                else:
                    # No target, suggest 5% of investment
                    suggested_amount = available_amount * 0.05
            
            formatted = self._format_security_recommendation(
                security=security,
                current_state=current_state,
                target_weight=target_weight,
                amount=suggested_amount,
                action='BUY' if suggested_amount > 0 else 'HOLD',
                reason='Hot stock - suggested for purchase' if suggested_amount > 0 else ('Hot stock - no price' if current_price == 0 else 'Hot stock - review'),
                is_recommended=False,
                section='hot_stocks',
                investment_amount=investment_amount,
                client_id=client_id
            )
            
            hot_stocks_list.append(formatted)
        
        self.logger.info(f"Section 2: Added {len(hot_stocks_list)} hot stocks. Skipped: {skipped_excluded} excluded, {skipped_no_price} no price (but still included)")
        
        # Sort by amount descending
        hot_stocks_list.sort(key=lambda x: x['amount'], reverse=True)
        
        return hot_stocks_list
    
    def _get_all_other_securities_formatted(
        self,
        client_id: int,
        recommended_ids: List[int],
        hot_stock_ids: List[int],
        current_state: Dict,
        model_assignment: ModelAssignment,
        excluded_security_ids: List[int] = None,
        investment_amount: float = 0.0,
        asset_recommendations: List[Dict] = None,
        asset_class: str = None
    ) -> List[Dict]:
        """Get all other securities in unified format for Section 3
        
        Args:
            excluded_security_ids: Additional securities to exclude (manually_selected + manually_rejected)
            asset_class: Optional asset class to filter by (e.g., 'Fixed Income', 'Equity'). 
                        If provided, only securities matching this asset class will be returned.
        """
        from models import Security
        
        # Get model securities
        model_securities = {}
        if model_assignment and model_assignment.stock_model:
            for allocation in model_assignment.stock_model.security_allocations:
                model_securities[allocation.security_id] = float(allocation.allocation_percentage)
        
        # Get all securities: current holdings + model securities
        current_holdings = current_state.get('current_holdings', {})
        held_ids = list(current_holdings.keys())
        model_ids = list(model_securities.keys())
        
        # Combine all exclusions: Section 1 + Section 2 + manually_selected + manually_rejected
        exclude_ids = set(recommended_ids + hot_stock_ids)
        if excluded_security_ids:
            exclude_ids.update(excluded_security_ids)
        
        self.logger.info(f"Section 3: Found {len(held_ids)} holdings, {len(model_ids)} model securities. Excluding {len(exclude_ids)} total (S1: {len(recommended_ids)}, S2: {len(hot_stock_ids)}, additional: {len(excluded_security_ids) if excluded_security_ids else 0})")
        
        # Combine and exclude
        all_relevant_ids = list(set(held_ids + model_ids))
        other_ids = [sid for sid in all_relevant_ids if sid not in exclude_ids]
        
        self.logger.info(f"Section 3: {len(all_relevant_ids)} total relevant IDs, {len(other_ids)} after exclusions")
        
        # Get security objects
        if not other_ids:
            self.logger.warning("Section 3: No securities to show - all are in Section 1 or Section 2")
            return []
        
        securities = Security.query.filter(Security.id.in_(other_ids)).all()
        
        # Filter by asset class if specified
        if asset_class:
            filtered_securities = []
            for security in securities:
                derived_asset_class = self._derive_asset_class(security)
                # Normalize asset class names for comparison
                if asset_class == 'Fixed Income' and derived_asset_class in ['Debt', 'Fixed Income', 'FIXED INCOME']:
                    filtered_securities.append(security)
                elif asset_class == 'REITs' and derived_asset_class in ['REITs', 'REIT', 'REIT/InvIT', 'REIT/INVIT']:
                    filtered_securities.append(security)
                elif derived_asset_class == asset_class:
                    filtered_securities.append(security)
            securities = filtered_securities
            self.logger.info(f"Filtered to {len(securities)} securities matching asset class '{asset_class}'")
        
        other_securities_list = []
        for security in securities:
            target_weight = model_securities.get(security.id, 0.0)
            
            # No suggested amount for Section 3 (user can add manually)
            formatted = self._format_security_recommendation(
                security=security,
                current_state=current_state,
                target_weight=target_weight,
                amount=0.0,
                action='HOLD',
                reason='In model' if security.id in model_ids else 'Current holding - review',
                is_recommended=False,
                section='other',
                investment_amount=investment_amount,
                client_id=client_id
            )
            
            other_securities_list.append(formatted)
        
        # Sort by current value descending (showing holdings first)
        other_securities_list.sort(key=lambda x: x['current_value'], reverse=True)
        
        self.logger.info(f"Section 3: Returning {len(other_securities_list)} securities")
        
        return other_securities_list
    
    def _recalculate_section1_with_exclusions(
        self,
        current_recommendations: List[Dict],
        excluded_security_ids: List[int],
        investment_amount: float,
        current_state: Dict
    ) -> List[Dict]:
        """Recalculate Section 1 recommendations excluding specified securities"""
        if not excluded_security_ids:
            return current_recommendations
        
        # Filter out excluded securities
        remaining_recommendations = [
            rec.copy() for rec in current_recommendations  # Use copy to avoid mutating original
            if rec.get('security_id') not in excluded_security_ids
        ]
        
        # Calculate total excluded amount
        excluded_amount = sum(
            rec.get('amount', 0) for rec in current_recommendations
            if rec.get('security_id') in excluded_security_ids
        )
        
        if excluded_amount <= 0 or not remaining_recommendations:
            return remaining_recommendations
        
        # Redistribute excluded amount proportionally based on target weights
        total_target_weight = sum(rec.get('target_weight', 0) for rec in remaining_recommendations)
        
        if total_target_weight > 0:
            # Distribute proportionally to target weights
            for rec in remaining_recommendations:
                target_weight = rec.get('target_weight', 0)
                if target_weight > 0:
                    proportion = target_weight / total_target_weight
                    additional_amount = excluded_amount * proportion
                    rec['amount'] = rec.get('amount', 0) + additional_amount
                    
                    # Recalculate quantity and future weight using _format_security_recommendation
                    # to ensure all fields are preserved
                    security = Security.query.get(rec.get('security_id'))
                    if security:
                        # Re-format to ensure all fields are present
                        rec_formatted = self._format_security_recommendation(
                            security=security,
                            current_state=current_state,
                            target_weight=rec.get('target_weight', 0),
                            amount=rec['amount'],
                            action=rec.get('action', 'BUY' if rec['amount'] > 0 else 'HOLD'),
                            reason=rec.get('reason', 'Recalculated after exclusions'),
                            is_recommended=True,
                            section='recommended',
                            investment_amount=investment_amount,
                            client_id=current_state.get('client_id')
                        )
                        # Update the rec with formatted data (preserves all fields)
                        rec.update(rec_formatted)
                    else:
                        # Fallback: recalculate manually if security not found
                        if rec.get('current_price', 0) > 0:
                            rec['quantity'] = int(round(abs(rec['amount']) / float(rec.get('current_price', 1))))
                        
                        # Update future weight: (current_value + amount) / (total_portfolio_value + investment_amount) * 100
                        current_value = rec.get('current_value', 0)
                        future_value = current_value + rec['amount']
                        total_portfolio_value = current_state.get('total_portfolio_value', 0.0)
                        total_future_portfolio_value = total_portfolio_value + investment_amount
                        rec['future_weight'] = (future_value / total_future_portfolio_value * 100) if total_future_portfolio_value > 0 else 0.0
        else:
            # If no target weights, distribute equally
            amount_per_security = excluded_amount / len(remaining_recommendations)
            for rec in remaining_recommendations:
                rec['amount'] = rec.get('amount', 0) + amount_per_security
                
                # Recalculate using _format_security_recommendation to preserve all fields
                security = Security.query.get(rec.get('security_id'))
                if security:
                    rec_formatted = self._format_security_recommendation(
                        security=security,
                        current_state=current_state,
                        target_weight=rec.get('target_weight', 0),
                        amount=rec['amount'],
                        action=rec.get('action', 'BUY' if rec['amount'] > 0 else 'HOLD'),
                        reason=rec.get('reason', 'Recalculated after exclusions'),
                        is_recommended=True,
                        section='recommended',
                        investment_amount=investment_amount,
                        client_id=current_state.get('client_id')
                    )
                    rec.update(rec_formatted)
                else:
                    # Fallback calculation
                    if rec.get('current_price', 0) > 0:
                        rec['quantity'] = int(round(abs(rec['amount']) / float(rec.get('current_price', 1))))
                    
                    # Update future weight: (current_value + amount) / (total_portfolio_value + investment_amount) * 100
                    current_value = rec.get('current_value', 0)
                    future_value = current_value + rec['amount']
                    total_portfolio_value = current_state.get('total_portfolio_value', 0.0)
                    total_future_portfolio_value = total_portfolio_value + investment_amount
                    rec['future_weight'] = (future_value / total_future_portfolio_value * 100) if total_future_portfolio_value > 0 else 0.0
        
        return remaining_recommendations
    
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
