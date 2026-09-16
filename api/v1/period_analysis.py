"""
Period Analysis API - Comprehensive period-based portfolio analysis
Analyzes portfolio performance for specific periods (6M, 12M, YTD, Custom)

This API provides detailed analysis including:
1. Mark-to-market gains from portfolio N months before
2. Gains from trades done in the period
3. Investment breakdown by asset class during the period
4. Portfolio gains breakdown (client additions vs unrealized profits)
5. Stocks contributing most in the period
6. Stocks doing worst in the period
"""
import logging
from datetime import datetime, timedelta, date
from decimal import Decimal
from flask import Blueprint, request
from sqlalchemy import and_, or_

from api.core.response import APIResponse
from api.core.exceptions import ValidationError
from models import db, Client, Security, Holding, Transaction, Cashflow
from api.v1.portfolio_reconstruction import PortfolioReconstructionService
from services.forward_holding_calculation_service import get_client_portfolio_by_date
# Adjustment API is now called through Portfolio Reconstruction Service
# No direct imports needed here

# Import audit logger
from utils.audit_logger import audit_api_call, log_api_call

# Custom logging handler to capture warnings and errors
class WarningCaptureHandler(logging.Handler):
    """Custom handler to capture warning and error log messages"""
    def __init__(self, warnings_list):
        super().__init__()
        self.warnings_list = warnings_list
        self.setLevel(logging.WARNING)  # Only capture WARNING and above
    
    def emit(self, record):
        """Capture log record and add to warnings list"""
        try:
            # Only capture WARNING and ERROR level messages
            if record.levelno >= logging.WARNING:
                # Format the log message - use simple format to avoid recursion
                try:
                    log_message = record.getMessage()
                except Exception:
                    log_message = str(record.msg) if hasattr(record, 'msg') else str(record)
                
                # Determine severity based on log level
                severity = 'error' if record.levelno >= logging.ERROR else 'warning'
                
                # Extract context from log record
                warning_entry = {
                    'type': 'log_message',
                    'severity': severity,
                    'level': logging.getLevelName(record.levelno),
                    'message': log_message,
                    'module': getattr(record, 'module', getattr(record, 'name', 'Unknown')),
                    'function': getattr(record, 'funcName', None),
                    'line': getattr(record, 'lineno', None),
                    'timestamp': datetime.fromtimestamp(record.created).isoformat() if hasattr(record, 'created') else datetime.now().isoformat()
                }
                
                self.warnings_list.append(warning_entry)
        except Exception:
            # Don't let logging errors break the application
            pass
from api.v1.performance import (
    calculate_xirr,
    calculate_nifty_xirr,
    calculate_risk_metrics,
    calculate_period_trade_analytics,
    calculate_sector_performance_in_period
)

logger = logging.getLogger(__name__)

# Create blueprint
period_analysis_bp = Blueprint('period_analysis', __name__)

