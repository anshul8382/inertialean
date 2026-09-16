"""
Equity Model vs Nifty Performance Tracking Service

Tracks and compares equity model portfolio performance against Nifty 50 benchmark.
Simulates model portfolio with proper corporate action handling and tracks changes over time.
Reuses existing client performance tracking methods.
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import logging
import math

logger = logging.getLogger(__name__)


class EquityModelVsNiftyService:
    """
    Service to track and compare equity model performance vs Nifty 50
    Uses forward calculation approach similar to client portfolios
    """
    
    @staticmethod
    def _simulate_model_portfolio_with_corporate_actions(
        model_id: int,
        investment_date: date,
        investment_amount: float,
        as_of_date: date,
        db_session=None
    ) -> Dict[str, Any]:
        """
        Simulate model portfolio investment and track it forward with corporate actions
        
        This simulates investing according to model allocations and applies corporate
        actions chronologically, similar to how client portfolios are tracked.
        
        Args:
            model_id: SecurityAllocationModel ID
            investment_date: Date when investment was made
            investment_amount: Total investment amount
            as_of_date: Date to calculate portfolio value for
            db_session: Optional database session
            
        Returns:
            Dictionary with portfolio state at as_of_date
        """
        try:
            from models import SecurityAllocationModel, Security, HistoricalPrice, CorporateAction
            
            # Get the model
            if db_session is not None:
                model = db_session.query(SecurityAllocationModel).get(model_id)
            else:
                model = SecurityAllocationModel.query.get(model_id)
            
            if not model:
                return {
                    'success': False,
                    'error': f'SecurityAllocationModel {model_id} not found'
                }
            
            # Get all security allocations from the model
            allocations = model.security_allocations if hasattr(model, 'security_allocations') else []
            
            if not allocations:
                return {
                    'success': False,
                    'error': f'Model {model_id} has no security allocations'
                }
            
            # Step 1: Calculate initial investment distribution
            initial_holdings = {}
            for allocation in allocations:
                security_id = allocation.security_id
                allocation_percent = float(allocation.allocation_percentage)
                target_value = investment_amount * (allocation_percent / 100.0)
                
                # Get price on investment date
                investment_price = EquityModelVsNiftyService._get_historical_price(
                    security_id, investment_date, db_session
                )
                
                if investment_price and investment_price > 0:
                    initial_quantity = target_value / investment_price
                    initial_holdings[security_id] = {
                        'security_id': security_id,
                        'allocation_percent': allocation_percent,
                        'investment_value': target_value,
                        'investment_price': investment_price,
                        'quantity': initial_quantity,
                        'cost_basis': target_value
                    }
            
            # Step 2: Apply corporate actions between investment_date and as_of_date
            # Get all corporate actions for securities in the model
            security_ids = list(initial_holdings.keys())
            
            if db_session is not None:
                corporate_actions = db_session.query(CorporateAction).filter(
                    CorporateAction.security_id.in_(security_ids),
                    CorporateAction.action_date > investment_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
                
                # Also get MERGER actions where model securities are sources
                source_merger_actions = db_session.query(CorporateAction).filter(
                    CorporateAction.source_security_id.in_(security_ids),
                    CorporateAction.action_type == 'MERGER',
                    CorporateAction.action_date > investment_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
            else:
                corporate_actions = CorporateAction.query.filter(
                    CorporateAction.security_id.in_(security_ids),
                    CorporateAction.action_date > investment_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
                
                source_merger_actions = CorporateAction.query.filter(
                    CorporateAction.source_security_id.in_(security_ids),
                    CorporateAction.action_type == 'MERGER',
                    CorporateAction.action_date > investment_date,
                    CorporateAction.action_date <= as_of_date,
                    CorporateAction.is_active == True
                ).order_by(CorporateAction.action_date).all()
            
            all_corporate_actions = list(corporate_actions) + list(source_merger_actions)
            
            # Step 3: Apply corporate actions chronologically
            current_holdings = initial_holdings.copy()
            
            for action in all_corporate_actions:
                action_date = action.action_date
                if isinstance(action_date, datetime):
                    action_date = action_date.date()
                
                security_id = action.security_id
                action_type = action.action_type
                ratio = float(action.ratio)
                
                if security_id in current_holdings:
                    # Apply corporate action to this security
                    if action_type == 'SPLIT':
                        old_qty = current_holdings[security_id]['quantity']
                        current_holdings[security_id]['quantity'] *= ratio
                        logger.debug(f"  {action_date}: SPLIT {ratio}:1 for security {security_id}: {old_qty:.2f} → {current_holdings[security_id]['quantity']:.2f}")
                    
                    elif action_type == 'BONUS':
                        old_qty = current_holdings[security_id]['quantity']
                        bonus_shares = math.floor(old_qty * ratio)
                        current_holdings[security_id]['quantity'] += bonus_shares
                        logger.debug(f"  {action_date}: BONUS {ratio}:1 for security {security_id}: {old_qty:.2f} → {current_holdings[security_id]['quantity']:.2f} (+{bonus_shares:.2f})")
                    
                    elif action_type == 'MERGER':
                        source_security_id = getattr(action, 'source_security_id', None)
                        if source_security_id and source_security_id == security_id:
                            # This security is being merged away
                            old_qty = current_holdings[security_id]['quantity']
                            # Cost basis transfers to target
                            cost_basis = current_holdings[security_id]['cost_basis']
                            del current_holdings[security_id]
                            logger.debug(f"  {action_date}: MERGER - security {security_id} merged away ({old_qty:.2f} shares, ₹{cost_basis:.2f} cost)")
                
                # Check if this security is the TARGET of a merger
                if action_type == 'MERGER' and security_id in current_holdings:
                    source_security_id = getattr(action, 'source_security_id', None)
                    if source_security_id and source_security_id in initial_holdings:
                        # Get source quantity before merger (would need to track this)
                        # For now, calculate based on ratio
                        # In full implementation, would track source holdings
                        pass
            
            # Step 4: Calculate current values at as_of_date
            holdings_list = []
            total_value = 0.0
            total_cost = 0.0
            
            for security_id, holding_data in current_holdings.items():
                # Get current price
                current_price = EquityModelVsNiftyService._get_historical_price(
                    security_id, as_of_date, db_session
                )
                
                if not current_price or current_price <= 0:
                    continue
                
                quantity = holding_data['quantity']
                current_value = quantity * current_price
                cost_basis = holding_data['cost_basis']
                
                # Get security details
                if db_session is not None:
                    security = db_session.query(Security).get(security_id)
                else:
                    security = Security.query.get(security_id)
                
                holdings_list.append({
                    'security_id': security_id,
                    'symbol': security.symbol if security else 'Unknown',
                    'name': security.name if security else 'Unknown',
                    'allocation_percent': holding_data['allocation_percent'],
                    'quantity': quantity,
                    'investment_price': holding_data['investment_price'],
                    'current_price': current_price,
                    'current_value': current_value,
                    'cost_basis': cost_basis,
                    'unrealized_pnl': current_value - cost_basis,
                    'unrealized_pnl_percent': ((current_value - cost_basis) / cost_basis * 100) if cost_basis > 0 else 0.0
                })
                
                total_value += current_value
                total_cost += cost_basis
            
            return {
                'success': True,
                'model_id': model_id,
                'model_name': model.name,
                'investment_date': investment_date.isoformat(),
                'as_of_date': as_of_date.isoformat(),
                'investment_amount': investment_amount,
                'total_value': total_value,
                'total_cost': total_cost,
                'unrealized_pnl': total_value - total_cost,
                'unrealized_pnl_percent': ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0.0,
                'holdings_count': len(holdings_list),
                'holdings': holdings_list,
                'corporate_actions_applied': len(all_corporate_actions)
            }
            
        except Exception as e:
            logger.error(f"Error simulating model portfolio: {str(e)}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
    
    @staticmethod
    def _get_historical_price(security_id: int, target_date: date, db_session=None) -> Optional[float]:
        """
        Get historical price for a security on a specific date.
        Uses: exact date, 3-day lookback, then nearest prior date.
        Only falls back to current_price when target_date is within 7 days of today
        (for end_date when today's data may not be in yet).
        """
        try:
            from models import HistoricalPrice, Security
            from sqlalchemy import desc
            
            def get_by_date(check_date):
                if db_session is not None:
                    return db_session.query(HistoricalPrice).filter_by(
                        security_id=security_id,
                        date=check_date
                    ).first()
                return HistoricalPrice.query.filter_by(
                    security_id=security_id,
                    date=check_date
                ).first()
            
            # 1. Try exact date
            price_record = get_by_date(target_date)
            if price_record and price_record.close_price:
                return float(price_record.close_price)
            
            # 2. Try 1-3 days back (for weekends/holidays)
            for days_back in range(1, 4):
                lookback_date = target_date - timedelta(days=days_back)
                price_record = get_by_date(lookback_date)
                if price_record and price_record.close_price:
                    return float(price_record.close_price)
            
            # 3. Get closest previous trading date
            if db_session is not None:
                price_record = db_session.query(HistoricalPrice).filter_by(
                    security_id=security_id
                ).filter(HistoricalPrice.date <= target_date).order_by(
                    desc(HistoricalPrice.date)
                ).first()
            else:
                price_record = HistoricalPrice.query.filter_by(
                    security_id=security_id
                ).filter(HistoricalPrice.date <= target_date).order_by(
                    desc(HistoricalPrice.date)
                ).first()
            
            if price_record and price_record.close_price:
                return float(price_record.close_price)
            
            # 4. Fallback to current price ONLY for recent dates (within 7 days)
            # Using current_price for old dates would give wrong historical values
            days_from_today = (date.today() - target_date).days
            if days_from_today <= 7 and days_from_today >= 0:
                if db_session is not None:
                    security = db_session.query(Security).get(security_id)
                else:
                    security = Security.query.get(security_id)
                if security and security.current_price:
                    return float(security.current_price)
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting historical price for security {security_id} on {target_date}: {str(e)}")
            return None
    
    @staticmethod
    def get_model_portfolio_value(
        model_id: int,
        as_of_date: date,
        base_value: float = 1000000.0,
        investment_date: Optional[date] = None,
        db_session=None
    ) -> Dict[str, Any]:
        """
        Calculate equity model portfolio value for a specific date
        Now with proper corporate action handling
        
        Args:
            model_id: SecurityAllocationModel ID
            as_of_date: Date to calculate portfolio for
            base_value: Base portfolio value to use for calculations (default: 10,00,000)
            investment_date: Date when investment was made (default: as_of_date)
            db_session: Optional database session
            
        Returns:
            Dictionary with model portfolio metrics
        """
        try:
            if investment_date is None:
                investment_date = as_of_date
            
            # Use the enhanced simulation method
            return EquityModelVsNiftyService._simulate_model_portfolio_with_corporate_actions(
                model_id, investment_date, base_value, as_of_date, db_session
            )
            
        except Exception as e:
            logger.error(f"Error calculating model portfolio value for model {model_id}: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'model_id': model_id,
                'as_of_date': as_of_date.isoformat()
            }
    
    @staticmethod
    def get_nifty_value_on_date(target_date: date, benchmark_id: int = 1) -> Optional[float]:
        """
        Get Nifty value on a specific date
        
        Args:
            target_date: Target date
            benchmark_id: Benchmark ID (default: 1 for Nifty 50)
            
        Returns:
            Nifty price or None if not found
        """
        try:
            from services.benchmark_service import BenchmarkService
            return BenchmarkService.get_price_on_date(benchmark_id, target_date)
        except Exception as e:
            logger.error(f"Error getting Nifty value for date {target_date}: {str(e)}")
            return None
    
    @staticmethod
    def calculate_model_vs_nifty_performance(
        model_id: int,
        start_date: date,
        end_date: date,
        base_value: float = 1000000.0,
        investment_date: Optional[date] = None,
        db_session=None
    ) -> Dict[str, Any]:
        """
        Calculate model vs Nifty performance over a date range
        Uses proper portfolio simulation with corporate actions
        
        Args:
            model_id: SecurityAllocationModel ID
            start_date: Start date for comparison
            end_date: End date for comparison
            base_value: Base portfolio value (default: 10,00,000)
            investment_date: Date when investment was made (default: start_date)
            db_session: Optional database session
            
        Returns:
            Dictionary with performance comparison metrics
        """
        try:
            if investment_date is None:
                investment_date = start_date
            
            # Get model portfolio values at start and end dates
            # Both use the same investment_date but different as_of_date
            start_portfolio = EquityModelVsNiftyService._simulate_model_portfolio_with_corporate_actions(
                model_id, investment_date, base_value, start_date, db_session
            )
            end_portfolio = EquityModelVsNiftyService._simulate_model_portfolio_with_corporate_actions(
                model_id, investment_date, base_value, end_date, db_session
            )
            
            if not start_portfolio.get('success') or not end_portfolio.get('success'):
                return {
                    'success': False,
                    'error': start_portfolio.get('error') or end_portfolio.get('error'),
                    'model_id': model_id,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                }
            
            # Get Nifty values at start and end dates
            start_nifty = EquityModelVsNiftyService.get_nifty_value_on_date(start_date)
            end_nifty = EquityModelVsNiftyService.get_nifty_value_on_date(end_date)
            
            if not start_nifty or not end_nifty:
                return {
                    'success': False,
                    'error': 'Nifty data not available for the date range',
                    'model_id': model_id,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                }
            
            # Calculate portfolio returns
            start_portfolio_value = start_portfolio.get('total_value', 0.0)
            end_portfolio_value = end_portfolio.get('total_value', 0.0)
            
            model_return = 0.0
            if start_portfolio_value > 0:
                model_return = ((end_portfolio_value - start_portfolio_value) / start_portfolio_value) * 100
            
            # Calculate Nifty returns
            nifty_return = ((end_nifty - start_nifty) / start_nifty) * 100
            
            # Calculate outperformance
            outperformance = model_return - nifty_return
            
            # Calculate tracking metrics
            tracking_error = abs(model_return - nifty_return)
            
            # Scaled Nifty display values for apple-to-apple comparison with model portfolio
            # Model shows "₹10L → ₹X" (portfolio values), Nifty should show "₹10L → ₹Y" (scaled)
            nifty_start_display = base_value
            nifty_end_display = base_value * (end_nifty / start_nifty) if start_nifty else base_value
            
            return {
                'success': True,
                'model_id': model_id,
                'model_name': start_portfolio.get('model_name', 'Unknown'),
                'investment_date': investment_date.isoformat(),
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'base_value': base_value,
                'model_portfolio': {
                    'start_value': start_portfolio_value,
                    'end_value': end_portfolio_value,
                    'start_cost': start_portfolio.get('total_cost', 0.0),
                    'end_cost': end_portfolio.get('total_cost', 0.0),
                    'return_percent': model_return,
                    'corporate_actions_applied': end_portfolio.get('corporate_actions_applied', 0)
                },
                'nifty': {
                    'start_value': start_nifty,
                    'end_value': end_nifty,
                    'start_value_display': nifty_start_display,
                    'end_value_display': nifty_end_display,
                    'return_percent': nifty_return
                },
                'comparison': {
                    'outperformance': outperformance,
                    'tracking_error': tracking_error,
                    'model_beat_nifty': outperformance > 0
                }
            }
            
        except Exception as e:
            logger.error(f"Error calculating model vs Nifty performance: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'model_id': model_id,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat()
            }
    
    @staticmethod
    def track_model_portfolio_evolution(
        model_id: int,
        start_date: date,
        end_date: date,
        base_value: float = 1000000.0,
        investment_date: Optional[date] = None,
        frequency: str = 'daily',  # 'daily', 'weekly', 'monthly'
        db_session=None
    ) -> List[Dict[str, Any]]:
        """
        Track how model portfolio evolves over time with corporate actions
        
        Args:
            model_id: SecurityAllocationModel ID
            start_date: Start date for tracking
            end_date: End date for tracking
            base_value: Base portfolio value (default: 10,00,000)
            investment_date: Date when investment was made (default: start_date)
            frequency: Data frequency ('daily', 'weekly', 'monthly')
            db_session: Optional database session
            
        Returns:
            List of portfolio state snapshots over time
        """
        try:
            if investment_date is None:
                investment_date = start_date
            
            evolution_data = []
            current_date = start_date
            
            # Determine date increment based on frequency
            if frequency == 'daily':
                increment = timedelta(days=1)
            elif frequency == 'weekly':
                increment = timedelta(weeks=1)
            elif frequency == 'monthly':
                increment = timedelta(days=30)  # Approximate month
            else:
                increment = timedelta(days=1)
            
            # Get initial Nifty value
            initial_nifty = EquityModelVsNiftyService.get_nifty_value_on_date(start_date)
            
            while current_date <= end_date:
                # Get portfolio state at this date
                portfolio_state = EquityModelVsNiftyService._simulate_model_portfolio_with_corporate_actions(
                    model_id, investment_date, base_value, current_date, db_session
                )
                
                if portfolio_state.get('success'):
                    portfolio_value = portfolio_state.get('total_value', 0.0)
                    nifty_value = EquityModelVsNiftyService.get_nifty_value_on_date(current_date)
                    
                    # Calculate returns from start
                    portfolio_return = 0.0
                    nifty_return = 0.0
                    if initial_nifty and nifty_value:
                        # Get initial portfolio value
                        initial_portfolio = EquityModelVsNiftyService._simulate_model_portfolio_with_corporate_actions(
                            model_id, investment_date, base_value, start_date, db_session
                        )
                        initial_portfolio_value = initial_portfolio.get('total_value', 0.0)
                        
                        if initial_portfolio_value > 0:
                            portfolio_return = ((portfolio_value - initial_portfolio_value) / initial_portfolio_value) * 100
                        if initial_nifty > 0:
                            nifty_return = ((nifty_value - initial_nifty) / initial_nifty) * 100
                    
                    evolution_data.append({
                        'date': current_date.isoformat(),
                        'portfolio_value': portfolio_value,
                        'portfolio_return': portfolio_return,
                        'nifty_value': nifty_value,
                        'nifty_return': nifty_return,
                        'outperformance': portfolio_return - nifty_return,
                        'holdings_count': portfolio_state.get('holdings_count', 0),
                        'corporate_actions_applied': portfolio_state.get('corporate_actions_applied', 0)
                    })
                
                current_date += increment
            
            return evolution_data
            
        except Exception as e:
            logger.error(f"Error tracking model portfolio evolution: {str(e)}")
            return []
    
    @staticmethod
    def get_historical_performance_tracking(
        model_id: int,
        start_date: date,
        end_date: date,
        base_value: float = 1000000.0,
        investment_date: Optional[date] = None,
        frequency: str = 'daily',  # 'daily', 'weekly', 'monthly'
        db_session=None
    ) -> List[Dict[str, Any]]:
        """
        Get historical performance tracking data over a date range
        (Alias for track_model_portfolio_evolution for backward compatibility)
        """
        return EquityModelVsNiftyService.track_model_portfolio_evolution(
            model_id, start_date, end_date, base_value, investment_date, frequency, db_session
        )
    
    @staticmethod
    def get_performance_summary(
        model_id: int,
        base_value: float = 1000000.0,
        investment_date: Optional[date] = None,
        periods: List[str] = None,  # ['1M', '3M', '6M', '1Y', 'YTD', 'ALL']
        db_session=None
    ) -> Dict[str, Any]:
        """
        Get performance summary for multiple time periods
        
        Args:
            model_id: SecurityAllocationModel ID
            base_value: Base portfolio value (default: 10,00,000)
            investment_date: Date when investment was made (default: calculated from periods)
            periods: List of periods to calculate (default: ['1M', '3M', '6M', '1Y', 'YTD'])
            db_session: Optional database session
            
        Returns:
            Dictionary with performance summary for each period
        """
        try:
            if periods is None:
                periods = ['1M', '3M', '6M', '1Y', 'YTD']
            
            end_date = date.today()
            summary = {
                'model_id': model_id,
                'as_of_date': end_date.isoformat(),
                'base_value': base_value,
                'periods': {}
            }
            
            # Get model to find creation date for 'ALL' period
            from models import SecurityAllocationModel
            if db_session is not None:
                model = db_session.query(SecurityAllocationModel).get(model_id)
            else:
                model = SecurityAllocationModel.query.get(model_id)
            
            model_created_date = None
            if model and model.created_at:
                if isinstance(model.created_at, datetime):
                    model_created_date = model.created_at.date()
                else:
                    model_created_date = model.created_at
            
            # Use earliest date as investment_date if not provided
            if investment_date is None:
                if 'ALL' in periods and model_created_date:
                    investment_date = model_created_date
                else:
                    # Use earliest period start date
                    investment_date = end_date - timedelta(days=365)  # Default to 1 year ago
            
            for period in periods:
                # Calculate start date based on period
                if period == '1M':
                    start_date = end_date - timedelta(days=30)
                elif period == '3M':
                    start_date = end_date - timedelta(days=90)
                elif period == '6M':
                    start_date = end_date - timedelta(days=180)
                elif period == '1Y':
                    start_date = end_date - timedelta(days=365)
                elif period == 'YTD':
                    start_date = date(end_date.year, 1, 1)
                elif period == 'ALL':
                    if model_created_date:
                        start_date = model_created_date
                    else:
                        start_date = end_date - timedelta(days=365)
                else:
                    continue
                
                # Ensure investment_date is not after start_date
                period_investment_date = min(investment_date, start_date)
                
                # Calculate performance for this period
                performance = EquityModelVsNiftyService.calculate_model_vs_nifty_performance(
                    model_id, start_date, end_date, base_value, period_investment_date, db_session
                )
                
                if performance.get('success'):
                    summary['periods'][period] = {
                        'investment_date': period_investment_date.isoformat(),
                        'start_date': start_date.isoformat(),
                        'end_date': end_date.isoformat(),
                        'model_return': performance['model_portfolio']['return_percent'],
                        'nifty_return': performance['nifty']['return_percent'],
                        'outperformance': performance['comparison']['outperformance'],
                        'tracking_error': performance['comparison']['tracking_error'],
                        'model_beat_nifty': performance['comparison']['model_beat_nifty'],
                        'corporate_actions_applied': performance['model_portfolio'].get('corporate_actions_applied', 0)
                    }
                else:
                    summary['periods'][period] = {
                        'error': performance.get('error', 'Unknown error')
                    }
            
            return summary
            
        except Exception as e:
            logger.error(f"Error getting performance summary: {str(e)}")
            return {
                'model_id': model_id,
                'error': str(e)
            }
    
    @staticmethod
    def get_current_performance_snapshot(
        model_id: int,
        base_value: float = 1000000.0,
        investment_date: Optional[date] = None,
        db_session=None
    ) -> Dict[str, Any]:
        """
        Get current performance snapshot comparing model vs Nifty
        
        Args:
            model_id: SecurityAllocationModel ID
            base_value: Base portfolio value (default: 10,00,000)
            investment_date: Date when investment was made (default: 1 year ago)
            db_session: Optional database session
            
        Returns:
            Dictionary with current performance snapshot
        """
        try:
            today = date.today()
            
            if investment_date is None:
                investment_date = today - timedelta(days=365)
            
            # Get current portfolio value
            portfolio_data = EquityModelVsNiftyService._simulate_model_portfolio_with_corporate_actions(
                model_id, investment_date, base_value, today, db_session
            )
            
            if not portfolio_data.get('success'):
                return {
                    'model_id': model_id,
                    'error': portfolio_data.get('error', 'Unknown error')
                }
            
            # Get current Nifty value
            nifty_value = EquityModelVsNiftyService.get_nifty_value_on_date(today)
            
            # Get performance summary
            summary = EquityModelVsNiftyService.get_performance_summary(
                model_id, base_value, investment_date, ['1M', '3M', '6M', '1Y', 'YTD'], db_session
            )
            
            return {
                'model_id': model_id,
                'model_name': portfolio_data.get('model_name', 'Unknown'),
                'investment_date': investment_date.isoformat(),
                'as_of_date': today.isoformat(),
                'base_value': base_value,
                'current_portfolio_value': portfolio_data.get('total_value', 0.0),
                'current_portfolio_cost': portfolio_data.get('total_cost', 0.0),
                'current_unrealized_pnl': portfolio_data.get('unrealized_pnl', 0.0),
                'current_unrealized_pnl_percent': portfolio_data.get('unrealized_pnl_percent', 0.0),
                'current_nifty_value': nifty_value,
                'holdings_count': portfolio_data.get('holdings_count', 0),
                'corporate_actions_applied': portfolio_data.get('corporate_actions_applied', 0),
                'performance_summary': summary.get('periods', {})
            }
            
        except Exception as e:
            logger.error(f"Error getting current performance snapshot: {str(e)}")
            return {
                'model_id': model_id,
                'error': str(e)
            }
