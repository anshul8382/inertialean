"""
Period Enhanced Analytics Calculator Module

This module calculates enhanced analytics using validated data and core performance results.
"""
import logging
from datetime import datetime, timedelta
import json

# Import calculation functions from v1
from api.v1.period_analysis import (
    calculate_period_summary,
    calculate_period_benchmark_comparison,
    calculate_holdings_changes_in_period,
    calculate_period_cashflow_analysis,
    calculate_holdings_evolution_detailed,
    calculate_concentration_risk,
    calculate_enhanced_sector_analysis,
    calculate_industry_performance,
    calculate_transaction_quality_metrics,
    calculate_value_attribution,
    calculate_pathway_breakdown,
    calculate_stocks_most_bought,
    calculate_previous_period_most_bought_performance,
    calculate_stocks_sold_analysis,
    calculate_wealth_creators_destroyers,
    calculate_mtm_trades_profitability,
    compute_value_contribution,
    compute_profit_change_contribution,
    build_holdings_map,
    calculate_best_performers,
    calculate_worst_performers_from_mtm,
    calculate_segment_wise_xirr
)
from api.v1.performance import (
    calculate_risk_metrics,
    calculate_period_trade_analytics,
    calculate_sector_performance_in_period
)

logger = logging.getLogger(__name__)