@period_analysis_bp.route('/<int:client_id>/period-analysis', methods=['GET'])
def get_period_analysis(client_id):
    """
    Get comprehensive period analysis for a client
    
    Query Parameters:
        - start_date (string, required): Period start date (YYYY-MM-DD)
        - end_date (string, required): Period end date (YYYY-MM-DD)
        - analysis_type (string, optional): '6M', '12M', 'YTD', 'CUSTOM'
    
    Returns comprehensive analysis including:
    1. Mark-to-market gains from portfolio at period start
    2. Realized gains from trades during the period
    3. Investment breakdown by asset class
    4. Portfolio gains breakdown (additions vs unrealized profits)
    5. Top contributors in the period
    6. Worst performers in the period
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get and validate parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        analysis_type = request.args.get('analysis_type', 'CUSTOM')
        basis = request.args.get('basis', 'adjusted').lower()
        matching = request.args.get('matching', 'fifo').lower()
        
        # Validate end_date first (always required)
        if not end_date_str:
            raise ValidationError("end_date is required")
        
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError as e:
            raise ValidationError(f"Invalid end_date format. Use YYYY-MM-DD: {str(e)}")

        # Handle missing start_date for lifetime-style analysis
        if not start_date_str:
            # Support multiple aliases for lifetime analysis
            lifetime_types = {'ALL', 'LIFETIME', 'LIFE', 'FULL'}
            if analysis_type and analysis_type.upper() in lifetime_types:
                # Derive start_date as the earliest cashflow/transaction date for the client
                earliest_cf = Cashflow.query.filter_by(client_id=client_id)\
                    .order_by(Cashflow.date.asc()).first()
                earliest_txn = Transaction.query.filter_by(client_id=client_id)\
                    .order_by(Transaction.transaction_date.asc()).first()

                # Pick the earliest of cashflow / transaction dates, if any
                candidate_dates = []
                if earliest_cf and earliest_cf.date:
                    cf_date = earliest_cf.date.date() if hasattr(earliest_cf.date, 'date') else earliest_cf.date
                    candidate_dates.append(cf_date)
                if earliest_txn and earliest_txn.transaction_date:
                    txn_date = earliest_txn.transaction_date.date() if hasattr(earliest_txn.transaction_date, 'date') else earliest_txn.transaction_date
                    candidate_dates.append(txn_date)

                if candidate_dates:
                    # IMPORTANT (Lifetime baseline):
                    # For lifetime analysis, the earliest activity date often contains the first cashflow/BUY,
                    # and portfolio snapshots for that same date can already reflect that activity.
                    # If we use that same date as the period start, the first cashflow can be implicitly
                    # counted once in "start value" and again in "net cashflow" (double counting) when
                    # computing metrics like accumulated profit: End − Start − NetCashflow.
                    #
                    # Fix: shift the lifetime start_date back by 1 day to create a true opening baseline
                    # (start portfolio value ~ 0, with all cashflows counted exactly once in net cashflow).
                    earliest_activity_date = min(candidate_dates)
                    start_date = earliest_activity_date - timedelta(days=1)
                    start_date_str = start_date.strftime('%Y-%m-%d')
                    logger.info(
                        f"Derived lifetime start_date={start_date_str} (shifted back 1 day from earliest activity {earliest_activity_date}) "
                        f"for client {client_id} using analysis_type={analysis_type}"
                    )
                else:
                    # No real history available – don't use any dummy date
                    logger.warning(
                        f"No historical cashflows/transactions found for client {client_id} "
                        f"while requesting lifetime analysis"
                    )
                    raise ValidationError(
                        "Lifetime analysis is not available because the client has no historical cashflows or transactions"
                    )
            else:
                raise ValidationError("start_date and end_date are required")
        else:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            except ValueError as e:
                raise ValidationError(f"Invalid start_date format. Use YYYY-MM-DD: {str(e)}")
        
        # ✅ FIX: If start_date equals the first transaction/cashflow date, shift back by 1 day
        # to avoid double counting (start_value including first investment + cashflow on same date)
        earliest_cf = Cashflow.query.filter_by(client_id=client_id)\
            .order_by(Cashflow.date.asc()).first()
        earliest_txn = Transaction.query.filter_by(client_id=client_id)\
            .order_by(Transaction.transaction_date.asc()).first()
        
        candidate_first_dates = []
        if earliest_cf and earliest_cf.date:
            cf_date = earliest_cf.date.date() if hasattr(earliest_cf.date, 'date') else earliest_cf.date
            candidate_first_dates.append(cf_date)
        if earliest_txn and earliest_txn.transaction_date:
            txn_date = earliest_txn.transaction_date.date() if hasattr(earliest_txn.transaction_date, 'date') else earliest_txn.transaction_date
            candidate_first_dates.append(txn_date)
        
        if candidate_first_dates:
            first_activity_date = min(candidate_first_dates)
            if start_date == first_activity_date:
                logger.info(
                    f"Start date {start_date} equals first activity date {first_activity_date}. "
                    f"Shifting back by 1 day to avoid double counting (start_value + cashflow on same date)."
                )
                start_date = start_date - timedelta(days=1)
                logger.info(f"Adjusted start_date to {start_date}")
        
        if start_date > end_date:
            raise ValidationError("start_date must be before end_date")
        
        logger.info(f"Period analysis for client {client_id}: {start_date} to {end_date} (basis: {basis})")
        
        # ✅ CREATE COMPREHENSIVE AUDIT TRAIL
        from utils.audit_trail import AuditTrail
        audit = AuditTrail(client_id, 'period_analysis', start_date, end_date)
        
        audit.add_step("Input Parameters")
        audit.add_api_call("GET /api/v1/clients/{id}/period-analysis", {
            'client_id': client_id,
            'client_name': client.name,
            'start_date': start_date,
            'end_date': end_date,
            'duration_days': (end_date - start_date).days,
            'basis': basis,
            'matching': matching
        })
        
        # Get current holdings for all analysis
        current_holdings = Holding.query.filter_by(client_id=client_id).all()
        
        audit.add_step("Get Current Holdings")
        audit.add_text(f"Query: Holding.query.filter_by(client_id={client_id})")
        audit.add_text(f"Result: {len(current_holdings)} holdings found")
        audit.add_text("")
        
        # Log current holdings summary
        holdings_summary = []
        for h in current_holdings:
            if h.security:
                holdings_summary.append([
                    h.security.symbol,
                    f"{float(h.quantity):.2f}",
                    f"₹{float(h.average_price):,.2f}" if h.average_price else "N/A",
                    f"₹{float(h.security.current_price):,.2f}" if h.security.current_price else "N/A"
                ])
        
        audit.add_table("Current Holdings", 
                       ['Symbol', 'Quantity', 'Avg Price', 'Current Price'],
                       holdings_summary[:10])  # Show top 10 in audit
        audit.add_text(f"... and {len(current_holdings) - 10} more holdings") if len(current_holdings) > 10 else None
        
        # ✅ DETAILED PORTFOLIO CALCULATION USING FORWARD METHOD
        audit.add_step("Portfolio Calculation - Start Date (Forward)")
        audit.add_text(f"API Call: get_client_portfolio_by_date()")
        audit.add_text(f"Input Parameters:")
        audit.add_text(f"  - client_id: {client_id}")
        audit.add_text(f"  - as_of_date: {start_date}")
        audit.add_text(f"  - method: Forward calculation from transactions")
        audit.add_text("")
        
        # Get start state using forward calculation
        start_portfolio = get_client_portfolio_by_date(client_id, start_date)
        
        # Log the portfolio value for debugging
        start_value = float(start_portfolio.get('total_value', 0.0) or 0.0)
        logger.info(f"Portfolio value at {start_date}: ₹{start_value:,.2f} (holdings: {len(start_portfolio.get('holdings', []))})")
        audit.add_text(f"Portfolio value at {start_date}: ₹{start_value:,.2f}")
        
        # Convert to format expected by rest of the code
        start_state = {
            'holdings': start_portfolio['holdings'],
            'total_value': start_portfolio['total_value'],
            'total_cost': start_portfolio['total_cost'],
            'source': 'forward_calculation',
            'as_of_date': start_date
        }
        
        # Log start portfolio in detail
        start_holdings_rows = []
        start_total = 0
        for h in start_state['holdings']:
            qty = float(h['quantity'])
            # Handle different key names for price
            price = float(h.get('price', h.get('current_price', 0)))
            # Handle different key names for value
            value = float(h.get('value', h.get('current_value', 0)))
            start_total += value
            start_holdings_rows.append([
                h['symbol'],
                f"{qty:.2f}",
                f"₹{price:,.2f}",
                f"₹{value:,.2f}"
            ])
        
        audit.add_table(f"Portfolio at Start ({start_date}) - ALL HOLDINGS",
                       ['Symbol', 'Quantity', 'Price', 'Value'],
                       sorted(start_holdings_rows, key=lambda x: float(x[3].replace('₹','').replace(',','')), reverse=True),
                       ['TOTAL', '', '', f"₹{start_total:,.2f}"])
        
        audit.add_step("Portfolio Calculation - End Date (Forward)")
        audit.add_text(f"API Call: get_client_portfolio_by_date()")
        audit.add_text(f"Input Parameters:")
        audit.add_text(f"  - client_id: {client_id}")
        audit.add_text(f"  - as_of_date: {end_date}")
        audit.add_text(f"  - method: Forward calculation from transactions")
        audit.add_text("")
        
        # Get end state using forward calculation
        end_portfolio = get_client_portfolio_by_date(client_id, end_date)
        
        # Convert to format expected by rest of the code
        end_state = {
            'holdings': end_portfolio['holdings'],
            'total_value': end_portfolio['total_value'],
            'total_cost': end_portfolio['total_cost'],
            'source': 'forward_calculation',
            'as_of_date': end_date
        }
        
        # Log end portfolio in detail
        end_holdings_rows = []
        end_total = 0
        for h in end_state['holdings']:
            qty = float(h['quantity'])
            # Handle different key names for price
            price = float(h.get('price', h.get('current_price', 0)))
            # Handle different key names for value
            value = float(h.get('value', h.get('current_value', 0)))
            end_total += value
            end_holdings_rows.append([
                h['symbol'],
                f"{qty:.2f}",
                f"₹{price:,.2f}",
                f"₹{value:,.2f}"
            ])
        
        audit.add_table(f"Portfolio at End ({end_date}) - ALL HOLDINGS",
                       ['Symbol', 'Quantity', 'Price', 'Value'],
                       sorted(end_holdings_rows, key=lambda x: float(x[3].replace('₹','').replace(',','')), reverse=True),
                       ['TOTAL', '', '', f"₹{end_total:,.2f}"])
        
        # Use Adjustment API for period analysis if basis is adjusted
        if basis == 'adjusted':
            try:
                adjusted_period = get_adjusted_period(client_id, start_date, end_date, matching, basis)
                
                # Extract adjusted metrics
                start_snapshot = adjusted_period['start_snapshot']
                end_snapshot = adjusted_period['end_snapshot']
                period_change = adjusted_period['period_change']
                
                # 1. MARK-TO-MARKET GAINS FROM PERIOD START (adjusted)
                mtm_analysis = {
                    'period_start_value': start_snapshot['summary']['total_market_value_adjusted'],
                    'period_end_value': end_snapshot['summary']['total_market_value_adjusted'],
                    'mtm_gains': period_change['market_value_change'],
                    'unrealized_gains_change': period_change['unrealized_pnl_change'],
                    'basis': 'adjusted'
                }
                
                # 2. TRADE GAINS DURING PERIOD (adjusted)
                trade_analysis = {
                    'realized_gains_in_period': period_change['realized_pnl_in_period'],
                    'total_realized_gains_to_date': end_snapshot['summary']['total_realized_pnl'],
                    'basis': 'adjusted'
                }
                
                logger.info(f"Using adjusted period analysis for client {client_id}")
                
            except Exception as e:
                logger.warning(f"Failed to get adjusted period analysis, falling back to regular analysis: {str(e)}")
                # Fall back to regular analysis
                mtm_analysis = calculate_mark_to_market_gains(client_id, start_date, end_date, current_holdings)
                trade_analysis = calculate_trade_gains_in_period(client_id, start_date, end_date)
        else:
            # Regular analysis
            audit.add_step("Calculate Mark-to-Market Gains")
            audit.add_text("Calculating gain/loss for each security:")
            audit.add_text("  Formula: End Value - Start Value = Gain")
            audit.add_text("")
            
            mtm_analysis = calculate_mark_to_market_gains(client_id, start_date, end_date, current_holdings)
            
            # Log MTM results summary
            audit.add_calculation(
                "Total Portfolio Change",
                f"End Value - Start Value = ₹{mtm_analysis['total_end_value']:,.2f} - ₹{mtm_analysis['total_start_value']:,.2f}",
                f"₹{mtm_analysis['total_mtm_gain']:,.2f} ({mtm_analysis['total_mtm_gain_percent']:.2f}%)"
            )
            audit.add_text(f"Holdings at start: {mtm_analysis['start_holdings_count']}")
            audit.add_text(f"Holdings at end: {mtm_analysis['end_holdings_count']}")
            audit.add_text("")
            
            # Add COMPLETE MTM gains by stock table (all securities)
            mtm_stocks = mtm_analysis.get('mtm_gains_by_stock', [])
            mtm_rows = []
            for s in sorted(mtm_stocks, key=lambda x: x['gain'], reverse=True):
                mtm_rows.append([
                    s['symbol'],
                    f"₹{s['start_value']:,.2f}",
                    f"₹{s['end_value']:,.2f}",
                    f"₹{s['gain']:,.2f}",
                    f"{s['gain_percent']:.2f}%"
                ])
            
            audit.add_table("Complete MTM Gains by Security (ALL SECURITIES)",
                           ['Symbol', 'Start Value', 'End Value', 'Gain', 'Gain %'],
                           mtm_rows,
                           ['TOTAL', 
                            f"₹{mtm_analysis['total_start_value']:,.2f}",
                            f"₹{mtm_analysis['total_end_value']:,.2f}",
                            f"₹{mtm_analysis['total_mtm_gain']:,.2f}",
                            f"{mtm_analysis['total_mtm_gain_percent']:.2f}%"])
            
            audit.add_step("Calculate Trade Gains in Period")
            audit.add_text("Finding all SELL transactions in the period and calculating realized gains")
            audit.add_text("  Using FIFO (First In, First Out) matching")
            audit.add_text("")
            
            trade_analysis = calculate_trade_gains_in_period(client_id, start_date, end_date)
            
            audit.add_text(f"Total Realized Gains: ₹{trade_analysis['total_realized_gain']:,.2f}")
            audit.add_text(f"Number of SELL trades: {trade_analysis['number_of_trades']}")
            audit.add_text(f"Average Gain %: {trade_analysis['average_gain_percent']:.2f}%")
            audit.add_text("")
            
            # Add detailed trade breakdown
            if trade_analysis.get('realized_gains_by_stock'):
                trade_rows = []
                for t in trade_analysis['realized_gains_by_stock']:
                    trade_rows.append([
                        t['symbol'],
                        t['sell_date'],
                        f"{t['quantity_sold']:.0f}",
                        f"₹{t['avg_buy_price']:,.2f}",
                        f"₹{t['sell_price']:,.2f}",
                        f"₹{t['realized_gain']:,.2f}",
                        f"{t['realized_gain_percent']:.2f}%"
                    ])
                
                audit.add_table("Realized Gains - Trade by Trade",
                               ['Symbol', 'Date', 'Qty', 'Buy Price', 'Sell Price', 'Gain', 'Gain %'],
                               trade_rows,
                               ['TOTAL', '', '', '', '', f"₹{trade_analysis['total_realized_gain']:,.2f}", ''])
        
        # ✅ ADD MTM SUMMARY (REGARDLESS OF BASIS)
        audit.add_section("Mark-to-Market Summary")
        if mtm_analysis.get('mtm_gains_by_stock'):
            # Add COMPLETE MTM gains by stock table (all securities)
            mtm_stocks = mtm_analysis.get('mtm_gains_by_stock', [])
            mtm_rows = []
            for s in sorted(mtm_stocks, key=lambda x: x.get('gain', 0), reverse=True):
                mtm_rows.append([
                    s['symbol'],
                    f"₹{s.get('start_value', 0):,.2f}",
                    f"₹{s.get('end_value', 0):,.2f}",
                    f"₹{s.get('gain', 0):,.2f}",
                    f"{s.get('gain_percent', 0):.2f}%"
                ])
            
            audit.add_table("Complete MTM Gains by Security (ALL SECURITIES - Sorted by Gain)",
                           ['Symbol', 'Start Value', 'End Value', 'Gain', 'Gain %'],
                           mtm_rows,
                           ['TOTAL', 
                            f"₹{mtm_analysis.get('total_start_value', 0):,.2f}",
                            f"₹{mtm_analysis.get('total_end_value', mtm_analysis.get('total_current_value', 0)):,.2f}",
                            f"₹{mtm_analysis.get('total_mtm_gain', 0):,.2f}",
                            f"{mtm_analysis.get('total_mtm_gain_percent', 0):.2f}%"])
        
        # 3. INVESTMENT BREAKDOWN BY ASSET CLASS
        audit.add_step("Calculate Investment Breakdown")
        investment_breakdown = calculate_investment_breakdown(client_id, start_date, end_date)
        
        audit.add_text(f"Gross Investments (BUY): ₹{investment_breakdown['gross_investment_in_period']:,.2f}")
        audit.add_text(f"Gross Withdrawals (SELL): ₹{investment_breakdown['gross_withdrawal_in_period']:,.2f}")
        audit.add_text(f"Net Investment: ₹{investment_breakdown['net_investment_in_period']:,.2f}")
        audit.add_text("")
        
        # 4. PORTFOLIO GAINS BREAKDOWN (Client additions vs Unrealized profits)
        audit.add_step("Calculate Portfolio Gains Breakdown")
        gains_breakdown = calculate_gains_breakdown(client_id, start_date, end_date, current_holdings)
        
        audit.add_calculation(
            "Total Portfolio Change",
            f"End Value - Start Value = ₹{gains_breakdown['current_portfolio_value']:,.2f} - ₹{gains_breakdown['portfolio_value_at_start']:,.2f}",
            f"₹{gains_breakdown['total_portfolio_change']:,.2f} ({gains_breakdown['total_portfolio_change_percent']:.2f}%)"
        )
        
        audit.add_text(f"Client Net Additions: ₹{gains_breakdown['client_additions']:,.2f}")
        audit.add_text(f"Unrealized Profit Contribution: ₹{gains_breakdown['unrealized_profit_contribution']:,.2f}")
        audit.add_text("")
        
        # 5. BEST PERFORMERS
        audit.add_step("Calculate Best Performers")
        best_performers = calculate_best_performers(client_id, start_date, end_date, current_holdings)
        
        top_rows = []
        for tc in best_performers['best_performers'][:10]:
            start_price = tc.get('start_price', 0)
            end_price = tc.get('end_price', tc.get('current_price', 0))
            top_rows.append([
                tc['symbol'],
                f"{tc.get('price_percent_change', 0):.2f}%",
                f"₹{start_price:,.2f} → ₹{end_price:,.2f}"
            ])
        
        audit.add_table("Top 10 Best Performers (Adjusted Price Change)",
                       ['Symbol', 'Price Change % (Adj)', 'Start → End Price'],
                       top_rows)
        
        # 6. WORST PERFORMERS
        # ✅ NEW ARCHITECTURE: Just reuse mtm_analysis data (already calculated correctly)
        audit.add_step("Calculate Worst Performers (from MTM data)")
        audit.add_text("Using mtm_gains_by_stock data - no recalculation needed")
        audit.add_text("")
        
        worst_performers = calculate_worst_performers_from_mtm(mtm_analysis, client_id, start_date, end_date)
        
        worst_rows = []
        for wp in worst_performers['worst_performers'][:10]:
            worst_rows.append([
                wp['symbol'],
                f"₹{wp['value_contribution']:,.2f}",
                f"{wp['contribution_percent']:.2f}%"
            ])
        
        audit.add_table("Worst 10 Performers",
                       ['Symbol', 'Value Contribution', 'Contribution %'],
                       worst_rows)
        
        # Additional analytics using existing functions
        # Calculate trade analytics with proper structure
        try:
            trade_analytics_raw = calculate_period_trade_analytics(client_id, current_holdings, start_date, end_date)
        except Exception as e:
            logger.error(f"Error in calculate_period_trade_analytics: {str(e)}", exc_info=True)
            trade_analytics_raw = {}
        
        # Transform to match UI expectations
        # Get buy/sell transactions for totals
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        
        try:
            buy_txns = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.type == 'BUY',
                Transaction.transaction_date >= start_datetime,
                Transaction.transaction_date <= end_datetime
            ).all()
            sell_txns = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.type == 'SELL',
                Transaction.transaction_date >= start_datetime,
                Transaction.transaction_date <= end_datetime
            ).all()
            
            logger.info(f"Trade Analytics: Found {len(buy_txns)} BUY and {len(sell_txns)} SELL transactions")
            
            total_buy_value = sum(float(t.quantity) * float(t.price) for t in buy_txns)
            total_sell_value = sum(float(t.quantity) * float(t.price) for t in sell_txns)
            net_trade_value = total_buy_value - total_sell_value
            
            # Calculate win rate from sold stocks
            realized_pnl = trade_analytics_raw.get('total_realized_pnl', 0)
            stocks_sold_count = len(trade_analytics_raw.get('stocks_sold_in_period', []))
            winning_trades = len([s for s in trade_analytics_raw.get('stocks_sold_in_period', []) if s.get('realized_gain', 0) > 0])
            win_rate = (winning_trades / stocks_sold_count * 100) if stocks_sold_count > 0 else 0
            
            # Calculate turnover ratio
            start_portfolio_value = mtm_analysis.get('total_start_value', 0) or mtm_analysis.get('total_value_at_period_start', 0)
            turnover_ratio = (total_buy_value / start_portfolio_value) if start_portfolio_value > 0 else 0
            
            trade_analytics = {
                'total_buy_value': total_buy_value,
                'total_sell_value': total_sell_value,
                'net_trade_value': net_trade_value,
                'buy_trades_count': len(buy_txns),
                'sell_trades_count': len(sell_txns),
                'total_trades_count': len(buy_txns) + len(sell_txns),
                'win_rate_percent': win_rate,
                'avg_holding_period_days': 30,  # Simplified - could calculate from actual trades
                'turnover_ratio': turnover_ratio,
                'total_realized_pnl': realized_pnl,
                'best_performers_in_period': trade_analytics_raw.get('best_performers_in_period', []),
                'worst_performers_in_period': trade_analytics_raw.get('worst_performers_in_period', []),
                'stocks_added_in_period': trade_analytics_raw.get('stocks_added_in_period', []),
                'stocks_sold_in_period': trade_analytics_raw.get('stocks_sold_in_period', [])
            }
        except Exception as e:
            logger.error(f"Error calculating trade analytics: {str(e)}", exc_info=True)
            trade_analytics = {
                'total_buy_value': 0,
                'total_sell_value': 0,
                'net_trade_value': 0,
                'buy_trades_count': 0,
                'sell_trades_count': 0,
                'total_trades_count': 0,
                'win_rate_percent': 0,
                'avg_holding_period_days': 0,
                'turnover_ratio': 0
            }
        sector_performance = calculate_sector_performance_in_period(client_id, current_holdings, start_date, end_date)
        
        # Calculate period XIRR
        # For period XIRR: portfolio value at start_date (as investment) + cashflows during period + current value (as withdrawal)
        
        # Get portfolio value at start of period using portfolio construction service
        start_portfolio = get_client_portfolio_by_date(client_id, start_date)
        start_value = start_portfolio.get('total_value', 0)
        logger.info(f"Period XIRR: Portfolio value at start_date ({start_date}): ₹{start_value:,.2f}")
        
        # Get portfolio value at end of period using portfolio construction service
        end_portfolio = get_client_portfolio_by_date(client_id, end_date)
        current_value = end_portfolio.get('total_value', 0)
        logger.info(f"Period XIRR: Portfolio value at end_date ({end_date}): ₹{current_value:,.2f}")
        
        # Fallback to MTM analysis if portfolio construction service failed
        if current_value == 0:
            logger.warning("Portfolio construction service returned 0, falling back to MTM analysis")
            current_value = mtm_analysis.get('total_current_value', 0) or mtm_analysis.get('total_end_value', 0)
        
        if start_value == 0:
            logger.warning("Portfolio construction service returned 0 for start value, falling back to MTM analysis")
            start_value = mtm_analysis.get('total_start_value', 0) or mtm_analysis.get('total_value_at_period_start', 0)
        
        # Get cashflows during the period (between start_date and end_date)
        # Handle both date and datetime types for comparison
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        
        # Query cashflows - SQLAlchemy will handle date/datetime comparison automatically
        period_cashflows = Cashflow.query.filter(
            Cashflow.client_id == client_id,
            Cashflow.date >= start_date,  # Use date for comparison (SQLAlchemy handles conversion)
            Cashflow.date <= end_date
        ).order_by(Cashflow.date).all()
        
        logger.info(f"Period XIRR: Querying cashflows for client {client_id} between {start_date} and {end_date}")
        logger.info(f"Period XIRR: Found {len(period_cashflows)} cashflows in period")
        if len(period_cashflows) > 0:
            logger.info(f"Period XIRR: First cashflow: {period_cashflows[0].date}, Amount: ₹{period_cashflows[0].amount:,.2f}, Description: {period_cashflows[0].description or 'N/A'}")
            if len(period_cashflows) > 1:
                logger.info(f"Period XIRR: Last cashflow: {period_cashflows[-1].date}, Amount: ₹{period_cashflows[-1].amount:,.2f}, Description: {period_cashflows[-1].description or 'N/A'}")
            # Log all cashflows for debugging
            for idx, cf in enumerate(period_cashflows, 1):
                logger.info(f"Period XIRR: Cashflow {idx}/{len(period_cashflows)}: Date={cf.date}, Amount=₹{cf.amount:,.2f}, Description={cf.description or 'N/A'}")
        else:
            # Check if there are ANY cashflows for this client to debug
            all_client_cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
            logger.warning(f"Period XIRR: No cashflows found in period, but client has {len(all_client_cashflows)} total cashflows")
            if len(all_client_cashflows) > 0:
                logger.warning(f"Period XIRR: Earliest cashflow: {all_client_cashflows[0].date}, Latest: {all_client_cashflows[-1].date}")
                logger.warning(f"Period XIRR: Period range: {start_date} to {end_date}")
        
        # ✅ Get ALL cashflows from client's first transaction to end_date for net investment calculation
        # This gives us the total client investment (net cashflow) to use as denominator for Total Return
        all_cashflows_for_net_investment = Cashflow.query.filter(
            Cashflow.client_id == client_id,
            Cashflow.date <= datetime.combine(end_date, datetime.max.time())
        ).order_by(Cashflow.date).all()
        
        period_xirr_data = {}

        # Pre-calculate period investment totals for adjusted absolute return
        total_investments_in_period = Decimal('0.0')
        total_withdrawals_in_period = Decimal('0.0')
        for cf in period_cashflows:
            cf_amount = Decimal(str(cf.amount))
            if cf_amount < 0:
                total_investments_in_period += abs(cf_amount)
            else:
                total_withdrawals_in_period += cf_amount
        net_investment_in_period = float(total_investments_in_period - total_withdrawals_in_period)
        
        # Period XIRR opening: compare period start_date to the client's first cashflow date (calendar
        # day). If start is on or before that first cashflow day, opening portfolio for XIRR is 0;
        # otherwise use reconstructed value at start_date.
        earliest_cf = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date.asc()).first()
        first_cashflow_date = None
        if earliest_cf and earliest_cf.date is not None:
            raw_cf = earliest_cf.date
            if isinstance(raw_cf, datetime):
                first_cashflow_date = raw_cf.date()
            elif isinstance(raw_cf, date):
                first_cashflow_date = raw_cf
        xirr_start_value = float(start_value) if start_value else 0.0
        if first_cashflow_date is not None and start_date <= first_cashflow_date:
            xirr_start_value = 0.0
            logger.info(
                f"Period XIRR: period start {start_date} is on or before first cashflow {first_cashflow_date}; "
                f"XIRR opening portfolio ₹0 (reconstructed start was ₹{float(start_value):,.2f})"
            )
        
        # Build cashflow list for period XIRR:
        # 1. Portfolio value at start_date as negative cashflow (investment)
        # 2. All cashflows during the period
        # 3. Current value at end_date as positive cashflow (withdrawal)
        cashflow_data = []
        
        if xirr_start_value > 0:
            # Add portfolio value at start as negative cashflow (we're "investing" this amount at the start)
            cashflow_data.append((datetime.combine(start_date, datetime.min.time()), -float(xirr_start_value)))
            logger.info(f"Period XIRR: Added start portfolio value ₹{xirr_start_value:,.2f} as investment at {start_date}")
        
        # Add all cashflows during the period
        for cf in period_cashflows:
            # Ensure date is datetime for consistency
            cf_date = cf.date
            if isinstance(cf_date, date) and not isinstance(cf_date, datetime):
                cf_date = datetime.combine(cf_date, datetime.min.time())
            cashflow_data.append((cf_date, float(cf.amount)))
            logger.info(f"Period XIRR: Added cashflow ₹{cf.amount:,.2f} on {cf.date} (description: {cf.description or 'N/A'})")
        
        if current_value > 0:
            # Add current value as positive cashflow (we're "withdrawing" this amount at the end)
            cashflow_data.append((datetime.combine(end_date, datetime.max.time()), float(current_value)))
            logger.info(f"Period XIRR: Added current portfolio value ₹{current_value:,.2f} as withdrawal at {end_date}")
        
        if cashflow_data:
            # Log detailed cashflow data for verification
            logger.info(f"=== PERIOD XIRR CALCULATION INPUTS ===")
            logger.info(f"Period: {start_date} to {end_date}")
            logger.info(
                f"Start Portfolio Value (XIRR opening): ₹{xirr_start_value:,.2f} (at {start_date}); "
                f"reconstructed at start: ₹{float(start_value):,.2f}"
            )
            logger.info(f"End Portfolio Value: ₹{current_value:,.2f} (at {end_date})")
            logger.info(f"Cashflows during period: {len(period_cashflows)}")
            logger.info(f"Total cashflows for XIRR: {len(cashflow_data)}")
            logger.info(f"Cashflow details:")
            for i, (cf_date, cf_amount) in enumerate(cashflow_data, 1):
                logger.info(f"  {i}. Date: {cf_date.strftime('%Y-%m-%d')}, Amount: ₹{cf_amount:,.2f} ({'Investment' if cf_amount < 0 else 'Withdrawal'})")
            
            # Calculate XIRR with the period cashflows
            # Note: We pass 0 as current_value because we've already included current_value at end_date in cashflow_data
            # The calculate_xirr function will add 0 at datetime.now(), which won't affect the calculation
            xirr, total_invested, total_withdrawn, net_investment, _ = calculate_xirr(
                cashflow_data, 0  # current_value is already in cashflow_data at end_date, so pass 0 to avoid double counting
            )
            
            # ✅ Calculate actual net client investment (net cashflow) - EXCLUDE start/end portfolio values
            # These were added for XIRR calculation but aren't actual client cashflows
            # Use ALL cashflows up to end_date to get total client investment (not just period cashflows)
            # Cashflows: negative amount = investment (money in), positive = withdrawal (money out)
            actual_total_invested = Decimal('0.0')
            actual_total_withdrawn = Decimal('0.0')
            for cf in all_cashflows_for_net_investment:
                cf_amount = Decimal(str(cf.amount))
                if cf_amount < 0:  # Negative = investment
                    actual_total_invested += abs(cf_amount)
                else:  # Positive = withdrawal
                    actual_total_withdrawn += cf_amount
            actual_net_investment_float = float(actual_total_invested - actual_total_withdrawn)
            
            # ✅ Calculate adjusted absolute return:
            # (Change in portfolio value - net investments during period) / average(start, end)
            current_value_float = float(current_value) if isinstance(current_value, Decimal) else current_value
            change_in_value = current_value_float - float(start_value)
            average_portfolio_value = (float(start_value) + current_value_float) / 2 if (float(start_value) + current_value_float) != 0 else 0
            adjusted_absolute_return = ((change_in_value - net_investment_in_period) / average_portfolio_value * 100) if average_portfolio_value != 0 else 0.0
            
            logger.info(f"Period XIRR: actual_net_investment (from cashflows only): ₹{actual_net_investment_float:,.2f}")
            logger.info(f"Period XIRR: net_investment_in_period: ₹{net_investment_in_period:,.2f}")
            logger.info(f"Period XIRR: change_in_value: ₹{change_in_value:,.2f}")
            logger.info(f"Period XIRR: average_portfolio_value: ₹{average_portfolio_value:,.2f}")
            logger.info(f"Period XIRR: adjusted_absolute_return: {adjusted_absolute_return:.2f}%")
            
            # Format cashflow data for response (for verification)
            cashflow_details = []
            for i, (cf_date, cf_amount) in enumerate(cashflow_data, 1):
                # Determine description based on position and value
                if i == 1 and cf_amount < 0 and xirr_start_value > 0:
                    description = "Portfolio value at start"
                elif i == len(cashflow_data) and cf_amount > 0 and current_value > 0:
                    description = "Portfolio value at end"
                else:
                    # For period cashflows, try to get description from original cashflow if available
                    description = "Period cashflow"
                    # Try to match with original period_cashflows to get description
                    if isinstance(cf_date, datetime):
                        cf_date_only = cf_date.date()
                    else:
                        cf_date_only = cf_date
                    for orig_cf in period_cashflows:
                        orig_cf_date = orig_cf.date.date() if isinstance(orig_cf.date, datetime) else orig_cf.date
                        if orig_cf_date == cf_date_only and abs(float(orig_cf.amount) - abs(cf_amount)) < 0.01:
                            description = orig_cf.description or f"Cashflow on {cf_date_only.strftime('%Y-%m-%d')}"
                            break
                
                cashflow_details.append({
                    'date': cf_date.strftime('%Y-%m-%d') if isinstance(cf_date, datetime) else str(cf_date),
                    'amount': float(cf_amount),
                    'type': 'investment' if cf_amount < 0 else 'withdrawal',
                    'description': description
                })
            
            logger.info(f"Period XIRR: Created {len(cashflow_details)} cashflow details for response")
            logger.info(f"Period XIRR: Cashflow details include {len([cf for cf in cashflow_details if cf['description'] == 'Period cashflow'])} period cashflows")
            
            period_xirr_data = {
                'xirr': xirr,
                'xirr_percent': xirr * 100,
                'total_invested': total_invested,
                'total_withdrawn': total_withdrawn,
                'net_investment': net_investment,  # From XIRR calculation (includes start portfolio for XIRR)
                'actual_net_investment': actual_net_investment_float,  # Actual client net cashflow (excludes start portfolio)
                'absolute_return': adjusted_absolute_return,
                'net_investment_in_period': net_investment_in_period,
                'change_in_value': change_in_value,
                'average_portfolio_value': average_portfolio_value,
                'current_value': current_value,
                'start_value': start_value,
                'xirr_opening_portfolio_value': xirr_start_value,
                'period_cashflows_count': len(period_cashflows),
                # Add detailed cashflow data for verification
                'xirr_calculation_inputs': {
                    'start_date': start_date.strftime('%Y-%m-%d'),
                    'end_date': end_date.strftime('%Y-%m-%d'),
                    'start_portfolio_value': float(xirr_start_value),
                    'end_portfolio_value': float(current_value),
                    'cashflows': cashflow_details,
                    'total_cashflows': len(cashflow_data)
                }
            }
            logger.info(
                f"Period XIRR calculated: {xirr * 100:.2f}% "
                f"(XIRR opening: ₹{xirr_start_value:,.2f}, recon start: ₹{float(start_value):,.2f}, "
                f"End: ₹{current_value:,.2f}, Period CFs: {len(period_cashflows)})"
            )
            logger.info(f"=== END XIRR CALCULATION ===")
        else:
            logger.warning("No cashflows available for period XIRR calculation")
            change_in_value = float(current_value) - float(start_value)
            average_portfolio_value = (float(start_value) + float(current_value)) / 2 if (float(start_value) + float(current_value)) != 0 else 0
            adjusted_absolute_return = ((change_in_value) / average_portfolio_value * 100) if average_portfolio_value != 0 else 0.0
            period_xirr_data = {
                'xirr': 0.0,
                'xirr_percent': 0.0,
                'total_invested': 0.0,
                'total_withdrawn': 0.0,
                'net_investment': 0.0,
                'absolute_return': adjusted_absolute_return,
                'net_investment_in_period': 0.0,
                'change_in_value': change_in_value,
                'average_portfolio_value': average_portfolio_value,
                'current_value': current_value,
                'start_value': start_value,
                'xirr_opening_portfolio_value': xirr_start_value,
                'period_cashflows_count': 0
            }
        
        # Calculate period risk metrics
        period_risk_metrics = calculate_risk_metrics(current_holdings, client_id) if current_holdings else {}
        
        # Calculate period vs benchmark comparison
        period_benchmark = calculate_period_benchmark_comparison(
            client_id, start_date, end_date, period_xirr_data.get('xirr', 0)
        )
        
        # Calculate Time-Weighted Return (TWR)
        audit.add_step("Calculate Time-Weighted Return (TWR)")
        twr_data = calculate_twr(
            client_id, start_date, end_date, 
            start_portfolio, end_portfolio, period_cashflows
        )
        audit.add_text(f"TWR: {twr_data.get('twr_percent', 0):.2f}% ({twr_data.get('sub_periods', 0)} sub-periods)")
        if twr_data.get('error'):
            audit.add_text(f"TWR Error: {twr_data.get('error')}")
        audit.add_text("")
        
        # Enhanced summary metrics (now includes TWR)
        summary_metrics = calculate_period_summary(
            mtm_analysis, trade_analysis, investment_breakdown, 
            gains_breakdown, period_xirr_data, twr_data
        )
        
        # Calculate holdings changes in period
        # Calculate holdings changes using portfolio construction API data
        holdings_changes = calculate_holdings_changes_in_period(
            client_id, start_date, end_date, start_portfolio, end_portfolio
        )
        
        # Calculate cashflow analysis
        cashflow_analysis = calculate_period_cashflow_analysis(
            client_id, start_date, end_date
        )
        
        # ✅ NEW: VALUE ATTRIBUTION ANALYSIS
        audit.add_step("Calculate Value Attribution Analysis")
        audit.add_text("Building holdings maps for start and end portfolios...")
        
        # Build holdings maps for easy lookup
        start_map = build_holdings_map(start_portfolio)
        end_map = build_holdings_map(end_portfolio)
        
        # Get period transactions for analysis
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        txns_in_period = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date >= start_datetime,
            Transaction.transaction_date <= end_datetime
        ).all()
        
        audit.add_text(f"Start portfolio: {len(start_map)} securities")
        audit.add_text(f"End portfolio: {len(end_map)} securities")
        audit.add_text(f"Period transactions: {len(txns_in_period)}")
        audit.add_text("")
        
        # Calculate value attribution
        value_attribution = calculate_value_attribution(start_map, end_map, txns_in_period, start_date, end_date)
        
        # Calculate sold stocks analysis first (needed for wealth creators/destroyers)
        stocks_sold = calculate_stocks_sold_analysis(client_id, start_date, end_date)
        
        # Calculate wealth creators and destroyers
        # ✅ UPDATED: Use profit-change ranking (Δ unrealized P&L + realized gain in period)
        # This aligns Section 9 with the "Portfolio Holdings Comparison" profit deltas.
        try:
            contrib_list = compute_profit_change_contribution(start_map, end_map, stocks_sold)
            wealth_analysis = calculate_wealth_creators_destroyers(contrib_list)
            logger.info(
                f"Wealth analysis (profit change): {len(wealth_analysis.get('wealth_creators', []))} creators, "
                f"{len(wealth_analysis.get('wealth_destroyers', []))} destroyers"
            )
        except Exception as e:
            logger.error(f"Error calculating wealth creators/destroyers: {str(e)}", exc_info=True)
            wealth_analysis = {'wealth_creators': [], 'wealth_destroyers': [], 'total_wealth_created': 0, 'total_wealth_destroyed': 0}
        
        # Calculate most/least profitable MTM trades in period
        try:
            mtm_trades_analysis = calculate_mtm_trades_profitability(client_id, start_date, end_date, end_map)
            logger.info(f"MTM trades analysis: {len(mtm_trades_analysis.get('most_profitable_mtm_trades', []))} most profitable, {len(mtm_trades_analysis.get('least_profitable_mtm_trades', []))} least profitable")
        except Exception as e:
            logger.error(f"Error calculating MTM trades profitability: {str(e)}", exc_info=True)
            mtm_trades_analysis = {'most_profitable_mtm_trades': [], 'least_profitable_mtm_trades': []}
        
        # Calculate most bought stocks
        try:
            most_bought = calculate_stocks_most_bought(client_id, start_date, end_date, end_map)
            logger.info(f"Most bought stocks: {len(most_bought)} stocks")
        except Exception as e:
            logger.error(f"Error calculating most bought stocks: {str(e)}", exc_info=True)
            most_bought = []
        
        # Calculate previous period most bought stocks performance (if prev_period_start_date is provided)
        prev_period_most_bought_perf = []
        prev_period_start_date_str = request.args.get('prev_period_start_date')
        if prev_period_start_date_str:
            try:
                # prev_period_start_date_str is actually the previous period's START date
                # But we want to find stocks bought BETWEEN current period start and previous period start
                # So the "previous period" for calculation is: current_period_start to prev_period_start_date_str
                prev_period_end_date = datetime.strptime(prev_period_start_date_str, '%Y-%m-%d').date()
                # Previous period start should be the current period start
                prev_period_start_date = start_date
                prev_period_most_bought_perf = calculate_previous_period_most_bought_performance(
                    client_id, prev_period_start_date, prev_period_end_date, start_date, end_date, end_map
                )
                logger.info(f"Previous period most bought performance: {len(prev_period_most_bought_perf)} stocks (period: {prev_period_start_date} to {prev_period_end_date})")
            except Exception as e:
                logger.error(f"Error calculating previous period most bought performance: {str(e)}", exc_info=True)
                prev_period_most_bought_perf = []

        # Calculate transaction quality metrics
        try:
            transaction_quality = calculate_transaction_quality_metrics(client_id, start_date, end_date)
            logger.info(f"Transaction quality: {transaction_quality.get('total_trades', 0)} trades")
        except Exception as e:
            logger.error(f"Error calculating transaction quality: {str(e)}", exc_info=True)
            transaction_quality = {'total_trades': 0, 'win_rate': 0, 'profit_factor': 0}
        
        # Calculate enhanced sector analysis
        sector_analysis = calculate_enhanced_sector_analysis(start_map, end_map)

        # Calculate industry-wise performance aggregation (from MTM gains)
        # Pass end_map to check if stocks are still held
        # Pass client_id, start_date, end_date to adjust for new investments
        try:
            industry_performance = calculate_industry_performance(mtm_analysis, end_map, client_id, start_date, end_date)
            logger.info(f"Industry performance calculated: {len(industry_performance.get('industry_performance', []))} industries")
        except Exception as e:
            logger.error(f"Error calculating industry performance: {str(e)}", exc_info=True)
            industry_performance = {'industry_performance': [], 'total_industries': 0, 'total_gains': 0, 'total_losses': 0}

        # Calculate detailed holdings evolution
        try:
            holdings_evolution = calculate_holdings_evolution_detailed(start_map, end_map, txns_in_period)
            logger.info(f"Holdings evolution: {holdings_evolution.get('summary', {}).get('start_count', 0)} start, {holdings_evolution.get('summary', {}).get('end_count', 0)} end")
        except Exception as e:
            logger.error(f"Error calculating holdings evolution: {str(e)}", exc_info=True)
            holdings_evolution = {'summary': {'start_count': 0, 'end_count': 0}, 'new_positions': [], 'exited_positions': []}

        # Calculate concentration risk
        try:
            concentration_risk = calculate_concentration_risk(end_map)
            logger.info(f"Concentration risk calculated: top_5={concentration_risk.get('top_5_weight', 0):.2f}%")
        except Exception as e:
            logger.error(f"Error calculating concentration risk: {str(e)}", exc_info=True)
            concentration_risk = {'top_5_weight': 0, 'top_10_weight': 0, 'herfindahl_index': 0, 'effective_number_of_stocks': 0}
        
        audit.add_text("Value Attribution Analysis completed successfully")
        audit.add_text(f"Market Movement: ₹{value_attribution['market_movement_attribution']:,.2f} ({value_attribution['market_movement_percent']:.1f}%)")
        audit.add_text(f"Portfolio Changes: ₹{value_attribution['portfolio_changes_attribution']:,.2f} ({value_attribution['portfolio_changes_percent']:.1f}%)")
        audit.add_text("")
        
        # ✅ SAVE COMPREHENSIVE AUDIT TRAIL
        audit.add_section("FINAL SUMMARY")
        audit.add_text(f"Analysis completed successfully")
        audit.add_text(f"Total calculations: {audit.step_number} steps")
        audit.add_text("")
        
        # Note: Snapshot creation removed for cleaner performance
        audit.add_step("Analysis Complete")
        audit.add_text("Analysis completed without creating snapshot files")
        
        audit_file = audit.save()
        logger.info(f"Comprehensive audit trail saved to: {audit_file}")
        
        # Initialize warnings list to track approximations and data quality issues
        warnings = []
        
        # Set up custom logging handler to capture all warnings and errors
        logger_instance = logging.getLogger(__name__)
        warning_handler = WarningCaptureHandler(warnings)
        warning_handler.setFormatter(logging.Formatter('%(message)s'))
        
        # Also capture from other relevant loggers
        loggers_to_capture = [
            logger_instance,
            logging.getLogger('api.v1.period_analysis'),
            logging.getLogger('services.forward_holding_calculation_service'),
            logging.getLogger('api.v1.portfolio_reconstruction')
        ]
        
        # Add handler to all relevant loggers
        for log in loggers_to_capture:
            if warning_handler not in log.handlers:
                log.addHandler(warning_handler)
        
        # Compile comprehensive response
        response_data = {
            'client_id': client_id,
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'analysis_type': analysis_type,
                'duration_days': (end_date - start_date).days,
                'duration_months': round((end_date - start_date).days / 30.44, 1)
            },
            
            # Core 6 Analyses
            'mark_to_market_gains': mtm_analysis,
            'trade_gains': trade_analysis,
            'investment_breakdown': investment_breakdown,
            'gains_breakdown': gains_breakdown,
            'best_performers': best_performers,
            'worst_performers': worst_performers,
            
            # Additional Enhanced Analytics
            'period_xirr': period_xirr_data,
            'twr': twr_data,  # Time-Weighted Return
            'period_risk_metrics': period_risk_metrics,
            'benchmark_comparison': period_benchmark,
            'summary_metrics': summary_metrics,
            'holdings_changes': holdings_changes,
            'cashflow_analysis': cashflow_analysis,
            'trade_analytics': trade_analytics,
            'sector_performance': sector_performance,
            
            # ✅ NEW: VALUE ATTRIBUTION ANALYSIS
            'value_attribution': value_attribution,
            'best_worst': wealth_analysis,
            'stocks_most_bought': most_bought,
            'prev_period_most_bought_performance': prev_period_most_bought_perf,
            'stocks_sold': stocks_sold,
            'transaction_quality': transaction_quality,
            'sector_analysis': sector_analysis,
            'industry_performance': industry_performance,
            'holdings_evolution': holdings_evolution,
            'concentration_risk': concentration_risk,
            'mtm_trades_analysis': mtm_trades_analysis
        }
        
        # Calculate pathway breakdown
        pathway_breakdown = None
        try:
            logger.info(f"Calculating pathway breakdown for client {client_id} from {start_date} to {end_date}")
            pathway_breakdown = calculate_pathway_breakdown(
                client_id, start_date, end_date, mtm_analysis, gains_breakdown, value_attribution
            )
            if pathway_breakdown:
                logger.info(f"Pathway breakdown calculated successfully. Start: ₹{pathway_breakdown.get('start_value', 0):,.2f}, End: ₹{pathway_breakdown.get('end_value', 0):,.2f}")
                audit.add_text("Pathway Breakdown calculated successfully")
            else:
                logger.warning("Pathway breakdown calculation returned None")
                audit.add_text("Pathway Breakdown calculation returned None")
        except Exception as e:
            logger.error(f"Could not calculate pathway breakdown: {str(e)}", exc_info=True)
            audit.add_text(f"Pathway Breakdown calculation failed: {str(e)}")
        
        # Log pathway status for debugging
        logger.info(f"Pathway breakdown status: {'PRESENT' if pathway_breakdown else 'MISSING'}")
        if pathway_breakdown:
            logger.info(f"Pathway keys: {list(pathway_breakdown.keys())}")
        
        # Add pathway to response
        if pathway_breakdown:
            response_data['pathway'] = pathway_breakdown
        
        # ✅ NEW: Add segment-wise XIRR
        try:
            logger.info("Calculating segment-wise XIRR for period analysis")
            segment_xirr_data = calculate_segment_wise_xirr(client_id, start_date, end_date)
            response_data['segment_wise_xirr'] = segment_xirr_data
            logger.info(f"Segment-wise XIRR calculated: {len(segment_xirr_data.get('segments', {}))} segments")
        except Exception as e:
            logger.warning(f"Error calculating segment-wise XIRR: {str(e)}")
            response_data['segment_wise_xirr'] = {
                'segments': {},
                'summary': {
                    'total_segments': 0,
                    'total_portfolio_value': 0.0,
                    'weighted_avg_xirr': 0.0
                }
            }
        
        # ✅ NEW: Add complete holdings from portfolio construction API
        # Merge start and end holdings to show all securities from either period
        try:
            # Always fetch portfolio data directly to ensure we have the latest data
            logger.info(f"Fetching portfolio holdings for comparison: client {client_id}, start {start_date}, end {end_date}")
            # Initialize warnings list for this section (will be populated during comparison_holdings building)
            comparison_warnings = []
            start_portfolio_data = get_client_portfolio_by_date(client_id, start_date)
            end_portfolio_data = get_client_portfolio_by_date(client_id, end_date)
            
            start_holdings_list = start_portfolio_data.get('holdings', [])
            end_holdings_list = end_portfolio_data.get('holdings', [])
            
            logger.info(f"Portfolio data fetched - Start: {len(start_holdings_list)} holdings, End: {len(end_holdings_list)} holdings")
            
            if not start_holdings_list and not end_holdings_list:
                logger.warning(f"No holdings found in portfolio construction for client {client_id}")
                response_data['portfolio_holdings_comparison'] = []
            else:
                start_holdings_dict = {h['security_id']: h for h in start_holdings_list}
                end_holdings_dict = {h['security_id']: h for h in end_holdings_list}
                
                # Get all unique security IDs from both periods
                all_security_ids = set(start_holdings_dict.keys()) | set(end_holdings_dict.keys())
                logger.info(f"Found {len(all_security_ids)} unique securities across both periods")
                
                # Build comparison holdings array
                comparison_holdings = []
                start_datetime = datetime.combine(start_date, datetime.min.time())
                end_datetime = datetime.combine(end_date, datetime.max.time())
                total_period_days = (end_date - start_date).days + 1
                
                for security_id in all_security_ids:
                    start_holding = start_holdings_dict.get(security_id)
                    end_holding = end_holdings_dict.get(security_id)
                    
                    # Get symbol from either holding
                    symbol = (start_holding.get('symbol') if start_holding 
                            else end_holding.get('symbol') if end_holding else 'Unknown')
                    
                    # Start period data
                    start_qty = float(start_holding.get('quantity', 0)) if start_holding else 0.0
                    start_avg_price = float(start_holding.get('average_price', 0)) if start_holding else 0.0
                    start_current_price = float(start_holding.get('current_price', 0)) if start_holding else 0.0
                    start_current_value = float(start_holding.get('current_value', 0)) if start_holding else 0.0
                    start_total_cost = float(start_holding.get('total_cost', 0)) if start_holding else 0.0
                    start_unrealized_pnl = start_current_value - start_total_cost
                    start_unrealized_pnl_percent = (start_unrealized_pnl / start_total_cost * 100) if start_total_cost > 0 else 0.0
                    
                    # End period data
                    end_qty = float(end_holding.get('quantity', 0)) if end_holding else 0.0
                    end_avg_price = float(end_holding.get('average_price', 0)) if end_holding else 0.0
                    end_current_price = float(end_holding.get('current_price', 0)) if end_holding else 0.0
                    end_current_value = float(end_holding.get('current_value', 0)) if end_holding else 0.0
                    end_total_cost = float(end_holding.get('total_cost', 0)) if end_holding else 0.0
                    end_unrealized_pnl = end_current_value - end_total_cost
                    end_unrealized_pnl_percent = (end_unrealized_pnl / end_total_cost * 100) if end_total_cost > 0 else 0.0
                    
                    # Calculate holding period and exit date for weighted average
                    exit_date = None
                    effective_end_date = end_date
                    holding_days = total_period_days  # Default to full period
                    exit_value = end_current_value  # Default to end value
                    exit_price_info = None  # Track which price/date was used for exit value
                    
                    # If security exited during period (start_qty > 0, end_qty == 0), find exit date
                    if start_qty > 0 and end_qty == 0:
                        # Check for sell transactions in the period
                        sell_transactions = Transaction.query.filter(
                            Transaction.client_id == client_id,
                            Transaction.security_id == security_id,
                            Transaction.type == 'SELL',
                            Transaction.transaction_date >= start_datetime,
                            Transaction.transaction_date <= end_datetime
                        ).order_by(Transaction.transaction_date.desc()).all()
                        
                        if sell_transactions:
                            # Use last sell date as exit date
                            exit_date = sell_transactions[0].transaction_date.date()
                            effective_end_date = exit_date
                            holding_days = (exit_date - start_date).days + 1
                            # Exit value should be the value at sell date (sum of all sell transactions)
                            exit_value = sum(float(t.quantity) * float(t.price) for t in sell_transactions)
                        else:
                            # Check if security has maturity date in metadata
                            security = Security.query.get(security_id)
                            if security and security.meta_data:
                                try:
                                    import json
                                    meta = json.loads(security.meta_data) if isinstance(security.meta_data, str) else security.meta_data
                                    maturity_date_str = meta.get('maturity_date') or meta.get('maturityDate')
                                    if maturity_date_str:
                                        try:
                                            # Try different date formats
                                            for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%Y']:
                                                try:
                                                    maturity_date = datetime.strptime(str(maturity_date_str), fmt).date()
                                                    if start_date <= maturity_date <= end_date:
                                                        exit_date = maturity_date
                                                        effective_end_date = maturity_date
                                                        holding_days = (maturity_date - start_date).days + 1
                                                        
                                                        # For matured instruments, get the actual maturity value
                                                        # Try to get price at maturity date (this is the maturity value)
                                                        from models import HistoricalPrice
                                                        maturity_price_record = HistoricalPrice.query.filter_by(
                                                            security_id=security_id,
                                                            date=maturity_date
                                                        ).first()
                                                        
                                                        maturity_price = None
                                                        price_date_used = None
                                                        price_source = None
                                                        
                                                        if maturity_price_record and maturity_price_record.close_price:
                                                            # Use price at maturity date
                                                            maturity_price = float(maturity_price_record.close_price)
                                                            price_date_used = maturity_date
                                                            price_source = "maturity_date"
                                                            exit_value = start_qty * maturity_price  # Value at maturity
                                                            logger.info(f"Security {security_id} ({symbol}) matured on {maturity_date}: using EXACT maturity date price ₹{maturity_price} (date: {price_date_used}) for {start_qty} shares = ₹{exit_value:,.2f}")
                                                        else:
                                                            # Fallback: try to get price just before maturity
                                                            from datetime import timedelta
                                                            # First try 1-5 days before (preferred - closer to maturity)
                                                            found_price = False
                                                            for days_back in range(1, 6):  # Try 1-5 days before maturity
                                                                lookup_date = maturity_date - timedelta(days=days_back)
                                                                price_record = HistoricalPrice.query.filter_by(
                                                                    security_id=security_id,
                                                                    date=lookup_date
                                                                ).first()
                                                                if price_record and price_record.close_price:
                                                                    maturity_price = float(price_record.close_price)
                                                                    price_date_used = lookup_date
                                                                    price_source = f"{days_back}_days_before_maturity"
                                                                    exit_value = start_qty * maturity_price
                                                                    logger.info(f"Security {security_id} ({symbol}) matured on {maturity_date}: using price ₹{maturity_price} from {days_back} days BEFORE maturity (date: {price_date_used}) for {start_qty} shares = ₹{exit_value:,.2f}")
                                                                    # Add warning for approximation
                                                                    comparison_warnings.append({
                                                                        'type': 'maturity_price_approximation',
                                                                        'severity': 'info',
                                                                        'security_id': security_id,
                                                                        'symbol': symbol,
                                                                        'message': f"{symbol} matured on {maturity_date}, but price data not available on maturity date. Used price from {price_date_used} ({days_back} days before maturity): ₹{maturity_price}",
                                                                        'maturity_date': maturity_date.isoformat(),
                                                                        'price_date_used': price_date_used.isoformat(),
                                                                        'days_before': days_back,
                                                                        'price_used': maturity_price,
                                                                        'exit_value': exit_value
                                                                    })
                                                                    found_price = True
                                                                    break
                                                            
                                                            # If no price in 1-5 day window, find closest price before maturity
                                                            if not found_price:
                                                                closest_price_record = HistoricalPrice.query.filter(
                                                                    HistoricalPrice.security_id == security_id,
                                                                    HistoricalPrice.date < maturity_date,
                                                                    HistoricalPrice.date >= start_date  # Only use prices within the period
                                                                ).order_by(HistoricalPrice.date.desc()).first()
                                                                
                                                                if closest_price_record and closest_price_record.close_price:
                                                                    maturity_price = float(closest_price_record.close_price)
                                                                    price_date_used = closest_price_record.date
                                                                    days_before = (maturity_date - price_date_used).days
                                                                    price_source = f"closest_before_maturity_{days_before}_days"
                                                                    exit_value = start_qty * maturity_price
                                                                    logger.info(f"Security {security_id} ({symbol}) matured on {maturity_date}: using CLOSEST available price ₹{maturity_price} from {price_date_used} ({days_before} days before maturity) for {start_qty} shares = ₹{exit_value:,.2f}")
                                                                    # Add warning for approximation (further from maturity)
                                                                    comparison_warnings.append({
                                                                        'type': 'maturity_price_approximation',
                                                                        'severity': 'warning',
                                                                        'security_id': security_id,
                                                                        'symbol': symbol,
                                                                        'message': f"{symbol} matured on {maturity_date}, but no price data found within 5 days of maturity. Used closest available price from {price_date_used} ({days_before} days before maturity): ₹{maturity_price}",
                                                                        'maturity_date': maturity_date.isoformat(),
                                                                        'price_date_used': price_date_used.isoformat(),
                                                                        'days_before': days_before,
                                                                        'price_used': maturity_price,
                                                                        'exit_value': exit_value
                                                                    })
                                                                else:
                                                                    # Last fallback: use start_value (assume principal returned at maturity)
                                                                    maturity_price = start_current_price if start_current_price > 0 else 0
                                                                    price_date_used = start_date
                                                                    price_source = "start_value_fallback"
                                                                    exit_value = start_current_value
                                                                    logger.warning(f"Security {security_id} ({symbol}) matured on {maturity_date}: NO price data found in historical_price table (checked maturity date, 1-5 days before, and all dates before maturity). Using start_value ₹{exit_value:,.2f} as exit value (price: ₹{maturity_price}, date: {price_date_used})")
                                                                    # Add warning for fallback
                                                                    comparison_warnings.append({
                                                                        'type': 'maturity_price_fallback',
                                                                        'severity': 'warning',
                                                                        'security_id': security_id,
                                                                        'symbol': symbol,
                                                                        'message': f"{symbol} matured on {maturity_date}, but NO price data found in database. Using start period value ₹{exit_value:,.2f} as approximation. Please add historical price data for accurate calculation.",
                                                                        'maturity_date': maturity_date.isoformat(),
                                                                        'price_date_used': price_date_used.isoformat(),
                                                                        'price_used': maturity_price,
                                                                        'exit_value': exit_value
                                                                    })
                                                        
                                                        # Store price info for debugging
                                                        exit_price_info = {
                                                            'maturity_price': maturity_price,
                                                            'price_date_used': price_date_used.isoformat() if price_date_used else None,
                                                            'price_source': price_source,
                                                            'maturity_date': maturity_date.isoformat()
                                                        }
                                                        break
                                                except ValueError:
                                                    continue
                                        except Exception as e:
                                            logger.debug(f"Error parsing maturity date for security {security_id}: {e}")
                                except Exception as e:
                                    logger.debug(f"Error parsing meta_data for security {security_id}: {e}")
                    
                    # ------------------------------------------------------------------
                    # Corporate-action-adjusted price return
                    #
                    # start_current_price/end_current_price are UNADJUSTED prices from the DB.
                    # When a split/bonus occurs during the period, the raw price return can look
                    # negative even if the corporate-action-adjusted return is positive/flat.
                    #
                    # Provide adjusted start/end prices and adjusted/unadjusted return % so the UI
                    # can display consistent “adjusted return” in Security-wise section.
                    # ------------------------------------------------------------------
                    try:
                        from services.price_service import PriceService
                        start_price_adj_data = PriceService.get_price(
                            security_id, start_date, use_adjusted=True, allow_fallback=True
                        )
                        end_price_adj_data = PriceService.get_price(
                            security_id, effective_end_date, use_adjusted=True, allow_fallback=True
                        )
                        start_price_adjusted = float(start_price_adj_data.price) if start_price_adj_data and start_price_adj_data.is_valid and start_price_adj_data.price else 0.0
                        end_price_adjusted = float(end_price_adj_data.price) if end_price_adj_data and end_price_adj_data.is_valid and end_price_adj_data.price else 0.0
                    except Exception:
                        start_price_adjusted = 0.0
                        end_price_adjusted = 0.0

                    price_return_percent_unadjusted = ((end_current_price - start_current_price) / start_current_price * 100) if start_current_price > 0 else 0.0
                    price_return_percent_adjusted = ((end_price_adjusted - start_price_adjusted) / start_price_adjusted * 100) if start_price_adjusted > 0 else 0.0

                    # Calculate weighted average daily value
                    # Weighted avg = ((start_value + exit_value) / 2) * (holding_days / total_period_days)
                    # This accounts for securities held for only part of the period
                    weight_factor = holding_days / total_period_days if total_period_days > 0 else 1.0
                    weighted_avg_value = ((start_current_value + exit_value) / 2) * weight_factor
                    
                    # For matured/exited securities, calculate change in unrealized P&L properly
                    # If security exited/matured, use exit_value to calculate realized P&L
                    change_unrealized_pnl = end_unrealized_pnl - start_unrealized_pnl
                    if exit_date and exit_value > 0:
                        # For exited/matured securities, calculate change as: (exit_value - start_total_cost) - (start_current_value - start_total_cost)
                        # Which simplifies to: exit_value - start_current_value
                        # But more accurately: realized gain from exit = exit_value - start_total_cost, unrealized was start_current_value - start_total_cost
                        # So change = realized_gain - unrealized_at_start = (exit_value - start_total_cost) - (start_current_value - start_total_cost) = exit_value - start_current_value
                        change_unrealized_pnl = exit_value - start_current_value
                        logger.debug(f"Security {security_id} ({symbol}) exited on {exit_date}: change_unrealized_pnl = exit_value ({exit_value:,.2f}) - start_current_value ({start_current_value:,.2f}) = {change_unrealized_pnl:,.2f}")
                    
                    comparison_holdings.append({
                        'security_id': security_id,
                        'symbol': symbol,
                        'holding_days': holding_days,
                        'total_period_days': total_period_days,
                        'weight_factor': weight_factor,
                        'exit_date': exit_date.isoformat() if exit_date else None,
                        'effective_end_date': effective_end_date.isoformat() if exit_date else end_date.isoformat(),
                        'weighted_avg_value': weighted_avg_value,
                        'exit_value': exit_value,
                        'exit_price_info': exit_price_info,
                        'start': {
                            'quantity': start_qty,
                            'average_price': start_avg_price,
                            'current_price': start_current_price,
                            'price_adjusted': start_price_adjusted,
                            'current_value': start_current_value,
                            'total_cost': start_total_cost,
                            'unrealized_pnl': start_unrealized_pnl,
                            'unrealized_pnl_percent': start_unrealized_pnl_percent
                        },
                        'end': {
                            'quantity': end_qty,
                            'average_price': end_avg_price,
                            'current_price': end_current_price,
                            'price_adjusted': end_price_adjusted,
                            'current_value': end_current_value,
                            'total_cost': end_total_cost,
                            'unrealized_pnl': end_unrealized_pnl,
                            'unrealized_pnl_percent': end_unrealized_pnl_percent,
                            'effective_end_date': effective_end_date.isoformat() if exit_date else None,
                            'exit_value': exit_value
                        },
                        'change': {
                            'quantity': end_qty - start_qty,
                            'current_value': end_current_value - start_current_value if not exit_date else (exit_value - start_current_value),
                            'unrealized_pnl': change_unrealized_pnl,  # Use corrected calculation for exited securities
                            'current_price': end_current_price - start_current_price,
                            'price_return_percent_unadjusted': price_return_percent_unadjusted,
                            'price_return_percent_adjusted': price_return_percent_adjusted
                        },
                        'price_return_percent_unadjusted': price_return_percent_unadjusted,
                        'price_return_percent_adjusted': price_return_percent_adjusted
                    })
                
                # Sort by symbol
                comparison_holdings.sort(key=lambda x: x['symbol'].upper())
                
                # Add to response
                response_data['portfolio_holdings_comparison'] = comparison_holdings
                # Merge comparison warnings into main warnings list
                warnings.extend(comparison_warnings)
                logger.info(f"Successfully added {len(comparison_holdings)} holdings to comparison data")
                if len(comparison_holdings) == 0:
                    logger.warning(f"No holdings found in comparison. Start holdings: {len(start_holdings_list)}, End holdings: {len(end_holdings_list)}")
        except Exception as e:
            logger.error(f"Error building holdings comparison: {str(e)}", exc_info=True)
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            response_data['portfolio_holdings_comparison'] = []
        
        # ✅ Generate AI text summaries for this period (LOCAL ONLY - privacy safe)
        try:
            from flask import current_app
            if not current_app.config.get('ENABLE_AI_SERVICES', False):
                response_data['ai_text_summaries'] = {
                    'disabled': True,
                    'message': 'AI summaries disabled (set ENABLE_AI_SERVICES=true to enable).',
                    'generated_at': datetime.utcnow().isoformat(),
                }
                logger.info("Skipping AI text summaries (ENABLE_AI_SERVICES is off)")
            else:
                logger.info(f"Generating AI text summaries for period {start_date} to {end_date}")
                from services.ai_insights_service import AIInsightsService
                
                # Get key metrics from summary_metrics
                key_metrics = summary_metrics.get('key_metrics', {})
                
                # Prepare comprehensive data for AI summary (similar to enhanced review format)
                period_review_data = {
                    'client_id': client_id,
                    'client_name': client.name,
                    'start_date': start_date.isoformat() if isinstance(start_date, date) else str(start_date),
                    'end_date': end_date.isoformat() if isinstance(end_date, date) else str(end_date),
                    'sections': {
                        'performance': {
                            'total_return_percent': summary_metrics.get('total_returns', 0),
                            'annualized_return': summary_metrics.get('annualized_return', 0),
                            'xirr': key_metrics.get('xirr_percent', 0),
                            'start_value': float(start_value),
                            'end_value': float(current_value),
                            'total_return': float(current_value - start_value),
                            'benchmark_return': period_benchmark.get('benchmark_return', 0) if period_benchmark else 0,
                            'excess_return': period_benchmark.get('excess_return', 0) if period_benchmark else 0,
                            'risk_metrics': period_risk_metrics or {}
                        }
                    },
                    'analysis_classification': {
                        'performance': {
                            'positive_signs': [],
                            'negative_signs': [],
                            'total_positive': 0,
                            'total_negative': 0
                        }
                    }
                }
                
                # Enhanced classification with more indicators
                total_return = summary_metrics.get('total_returns', 0)
                xirr = key_metrics.get('xirr_percent', 0)
                excess_return = period_benchmark.get('excess_return', 0) if period_benchmark else 0
                
                # Returns classification
                if total_return > 0:
                    period_review_data['analysis_classification']['performance']['positive_signs'].append({
                        'indicator': 'Positive Returns',
                        'value': f'{total_return:.2f}%',
                        'description': f'Portfolio generated positive returns of {total_return:.2f}% during this period'
                    })
                    period_review_data['analysis_classification']['performance']['total_positive'] += 1
                else:
                    period_review_data['analysis_classification']['performance']['negative_signs'].append({
                        'indicator': 'Negative Returns',
                        'value': f'{total_return:.2f}%',
                        'description': f'Portfolio experienced negative returns of {abs(total_return):.2f}% during this period'
                    })
                    period_review_data['analysis_classification']['performance']['total_negative'] += 1
                
                # XIRR classification
                if xirr > 10:
                    period_review_data['analysis_classification']['performance']['positive_signs'].append({
                        'indicator': 'Strong XIRR',
                        'value': f'{xirr:.2f}%',
                        'description': f'Excellent annualized return of {xirr:.2f}%'
                    })
                    period_review_data['analysis_classification']['performance']['total_positive'] += 1
                elif xirr < 0:
                    period_review_data['analysis_classification']['performance']['negative_signs'].append({
                        'indicator': 'Negative XIRR',
                        'value': f'{xirr:.2f}%',
                        'description': f'Negative annualized return of {xirr:.2f}%'
                    })
                    period_review_data['analysis_classification']['performance']['total_negative'] += 1
                
                # Benchmark comparison
                if excess_return > 5:
                    period_review_data['analysis_classification']['performance']['positive_signs'].append({
                        'indicator': 'Outperformed Benchmark',
                        'value': f'+{excess_return:.2f}%',
                        'description': f'Significantly outperformed benchmark by {excess_return:.2f}%'
                    })
                    period_review_data['analysis_classification']['performance']['total_positive'] += 1
                elif excess_return < -5:
                    period_review_data['analysis_classification']['performance']['negative_signs'].append({
                        'indicator': 'Underperformed Benchmark',
                        'value': f'{excess_return:.2f}%',
                        'description': f'Significantly underperformed benchmark by {abs(excess_return):.2f}%'
                    })
                    period_review_data['analysis_classification']['performance']['total_negative'] += 1
                
                # Risk metrics classification
                if period_risk_metrics:
                    sharpe = period_risk_metrics.get('sharpe_ratio', 0)
                    volatility = period_risk_metrics.get('volatility', 0)
                    
                    if sharpe > 1.0:
                        period_review_data['analysis_classification']['performance']['positive_signs'].append({
                            'indicator': 'Good Risk-Adjusted Returns',
                            'value': f'Sharpe: {sharpe:.2f}',
                            'description': f'Excellent risk-adjusted returns with Sharpe ratio of {sharpe:.2f}'
                        })
                        period_review_data['analysis_classification']['performance']['total_positive'] += 1
                    elif sharpe < 0:
                        period_review_data['analysis_classification']['performance']['negative_signs'].append({
                            'indicator': 'Poor Risk-Adjusted Returns',
                            'value': f'Sharpe: {sharpe:.2f}',
                            'description': f'Negative Sharpe ratio indicates poor risk-adjusted returns'
                        })
                        period_review_data['analysis_classification']['performance']['total_negative'] += 1
                    
                    if volatility > 25:
                        period_review_data['analysis_classification']['performance']['negative_signs'].append({
                            'indicator': 'High Volatility',
                            'value': f'{volatility:.2f}%',
                            'description': f'High portfolio volatility of {volatility:.2f}% may indicate risk concerns'
                        })
                        period_review_data['analysis_classification']['performance']['total_negative'] += 1
                
                # Generate AI summary for this period
                logger.info(f"Calling AI summary generation with {period_review_data['analysis_classification']['performance']['total_positive']} positive and {period_review_data['analysis_classification']['performance']['total_negative']} negative indicators")
                ai_summary = AIInsightsService.generate_ai_text_summary(period_review_data)
                
                # ✅ Generate detailed table-by-table analysis
                logger.info("Generating detailed table-by-table analysis for period analysis")
                try:
                    # Determine period name - check analysis_type first, then calculate from dates
                    period_name = None
                    days_diff = (end_date - start_date).days
                    
                    if analysis_type == '6M' or (180 <= days_diff <= 190):
                        period_name = "Last 6 Months"
                    elif analysis_type in ['12M', '1Y'] or (360 <= days_diff <= 370):
                        period_name = "Last 1 Year"
                    elif analysis_type in ['ALL', 'LIFETIME', 'LIFE', 'FULL']:
                        period_name = "Lifetime"
                    else:
                        # Custom period - use descriptive name based on duration
                        months = round(days_diff / 30.44, 1)
                        if months < 1:
                            period_name = f"Custom Period ({days_diff} days)"
                        elif months < 12:
                            period_name = f"Custom Period ({int(months)} months)"
                        else:
                            years = round(months / 12, 1)
                            period_name = f"Custom Period ({years} years)"
                    
                    # Fallback if period_name still not set
                    if not period_name:
                        period_name = f"{start_date} to {end_date}"
                    
                    logger.info(f"Generating detailed table analysis for period: {period_name} (days: {days_diff})")
                    logger.info(f"Response data keys available: {list(response_data.keys())[:10]}...")
                    
                    # Generate detailed analysis
                    detailed_analysis = AIInsightsService.generate_detailed_table_analysis(
                        response_data, period_name
                    )
                    
                    analyses_dict = detailed_analysis.get('analyses', {})
                    ai_summary['detailed_table_analyses'] = analyses_dict
                    
                    logger.info(f"✅ Detailed table analysis generated successfully!")
                    logger.info(f"   - Sections analyzed: {len(analyses_dict)}")
                    logger.info(f"   - Analysis keys: {list(analyses_dict.keys())}")
                    for key, value in analyses_dict.items():
                        if isinstance(value, str):
                            logger.info(f"   - {key}: {len(value)} characters")
                        else:
                            logger.info(f"   - {key}: {type(value)}")
                except Exception as e:
                    logger.error(f"Error generating detailed table analysis: {str(e)}", exc_info=True)
                    import traceback
                    logger.error(f"Detailed table analysis traceback: {traceback.format_exc()}")
                    ai_summary['detailed_table_analyses'] = {}
                    # Don't fail the entire request if detailed analysis fails
                
                response_data['ai_text_summaries'] = ai_summary
                logger.info("AI text summaries and detailed analyses generated successfully for period")
            
        except Exception as e:
            logger.error(f"Error generating AI text summaries for period: {str(e)}", exc_info=True)
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Continue without AI summaries if generation fails
            response_data['ai_text_summaries'] = {
                'error': 'AI summary generation failed',
                'fallback': True,
                'generated_at': datetime.utcnow().isoformat()
            }
        
        # Add warnings to response if any
        if warnings:
            response_data['warnings'] = warnings
            response_data['has_warnings'] = True
            logger.info(f"Period analysis completed with {len(warnings)} warnings/approximations")
        else:
            response_data['warnings'] = []
            response_data['has_warnings'] = False
        
        return APIResponse.success(
            data=response_data,
            message=f"Period analysis for client {client_id} ({start_date} to {end_date})"
        )
        
    except BrokenPipeError as e:
        # Client disconnected before response was fully sent
        logger.warning(f"Client disconnected during period analysis for client {client_id} ({client.name}): Broken pipe")
        # Don't raise the exception - the client already disconnected
        # Return empty response (won't reach client anyway)
        return APIResponse.error(
            message="Client disconnected before response could be sent",
            status_code=499  # Client Closed Request
        )
    except ConnectionError as e:
        # Connection issues
        logger.warning(f"Connection error during period analysis for client {client_id} ({client.name}): {str(e)}")
        return APIResponse.error(
            message="Connection error occurred",
            status_code=503
        )
    except Exception as e:
        logger.error(f"Error in period analysis for client {client_id}: {str(e)}")
        raise
    finally:
        # Remove custom handler to avoid memory leaks
        for log in loggers_to_capture:
            if warning_handler in log.handlers:
                log.removeHandler(warning_handler)

@period_analysis_bp.route('/<int:client_id>/segment-xirr', methods=['GET'])
@audit_api_call
def get_segment_wise_xirr(client_id):
    """
    Get XIRR (Extended Internal Rate of Return) for each asset class segment
    
    Query Parameters:
        - start_date (string, required): Period start date (YYYY-MM-DD)
        - end_date (string, required): Period end date (YYYY-MM-DD)
    
    Returns:
        {
            "success": true,
            "data": {
                "client_id": 1,
                "start_date": "2023-01-01",
                "end_date": "2024-01-01",
                "segments": {
                    "Equity": {
                        "asset_class": "Equity",
                        "xirr": 0.125,
                        "xirr_percent": 12.5,
                        "total_invested": 100000.0,
                        "total_withdrawn": 0.0,
                        "net_investment": 100000.0,
                        "current_value": 120000.0,
                        "absolute_return": 20000.0,
                        "absolute_return_percent": 20.0,
                        "transaction_count": 15
                    },
                    "Debt": {
                        ...
                    }
                },
                "summary": {
                    "total_segments": 2,
                    "total_portfolio_value": 150000.0,
                    "weighted_avg_xirr": 11.2
                }
            }
        }
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get and validate parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        
        if not start_date_str:
            raise ValidationError("start_date is required")
        if not end_date_str:
            raise ValidationError("end_date is required")
        
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError as e:
            raise ValidationError(f"Invalid date format. Use YYYY-MM-DD: {str(e)}")
        
        if start_date >= end_date:
            raise ValidationError("start_date must be before end_date")
        
        logger.info(f"Calculating segment-wise XIRR for client {client_id} from {start_date} to {end_date}")
        
        # Calculate segment-wise XIRR
        segment_data = calculate_segment_wise_xirr(client_id, start_date, end_date)
        
        # Prepare response
        response_data = {
            'client_id': client_id,
            'client_name': client.name,
            'start_date': start_date_str,
            'end_date': end_date_str,
            'segments': segment_data['segments'],
            'summary': segment_data['summary']
        }
        
        return APIResponse.success(
            data=response_data,
            message=f"Segment-wise XIRR calculated for {len(segment_data['segments'])} asset classes"
        )
        
    except ValidationError as e:
        logger.error(f"Validation error in segment XIRR API: {str(e)}")
        return APIResponse.error(message=str(e), status_code=400)
    except Exception as e:
        logger.error(f"Error calculating segment-wise XIRR for client {client_id}: {str(e)}", exc_info=True)
        import traceback
        error_trace = traceback.format_exc()
        logger.error(f"Full traceback for segment XIRR error:\n{error_trace}")
        return APIResponse.error(
            message=f"Error calculating segment-wise XIRR: {str(e)}",
            status_code=500
        )

