"""
Performance API - Portfolio Performance Analytics
"""
import logging
from datetime import datetime, timedelta, date
from decimal import Decimal
from flask import Blueprint, request
from sqlalchemy import func, desc, and_

from api.core.response import APIResponse
# from api.core.decorators import api_auth_required, api_rate_limit
from api.core.exceptions import ValidationError, NotFoundError
from models import db, Client, Security, Holding, Transaction, Cashflow, Benchmark, BenchmarkData
# Removed adjustments imports - these are only needed for enhanced review

logger = logging.getLogger(__name__)

# Create blueprint
performance_bp = Blueprint('performance', __name__)

# ============================================================================
# ADJUSTMENT API HELPERS
# ============================================================================

# Removed get_adjusted_snapshot and get_adjusted_period functions
# These are only needed for enhanced review and caused performance issues in client view

# ============================================================================
# PERFORMANCE API ENDPOINTS
# ============================================================================

@performance_bp.route('/<int:client_id>/performance', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_client_performance(client_id):
    """
    Get portfolio performance analytics for a client
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get query parameters
        period = request.args.get('period', '1Y')  # 1M, 3M, 6M, 1Y, 2Y, 5Y, ALL
        include_transactions = request.args.get('include_transactions', 'false').lower() == 'true'
        
        # Calculate date range
        end_date = datetime.now()
        start_date = calculate_start_date(period, end_date)
        
        # Get holdings summary
        holdings_summary = get_holdings_summary(client_id)
        
        # Get cashflow summary
        cashflow_summary = get_cashflow_summary(client_id, start_date, end_date)
        
        # Get transaction summary
        transaction_summary = get_transaction_summary(client_id, start_date, end_date) if include_transactions else None
        
        # Calculate performance metrics
        performance_metrics = calculate_performance_metrics(
            holdings_summary, 
            cashflow_summary, 
            transaction_summary
        )
        
        # Prepare response data
        performance_data = {
            'client_id': client_id,
            'period': period,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'holdings_summary': holdings_summary,
            'cashflow_summary': cashflow_summary,
            'transaction_summary': transaction_summary,
            'performance_metrics': performance_metrics
        }
        
        return APIResponse.success(
            data=performance_data,
            message=f"Performance analytics for client {client_id} ({period})"
        )
        
    except Exception as e:
        logger.error(f"Error getting performance for client {client_id}: {str(e)}")
        raise

@performance_bp.route('/<int:client_id>/performance/returns', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_performance_returns(client_id):
    """
    Get detailed returns analysis for a client
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get query parameters
        period = request.args.get('period', '1Y')
        frequency = request.args.get('frequency', 'monthly')  # daily, weekly, monthly
        
        # Calculate date range
        end_date = datetime.now()
        start_date = calculate_start_date(period, end_date)
        
        # Get holdings
        holdings = Holding.query.filter_by(client_id=client_id).all()
        
        if not holdings:
            return APIResponse.success(
                data={
                    'returns_data': [],
                    'summary': {
                        'total_return': 0,
                        'annualized_return': 0,
                        'volatility': 0,
                        'sharpe_ratio': 0
                    }
                },
                message=f"No holdings found for client {client_id}"
            )
        
        # Calculate returns data
        returns_data = calculate_returns_data(holdings, start_date, end_date, frequency)
        
        # Calculate summary metrics
        summary_metrics = calculate_summary_metrics(returns_data)
        
        response_data = {
            'returns_data': returns_data,
            'summary': summary_metrics
        }
        
        return APIResponse.success(
            data=response_data,
            message=f"Returns analysis for client {client_id} ({period}, {frequency})"
        )
        
    except Exception as e:
        logger.error(f"Error getting returns for client {client_id}: {str(e)}")
        raise

@performance_bp.route('/<int:client_id>/performance/risk', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_performance_risk(client_id):
    """
    Get risk analysis for a client's portfolio
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get holdings
        holdings = Holding.query.filter_by(client_id=client_id).all()
        
        if not holdings:
            return APIResponse.success(
                data={
                    'risk_metrics': {},
                    'concentration_analysis': {},
                    'volatility_analysis': {}
                },
                message=f"No holdings found for client {client_id}"
            )
        
        # Calculate risk metrics
        risk_metrics = calculate_risk_metrics(holdings)
        
        # Calculate concentration analysis
        concentration_analysis = calculate_concentration_analysis(holdings)
        
        # Calculate volatility analysis
        volatility_analysis = calculate_volatility_analysis(holdings)
        
        response_data = {
            'risk_metrics': risk_metrics,
            'concentration_analysis': concentration_analysis,
            'volatility_analysis': volatility_analysis
        }
        
        return APIResponse.success(
            data=response_data,
            message=f"Risk analysis for client {client_id}"
        )
        
    except Exception as e:
        logger.error(f"Error getting risk analysis for client {client_id}: {str(e)}")
        raise

@performance_bp.route('/<int:client_id>/performance/xirr', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_client_xirr(client_id):
    """
    Get XIRR and simple absolute return for a client (cashflows + terminal portfolio value).

    Query params:
        terminal_value (float, optional): Market value to use as XIRR terminal; when omitted,
        uses the same PriceService-based total as the V2 holdings summary.
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get all cashflows for the client
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        
        if not cashflows:
            return APIResponse.success(
                data={
                    'xirr': 0.0,
                    'total_invested': 0.0,
                    'total_withdrawn': 0.0,
                    'net_investment': 0.0,
                    'current_value': 0.0,
                    'absolute_return': 0.0,
                    'cashflows_count': 0
                },
                message=f"No cashflows found for client {client_id}"
            )

        # Terminal portfolio value: optional query override (must match UI / V2 summary)
        terminal_override = request.args.get("terminal_value", type=float)
        if terminal_override is not None and terminal_override >= 0:
            current_value = float(terminal_override)
        else:
            from services.portfolio_valuation_service import PortfolioValuationService

            current_value = PortfolioValuationService.total_market_value_for_client(client_id)

        # Prepare cashflow data for XIRR calculation
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
        
        # Calculate XIRR
        xirr, total_invested, total_withdrawn, net_investment, absolute_return = calculate_xirr(cashflow_data, current_value)
        
        response_data = {
            'xirr': xirr,
            'xirr_percent': xirr * 100,
            'total_invested': total_invested,
            'total_withdrawn': total_withdrawn,
            'net_investment': net_investment,
            'current_value': current_value,
            'absolute_return': absolute_return,
            'cashflows_count': len(cashflows)
        }
        
        return APIResponse.success(
            data=response_data,
            message=f"XIRR calculation for client {client_id}"
        )
        
    except Exception as e:
        logger.error(f"Error calculating XIRR for client {client_id}: {str(e)}")
        raise


@performance_bp.route('/<int:client_id>/performance/benchmark', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_benchmark_comparison(client_id):
    """
    Get benchmark comparison (Nifty XIRR) for a client
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get query parameters
        benchmark_id = request.args.get('benchmark_id', 1, type=int)  # Default to NIFTY 50
        
        # Calculate Nifty XIRR
        nifty_xirr, nifty_current_value, nifty_absolute_return = calculate_nifty_xirr(client_id)
        
        # Get client's actual XIRR for comparison
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        from services.portfolio_valuation_service import PortfolioValuationService

        current_value = PortfolioValuationService.total_market_value_for_client(client_id)

        client_xirr = 0.0
        if cashflows:
            cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
            client_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
        
        # Calculate outperformance
        outperformance = (client_xirr - nifty_xirr) * 100 if nifty_xirr != 0 else 0
        
        # Get benchmark details
        benchmark = Benchmark.query.get(benchmark_id)
        benchmark_name = benchmark.name if benchmark else "NIFTY 50"
        
        response_data = {
            'benchmark_name': benchmark_name,
            'benchmark_id': benchmark_id,
            'client_xirr': client_xirr,
            'client_xirr_percent': client_xirr * 100,
            'benchmark_xirr': nifty_xirr,
            'benchmark_xirr_percent': nifty_xirr * 100,
            'outperformance': outperformance,
            'client_current_value': current_value,
            'benchmark_current_value': nifty_current_value,
            'client_absolute_return': ((current_value - sum(float(cf.amount) for cf in cashflows if cf.amount < 0)) / sum(float(cf.amount) for cf in cashflows if cf.amount < 0) * 100) if cashflows else 0,
            'benchmark_absolute_return': nifty_absolute_return
        }
        
        return APIResponse.success(
            data=response_data,
            message=f"Benchmark comparison for client {client_id} vs {benchmark_name}"
        )
        
    except Exception as e:
        logger.error(f"Error getting benchmark comparison for client {client_id}: {str(e)}")
        raise

@performance_bp.route('/<int:client_id>/performance/calendar-year-xirr', methods=['GET'])
@performance_bp.route('/clients/<int:client_id>/performance/calendar-year-xirr', methods=['GET'])
def get_calendar_year_xirr(client_id):
    """
    Get calendar-year portfolio XIRR compared with Nifty.

    The response includes verification_rows for each year so users can audit
    the exact signed date-wise cashflow schedule used for XIRR.
    """
    try:
        Client.query.get_or_404(client_id)
        benchmark_id = request.args.get('benchmark_id', 1, type=int)

        from services.calendar_year_xirr_service import CalendarYearXirrService

        response_data = CalendarYearXirrService.calculate(
            client_id=client_id,
            benchmark_id=benchmark_id,
        )

        return APIResponse.success(
            data=response_data,
            message=f"Calendar-year XIRR for client {client_id}"
        )
    except Exception as e:
        logger.error(f"Error getting calendar-year XIRR for client {client_id}: {str(e)}")
        raise

@performance_bp.route('/<int:client_id>/performance/analytics', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_comprehensive_analytics(client_id):
    """
    Get comprehensive performance analytics including XIRR, benchmark comparison, and risk metrics
    
    Query Parameters:
        - start_date (string, optional): Start date for period analytics (YYYY-MM-DD)
        - end_date (string, optional): End date for period analytics (YYYY-MM-DD)
        - include_trades (boolean, optional): Include period trade analytics (default: True)
        - include_sectors (boolean, optional): Include sector performance (default: True)
    """
    try:
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        
        # Get period parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        include_trades = request.args.get('include_trades', 'true').lower() == 'true'
        include_sectors = request.args.get('include_sectors', 'true').lower() == 'true'
        
        # Parse dates
        period_start = None
        period_end = None
        if start_date_str:
            try:
                period_start = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValidationError(f"Invalid start_date format: {start_date_str}. Use YYYY-MM-DD")
        
        if end_date_str:
            try:
                period_end = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                raise ValidationError(f"Invalid end_date format: {end_date_str}. Use YYYY-MM-DD")
        
        # Validate period
        if period_start and period_end and period_start > period_end:
            raise ValidationError("start_date must be before end_date")
        
        logger.info(f"Analytics request for client {client_id}: period {period_start} to {period_end}")
        
        # Get all required data
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        
        # Get current holdings from database (simple and fast)
        holdings = Holding.query.filter_by(client_id=client_id).all()
        
        # Check for special date 1-Jan-2000 in cashflows
        special_date_warning = False
        special_date = date(2000, 1, 1)
        for cf in cashflows:
            if cf.date.date() == special_date:
                special_date_warning = True
                break
        
        if not cashflows and not holdings:
            return APIResponse.success(
                data={
                    'xirr_analysis': {},
                    'benchmark_comparison': {},
                    'risk_metrics': {},
                    'holdings_summary': {},
                    'cashflow_summary': {}
                },
                message=f"No data found for client {client_id}"
            )
        
        # Calculate current_value from holdings (quantity * current_price)
        current_value = 0
        for holding in holdings:
            if holding.security and holding.security.current_price:
                quantity = float(holding.quantity) if holding.quantity else 0
                current_price = float(holding.security.current_price) if holding.security.current_price else 0
                holding_value = quantity * current_price
                current_value += holding_value
        xirr_data = {}
        if cashflows:
            cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
            xirr, total_invested, total_withdrawn, net_investment, absolute_return = calculate_xirr(cashflow_data, current_value)
            xirr_data = {
                'xirr': xirr,
                'xirr_percent': xirr * 100,
                'total_invested': total_invested,
                'total_withdrawn': total_withdrawn,
                'net_investment': net_investment,
                'current_value': current_value,
                'absolute_return': absolute_return
            }
        
        # Calculate benchmark comparison
        nifty_xirr, nifty_current_value, nifty_absolute_return = calculate_nifty_xirr(client_id)
        # Calculate portfolio vs Nifty value comparison
        portfolio_current_value = xirr_data.get('current_value', 0)
        portfolio_vs_nifty_value = portfolio_current_value - nifty_current_value
        portfolio_vs_nifty_percent = (portfolio_vs_nifty_value / nifty_current_value * 100) if nifty_current_value > 0 else 0
        
        benchmark_data = {
            'nifty_xirr': nifty_xirr,
            'nifty_xirr_percent': nifty_xirr * 100,
            'nifty_current_value': nifty_current_value,
            'nifty_absolute_return': nifty_absolute_return,
            'nifty_investment_value': nifty_current_value,  # Same as nifty_current_value for consistency
            'nifty_total_invested': xirr_data.get('total_invested', 0),  # Total amount invested
            'nifty_value_added': nifty_current_value - xirr_data.get('total_invested', 0),  # Value added over investment
            'portfolio_vs_nifty_value': portfolio_vs_nifty_value,  # Portfolio value vs Nifty investment
            'portfolio_vs_nifty_percent': portfolio_vs_nifty_percent,  # Portfolio vs Nifty percentage
            'outperformance': (xirr_data.get('xirr', 0) - nifty_xirr) * 100 if nifty_xirr != 0 else 0
        }
        
        # Calculate risk metrics
        risk_metrics = calculate_risk_metrics(holdings, client_id) if holdings else {}
        
        # Calculate diversification metrics
        diversification_metrics = calculate_diversification_metrics(holdings) if holdings else {}
        
        # Calculate holdings summary from regular holdings
        total_cost = 0
        total_unrealized_pnl = 0
        for holding in holdings:
            if holding.security and holding.security.current_price:
                quantity = float(holding.quantity) if holding.quantity else 0
                current_price = float(holding.security.current_price) if holding.security.current_price else 0
                average_price = float(holding.average_price) if holding.average_price else 0
                
                holding_current_value = quantity * current_price
                cost = quantity * average_price
                unrealized_pnl = holding_current_value - cost
                
                total_cost += cost
                total_unrealized_pnl += unrealized_pnl
        
        holdings_summary = {
            'total_holdings': len(holdings),
            'total_value': current_value,
            'total_cost': total_cost,
            'total_unrealized_pnl': total_unrealized_pnl
        }
        
        print(f"DEBUG: API - holdings_summary: {holdings_summary}")
        
        # Cashflow summary
        cashflow_summary = {
            'total_cashflows': len(cashflows),
            'total_invested': sum(float(cf.amount) for cf in cashflows if cf.amount < 0),
            'total_withdrawn': sum(float(cf.amount) for cf in cashflows if cf.amount > 0),
            'net_cashflow': sum(float(cf.amount) for cf in cashflows)
        }
        
        response_data = {
            'xirr_analysis': xirr_data,
            'benchmark_comparison': benchmark_data,
            'risk_metrics': risk_metrics,
            'diversification_metrics': diversification_metrics,
            'holdings_summary': holdings_summary,
            'cashflow_summary': cashflow_summary,
            'special_date_warning': special_date_warning
        }

        try:
            from services.calendar_year_xirr_service import CalendarYearXirrService

            response_data['calendar_year_xirr'] = CalendarYearXirrService.calculate(client_id)
        except Exception as e:
            logger.warning(f"Could not add calendar-year XIRR for client {client_id}: {str(e)}")
            response_data['calendar_year_xirr'] = {'success': False, 'rows': [], 'error': str(e)}
        
        # Add period-based analytics if date range provided
        if period_start or period_end:
            logger.info(f"Adding period analytics for {period_start} to {period_end}")
            
            if include_trades:
                try:
                    trade_analytics = calculate_period_trade_analytics(
                        client_id, holdings, period_start, period_end
                    )
                    response_data['period_trade_analytics'] = trade_analytics
                    logger.info(f"Added trade analytics: {len(trade_analytics.get('stocks_added_in_period', []))} stocks added, {len(trade_analytics.get('stocks_sold_in_period', []))} stocks sold")
                except Exception as e:
                    logger.error(f"Error calculating trade analytics: {str(e)}")
                    response_data['period_trade_analytics'] = {'error': str(e)}
            
            if include_sectors:
                try:
                    sector_performance = calculate_sector_performance_in_period(
                        client_id, holdings, period_start, period_end
                    )
                    response_data['sector_performance_in_period'] = sector_performance
                    logger.info(f"Added sector performance: {len(sector_performance.get('sector_wise_performance', []))} sectors analyzed")
                except Exception as e:
                    logger.error(f"Error calculating sector performance: {str(e)}")
                    response_data['sector_performance_in_period'] = {'error': str(e)}
        
        # Add warning message if special date found
        if special_date_warning:
            response_data['warning_message'] = "⚠️ WARNING: We do not have exact date of all trades. Please update the cashflow dates to get correct performance details."
        
        print(f"DEBUG: API - Final response_data keys: {response_data.keys()}")
        
        return APIResponse.success(
            data=response_data,
            message=f"Comprehensive analytics for client {client_id}" + (f" (period: {period_start} to {period_end})" if period_start or period_end else "")
        )
        
    except BrokenPipeError as e:
        # Client disconnected before response was fully sent
        logger.warning(f"Client disconnected during analytics calculation for client {client_id} ({client.name}): Broken pipe")
        # Don't raise the exception - the client already disconnected
        return APIResponse.error(
            message="Client disconnected before response could be sent",
            status_code=499  # Client Closed Request
        )
    except ConnectionError as e:
        # Connection issues
        logger.warning(f"Connection error during analytics for client {client_id} ({client.name}): {str(e)}")
        return APIResponse.error(
            message="Connection error occurred",
            status_code=503
        )
    except Exception as e:
        logger.error(f"Error getting comprehensive analytics for client {client_id}: {str(e)}")
        raise

@performance_bp.route('/<int:client_id>/performance/portfolio-details', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_portfolio_details(client_id):
    """
    Get detailed portfolio breakdown including top sectors, investments, performers, etc.
    
    Query Parameters:
        - basis (string, optional): adjusted/unadjusted price basis (default: adjusted)
    """
    try:
        print(f"=== DEBUG: Portfolio Details API for Client {client_id} ===")
        
        # Get query parameters
        basis = request.args.get('basis', 'adjusted').lower()
        
        # Verify client exists
        client = Client.query.get_or_404(client_id)
        print(f"DEBUG: Found client: {client.name}")
        
        # Check for special date 1-Jan-2000 in cashflows
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        special_date_warning = False
        special_date = date(2000, 1, 1)
        for cf in cashflows:
            if cf.date.date() == special_date:
                special_date_warning = True
                break
        
        # Get holdings with security details
        holdings = db.session.query(Holding, Security).join(
            Security, Holding.security_id == Security.id
        ).filter(Holding.client_id == client_id).all()
        
        print(f"DEBUG: Found {len(holdings)} holdings")
        
        if not holdings:
            return APIResponse.success(
                data={
                    'top_sectors': [],
                    'top_investments': [],
                    'most_profitable': [],
                    'wealth_creators': [],
                    'bottom_performers': [],
                    'recent_investments': [],
                    'smallest_holdings': []
                },
                message=f"No holdings found for client {client_id}"
            )
        
        # Process holdings data
        holdings_data = []
        
        # ✅ For current portfolio (not historical), don't use reconstruction API
        # Just use raw Holding table data with current prices
        if basis == 'adjusted':
            # Note: 'adjusted' basis for current portfolio just means using current market prices
            # No historical reconstruction needed for today's date
            try:
                # Use regular holdings with current prices (no reconstruction needed)
                pass  # Fall through to regular holdings processing
            except Exception as e:
                logger.warning(f"Basis=adjusted for current date, using regular holdings: {str(e)}")
        
        # ✅ Use regular holdings processing for current portfolio
        # For today's portfolio, use actual Holding table data (no reconstruction)
        if not holdings_data:
            for holding, security in holdings:
                    if security and security.current_price:
                        quantity = float(holding.quantity) if holding.quantity else 0
                        current_price = float(security.current_price)
                        average_price = float(holding.average_price) if holding.average_price else 0
                        
                        current_value = quantity * current_price
                        cost = quantity * average_price
                        unrealized_pnl = current_value - cost
                        pnl_percent = (unrealized_pnl / cost * 100) if cost > 0 else 0
                        
                        # Get sector from meta_data or fallback
                        sector = 'Unknown'
                        if security.meta_data:
                            try:
                                import json
                                meta = json.loads(security.meta_data) if isinstance(security.meta_data, str) else security.meta_data
                                sector = meta.get('sector', meta.get('industry', 'Unknown'))
                            except:
                                sector = 'Unknown'
                        
                        holdings_data.append({
                            'symbol': security.symbol,
                            'name': security.name,
                            'quantity': quantity,
                            'current_price': current_price,
                            'average_price': average_price,
                            'current_value': current_value,
                            'cost': cost,
                            'unrealized_pnl': unrealized_pnl,
                            'pnl_percent': pnl_percent,
                            'sector': sector,
                            'basis': 'unadjusted'
                        })
        else:
            # Use regular holdings processing for unadjusted basis
            for holding, security in holdings:
                if security and security.current_price:
                    quantity = float(holding.quantity) if holding.quantity else 0
                    current_price = float(security.current_price)
                    average_price = float(holding.average_price) if holding.average_price else 0
                    
                    current_value = quantity * current_price
                    cost = quantity * average_price
                    unrealized_pnl = current_value - cost
                    pnl_percent = (unrealized_pnl / cost * 100) if cost > 0 else 0
                    
                    # Get sector from meta_data or fallback
                    sector = 'Unknown'
                    if security.meta_data:
                        try:
                            import json
                            meta = json.loads(security.meta_data) if isinstance(security.meta_data, str) else security.meta_data
                            sector = meta.get('sector', meta.get('industry', 'Unknown'))
                        except:
                            sector = 'Unknown'
                    
                    holdings_data.append({
                        'symbol': security.symbol,
                        'name': security.name,
                        'quantity': quantity,
                        'current_price': current_price,
                        'average_price': average_price,
                        'current_value': current_value,
                        'cost': cost,
                        'unrealized_pnl': unrealized_pnl,
                        'pnl_percent': pnl_percent,
                        'sector': sector,
                        'basis': 'unadjusted'
                    })
        
        # Calculate top sectors
        sector_totals = {}
        for holding in holdings_data:
            sector = holding['sector']
            if sector not in sector_totals:
                sector_totals[sector] = {'value': 0, 'count': 0}
            sector_totals[sector]['value'] += holding['current_value']
            sector_totals[sector]['count'] += 1
        
        top_sectors = sorted(sector_totals.items(), key=lambda x: x[1]['value'], reverse=True)[:5]
        
        # Sort holdings by different criteria
        top_investments = sorted(holdings_data, key=lambda x: x['current_value'], reverse=True)[:5]
        most_profitable = sorted(holdings_data, key=lambda x: x['unrealized_pnl'], reverse=True)[:5]
        wealth_creators = sorted(holdings_data, key=lambda x: x['pnl_percent'], reverse=True)[:5]
        bottom_performers = sorted(holdings_data, key=lambda x: x['unrealized_pnl'])[:5]
        smallest_holdings = sorted(holdings_data, key=lambda x: x['current_value'])[:5]
        
        # Get recent investments (last 6 months)
        six_months_ago = datetime.now() - timedelta(days=180)
        recent_transactions = db.session.query(Transaction, Security).join(
            Security, Transaction.security_id == Security.id
        ).filter(
            Transaction.client_id == client_id,
            Transaction.type == 'BUY',
            Transaction.transaction_date >= six_months_ago
        ).order_by(Transaction.transaction_date.desc()).limit(5).all()
        
        recent_investments = []
        for transaction, security in recent_transactions:
            recent_investments.append({
                'symbol': security.symbol,
                'name': security.name,
                'date': transaction.transaction_date.strftime('%Y-%m-%d'),
                'quantity': float(transaction.quantity),
                'price': float(transaction.price),
                'amount': float(transaction.quantity) * float(transaction.price)
            })
        
        response_data = {
            'top_sectors': [{'sector': sector, 'value': data['value'], 'count': data['count']} for sector, data in top_sectors],
            'top_investments': top_investments,
            'most_profitable': most_profitable,
            'wealth_creators': wealth_creators,
            'bottom_performers': bottom_performers,
            'recent_investments': recent_investments,
            'smallest_holdings': smallest_holdings,
            'special_date_warning': special_date_warning
        }
        
        # Add warning message if special date found
        if special_date_warning:
            response_data['warning_message'] = "⚠️ WARNING: We do not have exact date of all trades. Please update the cashflow dates to get correct performance details."
        
        return APIResponse.success(
            data=response_data,
            message=f"Portfolio details for client {client_id}"
        )
        
    except BrokenPipeError as e:
        # Client disconnected before response was fully sent
        logger.warning(f"Client disconnected during portfolio details for client {client_id} ({client.name}): Broken pipe")
        return APIResponse.error(
            message="Client disconnected before response could be sent",
            status_code=499
        )
    except ConnectionError as e:
        # Connection issues
        logger.warning(f"Connection error during portfolio details for client {client_id} ({client.name}): {str(e)}")
        return APIResponse.error(
            message="Connection error occurred",
            status_code=503
        )
    except Exception as e:
        logger.error(f"Error getting portfolio details for client {client_id}: {str(e)}")
        raise

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def calculate_xirr(cashflows, current_value, end_date=None):
    """
    Calculate XIRR (Internal Rate of Return) for a series of cashflows and current value.
    
    Args:
        cashflows: List of tuples (date, amount) where amount is positive for inflows and negative for outflows
        current_value: Current portfolio value (not included as a cashflow)
        end_date: Optional end date for the final value. If None, uses datetime.now()
    
    Returns:
        tuple: (xirr, total_invested, total_withdrawn, net_investment, absolute_return)
    """
    if not cashflows:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    
    # Initialize variables
    total_invested = Decimal('0.0')
    total_withdrawn = Decimal('0.0')
    
    # Sort cashflows by date
    cashflows = sorted(cashflows, key=lambda x: x[0])
    
    # Calculate total invested and withdrawn
    for cashflow_date, amount in cashflows:
        # Convert amount to Decimal if it's not already
        if isinstance(amount, (int, float)):
            amount = Decimal(str(amount))
        if amount < 0:  # Negative amount = INVESTMENT
            total_invested += abs(amount)
        else:  # Positive amount = WITHDRAWAL
            total_withdrawn += amount
    
    net_investment = total_invested - total_withdrawn
    
    # Add current value as final cashflow for XIRR calculation (only if non-zero)
    # For period XIRR, current_value is typically 0 as end_value is already included in cashflows
    # Use end_date if provided, otherwise use datetime.now() for backward compatibility
    current_value_float = float(current_value) if isinstance(current_value, Decimal) else current_value
    if current_value_float != 0:
        if end_date:
            # Use provided end_date (convert to datetime if needed)
            if isinstance(end_date, datetime):
                current_date = end_date
            elif isinstance(end_date, date):
                current_date = datetime.combine(end_date, datetime.min.time())
            else:
                # Try to parse if it's a string or other type
                try:
                    if hasattr(end_date, 'date'):
                        current_date = datetime.combine(end_date.date(), datetime.min.time())
                    else:
                        current_date = datetime.fromisoformat(str(end_date))
                except:
                    current_date = datetime.now()
        else:
            current_date = datetime.now()
        cashflows_with_current = cashflows + [(current_date, current_value_float)]
    else:
        cashflows_with_current = cashflows
    
    # Normalize all dates to datetime objects for consistent subtraction
    # This ensures we can subtract dates/datetimes without type errors
    # Note: datetime is a subclass of date, so check datetime first!
    normalized_cashflows = []
    for cf_date, cf_amount in cashflows_with_current:
        if isinstance(cf_date, datetime):
            # Already a datetime, preserve it (including time component)
            normalized_date = cf_date
        elif isinstance(cf_date, date):
            # Convert date to datetime at midnight
            normalized_date = datetime.combine(cf_date, datetime.min.time())
        else:
            # Try to parse if it's a string or other type
            try:
                if hasattr(cf_date, 'date'):
                    normalized_date = datetime.combine(cf_date.date(), datetime.min.time())
                else:
                    # Try parsing as ISO format string
                    normalized_date = datetime.fromisoformat(str(cf_date))
            except:
                logger.warning(f"Could not normalize date {cf_date}, skipping cashflow")
                continue
        normalized_cashflows.append((normalized_date, cf_amount))
    
    # Convert dates to years from first cashflow (all are now datetime objects)
    # XIRR treats dates as discrete days, so we use .days for consistency with standard XIRR
    # All dates are normalized to consistent times (typically midnight), so .days is appropriate
    first_date = normalized_cashflows[0][0]
    years = [(cf[0] - first_date).days / 365.0 for cf in normalized_cashflows]
    # Convert all amounts to float for XIRR calculation (use normalized cashflows)
    amounts = [float(cf[1]) if isinstance(cf[1], Decimal) else cf[1] for cf in normalized_cashflows]
    
    def xnpv(rate):
        try:
            npv = sum([amount / (1 + rate) ** year for amount, year in zip(amounts, years)])
            return npv
        except (OverflowError, ZeroDivisionError):
            return float('inf')
    
    try:
        # Use scipy's optimize
        from scipy import optimize
        
        # First, try root finding (brentq) - more accurate for XIRR
        # XIRR finds the rate where NPV = 0, which is a root-finding problem
        try:
            # Find a bracket where NPV changes sign
            low_rate = -0.9  # -90% (avoid -1.0 to prevent division by zero)
            high_rate = 5.0  # 500% as upper limit initially
            
            # Check if NPV changes sign in this bracket
            npv_low = xnpv(low_rate)
            npv_high = xnpv(high_rate)
            
            # If both have same sign, expand the bracket
            if npv_low * npv_high > 0:
                # Try wider range
                if npv_low > 0 and npv_high > 0:
                    # NPV is positive even at high rate, try even higher
                    high_rate = 10.0  # 1000%
                    npv_high = xnpv(high_rate)
                elif npv_low < 0 and npv_high < 0:
                    # NPV is negative even at low rate, try lower
                    low_rate = -0.99
                    npv_low = xnpv(low_rate)
            
            # Use root finding if we have opposite signs
            if npv_low * npv_high < 0:
                xirr = optimize.brentq(xnpv, low_rate, high_rate, maxiter=100)
            else:
                # Fall back to minimization if root finding isn't possible
                # This happens when NPV doesn't cross zero (rare but possible)
                result = optimize.minimize_scalar(
                    lambda r: abs(xnpv(r)),
                    bounds=(low_rate, min(high_rate, 5.0)),  # Cap at 500% for minimization
                    method='bounded'
                )
                xirr = result.x if result.success else 0.0
        except (ValueError, RuntimeError) as e:
            # Root finding failed, fall back to minimization
            logger.debug(f"Root finding failed: {str(e)}, falling back to minimization")
            bounds = (-0.99, 5.0)  # Cap at 500% for minimization
            result = optimize.minimize_scalar(
                lambda r: abs(xnpv(r)),
                bounds=bounds,
                method='bounded'
            )
            xirr = result.x if result.success else 0.0
        
        # Log if XIRR is outside typical bounds but don't modify it - display whatever is calculated
        if abs(xirr) > 5.0:  # > 500%
            logger.warning(f"XIRR calculation returned value outside typical range: {xirr} (as decimal, {xirr * 100}%). This may indicate unusual cashflow patterns or calculation issues.")
        
    except ImportError:
        # Fallback if scipy is not available
        logger.warning("scipy not available, XIRR calculation will return 0.0")
        xirr = 0.0
    except Exception as e:
        logger.error(f"Error calculating XIRR: {str(e)}")
        xirr = 0.0
    
    # Calculate absolute return - convert to float for calculation
    net_investment_float = float(net_investment)
    current_value_float = float(current_value) if isinstance(current_value, Decimal) else current_value
    absolute_return = ((current_value_float - net_investment_float) / net_investment_float * 100) if net_investment_float > 0 else 0
    
    return xirr, float(total_invested), float(total_withdrawn), float(net_investment), absolute_return


def _tuple_flow_as_date(flow_date):
    if isinstance(flow_date, datetime):
        return flow_date.date()
    return flow_date


def calculate_nifty_xirr_from_tuples(cashflow_tuples):
    """
    Nifty XIRR for an explicit signed cashflow schedule (same convention as Cashflow rows:
    negative = investment, positive = withdrawal). Simulates Nifty unit accumulation and
    marks terminal value to the latest benchmark price, then runs XIRR on the same flows.

    Used when the portfolio leg is built from the same schedule (e.g. equity-only trade
    cashflows) so benchmark XIRR is comparable to that leg.
    """
    try:
        if not cashflow_tuples:
            return 0.0, 0.0, 0.0

        benchmark = Benchmark.query.filter_by(id=1).first()  # NIFTY 50
        if not benchmark:
            logger.warning("NIFTY 50 benchmark not found")
            return 0.0, 0.0, 0.0

        date_list = [_tuple_flow_as_date(d) for d, _ in cashflow_tuples]
        min_date = min(date_list)
        max_date = max(date_list)

        benchmark_data = BenchmarkData.query.filter_by(benchmark_id=benchmark.id)\
            .filter(BenchmarkData.date >= min_date)\
            .filter(BenchmarkData.date <= max_date)\
            .order_by(BenchmarkData.date).all()

        if not benchmark_data:
            logger.warning("No benchmark data found for the date range")
            return 0.0, 0.0, 0.0

        price_lookup = {bd.date: float(bd.price) for bd in benchmark_data}

        latest_benchmark = BenchmarkData.query.filter_by(benchmark_id=benchmark.id)\
            .order_by(BenchmarkData.date.desc()).first()

        if not latest_benchmark:
            logger.warning("No latest benchmark price found")
            return 0.0, 0.0, 0.0

        latest_price = float(latest_benchmark.price)

        nifty_units = 0.0
        total_invested = 0.0
        total_withdrawn = 0.0
        weighted_total_cost = 0.0

        for cf_date_raw, cf_amount in sorted(cashflow_tuples, key=lambda x: x[0]):
            cf_amount = float(cf_amount)
            cf_date = _tuple_flow_as_date(cf_date_raw)

            benchmark_price = None
            if cf_date in price_lookup:
                benchmark_price = price_lookup[cf_date]
            else:
                available_dates = [d for d in price_lookup.keys() if d <= cf_date]
                if available_dates:
                    closest_date = max(available_dates)
                    benchmark_price = price_lookup[closest_date]

            if benchmark_price is None or benchmark_price <= 0:
                continue

            if cf_amount < 0:
                units_bought = abs(cf_amount) / benchmark_price
                nifty_units += units_bought
                total_invested += abs(cf_amount)
                weighted_total_cost += abs(cf_amount)
            else:
                if nifty_units > 0:
                    units_sold = min(cf_amount / benchmark_price, nifty_units)
                    nifty_units -= units_sold
                    total_withdrawn += cf_amount
                    if nifty_units > 0:
                        reduction_ratio = units_sold / (nifty_units + units_sold)
                        weighted_total_cost -= weighted_total_cost * reduction_ratio

        nifty_current_value = nifty_units * latest_price
        net_investment = total_invested - total_withdrawn

        # Align with BenchmarkService: simple return on net flows, not (index − avg ₹/unit) / avg
        if net_investment > 1e-6:
            nifty_absolute_return = ((nifty_current_value - net_investment) / net_investment) * 100.0
        else:
            nifty_absolute_return = 0.0

        cashflow_data = [(d, float(a)) for d, a in cashflow_tuples]
        nifty_xirr, _, _, _, _ = calculate_xirr(cashflow_data, nifty_current_value)

        return nifty_xirr, nifty_current_value, nifty_absolute_return

    except Exception as e:
        logger.error(f"Error calculating Nifty XIRR from tuples: {str(e)}")
        return 0.0, 0.0, 0.0


def calculate_nifty_xirr(client_id):
    """
    Calculate XIRR assuming client cashflows were invested in Nifty benchmark.
    
    Args:
        client_id: Client ID to calculate Nifty XIRR for
    
    Returns:
        tuple: (nifty_xirr, nifty_current_value, nifty_absolute_return)
    """
    try:
        # Get client cashflows
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        if not cashflows:
            return 0.0, 0.0, 0.0
        cashflow_tuples = [(cf.date, float(cf.amount)) for cf in cashflows]
        return calculate_nifty_xirr_from_tuples(cashflow_tuples)
        
    except Exception as e:
        logger.error(f"Error calculating Nifty XIRR: {str(e)}")
        return 0.0, 0.0, 0.0

def calculate_start_date(period, end_date):
    """Calculate start date based on period"""
    if period == '1M':
        return end_date - timedelta(days=30)
    elif period == '3M':
        return end_date - timedelta(days=90)
    elif period == '6M':
        return end_date - timedelta(days=180)
    elif period == '1Y':
        return end_date - timedelta(days=365)
    elif period == '2Y':
        return end_date - timedelta(days=730)
    elif period == '5Y':
        return end_date - timedelta(days=1825)
    else:  # ALL
        return datetime(2020, 1, 1)  # Default start date

def get_holdings_summary(client_id):
    """Get holdings summary for performance calculation"""
    holdings = Holding.query.filter_by(client_id=client_id).all()
    
    # Calculate current_value from holdings (quantity * current_price)
    total_value = 0
    total_cost = 0
    total_unrealized_pnl = 0
    
    for holding in holdings:
        if holding.security and holding.security.current_price:
            quantity = float(holding.quantity) if holding.quantity else 0
            current_price = float(holding.security.current_price) if holding.security.current_price else 0
            current_value = quantity * current_price
            total_value += current_value
            
            # Calculate cost and P&L
            average_price = float(holding.average_price) if holding.average_price else 0
            cost = quantity * average_price
            total_cost += cost
            total_unrealized_pnl += (current_value - cost)
    
    return {
        'total_holdings': len(holdings),
        'total_value': total_value,
        'total_cost': total_cost,
        'total_unrealized_pnl': total_unrealized_pnl,
        'unrealized_pnl_percent': (total_unrealized_pnl / total_cost * 100) if total_cost > 0 else 0
    }

def get_cashflow_summary(client_id, start_date, end_date):
    """Get cashflow summary for the period"""
    cashflows = Cashflow.query.filter(
        and_(
            Cashflow.client_id == client_id,
            Cashflow.date >= start_date,
            Cashflow.date <= end_date
        )
    ).all()
    
    total_inflow = sum(float(c.amount) for c in cashflows if c.amount < 0)  # Negative amounts are inflows
    total_outflow = sum(float(c.amount) for c in cashflows if c.amount > 0)  # Positive amounts are outflows
    
    return {
        'total_cashflows': len(cashflows),
        'total_inflow': abs(total_inflow),
        'total_outflow': total_outflow,
        'net_cashflow': total_outflow + total_inflow
    }

def get_transaction_summary(client_id, start_date, end_date):
    """Get transaction summary for the period"""
    transactions = Transaction.query.filter(
        and_(
            Transaction.client_id == client_id,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date
        )
    ).all()
    
    total_buy_amount = sum(float(t.amount) for t in transactions if t.type == 'BUY')
    total_sell_amount = sum(float(t.amount) for t in transactions if t.type == 'SELL')
    
    return {
        'total_transactions': len(transactions),
        'buy_transactions': len([t for t in transactions if t.type == 'BUY']),
        'sell_transactions': len([t for t in transactions if t.type == 'SELL']),
        'total_buy_amount': total_buy_amount,
        'total_sell_amount': total_sell_amount,
        'net_transaction_amount': total_sell_amount - total_buy_amount
    }

def calculate_performance_metrics(holdings_summary, cashflow_summary, transaction_summary):
    """Calculate overall performance metrics"""
    total_value = holdings_summary['total_value']
    total_cost = holdings_summary['total_cost']
    
    # Basic performance metrics
    total_return = holdings_summary['total_unrealized_pnl']
    total_return_percent = holdings_summary['unrealized_pnl_percent']
    
    # Calculate time-weighted return (simplified)
    if transaction_summary:
        net_investment = transaction_summary['net_transaction_amount']
        if net_investment != 0:
            time_weighted_return = (total_value - net_investment) / abs(net_investment) * 100
        else:
            time_weighted_return = total_return_percent
    else:
        time_weighted_return = total_return_percent
    
    return {
        'total_return': total_return,
        'total_return_percent': total_return_percent,
        'time_weighted_return_percent': time_weighted_return,
        'current_value': total_value,
        'total_invested': total_cost
    }

def calculate_returns_data(holdings, start_date, end_date, frequency):
    """Calculate returns data for the period"""
    # This is a simplified implementation
    # In a real system, you'd calculate actual historical returns
    
    returns_data = []
    current_date = start_date
    
    while current_date <= end_date:
        # Simplified calculation - in reality, you'd use historical prices
        # Calculate total_value from holdings (quantity * current_price)
        total_value = 0
        total_cost = 0
        for holding in holdings:
            if holding.security and holding.security.current_price:
                quantity = float(holding.quantity) if holding.quantity else 0
                current_price = float(holding.security.current_price) if holding.security.current_price else 0
                total_value += quantity * current_price
                
                # Calculate cost
                average_price = float(holding.average_price) if holding.average_price else 0
                total_cost += quantity * average_price
        
        return_percent = ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0
        
        returns_data.append({
            'date': current_date.isoformat(),
            'value': total_value,
            'return_percent': return_percent
        })
        
        # Increment date based on frequency
        if frequency == 'daily':
            current_date += timedelta(days=1)
        elif frequency == 'weekly':
            current_date += timedelta(weeks=1)
        else:  # monthly
            current_date += timedelta(days=30)
    
    return returns_data

def calculate_summary_metrics(returns_data):
    """Calculate summary metrics from returns data"""
    if not returns_data:
        return {
            'total_return': 0,
            'annualized_return': 0,
            'volatility': 0,
            'sharpe_ratio': 0
        }
    
    # Calculate basic metrics
    total_return = returns_data[-1]['return_percent'] if returns_data else 0
    
    # Calculate volatility (simplified)
    returns = [r['return_percent'] for r in returns_data]
    if len(returns) > 1:
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
        volatility = variance ** 0.5
    else:
        volatility = 0
    
    # Calculate Sharpe ratio (simplified - assuming 0% risk-free rate)
    sharpe_ratio = (total_return / volatility) if volatility > 0 else 0
    
    return {
        'total_return': total_return,
        'annualized_return': total_return,  # Simplified
        'volatility': volatility,
        'sharpe_ratio': sharpe_ratio
    }

def calculate_risk_metrics(holdings, client_id=None):
    """Calculate comprehensive risk metrics for the portfolio"""
    import numpy as np
    from collections import defaultdict
    
    # Calculate total_value and weights from holdings
    total_value = 0
    holding_data = []
    
    for holding in holdings:
        if holding.security and holding.security.current_price:
            quantity = float(holding.quantity) if holding.quantity else 0
            current_price = float(holding.security.current_price) if holding.security.current_price else 0
            current_value = quantity * current_price
            total_value += current_value
            
            # Get sector information from meta_data JSON field (same as server-side)
            sector = 'Unknown'
            if holding.security.meta_data:
                try:
                    import json
                    meta_data = json.loads(holding.security.meta_data)
                    sector = meta_data.get('sector', meta_data.get('industry', 'Unknown'))
                except (json.JSONDecodeError, AttributeError):
                    sector = 'Unknown'
            
            # Fallback to direct fields if meta_data is not available
            if sector == 'Unknown':
                if hasattr(holding.security, 'sector') and holding.security.sector:
                    sector = holding.security.sector
                elif hasattr(holding.security, 'industry') and holding.security.industry:
                    sector = holding.security.industry
            
            holding_data.append({
                'security': holding.security,
                'value': current_value,
                'quantity': quantity,
                'current_price': current_price,
                'sector': sector
            })
    
    if total_value == 0 or not holding_data:
        return {
            'portfolio_volatility': 0,
            'max_drawdown': 0,
            'var_95': 0,
            'beta': 0,
            'alpha': 0,
            'sharpe_ratio': 0,
            'treynor_ratio': 0,
            'information_ratio': 0,
            'calmar_ratio': 0,
            'sortino_ratio': 0
        }
    
    # Calculate portfolio weights
    weights = [h['value'] / total_value for h in holding_data]
    
    # Calculate metrics using available data and industry estimates
    portfolio_beta = calculate_portfolio_beta_simplified(holding_data, weights)
    portfolio_alpha = calculate_portfolio_alpha_simplified(holding_data, weights, client_id)
    portfolio_volatility = calculate_portfolio_volatility_simplified(holding_data, weights)
    sharpe_ratio = calculate_sharpe_ratio_simplified(holding_data, weights, client_id)
    treynor_ratio = calculate_treynor_ratio_simplified(holding_data, weights, client_id)
    information_ratio = calculate_information_ratio_simplified(holding_data, weights, client_id)
    max_drawdown = calculate_max_drawdown_simplified(holding_data, weights, client_id)
    var_95 = calculate_var_95_simplified(holding_data, weights)
    calmar_ratio = calculate_calmar_ratio_simplified(holding_data, weights, client_id)
    sortino_ratio = calculate_sortino_ratio_simplified(holding_data, weights, client_id)
    
    return {
        'portfolio_volatility': portfolio_volatility,
        'max_drawdown': max_drawdown,
        'var_95': var_95,
        'beta': portfolio_beta,
        'alpha': portfolio_alpha,
        'sharpe_ratio': sharpe_ratio,
        'treynor_ratio': treynor_ratio,
        'information_ratio': information_ratio,
        'calmar_ratio': calmar_ratio,
        'sortino_ratio': sortino_ratio
    }

# Simplified risk metric calculations using available data
def calculate_portfolio_beta_simplified(holding_data, weights):
    """Calculate portfolio beta using sector-based estimates"""
    # Industry average betas (simplified approach)
    sector_betas = {
        'Banking': 1.2,
        'Financial Services': 1.3,
        'Technology': 1.1,
        'Healthcare': 0.8,
        'Consumer Goods': 0.9,
        'Energy': 1.4,
        'Manufacturing': 1.0,
        'Real Estate': 1.5,
        'Utilities': 0.7,
        'Telecommunications': 0.9,
        'Electrical Equipment': 1.1,
        'Electrical Equipment Manufacturing': 1.1,
        'Capital Goods - Electrical Equipment': 1.0,
        'Electrical Transformers': 1.1,
        'Electrical Transformers & Rectifiers': 1.1,
        'Electrical & Electronics': 1.1,
        'Electrical Equipment & Cables': 1.1,
        'Electrical Switchgear & Cables': 1.1,
        'Electrical Appliances & Consumer Goods': 0.9,
        'Lighting & Electrical Equipment': 1.0,
        'Electricals': 1.1
    }
    
    portfolio_beta = 0
    unknown_sector_count = 0
    
    for i, holding in enumerate(holding_data):
        sector = holding['sector']  # Use the sector we determined above
        if sector == 'Unknown':
            unknown_sector_count += 1
        beta = sector_betas.get(sector, 1.0)  # Default to 1.0 if sector not found
        portfolio_beta += weights[i] * beta
    
    # If more than 50% of holdings have unknown sectors, return None to indicate insufficient data
    if unknown_sector_count > len(holding_data) * 0.5:
        return None
    
    return round(portfolio_beta, 3)

def calculate_portfolio_alpha_simplified(holding_data, weights, client_id):
    """Calculate portfolio alpha (excess return over expected return)"""
    # This would need portfolio returns vs benchmark returns
    # For now, return a placeholder based on portfolio performance
    try:
        # Get portfolio XIRR and benchmark XIRR
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        if not cashflows:
            return 0.0
            
        # Calculate portfolio current value
        current_value = sum(h['value'] for h in holding_data)
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
        
        # Get benchmark XIRR
        nifty_xirr, _, _ = calculate_nifty_xirr(client_id)
        
        # Alpha = Portfolio Return - (Risk-free rate + Beta * (Market Return - Risk-free rate))
        risk_free_rate = 0.065  # 6.5% (current RBI repo rate)
        portfolio_beta = calculate_portfolio_beta_simplified(holding_data, weights)
        
        alpha = portfolio_xirr - (risk_free_rate + portfolio_beta * (nifty_xirr - risk_free_rate))
        return round(alpha * 100, 2)  # Return as percentage
        
    except Exception as e:
        logger.error(f"Error calculating alpha: {str(e)}")
        return 0.0

def calculate_portfolio_volatility_simplified(holding_data, weights):
    """Calculate portfolio volatility using sector-based estimates"""
    # Industry average volatilities
    sector_volatilities = {
        'Banking': 25.0,
        'Financial Services': 28.0,
        'Technology': 30.0,
        'Healthcare': 20.0,
        'Consumer Goods': 18.0,
        'Energy': 35.0,
        'Manufacturing': 22.0,
        'Real Estate': 40.0,
        'Utilities': 15.0,
        'Telecommunications': 20.0
    }
    
    # Calculate weighted average volatility
    portfolio_volatility = 0
    for i, holding in enumerate(holding_data):
        sector = holding['security'].sector if hasattr(holding['security'], 'sector') else 'Manufacturing'
        volatility = sector_volatilities.get(sector, 22.0)
        portfolio_volatility += weights[i] * volatility
    
    return round(portfolio_volatility, 2)

def calculate_sharpe_ratio_simplified(holding_data, weights, client_id):
    """Calculate Sharpe ratio"""
    try:
        # Get portfolio return
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        if not cashflows:
            return 0.0
            
        current_value = sum(h['value'] for h in holding_data)
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
        
        # Risk-free rate (RBI repo rate)
        risk_free_rate = 0.065
        
        # Portfolio volatility
        portfolio_volatility = calculate_portfolio_volatility_simplified(holding_data, weights) / 100
        
        # Sharpe ratio = (Portfolio Return - Risk-free rate) / Portfolio Volatility
        sharpe_ratio = (portfolio_xirr - risk_free_rate) / portfolio_volatility if portfolio_volatility > 0 else 0
        return round(sharpe_ratio, 3)
        
    except Exception as e:
        logger.error(f"Error calculating Sharpe ratio: {str(e)}")
        return 0.0

def calculate_treynor_ratio_simplified(holding_data, weights, client_id):
    """Calculate Treynor ratio"""
    try:
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        if not cashflows:
            return 0.0
            
        current_value = sum(h['value'] for h in holding_data)
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
        
        risk_free_rate = 0.065
        portfolio_beta = calculate_portfolio_beta_simplified(holding_data, weights)
        
        # Treynor ratio = (Portfolio Return - Risk-free rate) / Beta
        treynor_ratio = (portfolio_xirr - risk_free_rate) / portfolio_beta if portfolio_beta > 0 else 0
        return round(treynor_ratio, 3)
        
    except Exception as e:
        logger.error(f"Error calculating Treynor ratio: {str(e)}")
        return 0.0

def calculate_information_ratio_simplified(holding_data, weights, client_id):
    """Calculate Information ratio"""
    try:
        # Get portfolio and benchmark returns
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        if not cashflows:
            return 0.0
            
        current_value = sum(h['value'] for h in holding_data)
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
        
        nifty_xirr, _, _ = calculate_nifty_xirr(client_id)
        
        # Tracking error (simplified as portfolio volatility)
        tracking_error = calculate_portfolio_volatility_simplified(holding_data, weights) / 100
        
        # Information ratio = (Portfolio Return - Benchmark Return) / Tracking Error
        information_ratio = (portfolio_xirr - nifty_xirr) / tracking_error if tracking_error > 0 else 0
        return round(information_ratio, 3)
        
    except Exception as e:
        logger.error(f"Error calculating Information ratio: {str(e)}")
        return 0.0

def calculate_max_drawdown_simplified(holding_data, weights, client_id):
    """Calculate maximum drawdown (simplified)"""
    # This would need historical portfolio values
    # For now, estimate based on portfolio volatility
    portfolio_volatility = calculate_portfolio_volatility_simplified(holding_data, weights)
    # Rough estimate: max drawdown is typically 1.5-2x the volatility
    max_drawdown = -(portfolio_volatility * 1.8)
    return round(max_drawdown, 2)

def calculate_var_95_simplified(holding_data, weights):
    """Calculate 95% Value at Risk"""
    portfolio_volatility = calculate_portfolio_volatility_simplified(holding_data, weights)
    # VaR 95% = -1.645 * volatility (assuming normal distribution)
    var_95 = -(1.645 * portfolio_volatility)
    return round(var_95, 2)

def calculate_calmar_ratio_simplified(holding_data, weights, client_id):
    """Calculate Calmar ratio"""
    try:
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        if not cashflows:
            return 0.0
            
        current_value = sum(h['value'] for h in holding_data)
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
        
        max_drawdown = abs(calculate_max_drawdown_simplified(holding_data, weights, client_id))
        
        # Calmar ratio = Annual Return / Max Drawdown
        calmar_ratio = (portfolio_xirr * 100) / max_drawdown if max_drawdown > 0 else 0
        return round(calmar_ratio, 3)
        
    except Exception as e:
        logger.error(f"Error calculating Calmar ratio: {str(e)}")
        return 0.0

def calculate_sortino_ratio_simplified(holding_data, weights, client_id):
    """Calculate Sortino ratio"""
    try:
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        if not cashflows:
            return 0.0
            
        current_value = sum(h['value'] for h in holding_data)
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
        
        risk_free_rate = 0.065
        
        # Downside deviation (simplified as half of total volatility)
        portfolio_volatility = calculate_portfolio_volatility_simplified(holding_data, weights) / 100
        downside_deviation = portfolio_volatility * 0.6  # Rough estimate
        
        # Sortino ratio = (Portfolio Return - Risk-free rate) / Downside Deviation
        sortino_ratio = (portfolio_xirr - risk_free_rate) / downside_deviation if downside_deviation > 0 else 0
        return round(sortino_ratio, 3)
        
    except Exception as e:
        logger.error(f"Error calculating Sortino ratio: {str(e)}")
        return 0.0

def calculate_diversification_metrics(holdings):
    """Calculate diversification effectiveness metrics"""
    from collections import defaultdict
    import math
    
    if not holdings:
        return {
            'herfindahl_index': 0,
            'effective_number_of_stocks': 0,
            'sector_concentration': 0,
            'top_5_concentration': 0,
            'top_5_stocks': [],
            'top_10_concentration': 0,
            'diversification_ratio': 0,
            'concentration_risk': 'Low',
            'number_of_sectors': 0,
            'number_of_stocks': 0,
            'sector_breakdown': {},
            'non_diversifying_stocks': {
                'overweight_stocks': [],
                'sector_overweight': [],
                'small_holdings': [],
                'recommendations': ['No holdings data available']
            },
            'data_quality': {
                'has_sector_data': False,
                'missing_sector_count': 0,
                'total_holdings': 0
            }
        }
    
    # Calculate total value and weights
    total_value = 0
    holding_data = []
    missing_sector_count = 0
    has_sector_data = False
    
    for holding in holdings:
        if holding.security and holding.security.current_price:
            quantity = float(holding.quantity) if holding.quantity else 0
            current_price = float(holding.security.current_price) if holding.security.current_price else 0
            current_value = quantity * current_price
            total_value += current_value
            
            # Get sector information from meta_data JSON field (same as server-side)
            sector = 'Unknown'
            if holding.security.meta_data:
                try:
                    import json
                    meta_data = json.loads(holding.security.meta_data)
                    sector = meta_data.get('sector', meta_data.get('industry', 'Unknown'))
                except (json.JSONDecodeError, AttributeError):
                    sector = 'Unknown'
            
            # Fallback to direct fields if meta_data is not available
            if sector == 'Unknown':
                if hasattr(holding.security, 'sector') and holding.security.sector:
                    sector = holding.security.sector
                elif hasattr(holding.security, 'industry') and holding.security.industry:
                    sector = holding.security.industry
                else:
                    missing_sector_count += 1
            else:
                has_sector_data = True
            
            holding_data.append({
                'security': holding.security,
                'value': current_value,
                'sector': sector
            })
    
    if total_value == 0:
        return {
            'herfindahl_index': 0,
            'effective_number_of_stocks': 0,
            'sector_concentration': 0,
            'top_5_concentration': 0,
            'top_10_concentration': 0,
            'diversification_ratio': 0,
            'concentration_risk': 'Low'
        }
    
    # Calculate weights
    weights = [h['value'] / total_value for h in holding_data]
    
    # 1. Herfindahl Index (concentration measure)
    herfindahl_index = sum(w**2 for w in weights)
    
    # 2. Effective Number of Stocks
    effective_number_of_stocks = 1 / herfindahl_index if herfindahl_index > 0 else 0
    
    # 3. Sector Concentration
    sector_weights = defaultdict(float)
    for i, holding in enumerate(holding_data):
        sector_weights[holding['sector']] += weights[i]
    
    sector_concentration = max(sector_weights.values()) if sector_weights else 0
    
    # 4. Top 5 and Top 10 Concentration with stock names
    # Create list of (weight, stock_name) tuples and sort by weight
    weight_stock_pairs = [(weights[i], holding_data[i]['security'].symbol) for i in range(len(weights))]
    weight_stock_pairs.sort(key=lambda x: x[0], reverse=True)
    
    sorted_weights = [pair[0] for pair in weight_stock_pairs]
    top_5_concentration = sum(sorted_weights[:5])
    top_10_concentration = sum(sorted_weights[:10])
    
    # Get top 5 stock names
    top_5_stocks = [pair[1] for pair in weight_stock_pairs[:5]]
    
    # 5. Diversification Ratio (simplified)
    # This would ideally use correlation matrix, but we'll use sector diversity
    num_sectors = len(sector_weights)
    diversification_ratio = num_sectors / len(holding_data) if holding_data else 0
    
    # 6. Concentration Risk Assessment
    if herfindahl_index > 0.25:
        concentration_risk = 'High'
    elif herfindahl_index > 0.15:
        concentration_risk = 'Medium'
    else:
        concentration_risk = 'Low'
    
    return {
        'herfindahl_index': round(herfindahl_index, 4),
        'effective_number_of_stocks': round(effective_number_of_stocks, 2),
        'sector_concentration': round(sector_concentration * 100, 2),  # As percentage
        'top_5_concentration': round(top_5_concentration * 100, 2),  # As percentage
        'top_5_stocks': top_5_stocks,  # Stock names
        'top_10_concentration': round(top_10_concentration * 100, 2),  # As percentage
        'diversification_ratio': round(diversification_ratio, 3),
        'concentration_risk': concentration_risk,
        'number_of_sectors': num_sectors,
        'number_of_stocks': len(holding_data),
        'sector_breakdown': dict(sector_weights),  # Add sector breakdown
        'non_diversifying_stocks': identify_non_diversifying_stocks(holding_data, weights),  # Add non-diversifying analysis
        'data_quality': {
            'has_sector_data': has_sector_data,
            'missing_sector_count': missing_sector_count,
            'total_holdings': len(holding_data),
            'sector_data_percentage': round((len(holding_data) - missing_sector_count) / len(holding_data) * 100, 1) if holding_data else 0
        }
    }

def identify_non_diversifying_stocks(holding_data, weights):
    """Identify stocks that don't add diversification value"""
    from collections import defaultdict
    
    if len(holding_data) < 3:  # Need at least 3 stocks for meaningful analysis
        return {
            'overweight_stocks': [],
            'sector_overweight': [],
            'small_holdings': [],
            'recommendations': []
        }
    
    # 1. Identify overweight stocks (>5% of portfolio)
    overweight_stocks = []
    for i, holding in enumerate(holding_data):
        if weights[i] > 0.05:  # More than 5%
            overweight_stocks.append({
                'symbol': holding['security'].symbol,
                'weight': round(weights[i] * 100, 1),
                'sector': holding['sector']
            })
    
    # 2. Identify sector overweight (>30% in one sector)
    sector_weights = defaultdict(float)
    for i, holding in enumerate(holding_data):
        sector_weights[holding['sector']] += weights[i]
    
    sector_overweight = []
    for sector, weight in sector_weights.items():
        if weight > 0.30:  # More than 30% in one sector
            sector_overweight.append({
                'sector': sector,
                'weight': round(weight * 100, 1)
            })
    
    # 3. Identify small holdings (<1% of portfolio)
    small_holdings = []
    for i, holding in enumerate(holding_data):
        if weights[i] < 0.01:  # Less than 1%
            small_holdings.append({
                'symbol': holding['security'].symbol,
                'weight': round(weights[i] * 100, 1),
                'sector': holding['sector']
            })
    
    # 4. Generate recommendations
    recommendations = []
    if len(overweight_stocks) > 0:
        recommendations.append("Consider reducing overweight positions for better diversification")
    if len(sector_overweight) > 0:
        recommendations.append("Reduce sector concentration to lower risk")
    if len(small_holdings) > 3:
        recommendations.append("Consider consolidating small positions to reduce complexity")
    if len(holding_data) < 10:
        recommendations.append("Add more stocks to improve diversification")
    
    return {
        'overweight_stocks': overweight_stocks,
        'sector_overweight': sector_overweight,
        'small_holdings': small_holdings,
        'recommendations': recommendations
    }

def calculate_concentration_analysis(holdings):
    """Calculate concentration analysis"""
    # Calculate total_value from holdings (quantity * current_price)
    total_value = 0
    for holding in holdings:
        if holding.security and holding.security.current_price:
            quantity = float(holding.quantity) if holding.quantity else 0
            current_price = float(holding.security.current_price) if holding.security.current_price else 0
            total_value += quantity * current_price
    
    if total_value == 0:
        return {
            'top_5_concentration': 0,
            'top_10_concentration': 0,
            'herfindahl_index': 0
        }
    
    # Calculate individual holding values for sorting and analysis
    holding_values = []
    for holding in holdings:
        if holding.security and holding.security.current_price:
            quantity = float(holding.quantity) if holding.quantity else 0
            current_price = float(holding.security.current_price) if holding.security.current_price else 0
            holding_value = quantity * current_price
            holding_values.append((holding, holding_value))
    
    # Sort holdings by value
    sorted_holdings = sorted(holding_values, key=lambda x: x[1], reverse=True)
    
    # Calculate top 5 and top 10 concentration
    top_5_value = sum(value for _, value in sorted_holdings[:5])
    top_10_value = sum(value for _, value in sorted_holdings[:10])
    
    top_5_concentration = (top_5_value / total_value * 100) if total_value > 0 else 0
    top_10_concentration = (top_10_value / total_value * 100) if total_value > 0 else 0
    
    # Calculate Herfindahl index
    herfindahl_index = sum((value / total_value) ** 2 for _, value in holding_values) if total_value > 0 else 0
    
    return {
        'top_5_concentration': top_5_concentration,
        'top_10_concentration': top_10_concentration,
        'herfindahl_index': herfindahl_index
    }

def calculate_volatility_analysis(holdings):
    """Calculate volatility analysis"""
    # Simplified volatility analysis
    # In a real system, you'd calculate actual volatility from historical data
    
    return {
        'portfolio_volatility': 15.0,
        'individual_volatilities': [
            {
                'security_id': h.security_id,
                'volatility': 20.0  # Placeholder
            }
            for h in holdings
        ]
    }

# ============================================================================
# PERIOD-BASED ANALYTICS (NEW)
# ============================================================================

def calculate_period_trade_analytics(client_id, holdings, start_date=None, end_date=None):
    """
    Calculate trade analytics for a specific period
    
    Returns:
        - best_performers_in_period: Stocks with highest gains in period
        - worst_performers_in_period: Stocks with highest losses in period
        - stocks_added_in_period: New positions added
        - stocks_sold_in_period: Positions closed with realized P&L
        - weight_changes_in_period: Significant weight changes
    """
    from api.v1.historical_prices import HistoricalPriceService
    
    logger.info(f"Calculating period trade analytics for client {client_id}: {start_date} to {end_date}")
    
    # Get all transactions in the period
    query = Transaction.query.filter_by(client_id=client_id)
    if start_date:
        query = query.filter(Transaction.transaction_date >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.filter(Transaction.transaction_date <= datetime.combine(end_date, datetime.max.time()))
    
    period_transactions = query.order_by(Transaction.transaction_date).all()
    
    logger.info(f"Found {len(period_transactions)} transactions in period")
    
    # ========== BEST/WORST PERFORMERS IN PERIOD ==========
    performers = []
    for holding in holdings:
        if not holding.security or not holding.security.current_price:
            continue
        
        quantity = float(holding.quantity)
        current_price = float(holding.security.current_price)
        avg_price = float(getattr(holding, "average_price", 0) or 0)
        
        current_value = quantity * current_price
        cost = quantity * avg_price
        absolute_gain = current_value - cost
        gain_percent = (absolute_gain / cost * 100) if cost > 0 else 0
        
        # Get purchase date (first BUY transaction for this security)
        first_buy = Transaction.query.filter_by(
            client_id=client_id,
            security_id=holding.security_id,
            type='BUY'
        ).order_by(Transaction.transaction_date).first()
        
        purchase_date = first_buy.transaction_date if first_buy else None
        days_held = (date.today() - purchase_date.date()).days if purchase_date else 0
        
        # Get sector information
        sector = 'Unknown'
        if hasattr(holding.security, 'sector') and holding.security.sector:
            sector = holding.security.sector
        elif hasattr(holding.security, 'industry') and holding.security.industry:
            sector = holding.security.industry
        
        performers.append({
            'symbol': holding.security.symbol,
            'name': holding.security.name,
            'sector': sector,
            'quantity': quantity,
            'buy_price_avg': avg_price,
            'current_price': current_price,
            'absolute_gain': absolute_gain,
            'gain_percent': gain_percent,
            'purchase_date': purchase_date.date() if purchase_date else None,
            'days_held': days_held,
            'current_value': current_value
        })
    
    # Sort by gain percent
    performers_sorted = sorted(performers, key=lambda x: x['gain_percent'], reverse=True)
    best_performers = performers_sorted[:5]
    worst_performers = performers_sorted[-5:] if len(performers_sorted) > 5 else []
    
    # ========== STOCKS ADDED IN PERIOD ==========
    stocks_added = []
    added_securities = set()
    
    for txn in period_transactions:
        if txn.type == 'BUY' and txn.security_id not in added_securities:
            # Check if this is the FIRST buy for this security
            previous_buy = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == txn.security_id,
                Transaction.type == 'BUY',
                Transaction.transaction_date < datetime.combine((start_date if start_date else txn.transaction_date.date()), datetime.min.time())
            ).first()
            
            if not previous_buy:  # This is a new position
                added_securities.add(txn.security_id)
                
                # Get current holding for this security
                current_holding = next((h for h in holdings if h.security_id == txn.security_id), None)
                current_price = float(txn.security.current_price) if txn.security and txn.security.current_price else float(txn.price)
                buy_price = float(txn.price)
                quantity = float(txn.quantity)
                amount_invested = quantity * buy_price
                current_value = quantity * current_price if current_holding else amount_invested
                performance_percent = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0
                
                stocks_added.append({
                    'symbol': txn.security.symbol if txn.security else 'Unknown',
                    'name': txn.security.name if txn.security else 'Unknown',
                    'add_date': txn.transaction_date.date(),
                    'quantity': quantity,
                    'buy_price': buy_price,
                    'amount_invested': amount_invested,
                    'current_price': current_price,
                    'current_performance_percent': performance_percent
                })
    
    # ========== STOCKS SOLD IN PERIOD (with Realized P&L) ==========
    stocks_sold = []
    
    sell_transactions = [t for t in period_transactions if t.type == 'SELL']
    
    for sell_txn in sell_transactions:
        # Find matching BUY transactions (FIFO basis)
        buy_txns = Transaction.query.filter_by(
            client_id=client_id,
            security_id=sell_txn.security_id,
            type='BUY'
        ).filter(Transaction.transaction_date <= sell_txn.transaction_date).order_by(Transaction.transaction_date).all()
        
        if buy_txns:
            # Calculate average buy price from all previous buys
            total_buy_cost = sum(float(b.quantity) * float(b.price) for b in buy_txns)
            total_buy_qty = sum(float(b.quantity) for b in buy_txns)
            avg_buy_price = total_buy_cost / total_buy_qty if total_buy_qty > 0 else 0
        else:
            avg_buy_price = 0
        
        sell_price = float(sell_txn.price)
        quantity_sold = float(sell_txn.quantity)
        realized_gain = (sell_price - avg_buy_price) * quantity_sold
        realized_gain_percent = ((sell_price - avg_buy_price) / avg_buy_price * 100) if avg_buy_price > 0 else 0
        
        stocks_sold.append({
            'symbol': sell_txn.security.symbol if sell_txn.security else 'Unknown',
            'name': sell_txn.security.name if sell_txn.security else 'Unknown',
            'sell_date': sell_txn.transaction_date.date(),
            'quantity_sold': quantity_sold,
            'avg_buy_price': avg_buy_price,
            'sell_price': sell_price,
            'realized_gain': realized_gain,
            'realized_gain_percent': realized_gain_percent
        })
    
    # ========== WEIGHT CHANGES IN PERIOD ==========
    # This requires historical holdings data or we reconstruct from transactions
    weight_changes = []
    
    # Calculate current portfolio value
    current_portfolio_value = sum(
        float(h.quantity) * float(h.security.current_price) 
        for h in holdings 
        if h.security and h.security.current_price
    )
    
    # For weight changes, we need to compare weights at period start vs now
    # Simplified: Show securities with significant buys/sells in the period
    for holding in holdings:
        if not holding.security:
            continue
        
        # Check if there were transactions for this security in the period
        security_txns = [t for t in period_transactions if t.security_id == holding.security_id]
        
        if security_txns:
            buy_amount = sum(float(t.quantity) * float(t.price) for t in security_txns if t.type == 'BUY')
            sell_amount = sum(float(t.quantity) * float(t.price) for t in security_txns if t.type == 'SELL')
            net_change_amount = buy_amount - sell_amount
            
            current_value = float(holding.quantity) * float(holding.security.current_price) if holding.security.current_price else 0
            current_weight = (current_value / current_portfolio_value * 100) if current_portfolio_value > 0 else 0
            
            # Estimate previous weight (current weight minus impact of period transactions)
            previous_value_estimate = current_value - net_change_amount
            previous_portfolio_value_estimate = current_portfolio_value - sum(
                float(t.quantity) * float(t.price) for t in period_transactions if t.type == 'BUY'
            ) + sum(
                float(t.quantity) * float(t.price) for t in period_transactions if t.type == 'SELL'
            )
            
            previous_weight = (previous_value_estimate / previous_portfolio_value_estimate * 100) if previous_portfolio_value_estimate > 0 else 0
            weight_change = current_weight - previous_weight
            
            # Only include significant changes (>1%)
            if abs(weight_change) > 1.0:
                change_type = "increased" if weight_change > 0 else "decreased"
                change_reason = "additional_purchase" if buy_amount > 0 else "partial_sale" if sell_amount > 0 else "price_movement"
                
                weight_changes.append({
                    'symbol': holding.security.symbol,
                    'name': holding.security.name,
                    'previous_weight_percent': previous_weight,
                    'current_weight_percent': current_weight,
                    'change_percent': weight_change,
                    'change_type': change_type,
                    'change_reason': change_reason,
                    'amount_added': buy_amount,
                    'amount_sold': sell_amount
                })
    
    # Sort by absolute weight change
    weight_changes = sorted(weight_changes, key=lambda x: abs(x['change_percent']), reverse=True)
    
    return {
        'period_start': start_date,
        'period_end': end_date,
        'best_performers_in_period': best_performers,
        'worst_performers_in_period': worst_performers,
        'stocks_added_in_period': stocks_added,
        'stocks_sold_in_period': stocks_sold,
        'weight_changes_in_period': weight_changes[:10],  # Top 10 weight changes
        'total_transactions_in_period': len(period_transactions),
        'total_stocks_added': len(stocks_added),
        'total_stocks_sold': len(stocks_sold),
        'total_realized_pnl': sum(s['realized_gain'] for s in stocks_sold)
    }

def calculate_sector_performance_in_period(client_id, holdings, start_date=None, end_date=None):
    """
    Calculate sector-wise performance for a specific period
    """
    logger.info(f"Calculating sector performance for client {client_id}: {start_date} to {end_date}")
    
    # Group holdings by sector
    sectors = {}
    for holding in holdings:
        if not holding.security or not holding.security.current_price:
            continue
        
        # Get sector information (prefer meta_data JSON → fallback to direct fields)
        sector = 'Unknown'
        try:
            meta_data = getattr(holding.security, 'meta_data', None)
            if meta_data:
                import json as _json
                parsed = _json.loads(meta_data) if isinstance(meta_data, str) else meta_data
                sector = parsed.get('sector') or parsed.get('industry') or 'Unknown'
        except Exception:
            # Ignore meta_data parsing errors and rely on fallbacks below
            pass

        if sector == 'Unknown':
            if hasattr(holding.security, 'sector') and holding.security.sector:
                sector = holding.security.sector
            elif hasattr(holding.security, 'industry') and holding.security.industry:
                sector = holding.security.industry
        
        quantity = float(holding.quantity)
        current_price = float(holding.security.current_price)
        avg_price = float(getattr(holding, "average_price", 0) or 0)
        
        current_value = quantity * current_price
        cost = quantity * avg_price
        unrealized_pnl = current_value - cost
        pnl_percent = (unrealized_pnl / cost * 100) if cost > 0 else 0
        
        if sector not in sectors:
            sectors[sector] = {
                'sector': sector,
                'current_value': 0,
                'cost_basis': 0,
                'unrealized_pnl': 0,
                'stocks_count': 0,
                'stocks': []
            }
        
        sectors[sector]['current_value'] += current_value
        sectors[sector]['cost_basis'] += cost
        sectors[sector]['unrealized_pnl'] += unrealized_pnl
        sectors[sector]['stocks_count'] += 1
        sectors[sector]['stocks'].append({
            'symbol': holding.security.symbol,
            'value': current_value,
            'pnl_percent': pnl_percent
        })
    
    # Calculate total portfolio value
    total_value = sum(s['current_value'] for s in sectors.values())
    
    # Calculate sector metrics
    sector_wise_performance = []
    for sector_data in sectors.values():
        pnl_percent = (sector_data['unrealized_pnl'] / sector_data['cost_basis'] * 100) if sector_data['cost_basis'] > 0 else 0
        weight_percent = (sector_data['current_value'] / total_value * 100) if total_value > 0 else 0
        contribution = (sector_data['unrealized_pnl'] / total_value * 100) if total_value > 0 else 0
        
        sector_wise_performance.append({
            'sector': sector_data['sector'],
            'current_value': sector_data['current_value'],
            'cost_basis': sector_data['cost_basis'],
            'unrealized_pnl': sector_data['unrealized_pnl'],
            'pnl_percent': pnl_percent,
            'weight_percent': weight_percent,
            'contribution_to_total_return': contribution,
            'stocks_count': sector_data['stocks_count']
        })
    
    # Sort by performance
    sector_wise_performance.sort(key=lambda x: x['pnl_percent'], reverse=True)
    
    best_sector = sector_wise_performance[0] if sector_wise_performance else None
    worst_sector = sector_wise_performance[-1] if sector_wise_performance else None
    
    return {
        'best_sector': {
            'name': best_sector['sector'],
            'absolute_return': best_sector['unrealized_pnl'],
            'return_percent': best_sector['pnl_percent'],
            'contribution_to_portfolio_return': best_sector['contribution_to_total_return'],
            'current_weight': best_sector['weight_percent']
        } if best_sector else None,
        'worst_sector': {
            'name': worst_sector['sector'],
            'absolute_return': worst_sector['unrealized_pnl'],
            'return_percent': worst_sector['pnl_percent'],
            'contribution_to_portfolio_return': worst_sector['contribution_to_total_return'],
            'current_weight': worst_sector['weight_percent']
        } if worst_sector else None,
        'sector_wise_performance': sector_wise_performance
    }


# ============================================================================
# EQUITY MODEL VS NIFTY PERFORMANCE TRACKING ENDPOINTS
# ============================================================================

@performance_bp.route('/equity-models/<int:model_id>/performance/vs-nifty', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_equity_model_vs_nifty_performance(model_id):
    """
    Get equity model vs Nifty performance comparison
    
    Query Parameters:
        - period: Optional period for comparison ('1M', '3M', '6M', '1Y', 'YTD', 'ALL')
        - start_date: Optional start date (YYYY-MM-DD)
        - end_date: Optional end date (YYYY-MM-DD)
        - base_value: Base portfolio value for calculation (default: 1000000)
    """
    try:
        from services.equity_model_vs_nifty_service import EquityModelVsNiftyService
        from models import SecurityAllocationModel
        
        # Verify model exists
        model = SecurityAllocationModel.query.get_or_404(model_id)
        
        # Get query parameters
        period = request.args.get('period')
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        investment_date_str = request.args.get('investment_date')
        base_value = float(request.args.get('base_value', 1000000.0))
        # When True (default when no investment_date), use period start for investment - ensures
        # model start value = base_value for fair apple-to-apple comparison
        use_period_start = request.args.get('use_period_start', 'true').lower() == 'true'
        
        # Parse investment_date if provided
        investment_date = None
        if investment_date_str:
            try:
                investment_date = datetime.strptime(investment_date_str, '%Y-%m-%d').date()
            except ValueError:
                return APIResponse.error(
                    message="Invalid investment_date format. Use YYYY-MM-DD",
                    status_code=400
                )
        
        if period:
            # Use period-based calculation
            end_date = date.today()
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
                if model.created_at:
                    if isinstance(model.created_at, datetime):
                        start_date = model.created_at.date()
                    else:
                        start_date = model.created_at
                else:
                    start_date = end_date - timedelta(days=365)
            else:
                return APIResponse.error(
                    message=f"Invalid period: {period}. Use: 1M, 3M, 6M, 1Y, YTD, ALL",
                    status_code=400
                )
            
            # When use_period_start: invest at period start for fair comparison (model start = base_value)
            effective_investment_date = start_date if use_period_start else investment_date
            
            performance = EquityModelVsNiftyService.calculate_model_vs_nifty_performance(
                model_id, start_date, end_date, base_value, effective_investment_date
            )
            
            # If performance calculation succeeded, get both start and end portfolio holdings
            if performance.get('success') and start_date != end_date:
                start_portfolio = EquityModelVsNiftyService.get_model_portfolio_value(
                    model_id, start_date, base_value, effective_investment_date
                )
                end_portfolio = EquityModelVsNiftyService.get_model_portfolio_value(
                    model_id, end_date, base_value, effective_investment_date
                )
                if start_portfolio.get('success') and end_portfolio.get('success'):
                    start_prices = {h['security_id']: h.get('current_price') for h in start_portfolio.get('holdings', [])}
                    end_holdings = end_portfolio.get('holdings', [])
                    for h in end_holdings:
                        h['start_price'] = start_prices.get(h['security_id'], h.get('investment_price'))
                    performance['end_portfolio_holdings'] = end_holdings
                    performance['end_portfolio_total_value'] = end_portfolio.get('total_value', 0.0)
                    performance['end_portfolio_total_cost'] = end_portfolio.get('total_cost', 0.0)
            elif performance.get('success'):
                end_portfolio = EquityModelVsNiftyService.get_model_portfolio_value(
                    model_id, end_date, base_value, effective_investment_date
                )
                if end_portfolio.get('success'):
                    performance['end_portfolio_holdings'] = end_portfolio.get('holdings', [])
                    performance['end_portfolio_total_value'] = end_portfolio.get('total_value', 0.0)
                    performance['end_portfolio_total_cost'] = end_portfolio.get('total_cost', 0.0)
            
            return APIResponse.success(
                data=performance,
                message=f"Equity model vs Nifty performance for period {period}"
            )
        
        elif start_date_str and end_date_str:
            # Use date range
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                return APIResponse.error(
                    message="Invalid date format. Use YYYY-MM-DD",
                    status_code=400
                )
            
            # When use_period_start: invest at period start for fair comparison (model start = base_value)
            effective_investment_date = start_date if use_period_start else investment_date
            
            performance = EquityModelVsNiftyService.calculate_model_vs_nifty_performance(
                model_id, start_date, end_date, base_value, effective_investment_date
            )
            
            # If performance calculation succeeded, get both start and end portfolio holdings
            # Start portfolio has prices at start_date; end portfolio has prices at end_date
            if performance.get('success') and start_date != end_date:
                start_portfolio = EquityModelVsNiftyService.get_model_portfolio_value(
                    model_id, start_date, base_value, effective_investment_date
                )
                end_portfolio = EquityModelVsNiftyService.get_model_portfolio_value(
                    model_id, end_date, base_value, effective_investment_date
                )
                if start_portfolio.get('success') and end_portfolio.get('success'):
                    # Build start_prices_by_security for correct Start Price column
                    start_prices = {h['security_id']: h.get('current_price') for h in start_portfolio.get('holdings', [])}
                    end_holdings = end_portfolio.get('holdings', [])
                    for h in end_holdings:
                        h['start_price'] = start_prices.get(h['security_id'], h.get('investment_price'))
                    performance['end_portfolio_holdings'] = end_holdings
                    performance['end_portfolio_total_value'] = end_portfolio.get('total_value', 0.0)
                    performance['end_portfolio_total_cost'] = end_portfolio.get('total_cost', 0.0)
            elif performance.get('success'):
                end_portfolio = EquityModelVsNiftyService.get_model_portfolio_value(
                    model_id, end_date, base_value, effective_investment_date
                )
                if end_portfolio.get('success'):
                    performance['end_portfolio_holdings'] = end_portfolio.get('holdings', [])
                    performance['end_portfolio_total_value'] = end_portfolio.get('total_value', 0.0)
                    performance['end_portfolio_total_cost'] = end_portfolio.get('total_cost', 0.0)
            
            return APIResponse.success(
                data=performance,
                message=f"Equity model vs Nifty performance from {start_date_str} to {end_date_str}"
            )
        
        else:
            # Default: current snapshot with summary
            snapshot = EquityModelVsNiftyService.get_current_performance_snapshot(model_id, base_value, investment_date)
            
            return APIResponse.success(
                data=snapshot,
                message="Current equity model vs Nifty performance snapshot"
            )
        
    except Exception as e:
        logger.error(f"Error getting equity model vs Nifty performance for model {model_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error calculating performance: {str(e)}",
            status_code=500
        )


@performance_bp.route('/equity-models/<int:model_id>/performance/vs-nifty/historical', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_equity_model_vs_nifty_historical(model_id):
    """
    Get historical equity model vs Nifty performance tracking data
    
    Query Parameters:
        - start_date: Start date (YYYY-MM-DD, default: 1 year ago)
        - end_date: End date (YYYY-MM-DD, default: today)
        - frequency: Data frequency ('daily', 'weekly', 'monthly', default: 'daily')
        - base_value: Base portfolio value for calculation (default: 1000000)
    """
    try:
        from services.equity_model_vs_nifty_service import EquityModelVsNiftyService
        from models import SecurityAllocationModel
        
        # Verify model exists
        model = SecurityAllocationModel.query.get_or_404(model_id)
        
        # Get query parameters
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        investment_date_str = request.args.get('investment_date')
        frequency = request.args.get('frequency', 'daily')
        base_value = float(request.args.get('base_value', 1000000.0))
        
        # Parse investment_date if provided
        investment_date = None
        if investment_date_str:
            try:
                investment_date = datetime.strptime(investment_date_str, '%Y-%m-%d').date()
            except ValueError:
                return APIResponse.error(
                    message="Invalid investment_date format. Use YYYY-MM-DD",
                    status_code=400
                )
        
        # Parse dates
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            except ValueError:
                return APIResponse.error(
                    message="Invalid start_date format. Use YYYY-MM-DD",
                    status_code=400
                )
        else:
            start_date = date.today() - timedelta(days=365)  # Default: 1 year ago
        
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                return APIResponse.error(
                    message="Invalid end_date format. Use YYYY-MM-DD",
                    status_code=400
                )
        else:
            end_date = date.today()
        
        # Validate frequency
        if frequency not in ['daily', 'weekly', 'monthly']:
            return APIResponse.error(
                message="Invalid frequency. Use: daily, weekly, monthly",
                status_code=400
            )
        
        # Get historical data
        historical_data = EquityModelVsNiftyService.get_historical_performance_tracking(
            model_id, start_date, end_date, base_value, investment_date, frequency
        )
        
        return APIResponse.success(
            data={
                'model_id': model_id,
                'model_name': model.name,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'frequency': frequency,
                'base_value': base_value,
                'data_points': len(historical_data),
                'data': historical_data
            },
            message=f"Historical equity model vs Nifty performance ({frequency})"
        )
        
    except Exception as e:
        logger.error(f"Error getting historical equity model vs Nifty data for model {model_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error retrieving historical data: {str(e)}",
            status_code=500
        )


@performance_bp.route('/equity-models/<int:model_id>/performance/vs-nifty/summary', methods=['GET'])
# @api_auth_required
# @api_rate_limit
def get_equity_model_vs_nifty_summary(model_id):
    """
    Get equity model performance summary for multiple time periods
    
    Query Parameters:
        - periods: Comma-separated list of periods (default: '1M,3M,6M,1Y,YTD')
                  Options: 1M, 3M, 6M, 1Y, YTD, ALL
        - base_value: Base portfolio value for calculation (default: 1000000)
    """
    try:
        from services.equity_model_vs_nifty_service import EquityModelVsNiftyService
        from models import SecurityAllocationModel
        
        # Verify model exists
        model = SecurityAllocationModel.query.get_or_404(model_id)
        
        # Get query parameters
        periods_str = request.args.get('periods', '1M,3M,6M,1Y,YTD')
        periods = [p.strip() for p in periods_str.split(',')]
        investment_date_str = request.args.get('investment_date')
        base_value = float(request.args.get('base_value', 1000000.0))
        
        # Parse investment_date if provided
        investment_date = None
        if investment_date_str:
            try:
                investment_date = datetime.strptime(investment_date_str, '%Y-%m-%d').date()
            except ValueError:
                return APIResponse.error(
                    message="Invalid investment_date format. Use YYYY-MM-DD",
                    status_code=400
                )
        
        # Get summary
        summary = EquityModelVsNiftyService.get_performance_summary(model_id, base_value, investment_date, periods)
        
        return APIResponse.success(
            data=summary,
            message="Equity model vs Nifty performance summary"
        )
        
    except Exception as e:
        logger.error(f"Error getting equity model vs Nifty summary for model {model_id}: {str(e)}")
        return APIResponse.error(
            message=f"Error calculating summary: {str(e)}",
            status_code=500
        )