class PeriodEnhancedAnalyticsCalculator:
    """
    Calculates enhanced analytics using validated data and core performance results
    
    Enhanced analytics include:
    - Risk metrics
    - Benchmark comparison
    - Summary metrics
    - Holdings analytics (changes, evolution, concentration)
    - Sector and industry performance
    - Transaction quality
    - Value attribution
    - Wealth creators/destroyers
    - Pathway breakdown
    - Stock activity (most bought, sold, previous period performance)
    """
    
    def __init__(self, data_collector):
        """
        Initialize calculator with validated data collector
        
        Args:
            data_collector: PeriodAnalysisDataCollector instance with validated data
        """
        if not data_collector.is_valid:
            raise ValueError("Data collector must be valid before calculating enhanced analytics")
        
        self.data = data_collector
        self.results = {}
    
    def calculate_all(self, performance_results, prev_period_start_date=None, warnings_list=None):
        """
        Calculate all enhanced analytics
        
        Args:
            performance_results: dict with core performance metrics from PeriodPerformanceCalculator
            prev_period_start_date: Optional previous period start date for tracking
            warnings_list: Optional list to collect warnings
        
        Returns:
            tuple: (dict with all enhanced analytics, list of warnings)
        """
        logger.info("Calculating enhanced analytics")
        
        # Risk metrics
        self.results['period_risk_metrics'] = self._calculate_risk_metrics()
        
        # Benchmark comparison
        self.results['benchmark_comparison'] = self._calculate_benchmark_comparison(
            performance_results.get('period_xirr', {}).get('xirr', 0)
        )
        
        # Summary metrics
        self.results['summary_metrics'] = self._calculate_summary_metrics(performance_results)
        
        # Holdings analytics
        self.results['holdings_changes'] = self._calculate_holdings_changes()
        self.results['holdings_evolution'] = self._calculate_holdings_evolution()
        self.results['portfolio_holdings_comparison'] = self._calculate_portfolio_holdings_comparison(warnings_list)
        self.results['concentration_risk'] = self._calculate_concentration_risk()
        
        # Cashflow analysis
        self.results['cashflow_analysis'] = self._calculate_cashflow_analysis()
        
        # Sector and industry analytics
        self.results['sector_performance'] = self._calculate_sector_performance()
        self.results['sector_analysis'] = self._calculate_enhanced_sector_analysis()
        self.results['industry_performance'] = self._calculate_industry_performance(
            performance_results.get('mtm_analysis', {})
        )
        
        # Performance rankings
        self.results['best_performers'] = self._calculate_best_performers()
        self.results['worst_performers'] = self._calculate_worst_performers(
            performance_results.get('mtm_analysis', {})
        )
        
        # Segment-wise XIRR
        self.results['segment_wise_xirr'] = self._calculate_segment_wise_xirr()
        
        # Transaction analytics
        self.results['trade_analytics'] = self._calculate_trade_analytics(
            performance_results.get('mtm_analysis', {})
        )
        self.results['transaction_quality'] = self._calculate_transaction_quality()
        self.results['stocks_most_bought'] = self._calculate_stocks_most_bought()
        self.results['stocks_sold'] = self._calculate_stocks_sold()
        
        # Previous period most bought performance (if prev_period_start_date provided)
        if prev_period_start_date:
            self.results['prev_period_most_bought_performance'] = self._calculate_previous_period_most_bought_performance(
                prev_period_start_date
            )
        else:
            self.results['prev_period_most_bought_performance'] = []
        
        # Value attribution analytics
        self.results['value_attribution'] = self._calculate_value_attribution()
        
        # Wealth creators/destroyers (depends on stocks_sold, which is already calculated above)
        self.results['best_worst'] = self._calculate_wealth_creators_destroyers()
        
        # MTM trades profitability
        self.results['mtm_trades_analysis'] = self._calculate_mtm_trades_profitability()
        
        # Pathway breakdown
        self.results['pathway'] = self._calculate_pathway_breakdown(
            performance_results.get('mtm_analysis', {}),
            performance_results.get('gains_breakdown', {}),
            self.results.get('value_attribution', {})
        )
        
        logger.info("Enhanced analytics calculations completed")
        return self.results, warnings_list or []
    
    def _calculate_risk_metrics(self):
        """Calculate risk metrics"""
        logger.info("Calculating risk metrics")
        try:
            return calculate_risk_metrics(self.data.current_holdings, self.data.client_id)
        except Exception as e:
            logger.error(f"Error calculating risk metrics: {str(e)}", exc_info=True)
            return {}
    
    def _calculate_benchmark_comparison(self, portfolio_xirr):
        """Calculate benchmark comparison"""
        logger.info("Calculating benchmark comparison")
        try:
            return calculate_period_benchmark_comparison(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date,
                portfolio_xirr
            )
        except Exception as e:
            logger.error(f"Error calculating benchmark comparison: {str(e)}", exc_info=True)
            return {}
    
    def _calculate_summary_metrics(self, performance_results):
        """Calculate summary metrics"""
        logger.info("Calculating summary metrics")
        try:
            return calculate_period_summary(
                performance_results.get('mtm_analysis', {}),
                performance_results.get('trade_analysis', {}),
                performance_results.get('investment_breakdown', {}),
                performance_results.get('gains_breakdown', {}),
                performance_results.get('period_xirr', {}),
                performance_results.get('twr', {})  # Include TWR data
            )
        except Exception as e:
            logger.error(f"Error calculating summary metrics: {str(e)}", exc_info=True)
            return {}
    
    def _calculate_holdings_changes(self):
        """Calculate holdings changes"""
        logger.info("Calculating holdings changes")
        try:
            return calculate_holdings_changes_in_period(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date,
                self.data.start_portfolio,
                self.data.end_portfolio
            )
        except Exception as e:
            logger.error(f"Error calculating holdings changes: {str(e)}", exc_info=True)
            return {}
    
    def _calculate_holdings_evolution(self):
        """Calculate detailed holdings evolution"""
        logger.info("Calculating holdings evolution")
        try:
            return calculate_holdings_evolution_detailed(
                self.data.start_map,
                self.data.end_map,
                self.data.txns_in_period
            )
        except Exception as e:
            logger.error(f"Error calculating holdings evolution: {str(e)}", exc_info=True)
            return {'summary': {'start_count': 0, 'end_count': 0}, 'new_positions': [], 'exited_positions': []}
    
    def _calculate_portfolio_holdings_comparison(self, warnings_list=None):
        """Build portfolio holdings comparison with maturity/exit handling"""
        logger.info("Building portfolio holdings comparison")
        warnings = warnings_list or []
        
        try:
            # PriceService provides corporate-action-adjusted historical prices (splits/bonus adjusted)
            from services.price_service import PriceService

            start_holdings_list = self.data.start_portfolio.get('holdings', [])
            end_holdings_list = self.data.end_portfolio.get('holdings', [])
            
            if not start_holdings_list and not end_holdings_list:
                return []
            
            start_holdings_dict = {h['security_id']: h for h in start_holdings_list}
            end_holdings_dict = {h['security_id']: h for h in end_holdings_list}
            
            # Get all unique security IDs from both periods
            all_security_ids = set(start_holdings_dict.keys()) | set(end_holdings_dict.keys())
            
            # Import required models
            from models import Security, Transaction, HistoricalPrice
            
            # Build comparison holdings array
            comparison_holdings = []
            start_datetime = datetime.combine(self.data.start_date, datetime.min.time())
            end_datetime = datetime.combine(self.data.end_date, datetime.max.time())
            total_period_days = (self.data.end_date - self.data.start_date).days + 1
            
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
                # Fallback: derive avg from total_cost/quantity when average_price missing
                if end_holding and end_avg_price <= 0 and end_qty > 0:
                    end_total_cost_val = float(end_holding.get('total_cost', 0) or 0)
                    if end_total_cost_val > 0:
                        end_avg_price = end_total_cost_val / end_qty
                end_current_price = float(end_holding.get('current_price', 0)) if end_holding else 0.0
                end_current_value = float(end_holding.get('current_value', 0)) if end_holding else 0.0
                end_total_cost = float(end_holding.get('total_cost', 0)) if end_holding else 0.0
                end_unrealized_pnl = end_current_value - end_total_cost
                end_unrealized_pnl_percent = (end_unrealized_pnl / end_total_cost * 100) if end_total_cost > 0 else 0.0
                
                # Calculate holding period and exit date for weighted average
                exit_date = None
                effective_end_date = self.data.end_date
                holding_days = total_period_days  # Default to full period
                exit_value = end_current_value  # Default to end value
                
                # If security exited during period (start_qty > 0, end_qty == 0), find exit date
                if start_qty > 0 and end_qty == 0:
                    # Check for sell transactions in the period
                    sell_transactions = Transaction.query.filter(
                        Transaction.client_id == self.data.client_id,
                        Transaction.security_id == security_id,
                        Transaction.type == 'SELL',
                        Transaction.transaction_date >= start_datetime,
                        Transaction.transaction_date <= end_datetime
                    ).order_by(Transaction.transaction_date.desc()).all()
                    
                    if sell_transactions:
                        # Use last sell date as exit date
                        exit_date = sell_transactions[0].transaction_date.date()
                        effective_end_date = exit_date
                        holding_days = (exit_date - self.data.start_date).days + 1
                        # Exit value should be the value at sell date (sum of all sell transactions)
                        exit_value = sum(float(t.quantity) * float(t.price) for t in sell_transactions)
                    else:
                        # Check if security has maturity date in metadata
                        security = Security.query.get(security_id)
                        if security and security.meta_data:
                            try:
                                meta = json.loads(security.meta_data) if isinstance(security.meta_data, str) else security.meta_data
                                maturity_date_str = meta.get('maturity_date') or meta.get('maturityDate')
                                if maturity_date_str:
                                    try:
                                        # Try different date formats
                                        for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%Y']:
                                            try:
                                                maturity_date = datetime.strptime(str(maturity_date_str), fmt).date()
                                                if self.data.start_date <= maturity_date <= self.data.end_date:
                                                    exit_date = maturity_date
                                                    effective_end_date = maturity_date
                                                    holding_days = (maturity_date - self.data.start_date).days + 1
                                                    
                                                    # For matured instruments, get the actual maturity value
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
                                                        exit_value = start_qty * maturity_price
                                                        logger.info(f"Security {security_id} ({symbol}) matured on {maturity_date}: using EXACT maturity date price ₹{maturity_price} (date: {price_date_used}) for {start_qty} shares = ₹{exit_value:,.2f}")
                                                    else:
                                                        # Fallback: try to get price just before maturity
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
                                                                warnings.append({
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
                                                                HistoricalPrice.date >= self.data.start_date
                                                            ).order_by(HistoricalPrice.date.desc()).first()
                                                            
                                                            if closest_price_record and closest_price_record.close_price:
                                                                maturity_price = float(closest_price_record.close_price)
                                                                price_date_used = closest_price_record.date
                                                                days_before = (maturity_date - price_date_used).days
                                                                price_source = f"closest_before_maturity_{days_before}_days"
                                                                exit_value = start_qty * maturity_price
                                                                logger.info(f"Security {security_id} ({symbol}) matured on {maturity_date}: using CLOSEST available price ₹{maturity_price} from {price_date_used} ({days_before} days before maturity) for {start_qty} shares = ₹{exit_value:,.2f}")
                                                                # Add warning for approximation (further from maturity)
                                                                warnings.append({
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
                                                                price_date_used = self.data.start_date
                                                                price_source = "start_value_fallback"
                                                                exit_value = start_current_value
                                                                logger.warning(f"Security {security_id} ({symbol}) matured on {maturity_date}: NO price data found in historical_price table (checked maturity date, 1-5 days before, and all dates before maturity). Using start_value ₹{exit_value:,.2f} as exit value (price: ₹{maturity_price}, date: {price_date_used})")
                                                                # Add warning for fallback
                                                                warnings.append({
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
                                                break
                                            except ValueError:
                                                continue
                                    except Exception as e:
                                        logger.debug(f"Error parsing maturity date for security {security_id}: {e}")
                            except Exception as e:
                                logger.debug(f"Error parsing meta_data for security {security_id}: {e}")
                
                # ------------------------------------------------------------------
                # Corporate-action-adjusted price return (key for “Security Return %”)
                #
                # Problem: start_current_price/end_current_price are UNADJUSTED database prices.
                # If a split/bonus happens between start_date and end_date, the raw price series can
                # show a large negative return even though the adjusted return is positive/flat.
                #
                # Fix: compute adjusted start/end prices in a common basis (today) using PriceService,
                # then compute adjusted price return for the holding window (start_date → effective_end_date).
                # ------------------------------------------------------------------
                effective_end_date_for_price = effective_end_date
                try:
                    start_price_adj_data = PriceService.get_price(
                        security_id,
                        self.data.start_date,
                        use_adjusted=True,
                        allow_fallback=True
                    )
                    end_price_adj_data = PriceService.get_price(
                        security_id,
                        effective_end_date_for_price,
                        use_adjusted=True,
                        allow_fallback=True
                    )
                    start_price_adjusted = float(start_price_adj_data.price) if start_price_adj_data and start_price_adj_data.is_valid and start_price_adj_data.price else 0.0
                    end_price_adjusted = float(end_price_adj_data.price) if end_price_adj_data and end_price_adj_data.is_valid and end_price_adj_data.price else 0.0
                except Exception:
                    # Never fail the whole comparison for a price issue
                    start_price_adjusted = 0.0
                    end_price_adjusted = 0.0

                price_return_percent_unadjusted = ((end_current_price - start_current_price) / start_current_price * 100) if start_current_price > 0 else 0.0
                price_return_percent_adjusted = ((end_price_adjusted - start_price_adjusted) / start_price_adjusted * 100) if start_price_adjusted > 0 else 0.0

                # Holder-relevant return: period-start market for existing qty, VWAP for period buys, blended if both
                # Buy VWAP must be CA-restated onto the same share basis as adjusted market prices.
                # When start_qty > 0, exclude same-day start buys (already in start holdings).
                from services.period_performer_return_service import (
                    compute_holder_period_price_return,
                    filter_period_buy_txns_for_holder_return,
                    vwap_from_transactions_ca_adjusted,
                )
                period_buy_txns = Transaction.query.filter(
                    Transaction.client_id == self.data.client_id,
                    Transaction.security_id == security_id,
                    Transaction.type == 'BUY',
                    Transaction.transaction_date >= start_datetime,
                    Transaction.transaction_date <= end_datetime,
                ).all()
                buy_txns_for_vwap = filter_period_buy_txns_for_holder_return(
                    period_buy_txns, self.data.start_date, start_qty
                )
                period_buy_qty, period_buy_vwap = vwap_from_transactions_ca_adjusted(
                    buy_txns_for_vwap, security_id, effective_end_date_for_price
                )
                period_start_market_price = start_price_adjusted
                return_basis = "period_start_market"
                holder_return = compute_holder_period_price_return(
                    period_start_market=start_price_adjusted,
                    period_end_market=end_price_adjusted,
                    start_qty=start_qty,
                    period_buy_qty=period_buy_qty,
                    period_buy_vwap=period_buy_vwap,
                    end_avg_price=end_avg_price,
                )
                if holder_return:
                    price_return_percent_adjusted = holder_return['price_percent_change']
                    start_price_adjusted = holder_return['start_price']
                    return_basis = holder_return.get('return_basis', return_basis)
                    if holder_return['return_basis'] != 'period_start_market':
                        logger.info(
                            f"Section 2D {symbol}: holder return {price_return_percent_adjusted:.2f}% "
                            f"(basis={holder_return['return_basis']}, effective_start=₹{start_price_adjusted:.2f})"
                        )
                    if holder_return['return_basis'] in ('period_purchase_vwap', 'holding_average_cost'):
                        price_return_percent_unadjusted = (
                            ((end_current_price - end_avg_price) / end_avg_price * 100)
                            if end_avg_price > 0
                            else price_return_percent_unadjusted
                        )

                # Calculate weighted average daily value
                # Weighted avg = ((start_value + exit_value) / 2) * (holding_days / total_period_days)
                weight_factor = holding_days / total_period_days if total_period_days > 0 else 1.0
                weighted_avg_value = ((start_current_value + exit_value) / 2) * weight_factor
                
                # For matured/exited securities, calculate change in unrealized P&L properly
                change_unrealized_pnl = end_unrealized_pnl - start_unrealized_pnl
                if exit_date and exit_value > 0:
                    # For exited/matured securities, calculate change as: exit_value - start_current_value
                    change_unrealized_pnl = exit_value - start_current_value
                    logger.debug(f"Security {security_id} ({symbol}) exited on {exit_date}: change_unrealized_pnl = exit_value ({exit_value:,.2f}) - start_current_value ({start_current_value:,.2f}) = {change_unrealized_pnl:,.2f}")
                
                comparison_holdings.append({
                    'security_id': security_id,
                    'symbol': symbol,
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
                        'exit_value': exit_value if exit_date else None
                    },
                    'change': {
                        'quantity': end_qty - start_qty,
                        'current_value': end_current_value - start_current_value,
                        'unrealized_pnl': change_unrealized_pnl,
                        'current_price': end_current_price - start_current_price,
                        'price_return_percent_unadjusted': price_return_percent_unadjusted,
                        'price_return_percent_adjusted': price_return_percent_adjusted
                    },
                    # Convenience fields for UI clients
                    'price_return_percent_unadjusted': price_return_percent_unadjusted,
                    'price_return_percent_adjusted': price_return_percent_adjusted,
                    'holding_days': holding_days,
                    'total_period_days': total_period_days,
                    'exit_date': exit_date.isoformat() if exit_date else None,
                    'exit_value': exit_value if exit_date else None,
                    'weighted_avg_value': weighted_avg_value,
                    'return_basis': return_basis,
                    'period_start_market_price': period_start_market_price,
                    'period_buy_vwap': period_buy_vwap if period_buy_qty > 0 else 0.0,
                })
            
            # Sort by symbol
            comparison_holdings.sort(key=lambda x: x['symbol'].upper())
            
            return comparison_holdings
        except Exception as e:
            logger.error(f"Error building holdings comparison: {str(e)}", exc_info=True)
            return []
    
    def _calculate_concentration_risk(self):
        """Calculate concentration risk"""
        logger.info("Calculating concentration risk")
        try:
            return calculate_concentration_risk(self.data.end_map)
        except Exception as e:
            logger.error(f"Error calculating concentration risk: {str(e)}", exc_info=True)
            raise RuntimeError("Concentration risk calculation failed") from e
    
    def _calculate_cashflow_analysis(self):
        """Calculate cashflow analysis"""
        logger.info("Calculating cashflow analysis")
        try:
            result = calculate_period_cashflow_analysis(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date
            )
            # Compatibility + correctness: the UI expects these keys.
            # Use data collected in V2 as the source of truth for counts.
            if isinstance(result, dict):
                result.setdefault('cashflow_count', len(self.data.period_cashflows or []))
                result.setdefault('total_cashflows_count', len(self.data.all_cashflows or []))
            return result
        except Exception as e:
            logger.error(f"Error calculating cashflow analysis: {str(e)}", exc_info=True)
            raise RuntimeError("Cashflow analysis calculation failed") from e
    
    def _calculate_sector_performance(self):
        """Calculate sector performance"""
        logger.info("Calculating sector performance")
        try:
            return calculate_sector_performance_in_period(
                self.data.client_id,
                self.data.current_holdings,
                self.data.start_date,
                self.data.end_date
            )
        except Exception as e:
            logger.error(f"Error calculating sector performance: {str(e)}", exc_info=True)
            raise RuntimeError("Sector performance calculation failed") from e
    
    def _calculate_enhanced_sector_analysis(self):
        """Calculate enhanced sector analysis"""
        logger.info("Calculating enhanced sector analysis")
        try:
            return calculate_enhanced_sector_analysis(
                self.data.start_map,
                self.data.end_map
            )
        except Exception as e:
            logger.error(f"Error calculating enhanced sector analysis: {str(e)}", exc_info=True)
            raise RuntimeError("Enhanced sector analysis calculation failed") from e
    
    def _calculate_industry_performance(self, mtm_analysis):
        """Calculate industry performance"""
        logger.info("Calculating industry performance")
        try:
            return calculate_industry_performance(
                mtm_analysis,
                self.data.end_map,
                self.data.client_id,
                self.data.start_date,
                self.data.end_date
            )
        except Exception as e:
            logger.error(f"Error calculating industry performance: {str(e)}", exc_info=True)
            raise RuntimeError("Industry performance calculation failed") from e
    
    def _calculate_trade_analytics(self, mtm_analysis):
        """Calculate trade analytics"""
        logger.info("Calculating trade analytics")
        try:
            # Get trade analytics from performance module
            trade_analytics_raw = calculate_period_trade_analytics(
                self.data.client_id,
                self.data.current_holdings,
                self.data.start_date,
                self.data.end_date
            )
            
            # Calculate additional metrics
            total_buy_value = sum(float(t.quantity) * float(t.price) for t in self.data.buy_txns)
            total_sell_value = sum(float(t.quantity) * float(t.price) for t in self.data.sell_txns)
            net_trade_value = total_buy_value - total_sell_value
            
            # Calculate win rate from sold stocks
            realized_pnl = trade_analytics_raw.get('total_realized_pnl', 0)
            stocks_sold_count = len(trade_analytics_raw.get('stocks_sold_in_period', []))
            winning_trades = len([s for s in trade_analytics_raw.get('stocks_sold_in_period', []) if s.get('realized_gain', 0) > 0])
            win_rate = (winning_trades / stocks_sold_count * 100) if stocks_sold_count > 0 else 0
            
            # Calculate turnover ratio
            start_portfolio_value = mtm_analysis.get('total_start_value', 0) or mtm_analysis.get('total_value_at_period_start', 0)
            turnover_ratio = (total_buy_value / start_portfolio_value) if start_portfolio_value > 0 else 0
            
            result = {
                'total_buy_value': total_buy_value,
                'total_sell_value': total_sell_value,
                'net_trade_value': net_trade_value,
                'buy_trades_count': len(self.data.buy_txns),
                'sell_trades_count': len(self.data.sell_txns),
                'total_trades_count': len(self.data.txns_in_period),
                # Compatibility: the UI reads total_transactions_in_period
                'total_transactions_in_period': len(self.data.txns_in_period),
                'win_rate_percent': win_rate,
                'avg_holding_period_days': 30,  # Simplified - could calculate from actual trades
                'turnover_ratio': turnover_ratio,
                'total_realized_pnl': realized_pnl,
                'best_performers_in_period': trade_analytics_raw.get('best_performers_in_period', []),
                'worst_performers_in_period': trade_analytics_raw.get('worst_performers_in_period', []),
                'stocks_added_in_period': trade_analytics_raw.get('stocks_added_in_period', []),
                'stocks_sold_in_period': trade_analytics_raw.get('stocks_sold_in_period', [])
            }
            return result
        except Exception as e:
            logger.error(f"Error calculating trade analytics: {str(e)}", exc_info=True)
            raise RuntimeError("Trade analytics calculation failed") from e
    
    def _calculate_transaction_quality(self):
        """Calculate transaction quality metrics"""
        logger.info("Calculating transaction quality")
        try:
            return calculate_transaction_quality_metrics(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date
            )
        except Exception as e:
            logger.error(f"Error calculating transaction quality: {str(e)}", exc_info=True)
            raise RuntimeError("Transaction quality calculation failed") from e
    
    def _calculate_stocks_most_bought(self):
        """Calculate most bought stocks"""
        logger.info("Calculating most bought stocks")
        try:
            return calculate_stocks_most_bought(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date,
                self.data.end_map
            )
        except Exception as e:
            logger.error(f"Error calculating most bought stocks: {str(e)}", exc_info=True)
            raise RuntimeError("Stocks most bought calculation failed") from e
    
    def _calculate_stocks_sold(self):
        """Calculate stocks sold analysis"""
        logger.info("Calculating stocks sold analysis")
        try:
            return calculate_stocks_sold_analysis(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date
            )
        except Exception as e:
            logger.error(f"Error calculating stocks sold analysis: {str(e)}", exc_info=True)
            raise RuntimeError("Stocks sold analysis calculation failed") from e
    
    def _calculate_previous_period_most_bought_performance(self, prev_period_start_date):
        """Calculate previous period most bought performance"""
        logger.info("Calculating previous period most bought performance")
        try:
            # prev_period_start_date is actually the previous period's START date
            # But we want to find stocks bought BETWEEN current period start and previous period start
            # So the "previous period" for calculation is: current_period_start to prev_period_start_date
            prev_period_end_date = prev_period_start_date
            prev_period_start_date_actual = self.data.start_date
            
            return calculate_previous_period_most_bought_performance(
                self.data.client_id,
                prev_period_start_date_actual,
                prev_period_end_date,
                self.data.start_date,
                self.data.end_date,
                self.data.end_map
            )
        except Exception as e:
            logger.error(f"Error calculating previous period most bought performance: {str(e)}", exc_info=True)
            return []
    
    def _calculate_value_attribution(self):
        """Calculate value attribution"""
        logger.info("Calculating value attribution")
        try:
            return calculate_value_attribution(
                self.data.start_map,
                self.data.end_map,
                self.data.txns_in_period,
                self.data.start_date,
                self.data.end_date
            )
        except Exception as e:
            logger.error(f"Error calculating value attribution: {str(e)}", exc_info=True)
            return {}
    
    def _calculate_wealth_creators_destroyers(self):
        """Calculate wealth creators and destroyers
        
        ✅ Uses compute_profit_change_contribution to match Portfolio Holdings Comparison logic EXACTLY.
        
        The calculation matches Portfolio Holdings Comparison's change.unrealized_pnl:
        - For held securities: gain = end_unrealized_pnl - start_unrealized_pnl
        - For fully exited securities: gain = exit_value - start_current_value
        
        This ensures Wealth Creators/Destroyers values match the delta PNL shown in Portfolio Holdings Comparison.
        """
        logger.info("Calculating wealth creators/destroyers")
        try:
            # Use stocks_sold from results (already calculated in calculate_all)
            stocks_sold = self.results.get('stocks_sold', [])
            
            # ✅ Use compute_profit_change_contribution to match Portfolio Holdings Comparison logic
            # This uses the same formula as portfolio_holdings_comparison: change_unrealized_pnl + realized_gain
            contrib_list = compute_profit_change_contribution(
                self.data.start_map,
                self.data.end_map,
                stocks_sold
            )
            
            return calculate_wealth_creators_destroyers(contrib_list)
        except Exception as e:
            logger.error(f"Error calculating wealth creators/destroyers: {str(e)}", exc_info=True)
            return {'wealth_creators': [], 'wealth_destroyers': [], 'total_wealth_created': 0, 'total_wealth_destroyed': 0}
    
    def _calculate_mtm_trades_profitability(self):
        """Calculate MTM trades profitability"""
        logger.info("Calculating MTM trades profitability")
        try:
            return calculate_mtm_trades_profitability(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date,
                self.data.end_map
            )
        except Exception as e:
            logger.error(f"Error calculating MTM trades profitability: {str(e)}", exc_info=True)
            return {
                'most_profitable_mtm_trades': [],
                'least_profitable_mtm_trades': [],
                'all_mtm_trades': [],
            }
    
    def _calculate_pathway_breakdown(self, mtm_analysis, gains_breakdown, value_attribution):
        """Calculate pathway breakdown"""
        logger.info("Calculating pathway breakdown")
        try:
            return calculate_pathway_breakdown(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date,
                mtm_analysis,
                gains_breakdown,
                value_attribution
            )
        except Exception as e:
            logger.error(f"Error calculating pathway breakdown: {str(e)}", exc_info=True)
            return None
    
    def _calculate_best_performers(self):
        """Calculate best performers"""
        logger.info("Calculating best performers")
        try:
            return calculate_best_performers(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date,
                self.data.current_holdings
            )
        except Exception as e:
            logger.error(f"Error calculating best performers: {str(e)}", exc_info=True)
            return {'best_performers': []}
    
    def _calculate_worst_performers(self, mtm_analysis):
        """Calculate worst performers"""
        logger.info("Calculating worst performers")
        try:
            return calculate_worst_performers_from_mtm(
                mtm_analysis,
                self.data.client_id,
                self.data.start_date,
                self.data.end_date
            )
        except Exception as e:
            logger.error(f"Error calculating worst performers: {str(e)}", exc_info=True)
            return {'worst_performers': []}
    
    def _calculate_segment_wise_xirr(self):
        """Calculate segment-wise XIRR"""
        logger.info("Calculating segment-wise XIRR")
        try:
            return calculate_segment_wise_xirr(
                self.data.client_id,
                self.data.start_date,
                self.data.end_date
            )
        except Exception as e:
            logger.error(f"Error calculating segment-wise XIRR: {str(e)}", exc_info=True)
            return {'segments': {}, 'summary': {'total_segments': 0, 'total_portfolio_value': 0.0, 'weighted_avg_xirr': 0.0}}