# ============================================================================
# HELPER FUNCTIONS FOR PERIOD ANALYSIS
# ============================================================================

def _fifo_realized_gains_by_security(client_id, start_date, end_date):
    """
    FIFO realized P&L by security_id for all SELLs in [start_date, end_date].
    Single pass over period transactions (P4); matches calculate_realized_gain_from_transactions.
    """
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    txns = (
        Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date >= start_datetime,
            Transaction.transaction_date <= end_datetime,
        )
        .order_by(Transaction.transaction_date, Transaction.id)
        .all()
    )

    buy_queues = {}
    realized = {}

    for txn in txns:
        sid = txn.security_id
        qty = float(txn.quantity)
        price = float(txn.price)
        if txn.type == "BUY":
            buy_queues.setdefault(sid, []).append([qty, price])
        elif txn.type == "SELL":
            remaining = qty
            while remaining > 0 and buy_queues.get(sid):
                lot_qty, lot_price = buy_queues[sid][0]
                matched = min(remaining, lot_qty)
                realized[sid] = realized.get(sid, 0.0) + (price - lot_price) * matched
                remaining -= matched
                if matched >= lot_qty:
                    buy_queues[sid].pop(0)
                else:
                    buy_queues[sid][0][0] = lot_qty - matched

    return realized


def _empty_holding_dict(security_id, symbol="Unknown"):
    return {
        "security_id": security_id,
        "symbol": symbol,
        "quantity": 0.0,
        "average_price": 0.0,
        "current_price": 0.0,
        "current_value": 0.0,
    }


@audit_api_call
def calculate_mark_to_market_gains(
    client_id,
    start_date,
    end_date,
    current_holdings=None,
    start_portfolio=None,
    end_portfolio=None,
):
    """
    1. Calculate mark-to-market gains from portfolio N months before
    
    Uses Forward Calculation Service to get accurate historical portfolio states.
    When start_portfolio/end_portfolio are supplied (Period Analysis v2 collector),
    skips duplicate forward reconstruction.
    """
    logger.debug(
        "Calculating MTM gains for period %s to %s (client %s)",
        start_date,
        end_date,
        client_id,
    )
    
    try:
        if start_portfolio is None:
            logger.debug("MTM: fetching start portfolio for client %s at %s", client_id, start_date)
            start_portfolio = get_client_portfolio_by_date(client_id, start_date)
        if end_portfolio is None:
            logger.debug("MTM: fetching end portfolio for client %s at %s", client_id, end_date)
            end_portfolio = get_client_portfolio_by_date(client_id, end_date)

        start_value = float(start_portfolio.get('total_value', 0.0) or 0.0)
        end_value = float(end_portfolio.get('total_value', 0.0) or 0.0)
        logger.debug(
            "MTM portfolios: start ₹%s (%s holdings), end ₹%s (%s holdings)",
            f"{start_value:,.2f}",
            len(start_portfolio.get('holdings', [])),
            f"{end_value:,.2f}",
            len(end_portfolio.get('holdings', [])),
        )
        
        start_state = {
            'holdings': start_portfolio.get('holdings') or [],
            'total_value': start_portfolio.get('total_value', 0.0),
            'total_cost': start_portfolio.get('total_cost', 0.0),
            'source': 'forward_calculation' if start_portfolio else 'error',
        }
        
        end_state = {
            'holdings': end_portfolio.get('holdings') or [],
            'total_value': end_portfolio.get('total_value', 0.0),
            'total_cost': end_portfolio.get('total_cost', 0.0),
            'source': 'forward_calculation' if end_portfolio else 'error',
        }
        
        total_mtm_gain = end_state['total_value'] - start_state['total_value']
        total_mtm_gain_percent = (total_mtm_gain / start_state['total_value'] * 100) if start_state['total_value'] > 0 else 0
        
        mtm_gains_by_stock = []
        start_holdings_dict = {h['security_id']: h for h in start_state['holdings']}
        end_holdings_dict = {h['security_id']: h for h in end_state['holdings']}
        realized_by_security = _fifo_realized_gains_by_security(client_id, start_date, end_date)

        # B1: include securities held at start OR end (new buys, full exits)
        all_security_ids = set(start_holdings_dict.keys()) | set(end_holdings_dict.keys())
        
        for security_id in all_security_ids:
            start_holding = start_holdings_dict.get(security_id) or _empty_holding_dict(
                security_id,
                (end_holdings_dict.get(security_id) or {}).get('symbol', 'Unknown'),
            )
            end_holding = end_holdings_dict.get(security_id) or _empty_holding_dict(
                security_id, start_holding.get('symbol', 'Unknown')
            )

            start_value_h = float(start_holding.get('current_value') or 0.0)
            end_value_h = float(end_holding.get('current_value') or 0.0)
            end_cost = float(end_holding.get('quantity', 0) or 0) * float(
                end_holding.get('average_price', 0) or 0
            )
            unrealized_gain = end_value_h - end_cost if end_value_h else 0.0
            realized_gain = float(realized_by_security.get(security_id, 0.0))
            total_profit_loss = realized_gain + unrealized_gain
            value_change = end_value_h - start_value_h
            
            mtm_gains_by_stock.append({
                'security_id': security_id,
                'symbol': start_holding.get('symbol') or end_holding.get('symbol', 'Unknown'),
                'start_value': start_value_h,
                'end_value': end_value_h,
                'value_change': value_change,
                'value_change_percent': (value_change / start_value_h * 100) if start_value_h > 0 else 0,
                'realized_gain': realized_gain,
                'unrealized_gain': unrealized_gain,
                'total_profit_loss': total_profit_loss,
                'gain': value_change,
                'gain_percent': (value_change / start_value_h * 100) if start_value_h > 0 else 0,
                'start_quantity': start_holding.get('quantity', 0),
                'start_price': start_holding.get('current_price', 0),
                'end_quantity': end_holding.get('quantity', 0),
                'end_price': end_holding.get('current_price', 0),
            })
        
        return {
            'total_start_value': start_state['total_value'],
            'total_end_value': end_state['total_value'],
            'total_mtm_gain': total_mtm_gain,
            'total_mtm_gain_percent': total_mtm_gain_percent,
            'total_current_value': end_state['total_value'],
            'start_holdings_count': len(start_state['holdings']),
            'end_holdings_count': len(end_state['holdings']),
            'reconstruction_source_start': start_state['source'],
            'reconstruction_source_end': end_state['source'],
            'mtm_gains_by_stock': mtm_gains_by_stock
        }
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        logger.error(f"Error calculating MTM gains using reconstruction service for client {client_id}, period {start_date} to {end_date}: {str(e)}")
        logger.error(f"Full traceback:\n{error_trace}")
        # Return empty result on error
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
            'error': str(e),
            'error_traceback': error_trace
        }

def calculate_trade_gains_in_period(client_id, start_date, end_date):
    """
    2. Calculate realized gains from trades done in the period
    
    Logic:
    - Find all SELL transactions in the period
    - For each SELL, find matching BUY transactions
    - Calculate realized P&L = (Sell Price - Avg Buy Price) * Quantity
    """
    logger.info(f"Calculating trade gains for period {start_date} to {end_date}")
    
    # Get all transactions in period
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    sell_transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'SELL',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    total_realized_gain = 0
    total_sell_value = 0
    realized_gains_by_stock = []
    aggregated_gains = {}
    
    for sell_txn in sell_transactions:
        # Find all BUY transactions for this security before or at sell date
        buy_txns = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.security_id == sell_txn.security_id,
            Transaction.type == 'BUY',
            Transaction.transaction_date <= sell_txn.transaction_date
        ).order_by(Transaction.transaction_date).all()
        
        if buy_txns:
            # Calculate weighted average buy price
            total_buy_cost = sum(float(b.quantity) * float(b.price) for b in buy_txns)
            total_buy_qty = sum(float(b.quantity) for b in buy_txns)
            avg_buy_price = total_buy_cost / total_buy_qty if total_buy_qty > 0 else 0
        else:
            avg_buy_price = 0
        
        sell_price = float(sell_txn.price)
        quantity_sold = float(sell_txn.quantity)
        sell_value = sell_price * quantity_sold
        
        realized_gain = (sell_price - avg_buy_price) * quantity_sold
        realized_gain_percent = ((sell_price - avg_buy_price) / avg_buy_price * 100) if avg_buy_price > 0 else 0
        
        total_realized_gain += realized_gain
        total_sell_value += sell_value
        
        # Aggregate by stock
        key = sell_txn.security_id
        if key not in aggregated_gains:
            aggregated_gains[key] = {
                'symbol': sell_txn.security.symbol if sell_txn.security else 'Unknown',
                'name': sell_txn.security.name if sell_txn.security else 'Unknown',
                'total_quantity_sold': 0.0,
                'weighted_buy_cost': 0.0,
                'weighted_sell_value': 0.0,
                'total_sell_value': 0.0,
                'total_realized_gain': 0.0,
                'trades': 0,
                'last_sell_date': sell_txn.transaction_date.date().isoformat()
            }
        
        entry = aggregated_gains[key]
        entry['total_quantity_sold'] += quantity_sold
        entry['weighted_buy_cost'] += avg_buy_price * quantity_sold
        entry['weighted_sell_value'] += sell_price * quantity_sold
        entry['total_sell_value'] += sell_value
        entry['total_realized_gain'] += realized_gain
        entry['trades'] += 1
        entry['last_sell_date'] = max(entry['last_sell_date'], sell_txn.transaction_date.date().isoformat())
    
    # Build aggregated list with template-friendly keys
    for entry in aggregated_gains.values():
        total_qty = entry['total_quantity_sold']
        average_buy_price = entry['weighted_buy_cost'] / total_qty if total_qty > 0 else 0
        average_sell_price = entry['weighted_sell_value'] / total_qty if total_qty > 0 else 0
        gain_percent = ((average_sell_price - average_buy_price) / average_buy_price * 100) if average_buy_price > 0 else 0
        
        realized_gains_by_stock.append({
            'symbol': entry['symbol'],
            'name': entry['name'],
            'sell_date': entry['last_sell_date'],
            'quantity_sold': total_qty,
            'avg_buy_price': average_buy_price,
            'average_buy_price': average_buy_price,  # UI-friendly alias
            'sell_price': average_sell_price,
            'average_sell_price': average_sell_price,  # UI-friendly alias
            'sell_value': entry['total_sell_value'],
            'realized_gain': entry['total_realized_gain'],
            'realized_gain_percent': gain_percent,
            'gain_percent': gain_percent,  # UI-friendly alias
            'number_of_trades': entry['trades']
        })
    
    # Sort by realized gain
    realized_gains_by_stock.sort(key=lambda x: x['realized_gain'], reverse=True)
    
    return {
        'total_realized_gain': total_realized_gain,
        'total_sell_value': total_sell_value,
        'average_gain_percent': (total_realized_gain / total_sell_value * 100) if total_sell_value > 0 else 0,
        'number_of_trades': len(sell_transactions),
        'realized_gains_by_stock': realized_gains_by_stock
    }

def reconcile_cashflow_vs_trades(client_id, start_date, end_date):
    """
    Reconcile cashflow data with trade data to detect mismatches
    
    Returns:
        dict with reconciliation results and any warnings
    """
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get cashflows for period
    cashflows = Cashflow.query.filter(
        Cashflow.client_id == client_id,
        Cashflow.date >= start_datetime,
        Cashflow.date <= end_datetime
    ).all()
    
    # Calculate net from cashflows (negative = investment, positive = withdrawal)
    cashflow_inflows = sum(abs(float(cf.amount)) for cf in cashflows if float(cf.amount) < 0)  # Investments
    cashflow_outflows = sum(float(cf.amount) for cf in cashflows if float(cf.amount) > 0)  # Withdrawals
    cashflow_net_investment = cashflow_inflows - cashflow_outflows
    
    # Get transactions for period
    buy_transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'BUY',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    sell_transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'SELL',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    # Calculate from trades
    trade_investments = sum(float(tx.quantity) * float(tx.price) for tx in buy_transactions)
    trade_withdrawals = sum(float(tx.quantity) * float(tx.price) for tx in sell_transactions)
    trade_net_investment = trade_investments - trade_withdrawals
    
    # Calculate difference
    difference = abs(cashflow_net_investment - trade_net_investment)
    tolerance = 100.0  # ₹100 tolerance for rounding differences
    has_mismatch = difference > tolerance
    
    reconciliation = {
        'cashflow_data': {
            'total_inflows': round(cashflow_inflows, 2),
            'total_outflows': round(cashflow_outflows, 2),
            'net_investment': round(cashflow_net_investment, 2),
            'cashflow_count': len(cashflows)
        },
        'trade_data': {
            'total_investments': round(trade_investments, 2),
            'total_withdrawals': round(trade_withdrawals, 2),
            'net_investment': round(trade_net_investment, 2),
            'buy_count': len(buy_transactions),
            'sell_count': len(sell_transactions)
        },
        'reconciliation': {
            'difference': round(difference, 2),
            'difference_percent': round((difference / max(cashflow_net_investment, trade_net_investment, 1) * 100), 2) if max(cashflow_net_investment, trade_net_investment) > 0 else 0,
            'has_mismatch': has_mismatch,
            'is_reconciled': not has_mismatch
        }
    }
    
    # Log warning if mismatch detected
    if has_mismatch:
        warning_msg = (
            f"⚠️ CASHFLOW-TRADE MISMATCH DETECTED for Client {client_id} | "
            f"Period: {start_date} to {end_date} | "
            f"Cashflow Net: ₹{cashflow_net_investment:,.2f} | "
            f"Trade Net: ₹{trade_net_investment:,.2f} | "
            f"Difference: ₹{difference:,.2f} ({reconciliation['reconciliation']['difference_percent']:.2f}%) | "
            f"Action Required: Verify cashflow entries match trade data"
        )
        logger.warning(warning_msg)
        reconciliation['warning'] = warning_msg
    else:
        logger.info(f"✓ Cashflow-Trade reconciliation passed for Client {client_id}: Difference ₹{difference:.2f} (within tolerance)")
    
    return reconciliation

