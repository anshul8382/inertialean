"""
Period Performance Calculator Module

This module calculates core performance metrics using validated data.
"""
import logging
from datetime import datetime, date
from decimal import Decimal

from models import Cashflow

# Import calculation functions from v1
from api.v1.period_analysis import (
    calculate_mark_to_market_gains,
    calculate_trade_gains_in_period,
    calculate_investment_breakdown,
    calculate_gains_breakdown,
    calculate_twr
)
from api.v1.performance import calculate_xirr

logger = logging.getLogger(__name__)


class PeriodPerformanceCalculator:
    """
    Calculates core period performance metrics using validated data
    
    Core metrics:
    1. MTM (Mark-to-Market) analysis
    2. Trade gains analysis
    3. Investment breakdown
    4. Gains breakdown (additions vs unrealized)
    5. Period XIRR
    """
    
    def __init__(self, data_collector):
        """
        Initialize calculator with validated data collector
        
        Args:
            data_collector: PeriodAnalysisDataCollector instance with validated data
        """
        if not data_collector.is_valid:
            raise ValueError("Data collector must be valid before calculating performance")
        
        self.data = data_collector
        self.results = {}
    
    def calculate_all(self, basis='adjusted', matching='fifo'):
        """
        Calculate all period performance metrics
        
        Args:
            basis: 'adjusted' or 'unadjusted' (default: 'adjusted')
            matching: 'fifo' or 'lifo' (default: 'fifo')
        
        Returns:
            dict with all performance metrics
        """
        logger.info(f"Calculating period performance - Basis: {basis}, Matching: {matching}")
        
        # Core performance calculations (in order due to dependencies)
        self.results['mtm_analysis'] = self._calculate_mtm_analysis(basis, matching)
        self.results['trade_analysis'] = self._calculate_trade_analysis(basis, matching)
        self.results['investment_breakdown'] = self._calculate_investment_breakdown()
        self.results['gains_breakdown'] = self._calculate_gains_breakdown()
        self.results['period_xirr'] = self._calculate_period_xirr()
        try:
            twr_result = self._calculate_twr()
            self.results['twr'] = twr_result
            logger.info(f"✅ TWR calculation completed successfully: {twr_result}")
            if not twr_result or not twr_result.get('twr_percent'):
                logger.warning(f"⚠️ TWR result is empty or missing twr_percent: {twr_result}")
        except Exception as e:
            logger.error(f"❌ Error calculating TWR: {str(e)}", exc_info=True)
            self.results['twr'] = {'twr': 0.0, 'twr_percent': 0.0, 'error': str(e)}
        
        logger.info("Core performance calculations completed")
        return self.results
    
    def _calculate_mtm_analysis(self, basis, matching):
        """
        Calculate mark-to-market gains
        
        Args:
            basis: 'adjusted' or 'unadjusted'
            matching: 'fifo' or 'lifo'
        
        Returns:
            dict with MTM analysis
        """
        logger.info(f"Calculating MTM analysis for client {self.data.client_id}, period {self.data.start_date} to {self.data.end_date}")
        
        try:
            # Use the existing function from v1 (it will use data from data_collector internally)
            # Note: The v1 function fetches portfolios again, but for now we'll use it
            # In the future, we could optimize this to use already-fetched data
            mtm_result = calculate_mark_to_market_gains(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date,
                self.data.current_holdings,
                start_portfolio=self.data.start_portfolio,
                end_portfolio=self.data.end_portfolio,
            )
            
            # Log the result for debugging
            total_start_value = mtm_result.get('total_start_value', 0)
            total_end_value = mtm_result.get('total_end_value', 0)
            reconstruction_source = mtm_result.get('reconstruction_source_start', 'unknown')
            error = mtm_result.get('error')
            
            logger.info(f"MTM Analysis Result - Start Value: ₹{total_start_value:,.2f}, End Value: ₹{total_end_value:,.2f}, Source: {reconstruction_source}")
            
            if error:
                logger.error(f"MTM Analysis returned with error: {error}")
            if reconstruction_source == 'error':
                logger.error(f"MTM Analysis failed - reconstruction_source_start is 'error'. Error: {error}")
            if total_start_value == 0 and reconstruction_source != 'error':
                logger.warning(f"MTM Analysis: total_start_value is 0 but no error reported. This may indicate portfolio value is actually 0 on {self.data.start_date}")
            
            return mtm_result
        except Exception as e:
            logger.error(f"Exception in _calculate_mtm_analysis for client {self.data.client_id}, period {self.data.start_date} to {self.data.end_date}: {str(e)}", exc_info=True)
            # Return error result similar to what calculate_mark_to_market_gains returns
            return {
                'total_start_value': 0,
                'total_end_value': 0,
                'total_mtm_gain': 0,
                'total_mtm_gain_percent': 0,
                'total_current_value': 0,
                'start_holdings_count': 0,
                'end_holdings_count': 0,
                'reconstruction_source_start': 'error',
                'reconstruction_source_end': 'error',
                'mtm_gains_by_stock': [],
                'error': str(e)
            }
    
    def _calculate_trade_analysis(self, basis, matching):
        """
        Calculate trade gains
        
        Args:
            basis: 'adjusted' or 'unadjusted'
            matching: 'fifo' or 'lifo'
        
        Returns:
            dict with trade analysis
        """
        logger.info("Calculating trade analysis")
        
        return calculate_trade_gains_in_period(
            self.data.client_id,
            self.data.start_date,
            self.data.end_date
        )
    
    def _calculate_investment_breakdown(self):
        """
        Calculate investment breakdown
        
        Returns:
            dict with investment breakdown
        """
        logger.info("Calculating investment breakdown")
        
        return calculate_investment_breakdown(
            self.data.client_id,
            self.data.start_date,
            self.data.end_date
        )
    
    def _calculate_gains_breakdown(self):
        """
        Calculate gains breakdown
        
        Returns:
            dict with gains breakdown
        """
        logger.info("Calculating gains breakdown")
        
        return calculate_gains_breakdown(
            self.data.client_id,
            self.data.start_date,
            self.data.end_date,
            self.data.current_holdings
        )
    
    def _calculate_period_xirr(self):
        """
        Calculate period XIRR using collected data
        
        Returns:
            dict with XIRR data
        """
        logger.info("Calculating period XIRR")
        
        # Get portfolio values from collected data
        start_value = float(self.data.start_portfolio.get('total_value', 0))
        end_value = float(self.data.end_portfolio.get('total_value', 0))

        # If period start is on or before the client's first cashflow day, XIRR opening = 0 (cashflows
        # define funding timing; same rule as api.v1.period_analysis).
        earliest_cf = Cashflow.query.filter_by(client_id=self.data.client_id).order_by(Cashflow.date.asc()).first()
        first_cashflow_date = None
        if earliest_cf and earliest_cf.date is not None:
            raw_cf = earliest_cf.date
            if isinstance(raw_cf, datetime):
                first_cashflow_date = raw_cf.date()
            elif isinstance(raw_cf, date):
                first_cashflow_date = raw_cf
        xirr_start_value = float(start_value) if start_value else 0.0
        if first_cashflow_date is not None and self.data.start_date <= first_cashflow_date:
            xirr_start_value = 0.0
            logger.info(
                f"Period XIRR (v2): period start {self.data.start_date} on or before first cashflow "
                f"{first_cashflow_date}; XIRR opening ₹0 (reconstructed start ₹{start_value:,.2f})"
            )
        
        # Build cashflow list for XIRR
        cashflow_data = []
        
        if xirr_start_value > 0:
            cashflow_data.append((
                datetime.combine(self.data.start_date, datetime.min.time()),
                -float(xirr_start_value)
            ))
        
        # Add period cashflows
        for cf in self.data.period_cashflows:
            cashflow_data.append((cf.date, float(cf.amount)))
        
        if end_value > 0:
            # Use min.time() for consistency - XIRR treats dates as discrete days
            cashflow_data.append((
                datetime.combine(self.data.end_date, datetime.min.time()),
                float(end_value)
            ))
        
        if not cashflow_data:
            logger.warning("No cashflow data available for XIRR calculation")
            return {
                'xirr': 0.0,
                'xirr_percent': 0.0,
                'total_invested': 0.0,
                'total_withdrawn': 0.0,
                'net_investment': 0.0,
                'actual_net_investment': 0.0,
                'start_value': start_value,
                'xirr_opening_portfolio_value': xirr_start_value,
                'end_value': end_value,
                'period_cashflows_count': 0
            }
        
        # Calculate XIRR
        xirr, total_invested, total_withdrawn, net_investment, _ = calculate_xirr(
            cashflow_data, 0
        )
        
        # Calculate net investment from actual cashflows only (exclude start/end portfolio values)
        actual_total_invested = Decimal('0.0')
        actual_total_withdrawn = Decimal('0.0')
        for cf in self.data.all_cashflows:
            cf_amount = Decimal(str(cf.amount))
            if cf_amount < 0:  # Negative = investment (money in)
                actual_total_invested += abs(cf_amount)
            else:  # Positive = withdrawal (money out)
                actual_total_withdrawn += cf_amount
        
        # Calculate net investment in period
        total_investments_in_period = Decimal('0.0')
        total_withdrawals_in_period = Decimal('0.0')
        for cf in self.data.period_cashflows:
            cf_amount = Decimal(str(cf.amount))
            if cf_amount < 0:
                total_investments_in_period += abs(cf_amount)
            else:
                total_withdrawals_in_period += cf_amount
        net_investment_in_period = float(total_investments_in_period - total_withdrawals_in_period)
        
        # Calculate adjusted absolute return
        change_in_value = end_value - start_value
        average_portfolio_value = (start_value + end_value) / 2 if (start_value + end_value) != 0 else 0
        adjusted_absolute_return = ((change_in_value - net_investment_in_period) / average_portfolio_value * 100) if average_portfolio_value != 0 else 0.0
        
        # Format cashflow_data for verification (date, amount pairs)
        cashflow_verification = []
        for cf_date, cf_amount in cashflow_data:
            # Handle both datetime and date objects
            if hasattr(cf_date, 'date'):
                cf_date_obj = cf_date.date()
            elif hasattr(cf_date, 'isoformat'):
                cf_date_obj = cf_date
            else:
                cf_date_obj = cf_date
            
            # Determine description
            description = 'Period Cashflow'
            if cf_amount < 0:
                if hasattr(cf_date_obj, '__eq__') and cf_date_obj == self.data.start_date and xirr_start_value > 0:
                    description = 'Start Portfolio Value'
                else:
                    description = 'Investment'
            elif cf_amount > 0:
                if hasattr(cf_date_obj, '__eq__') and cf_date_obj == self.data.end_date:
                    description = 'End Portfolio Value'
                else:
                    description = 'Withdrawal'
            
            cashflow_verification.append({
                'date': cf_date_obj.isoformat() if hasattr(cf_date_obj, 'isoformat') else str(cf_date_obj),
                'amount': float(cf_amount),
                'type': 'Investment' if cf_amount < 0 else 'Withdrawal',
                'description': description
            })
        
        return {
            'xirr': xirr,
            'xirr_percent': xirr * 100,
            'total_invested': total_invested,
            'total_withdrawn': total_withdrawn,
            'net_investment': net_investment,
            'actual_net_investment': float(actual_total_invested - actual_total_withdrawn),
            'absolute_return': adjusted_absolute_return,
            'net_investment_in_period': net_investment_in_period,
            'change_in_value': change_in_value,
            'average_portfolio_value': average_portfolio_value,
            'current_value': end_value,
            'start_value': start_value,
            'xirr_opening_portfolio_value': xirr_start_value,
            'period_cashflows_count': len(self.data.period_cashflows),
            'cashflow_verification': cashflow_verification  # For UI verification table
        }
    
    def _calculate_twr(self):
        """
        Calculate Time-Weighted Return (TWR) for the period
        
        Returns:
            dict with TWR data
        """
        logger.info("Calculating Time-Weighted Return (TWR)")
        logger.info(f"TWR Input - client_id: {self.data.client_id}, start_date: {self.data.start_date}, end_date: {self.data.end_date}")
        logger.info(f"TWR Input - start_portfolio keys: {self.data.start_portfolio.keys() if isinstance(self.data.start_portfolio, dict) else 'N/A'}")
        logger.info(f"TWR Input - end_portfolio keys: {self.data.end_portfolio.keys() if isinstance(self.data.end_portfolio, dict) else 'N/A'}")
        logger.info(f"TWR Input - period_cashflows count: {len(self.data.period_cashflows) if self.data.period_cashflows else 0}")
        
        try:
            # Use the calculate_twr function from v1
            # It needs: client_id, start_date, end_date, start_portfolio, end_portfolio, period_cashflows
            twr_data = calculate_twr(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date,
                self.data.start_portfolio,
                self.data.end_portfolio,
                self.data.period_cashflows
            )
            
            logger.info(f"TWR Result: {twr_data}")
            return twr_data
        except Exception as e:
            logger.error(f"Error in TWR calculation: {str(e)}", exc_info=True)
            return {'twr': 0.0, 'twr_percent': 0.0, 'error': str(e)}



