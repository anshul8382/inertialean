"""
Centralized Price Service
Provides single source of truth for price lookups with validation and corporate action adjustments

Key Features:
- Strict 4-day window for historical prices (3 days back + 1 day forward)
- No stale fallback mechanisms
- Integration with corporate action adjustments
- Centralized validation and logging
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any
from sqlalchemy import desc
from models import Security, HistoricalPrice
import logging

logger = logging.getLogger(__name__)


class PriceData:
    """Price data with validation and source tracking"""
    
    def __init__(self, price: Optional[float], source: str, is_valid: bool, 
                 last_updated: Optional[date] = None, adjustment_applied: bool = False):
        self.price = price
        self.source = source  # "historical", "current", "adjusted", "stale", "none"
        self.is_valid = is_valid
        self.last_updated = last_updated
        self.adjustment_applied = adjustment_applied
    
    def __repr__(self):
        return f"PriceData(price={self.price}, source={self.source}, valid={self.is_valid})"


class PriceService:
    """Centralized price management service"""
    
    @staticmethod
    def get_price(security_id: int, target_date: Optional[date] = None, 
                  use_adjusted: bool = False, allow_fallback: bool = True) -> PriceData:
        """
        Centralized price lookup with consistent validation
        
        Args:
            security_id: Security ID
            target_date: Date for historical price (None for current)
            use_adjusted: Whether to apply corporate action adjustments (for historical dates)
            allow_fallback: Whether to allow stale fallbacks (default: False)
        
        Returns:
            PriceData object with price, source, and validation info
        """
        if target_date:
            today = datetime.now().date()
            if target_date > today:
                # Future-dated request: only live quote available
                result = PriceService._get_current_price(security_id, allow_fallback)
                if result and result.price is not None:
                    return PriceData(
                        price=result.price,
                        source="current",
                        is_valid=result.is_valid,
                        last_updated=result.last_updated
                    )
                return result
            if target_date == today:
                # Same calendar day: if EOD is already in historical_price (e.g. after market close),
                # use that so period analysis / portfolio review matches next-day audit & official close.
                # If not ingested yet, fall back to live Security.current_price (intraday / pre-close).
                hist = PriceService._get_historical_price(
                    security_id, target_date, use_adjusted, allow_fallback=False
                )
                if hist and hist.price is not None and hist.is_valid:
                    return hist
                result = PriceService._get_current_price(security_id, allow_fallback)
                if result and result.price is not None:
                    return PriceData(
                        price=result.price,
                        source="current",
                        is_valid=result.is_valid,
                        last_updated=result.last_updated
                    )
                return result
            return PriceService._get_historical_price(security_id, target_date, use_adjusted, allow_fallback)
        else:
            # For current prices, the price is already post-split (current market price)
            # No adjustment needed - return current price as-is
            return PriceService._get_current_price(security_id, allow_fallback)
    
    @staticmethod
    def _get_historical_price(security_id: int, target_date: date, 
                               use_adjusted: bool = False, allow_fallback: bool = True) -> PriceData:
        """
        Get historical price with strict validation
        
        If allow_fallback=False:
        - Only check exact target_date
        - Return no data if exact date not found
        
        If allow_fallback=True:
        - Check exact target_date first
        - If not found, check 4-day window (3 days back + 1 day forward)
        - Return no data if nothing found in 4-day window
        
        For target_date = 24 Aug 2024 with fallback=True, checks:
        - 24 Aug (exact)
        - 23 Aug (1 day back)
        - 22 Aug (2 days back) 
        - 21 Aug (3 days back)
        - 25 Aug (1 day forward)
        
        Returns PriceData with None if no valid price found
        """
        # First try exact date
        price_data = HistoricalPrice.query.filter_by(
            security_id=security_id,
            date=target_date
        ).first()
        
        if price_data:
            price = float(price_data.close_price)
            
            # Apply corporate action adjustments if requested
            if use_adjusted:
                adjusted_price = PriceService._apply_adjustments(security_id, price, target_date)
                logger.info(f"Price for security {security_id} on {target_date}: {price} -> adjusted: {adjusted_price}")
                return PriceData(
                    price=adjusted_price,
                    source="adjusted",
                    is_valid=True,
                    adjustment_applied=True
                )
            
            logger.info(f"Using exact historical price for security {security_id} on {target_date}")
            return PriceData(
                price=price,
                source="historical",
                is_valid=True,
                last_updated=target_date
            )
        
        # If exact date not found and fallback is disabled, return no data
        if not allow_fallback:
            logger.error(f"No exact price data available for security {security_id} on {target_date} (fallback disabled)")
            return PriceData(
                price=None,
                source="none",
                is_valid=False
            )
        
        # If fallback is enabled, check 4-day window
        search_dates = []
        
        # Check 3 days back (in order of preference)
        for days_back in range(1, 4):
            check_date = target_date - timedelta(days=days_back)
            search_dates.append(check_date)
        
        # Check 1 day forward
        check_date = target_date + timedelta(days=1)
        search_dates.append(check_date)
        
        # Try each date in the 4-day window
        for check_date in search_dates:
            price_data = HistoricalPrice.query.filter_by(
                security_id=security_id,
                date=check_date
            ).first()
            
            if price_data:
                price = float(price_data.close_price)
                
                # Apply corporate action adjustments if requested
                if use_adjusted:
                    adjusted_price = PriceService._apply_adjustments(security_id, price, target_date)
                    logger.info(f"Price for security {security_id} on {target_date}: {price} -> adjusted: {adjusted_price}")
                    return PriceData(
                        price=adjusted_price,
                        source="adjusted",
                        is_valid=True,
                        adjustment_applied=True
                    )
                
                logger.info(f"Using historical price for security {security_id} from {check_date} for target date {target_date}")
                return PriceData(
                    price=price,
                    source="historical",
                    is_valid=True,
                    last_updated=check_date
                )
        
        # No valid price found in 4-day window
        # Fallback: when target_date is today or recent (within 7 days), use Security.current_price
        # This fixes incorrect -77%/-100% returns when report end_date has no historical_price yet
        today = datetime.now().date()
        if allow_fallback and target_date >= today - timedelta(days=7):
            security = Security.query.get(security_id)
            if security and security.current_price and float(security.current_price) > 0:
                current_p = float(security.current_price)
                logger.info(
                    f"No historical price for security {security_id} on {target_date}; "
                    f"using current_price {current_p} as fallback (target_date is recent)"
                )
                return PriceData(
                    price=current_p,
                    source="current_fallback",
                    is_valid=True,
                    last_updated=security.last_updated.date() if security.last_updated else today
                )

        logger.error(f"No historical price data available for security {security_id} on {target_date} or within 4-day window")
        return PriceData(
            price=None,
            source="none",
            is_valid=False
        )
    
    @staticmethod
    def _get_current_price(security_id: int, allow_fallback: bool = True) -> PriceData:
        """
        Get current price with staleness validation
        
        Only returns valid price if updated within 7 days
        Never uses stale current prices as fallback for historical lookups
        """
        security = Security.query.get(security_id)
        
        if not security or not security.current_price:
            return PriceData(
                price=None,
                source="none",
                is_valid=False
            )
        
        # Check if price is recent enough
        if security.last_updated:
            days_old = (datetime.now().date() - security.last_updated.date()).days
            
            if days_old <= 7:
                return PriceData(
                    price=float(security.current_price),
                    source="current",
                    is_valid=True,
                    last_updated=security.last_updated.date()
                )
            else:
                logger.warning(f"Current price for security {security_id} is too stale ({days_old} days old)")
                if allow_fallback:
                    return PriceData(
                        price=float(security.current_price),
                        source="stale",
                        is_valid=False,
                        last_updated=security.last_updated.date()
                    )
        
        return PriceData(
            price=None,
            source="stale",
            is_valid=False
        )
    
    @staticmethod
    def restate_historical_price_to_current_basis(
        security_id: int, price: float, as_of_date: date
    ) -> float:
        """
        Restate a price observed on ``as_of_date`` to current share basis using recorded
        splits/bonuses after that date (same logic as ``use_adjusted`` historical lookups).
        """
        return PriceService._apply_adjustments(security_id, price, as_of_date)

    @staticmethod
    def _apply_adjustments(security_id: int, price: float, target_date: date) -> float:
        """
        Apply corporate action adjustments to historical price
        
        This integrates with the adjustments service to reverse corporate actions
        that occurred after the target date
        
        Args:
            security_id: Security ID
            price: Unadjusted historical price
            target_date: Target date for reconstruction
        
        Returns:
            Adjusted price
        """
        try:
            from services.adjustments_service import get_corporate_actions
            from models import CorporateAction
            
            # Get corporate actions that occurred after target_date
            current_date = datetime.now().date()
            corporate_actions = CorporateAction.query.filter(
                CorporateAction.security_id == security_id,
                CorporateAction.action_date > target_date,
                CorporateAction.action_date <= current_date,
                CorporateAction.is_active == True
            ).order_by(CorporateAction.action_date).all()
            
            if not corporate_actions:
                return price
            
            # Calculate cumulative adjustment factor
            adjustment_factor = 1.0
            for action in corporate_actions:
                if action.action_type == 'SPLIT':
                    # Split increases shares, so price needs to be divided
                    adjustment_factor /= float(action.ratio)
                elif action.action_type == 'BONUS':
                    # Bonus increases shares, so price needs to be divided
                    adjustment_factor /= (1.0 + float(action.ratio))
            
            adjusted_price = price * adjustment_factor
            logger.info(f"Applied adjustments for security {security_id}: {price} -> {adjusted_price} (factor: {adjustment_factor})")
            
            return adjusted_price
            
        except Exception as e:
            logger.error(f"Error applying adjustments for security {security_id}: {str(e)}")
            return price  # Return unadjusted price on error
    
    @staticmethod
    def validate_price(price_data: PriceData, security_id: int, target_date: Optional[date] = None) -> bool:
        """
        Validate price data for a specific security and date
        
        Args:
            price_data: PriceData object to validate
            security_id: Security ID
            target_date: Target date (optional)
        
        Returns:
            True if price is valid, False otherwise
        """
        if not price_data.is_valid:
            return False
        
        if price_data.price is None or price_data.price <= 0:
            return False
        
        # Additional validation can be added here
        return True
    
    @staticmethod
    def get_missing_prices_summary(missing_prices: list) -> Dict[str, Any]:
        """
        Generate summary of missing prices for user reporting
        
        Args:
            missing_prices: List of PriceData objects with invalid prices
        
        Returns:
            Summary dictionary
        """
        if not missing_prices:
            return {
                'total_missing': 0,
                'securities': []
            }
        
        securities_info = []
        for price_data in missing_prices:
            if not price_data.is_valid:
                securities_info.append({
                    'security_id': getattr(price_data, 'security_id', None),
                    'symbol': getattr(price_data, 'symbol', 'Unknown'),
                    'source': price_data.source,
                    'reason': f"Price {price_data.source} - no valid data"
                })
        
        return {
            'total_missing': len(securities_info),
            'securities': securities_info
        }