def calculate_investment_breakdown(client_id, start_date, end_date):
    """
    3. Calculate investment breakdown by asset class during the period
    
    Logic:
    - Find all BUY transactions in the period
    - Group by asset class (from security metadata)
    - Sum investment amounts by asset class
    - Reconcile with cashflow data and log warnings if mismatch
    """
    logger.info(f"Calculating investment breakdown for period {start_date} to {end_date}")
    
    # First, reconcile cashflow vs trades
    reconciliation = reconcile_cashflow_vs_trades(client_id, start_date, end_date)
    
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    buy_transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'BUY',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    # Group by asset class
    asset_class_breakdown = {}
    total_investment = 0
    
    for txn in buy_transactions:
        amount = float(txn.quantity) * float(txn.price)
        total_investment += amount
        
        # Get asset class from security
        asset_class = 'Unknown'
        if txn.security:
            # Try to get from meta_data
            if txn.security.meta_data:
                try:
                    import json
                    meta = json.loads(txn.security.meta_data) if isinstance(txn.security.meta_data, str) else txn.security.meta_data
                    asset_class = meta.get('asset_class', meta.get('security_type', 'Unknown'))
                except:
                    pass
            
            # Fallback to security_type or sector
            if asset_class == 'Unknown':
                asset_class = getattr(txn.security, 'security_type', getattr(txn.security, 'sector', 'Unknown'))
        
        if asset_class not in asset_class_breakdown:
            asset_class_breakdown[asset_class] = {
                'asset_class': asset_class,
                'total_investment': 0,
                'transaction_count': 0,
                'securities': set()
            }
        
        asset_class_breakdown[asset_class]['total_investment'] += amount
        asset_class_breakdown[asset_class]['transaction_count'] += 1
        if txn.security:
            asset_class_breakdown[asset_class]['securities'].add(txn.security.symbol)
    
    # ✅ FIXED: Also calculate SELL transactions for net investment
    sell_transactions_in_period = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'SELL',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    total_withdrawals = sum(float(txn.quantity) * float(txn.price) for txn in sell_transactions_in_period)
    net_investment = total_investment - total_withdrawals
    
    # Convert to list and calculate percentages
    breakdown_list = []
    for ac_data in asset_class_breakdown.values():
        breakdown_list.append({
            'asset_class': ac_data['asset_class'],
            'total_investment': ac_data['total_investment'],
            'percentage': (ac_data['total_investment'] / total_investment * 100) if total_investment > 0 else 0,
            'transaction_count': ac_data['transaction_count'],
            'unique_securities': len(ac_data['securities'])
        })
    
    # Sort by investment amount
    breakdown_list.sort(key=lambda x: x['total_investment'], reverse=True)
    
    result = {
        'gross_investment_in_period': total_investment,  # Gross BUY amount
        'gross_withdrawal_in_period': total_withdrawals,  # Gross SELL amount
        'net_investment_in_period': net_investment,  # ✅ NET (BUY - SELL)
        'total_investment_in_period': net_investment,  # Alias for backward compatibility
        'total_buy_transactions': len(buy_transactions),
        'total_sell_transactions': len(sell_transactions_in_period),
        'total_transactions': len(buy_transactions) + len(sell_transactions_in_period),
        'asset_class_breakdown': breakdown_list,
        'cashflow_reconciliation': reconciliation
    }
    
    # Add warning to result if mismatch detected
    if reconciliation['reconciliation']['has_mismatch']:
        result['reconciliation_warning'] = reconciliation.get('warning')
    
    return result

def calculate_gains_breakdown(client_id, start_date, end_date, current_holdings):
    """
    4. Calculate portfolio gains breakdown: Client additions vs Unrealized profits
    
    Logic:
    - Total portfolio value increase = (Current Value - Value at Start)
    - Client additions = Sum of all BUY transactions in period
    - Unrealized profit contribution = Total increase - Client additions - Realized gains
    
    ✅ FIXED: Now uses Portfolio Reconstruction Service to get actual market value at start
    with proper split/bonus adjustments instead of using cost basis
    """
    logger.info(f"Calculating gains breakdown for period {start_date} to {end_date}")
    
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # ✅ UPDATED: Use Forward Calculation Service to get actual market value at start AND end
    try:
        start_portfolio = get_client_portfolio_by_date(client_id, start_date)
        value_at_start = start_portfolio['total_value']
        logger.info(f"Portfolio value at start ({start_date}): ₹{value_at_start:,.2f} (using forward calculation)")
    except Exception as e:
        logger.error(f"Error getting portfolio state at start date: {str(e)}")
        # Fallback to manual calculation (old method) if reconstruction fails
        logger.warning("Falling back to manual calculation (may be inaccurate for split/bonus stocks)")
        transactions_before_start = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date <= start_datetime
        ).all()
        
        holdings_at_start = {}
        for txn in transactions_before_start:
            security_id = txn.security_id
            if security_id not in holdings_at_start:
                holdings_at_start[security_id] = {'quantity': 0, 'total_cost': 0}
            
            if txn.type == 'BUY':
                holdings_at_start[security_id]['quantity'] += float(txn.quantity)
                holdings_at_start[security_id]['total_cost'] += float(txn.quantity) * float(txn.price)
            elif txn.type == 'SELL':
                if holdings_at_start[security_id]['quantity'] > 0:
                    avg_cost = holdings_at_start[security_id]['total_cost'] / holdings_at_start[security_id]['quantity']
                    holdings_at_start[security_id]['quantity'] -= float(txn.quantity)
                    holdings_at_start[security_id]['total_cost'] -= float(txn.quantity) * avg_cost
        
        value_at_start = sum(h['total_cost'] for h in holdings_at_start.values() if h['quantity'] > 0)
        logger.warning(f"Fallback calculation - Portfolio value at start: ₹{value_at_start:,.2f} (using cost basis - may be inaccurate)")
    
    # ✅ UPDATED: Use Forward Calculation Service for current value as well
    try:
        end_portfolio = get_client_portfolio_by_date(client_id, end_date)
        current_value = end_portfolio['total_value']
        logger.info(f"Portfolio value at end ({end_date}): ₹{current_value:,.2f} (using forward calculation)")
    except Exception as e:
        logger.error(f"Error getting portfolio state at end date: {str(e)}")
        # Fallback to manual calculation
        logger.warning("Falling back to manual calculation for current value")
        current_value = sum(
            float(h.quantity) * float(h.security.current_price)
            for h in current_holdings
            if h.security and h.security.current_price
        )
        logger.warning(f"Fallback calculation - Current portfolio value: ₹{current_value:,.2f} (may be inaccurate)")
    
    # Client additions during period (BUY transactions)
    buy_transactions_in_period = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'BUY',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    total_investments = sum(float(txn.quantity) * float(txn.price) for txn in buy_transactions_in_period)
    
    # Client withdrawals during period (SELL transactions)
    sell_transactions_in_period = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'SELL',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    total_withdrawals = sum(float(txn.quantity) * float(txn.price) for txn in sell_transactions_in_period)
    
    # ✅ FIXED: Net client additions = investments - withdrawals
    client_additions = total_investments - total_withdrawals
    
    # Calculate breakdown
    total_portfolio_change = current_value - value_at_start
    net_client_flow = client_additions  # Already net (investments - withdrawals)
    unrealized_profit_contribution = total_portfolio_change - net_client_flow
    
    return {
        'portfolio_value_at_start': value_at_start,
        'current_portfolio_value': current_value,
        'total_portfolio_change': total_portfolio_change,
        'total_portfolio_change_percent': (total_portfolio_change / value_at_start * 100) if value_at_start > 0 else 0,
        'total_investments': total_investments,  # Gross investments
        'total_withdrawals': total_withdrawals,  # Gross withdrawals
        'client_additions': client_additions,  # ✅ NET (investments - withdrawals)
        'net_client_flow': net_client_flow,
        'unrealized_profit_contribution': unrealized_profit_contribution,
        'breakdown_percent': {
            'client_flow_contribution': (net_client_flow / total_portfolio_change * 100) if total_portfolio_change != 0 else 0,
            'unrealized_profit_contribution': (unrealized_profit_contribution / total_portfolio_change * 100) if total_portfolio_change != 0 else 0
        }
    }

def calculate_best_performers(client_id, start_date, end_date, current_holdings):
    """
    5. Calculate best performing stocks in the period
    
    Sort by holder-relevant price % change (via period_performer_return_service):
    - Held at period start: blended or period-start adjusted market price
    - New / added shares: VWAP of period BUYs blended with start quantity
    - End price: adjusted market at end_date, or at last SELL date when exited in period
    """
    logger.info(f"Calculating best performers for period {start_date} to {end_date}")
    
    # ✅ UPDATED: Use Forward Calculation Service for accurate holdings
    try:
        from models import Security
        
        start_portfolio = get_client_portfolio_by_date(client_id, start_date)
        end_portfolio = get_client_portfolio_by_date(client_id, end_date)
        
        start_state = {'holdings': start_portfolio['holdings']}
        end_state = {'holdings': end_portfolio['holdings']}
        
        # Build holdings dictionaries for easy lookup
        start_holdings_dict = {h['security_id']: h for h in start_state['holdings']}
        end_holdings_dict = {h['security_id']: h for h in end_state['holdings']}
        
        contributors = []
        
        # Process all securities that exist in either start or end state
        all_security_ids = set(start_holdings_dict.keys()) | set(end_holdings_dict.keys())
        
        for security_id in all_security_ids:
            start_holding = start_holdings_dict.get(security_id)
            end_holding = end_holdings_dict.get(security_id)
            
            # Get security info
            security = Security.query.get(security_id)
            if not security:
                continue
            
            from services.period_performer_return_service import (
                compute_security_period_price_return,
                period_net_investment,
            )

            price_metrics = compute_security_period_price_return(
                client_id,
                security_id,
                start_date,
                end_date,
                start_holding=start_holding,
                end_holding=end_holding,
            )
            if not price_metrics:
                logger.warning(
                    f"Could not determine period price return for security_id {security_id} "
                    f"(symbol: {security.symbol if security else 'Unknown'}) for period {start_date} to {end_date}. Skipping."
                )
                continue

            start_price_adjusted = price_metrics['start_price']
            end_price_adjusted = price_metrics['end_price']
            price_percent_change = price_metrics['price_percent_change']
            price_change = price_metrics['price_change']

            # Get start/end data for display
            start_value = start_holding['current_value'] if start_holding else 0
            start_quantity = start_holding['quantity'] if start_holding else 0
            end_value = end_holding['current_value'] if end_holding else 0
            end_quantity = end_holding['quantity'] if end_holding else 0

            net_investment = period_net_investment(client_id, security_id, start_date, end_date)
            
            # ✅ Value contribution = MTM gain (profit/loss) = (end_value - start_value) - net_investment
            # This shows the actual value added/destroyed, accounting for new investments
            value_contribution = (end_value - start_value) - net_investment
            
            contributors.append({
                'symbol': (end_holding or start_holding)['symbol'],
                'name': (end_holding or start_holding).get('name', security.name if security else 'Unknown'),
                'sector': (end_holding or start_holding).get('sector', 'Unknown'),
                'value_contribution': value_contribution,
                'contribution_percent': (value_contribution / start_value * 100) if start_value > 0 else 0,
                'price_percent_change': price_percent_change,  # ✅ NEW: Percentage price change (adjusted)
                'price_change': price_change,
                'start_value': start_value,
                'end_value': end_value,
                'current_value': end_value,
                'current_quantity': end_quantity,
                'current_price': end_price_adjusted,
                'start_quantity': start_quantity,
                'start_price': start_price_adjusted,
                'end_price': end_price_adjusted,
                'return_basis': price_metrics.get('return_basis'),
                'end_price_basis': price_metrics.get('end_price_basis'),
                'effective_end_date': price_metrics.get('effective_end_date'),
            })
        
        # ✅ Sort by price_percent_change (highest first) instead of value_contribution
        # Filter out stocks with 0 or negative price change for best performers
        positive_performers = [c for c in contributors if c['price_percent_change'] > 0]
        positive_performers.sort(key=lambda x: x['price_percent_change'], reverse=True)
        
        # If we have less than 10 positive performers, include zero-change stocks
        if len(positive_performers) < 10:
            zero_performers = [c for c in contributors if c['price_percent_change'] == 0]
            positive_performers.extend(zero_performers[:10 - len(positive_performers)])
        
        return {
            'best_performers': positive_performers[:10],  # Top 10 by price % change (only positive)
            'total_contribution': sum(c['value_contribution'] for c in contributors if c['value_contribution'] > 0)
        }
        
    except Exception as e:
        logger.error(f"Error calculating best performers using reconstruction: {str(e)}")
        # Fallback to old method
        logger.warning("Falling back to old best performers calculation")
        
        start_datetime = datetime.combine(start_date, datetime.min.time())
        
        # Get transactions at start to determine holdings and prices then
        transactions_before_start = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.transaction_date <= start_datetime
        ).all()
        
        # Build holdings at start with average prices
        holdings_at_start = {}
        for txn in transactions_before_start:
            security_id = txn.security_id
            if security_id not in holdings_at_start:
                holdings_at_start[security_id] = {
                    'quantity': 0,
                    'total_cost': 0,
                    'security': txn.security
                }
            
            if txn.type == 'BUY':
                holdings_at_start[security_id]['quantity'] += float(txn.quantity)
                holdings_at_start[security_id]['total_cost'] += float(txn.quantity) * float(txn.price)
            elif txn.type == 'SELL':
                if holdings_at_start[security_id]['quantity'] > 0:
                    avg_cost = holdings_at_start[security_id]['total_cost'] / holdings_at_start[security_id]['quantity']
                    holdings_at_start[security_id]['quantity'] -= float(txn.quantity)
                    holdings_at_start[security_id]['total_cost'] -= float(txn.quantity) * avg_cost
        
        # Calculate contribution for each stock
        contributors = []
        
        for holding in current_holdings:
            if not holding.security or not holding.security.current_price:
                continue
            
            security_id = holding.security_id
            current_quantity = float(holding.quantity)
            current_price = float(holding.security.current_price)
            current_value = current_quantity * current_price
            
            # Get start data
            if security_id in holdings_at_start and holdings_at_start[security_id]['quantity'] > 0:
                start_quantity = holdings_at_start[security_id]['quantity']
                start_avg_price = holdings_at_start[security_id]['total_cost'] / start_quantity
                start_value = start_quantity * start_avg_price
                
                # Value contribution = current value - start value (for shares held throughout)
                # For simplicity, use minimum quantity
                common_quantity = min(start_quantity, current_quantity)
                value_contribution = common_quantity * (current_price - start_avg_price)
                contribution_percent = (value_contribution / start_value * 100) if start_value > 0 else 0
            else:
                # New position added during period
                value_contribution = 0
                contribution_percent = 0
            
            # Get sector
            sector = 'Unknown'
            if hasattr(holding.security, 'sector') and holding.security.sector:
                sector = holding.security.sector
            elif hasattr(holding.security, 'industry') and holding.security.industry:
                sector = holding.security.industry
            
            contributors.append({
                'symbol': holding.security.symbol,
                'name': holding.security.name,
                'sector': sector,
                'value_contribution': value_contribution,
                'contribution_percent': contribution_percent,
                'current_value': current_value,
                'current_quantity': current_quantity,
                'current_price': current_price
            })
        
        # Sort by contribution
        contributors.sort(key=lambda x: x['value_contribution'], reverse=True)
        
        return {
            'best_performers': contributors[:10],  # Top 10
            'total_contribution': sum(c['value_contribution'] for c in contributors)
        }

def calculate_worst_performers(client_id, start_date, end_date, current_holdings):
    """
    6. Calculate stocks doing worst in the period
    
    ✅ NEW ARCHITECTURE: Uses Portfolio Reconstruction Service (quantity-only adjustments)
    Wrapper function for backward compatibility with routes
    """
    logger.info(f"Calculating worst performers for period {start_date} to {end_date}")
    
    # Calculate MTM analysis which has all the data we need
    mtm_analysis = calculate_mark_to_market_gains(client_id, start_date, end_date, current_holdings)
    
    # Transform MTM data to worst_performers format
    return calculate_worst_performers_from_mtm(mtm_analysis, client_id, start_date, end_date)


def calculate_worst_performers_from_mtm(mtm_analysis, client_id=None, start_date=None, end_date=None):
    """
    Calculate worst performers from existing MTM analysis data
    
    ✅ UPDATED: Sort by percentage price change (adjusted for corporate actions)
    - Calculates price_percent_change using adjusted historical prices
    - Sorts by lowest price_percent_change instead of absolute value contribution
    
    Args:
        mtm_analysis: MTM analysis data containing mtm_gains_by_stock
        start_date: Period start date (for getting adjusted prices)
        end_date: Period end date (for getting adjusted prices)
    """
    logger.info(f"Calculating worst performers from MTM analysis")
    
    # Get stock-level gains from mtm_analysis (already calculated by Portfolio Reconstruction API)
    mtm_gains_by_stock = mtm_analysis.get('mtm_gains_by_stock', [])
    
    if not mtm_gains_by_stock:
        logger.warning("No MTM gains by stock data available")
        return {
            'worst_performers': [],
            'total_loss': 0
        }
    
    from models import Security
    from services.period_performer_return_service import (
        compute_security_period_price_return,
        period_net_investment,
    )
    
    # Get period dates from mtm_analysis if not provided
    if not start_date:
        start_date = mtm_analysis.get('start_date')
    if not end_date:
        end_date = mtm_analysis.get('end_date')
    
    performers = []
    for stock in mtm_gains_by_stock:
        security_id = stock.get('security_id')
        if not security_id:
            # Try to get security_id from symbol
            symbol = stock.get('symbol')
            if symbol:
                security = Security.query.filter_by(symbol=symbol).first()
                security_id = security.id if security else None
        
        if not (security_id and client_id and start_date and end_date):
            continue

        start_qty = float(stock.get('start_quantity', 0) or stock.get('quantity', 0) or 0)
        end_qty = float(stock.get('end_quantity', 0) or 0)
        end_holding_proxy = None
        if end_qty > 0:
            end_value = float(stock.get('end_value', 0) or 0)
            end_holding_proxy = {
                'quantity': end_qty,
                'current_price': (end_value / end_qty) if end_qty else 0,
                'average_price': (end_value / end_qty) if end_qty else 0,
            }

        price_metrics = compute_security_period_price_return(
            client_id,
            security_id,
            start_date,
            end_date,
            start_holding=None,
            end_holding=end_holding_proxy,
            start_quantity=start_qty,
        )
        if not price_metrics:
            logger.warning(
                f"Could not determine prices for security_id {security_id} "
                f"(symbol: {stock.get('symbol', 'Unknown')}) for period {start_date} to {end_date}. Skipping."
            )
            continue

        start_price_adjusted = price_metrics['start_price']
        end_price_adjusted = price_metrics['end_price']
        price_percent_change = price_metrics['price_percent_change']
        price_change = price_metrics['price_change']

        start_value_for_contrib = float(stock.get('start_value', 0) or 0)
        end_value_for_contrib = float(stock.get('end_value', 0) or 0)
        value_contribution = float(stock.get('gain', 0) or 0)
        contribution_percent = float(stock.get('gain_percent', 0) or 0)

        try:
            net_investment = period_net_investment(
                client_id, security_id, start_date, end_date
            )
            value_contribution = (end_value_for_contrib - start_value_for_contrib) - net_investment
            contribution_percent = (
                (value_contribution / start_value_for_contrib * 100)
                if start_value_for_contrib > 0
                else 0.0
            )
        except Exception as e:
            logger.warning(
                f"Could not compute net investment for worst performer {stock.get('symbol')} "
                f"(security_id={security_id}): {e}"
            )

        performers.append({
            'symbol': stock['symbol'],
            'name': stock.get('name', 'Unknown'),
            'sector': stock.get('sector', 'Unknown'),
            'value_contribution': value_contribution,  # ✅ Profit/Loss added (aligned with best performers)
            'contribution_percent': contribution_percent,
            'price_percent_change': price_percent_change,  # ✅ NEW: Percentage price change (adjusted)
            'price_change': price_change,  # Absolute price change
            'start_value': stock['start_value'],
            'end_value': stock['end_value'],
            'current_value': stock['end_value'],
            'current_quantity': stock.get('end_quantity', 0),
            'current_price': end_price_adjusted,
            'end_price': end_price_adjusted,  # Alias for template compatibility
            'start_price': start_price_adjusted,
            'return_basis': price_metrics.get('return_basis'),
            'end_price_basis': price_metrics.get('end_price_basis'),
            'effective_end_date': price_metrics.get('effective_end_date'),
        })
    
    # ✅ Sort by price_percent_change (lowest first = worst performers)
    # Filter to only show stocks with negative price change OR negative value contribution
    # This ensures worst performers actually show losses, not just low gains
    worst_performers_filtered = [
        p for p in performers 
        if p['price_percent_change'] < 0 or p['value_contribution'] < 0
    ]
    worst_performers_filtered.sort(key=lambda x: (x['price_percent_change'], x['value_contribution']))
    
    # If we have less than 10 negative performers, don't fill with positive ones
    # Worst performers should only show actual losses
    return {
        'worst_performers': worst_performers_filtered[:10],  # Worst 10 by price % change (only negative)
        'total_loss': sum(p['value_contribution'] for p in performers if p['value_contribution'] < 0)
    }

def calculate_period_benchmark_comparison(client_id, start_date, end_date, portfolio_xirr):
    """
    Calculate benchmark comparison for the period
    """
    logger.warning(f"🔍 calculate_period_benchmark_comparison called: client_id={client_id}, start_date={start_date}, end_date={end_date}, start_date_type={type(start_date)}, end_date_type={type(end_date)}")
    try:
        # ------------------------------------------------------------------
        # ✅ UPDATED: Period benchmark XIRR using SAME cashflow schedule as portfolio period XIRR
        # We simulate investing:
        #   - start_portfolio_value at start_date
        #   - all cashflows during the period (same dates/amounts)
        # into the benchmark (NIFTY 50), then compute:
        #   - benchmark_end_value at end_date
        #   - benchmark_xirr (XIRR of SAME cashflows with benchmark_end_value as terminal value)
        #   - benchmark absolute gain/loss for the period
        # ------------------------------------------------------------------
        from models import Benchmark, BenchmarkData
        from services.forward_holding_calculation_service import get_client_portfolio_by_date
        from bisect import bisect_right

        benchmark = Benchmark.query.filter_by(id=1).first()  # NIFTY 50
        if not benchmark:
            logger.warning("NIFTY 50 benchmark not found")
            return {}

        # Portfolio start value for the period (same notion as period XIRR start cashflow)
        start_portfolio = get_client_portfolio_by_date(client_id, start_date)
        start_value = float(start_portfolio.get('total_value', 0.0) or 0.0)

        # Period cashflows (same dates/amounts used for portfolio XIRR in this period)
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        period_cashflows = Cashflow.query.filter(
            Cashflow.client_id == client_id,
            Cashflow.date >= start_datetime,
            Cashflow.date <= end_datetime
        ).order_by(Cashflow.date).all()

        total_invested = sum(abs(float(cf.amount)) for cf in period_cashflows if float(cf.amount) < 0)
        total_withdrawn = sum(float(cf.amount) for cf in period_cashflows if float(cf.amount) > 0)
        net_cashflow = total_invested - total_withdrawn

        # Detect cashflows on the exact start_date (can double count if start_value already includes them)
        start_date_cashflows = [
            cf for cf in period_cashflows
            if (cf.date.date() if hasattr(cf.date, 'date') else cf.date) == start_date
        ]
        start_date_cashflow_amount = sum(float(cf.amount) for cf in start_date_cashflows) if start_date_cashflows else 0.0
        start_date_cashflow_count = len(start_date_cashflows)
        if start_value > 0 and start_date_cashflow_amount != 0:
            logger.warning(
                "Benchmark calc risk: start_date cashflows detected (amount=%s) while start_value>0 for client %s",
                start_date_cashflow_amount,
                client_id
            )

        # Normalize dates to date objects (not datetime) for database query and lookup
        start_date_normalized = start_date.date() if hasattr(start_date, 'date') else start_date
        end_date_normalized = end_date.date() if hasattr(end_date, 'date') else end_date
        
        # Load benchmark prices for [start_date - 10d, end_date] to allow fallback to closest prior
        price_rows = BenchmarkData.query.filter(
            BenchmarkData.benchmark_id == benchmark.id,
            BenchmarkData.date >= (start_date_normalized - timedelta(days=10)),
            BenchmarkData.date <= end_date_normalized
        ).order_by(BenchmarkData.date).all()

        if not price_rows:
            logger.warning(f"🔍 No benchmark price data found for period benchmark comparison: client_id={client_id}, start_date={start_date_normalized}, end_date={end_date_normalized}, benchmark_id={benchmark.id if benchmark else 'None'}")
            return {}

        # Build sorted price series (date -> price), ensure pure date objects
        dates = []
        prices = []
        for r in price_rows:
            if not r.date or r.price is None:
                continue
            d = r.date.date() if hasattr(r.date, 'date') else r.date
            if d is None:
                logger.warning(f"🔍 Skipping row with None date: r.date={r.date}, r.price={r.price}")
                continue
            try:
                p = float(r.price)
            except Exception:
                continue
            dates.append(d)
            prices.append(p)
        logger.warning(f"🔍 Built dates/prices lists: {len(dates)} dates, {len(prices)} prices, date_range=[{dates[0] if dates else 'none'} to {dates[-1] if dates else 'none'}]")

        if not dates:
            logger.warning(f"🔍 No valid dates extracted from price_rows: client_id={client_id}, price_rows_count={len(price_rows)}, dates_count={len(dates)}")
            return {}

        logger.debug(f"Benchmark price lookup: query returned {len(dates)} prices from {dates[0] if dates else 'N/A'} to {dates[-1] if dates else 'N/A'}, searching for start_date={start_date_normalized}, end_date={end_date_normalized}")

        def price_on_or_before(target_date):
            """Return (benchmark price, actual date used) at target_date or closest prior price."""
            # target_date should already be a date object, but normalize just in case
            target_date = target_date.date() if hasattr(target_date, 'date') else target_date
            # dates is ascending
            idx = bisect_right(dates, target_date) - 1
            if idx >= 0:
                actual_date = dates[idx]
                actual_price = prices[idx]
                # Ensure actual_date is not None
                if actual_date is None:
                    logger.error(f"ERROR: actual_date is None at idx={idx} for target_date={target_date}, dates={dates[:5]}...")
                    return (actual_price, None)
                logger.warning(f"✅ Found benchmark price for {target_date}: {actual_price} from date {actual_date} (idx={idx}, total_dates={len(dates)})")
                return (actual_price, actual_date)
            logger.warning(f"No benchmark price found for {target_date}. Available date range: {dates[0] if dates else 'none'} to {dates[-1] if dates else 'none'}")
            return (None, None)

        # Validate dates are not in the future and start_date is before end_date
        from datetime import date as date_type
        today = date_type.today()
        
        # Check for invalid dates (use normalized dates)
        if start_date_normalized > end_date_normalized:
            logger.error(f"Start date ({start_date_normalized}) is after end date ({end_date_normalized}) in benchmark calculation")
            return {}
        
        # Warn if dates are in the future but still proceed (price_on_or_before will use latest available price)
        if start_date_normalized > today:
            logger.warning(f"Start date ({start_date_normalized}) is in the future (today={today}). Using latest available benchmark price.")
        if end_date_normalized > today:
            logger.warning(f"End date ({end_date_normalized}) is in the future (today={today}). Using latest available benchmark price.")
        
        try:
            start_benchmark_price, start_benchmark_price_date = price_on_or_before(start_date_normalized)
            end_benchmark_price, end_benchmark_price_date = price_on_or_before(end_date_normalized)
        except Exception as e:
            logger.error(f"🔍 ERROR in price_on_or_before: {str(e)}", exc_info=True)
            start_benchmark_price, start_benchmark_price_date = None, None
            end_benchmark_price, end_benchmark_price_date = None, None
        
        # Log detailed information about the price lookup at WARNING level for debugging (WARNING shows in gunicorn logs)
        logger.warning(f"🔍 Benchmark price lookup result: start_date={start_date_normalized}, start_price={start_benchmark_price}, start_price_date={start_benchmark_price_date}, end_date={end_date_normalized}, end_price={end_benchmark_price}, end_price_date={end_benchmark_price_date}, available_dates_range=[{dates[0] if dates else 'none'} to {dates[-1] if dates else 'none'}], total_dates={len(dates)}")
        
        if not start_benchmark_price or not end_benchmark_price or start_benchmark_price <= 0:
            logger.warning(f"Missing benchmark prices: start_price={start_benchmark_price}, end_price={end_benchmark_price}, start_date={start_date_normalized}, end_date={end_date_normalized}, start_price_date={start_benchmark_price_date}, end_price_date={end_benchmark_price_date}, available_dates={dates[0] if dates else 'none'} to {dates[-1] if dates else 'none'}")
            return {}
        
        # Log successful price lookup with dates
        if start_benchmark_price_date and end_benchmark_price_date:
            logger.info(f"Benchmark price lookup successful with dates: start_date={start_date_normalized} -> price_date={start_benchmark_price_date.isoformat() if hasattr(start_benchmark_price_date, 'isoformat') else start_benchmark_price_date}, end_date={end_date_normalized} -> price_date={end_benchmark_price_date.isoformat() if hasattr(end_benchmark_price_date, 'isoformat') else end_benchmark_price_date}")
        else:
            logger.warning(f"Benchmark prices found but dates are None: start_price_date={start_benchmark_price_date}, end_price_date={end_benchmark_price_date}")
        
        # Calculate simple price return: (end_price / start_price - 1) * 100
        # This is the pure index return between the two dates (no cashflow weighting)
        price_return_percent = ((end_benchmark_price / start_benchmark_price) - 1) * 100 if start_benchmark_price > 0 else 0.0
        
        logger.info(
            "Benchmark return calculation: start_date=%s, end_date=%s, start_price=%.2f, end_price=%.2f, price_return=%.2f%%, start_date_cashflows=%s",
            start_date,
            end_date,
            start_benchmark_price,
            end_benchmark_price,
            price_return_percent,
            start_date_cashflow_amount
        )

        # Simulate benchmark units using the SAME cashflows schedule
        benchmark_units = 0.0
        benchmark_cashflow_verification = []

        # 1) Invest start_value at start_date
        if start_value > 0:
            units_added = start_value / start_benchmark_price
            benchmark_units += units_added
            benchmark_cashflow_verification.append({
                'date': start_date_normalized.isoformat(),
                'type': 'Initial Investment',
                'amount': -start_value,
                'benchmark_price': start_benchmark_price,
                'benchmark_price_date': start_benchmark_price_date.isoformat() if start_benchmark_price_date else None,
                'units_added': units_added,
                'units_removed': 0.0,
                'cumulative_units': benchmark_units,
                'cumulative_value': benchmark_units * start_benchmark_price,
                'description': f'Portfolio start value invested at {start_benchmark_price_date.isoformat() if start_benchmark_price_date else start_date_normalized.isoformat()} price'
            })

        # 2) Apply period cashflows
        for cf in period_cashflows:
            cf_date = cf.date.date() if hasattr(cf.date, 'date') else cf.date
            amt = float(cf.amount)
            px, px_date = price_on_or_before(cf_date)
            if not px or px <= 0:
                # Log skipped cashflow
                benchmark_cashflow_verification.append({
                    'date': cf_date.isoformat() if hasattr(cf_date, 'isoformat') else str(cf_date),
                    'type': 'Investment' if amt < 0 else 'Withdrawal',
                    'amount': amt,
                    'benchmark_price': None,
                    'benchmark_price_date': None,
                    'units_added': 0.0,
                    'units_removed': 0.0,
                    'cumulative_units': benchmark_units,
                    'cumulative_value': benchmark_units * (px if px and px > 0 else end_benchmark_price),
                    'description': f'No benchmark price available for {cf_date}'
                })
                continue
            if amt < 0:
                # Investment: buy more units
                units_added = abs(amt) / px
                benchmark_units += units_added
                benchmark_cashflow_verification.append({
                    'date': cf_date.isoformat() if hasattr(cf_date, 'isoformat') else str(cf_date),
                    'type': 'Investment',
                    'amount': amt,
                    'benchmark_price': px,
                    'benchmark_price_date': px_date.isoformat() if px_date else None,
                    'units_added': units_added,
                    'units_removed': 0.0,
                    'cumulative_units': benchmark_units,
                    'cumulative_value': benchmark_units * px,
                    'description': f'Investment at {px_date.isoformat() if px_date else str(cf_date)} price'
                })
            elif amt > 0:
                # Withdrawal: sell units up to available
                units_to_sell = min(amt / px, benchmark_units)
                benchmark_units = max(0.0, benchmark_units - units_to_sell)
                benchmark_cashflow_verification.append({
                    'date': cf_date.isoformat() if hasattr(cf_date, 'isoformat') else str(cf_date),
                    'type': 'Withdrawal',
                    'amount': amt,
                    'benchmark_price': px,
                    'benchmark_price_date': px_date.isoformat() if px_date else None,
                    'units_added': 0.0,
                    'units_removed': units_to_sell,
                    'cumulative_units': benchmark_units,
                    'cumulative_value': benchmark_units * px,
                    'description': f'Withdrawal at {px_date.isoformat() if px_date else str(cf_date)} price'
                })

        # Add final row showing end value calculation
        benchmark_end_value = benchmark_units * end_benchmark_price
        benchmark_cashflow_verification.append({
            'date': end_date_normalized.isoformat(),
            'type': 'Final Value',
            'amount': 0.0,
            'benchmark_price': end_benchmark_price,
            'benchmark_price_date': end_benchmark_price_date.isoformat() if end_benchmark_price_date else None,
            'units_added': 0.0,
            'units_removed': 0.0,
            'cumulative_units': benchmark_units,
            'cumulative_value': benchmark_end_value,
            'description': f'Final units × end price = {benchmark_units:.4f} × ₹{end_benchmark_price:,.2f} = ₹{benchmark_end_value:,.2f}'
        })
        
        # Log verification array status for debugging
        logger.warning(f"🔍 Benchmark cashflow verification built: {len(benchmark_cashflow_verification)} entries, start_value={start_value}, period_cashflows_count={len(period_cashflows)}")

        # Benchmark XIRR: same cashflows schedule as portfolio period XIRR
        # IMPORTANT: Use the actual end_benchmark_price_date (not end_date) for final value
        # This ensures the final value date matches the price date used for calculating benchmark_end_value
        cashflow_data = []
        if start_value > 0:
            cashflow_data.append((start_datetime, -start_value))
        for cf in period_cashflows:
            cashflow_data.append((cf.date, float(cf.amount)))
        
        # Determine the correct end date for XIRR calculation
        # Use end_benchmark_price_date if available (the actual date of the price used)
        # Otherwise use end_date (the requested period end date)
        if end_benchmark_price_date:
            final_value_date = end_benchmark_price_date
        else:
            final_value_date = end_date_normalized

        benchmark_xirr, _, _, _, _ = calculate_xirr(cashflow_data, benchmark_end_value, end_date=final_value_date)

        # Absolute gain/loss in the period (analogous to accumulated profit): End - Start - Net cashflow
        benchmark_accumulated_profit = benchmark_end_value - start_value - net_cashflow

        # Portfolio vs benchmark (XIRR outperformance)
        outperformance = (portfolio_xirr - benchmark_xirr) * 100 if benchmark_xirr else 0

        return {
            'benchmark_name': benchmark.name if getattr(benchmark, 'name', None) else 'NIFTY 50',
            'benchmark_id': benchmark.id,

            # Portfolio
            'portfolio_xirr_percent': portfolio_xirr * 100,

            # Benchmark
            'benchmark_xirr_percent': benchmark_xirr * 100,
            'benchmark_start_price': start_benchmark_price,
            'benchmark_end_price': end_benchmark_price,
            'benchmark_start_price_date': start_benchmark_price_date.isoformat() if start_benchmark_price_date else None,
            'benchmark_end_price_date': end_benchmark_price_date.isoformat() if end_benchmark_price_date else None,
            'benchmark_start_value': start_value,
            'benchmark_end_value': benchmark_end_value,
            'benchmark_gain_loss': benchmark_accumulated_profit,
            'benchmark_gain_loss_percent': (benchmark_accumulated_profit / (start_value + total_invested) * 100) if (start_value + total_invested) > 0 else 0.0,
            'benchmark_total_invested': total_invested,
            'benchmark_total_withdrawn': total_withdrawn,
            'benchmark_net_cashflow': net_cashflow,
            'benchmark_price_return_percent': price_return_percent,
            'benchmark_start_date_cashflow_amount': start_date_cashflow_amount,
            'benchmark_start_date_cashflow_count': start_date_cashflow_count,
            'benchmark_start_date_cashflow_double_count_risk': bool(start_value > 0 and start_date_cashflow_amount != 0),
            'benchmark_cashflow_verification': benchmark_cashflow_verification,
            'benchmark_final_units': benchmark_units,
            'benchmark_final_value': benchmark_end_value,

            # Compatibility keys (old template names)
            'nifty_xirr_percent': benchmark_xirr * 100,
            'nifty_current_value': benchmark_end_value,

            # Simple price return between start and end dates (compat)
            'estimated_benchmark_return_for_period': price_return_percent,

            # Comparison
            'outperformance': outperformance,
            'outperformance_percent': outperformance,
            'benchmark_vs_portfolio': 'Outperforming' if outperformance > 0 else 'Underperforming'
        }
    except Exception as e:
        logger.error(f"Error calculating benchmark comparison: {str(e)}", exc_info=True)
        import traceback
        logger.error(f"Benchmark comparison traceback: {traceback.format_exc()}")
        return {}

