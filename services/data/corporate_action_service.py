"""
Corporate Action Service
Handles splits, bonuses, dividends, and price adjustments
Extracted from api/v1/corporate_actions.py and api/v1/adjustments.py
"""
import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
from decimal import Decimal

from models import CorporateAction, Security, HistoricalPrice, Holding, Client
from extensions import db

logger = logging.getLogger(__name__)

class CorporateActionService:
    """Service for managing corporate actions and price adjustments"""
    
    @staticmethod
    def get_corporate_actions(
        security_id: Optional[int] = None,
        action_type: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        is_active: bool = True
    ) -> List[CorporateAction]:
        """
        Get corporate actions with optional filtering
        
        Args:
            security_id: Filter by security ID
            action_type: Filter by action type
            start_date: Filter by start date
            end_date: Filter by end date
            is_active: Filter by active status
        
        Returns:
            List of CorporateAction objects
        """
        try:
            query = CorporateAction.query
            
            if security_id:
                query = query.filter(CorporateAction.security_id == security_id)
            
            if action_type:
                query = query.filter(CorporateAction.action_type == action_type)
            
            if start_date:
                query = query.filter(CorporateAction.action_date >= start_date)
            
            if end_date:
                query = query.filter(CorporateAction.action_date <= end_date)
            
            if is_active is not None:
                query = query.filter(CorporateAction.is_active == is_active)
            
            return query.order_by(CorporateAction.action_date.desc()).all()
            
        except Exception as e:
            logger.error(f"Error getting corporate actions: {str(e)}")
            raise
    
    @staticmethod
    def create_corporate_action(
        security_id: int,
        action_type: str,
        action_date: date,
        ratio: float,
        description: str = "",
        source: str = "MANUAL"
    ) -> CorporateAction:
        """
        Create a new corporate action
        
        Args:
            security_id: ID of the security
            action_type: Type of action (SPLIT, BONUS, DIVIDEND, RIGHTS, MERGER)
            action_date: Date of the corporate action
            ratio: Split ratio, bonus ratio, etc.
            description: Optional description
            source: Source of the data
        
        Returns:
            Created CorporateAction object
        """
        try:
            # Validate security exists
            security = Security.query.get(security_id)
            if not security:
                raise ValueError(f"Security with ID {security_id} not found")
            
            # Validate action type
            valid_types = ['SPLIT', 'BONUS', 'DIVIDEND', 'RIGHTS', 'MERGER']
            if action_type not in valid_types:
                raise ValueError(f"Invalid action_type. Must be one of: {', '.join(valid_types)}")
            
            # Validate ratio
            if ratio <= 0:
                raise ValueError("Ratio must be positive")
            
            # Check for duplicate
            existing = CorporateAction.query.filter(
                CorporateAction.security_id == security_id,
                CorporateAction.action_date == action_date,
                CorporateAction.action_type == action_type
            ).first()
            
            if existing:
                raise ValueError(f"Corporate action already exists for {security.symbol} on {action_date}")
            
            # Create corporate action
            corporate_action = CorporateAction(
                security_id=security_id,
                action_type=action_type,
                action_date=action_date,
                ratio=Decimal(str(ratio)),
                description=description,
                source=source,
                is_active=True
            )
            
            db.session.add(corporate_action)
            db.session.commit()
            
            logger.info(f"Created corporate action: {security.symbol} - {action_type} {ratio} on {action_date}")
            
            # Automatically update holdings for all clients holding this security
            try:
                update_result = CorporateActionService.update_holdings_for_security_after_corporate_action(security_id)
                logger.info(f"Holdings update result: {update_result}")
            except Exception as e:
                # Log error but don't fail the corporate action creation
                logger.error(f"Failed to update holdings after corporate action creation: {str(e)}")
            
            return corporate_action
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating corporate action: {str(e)}")
            raise
    
    @staticmethod
    def calculate_adjustment_factor(
        security_id: int,
        start_date: date,
        end_date: date
    ) -> float:
        """
        Calculate cumulative adjustment factor for a security between dates
        
        Args:
            security_id: Security ID
            start_date: Start date (exclusive)
            end_date: End date (inclusive)
        
        Returns:
            Cumulative adjustment factor (float)
        """
        try:
            actions = CorporateActionService.get_corporate_actions(
                security_id=security_id,
                start_date=start_date,
                end_date=end_date,
                is_active=True
            )
            
            factor = 1.0
            for action in actions:
                if action.action_type == 'SPLIT':
                    # Split factor: e.g., 2.0 for 1:2 split
                    factor *= float(action.ratio)
                elif action.action_type == 'BONUS':
                    # Bonus factor: e.g., 1.5 for 1:2 bonus (1 + 0.5)
                    factor *= (1.0 + float(action.ratio))
            
            logger.debug(f"Adjustment factor for security {security_id}: {factor}")
            return factor
            
        except Exception as e:
            logger.error(f"Error calculating adjustment factor: {str(e)}")
            raise
    
    @staticmethod
    def calculate_split_adjustment(
        old_quantity: float,
        split_ratio: float
    ) -> Tuple[float, float]:
        """
        Calculate quantity and price adjustments for stock splits
        
        Args:
            old_quantity: Original quantity
            split_ratio: Split ratio (e.g., 2.0 for 1:2 split)
        
        Returns:
            Tuple of (new_quantity, price_adjustment_factor)
        """
        try:
            # Calculate new quantity
            new_quantity = old_quantity * split_ratio
            
            # Price adjustment factor (price should be divided by this)
            price_adjustment_factor = split_ratio
            
            logger.info(f"Split adjustment: {old_quantity} -> {new_quantity}, factor: {price_adjustment_factor}")
            
            return new_quantity, price_adjustment_factor
            
        except Exception as e:
            logger.error(f"Error calculating split adjustment: {str(e)}")
            raise
    
    @staticmethod
    def calculate_bonus_adjustment(
        old_quantity: float,
        bonus_ratio: float
    ) -> Tuple[float, float]:
        """
        Calculate quantity and price adjustments for bonus issues
        
        Args:
            old_quantity: Original quantity
            bonus_ratio: Bonus ratio (e.g., 0.5 for 1:2 bonus)
        
        Returns:
            Tuple of (new_quantity, price_adjustment_factor)
        """
        try:
            # Calculate new quantity (existing + bonus)
            bonus_quantity = old_quantity * bonus_ratio
            new_quantity = old_quantity + bonus_quantity
            
            # Price adjustment factor (price should be divided by this)
            price_adjustment_factor = new_quantity / old_quantity
            
            logger.info(f"Bonus adjustment: {old_quantity} -> {new_quantity}, factor: {price_adjustment_factor}")
            
            return new_quantity, price_adjustment_factor
            
        except Exception as e:
            logger.error(f"Error calculating bonus adjustment: {str(e)}")
            raise
    
    @staticmethod
    def apply_corporate_action_to_holdings(
        security_id: int,
        action_date: date,
        action_type: str,
        ratio: float
    ) -> Dict:
        """
        Apply corporate action to all holdings of a security
        
        Args:
            security_id: ID of the security
            action_date: Date of the corporate action
            action_type: Type of action (SPLIT, BONUS)
            ratio: Action ratio
        
        Returns:
            Dict with adjustment details
        """
        try:
            # Get all holdings for this security
            holdings = Holding.query.filter_by(security_id=security_id).all()
            
            if not holdings:
                logger.warning(f"No holdings found for security {security_id}")
                return {"adjusted_holdings": 0, "total_adjustment": 0}
            
            total_adjustment = 0
            adjusted_holdings = 0
            
            for holding in holdings:
                old_quantity = float(holding.quantity) if holding.quantity else 0
                
                if old_quantity <= 0:
                    continue
                
                if action_type == "SPLIT":
                    new_quantity, price_factor = CorporateActionService.calculate_split_adjustment(
                        old_quantity, ratio
                    )
                elif action_type == "BONUS":
                    new_quantity, price_factor = CorporateActionService.calculate_bonus_adjustment(
                        old_quantity, ratio
                    )
                else:
                    logger.warning(f"Unsupported action type: {action_type}")
                    continue
                
                # Update holding
                holding.quantity = new_quantity
                holding.average_price = float(holding.average_price) / price_factor if holding.average_price else 0
                
                total_adjustment += (new_quantity - old_quantity)
                adjusted_holdings += 1
            
            # Commit changes
            db.session.commit()
            
            logger.info(f"Applied {action_type} to {adjusted_holdings} holdings, total adjustment: {total_adjustment}")
            
            return {
                "adjusted_holdings": adjusted_holdings,
                "total_adjustment": total_adjustment,
                "action_type": action_type,
                "ratio": ratio
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error applying corporate action: {str(e)}")
            raise
    
    @staticmethod
    def check_security_has_corporate_actions(
        security_id: int,
        cutoff_date: Optional[date] = None
    ) -> Tuple[bool, float]:
        """
        Check if a security has corporate actions and get total factor
        
        Args:
            security_id: Security ID
            cutoff_date: Check actions up to this date (default: today)
        
        Returns:
            Tuple of (has_actions, total_factor)
        """
        try:
            if cutoff_date is None:
                cutoff_date = date.today()
            
            # Check for corporate actions
            actions = CorporateActionService.get_corporate_actions(
                security_id=security_id,
                end_date=cutoff_date,
                is_active=True
            )
            
            has_actions = len(actions) > 0
            
            # Calculate total factor
            total_factor = 1.0
            if has_actions:
                for action in actions:
                    if action.action_type == 'SPLIT':
                        total_factor *= float(action.ratio)
                    elif action.action_type == 'BONUS':
                        total_factor *= (1.0 + float(action.ratio))
            
            return has_actions, total_factor
            
        except Exception as e:
            logger.error(f"Error checking corporate actions: {str(e)}")
            raise
    
    @staticmethod
    def get_factors_for_security(
        security_id: int,
        cutoff_date: date
    ) -> List[Dict]:
        """
        Get corporate action factors used for a security
        
        Args:
            security_id: Security ID
            cutoff_date: Cutoff date
        
        Returns:
            List of factor details
        """
        try:
            actions = CorporateActionService.get_corporate_actions(
                security_id=security_id,
                end_date=cutoff_date,
                is_active=True
            )
            
            factors = []
            cumulative_factor = 1.0
            
            for action in actions:
                if action.action_type == 'SPLIT':
                    factor = float(action.ratio)
                elif action.action_type == 'BONUS':
                    factor = 1.0 + float(action.ratio)
                else:
                    continue  # Skip other action types for factor calculation
                
                cumulative_factor *= factor
                
                factors.append({
                    'date': action.action_date.isoformat(),
                    'type': action.action_type,
                    'ratio': float(action.ratio),
                    'factor': factor,
                    'cumulative_factor': round(cumulative_factor, 6)
                })
            
            return factors
            
        except Exception as e:
            logger.error(f"Error getting factors for security: {str(e)}")
            raise
    
    @staticmethod
    def update_holdings_for_security_after_corporate_action(security_id: int) -> Dict:
        """
        Update holdings for all clients holding a security after a corporate action is added.
        Uses forward calculation to ensure accuracy for all corporate action types.
        
        Args:
            security_id: ID of the security that had a corporate action added
        
        Returns:
            Dict with update statistics
        """
        try:
            from services.forward_holding_calculation_service import get_client_portfolio_by_date
            from datetime import date as date_type
            
            # Get all clients who have holdings in this security
            clients_with_holdings = db.session.query(Client.id).join(
                Holding, Client.id == Holding.client_id
            ).filter(
                Holding.security_id == security_id,
                Holding.quantity > 0
            ).distinct().all()
            
            client_ids = [c[0] for c in clients_with_holdings]
            
            if not client_ids:
                logger.info(f"No clients found with holdings in security {security_id}")
                return {
                    "clients_updated": 0,
                    "holdings_updated": 0,
                    "message": "No clients with holdings found"
                }
            
            logger.info(f"Updating holdings for {len(client_ids)} clients after corporate action on security {security_id}")
            
            clients_updated = 0
            holdings_updated = 0
            clients_failed = 0
            
            # Update holdings for each client using forward calculation
            for client_id in client_ids:
                try:
                    # Get forward calculation results
                    portfolio = get_client_portfolio_by_date(client_id, date_type.today())
                    
                    if 'error' in portfolio:
                        logger.warning(f"Error calculating portfolio for client {client_id}: {portfolio['error']}")
                        clients_failed += 1
                        continue
                    
                    # Get current holdings for this client
                    current_holdings = Holding.query.filter_by(client_id=client_id).all()
                    current_holdings_dict = {h.security_id: h for h in current_holdings}
                    
                    # Update or create holdings from forward calculation
                    for holding_data in portfolio.get('holdings', []):
                        security_id_from_portfolio = holding_data['security_id']
                        new_quantity = holding_data.get('quantity', 0)
                        new_avg_price = holding_data.get('average_price', 0)
                        
                        # Only update if quantity > 0 or if holding exists
                        if new_quantity > 0 or security_id_from_portfolio in current_holdings_dict:
                            if security_id_from_portfolio in current_holdings_dict:
                                # Update existing holding only if values have changed
                                holding = current_holdings_dict[security_id_from_portfolio]
                                old_quantity = float(holding.quantity) if holding.quantity else 0
                                old_avg_price = float(holding.average_price) if holding.average_price else 0
                                
                                # Check if values are already correct (within small tolerance for floating point)
                                quantity_diff = abs(old_quantity - new_quantity)
                                price_diff = abs(old_avg_price - new_avg_price) if new_avg_price > 0 else 0
                                
                                # Only update if there's a meaningful difference
                                if quantity_diff > 0.01 or (new_avg_price > 0 and price_diff > 0.01):
                                    holding.quantity = new_quantity
                                    holding.average_price = new_avg_price
                                    holdings_updated += 1
                                    logger.debug(f"Updated holding for client {client_id}, security {security_id_from_portfolio}: "
                                               f"qty {old_quantity:.2f}→{new_quantity:.2f}, price ₹{old_avg_price:.2f}→₹{new_avg_price:.2f}")
                                else:
                                    logger.debug(f"Holding already correct for client {client_id}, security {security_id_from_portfolio}: "
                                               f"qty {old_quantity:.2f}, price ₹{old_avg_price:.2f}")
                            elif new_quantity > 0:
                                # Create new holding
                                holding = Holding(
                                    client_id=client_id,
                                    security_id=security_id_from_portfolio,
                                    quantity=new_quantity,
                                    average_price=new_avg_price
                                )
                                db.session.add(holding)
                                holdings_updated += 1
                    
                    # Remove holdings with zero quantity
                    for holding in current_holdings:
                        if holding.quantity <= 0:
                            db.session.delete(holding)
                            holdings_updated += 1
                    
                    clients_updated += 1
                    
                except Exception as e:
                    logger.error(f"Error updating holdings for client {client_id}: {str(e)}")
                    clients_failed += 1
                    continue
            
            # Commit all changes
            db.session.commit()
            
            logger.info(f"Updated holdings for {clients_updated} clients, {holdings_updated} holdings updated, {clients_failed} failed")
            
            return {
                "clients_updated": clients_updated,
                "holdings_updated": holdings_updated,
                "clients_failed": clients_failed,
                "message": f"Updated holdings for {clients_updated} clients"
            }
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating holdings after corporate action: {str(e)}")
            raise