def calculate_period_summary(mtm_analysis, trade_analysis, investment_breakdown, 
                             gains_breakdown, period_xirr_data, twr_data=None):
    """
    Calculate comprehensive summary metrics for the period
    """
    # ✅ Total Return % for the period should be:
    # (current_portfolio_value / (start_portfolio + investments) - 1) * 100
    # This represents the return on the starting portfolio plus new investments during the period
    
    # Get start and end portfolio values
    start_value = period_xirr_data.get('start_value', 0) or mtm_analysis.get('total_start_value', 0) or mtm_analysis.get('total_value_at_period_start', 0)
    current_value = period_xirr_data.get('current_value', 0) or mtm_analysis.get('total_end_value', 0) or mtm_analysis.get('total_current_value', 0)
    
    # Get gross investments during the period (not net, just investments)
    gross_investments = investment_breakdown.get('gross_investment_in_period', 0)
    
    # Calculate total return: (current_value / (start_value + investments) - 1) * 100
    denominator = start_value + gross_investments
    if denominator > 0:
        total_return_percent = ((current_value / denominator) - 1) * 100
    else:
        # Fallback: if denominator is 0, use MTM gain percent
        total_return_percent = mtm_analysis.get('total_mtm_gain_percent', 0)
        if not total_return_percent:
            # Last fallback: (End - Start) / Start * 100
            if start_value > 0:
                total_return_percent = ((current_value - start_value) / start_value) * 100
            else:
                total_return_percent = 0.0
    
    logger.info(f"Total Return Calculation: current_value={current_value:,.2f}, start_value={start_value:,.2f}, investments={gross_investments:,.2f}, denominator={denominator:,.2f}, total_return_percent={total_return_percent:.2f}%")
    
    # Total returns in absolute terms (for reference)
    total_returns = (
        mtm_analysis.get('total_mtm_gain', 0) + 
        trade_analysis.get('total_realized_gain', 0)
    )
    
    total_invested = investment_breakdown.get('total_investment_in_period', 0)
    
    # Calculate return on period investment (for reference, but not used as Total Return)
    return_on_period_investment = (total_returns / total_invested * 100) if total_invested > 0 else 0
    
    # Calculate overall performance score (0-100)
    performance_score = calculate_performance_score(
        mtm_analysis.get('total_mtm_gain_percent', 0),
        trade_analysis.get('average_gain_percent', 0),
        period_xirr_data.get('xirr_percent', 0)
    )
    
    return {
        'total_return_percent': total_return_percent,  # Period return: (current_value / (start_value + investments) - 1) * 100
        'total_returns': total_return_percent,  # Alias for backward compatibility
        'total_returns_absolute': total_returns,  # Keep absolute value for reference
        'total_invested_in_period': total_invested,
        'return_on_period_investment_percent': return_on_period_investment,
        'mtm_contribution': mtm_analysis.get('total_mtm_gain', 0),
        'realized_gains_contribution': trade_analysis.get('total_realized_gain', 0),
        'unrealized_gains_contribution': gains_breakdown.get('unrealized_profit_contribution', 0),
        'performance_score': performance_score,
        'performance_rating': get_performance_rating(performance_score),
        'key_metrics': {
            'mtm_gain_percent': mtm_analysis.get('total_mtm_gain_percent', 0),
            'realized_gain_percent': trade_analysis.get('average_gain_percent', 0),
            'portfolio_growth_percent': gains_breakdown.get('total_portfolio_change_percent', 0),
            'xirr_percent': period_xirr_data.get('xirr_percent', 0),
            'twr_percent': twr_data.get('twr_percent', 0) if twr_data else 0.0
        },
        'twr': twr_data.get('twr', 0) if twr_data else 0.0,
        'twr_percent': twr_data.get('twr_percent', 0) if twr_data else 0.0
    }

def calculate_performance_score(mtm_gain_percent, realized_gain_percent, xirr_percent):
    """
    Calculate overall performance score (0-100) based on multiple factors
    """
    # Weight different metrics
    weighted_score = (
        mtm_gain_percent * 0.4 +
        realized_gain_percent * 0.3 +
        xirr_percent * 100 * 0.3
    )
    
    # Normalize to 0-100 scale (assuming 50% annual return is 100 score)
    score = min(100, max(0, (weighted_score + 50) * 2))
    
    return round(score, 2)

def get_performance_rating(score):
    """
    Get performance rating based on score
    """
    if score >= 80:
        return 'Excellent'
    elif score >= 60:
        return 'Good'
    elif score >= 40:
        return 'Average'
    elif score >= 20:
        return 'Below Average'
    else:
        return 'Poor'

def calculate_twr(client_id, start_date, end_date, start_portfolio, end_portfolio, period_cashflows):
    """
    Calculate Time-Weighted Return (TWR) for the period.
    
    TWR eliminates the effect of cash flows by breaking the period into sub-periods
    at each cash flow and compounding the returns.
    
    Formula:
    - For each sub-period: R = (End Value - Start Value - Cashflow) / Start Value
    - TWR = (1 + R1) × (1 + R2) × ... × (1 + Rn) - 1
    
    Args:
        client_id: Client ID
        start_date: Period start date
        end_date: Period end date
        start_portfolio: Portfolio state at start_date (from get_client_portfolio_by_date)
        end_portfolio: Portfolio state at end_date (from get_client_portfolio_by_date)
        period_cashflows: List of Cashflow objects during the period (sorted by date)
    
    Returns:
        dict with 'twr_percent' and 'twr' (decimal) keys
    """
    try:
        from services.forward_holding_calculation_service import get_client_portfolio_by_date
        from datetime import timedelta
        
        # Get start portfolio value
        start_value = float(start_portfolio.get('total_value', 0))
        if start_value <= 0:
            logger.warning(f"TWR: Start portfolio value is {start_value}, cannot calculate TWR")
            return {'twr': 0.0, 'twr_percent': 0.0, 'error': 'Invalid start portfolio value'}
        
        # Get end portfolio value
        end_value = float(end_portfolio.get('total_value', 0))
        if end_value <= 0:
            logger.warning(f"TWR: End portfolio value is {end_value}, cannot calculate TWR")
            return {'twr': 0.0, 'twr_percent': 0.0, 'error': 'Invalid end portfolio value'}
        
        # If no cashflows, TWR is simple return
        if not period_cashflows or len(period_cashflows) == 0:
            logger.info(f"TWR: No cashflows in period, calculating simple return")
            simple_return = (end_value - start_value) / start_value
            return {
                'twr': simple_return,
                'twr_percent': simple_return * 100,
                'sub_periods': 1
            }
        
        # Sort cashflows by date and group by date (multiple cashflows on same day)
        sorted_cashflows = sorted(period_cashflows, key=lambda cf: cf.date)
        
        # Group cashflows by date
        cashflows_by_date = {}
        for cf in sorted_cashflows:
            cf_date = cf.date.date() if hasattr(cf.date, 'date') else cf.date
            if cf_date not in cashflows_by_date:
                cashflows_by_date[cf_date] = 0.0
            cashflows_by_date[cf_date] += float(cf.amount)
        
        # Get unique cashflow dates sorted
        cashflow_dates = sorted(cashflows_by_date.keys())
        
        logger.info(f"TWR: Processing {len(cashflow_dates)} cashflow dates between {start_date} and {end_date}")
        
        # Calculate sub-period returns
        sub_period_returns = []
        cumulative_twr = 1.0
        
        # Pre-fetch all unique dates needed for TWR sub-period calculations in one
        # efficient forward pass (single DB scan) instead of N separate queries.
        _twr_dates_needed = set()
        for cf_date in cashflow_dates:
            eval_date = cf_date - timedelta(days=1) if cf_date > start_date else start_date
            if eval_date < start_date:
                eval_date = start_date
            _twr_dates_needed.add(eval_date)
            _twr_dates_needed.add(cf_date)
        _twr_dates_needed.add(end_date)

        try:
            from services.forward_holding_calculation_service import get_client_portfolio_values_at_dates
            _prefetched = get_client_portfolio_values_at_dates(client_id, list(_twr_dates_needed))
        except Exception as _pf_err:
            logger.warning(f"TWR: multi-date prefetch failed ({_pf_err}), falling back to per-date queries")
            _prefetched = {}

        # Per-request cache for any date not covered by the pre-fetch
        _portfolio_cache = {}

        def _get_portfolio_value(date):
            if date in _prefetched:
                return _prefetched[date]
            if date not in _portfolio_cache:
                _portfolio_cache[date] = get_client_portfolio_by_date(client_id, date)
            return _portfolio_cache[date]

        # Start with portfolio value at start_date
        period_start_value = start_value
        period_start_date = start_date
        
        # Process each sub-period (between cashflows)
        for cf_date in cashflow_dates:
            # Get portfolio value just before this cashflow (on previous day if possible, else same day)
            # For TWR, we need value BEFORE the cashflow affects the portfolio
            eval_date = cf_date - timedelta(days=1) if cf_date > start_date else start_date
            if eval_date < start_date:
                eval_date = start_date
            
            portfolio_before_cf = _get_portfolio_value(eval_date)
            portfolio_value_before_cf = float(portfolio_before_cf.get('total_value', 0))
            
            # If we're evaluating on start_date, use the provided start_value
            if eval_date == start_date:
                portfolio_value_before_cf = start_value
            
            # Calculate return for sub-period ending at this cashflow
            # R = (Value_before_CF - Start_Value) / Start_Value
            cashflow_amount = cashflows_by_date[cf_date]
            
            # Get portfolio value ON the cashflow date (after cashflow) for next period's start
            portfolio_after_cf = _get_portfolio_value(cf_date)
            portfolio_value_after_cf = float(portfolio_after_cf.get('total_value', 0))
            
            # Expected value after cashflow: end value of previous period + cashflow
            # Note: In this system, negative cashflow = investment (INFLOW), positive = withdrawal (OUTFLOW)
            # For TWR: investment increases portfolio value, withdrawal decreases it
            # So we need to negate the cashflow amount: negative investment becomes positive addition
            expected_value_after_cf = portfolio_value_before_cf - cashflow_amount
            
            # Verify consistency: portfolio value on cashflow date should equal expected value
            # Allow small rounding differences (0.01)
            if abs(portfolio_value_after_cf - expected_value_after_cf) > 0.01:
                logger.warning(f"TWR: Portfolio value on {cf_date} ({portfolio_value_after_cf:,.2f}) "
                              f"does not match expected value ({expected_value_after_cf:,.2f}) = "
                              f"before_cf ({portfolio_value_before_cf:,.2f}) - cashflow ({cashflow_amount:,.2f}). "
                              f"Using expected value for TWR calculation consistency.")
                next_period_start_value = expected_value_after_cf
            else:
                # Use actual portfolio value (should match expected)
                next_period_start_value = portfolio_value_after_cf
            
            # Calculate return for this sub-period
            if period_start_value > 0:
                sub_period_return = (portfolio_value_before_cf - period_start_value) / period_start_value
                sub_period_returns.append({
                    'start_date': period_start_date,
                    'end_date': cf_date,
                    'start_value': period_start_value,
                    'end_value': portfolio_value_before_cf,
                    'cashflow': cashflow_amount,  # Cashflow amount for this sub-period
                    'next_period_start_value': next_period_start_value,  # Start value for next period (for verification)
                    'return': sub_period_return,
                    'return_percent': sub_period_return * 100
                })
                cumulative_twr *= (1 + sub_period_return)
                logger.debug(f"TWR Sub-period: {period_start_date} to {cf_date}, "
                           f"Start=₹{period_start_value:,.2f}, End=₹{portfolio_value_before_cf:,.2f}, "
                           f"Cashflow=₹{cashflow_amount:,.2f}, Next Start=₹{next_period_start_value:,.2f}, "
                           f"R={sub_period_return*100:.2f}%")
            
            # Update for next sub-period
            period_start_value = next_period_start_value
            period_start_date = cf_date
            logger.debug(f"TWR: Next period starts on {cf_date} with value ₹{period_start_value:,.2f} "
                        f"(verified: end of prev ₹{portfolio_value_before_cf:,.2f} - cashflow ₹{cashflow_amount:,.2f} = {expected_value_after_cf:,.2f})")
        
        # Final sub-period: from last cashflow to end_date
        if period_start_date < end_date:
            portfolio_at_end = _get_portfolio_value(end_date)
            portfolio_value_at_end = float(portfolio_at_end.get('total_value', 0))
            
            if period_start_value > 0:
                final_sub_period_return = (portfolio_value_at_end - period_start_value) / period_start_value
                sub_period_returns.append({
                    'start_date': period_start_date,
                    'end_date': end_date,
                    'start_value': period_start_value,
                    'end_value': portfolio_value_at_end,
                    'cashflow': 0.0,
                    'return': final_sub_period_return,
                    'return_percent': final_sub_period_return * 100
                })
                cumulative_twr *= (1 + final_sub_period_return)
                logger.debug(f"TWR Final sub-period: {period_start_date} to {end_date}, "
                           f"Start=₹{period_start_value:,.2f}, End=₹{portfolio_value_at_end:,.2f}, "
                           f"R={final_sub_period_return*100:.2f}%")
        
        # Final TWR
        twr = cumulative_twr - 1.0
        
        logger.info(f"TWR Calculation: {len(sub_period_returns)} sub-periods, Final TWR: {twr*100:.2f}%")
        
        return {
            'twr': twr,
            'twr_percent': twr * 100,
            'sub_periods': len(sub_period_returns),
            'sub_period_details': sub_period_returns
        }
        
    except Exception as e:
        logger.error(f"Error calculating TWR: {str(e)}", exc_info=True)
        return {
            'twr': 0.0,
            'twr_percent': 0.0,
            'error': str(e)
        }

def calculate_holdings_changes_in_period(client_id, start_date, end_date, start_portfolio, end_portfolio):
    """
    Calculate detailed holdings changes during the period using portfolio construction API data
    ✅ UPDATED: Now includes invested_amount for new and increased positions
    """
    # Build holdings maps from portfolio construction API data
    start_holdings = start_portfolio.get('holdings', [])
    end_holdings = end_portfolio.get('holdings', [])
    
    start_holdings_dict = {h['security_id']: h for h in start_holdings}
    end_holdings_dict = {h['security_id']: h for h in end_holdings}
    
    # Get all security IDs from both periods
    all_security_ids = set(start_holdings_dict.keys()) | set(end_holdings_dict.keys())
    
    # ✅ Get BUY and SELL transactions in period to calculate NET invested amounts
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    buy_transactions_in_period = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'BUY',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    sell_transactions_in_period = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'SELL',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    # ✅ Calculate NET investment (BUY - SELL) by security_id
    net_invested_by_security = {}
    for txn in buy_transactions_in_period:
        security_id = txn.security_id
        if security_id not in net_invested_by_security:
            net_invested_by_security[security_id] = 0.0
        net_invested_by_security[security_id] += float(txn.quantity) * float(txn.price)
    
    # Subtract SELL amounts to get net investment
    for txn in sell_transactions_in_period:
        security_id = txn.security_id
        if security_id not in net_invested_by_security:
            net_invested_by_security[security_id] = 0.0
        net_invested_by_security[security_id] -= float(txn.quantity) * float(txn.price)
    
    # Calculate changes
    new_positions = []
    exited_positions = []
    increased_positions = []
    decreased_positions = []
    
    # Find new positions (in end but not in start)
    for security_id in set(end_holdings_dict.keys()) - set(start_holdings_dict.keys()):
        end_holding = end_holdings_dict[security_id]
        # ✅ Get NET invested amount (BUY - SELL) for this security during the period
        net_invested_amount = net_invested_by_security.get(security_id, 0.0)
        end_val = float(end_holding.get('current_value') or 0)
        invested = max(0.0, float(net_invested_amount))
        mtm_gain = end_val - invested
        mtm_gain_percent = (mtm_gain / invested * 100.0) if invested > 0 else None
        new_positions.append({
            'symbol': end_holding.get('symbol', 'Unknown'),
            'name': end_holding.get('name', 'Unknown'),
            'quantity': end_holding.get('quantity', 0),
            'value': end_holding.get('current_value', 0),  # Keep for reference
            'invested_amount': invested,  # ✅ NET investment (only positive, as it's a new position)
            'mtm_gain': mtm_gain,
            'mtm_gain_percent': mtm_gain_percent,
        })
    
    # Find exited positions (in start but not in end)
    for security_id in set(start_holdings_dict.keys()) - set(end_holdings_dict.keys()):
        start_holding = start_holdings_dict[security_id]
        exited_positions.append({
            'symbol': start_holding.get('symbol', 'Unknown'),
            'name': start_holding.get('name', 'Unknown'),
            'quantity_sold': start_holding.get('quantity', 0),
            'value': start_holding.get('current_value', 0)
        })
    
    # Find changed positions (in both)
    for security_id in set(start_holdings_dict.keys()) & set(end_holdings_dict.keys()):
        start_holding = start_holdings_dict[security_id]
        end_holding = end_holdings_dict[security_id]
        
        start_qty = start_holding.get('quantity', 0)
        end_qty = end_holding.get('quantity', 0)
        change = end_qty - start_qty
        change_percent = (change / start_qty * 100) if start_qty > 0 else 0
        
        if abs(change) > 0.01:  # Any change in quantity
            # ✅ Get NET invested amount (BUY - SELL) for this security during the period
            net_invested_amount = net_invested_by_security.get(security_id, 0.0)
            
            change_data = {
                'symbol': end_holding.get('symbol', 'Unknown'),
                'name': end_holding.get('name', 'Unknown'),
                'start_quantity': start_qty,
                'current_quantity': end_qty,
                'change': change,
                'change_percent': change_percent,
                'start_value': start_holding.get('current_value', 0),
                'end_value': end_holding.get('current_value', 0),
                'invested_amount': max(0, net_invested_amount) if change > 0 else 0  # ✅ NET investment for increased positions (only positive)
            }
            
            if change > 0:
                increased_positions.append(change_data)
            else:
                decreased_positions.append(change_data)
    
    return {
        'new_positions': new_positions,
        'exited_positions': exited_positions,
        'increased_positions': increased_positions,
        'decreased_positions': decreased_positions,
        'summary': {
            'total_new': len(new_positions),
            'total_exited': len(exited_positions),
            'total_increased': len(increased_positions),
            'total_decreased': len(decreased_positions),
            'holdings_at_start': len(start_holdings_dict),
            'holdings_at_end': len(end_holdings_dict),
            'net_change': len(end_holdings_dict) - len(start_holdings_dict)
        }
    }

def calculate_period_cashflow_analysis(client_id, start_date, end_date):
    """
    Analyze cashflows during the period
    """
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    cashflows = Cashflow.query.filter(
        Cashflow.client_id == client_id,
        Cashflow.date >= start_datetime,
        Cashflow.date <= end_datetime
    ).order_by(Cashflow.date).all()
    
    total_inflows = 0
    total_outflows = 0
    inflow_count = 0
    outflow_count = 0
    cashflow_by_month = {}
    
    for cf in cashflows:
        amount = float(cf.amount)
        month_key = cf.date.strftime('%Y-%m')
        
        if month_key not in cashflow_by_month:
            cashflow_by_month[month_key] = {'inflows': 0, 'outflows': 0, 'net': 0}
        
        if amount < 0:  # Inflow (investment)
            total_inflows += abs(amount)
            inflow_count += 1
            cashflow_by_month[month_key]['inflows'] += abs(amount)
        else:  # Outflow (withdrawal)
            total_outflows += amount
            outflow_count += 1
            cashflow_by_month[month_key]['outflows'] += amount
        
        cashflow_by_month[month_key]['net'] += amount
    
    # Convert to list
    cashflow_timeline = [
        {
            'month': month,
            'inflows': data['inflows'],
            'outflows': data['outflows'],
            'net_cashflow': data['net']
        }
        for month, data in sorted(cashflow_by_month.items())
    ]
    
    net_cashflow = total_inflows - total_outflows
    
    # Calculate average monthly investment
    num_months = max(1, (end_date - start_date).days / 30.44)
    avg_monthly_investment = total_inflows / num_months if num_months > 0 else 0
    
    return {
        'total_inflows': total_inflows,
        'total_outflows': total_outflows,
        'net_cashflow': net_cashflow,
        'inflow_count': inflow_count,
        'outflow_count': outflow_count,
        'average_inflow': total_inflows / inflow_count if inflow_count > 0 else 0,
        'average_outflow': total_outflows / outflow_count if outflow_count > 0 else 0,
        'average_monthly_investment': avg_monthly_investment,
        'cashflow_timeline': cashflow_timeline,
        'cashflow_pattern': 'Net Investor' if net_cashflow < 0 else 'Net Withdrawer' if net_cashflow > 0 else 'Balanced'
    }

# ============================================================================
# VALUE ATTRIBUTION ANALYSIS - NEW ANALYTICS FUNCTIONS
# ============================================================================

def build_holdings_map(portfolio):
    """
    Build a holdings map from portfolio data for easy lookup
    """
    return {h['security_id']: h for h in portfolio['holdings']}

def compute_value_contribution(start_map, end_map, client_id, start_date, end_date, stocks_sold_data=None):
    """
    Compute value contribution for each security between start and end portfolios
    Returns list with symbol, start_value, end_value, gain, qty/price details
    
    Wealth Creators/Destroyers Logic:
    - Wealth Creator: Security that generated profit (realized or unrealized)
    - Wealth Destroyer: Security that generated loss (realized or unrealized)
    - For sold positions: gain = sell_value - cost_basis (from transactions)
    - For held positions: gain = current_value - cost_basis
    - For merged positions: gain = merger_consideration - cost_basis
    """
    contributions = []
    all_security_ids = set(start_map.keys()) | set(end_map.keys())
    
    # Create lookup for sold stocks data
    sold_stocks_lookup = {}
    if stocks_sold_data:
        for sold_stock in stocks_sold_data:
            sold_stocks_lookup[sold_stock['security_id']] = sold_stock
    
    # Add sold stocks to all_security_ids so they're included in analysis
    if stocks_sold_data:
        for sold_stock in stocks_sold_data:
            all_security_ids.add(sold_stock['security_id'])
    
    # Get transaction data for accurate P&L calculation
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    for security_id in all_security_ids:
        start_holding = start_map.get(security_id)
        end_holding = end_map.get(security_id)
        
        start_value = start_holding['current_value'] if start_holding else 0
        end_value = end_holding['current_value'] if end_holding else 0
        
        
        # ✅ Calculate gain using adjusted prices for accurate comparison (adjusted for corporate actions)
        from services.price_service import PriceService
        
        # Get adjusted prices at start and end dates
        start_price_adjusted = 0
        end_price_adjusted = 0
        
        if start_holding:
            start_price_data = PriceService.get_price(security_id, start_date, use_adjusted=True, allow_fallback=True)
            start_price_adjusted = start_price_data.price if start_price_data and start_price_data.is_valid else start_holding.get('current_price', 0)
        
        if end_holding:
            end_price_data = PriceService.get_price(security_id, end_date, use_adjusted=True, allow_fallback=True)
            end_price_adjusted = end_price_data.price if end_price_data and end_price_data.is_valid else end_holding.get('current_price', 0)
        
        # Calculate new investments and sells during the period for this stock
        new_investments = 0
        sells_value = 0
        if client_id and start_date and end_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            end_datetime = datetime.combine(end_date, datetime.max.time())
            
            # Get BUY transactions (new investments)
            buy_txns = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.type == 'BUY',
                Transaction.transaction_date >= start_datetime,
                Transaction.transaction_date <= end_datetime
            ).all()
            new_investments = sum(float(t.quantity) * float(t.price) for t in buy_txns)
            
            # Get SELL transactions (withdrawals)
            sell_txns = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.type == 'SELL',
                Transaction.transaction_date >= start_datetime,
                Transaction.transaction_date <= end_datetime
            ).all()
            sells_value = sum(float(t.quantity) * float(t.price) for t in sell_txns)
        
        # Calculate gain based on actual profit/loss logic (using adjusted prices)
        if start_holding and end_holding:
            # Position held throughout period - unrealized gain/loss
            # Use average cost basis for more accurate calculation
            avg_price = end_holding.get('average_price', 0)
            if avg_price > 0:
                cost_basis = end_holding['quantity'] * avg_price
                # Use adjusted end price for current value
                current_value_adjusted = end_holding['quantity'] * end_price_adjusted if end_price_adjusted > 0 else end_value
                raw_gain = current_value_adjusted - cost_basis
            else:
                # Fallback: use adjusted prices for comparison
                start_value_adjusted = start_holding['quantity'] * start_price_adjusted if start_price_adjusted > 0 else start_value
                end_value_adjusted = end_holding['quantity'] * end_price_adjusted if end_price_adjusted > 0 else end_value
                raw_gain = end_value_adjusted - start_value_adjusted
            
            # Adjust gain: subtract new investments to get true value added from price appreciation
            # Formula: adjusted_gain = change_in_value - new_investments + sells
            # (sells are added back because they reduce end_value but we want true price performance)
            gain = raw_gain - new_investments + sells_value
        elif start_holding and not end_holding:
            # Position sold during period - use realized gain from stocks_sold analysis
            if security_id in sold_stocks_lookup:
                sold_data = sold_stocks_lookup[security_id]
                raw_gain = sold_data.get('realized_gain', 0)
            else:
                # Fallback to transaction-based calculation
                raw_gain = calculate_realized_gain_from_transactions(client_id, security_id, start_date, end_date)
            
            # For sold positions: adjusted_gain = realized_gain - new_investments + sells
            # (sells are already accounted in realized_gain, but we add them back to see true loss)
            # Actually, for sold positions, we want: realized_gain - any new investments made before selling
            gain = raw_gain - new_investments + sells_value
        elif not start_holding and not end_holding and security_id in sold_stocks_lookup:
            # Position fully sold during period (not in start or end holdings)
            sold_data = sold_stocks_lookup[security_id]
            raw_gain = sold_data.get('realized_gain', 0)
            # Adjust: realized_gain - new_investments + sells
            gain = raw_gain - new_investments + sells_value
            # Use sold stock data for other fields
            start_value = sold_data.get('total_invested', 0)
            end_value = sold_data.get('total_proceeds', 0)
        elif not start_holding and end_holding:
            # New position - unrealized gain/loss from purchase price
            avg_price = end_holding.get('average_price', 0)
            if avg_price > 0:
                cost_basis = end_holding['quantity'] * avg_price
                # Use adjusted end price for current value
                current_value_adjusted = end_holding['quantity'] * end_price_adjusted if end_price_adjusted > 0 else end_value
                raw_gain = current_value_adjusted - cost_basis
            else:
                # Fallback: for new positions, if no avg_price, gain is just price appreciation
                # end_value already includes new_investments, so raw_gain = end_value - new_investments (price appreciation only)
                raw_gain = end_value - new_investments if new_investments > 0 else 0
            
            # For new positions: adjusted_gain = price_appreciation - new_investments + sells
            # Since raw_gain already excludes new_investments, we just add sells back
            gain = raw_gain + sells_value
        else:
            # Neither start nor end - shouldn't happen
            gain = 0
        
        # Get symbol, name, sector from available data
        if end_holding:
            symbol = end_holding['symbol']
            name = end_holding.get('name', 'Unknown')
            sector = end_holding.get('sector', 'Unknown')
        elif start_holding:
            symbol = start_holding['symbol']
            name = start_holding.get('name', 'Unknown')
            sector = start_holding.get('sector', 'Unknown')
        elif security_id in sold_stocks_lookup:
            sold_data = sold_stocks_lookup[security_id]
            symbol = sold_data.get('symbol', 'Unknown')
            name = sold_data.get('name', 'Unknown')
            sector = 'Unknown'  # Sold stocks don't have sector info
        else:
            symbol = 'Unknown'
            name = 'Unknown'
            sector = 'Unknown'
        
        contributions.append({
            'security_id': security_id,
            'symbol': symbol,
            'name': name,
            'sector': sector,
            'start_value': start_value,
            'end_value': end_value,
            'gain': gain,  # Adjusted gain (after subtracting new investments and adding sells)
            'raw_gain': gain + new_investments - sells_value,  # Original gain before adjustment (for reference)
            'new_investments': new_investments,  # New investments during period
            'sells_value': sells_value,  # Sells during period
            'gain_percent': (gain / start_value * 100) if start_value > 0 else 0,
            'start_quantity': start_holding['quantity'] if start_holding else 0,
            'end_quantity': end_holding['quantity'] if end_holding else 0,
            'start_price': start_price_adjusted if start_holding else 0,  # ✅ Use adjusted price
            'end_price': end_price_adjusted if end_holding else 0  # ✅ Use adjusted price
        })
    
    return contributions


def compute_profit_change_contribution(start_map, end_map, stocks_sold_data=None):
    """
    Compute profit-change contribution for each security between start and end portfolios.
    
    ✅ UPDATED: This now matches EXACTLY the Portfolio Holdings Comparison calculation:
        change_unrealized_pnl = end_unrealized_pnl - start_unrealized_pnl
    
    For exited/matured securities, Portfolio Holdings Comparison uses:
        change_unrealized_pnl = exit_value - start_current_value
    
    This ensures Wealth Creators/Destroyers matches the delta PNL shown in Portfolio Holdings Comparison.

    Where:
      - unrealized_pnl(date) = current_value - total_cost (from portfolio construction snapshot)
      - For sold securities: exit_value is the sell proceeds (from stocks_sold_data)

    Note:
      - This intentionally does NOT adjust for new investments / sells value as cashflows.
      - This is purely a "change in profit" view, matching the holdings comparison logic EXACTLY.
    """
    sold_lookup = {}
    if stocks_sold_data:
        for s in stocks_sold_data:
            sid = s.get('security_id')
            if sid is not None:
                sold_lookup[sid] = s

    all_security_ids = set(start_map.keys()) | set(end_map.keys()) | set(sold_lookup.keys())
    contributions = []

    for security_id in all_security_ids:
        start_holding = start_map.get(security_id)
        end_holding = end_map.get(security_id)
        sold_data = sold_lookup.get(security_id, {})

        start_current_value = float(start_holding.get('current_value', 0)) if start_holding else 0.0
        start_total_cost = float(start_holding.get('total_cost', 0)) if start_holding else 0.0
        start_unrealized_pnl = start_current_value - start_total_cost

        end_current_value = float(end_holding.get('current_value', 0)) if end_holding else 0.0
        end_total_cost = float(end_holding.get('total_cost', 0)) if end_holding else 0.0
        end_unrealized_pnl = end_current_value - end_total_cost

        # ✅ Match Portfolio Holdings Comparison logic EXACTLY
        # Portfolio Holdings Comparison uses: change_unrealized_pnl = end_unrealized_pnl - start_unrealized_pnl
        # For fully exited securities: change_unrealized_pnl = exit_value - start_current_value
        # For partially sold securities: change_unrealized_pnl = end_unrealized_pnl - start_unrealized_pnl
        #   (The realized gain from partial sale is NOT added separately - it's already reflected in the reduced end_unrealized_pnl)
        
        start_qty = float(start_holding.get('quantity', 0)) if start_holding else 0.0
        end_qty = float(end_holding.get('quantity', 0)) if end_holding else 0.0
        
        if start_qty > 0 and end_qty == 0 and sold_data:
            # Security was fully sold/exited during period
            # Match Portfolio Holdings Comparison: exit_value - start_current_value
            exit_value = float(sold_data.get('total_proceeds', 0) or 0.0)
            gain = exit_value - start_current_value
        else:
            # Security held throughout period (or partially sold, or new position)
            # Match Portfolio Holdings Comparison: end_unrealized_pnl - start_unrealized_pnl
            # Note: For partially sold securities, the realized gain is NOT added separately
            # because Portfolio Holdings Comparison only shows change in unrealized PNL of remaining holdings
            gain = end_unrealized_pnl - start_unrealized_pnl

        symbol = (end_holding or start_holding or sold_data or {}).get('symbol', 'Unknown')
        name = (end_holding or start_holding or sold_data or {}).get('name', 'Unknown')
        sector = (end_holding or start_holding or {}).get('sector', 'Unknown')

        contributions.append({
            'security_id': security_id,
            'symbol': symbol,
            'name': name,
            'sector': sector,
            # calculate_wealth_creators_destroyers expects 'gain'
            # This now matches Portfolio Holdings Comparison change.unrealized_pnl EXACTLY
            'gain': gain,
            # Optional fields (useful for UI/debug; harmless if unused)
            'unrealized_pnl_change': end_unrealized_pnl - start_unrealized_pnl,
            'realized_gain_in_period': float(sold_data.get('realized_gain', 0) or 0.0) if sold_data else 0.0
        })

    return contributions

def calculate_realized_gain_from_transactions(client_id, security_id, start_date, end_date):
    """
    Calculate realized gain/loss for a sold position based on actual transactions
    """
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get all transactions for this security in the period
    transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.security_id == security_id,
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).order_by(Transaction.transaction_date).all()
    
    if not transactions:
        return 0
    
    # Calculate realized P&L using FIFO method
    buy_queue = []  # Queue of (quantity, price, date) tuples
    total_realized_gain = 0
    
    for txn in transactions:
        qty = float(txn.quantity)
        price = float(txn.price)
        
        if txn.type == 'BUY':
            buy_queue.append((qty, price, txn.transaction_date))
        elif txn.type == 'SELL':
            remaining_sell_qty = qty
            
            while remaining_sell_qty > 0 and buy_queue:
                buy_qty, buy_price, buy_date = buy_queue[0]
                
                if buy_qty <= remaining_sell_qty:
                    # Sell entire buy lot
                    realized_gain = (price - buy_price) * buy_qty
                    total_realized_gain += realized_gain
                    remaining_sell_qty -= buy_qty
                    buy_queue.pop(0)
                else:
                    # Sell partial buy lot
                    realized_gain = (price - buy_price) * remaining_sell_qty
                    total_realized_gain += realized_gain
                    buy_queue[0] = (buy_qty - remaining_sell_qty, buy_price, buy_date)
                    remaining_sell_qty = 0
    
    return total_realized_gain

def calculate_wealth_creators_destroyers(contrib_list):
    """
    Calculate wealth creators and destroyers from contribution list
    
    ✅ UPDATED: Wealth destroyers only include losses (realized or unrealized)
    - Uses adjusted prices (accounting for corporate actions)
    - Only negative gains (losses) are included in destroyers
    """
    # Sort by gain (highest first for creators, lowest first for destroyers)
    # ✅ Only include losses (gain < 0) in destroyers
    creators = sorted([c for c in contrib_list if c['gain'] > 0], key=lambda x: x['gain'], reverse=True)
    destroyers = sorted([c for c in contrib_list if c['gain'] < 0], key=lambda x: x['gain'])  # Lowest (most negative) first
    
    return {
        'wealth_creators': creators[:10],  # Top 10 creators
        'wealth_destroyers': destroyers[:10],  # Top 10 destroyers (only losses, adjusted for corporate actions)
        'total_wealth_created': sum(c['gain'] for c in creators),
        'total_wealth_destroyed': sum(c['gain'] for c in destroyers)  # Sum of losses (will be negative)
    }

def calculate_mtm_trades_profitability(client_id, start_date, end_date, end_holdings_map=None):
    """
    Calculate most and least profitable MTM trades in the period
    
    ✅ UPDATED (per requirement): Group by security (not trade-wise) and calculate:
      - avg_buy_price (adjusted to end_date basis) vs current_price (end_date adjusted)
      - MTM profit % and ₹ based on aggregated quantity bought in the period
    
    ✅ FIXED: Now adjusts quantities and prices for corporate actions (splits, bonuses)
    ✅ Uses standard APIs:
        - PriceService.get_price() with use_adjusted=True for adjusted prices
        - quantity_adjustment_service for quantity adjustments
    
    Returns:
        - most_profitable_mtm_trades: Top securities by MTM profit % (positive)
        - least_profitable_mtm_trades: Worst securities by MTM profit % (negative)
        - all_mtm_trades: Every BUY in period, trade-wise (same MTM logic as above)
    """
    # ✅ Use standard APIs for corporate action adjustments
    from models import Transaction, Security, CorporateAction
    from services.price_service import PriceService
    from services.quantity_adjustment_service import (
        get_corporate_actions_after_date,
        calculate_quantity_adjustment_factor
    )
    
    logger.info(f"Calculating MTM trades profitability for period {start_date} to {end_date}")
    
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get all corporate actions for quantity adjustments (used by standard APIs)
    all_corporate_actions = CorporateAction.query.filter_by(is_active=True).all()
    
    # Get all BUY transactions in the period (we aggregate by security)
    buy_transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'BUY',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).order_by(Transaction.transaction_date).all()

    end_price_cache = {}

    def _end_price_for_security(security_id):
        if security_id in end_price_cache:
            return end_price_cache[security_id]

        current_price_data = PriceService.get_price(
            security_id, end_date, use_adjusted=True, allow_fallback=True
        )
        current_price = (
            float(current_price_data.price)
            if current_price_data and current_price_data.is_valid and current_price_data.price
            else 0.0
        )

        if current_price == 0 and end_holdings_map:
            end_holding = end_holdings_map.get(security_id)
            if end_holding and float(end_holding.get('current_price', 0) or 0) > 0:
                current_price = float(end_holding.get('current_price', 0))

        if current_price == 0:
            try:
                sec = Security.query.get(security_id)
                if sec and getattr(sec, 'current_price', None):
                    current_price = float(sec.current_price)
            except Exception:
                pass

        end_price_cache[security_id] = current_price
        return current_price

    # Aggregate buys by security; also collect trade-wise rows
    per_security = {}
    all_mtm_trades = []
    for buy_txn in buy_transactions:
        if not buy_txn.security:
            continue

        security_id = buy_txn.security_id
        buy_date = buy_txn.transaction_date.date() if hasattr(buy_txn.transaction_date, 'date') else buy_txn.transaction_date

        buy_price_original = float(buy_txn.price)
        buy_quantity_original = float(buy_txn.quantity)

        actions_after_buy = get_corporate_actions_after_date(
            security_id, buy_date, end_date, all_corporate_actions
        )
        adjustment_factor = calculate_quantity_adjustment_factor(actions_after_buy)

        buy_qty_adjusted = buy_quantity_original * adjustment_factor
        buy_price_adjusted = buy_price_original / adjustment_factor if adjustment_factor > 0 else buy_price_original

        buy_value = buy_qty_adjusted * buy_price_adjusted  # equals original buy cost

        entry = per_security.get(security_id)
        if not entry:
            entry = {
                'security_id': security_id,
                'symbol': buy_txn.security.symbol if buy_txn.security else 'Unknown',
                'name': buy_txn.security.name if buy_txn.security else 'Unknown',
                'total_qty_adjusted': 0.0,
                'total_invested': 0.0,
                'first_buy_date': buy_date,
                'last_buy_date': buy_date,
                'trades_count': 0
            }
            per_security[security_id] = entry

        entry['total_qty_adjusted'] += buy_qty_adjusted
        entry['total_invested'] += buy_value
        entry['trades_count'] += 1
        entry['first_buy_date'] = min(entry['first_buy_date'], buy_date)
        entry['last_buy_date'] = max(entry['last_buy_date'], buy_date)

        current_price = _end_price_for_security(security_id)
        if current_price > 0 and buy_price_adjusted > 0 and buy_qty_adjusted > 0:
            trade_mtm = (current_price - buy_price_adjusted) * buy_qty_adjusted
            trade_mtm_pct = ((current_price - buy_price_adjusted) / buy_price_adjusted) * 100.0
            trade_date_str = buy_date.isoformat() if hasattr(buy_date, 'isoformat') else str(buy_date)
            all_mtm_trades.append({
                'trade_date': trade_date_str,
                'trade_type': 'BUY',
                'symbol': buy_txn.security.symbol if buy_txn.security else 'Unknown',
                'name': buy_txn.security.name if buy_txn.security else 'Unknown',
                'quantity': buy_qty_adjusted,
                'avg_buy_price': buy_price_adjusted,
                'current_price': current_price,
                'trade_value': buy_value,
                'mtm_profit': round(trade_mtm, 2),
                'mtm_profit_percent': round(trade_mtm_pct, 2),
                'transaction_id': int(buy_txn.id) if buy_txn.id else None,
            })

    # Build aggregated MTM rows
    mtm_rows = []
    for security_id, data in per_security.items():
        qty = data['total_qty_adjusted']
        invested = data['total_invested']
        if qty <= 0:
            continue

        avg_buy_price = invested / qty if qty > 0 else 0.0
        current_price = _end_price_for_security(security_id)

        if current_price <= 0 or avg_buy_price <= 0:
            continue

        current_value = current_price * qty
        mtm_profit = current_value - invested
        mtm_profit_percent = ((current_price - avg_buy_price) / avg_buy_price) * 100 if avg_buy_price > 0 else 0.0

        mtm_rows.append({
            'security_id': security_id,
            'symbol': data.get('symbol', 'Unknown'),
            'name': data.get('name', 'Unknown'),
            'quantity': qty,
            'avg_buy_price': avg_buy_price,
            'current_price': current_price,
            'current_value': current_value,
            'trade_value': invested,
            'mtm_profit': mtm_profit,
            'mtm_profit_percent': mtm_profit_percent,
            'trades_count': data.get('trades_count', 0),
            'first_buy_date': data.get('first_buy_date').isoformat() if hasattr(data.get('first_buy_date'), 'isoformat') else str(data.get('first_buy_date')),
            'last_buy_date': data.get('last_buy_date').isoformat() if hasattr(data.get('last_buy_date'), 'isoformat') else str(data.get('last_buy_date')),
        })

    # Sort by MTM profit %
    mtm_rows.sort(key=lambda x: x['mtm_profit_percent'], reverse=True)
    all_mtm_trades.sort(key=lambda x: (x.get('trade_date') or '', x.get('symbol') or ''))

    most_profitable = [t for t in mtm_rows if t['mtm_profit_percent'] > 0][:20]
    least_profitable = sorted([t for t in mtm_rows if t['mtm_profit_percent'] < 0], key=lambda x: x['mtm_profit_percent'])[:20]

    return {
        'most_profitable_mtm_trades': most_profitable,
        'least_profitable_mtm_trades': least_profitable,
        'all_mtm_trades': all_mtm_trades,
        'total_trades_analyzed': len(buy_transactions),
        'total_securities_analyzed': len(mtm_rows)
    }

def calculate_stocks_most_bought(client_id, start_date, end_date, end_map):
    """
    Calculate most bought stocks in period with returns
    ✅ FIXED: Now adjusts quantities and prices for corporate actions (splits, bonuses)
    """
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get all BUY transactions in period
    buy_transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'BUY',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    # Get all corporate actions for quantity adjustments
    from models import CorporateAction
    from services.quantity_adjustment_service import (
        get_corporate_actions_after_date,
        calculate_quantity_adjustment_factor
    )
    
    all_corporate_actions = CorporateAction.query.filter_by(is_active=True).all()
    
    # Group by security and adjust for corporate actions
    security_buys = {}
    for txn in buy_transactions:
        security_id = txn.security_id
        txn_date = txn.transaction_date.date() if hasattr(txn.transaction_date, 'date') else txn.transaction_date
        
        if security_id not in security_buys:
            security_buys[security_id] = {
                'total_qty': 0,
                'total_qty_adjusted': 0,  # Adjusted for corporate actions
                'total_invested': 0,
                'transactions': []
            }
        
        # Original transaction data
        qty_original = float(txn.quantity)
        price_original = float(txn.price)
        
        # ✅ Use standard API: get_corporate_actions_after_date() to find actions between txn_date and end_date
        actions_after_txn = get_corporate_actions_after_date(
            security_id, txn_date, end_date, all_corporate_actions
        )
        
        # ✅ Use standard API: calculate_quantity_adjustment_factor() to get adjustment factor (e.g., 2:1 split = factor of 2)
        adjustment_factor = calculate_quantity_adjustment_factor(actions_after_txn)
        
        # ✅ Adjust quantity: if we bought 100 shares and there was a 2:1 split, we now have 200
        qty_adjusted = qty_original * adjustment_factor
        
        # ✅ Adjust buy price: if there was a 2:1 split, the effective buy price is halved
        # This ensures the cost basis calculation is correct
        price_adjusted = price_original / adjustment_factor if adjustment_factor > 0 else price_original
        
        # Total invested remains the same (qty * price = adjusted_qty * adjusted_price)
        invested = qty_adjusted * price_adjusted
        
        security_buys[security_id]['total_qty'] += qty_original  # Keep original for reference
        security_buys[security_id]['total_qty_adjusted'] += qty_adjusted  # Use adjusted for calculations
        security_buys[security_id]['total_invested'] += invested
        security_buys[security_id]['transactions'].append({
            'date': txn_date,
            'qty': qty_original,
            'qty_adjusted': qty_adjusted,
            'price': price_original,
            'price_adjusted': price_adjusted,
            'invested': invested,
            'adjustment_factor': adjustment_factor
        })
    
    # Calculate returns for each security
    # ✅ Use standard API: PriceService.get_price() with use_adjusted=True for adjusted prices
    from models import Security
    from services.price_service import PriceService
    
    most_bought = []
    for security_id, data in security_buys.items():
        security = Security.query.get(security_id)
        if not security:
            continue
        
        # ✅ Standard API: Get adjusted price at end_date (accounts for corporate actions)
        price_data = PriceService.get_price(security_id, end_date, use_adjusted=True, allow_fallback=True)
        current_price = price_data.price if price_data and price_data.is_valid else 0
        
        # Fallback to end_holding price if PriceService returns 0
        if current_price == 0:
            end_holding = end_map.get(security_id)
            if end_holding:
                current_price = end_holding.get('current_price', 0)
        
        # Fallback to security current_price
        if current_price == 0 and security:
            current_price = float(security.current_price) if security.current_price else 0
        
        # ✅ Calculate current value using ADJUSTED quantity (accounts for splits/bonuses)
        # This gives us MTM gains for the bought position, accounting for corporate actions
        current_value = data['total_qty_adjusted'] * current_price
        
        # Calculate MTM gain for bought position: (current value - invested value)
        mtm_gain = current_value - data['total_invested']
        
        # Return % = (MTM gain / invested value in this period) * 100
        return_percent = (mtm_gain / data['total_invested'] * 100) if data['total_invested'] > 0 else 0
        
        # ✅ Calculate average buy price using adjusted values
        avg_buy_price = data['total_invested'] / data['total_qty_adjusted'] if data['total_qty_adjusted'] > 0 else 0
        
        most_bought.append({
            'security_id': security_id,
            'symbol': security.symbol,
            'name': security.name,
            'total_qty': data['total_qty_adjusted'],  # Return adjusted quantity
            'total_invested': data['total_invested'],
            'avg_buy_price': avg_buy_price,  # Adjusted average buy price
            'current_price': current_price,
            'current_value': current_value,
            'unrealized_pnl': mtm_gain,  # MTM gain for bought position
            'mtm_gain': mtm_gain,  # Alias for clarity
            'return_percent': return_percent,
            'transaction_count': len(data['transactions'])
        })
    
    # Sort by total invested amount
    most_bought.sort(key=lambda x: x['total_invested'], reverse=True)
    
    return most_bought[:10]  # Top 10 most bought

def calculate_previous_period_most_bought_performance(client_id, prev_period_start, prev_period_end, current_period_start, current_period_end, current_end_map):
    """
    Calculate performance of stocks that were most bought in previous period (prev_period_start to prev_period_end)
    and how they performed in current period (current_period_start to current_period_end)
    
    Args:
        client_id: Client ID
        prev_period_start: Start date of previous period
        prev_period_end: End date of previous period (should be same as current_period_start)
        current_period_start: Start date of current period
        current_period_end: End date of current period
        current_end_map: Holdings map at end of current period
    
    Returns:
        List of stocks with their previous period buying data and current period performance
    """
    try:
        from models import Security
        from services.price_service import PriceService
        
        # Step 1: Find stocks most bought in previous period (prev_period_start to prev_period_end)
        prev_start_datetime = datetime.combine(prev_period_start, datetime.min.time())
        prev_end_datetime = datetime.combine(prev_period_end, datetime.max.time())
        
        prev_buy_transactions = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.type == 'BUY',
            Transaction.transaction_date >= prev_start_datetime,
            Transaction.transaction_date <= prev_end_datetime
        ).all()
        
        # Group by security
        prev_security_buys = {}
        for txn in prev_buy_transactions:
            security_id = txn.security_id
            if security_id not in prev_security_buys:
                prev_security_buys[security_id] = {
                    'total_qty': 0,
                    'total_invested': 0,
                    'transactions': []
                }
            
            qty = float(txn.quantity)
            price = float(txn.price)
            invested = qty * price
            
            prev_security_buys[security_id]['total_qty'] += qty
            prev_security_buys[security_id]['total_invested'] += invested
            prev_security_buys[security_id]['transactions'].append({
                'date': txn.transaction_date.date(),
                'qty': qty,
                'price': price,
                'invested': invested
            })
        
        if not prev_security_buys:
            return []
        
        # Step 2: Calculate how these stocks performed in current period
        # Get price at start of current period and end of current period (adjusted prices)
        performance_data = []
        
        for security_id, prev_data in prev_security_buys.items():
            security = Security.query.get(security_id)
            if not security:
                continue
            
            # Get prices at start and end of current period (adjusted)
            start_price_data = PriceService.get_price(security_id, current_period_start, use_adjusted=True, allow_fallback=True)
            end_price_data = PriceService.get_price(security_id, current_period_end, use_adjusted=True, allow_fallback=True)
            
            start_price_adjusted = start_price_data.price if start_price_data and start_price_data.is_valid else 0
            end_price_adjusted = end_price_data.price if end_price_data and end_price_data.is_valid else 0
            
            # Fallback to holdings price if PriceService returns 0
            if start_price_adjusted == 0:
                # Try to get from portfolio reconstruction
                start_portfolio = get_client_portfolio_by_date(client_id, current_period_start)
                start_holding = next((h for h in start_portfolio.get('holdings', []) if h.get('security_id') == security_id), None)
                if start_holding:
                    start_price_adjusted = start_holding.get('current_price', 0)
            
            if end_price_adjusted == 0:
                current_holding = current_end_map.get(security_id)
                if current_holding:
                    end_price_adjusted = current_holding.get('current_price', 0)
            
            # Calculate value at start of current period (using quantity bought in previous period)
            quantity_bought_prev = prev_data['total_qty']
            value_at_current_start = quantity_bought_prev * start_price_adjusted
            value_at_current_end = quantity_bought_prev * end_price_adjusted
            
            # Calculate performance in current period
            period_gain = value_at_current_end - value_at_current_start
            period_gain_percent = (period_gain / value_at_current_start * 100) if value_at_current_start > 0 else 0
            
            # Calculate total gain from original investment
            total_gain = value_at_current_end - prev_data['total_invested']
            total_gain_percent = (total_gain / prev_data['total_invested'] * 100) if prev_data['total_invested'] > 0 else 0
            
            performance_data.append({
                'security_id': security_id,
                'symbol': security.symbol,
                'name': security.name,
                'prev_period_qty_bought': quantity_bought_prev,
                'prev_period_avg_buy_price': prev_data['total_invested'] / quantity_bought_prev if quantity_bought_prev > 0 else 0,
                'prev_period_investment': prev_data['total_invested'],
                'current_period_start_price': start_price_adjusted,
                'current_period_end_price': end_price_adjusted,
                'current_period_start_value': value_at_current_start,
                'current_period_end_value': value_at_current_end,
                'current_period_gain': period_gain,
                'current_period_gain_percent': period_gain_percent,
                'total_gain_from_original': total_gain,
                'total_gain_percent_from_original': total_gain_percent
            })
        
        # Sort by previous period investment amount (most invested first)
        performance_data.sort(key=lambda x: x['prev_period_investment'], reverse=True)
        
        return performance_data[:10]  # Top 10
        
    except Exception as e:
        logger.error(f"Error calculating previous period most bought performance: {str(e)}", exc_info=True)
        return []


def calculate_previous_period_stocks_most_bought(client_id, prev_period_start, prev_period_end, current_end_date, current_end_map):
    """
    Previous period version of "Stocks Most Bought", with MTM performance measured as-of current_end_date.

    - Universe: BUY transactions in [prev_period_start, prev_period_end]
    - Adjust quantities/prices for corporate actions from txn date -> current_end_date
    - Compute MTM using adjusted current_end_date price (PriceService use_adjusted=True)
    """
    try:
        from models import CorporateAction, Security
        from services.price_service import PriceService
        from services.quantity_adjustment_service import (
            get_corporate_actions_after_date,
            calculate_quantity_adjustment_factor
        )

        prev_start_datetime = datetime.combine(prev_period_start, datetime.min.time())
        prev_end_datetime = datetime.combine(prev_period_end, datetime.max.time())

        buy_transactions = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.type == 'BUY',
            Transaction.transaction_date >= prev_start_datetime,
            Transaction.transaction_date <= prev_end_datetime
        ).all()

        if not buy_transactions:
            return []

        all_corporate_actions = CorporateAction.query.filter_by(is_active=True).all()

        # Group by security (in current_end_date basis)
        security_buys = {}
        for txn in buy_transactions:
            security_id = txn.security_id
            txn_date = txn.transaction_date.date() if hasattr(txn.transaction_date, 'date') else txn.transaction_date

            if security_id not in security_buys:
                security_buys[security_id] = {
                    'total_qty_adjusted': 0.0,
                    'total_invested': 0.0,
                    'transactions': []
                }

            qty_original = float(txn.quantity)
            price_original = float(txn.price)

            actions_after_txn = get_corporate_actions_after_date(
                security_id, txn_date, current_end_date, all_corporate_actions
            )
            factor = calculate_quantity_adjustment_factor(actions_after_txn)

            qty_adjusted = qty_original * factor
            price_adjusted = price_original / factor if factor > 0 else price_original

            invested = qty_adjusted * price_adjusted

            security_buys[security_id]['total_qty_adjusted'] += qty_adjusted
            security_buys[security_id]['total_invested'] += invested
            security_buys[security_id]['transactions'].append({
                'date': txn_date,
                'qty_original': qty_original,
                'price_original': price_original,
                'qty_adjusted': qty_adjusted,
                'price_adjusted': price_adjusted,
                'invested': invested
            })

        results = []
        for security_id, data in security_buys.items():
            if not data['transactions'] or data['total_qty_adjusted'] <= 0:
                continue
            sec_obj = Security.query.get(security_id)
            if not sec_obj:
                continue

            price_data = PriceService.get_price(security_id, current_end_date, use_adjusted=True, allow_fallback=True)
            current_price = price_data.price if price_data and price_data.is_valid else 0

            if current_price == 0 and current_end_map:
                holding = current_end_map.get(security_id)
                if holding and holding.get('current_price', 0) > 0:
                    current_price = float(holding.get('current_price', 0))

            if current_price == 0 and getattr(sec_obj, 'current_price', None):
                current_price = float(sec_obj.current_price)

            qty = data['total_qty_adjusted']
            total_invested = data['total_invested']
            avg_buy_price = total_invested / qty if qty > 0 else 0.0
            current_value = current_price * qty if current_price else 0.0
            mtm_gain = current_value - total_invested
            return_percent = (mtm_gain / total_invested * 100) if total_invested > 0 else 0.0

            results.append({
                'security_id': security_id,
                'symbol': sec_obj.symbol,
                'name': sec_obj.name,
                'total_qty': qty,
                'total_invested': total_invested,
                'avg_buy_price': avg_buy_price,
                'current_price': current_price,
                'current_value': current_value,
                'unrealized_pnl': mtm_gain,
                'mtm_gain': mtm_gain,
                'return_percent': return_percent,
                'transaction_count': len(data['transactions'])
            })

        results.sort(key=lambda x: x.get('total_invested', 0), reverse=True)
        return results[:10]

    except Exception as e:
        logger.error(f"Error calculating previous period stocks most bought: {str(e)}", exc_info=True)
        return []


def calculate_previous_period_stocks_sold_analysis(client_id, prev_period_start, prev_period_end, current_end_date):
    """
    Previous period version of "Stocks Sold", with current_price / mtm_on_selling computed as-of current_end_date.

    Uses calculate_stocks_sold_analysis(prev_period_start, prev_period_end) for FIFO realized gain (already CA-adjusted),
    then recomputes:
      - current_price (as of current_end_date, adjusted)
      - mtm_on_selling = (avg_sell_price - current_price) * qty_sold
    """
    try:
        from models import Security
        from services.price_service import PriceService

        sold_rows = calculate_stocks_sold_analysis(client_id, prev_period_start, prev_period_end) or []
        if not sold_rows:
            return []

        for row in sold_rows:
            security_id = row.get('security_id')
            if not security_id:
                continue

            current_price = None
            try:
                price_data = PriceService.get_price(
                    security_id=security_id,
                    target_date=current_end_date,
                    use_adjusted=True,
                    allow_fallback=True
                )
                if price_data and price_data.price is not None:
                    current_price = float(price_data.price)
            except Exception:
                current_price = None

            if (current_price is None or current_price == 0) and row.get('symbol'):
                try:
                    sec_obj = Security.query.get(security_id)
                    if sec_obj and getattr(sec_obj, 'current_price', None):
                        current_price = float(sec_obj.current_price)
                except Exception:
                    pass

            row['current_price'] = current_price

            qty_sold = float(row.get('qty_sold', 0) or 0)
            avg_sell_price = float(row.get('avg_sell_price', 0) or 0)
            row['mtm_on_selling'] = (avg_sell_price - current_price) * qty_sold if current_price not in (None, 0) else None

        return sold_rows

    except Exception as e:
        logger.error(f"Error calculating previous period stocks sold analysis: {str(e)}", exc_info=True)
        return []

def calculate_stocks_sold_analysis(client_id, start_date, end_date):
    """
    Calculate sold stocks analysis with realized returns
    ✅ FIXED: Now adjusts BUY transaction quantities and prices for corporate actions
    when calculating cost basis for sold positions
    """
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get all corporate actions for quantity adjustments
    from models import CorporateAction
    from services.quantity_adjustment_service import (
        get_corporate_actions_after_date,
        calculate_quantity_adjustment_factor
    )
    
    all_corporate_actions = CorporateAction.query.filter_by(is_active=True).all()
    
    # Get all SELL transactions in period
    sell_transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'SELL',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    # Group by security
    security_sells = {}
    for txn in sell_transactions:
        security_id = txn.security_id
        if security_id not in security_sells:
            security_sells[security_id] = {
                'total_qty': 0,
                'total_proceeds': 0,
                'transactions': []
            }
        
        qty = float(txn.quantity)
        price = float(txn.price)
        proceeds = qty * price
        
        security_sells[security_id]['total_qty'] += qty
        security_sells[security_id]['total_proceeds'] += proceeds
        security_sells[security_id]['transactions'].append({
            'date': txn.transaction_date.date() if hasattr(txn.transaction_date, 'date') else txn.transaction_date,
            'qty': qty,
            'price': price,
            'proceeds': proceeds
        })
    
    # Calculate realized gains for each security
    stocks_sold = []
    for security_id, data in security_sells.items():
        # Get security info
        security = Security.query.get(security_id)
        if not security:
            continue
        
        # Calculate cost basis for the quantity sold in this period
        # Use FIFO: Match sells with buys in chronological order
        # ✅ FIXED: Adjust BUY transactions for corporate actions between buy and sell dates
        from collections import deque
        
        # Get all BUY transactions (chronologically ordered)
        all_buy_txns = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.security_id == security_id,
            Transaction.type == 'BUY'
        ).order_by(Transaction.transaction_date).all()
        
        # Create a queue of available buy lots (FIFO) with adjusted quantities
        buy_lots = deque()
        for buy_txn in all_buy_txns:
            buy_date = buy_txn.transaction_date.date() if hasattr(buy_txn.transaction_date, 'date') else buy_txn.transaction_date
            
            # Original buy transaction data
            buy_qty_original = float(buy_txn.quantity)
            buy_price_original = float(buy_txn.price)
            
            # Store original data for reference, but we'll adjust when matching with sells
            buy_lots.append({
                'date': buy_date,
                'quantity_original': buy_qty_original,
                'price_original': buy_price_original,
                'cost_original': buy_qty_original * buy_price_original,
                'quantity': buy_qty_original,  # Will be adjusted per sell
                'price': buy_price_original,  # Will be adjusted per sell
                'cost': buy_qty_original * buy_price_original  # Will be adjusted per sell
            })
        
        # Match sells with buys using FIFO
        total_cost_basis = 0.0
        total_matched_qty = 0.0
        
        # Sort sell transactions by date
        sorted_sells = sorted(data['transactions'], key=lambda x: x['date'])
        
        for sell_txn in sorted_sells:
            sell_date = sell_txn['date']
            qty_to_match = sell_txn['qty']
            
            while qty_to_match > 0 and buy_lots:
                buy_lot = buy_lots[0]
                buy_date = buy_lot['date']
                
                # ✅ Get corporate actions between buy date and sell date
                actions_between = get_corporate_actions_after_date(
                    security_id, buy_date, sell_date, all_corporate_actions
                )
                
                # ✅ Calculate adjustment factor
                adjustment_factor = calculate_quantity_adjustment_factor(actions_between)
                
                # ✅ Adjust quantity and price for this buy lot
                buy_qty_adjusted = buy_lot['quantity_original'] * adjustment_factor
                buy_price_adjusted = buy_lot['price_original'] / adjustment_factor if adjustment_factor > 0 else buy_lot['price_original']
                buy_cost_adjusted = buy_qty_adjusted * buy_price_adjusted
                
                available_qty = buy_qty_adjusted
                
                if available_qty <= qty_to_match:
                    # Use entire buy lot
                    matched_qty = available_qty
                    cost_for_this_match = buy_cost_adjusted
                    buy_lots.popleft()
                else:
                    # Use partial buy lot
                    matched_qty = qty_to_match
                    cost_for_this_match = (matched_qty / available_qty) * buy_cost_adjusted
                    # Update buy lot (using original values, will be recalculated for next sell)
                    buy_lot['quantity_original'] -= (matched_qty / adjustment_factor) if adjustment_factor > 0 else matched_qty
                    buy_lot['cost_original'] -= cost_for_this_match
                
                total_cost_basis += cost_for_this_match
                total_matched_qty += matched_qty
                qty_to_match -= matched_qty
        
        # If we couldn't match all sells (insufficient buy lots), use average price as fallback
        unmatched_qty = data['total_qty'] - total_matched_qty
        if unmatched_qty > 0 and len(all_buy_txns) > 0:
            # ✅ Calculate average buy price using adjusted values as of end_date
            total_buy_cost_adjusted = 0.0
            total_buy_qty_adjusted = 0.0
            
            for buy_txn in all_buy_txns:
                buy_date = buy_txn.transaction_date.date() if hasattr(buy_txn.transaction_date, 'date') else buy_txn.transaction_date
                
                # Get corporate actions between buy date and end_date
                actions_after_buy = get_corporate_actions_after_date(
                    security_id, buy_date, end_date, all_corporate_actions
                )
                
                adjustment_factor = calculate_quantity_adjustment_factor(actions_after_buy)
                
                buy_qty_original = float(buy_txn.quantity)
                buy_price_original = float(buy_txn.price)
                
                buy_qty_adjusted = buy_qty_original * adjustment_factor
                buy_price_adjusted = buy_price_original / adjustment_factor if adjustment_factor > 0 else buy_price_original
                
                total_buy_cost_adjusted += buy_qty_adjusted * buy_price_adjusted
                total_buy_qty_adjusted += buy_qty_adjusted
            
            avg_buy_price = total_buy_cost_adjusted / total_buy_qty_adjusted if total_buy_qty_adjusted > 0 else 0
            total_cost_basis += unmatched_qty * avg_buy_price
        
        # Calculate MTM gain for sold position: (proceeds - cost basis)
        mtm_gain = data['total_proceeds'] - total_cost_basis
        
        # Calculate average buy price for display
        avg_buy_price = total_cost_basis / data['total_qty'] if data['total_qty'] > 0 else 0

        # Current price (as of end_date) for "MTM on selling decision"
        current_price = None
        try:
            from services.price_service import PriceService
            price_data = PriceService.get_price(
                security_id=security_id,
                target_date=end_date,
                use_adjusted=True,
                allow_fallback=True
            )
            if price_data and price_data.price is not None:
                current_price = float(price_data.price)
        except Exception as e:
            logger.warning(f"Could not fetch current price for sold stock {security_id} on {end_date}: {str(e)}")
        
        # Return % = (MTM gain / cost basis) * 100
        return_percent = (mtm_gain / total_cost_basis * 100) if total_cost_basis > 0 else 0
        
        # Calculate average holding period (simplified)
        avg_holding_days = 30  # Default assumption
        
        avg_sell_price = data['total_proceeds'] / data['total_qty'] if data['total_qty'] > 0 else 0
        mtm_on_selling = None
        if current_price is not None:
            # Notional profit/loss of selling decision:
            # (sell_price - current_price_at_end_date) * qty_sold
            mtm_on_selling = (avg_sell_price - current_price) * data['total_qty']

        stocks_sold.append({
            'security_id': security_id,
            'symbol': security.symbol,
            'name': security.name,
            'qty_sold': data['total_qty'],
            'avg_buy_price': avg_buy_price,
            'avg_sell_price': avg_sell_price,
            'current_price': current_price,
            'total_proceeds': data['total_proceeds'],
            'cost_basis': total_cost_basis,  # Cost basis for sold quantity
            'realized_gain': mtm_gain,  # MTM gain for sold position
            'mtm_gain': mtm_gain,  # Alias for clarity
            'mtm_on_selling': mtm_on_selling,
            'return_percent': return_percent,
            'avg_holding_period_days': avg_holding_days,
            'transaction_count': len(data['transactions'])
        })
    
    # Sort by realized gain
    stocks_sold.sort(key=lambda x: x['realized_gain'], reverse=True)
    
    return stocks_sold[:10]  # Top 10 by realized gain

def calculate_transaction_quality_metrics(client_id, start_date, end_date):
    """
    Calculate transaction quality metrics
    """
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get all SELL transactions in period
    sell_transactions = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.type == 'SELL',
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime
    ).all()
    
    if not sell_transactions:
        return {
            'total_trades': 0,
            'win_rate': 0,
            'avg_holding_period_days': 0,
            'avg_gain_winners_percent': 0,
            'avg_loss_losers_percent': 0,
            'profit_factor': 0,
            'best_trade': None,
            'worst_trade': None
        }
    
    trades = []
    total_gains = 0
    total_losses = 0
    winners = 0
    losers = 0
    
    for sell_txn in sell_transactions:
        # Calculate realized gain for this trade
        buy_txns = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.security_id == sell_txn.security_id,
            Transaction.type == 'BUY',
            Transaction.transaction_date <= sell_txn.transaction_date
        ).all()
        
        if buy_txns:
            total_buy_cost = sum(float(b.quantity) * float(b.price) for b in buy_txns)
            total_buy_qty = sum(float(b.quantity) for b in buy_txns)
            avg_buy_price = total_buy_cost / total_buy_qty if total_buy_qty > 0 else 0
        else:
            avg_buy_price = 0
        
        sell_price = float(sell_txn.price)
        quantity_sold = float(sell_txn.quantity)
        realized_gain = (sell_price - avg_buy_price) * quantity_sold
        
        trade_data = {
            'symbol': sell_txn.security.symbol if sell_txn.security else 'Unknown',
            'date': sell_txn.transaction_date.date(),
            'quantity': quantity_sold,
            'buy_price': avg_buy_price,
            'sell_price': sell_price,
            'realized_gain': realized_gain,
            'return_percent': ((sell_price - avg_buy_price) / avg_buy_price * 100) if avg_buy_price > 0 else 0
        }
        
        trades.append(trade_data)
        
        if realized_gain > 0:
            winners += 1
            total_gains += realized_gain
        else:
            losers += 1
            total_losses += abs(realized_gain)
    
    # Calculate metrics
    total_trades = len(trades)
    win_rate = (winners / total_trades * 100) if total_trades > 0 else 0
    avg_gain_winners = (total_gains / winners) if winners > 0 else 0
    avg_loss_losers = (total_losses / losers) if losers > 0 else 0
    # Use a large number instead of infinity for JSON serialization
    profit_factor = (total_gains / total_losses) if total_losses > 0 else 999999.99 if total_gains > 0 else 0
    
    # Find best and worst trades
    best_trade = max(trades, key=lambda x: x['realized_gain']) if trades else None
    worst_trade = min(trades, key=lambda x: x['realized_gain']) if trades else None
    
    return {
        'total_trades': total_trades,
        'win_rate': win_rate,
        'avg_holding_period_days': 30,  # Simplified
        'avg_gain_winners_percent': (avg_gain_winners / (avg_gain_winners + avg_loss_losers) * 100) if (avg_gain_winners + avg_loss_losers) > 0 else 0,
        'avg_loss_losers_percent': (avg_loss_losers / (avg_gain_winners + avg_loss_losers) * 100) if (avg_gain_winners + avg_loss_losers) > 0 else 0,
        'profit_factor': profit_factor,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'total_gains': total_gains,
        'total_losses': total_losses
    }

def calculate_enhanced_sector_analysis(start_map, end_map):
    """
    Calculate enhanced sector analysis
    """
    # Get all securities with sector info
    all_security_ids = set(start_map.keys()) | set(end_map.keys())
    sectors = {}
    
    for security_id in all_security_ids:
        start_holding = start_map.get(security_id)
        end_holding = end_map.get(security_id)
        
        # Get sector from either holding
        sector = 'Unknown'
        if end_holding:
            sector = end_holding.get('sector', 'Unknown')
        elif start_holding:
            sector = start_holding.get('sector', 'Unknown')
        
        if sector not in sectors:
            sectors[sector] = {
                'sector': sector,
                'start_value': 0,
                'end_value': 0,
                'value_gain': 0,
                'return_percent': 0,
                'contribution_to_gain': 0,
                'securities': set()
            }
        
        start_value = start_holding['current_value'] if start_holding else 0
        end_value = end_holding['current_value'] if end_holding else 0
        gain = end_value - start_value
        
        sectors[sector]['start_value'] += start_value
        sectors[sector]['end_value'] += end_value
        sectors[sector]['value_gain'] += gain
        
        if end_holding:
            sectors[sector]['securities'].add(end_holding['symbol'])
        elif start_holding:
            sectors[sector]['securities'].add(start_holding['symbol'])
    
    # Calculate percentages and contribution
    total_gain = sum(s['value_gain'] for s in sectors.values())
    
    sector_list = []
    for sector_data in sectors.values():
        return_percent = (sector_data['value_gain'] / sector_data['start_value'] * 100) if sector_data['start_value'] > 0 else 0
        contribution_percent = (sector_data['value_gain'] / total_gain * 100) if total_gain != 0 else 0
        
        sector_list.append({
            'sector': sector_data['sector'],
            'start_value': sector_data['start_value'],
            'end_value': sector_data['end_value'],
            'value_gain': sector_data['value_gain'],
            'return_percent': return_percent,
            'contribution_to_gain': contribution_percent,
            'securities_count': len(sector_data['securities']),
            'securities': list(sector_data['securities'])
        })
    
    # Sort by value gain
    sector_list.sort(key=lambda x: x['value_gain'], reverse=True)
    
    # Find best and worst sectors
    best_sector = sector_list[0] if sector_list else None
    worst_sector = sector_list[-1] if sector_list else None
    
    # Find most concentrated sector
    concentrated_sector = max(sector_list, key=lambda x: x['end_value']) if sector_list else None
    
    return {
        'sector_analysis': sector_list,
        'best_sector': best_sector,
        'worst_sector': worst_sector,
        'concentrated_sector': concentrated_sector,
        'total_sectors': len(sector_list)
    }

def calculate_industry_performance(mtm_analysis, end_holdings_map=None, client_id=None, start_date=None, end_date=None):
    """
    Aggregate stock-wise MTM performance data by industry
    Shows absolute gains and losses industry-wise adjusted for new investments
    Only counts actual price losses, not sales (stocks that were sold)
    
    Args:
        mtm_analysis: MTM analysis data containing mtm_gains_by_stock
        end_holdings_map: Dictionary of end holdings {security_id: holding} to check if stock is still held
        client_id: Client ID to calculate investments by industry
        start_date: Period start date to calculate investments
        end_date: Period end date to calculate investments
    """
    from models import Security, Transaction
    import json
    
    mtm_gains_by_stock = mtm_analysis.get('mtm_gains_by_stock', [])
    
    if not mtm_gains_by_stock:
        return {
            'industry_performance': [],
            'total_industries': 0,
            'total_gains': 0,
            'total_losses': 0
        }
    
    # Calculate investments by industry during the period (if parameters provided)
    investments_by_industry = {}
    if client_id and start_date and end_date:
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        
        buy_transactions = Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.type == 'BUY',
            Transaction.transaction_date >= start_datetime,
            Transaction.transaction_date <= end_datetime
        ).all()
        
        for txn in buy_transactions:
            if not txn.security:
                continue
            
            # Get industry from security
            industry = 'Unknown'
            if txn.security.meta_data:
                try:
                    meta_data = json.loads(txn.security.meta_data) if isinstance(txn.security.meta_data, str) else txn.security.meta_data
                    industry = meta_data.get('industry') or meta_data.get('sector') or 'Unknown'
                except:
                    pass
            
            if industry == 'Unknown':
                if hasattr(txn.security, 'industry') and txn.security.industry:
                    industry = txn.security.industry
                elif hasattr(txn.security, 'sector') and txn.security.sector:
                    industry = txn.security.sector
            
            investment_amount = float(txn.quantity) * float(txn.price)
            investments_by_industry[industry] = investments_by_industry.get(industry, 0) + investment_amount
    
    # Group by industry
    industry_data = {}
    
    for stock in mtm_gains_by_stock:
        security_id = stock.get('security_id')
        if not security_id:
            continue
        
        security = Security.query.get(security_id)
        if not security:
            continue
        
        # Get industry from meta_data or direct field
        industry = 'Unknown'
        if security.meta_data:
            try:
                meta_data = json.loads(security.meta_data) if isinstance(security.meta_data, str) else security.meta_data
                industry = meta_data.get('industry') or meta_data.get('sector') or 'Unknown'
            except:
                pass
        
        if industry == 'Unknown':
            if hasattr(security, 'industry') and security.industry:
                industry = security.industry
            elif hasattr(security, 'sector') and security.sector:
                industry = security.sector
        
        if industry not in industry_data:
            industry_data[industry] = {
                'industry': industry,
                'total_gains': 0,
                'total_losses': 0,
                'net_gain': 0,
                'securities_count': 0,
                'securities': []
            }
        
        gain = stock.get('gain', 0) or stock.get('value_change', 0)
        start_value = stock.get('start_value', 0) or 0
        end_value = stock.get('end_value', 0) or 0
        start_qty = stock.get('start_quantity', 0) or stock.get('quantity', 0) or 0
        end_qty = stock.get('end_quantity', 0) or stock.get('quantity', 0) or 0
        
        # Calculate new investments for this stock during the period
        stock_investment = 0
        if client_id and start_date and end_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            end_datetime = datetime.combine(end_date, datetime.max.time())
            
            stock_buy_txns = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.type == 'BUY',
                Transaction.transaction_date >= start_datetime,
                Transaction.transaction_date <= end_datetime
            ).all()
            
            stock_investment = sum(float(t.quantity) * float(t.price) for t in stock_buy_txns)
        
        # Adjust gain by subtracting new investments: adjusted_gain = change_in_value - new_investments
        adjusted_gain = gain - stock_investment
        
        # Check if stock is still held in end portfolio
        # If end_holdings_map is provided, use it to check if stock exists with quantity > 0
        is_still_held = False
        if end_holdings_map:
            end_holding = end_holdings_map.get(security_id)
            if end_holding:
                end_holding_qty = end_holding.get('quantity', 0) or 0
                is_still_held = end_holding_qty > 0.01  # Still held if quantity > 0
        else:
            # Fallback: check if end_value > 0 and end_qty > 0
            is_still_held = (end_value > 0 and end_qty > 0.01) or (end_value > abs(start_value) * 0.01)
        
        # Only count as loss if stock was held (still in portfolio) and price declined
        # If stock was sold (not in end holdings), don't count it as a price loss
        is_sold = not is_still_held and start_value > 0
        
        if is_still_held and start_value > 0:
            # Stock still held - count adjusted gains/losses (price movement minus new investments)
            if adjusted_gain > 0:
                industry_data[industry]['total_gains'] += adjusted_gain
            elif adjusted_gain < 0:
                industry_data[industry]['total_losses'] += abs(adjusted_gain)
            
            industry_data[industry]['net_gain'] += adjusted_gain
        elif is_sold:
            # Stock was sold - don't count in gains/losses, but still count in securities_count
            # The sale is a transaction, not a price loss
            pass
        
        industry_data[industry]['securities_count'] += 1
        industry_data[industry]['securities'].append({
            'symbol': stock.get('symbol', 'Unknown'),
            'gain': adjusted_gain,  # Use adjusted gain (performance after subtracting investments)
            'raw_gain': gain,  # Keep original gain for reference
            'new_investment': stock_investment,  # Track new investments
            'is_sold': is_sold
        })
    
    # Convert to list and sort by absolute net gain
    industry_list = []
    total_gains = 0
    total_losses = 0
    
    for industry, data in industry_data.items():
        # Get total new investments for this industry
        industry_investment = investments_by_industry.get(industry, 0)
        
        industry_list.append({
            'industry': industry,
            'total_gains': data['total_gains'],  # Adjusted gains (after subtracting investments)
            'total_losses': data['total_losses'],  # Adjusted losses
            'net_gain': data['net_gain'],  # Adjusted net gain (performance)
            'new_investments': industry_investment,  # New investments in this industry during period
            'securities_count': data['securities_count'],
            'top_contributors': sorted(data['securities'], key=lambda x: x['gain'], reverse=True)[:5]
        })
        total_gains += data['total_gains']
        total_losses += data['total_losses']
    
    # Sort by absolute net gain (descending)
    industry_list.sort(key=lambda x: abs(x['net_gain']), reverse=True)
    
    return {
        'industry_performance': industry_list,
        'total_industries': len(industry_list),
        'total_gains': total_gains,
        'total_losses': total_losses,
        'net_gain': total_gains - total_losses
    }

def calculate_holdings_evolution_detailed(start_map, end_map, txns_in_period):
    """
    Calculate detailed holdings evolution
    """
    start_securities = set(start_map.keys())
    end_securities = set(end_map.keys())
    
    new_positions = []
    exited_positions = []
    increased_positions = []
    decreased_positions = []
    
    # Find new positions
    for security_id in end_securities - start_securities:
        end_holding = end_map[security_id]
        new_positions.append({
            'symbol': end_holding['symbol'],
            'name': end_holding.get('name', 'Unknown'),
            'quantity': end_holding['quantity'],
            'value': end_holding['current_value']
        })
    
    # Find exited positions
    for security_id in start_securities - end_securities:
        start_holding = start_map[security_id]
        exited_positions.append({
            'symbol': start_holding['symbol'],
            'name': start_holding.get('name', 'Unknown'),
            'quantity': start_holding['quantity'],
            'value': start_holding['current_value']
        })
    
    # Find changed positions
    for security_id in start_securities & end_securities:
        start_holding = start_map[security_id]
        end_holding = end_map[security_id]
        
        start_qty = start_holding['quantity']
        end_qty = end_holding['quantity']
        change = end_qty - start_qty
        change_percent = (change / start_qty * 100) if start_qty > 0 else 0
        
        if abs(change_percent) > 5:  # Significant change
            change_data = {
                'symbol': end_holding['symbol'],
                'name': end_holding.get('name', 'Unknown'),
                'start_quantity': start_qty,
                'end_quantity': end_qty,
                'change': change,
                'change_percent': change_percent,
                'start_value': start_holding['current_value'],
                'end_value': end_holding['current_value']
            }
            
            if change > 0:
                increased_positions.append(change_data)
            else:
                decreased_positions.append(change_data)
    
    # Calculate portfolio churn rate
    total_positions = len(start_securities | end_securities)
    churned_positions = len(new_positions) + len(exited_positions)
    churn_rate = (churned_positions / total_positions * 100) if total_positions > 0 else 0
    
    return {
        'start_count': len(start_securities),
        'end_count': len(end_securities),
        'new_positions': new_positions,
        'exited_positions': exited_positions,
        'increased_positions': increased_positions,
        'decreased_positions': decreased_positions,
        'portfolio_churn_rate': churn_rate,
        'summary': {
            'start_count': len(start_securities),  # ✅ Template expects this in summary
            'end_count': len(end_securities),  # ✅ Template expects this in summary
            'total_new': len(new_positions),
            'total_exited': len(exited_positions),
            'total_increased': len(increased_positions),
            'total_decreased': len(decreased_positions),
            'net_change': len(end_securities) - len(start_securities)
        }
    }

def calculate_concentration_risk(end_map):
    """
    Calculate concentration risk metrics
    """
    if not end_map:
        return {
            'top_5_weight': 0,
            'top_10_weight': 0,
            'herfindahl_index': 0,
            'effective_number_of_stocks': 0,
            'max_single_position': 0,
            'diversification_score': 0,
            'recommendation': 'No holdings'
        }
    
    # Calculate total value and individual weights
    total_value = sum(h['current_value'] for h in end_map.values())
    if total_value == 0:
        return {
            'top_5_weight': 0,
            'top_10_weight': 0,
            'herfindahl_index': 0,
            'effective_number_of_stocks': 0,
            'max_single_position': 0,
            'diversification_score': 0,
            'recommendation': 'No value'
        }
    
    # Sort by value
    sorted_holdings = sorted(end_map.values(), key=lambda x: x['current_value'], reverse=True)
    
    # Calculate top 5 and top 10 weights
    top_5_weight = sum(h['current_value'] for h in sorted_holdings[:5]) / total_value * 100
    top_10_weight = sum(h['current_value'] for h in sorted_holdings[:10]) / total_value * 100
    
    # Calculate Herfindahl Index
    weights = [h['current_value'] / total_value for h in sorted_holdings]
    herfindahl_index = sum(w**2 for w in weights)
    
    # Calculate effective number of stocks
    effective_number_of_stocks = 1 / herfindahl_index if herfindahl_index > 0 else 0
    
    # Max single position
    max_single_position = sorted_holdings[0]['current_value'] / total_value * 100 if sorted_holdings else 0
    
    # Diversification score (0-100, higher is better)
    diversification_score = min(100, effective_number_of_stocks * 10)  # Scale factor
    
    # Calculate asset class breakdown - Look up asset class from Security table
    from models import Security
    asset_class_breakdown = {}
    for h in sorted_holdings:
        # Look up security to get asset class
        security = Security.query.get(h['security_id'])
        if security and security.asset_class:
            asset_class = security.asset_class.name  # Get asset class name from relationship
        else:
            asset_class = 'Equity'  # Default fallback
        
        if asset_class not in asset_class_breakdown:
            asset_class_breakdown[asset_class] = {
                'total_value': 0,
                'weight': 0,
                'holdings': []
            }
        asset_class_breakdown[asset_class]['total_value'] += h['current_value']
        asset_class_breakdown[asset_class]['holdings'].append({
            'symbol': h['symbol'],
            'value': h['current_value'],
            'weight': h['current_value'] / total_value * 100
        })
    
    # Calculate weights for each asset class
    for asset_class in asset_class_breakdown:
        asset_class_breakdown[asset_class]['weight'] = asset_class_breakdown[asset_class]['total_value'] / total_value * 100
        # Sort holdings within asset class by value
        asset_class_breakdown[asset_class]['holdings'] = sorted(
            asset_class_breakdown[asset_class]['holdings'],
            key=lambda x: x['value'],
            reverse=True
        )
    
    # Recommendation
    if diversification_score >= 80:
        recommendation = 'Well Diversified'
    elif diversification_score >= 60:
        recommendation = 'Moderately Diversified'
    elif diversification_score >= 40:
        recommendation = 'Concentrated'
    else:
        recommendation = 'Highly Concentrated'
    
    return {
        'top_5_weight': top_5_weight,
        'top_10_weight': top_10_weight,
        'herfindahl_index': herfindahl_index,
        'effective_number_of_stocks': effective_number_of_stocks,
        'max_single_position': max_single_position,
        'diversification_score': diversification_score,
        'recommendation': recommendation,
        'top_holdings': [
            {
                'symbol': h['symbol'],
                'weight': h['current_value'] / total_value * 100,
                'value': h['current_value'],
                'sector': h.get('sector', 'Unknown'),
                'asset_class': Security.query.get(h['security_id']).asset_class.name if Security.query.get(h['security_id']) and Security.query.get(h['security_id']).asset_class else 'Equity'
            }
            for h in sorted_holdings[:10]
        ],
        'asset_class_breakdown': asset_class_breakdown
    }

def calculate_value_attribution(start_map, end_map, txns_in_period, start_date, end_date):
    """
    Calculate value attribution analysis with corporate action adjustments
    """
    from models import CorporateAction
    
    # Market Movement Attribution: Hold start quantities, apply end prices (adjusted for CAs)
    market_movement_attribution = 0
    portfolio_changes_attribution = 0
    
    # Security-level details
    market_movement_by_security = []
    portfolio_changes_by_security = []
    
    # Calculate total portfolio change
    start_total = sum(h['current_value'] for h in start_map.values())
    end_total = sum(h['current_value'] for h in end_map.values())
    total_change = end_total - start_total
    
    # Get corporate actions that happened in the period
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    period_cas = CorporateAction.query.filter(
        CorporateAction.action_date >= start_datetime,
        CorporateAction.action_date <= end_datetime,
        CorporateAction.is_active == True
    ).all()
    
    # Build CA lookup by security
    ca_by_security = {}
    for ca in period_cas:
        if ca.security_id not in ca_by_security:
            ca_by_security[ca.security_id] = []
        ca_by_security[ca.security_id].append(ca)
    
    # Build net cashflow map from BUY and SELL transactions (to subtract from portfolio changes)
    # Net cashflow = Total Buy - Total Sell per security
    new_investments_by_security = {}
    for txn in txns_in_period:
        if txn.security_id:
            security_id = txn.security_id
            if security_id not in new_investments_by_security:
                new_investments_by_security[security_id] = 0
            txn_value = float(txn.quantity) * float(txn.price)
            if txn.type == 'BUY':
                new_investments_by_security[security_id] += txn_value
            elif txn.type == 'SELL':
                new_investments_by_security[security_id] -= txn_value
    
    # For each security, calculate what the value would be if we held start quantities at end prices
    for security_id in start_map:
        start_holding = start_map[security_id]
        end_holding = end_map.get(security_id)
        
        if end_holding:
            start_qty = start_holding['quantity']
            start_price = start_holding.get('current_price', 0)
            end_price = end_holding.get('current_price', 0)
            symbol = start_holding.get('symbol', 'Unknown')
            
            # Adjust start quantity for corporate actions
            adjusted_qty = start_qty
            adjustment_ratio = 1.0
            ca_notes = []
            
            if security_id in ca_by_security:
                for ca in sorted(ca_by_security[security_id], key=lambda x: x.action_date):
                    if ca.action_type == 'SPLIT':
                        adjusted_qty *= float(ca.ratio)
                        adjustment_ratio *= float(ca.ratio)
                        ca_notes.append(f"Split {ca.ratio}:1")
                    elif ca.action_type == 'BONUS':
                        adjusted_qty *= (1 + float(ca.ratio))
                        adjustment_ratio *= (1 + float(ca.ratio))
                        ca_notes.append(f"Bonus {ca.ratio}:1")
                    # MERGER handled separately (don't adjust here)
            
            # Adjust start price proportionally
            adjusted_start_price = start_price / adjustment_ratio if adjustment_ratio != 0 else start_price
            
            # Market movement: adjusted start quantity * (end price - adjusted start price)
            market_movement = adjusted_qty * (end_price - adjusted_start_price)
            market_movement_attribution += market_movement
            
            # Store security-level market movement
            market_movement_by_security.append({
                'security_id': security_id,
                'symbol': symbol,
                'start_qty': start_qty,
                'adjusted_qty': adjusted_qty,
                'start_price': start_price,
                'adjusted_start_price': adjusted_start_price,
                'end_price': end_price,
                'market_movement': market_movement,
                'corporate_actions': ', '.join(ca_notes) if ca_notes else None
            })
            
            # Portfolio changes: actual end value - (adjusted start qty * end price) - net cashflow (buy - sell)
            actual_end_value = end_holding['current_value']
            hypothetical_end_value = adjusted_qty * end_price
            net_cashflow = new_investments_by_security.get(security_id, 0)  # Net: Total Buy - Total Sell
            portfolio_change = actual_end_value - hypothetical_end_value - net_cashflow
            portfolio_changes_attribution += portfolio_change
            
            # Debug logging for first security
            if len(portfolio_changes_by_security) == 0:
                logger.info(f"Value Attribution Debug - Security: {symbol}, Actual: {actual_end_value:.2f}, "
                          f"Hypothetical: {hypothetical_end_value:.2f}, Net Cashflow: {net_cashflow:.2f}, "
                          f"Portfolio Change: {portfolio_change:.2f}")
            
            # Store security-level portfolio changes
            if abs(portfolio_change) > 0.01:  # Only include if significant
                portfolio_changes_by_security.append({
                    'security_id': security_id,
                    'symbol': symbol,
                    'adjusted_qty': adjusted_qty,
                    'actual_qty': end_holding['quantity'],
                    'qty_change': end_holding['quantity'] - adjusted_qty,
                    'end_price': end_price,
                    'portfolio_change': portfolio_change,
                    'hypothetical_value': hypothetical_end_value,
                    'actual_value': actual_end_value
                })
    
    # ✅ CRITICAL FIX: Include exited positions in market_movement_by_security
    # Base Portfolio Gains must include ALL starting positions, even if sold during period
    # This ensures: Hypothetical Value = Adjusted Start Quantity × End Price for ALL positions
    for security_id in set(start_map.keys()) - set(end_map.keys()):
        start_holding = start_map[security_id]
        start_qty = start_holding['quantity']
        start_price = start_holding.get('current_price', 0)
        symbol = start_holding.get('symbol', 'Unknown')
        
        # Adjust start quantity for corporate actions that happened before sale
        adjusted_qty = start_qty
        adjustment_ratio = 1.0
        ca_notes = []
        
        if security_id in ca_by_security:
            for ca in sorted(ca_by_security[security_id], key=lambda x: x.action_date):
                if ca.action_type == 'SPLIT':
                    adjusted_qty *= float(ca.ratio)
                    adjustment_ratio *= float(ca.ratio)
                    ca_notes.append(f"Split {ca.ratio}:1")
                elif ca.action_type == 'BONUS':
                    adjusted_qty *= (1 + float(ca.ratio))
                    adjustment_ratio *= (1 + float(ca.ratio))
                    ca_notes.append(f"Bonus {ca.ratio}:1")
        
        adjusted_start_price = start_price / adjustment_ratio if adjustment_ratio != 0 else start_price
        
        # Get end price at end_date (even though position was sold)
        # This is critical: we need the price at end_date to calculate hypothetical value
        from services.price_service import PriceService
        from models import Security, HistoricalPrice
        from datetime import timedelta
        
        end_price = 0
        
        # Check if security matured before end_date
        security = Security.query.get(security_id)
        maturity_date = None
        if security and security.meta_data:
            import json
            try:
                meta = json.loads(security.meta_data) if isinstance(security.meta_data, str) else security.meta_data
                maturity_date_str = meta.get('maturity_date') or meta.get('maturityDate')
                if maturity_date_str:
                    # Try different date formats
                    for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S']:
                        try:
                            maturity_date = datetime.strptime(str(maturity_date_str), fmt).date()
                            break
                        except ValueError:
                            continue
            except (json.JSONDecodeError, AttributeError):
                pass
        
        # If security matured before end_date, use maturity price (last price before/on maturity)
        if maturity_date and end_date > maturity_date:
            # Try to get price on maturity date first
            maturity_price_record = HistoricalPrice.query.filter_by(
                security_id=security_id,
                date=maturity_date
            ).first()
            
            if maturity_price_record and maturity_price_record.close_price:
                end_price = float(maturity_price_record.close_price)
                logger.info(f"Exited position {symbol} matured on {maturity_date}: Using maturity date price ₹{end_price} for end_date {end_date}")
            else:
                # Try to get price just before maturity (1-5 days before)
                found_price = False
                for days_back in range(1, 6):
                    lookup_date = maturity_date - timedelta(days=days_back)
                    price_record = HistoricalPrice.query.filter_by(
                        security_id=security_id,
                        date=lookup_date
                    ).first()
                    if price_record and price_record.close_price:
                        end_price = float(price_record.close_price)
                        logger.info(f"Exited position {symbol} matured on {maturity_date}: Using price ₹{end_price} from {days_back} days before maturity for end_date {end_date}")
                        found_price = True
                        break
                
                # If no price in 1-5 day window, get closest price before maturity
                if not found_price:
                    closest_price_record = HistoricalPrice.query.filter(
                        HistoricalPrice.security_id == security_id,
                        HistoricalPrice.date < maturity_date
                    ).order_by(HistoricalPrice.date.desc()).first()
                    
                    if closest_price_record and closest_price_record.close_price:
                        end_price = float(closest_price_record.close_price)
                        logger.info(f"Exited position {symbol} matured on {maturity_date}: Using closest price before maturity ₹{end_price} for end_date {end_date}")
        
        # If not matured or maturity price not found, use normal price lookup
        if end_price == 0:
            end_price_data = PriceService.get_price(security_id, end_date, use_adjusted=True, allow_fallback=True)
            if end_price_data and end_price_data.is_valid:
                end_price = end_price_data.price
        
        # If PriceService fails, try to get from Security model as fallback
        if end_price == 0:
            if security and security.current_price:
                # Use current price as fallback (not ideal, but better than 0)
                end_price = float(security.current_price)
                logger.warning(f"Exited position {symbol}: Using current_price {end_price} as fallback for end_date {end_date}")
        
        # Calculate market movement: what would this position be worth if held?
        hypothetical_value = adjusted_qty * end_price if end_price > 0 else 0
        market_movement = hypothetical_value - (adjusted_qty * adjusted_start_price)
        
        # Add to market movement attribution
        market_movement_attribution += market_movement
        
        # Store in market_movement_by_security
        market_movement_by_security.append({
            'security_id': security_id,
            'symbol': symbol,
            'start_qty': start_qty,
            'adjusted_qty': adjusted_qty,
            'start_price': start_price,
            'adjusted_start_price': adjusted_start_price,
            'end_price': end_price,
            'market_movement': market_movement,
            'corporate_actions': ', '.join(ca_notes) if ca_notes else None,
            'exited': True  # Flag to indicate this position was sold
        })
        
        logger.info(f"Exited position included in Base Portfolio Gains: {symbol}, "
                    f"Adjusted Qty: {adjusted_qty:.2f}, Start Price: {adjusted_start_price:.2f}, "
                    f"End Price: {end_price:.2f}, Hypothetical Value: {hypothetical_value:.2f}, "
                    f"Market Movement: {market_movement:.2f}")
    
    # Break down portfolio changes
    new_positions_value_added = 0
    exited_positions_value_added = 0
    position_sizing_value_added = 0
    
    # New positions (securities in end but not in start)
    for security_id in set(end_map.keys()) - set(start_map.keys()):
        end_holding = end_map[security_id]
        net_cashflow = new_investments_by_security.get(security_id, 0)  # Net: Total Buy - Total Sell
        new_position_change = end_holding['current_value'] - net_cashflow
        new_positions_value_added += new_position_change
        portfolio_changes_attribution += new_position_change
    
    # Exited positions (securities in start but not in end)
    # Calculate realized gain/loss from sale proceeds
    for security_id in set(start_map.keys()) - set(end_map.keys()):
        start_holding = start_map[security_id]
        start_value = start_holding['current_value']
        
        # Get sale proceeds from transactions (SELL transactions for this security in period)
        sale_proceeds = 0
        for txn in txns_in_period:
            if txn.security_id == security_id and txn.type == 'SELL':
                sale_proceeds += float(txn.quantity) * float(txn.price)
        
        # If no sale transactions found, use net_cashflow (which includes SELL)
        if sale_proceeds == 0:
            # Net cashflow for exited position would be negative (SELL only, no BUY)
            net_cashflow = new_investments_by_security.get(security_id, 0)
            if net_cashflow < 0:  # Negative means more sold than bought
                sale_proceeds = abs(net_cashflow)
        
        # Realized gain/loss = Sale proceeds - Start value
        realized_gain_loss = sale_proceeds - start_value if sale_proceeds > 0 else -start_value
        exited_positions_value_added += realized_gain_loss
    
    # Position sizing changes (securities in both)
    # This measures the gain/loss from changing position size (buying more or selling some)
    # Should not double-count net_cashflow since it's already subtracted in portfolio_change
    for security_id in set(start_map.keys()) & set(end_map.keys()):
        start_holding = start_map[security_id]
        end_holding = end_map[security_id]
        
        start_qty = start_holding['quantity']
        end_qty = end_holding['quantity']
        start_price = start_holding.get('current_price', 0)
        end_price = end_holding.get('current_price', 0)
        
        # Calculate value change from position sizing
        # If quantity increased: gain from buying more at good prices
        # If quantity decreased: loss from selling some
        qty_change = end_qty - start_qty
        
        if qty_change > 0:
            # Position increased - calculate gain on additional quantity
            # Average price of additional purchases
            net_cashflow = new_investments_by_security.get(security_id, 0)
            avg_buy_price = net_cashflow / qty_change if qty_change > 0 and net_cashflow > 0 else start_price
            # Gain = (end_price - avg_buy_price) * additional_qty
            position_sizing_gain = (end_price - avg_buy_price) * qty_change if avg_buy_price > 0 else 0
            position_sizing_value_added += position_sizing_gain
        elif qty_change < 0:
            # Position decreased - calculate loss from selling some
            # Average sell price
            net_cashflow = new_investments_by_security.get(security_id, 0)
            qty_sold = abs(qty_change)
            avg_sell_price = abs(net_cashflow) / qty_sold if qty_sold > 0 and net_cashflow < 0 else start_price
            # Loss = (avg_sell_price - start_price) * qty_sold (negative if sold at loss)
            position_sizing_loss = (avg_sell_price - start_price) * qty_sold
            position_sizing_value_added += position_sizing_loss
    
    # Calculate percentages
    market_movement_percent = (market_movement_attribution / total_change * 100) if total_change != 0 else 0
    portfolio_changes_percent = (portfolio_changes_attribution / total_change * 100) if total_change != 0 else 0
    
    # Sort by contribution
    market_movement_by_security_sorted = sorted(market_movement_by_security, key=lambda x: x['market_movement'], reverse=True)
    portfolio_changes_by_security_sorted = sorted(portfolio_changes_by_security, key=lambda x: x['portfolio_change'], reverse=True)
    
    return {
        'market_movement_attribution': market_movement_attribution,
        'portfolio_changes_attribution': portfolio_changes_attribution,
        'new_positions_value_added': new_positions_value_added,
        'exited_positions_value_added': exited_positions_value_added,
        'position_sizing_value_added': position_sizing_value_added,
        'total_change': total_change,
        'market_movement_percent': market_movement_percent,
        'portfolio_changes_percent': portfolio_changes_percent,
        'market_movement_by_security': market_movement_by_security_sorted,  # All securities (for detailed breakdown)
        'portfolio_changes_by_security': portfolio_changes_by_security_sorted[:10],  # Top 10 contributors
        'attribution_summary': {
            'market_movement': {
                'value': market_movement_attribution,
                'percent': market_movement_percent,
                'description': 'Passive gains from market movement'
            },
            'portfolio_changes': {
                'value': portfolio_changes_attribution,
                'percent': portfolio_changes_percent,
                'description': 'Active management value added'
            }
        },
        'chart_data': {
            'labels': ['Market Movement', 'Portfolio Changes'],
            'values': [abs(market_movement_attribution), abs(portfolio_changes_attribution)],
            'colors': ['#28a745', '#007bff']
        }
    }

def calculate_pathway_breakdown(client_id, start_date, end_date, mtm_analysis, gains_breakdown, value_attribution):
    """
    Calculate pathway breakdown showing how portfolio value changed during the period
    
    Pathway Components:
    1. Base Portfolio Gains: Market movement on existing holdings
    2. Gains from Changes: Value added from trades and rebalancing
    3. New Funds Added: Net client flow (investments - withdrawals)
    4. Gains on New Funds: Returns on new funds invested during period
    
    Args:
        client_id: Client ID
        start_date: Period start date
        end_date: Period end date
        mtm_analysis: Mark-to-market analysis data
        gains_breakdown: Gains breakdown data
        value_attribution: Value attribution analysis data
    
    Returns:
        Dictionary with pathway breakdown
    """
    try:
        logger.info(f"Calculating pathway breakdown for client {client_id}, period {start_date} to {end_date}")
        
        # Get start and end values
        start_value = mtm_analysis.get('total_start_value', 0) or mtm_analysis.get('total_value_at_period_start', 0)
        end_value = mtm_analysis.get('total_end_value', 0) or mtm_analysis.get('total_current_value', 0)
        
        # Add detailed logging to diagnose why start_value might be 0
        logger.info(f"Pathway breakdown - mtm_analysis keys: {list(mtm_analysis.keys())}")
        logger.info(f"Pathway breakdown - total_start_value: {mtm_analysis.get('total_start_value')}")
        logger.info(f"Pathway breakdown - total_value_at_period_start: {mtm_analysis.get('total_value_at_period_start')}")
        logger.info(f"Pathway breakdown - total_end_value: {mtm_analysis.get('total_end_value')}")
        logger.info(f"Pathway breakdown - total_current_value: {mtm_analysis.get('total_current_value')}")
        logger.info(f"Pathway breakdown - calculated start_value: {start_value}, end_value: {end_value}")
        
        if start_value == 0:
            logger.warning(f"Start value is 0 in pathway breakdown. MTM analysis data: {mtm_analysis}")
            logger.warning("This may indicate an issue in calculate_mark_to_market_gains returning 0 even when portfolio has value")
            return None
        
        total_change = end_value - start_value
        total_change_percent = (total_change / start_value * 100) if start_value > 0 else 0
        
        # Get components from value attribution
        base_gains = value_attribution.get('market_movement_attribution', 0) or mtm_analysis.get('total_mtm_gain', 0)
        gains_from_changes = value_attribution.get('portfolio_changes_attribution', 0)
        
        # Get new funds from gains breakdown
        new_funds = gains_breakdown.get('client_additions', 0) or gains_breakdown.get('net_client_flow', 0)
        
        # Get detailed breakdown for base portfolio gains (all securities with start qty, adjusted qty, end price)
        market_movement_by_security = value_attribution.get('market_movement_by_security', [])
        logger.info(f"Found {len(market_movement_by_security)} securities in market_movement_by_security for detailed breakdown")
        if market_movement_by_security:
            logger.info(f"First security sample: {market_movement_by_security[0] if len(market_movement_by_security) > 0 else 'N/A'}")
        
        # Calculate percentages
        base_gains_percent = (base_gains / total_change * 100) if total_change != 0 else 0
        gains_from_changes_percent = (gains_from_changes / total_change * 100) if total_change != 0 else 0
        new_funds_percent = (new_funds / total_change * 100) if total_change != 0 else 0
        
        # Build detailed breakdown
        detailed_breakdown = []
        if market_movement_by_security:
            for sec in market_movement_by_security:
                detailed_breakdown.append({
                    'symbol': sec.get('symbol', 'Unknown'),
                    'start_qty': float(sec.get('start_qty', 0)),
                    'adjusted_qty': float(sec.get('adjusted_qty', 0)),
                    'start_price': float(sec.get('start_price', 0)),
                    'adjusted_start_price': float(sec.get('adjusted_start_price', 0)),
                    'end_price': float(sec.get('end_price', 0)),
                    'hypothetical_value': float(sec.get('adjusted_qty', 0)) * float(sec.get('end_price', 0)),
                    'market_movement': float(sec.get('market_movement', 0)),
                    'corporate_actions': sec.get('corporate_actions') or '-'
                })
        
        logger.info(f"Built detailed_breakdown with {len(detailed_breakdown)} securities")
        
        pathway_breakdown = {
            'start_value': float(start_value),
            'end_value': float(end_value),
            'total_change': float(total_change),
            'total_change_percent': float(total_change_percent),
            'pathway_components': {
                'base_portfolio_gains': {
                    'value': float(base_gains),
                    'percent': float(base_gains_percent),
                    'description': 'Gain from base portfolio at start (price appreciation on existing holdings)',
                    'detailed_breakdown': detailed_breakdown
                },
                'gains_from_changes': {
                    'value': float(gains_from_changes),
                    'percent': float(gains_from_changes_percent),
                    'description': 'Gains/losses from trades and rebalancing'
                },
                'new_funds_added': {
                    'value': float(new_funds),
                    'percent': float(new_funds_percent),
                    'description': 'New funds added (investments - withdrawals)'
                }
            }
        }
        
        logger.info(f"Pathway breakdown: Start=₹{start_value:,.2f}, End=₹{end_value:,.2f}, Change=₹{total_change:,.2f}")
        logger.info(f"  Base gains: ₹{base_gains:,.2f} ({base_gains_percent:.2f}%)")
        logger.info(f"  Gains from changes: ₹{gains_from_changes:,.2f} ({gains_from_changes_percent:.2f}%)")
        logger.info(f"  New funds: ₹{new_funds:,.2f} ({new_funds_percent:.2f}%)")
        
        return pathway_breakdown
        
    except Exception as e:
        logger.error(f"Error calculating pathway breakdown: {str(e)}", exc_info=True)
        return None

def calculate_segment_wise_xirr(client_id, start_date, end_date):
    """
    Calculate XIRR for each asset class segment
    
    Args:
        client_id: Client ID
        start_date: Period start date
        end_date: Period end date
    
    Returns:
        dict with segments and summary
    """
    from models import AssetClass
    
    logger.info(f"=== CALCULATING SEGMENT-WISE XIRR ===")
    logger.info(f"Client ID: {client_id}, Period: {start_date} to {end_date}")
    
    # Get cashflows by asset class from transactions
    asset_class_cashflows = get_asset_class_cashflows_from_trades(client_id, start_date, end_date)
    logger.info(f"Retrieved {len(asset_class_cashflows)} asset classes with cashflows")
    
    # Get current values by asset class
    asset_class_values = get_current_value_by_asset_class(client_id, end_date)
    logger.info(f"Retrieved {len(asset_class_values)} asset classes with current values")
    
    # Calculate XIRR for each segment
    segments = {}
    total_portfolio_value = 0.0
    weighted_xirr_sum = 0.0
    
    # Process segments with transactions
    logger.info(f"Processing {len(asset_class_cashflows)} asset classes with cashflows")
    for asset_class_name, cashflows in asset_class_cashflows.items():
        if not cashflows:
            logger.warning(f"Skipping asset class '{asset_class_name}' - no cashflows")
            continue
        
        logger.info(f"Processing segment '{asset_class_name}' with {len(cashflows)} cashflows")
        
        current_value = asset_class_values.get(asset_class_name, 0.0)
        current_value_float = float(current_value) if not isinstance(current_value, float) else current_value
        total_portfolio_value += current_value_float
        
        # Build cashflow verification data (similar to benchmark verification)
        segment_cashflow_verification = []
        cumulative_investment = 0.0
        
        # Sort cashflows by date for verification
        sorted_cashflows = sorted(cashflows, key=lambda x: x[0])
        
        for cf_date, cf_amount in sorted_cashflows:
            cf_date_normalized = cf_date.date() if hasattr(cf_date, 'date') else cf_date
            cf_amount_float = float(cf_amount) if not isinstance(cf_amount, float) else cf_amount
            
            cumulative_investment += cf_amount_float  # Note: investments are negative, withdrawals positive
            
            segment_cashflow_verification.append({
                'date': cf_date_normalized.isoformat() if hasattr(cf_date_normalized, 'isoformat') else str(cf_date_normalized),
                'type': 'Investment' if cf_amount_float < 0 else 'Withdrawal',
                'amount': cf_amount_float,
                'cumulative_investment': cumulative_investment,
                'description': f'Transaction in {asset_class_name} segment'
            })
        
        # Add final value row
        segment_cashflow_verification.append({
            'date': end_date.isoformat() if hasattr(end_date, 'isoformat') else str(end_date),
            'type': 'Final Value',
            'amount': 0.0,
            'cumulative_investment': cumulative_investment,
            'cumulative_value': current_value_float,
            'description': f'Final value of {asset_class_name} segment: ₹{current_value_float:,.2f}'
        })
        
        # Calculate XIRR — pass end_date so terminal value is discounted to period end, not datetime.now()
        xirr, total_invested, total_withdrawn, net_investment, absolute_return = calculate_xirr(
            cashflows, current_value_float, end_date=end_date
        )
        
        # Ensure net_investment is float
        net_investment_float = float(net_investment) if not isinstance(net_investment, float) else net_investment
        
        # Calculate absolute return percentage
        absolute_return_percent = ((current_value_float - net_investment_float) / net_investment_float * 100) if net_investment_float > 0 else 0.0
        
        # Count transactions
        transaction_count = len(cashflows)
        
        # Calculate average investment duration (weighted by investment amount)
        # Duration = days from investment date to end_date, weighted by investment amount
        total_weighted_days = 0.0
        total_investment_for_duration = 0.0
        for cf_date, cf_amount in cashflows:
            if cf_amount < 0:  # Investment (negative amount)
                # Normalize cf_date to date object for subtraction
                if isinstance(cf_date, datetime):
                    cf_date_normalized = cf_date.date()
                elif isinstance(cf_date, date):
                    cf_date_normalized = cf_date
                else:
                    # Try to convert if it's a string or other type
                    try:
                        cf_date_normalized = cf_date.date() if hasattr(cf_date, 'date') else date.fromisoformat(str(cf_date))
                    except:
                        logger.warning(f"Could not normalize cashflow date: {cf_date}, skipping duration calculation for this cashflow")
                        continue
                
                # Now both are date objects, safe to subtract
                days_from_investment = (end_date - cf_date_normalized).days
                investment_amount = abs(cf_amount)
                total_weighted_days += days_from_investment * investment_amount
                total_investment_for_duration += investment_amount
        
        avg_duration_days = (total_weighted_days / total_investment_for_duration) if total_investment_for_duration > 0 else 0.0
        avg_duration_months = avg_duration_days / 30.44 if avg_duration_days > 0 else 0.0
        avg_duration_years = avg_duration_days / 365.25 if avg_duration_days > 0 else 0.0
        
        segments[asset_class_name] = {
            'asset_class': asset_class_name,
            'xirr': float(xirr) if not isinstance(xirr, float) else xirr,
            'xirr_percent': float(xirr) * 100 if not isinstance(xirr, float) else xirr * 100,
            'total_invested': float(total_invested) if not isinstance(total_invested, float) else total_invested,
            'total_withdrawn': float(total_withdrawn) if not isinstance(total_withdrawn, float) else total_withdrawn,
            'net_investment': net_investment_float,
            'current_value': current_value_float,
            'absolute_return': current_value_float - net_investment_float,
            'absolute_return_percent': absolute_return_percent,
            'transaction_count': transaction_count,
            'avg_duration_days': round(avg_duration_days, 0),
            'avg_duration_months': round(avg_duration_months, 1),
            'avg_duration_years': round(avg_duration_years, 2),
            'cashflow_verification': segment_cashflow_verification
        }
        
        # Add to weighted average calculation
        if current_value_float > 0:
            # Ensure all values are floats to avoid type errors
            xirr_float = float(xirr) if not isinstance(xirr, float) else xirr
            current_value_float = float(current_value) if not isinstance(current_value, float) else current_value
            weighted_xirr_sum += xirr_float * current_value_float
        
        logger.info(f"Segment {asset_class_name}: XIRR={xirr*100:.2f}%, Value=₹{current_value:,.2f}, Net Investment=₹{net_investment:,.2f}")
    
    # Include asset classes with holdings but no transactions in period
    for asset_class_name, current_value in asset_class_values.items():
        if asset_class_name not in segments and current_value > 0:
            current_value_float = float(current_value) if not isinstance(current_value, float) else current_value
            total_portfolio_value += current_value_float
            segments[asset_class_name] = {
                'asset_class': asset_class_name,
                'xirr': 0.0,
                'xirr_percent': 0.0,
                'total_invested': 0.0,
                'total_withdrawn': 0.0,
                'net_investment': 0.0,
                'current_value': current_value_float,
                'absolute_return': current_value_float,
                'absolute_return_percent': 0.0,
                'transaction_count': 0,
                'note': 'No transactions in period - holdings exist from prior periods'
            }
    
    # Calculate weighted average XIRR - ensure all values are floats
    weighted_xirr_sum_float = float(weighted_xirr_sum) if not isinstance(weighted_xirr_sum, float) else weighted_xirr_sum
    total_portfolio_value_float = float(total_portfolio_value) if not isinstance(total_portfolio_value, float) else total_portfolio_value
    weighted_avg_xirr = (weighted_xirr_sum_float / total_portfolio_value_float * 100) if total_portfolio_value_float > 0 else 0.0
    
    summary = {
        'total_segments': len(segments),
        'total_portfolio_value': total_portfolio_value,
        'weighted_avg_xirr': weighted_avg_xirr,
        'segments_with_transactions': len([s for s in segments.values() if s.get('transaction_count', 0) > 0]),
        'segments_without_transactions': len([s for s in segments.values() if s.get('transaction_count', 0) == 0])
    }
    
    logger.info(f"=== SEGMENT-WISE XIRR CALCULATION COMPLETE ===")
    logger.info(f"Total segments: {summary['total_segments']}, Weighted avg XIRR: {weighted_avg_xirr:.2f}%")
    logger.info(f"Segments with transactions: {summary['segments_with_transactions']}")
    logger.info(f"Segments without transactions: {summary['segments_without_transactions']}")
    
    result = {
        'segments': segments,
        'summary': summary
    }
    
    logger.info(f"Returning result with {len(segments)} segments")
    if len(segments) == 0:
        logger.warning(f"⚠️ WARNING: No segments calculated! This may indicate:")
        logger.warning(f"   1. No transactions in the period")
        logger.warning(f"   2. No holdings as of end date")
        logger.warning(f"   3. Asset class mapping issues")
    
    return result


def get_start_value_by_asset_class(client_id, start_date):
    """
    Get start portfolio value grouped by asset class at the start date.
    
    Args:
        client_id: Client ID
        start_date: Date to get portfolio value
    
    Returns:
        dict: {
            'Equity': start_value,
            'Debt': start_value,
            ...
        }
    """
    from models import AssetClass
    import json
    
    # Get portfolio by date to get accurate holdings with prices
    portfolio = get_client_portfolio_by_date(client_id, start_date)
    
    asset_class_values = {}
    
    # Get holdings from portfolio data
    holdings = portfolio.get('holdings', [])
    
    logger.info(f"Processing {len(holdings)} holdings for start value calculation (segment XIRR)")
    
    if len(holdings) == 0:
        logger.warning(f"No holdings found for client {client_id} as of {start_date}")
    
    for holding in holdings:
        security_id = holding.get('security_id')
        if not security_id:
            continue
        
        security = Security.query.get(security_id)
        if not security:
            continue
        
        # Get asset class
        asset_class_name = 'Unknown'
        
        # First try: Get from asset_class relationship
        if security.asset_class:
            asset_class_name = security.asset_class.name
        # Second try: Get from meta_data
        elif security.meta_data:
            try:
                meta = json.loads(security.meta_data) if isinstance(security.meta_data, str) else security.meta_data
                asset_class_name = meta.get('asset_class', 'Unknown')
            except:
                pass
        
        # Fallback: Use security_type
        if asset_class_name == 'Unknown':
            asset_class_name = getattr(security, 'security_type', 'Equity')
        
        # Initialize if not exists
        if asset_class_name not in asset_class_values:
            asset_class_values[asset_class_name] = 0.0
        
        # Add start value
        start_value = holding.get('current_value', 0.0)
        asset_class_values[asset_class_name] += float(start_value)
    
    # Log summary
    logger.info(f"=== START VALUE BY ASSET CLASS (for segment XIRR) ===")
    logger.info(f"Total asset classes with holdings: {len(asset_class_values)}")
    total_value = sum(asset_class_values.values())
    for asset_class, value in asset_class_values.items():
        logger.info(f"Asset class '{asset_class}': ₹{value:,.2f}")
    logger.info(f"Total start portfolio value: ₹{total_value:,.2f}")
    
    if len(asset_class_values) == 0:
        logger.warning(f"No asset class values found for client {client_id} as of {start_date}")
    
    return asset_class_values


def get_asset_class_cashflows_from_trades(client_id, start_date, end_date):
    """
    Extract cashflows grouped by asset class from transaction data.
    Cashflows include:
    1. Start value of holdings at start_date (negative, as initial investment)
    2. BUY/SELL transactions during the period
    
    Args:
        client_id: Client ID
        start_date: Period start date
        end_date: Period end date
    
    Returns:
        dict: {
            'Equity': [(date1, amount1), (date2, amount2), ...],
            'Debt': [(date1, amount1), ...],
            ...
        }
    """
    from models import AssetClass
    import json
    
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get start portfolio values by asset class
    try:
        start_values_by_asset_class = get_start_value_by_asset_class(client_id, start_date)
        logger.info(f"Retrieved start values for {len(start_values_by_asset_class)} asset classes")
    except Exception as e:
        logger.error(f"Error getting start values by asset class: {str(e)}", exc_info=True)
        # Continue with empty start values - transactions will still be processed
        start_values_by_asset_class = {}
        logger.warning(f"Continuing segment XIRR calculation without start values")
    
    # Get all transactions in the period (BUY and SELL only)
    transactions = Transaction.query.filter(
        and_(
            Transaction.client_id == client_id,
            Transaction.transaction_date >= start_datetime,
            Transaction.transaction_date <= end_datetime,
            Transaction.type.in_(['BUY', 'SELL'])
        )
    ).all()
    
    logger.info(f"Found {len(transactions)} transactions for segment XIRR calculation")
    
    # Group cashflows by asset class based on actual transactions
    # Each transaction's cashflow is assigned to its security's asset class
    asset_class_cashflows = {}
    
    # First, add start values as initial cashflows (negative = investment)
    for asset_class_name, start_value in start_values_by_asset_class.items():
        if start_value > 0:
            if asset_class_name not in asset_class_cashflows:
                asset_class_cashflows[asset_class_name] = []
            # Start value is negative (investment/outflow) at start_date
            asset_class_cashflows[asset_class_name].append((start_date, -float(start_value)))
            logger.debug(f"Added start value: {asset_class_name} - ₹{-start_value:,.2f} on {start_date}")
    
    # Process transactions and add to asset class cashflows
    for txn in transactions:
        # Get asset class name
        asset_class_name = 'Unknown'
        
        if txn.security:
            # First try: Get from asset_class relationship
            if txn.security.asset_class:
                asset_class_name = txn.security.asset_class.name
            # Second try: Get from meta_data
            elif txn.security.meta_data:
                try:
                    meta = json.loads(txn.security.meta_data) if isinstance(txn.security.meta_data, str) else txn.security.meta_data
                    asset_class_name = meta.get('asset_class', meta.get('security_type', 'Unknown'))
                except:
                    pass
            
            # Fallback: Use security_type or sector
            if asset_class_name == 'Unknown':
                asset_class_name = getattr(txn.security, 'security_type', getattr(txn.security, 'sector', 'Unknown'))
        
        # Initialize if not exists
        if asset_class_name not in asset_class_cashflows:
            asset_class_cashflows[asset_class_name] = []
        
        # BUY = negative cashflow (investment/outflow)
        # SELL = positive cashflow (withdrawal/inflow)
        amount = float(txn.amount)
        if txn.type == 'BUY':
            amount = -amount  # Negative for investment
        # SELL is already positive (withdrawal)
        
        # Use transaction date (convert to date if datetime)
        txn_date = txn.transaction_date
        if isinstance(txn_date, datetime):
            txn_date = txn_date.date()
        
        asset_class_cashflows[asset_class_name].append((txn_date, amount))
        logger.debug(f"Added {txn.type} transaction: {asset_class_name} - ₹{amount:,.2f} on {txn_date}")
    
    # Log summary - includes start values + transactions
    logger.info(f"=== ASSET CLASS CASHFLOWS (Start Value + Transactions) ===")
    logger.info(f"Total asset classes with cashflows: {len(asset_class_cashflows)}")
    for asset_class, cashflows in asset_class_cashflows.items():
        # Separate start value from transactions
        start_value_cf = [cf for cf in cashflows if cf[0] == start_date and cf[1] < 0]
        transaction_cfs = [cf for cf in cashflows if cf[0] != start_date or cf[1] >= 0]
        
        start_value = sum(abs(amount) for _, amount in start_value_cf)
        total_invested = sum(abs(amount) for _, amount in transaction_cfs if amount < 0)
        total_withdrawn = sum(amount for _, amount in transaction_cfs if amount > 0)
        net_cashflow = sum(amount for _, amount in cashflows)
        
        logger.info(f"Asset class '{asset_class}': "
                   f"Start Value: ₹{start_value:,.2f}, "
                   f"Transactions: {len(transaction_cfs)}, "
                   f"Invested: ₹{total_invested:,.2f}, Withdrawn: ₹{total_withdrawn:,.2f}, "
                   f"Net: ₹{net_cashflow:,.2f}")
    
    if len(asset_class_cashflows) == 0:
        logger.warning(f"No asset class cashflows found for client {client_id} in period {start_date} to {end_date}")
    
    return asset_class_cashflows


def get_current_value_by_asset_class(client_id, end_date):
    """
    Get current portfolio value grouped by asset class.
    
    Args:
        client_id: Client ID
        end_date: Date to get portfolio value
    
    Returns:
        dict: {
            'Equity': current_value,
            'Debt': current_value,
            ...
        }
    """
    from models import AssetClass
    import json
    
    # Get portfolio by date to get accurate holdings with prices
    portfolio = get_client_portfolio_by_date(client_id, end_date)
    
    asset_class_values = {}
    
    # Get holdings from portfolio data
    holdings = portfolio.get('holdings', [])
    
    logger.info(f"Processing {len(holdings)} holdings for segment value calculation")
    
    if len(holdings) == 0:
        logger.warning(f"No holdings found for client {client_id} as of {end_date}")
    
    for holding in holdings:
        security_id = holding.get('security_id')
        if not security_id:
            continue
        
        security = Security.query.get(security_id)
        if not security:
            continue
        
        # Get asset class
        asset_class_name = 'Unknown'
        
        # First try: Get from asset_class relationship
        if security.asset_class:
            asset_class_name = security.asset_class.name
        # Second try: Get from meta_data
        elif security.meta_data:
            try:
                meta = json.loads(security.meta_data) if isinstance(security.meta_data, str) else security.meta_data
                asset_class_name = meta.get('asset_class', 'Unknown')
            except:
                pass
        
        # Fallback: Use security_type
        if asset_class_name == 'Unknown':
            asset_class_name = getattr(security, 'security_type', 'Equity')
        
        # Initialize if not exists
        if asset_class_name not in asset_class_values:
            asset_class_values[asset_class_name] = 0.0
        
        # Add current value
        current_value = holding.get('current_value', 0.0)
        asset_class_values[asset_class_name] += float(current_value)
    
    # Log summary
    logger.info(f"=== ASSET CLASS VALUES SUMMARY ===")
    logger.info(f"Total asset classes with holdings: {len(asset_class_values)}")
    total_value = sum(asset_class_values.values())
    for asset_class, value in asset_class_values.items():
        logger.info(f"Asset class '{asset_class}': ₹{value:,.2f}")
    logger.info(f"Total portfolio value: ₹{total_value:,.2f}")
    
    if len(asset_class_values) == 0:
        logger.warning(f"No asset class values found for client {client_id} as of {end_date}")
    
    return asset_class_values

