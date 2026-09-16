"""
Financial Analytics Service
Handles calculation of financial metrics with configurable time periods and data validation
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Any
from models import db, Client, Holding, HistoricalPrice, BenchmarkData, Transaction, Cashflow
from services.benchmark_service import BenchmarkService
# Import calculation functions locally when needed to avoid circular imports
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class FinancialAnalyticsService:
    """Service for calculating financial metrics with custom time periods"""
    
    # Default time periods
    DEFAULT_PERIODS = {
        'beta': 365,  # 1 year in days
        'alpha': 365,
        'sharpe_ratio': 365,
        'treynor_ratio': 365,
        'sortino_ratio': 365,
        'information_ratio': 365,
        'calmar_ratio': 365,
        'portfolio_volatility': 365,
        'max_drawdown': 365,
        'var_95': 365,
        'xirr': None,  # Uses all available data
        'benchmark_comparison': 365,
        'upside_capture_ratio': 365,
        'downside_capture_ratio': 365,
        'capture_ratio': 365
    }
    
    # Minimum required days for each metric
    MIN_REQUIRED_DAYS = {
        'beta': 30,
        'alpha': 30,
        'sharpe_ratio': 30,
        'treynor_ratio': 30,
        'sortino_ratio': 30,
        'information_ratio': 30,
        'calmar_ratio': 90,  # Needs more data for max drawdown
        'portfolio_volatility': 30,
        'max_drawdown': 90,
        'var_95': 30,
        'upside_capture_ratio': 60,
        'downside_capture_ratio': 60,
        'capture_ratio': 60
    }
    
    # Minimum coverage percent required to rely on historical data
    MIN_COVERAGE_PERCENT = 80.0
    
    @staticmethod
    def get_client_first_trade_date(client_id: int) -> Optional[date]:
        """Get the first trade date for a client"""
        first_transaction = Transaction.query.filter_by(
            client_id=client_id
        ).filter(
            Transaction.transaction_date > date(2000, 1, 1)
        ).order_by(
            Transaction.transaction_date.asc()
        ).first()
        
        if first_transaction:
            trans_date = first_transaction.transaction_date
            return trans_date.date() if isinstance(trans_date, datetime) else trans_date
        return None
    
    @staticmethod
    def validate_data_for_metrics(
        client_id: int,
        metrics_config: Dict[str, Dict[str, Any]],
        strict_mode: bool = True
    ) -> Dict[str, Any]:
        """
        Validate data availability for all requested metrics
        
        Args:
            client_id: Client ID
            metrics_config: Dictionary of metrics to calculate with their configs
            strict_mode: If True, return errors for missing data. If False, return warnings.
        
        Returns:
            Validation result dictionary
        """
        try:
            client = Client.query.get_or_404(client_id)
            validation_results = {}
            all_missing_securities = set()
            all_missing_dates = []
            
            # Get client's holdings
            holdings = Holding.query.filter_by(client_id=client_id).all()
            if not holdings:
                return {
                    'is_valid': False,
                    'can_proceed': False,
                    'validation_results': {},
                    'summary': {
                        'error': 'No holdings found for client'
                    },
                    'error_message': 'Client has no holdings. Please add transactions first.'
                }
            
            # Get first trade date
            first_trade_date = FinancialAnalyticsService.get_client_first_trade_date(client_id)
            
            # Validate each metric
            for metric_type, config in metrics_config.items():
                # Calculate date range
                end_date = config.get('end_date')
                if end_date is None:
                    end_date = date.today()
                elif isinstance(end_date, str):
                    end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
                
                start_date = config.get('start_date')
                if start_date is None:
                    period_days = config.get('period_days') or FinancialAnalyticsService.DEFAULT_PERIODS.get(metric_type, 365)
                    start_date = end_date - timedelta(days=period_days)
                
                if isinstance(start_date, str):
                    start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                
                # Adjust start_date to first trade date if needed
                if first_trade_date and start_date < first_trade_date:
                    start_date = first_trade_date
                
                # Validate for this specific metric
                metric_validation = FinancialAnalyticsService._validate_metric_data(
                    client_id=client_id,
                    metric_type=metric_type,
                    start_date=start_date,
                    end_date=end_date,
                    holdings=holdings,
                    strict_mode=strict_mode
                )
                
                validation_results[metric_type] = metric_validation
                
                # Collect missing securities
                if metric_validation.get('missing_securities'):
                    for sec in metric_validation['missing_securities']:
                        if isinstance(sec, dict) and 'symbol' in sec:
                            all_missing_securities.add(sec['symbol'])
                        else:
                            all_missing_securities.add(str(sec))
                
                # Collect missing dates
                if metric_validation.get('missing_dates'):
                    all_missing_dates.extend(metric_validation['missing_dates'])
            
            # Calculate summary
            total_metrics = len(metrics_config)
            valid_metrics = sum(1 for v in validation_results.values() if v.get('is_valid', False))
            metrics_with_warnings = sum(1 for v in validation_results.values() if v.get('warnings'))
            metrics_with_errors = sum(1 for v in validation_results.values() if v.get('errors'))
            
            # Always allow proceeding - validation is informational only
            # System will use fallbacks to calculate with available data
            can_proceed = True
            
            return {
                'is_valid': metrics_with_errors == 0,
                'can_proceed': can_proceed,
                'validation_results': validation_results,
                'summary': {
                    'total_metrics': total_metrics,
                    'valid_metrics': valid_metrics,
                    'metrics_with_warnings': metrics_with_warnings,
                    'metrics_with_errors': metrics_with_errors,
                    'missing_securities_count': len(all_missing_securities),
                    'missing_securities': sorted(list(all_missing_securities)),
                    'missing_dates_count': len(set(all_missing_dates)),
                    'date_range': {
                        'earliest_missing': min(all_missing_dates).isoformat() if all_missing_dates else None,
                        'latest_missing': max(all_missing_dates).isoformat() if all_missing_dates else None
                    }
                },
                'recommendations': FinancialAnalyticsService._generate_data_recommendations(
                    validation_results, all_missing_securities
                )
            }
            
        except Exception as e:
            logger.error(f"Error validating data for client {client_id}: {str(e)}")
            return {
                'is_valid': False,
                'can_proceed': False,
                'error': str(e)
            }
    
    @staticmethod
    def _validate_metric_data(
        client_id: int,
        metric_type: str,
        start_date: date,
        end_date: date,
        holdings: List,
        strict_mode: bool = True
    ) -> Dict[str, Any]:
        """Validate data for a specific metric"""
        warnings = []
        errors = []
        missing_securities = []
        missing_dates = []
        data_coverage = {}
        
        # Get minimum required days
        min_required_days = FinancialAnalyticsService.MIN_REQUIRED_DAYS.get(metric_type, 30)
        actual_days = (end_date - start_date).days
        
        # Check period length
        if actual_days < min_required_days:
            msg = f"Period too short: {actual_days} days, need at least {min_required_days} days"
            if strict_mode:
                errors.append(msg)
            else:
                warnings.append(msg)
        
        # Check if metric needs historical prices
        needs_historical_data = metric_type in [
            'beta', 'alpha', 'sharpe_ratio', 'treynor_ratio', 'sortino_ratio',
            'information_ratio', 'calmar_ratio', 'portfolio_volatility',
            'max_drawdown', 'var_95', 'upside_capture_ratio', 'downside_capture_ratio',
            'capture_ratio'
        ]
        
        if needs_historical_data:
            # Validate historical price data
            validation_result = FinancialAnalyticsService._validate_historical_prices(
                holdings=holdings,
                start_date=start_date,
                end_date=end_date,
                min_required_days=min_required_days,
                strict_mode=strict_mode
            )
            
            missing_securities.extend(validation_result['missing_securities'])
            missing_dates.extend(validation_result['missing_dates'])
            data_coverage.update(validation_result['data_coverage'])
            
            if validation_result['errors']:
                errors.extend(validation_result['errors'])
            if validation_result['warnings']:
                warnings.extend(validation_result['warnings'])
            
            # Check Nifty data if needed
            if metric_type in ['beta', 'alpha', 'upside_capture_ratio', 'downside_capture_ratio', 'capture_ratio', 'benchmark_comparison']:
                nifty_validation = FinancialAnalyticsService._validate_nifty_data(
                    start_date=start_date,
                    end_date=end_date,
                    min_required_days=min_required_days,
                    strict_mode=strict_mode
                )
                
                if nifty_validation['errors']:
                    errors.extend(nifty_validation['errors'])
                if nifty_validation['warnings']:
                    warnings.extend(nifty_validation['warnings'])
                if nifty_validation['missing_dates']:
                    missing_dates.extend(nifty_validation['missing_dates'])
        
        # Check if metric needs cashflow data
        if metric_type in ['xirr', 'alpha']:
            cashflow_validation = FinancialAnalyticsService._validate_cashflow_data(
                client_id=client_id,
                start_date=start_date,
                end_date=end_date,
                strict_mode=strict_mode
            )
            
            if cashflow_validation['errors']:
                errors.extend(cashflow_validation['errors'])
            if cashflow_validation['warnings']:
                warnings.extend(cashflow_validation['warnings'])
        
        # Determine if valid
        is_valid = len(errors) == 0
        
        return {
            'is_valid': is_valid,
            'metric_type': metric_type,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'period_days': actual_days,
            'warnings': warnings,
            'errors': errors,
            'missing_securities': missing_securities,
            'missing_dates': sorted(list(set(missing_dates)))[:100],
            'data_coverage': data_coverage,
            'recommendations': FinancialAnalyticsService._generate_metric_recommendations(
                metric_type, warnings, errors, missing_securities, data_coverage
            )
        }
    
    @staticmethod
    def _validate_historical_prices(
        holdings: List,
        start_date: date,
        end_date: date,
        min_required_days: int,
        strict_mode: bool = True
    ) -> Dict[str, Any]:
        """Validate historical price data availability"""
        from models import Security
        
        missing_securities = []
        missing_dates = []
        data_coverage = {}
        errors = []
        warnings = []
        
        # Check each security
        for holding in holdings:
            if not holding.security_id or not holding.security:
                continue
            
            security = Security.query.get(holding.security_id)
            if not security:
                continue
            
            symbol = security.symbol if hasattr(security, 'symbol') else f"Security_{security.id}"

            # Determine when this security first appears for the client
            from models import Transaction  # local import to avoid circular dependency
            first_trade = (
                Transaction.query.filter_by(client_id=holding.client_id, security_id=holding.security_id)
                .order_by(Transaction.transaction_date.asc())
                .first()
            )
            security_start_date = start_date
            if first_trade:
                trade_date = first_trade.transaction_date
                trade_date = trade_date.date() if isinstance(trade_date, datetime) else trade_date
                if trade_date > security_start_date:
                    security_start_date = trade_date
            
            # Generate expected trading dates from the security's start date
            expected_dates = []
            current_date = security_start_date
            while current_date <= end_date:
                if current_date.weekday() < 5:
                    expected_dates.append(current_date)
                current_date += timedelta(days=1)
            total_expected_days = len(expected_dates)
            
            # Get historical prices
            historical_prices = HistoricalPrice.query.filter(
                HistoricalPrice.security_id == holding.security_id,
                HistoricalPrice.date >= security_start_date,
                HistoricalPrice.date <= end_date
            ).order_by(HistoricalPrice.date.asc()).all()
            
            available_dates = [hp.date for hp in historical_prices]
            available_days = len(available_dates)
            
            # Calculate coverage
            coverage_percent = (available_days / total_expected_days * 100) if total_expected_days > 0 else 0
            
            # Find date gaps
            date_gaps = [d for d in expected_dates if d not in available_dates]
            
            # Store coverage data
            data_coverage[holding.security_id] = {
                'symbol': symbol,
                'security_name': security.name if hasattr(security, 'name') else 'Unknown',
                'total_expected_days': total_expected_days,
                'available_days': available_days,
                'coverage_percent': round(coverage_percent, 2),
                'missing_days': len(date_gaps),
                'date_gaps': date_gaps[:50],
                'first_available_date': available_dates[0].isoformat() if available_dates else None,
                'last_available_date': available_dates[-1].isoformat() if available_dates else None
            }
            
            # Check if security has sufficient data
            if available_days == 0:
                missing_securities.append({
                    'security_id': holding.security_id,
                    'symbol': symbol,
                    'security_name': security.name if hasattr(security, 'name') else 'Unknown',
                    'reason': f'No historical price data available for period {start_date} to {end_date}',
                    'available_days': 0,
                    'required_days': min_required_days,
                    'coverage_percent': 0.0,
                    'period': f'{start_date} to {end_date}'
                })
                if strict_mode:
                    errors.append(f"{symbol}: No historical price data found for period {start_date} to {end_date}")
                else:
                    warnings.append(f"{symbol}: No historical price data found for period {start_date} to {end_date}")
            
            elif available_days < min_required_days:
                missing_securities.append({
                    'security_id': holding.security_id,
                    'symbol': symbol,
                    'security_name': security.name if hasattr(security, 'name') else 'Unknown',
                    'reason': f'Insufficient data: {available_days} days available, need {min_required_days} days',
                    'available_days': available_days,
                    'required_days': min_required_days,
                    'coverage_percent': coverage_percent,
                    'period': f'{start_date} to {end_date}'
                })
                if strict_mode:
                    errors.append(f"{symbol}: Only {available_days} days of data, need at least {min_required_days} days")
                else:
                    warnings.append(f"{symbol}: Only {available_days} days of data (recommended: {min_required_days} days)")
            
            elif coverage_percent < 80:
                missing_securities.append({
                    'security_id': holding.security_id,
                    'symbol': symbol,
                    'security_name': security.name if hasattr(security, 'name') else 'Unknown',
                    'reason': f'Low data coverage: {coverage_percent:.1f}%',
                    'available_days': available_days,
                    'required_days': min_required_days,
                    'coverage_percent': coverage_percent,
                    'period': f'{start_date} to {end_date}'
                })
                if strict_mode:
                    warnings.append(f"{symbol}: Low data coverage ({coverage_percent:.1f}%)")
                else:
                    warnings.append(f"{symbol}: Data coverage {coverage_percent:.1f}% (may affect accuracy)")
            
            missing_dates.extend(date_gaps[:20])
        
        return {
            'missing_securities': missing_securities,
            'missing_dates': sorted(list(set(missing_dates))),
            'data_coverage': data_coverage,
            'errors': errors,
            'warnings': warnings
        }
    
    @staticmethod
    def _validate_nifty_data(
        start_date: date,
        end_date: date,
        min_required_days: int,
        strict_mode: bool = True
    ) -> Dict[str, Any]:
        """Validate Nifty benchmark data availability"""
        errors = []
        warnings = []
        missing_dates = []
        
        nifty_benchmark = BenchmarkService.get_nifty_benchmark()
        if not nifty_benchmark:
            errors.append("Nifty benchmark not found in database")
            return {
                'errors': errors,
                'warnings': warnings,
                'missing_dates': []
            }
        
        nifty_data = BenchmarkData.query.filter_by(
            benchmark_id=nifty_benchmark.id
        ).filter(
            BenchmarkData.date >= start_date,
            BenchmarkData.date <= end_date
        ).order_by(BenchmarkData.date.asc()).all()
        
        available_days = len(nifty_data)
        
        if available_days == 0:
            if strict_mode:
                errors.append("No Nifty benchmark data available for the selected period")
            else:
                warnings.append("No Nifty benchmark data available - calculations may be inaccurate")
        elif available_days < min_required_days:
            if strict_mode:
                errors.append(f"Insufficient Nifty data: {available_days} days available, need at least {min_required_days} days")
            else:
                warnings.append(f"Nifty data: {available_days} days (recommended: {min_required_days} days)")
        
        # Find missing dates
        available_dates = {bd.date for bd in nifty_data}
        current_date = start_date
        while current_date <= end_date:
            if current_date.weekday() < 5 and current_date not in available_dates:
                missing_dates.append(current_date)
            current_date += timedelta(days=1)
        
        return {
            'errors': errors,
            'warnings': warnings,
            'missing_dates': missing_dates[:50]
        }
    
    @staticmethod
    def _validate_cashflow_data(
        client_id: int,
        start_date: date,
        end_date: date,
        strict_mode: bool = True
    ) -> Dict[str, Any]:
        """Validate cashflow data availability"""
        errors = []
        warnings = []
        
        cashflows = Cashflow.query.filter_by(client_id=client_id).all()
        
        if not cashflows:
            if strict_mode:
                errors.append("No cashflow data found - required for XIRR and Alpha calculations")
            else:
                warnings.append("No cashflow data found - XIRR and Alpha calculations may be inaccurate")
        else:
            period_cashflows = [cf for cf in cashflows if start_date <= cf.date.date() <= end_date]
            if len(period_cashflows) == 0:
                warnings.append(f"No cashflows found in period {start_date} to {end_date}")
        
        return {
            'errors': errors,
            'warnings': warnings
        }
    
    @staticmethod
    def _generate_data_recommendations(
        validation_results: Dict[str, Dict[str, Any]],
        missing_securities: set
    ) -> List[str]:
        """Generate recommendations for data updates"""
        recommendations = []
        
        if missing_securities:
            sec_list = ', '.join(sorted(list(missing_securities))[:10])
            recommendations.append(
                f"Upload historical price data for {len(missing_securities)} securities: {sec_list}"
                + ("..." if len(missing_securities) > 10 else "")
            )
        
        return recommendations
    
    @staticmethod
    def _generate_metric_recommendations(
        metric_type: str,
        warnings: List[str],
        errors: List[str],
        missing_securities: List[Dict],
        data_coverage: Dict
    ) -> List[str]:
        """Generate specific recommendations for a metric"""
        recommendations = []
        
        if missing_securities:
            sec_list = ', '.join([s['symbol'] if isinstance(s, dict) else str(s) for s in missing_securities[:5]])
            recommendations.append(f"Upload historical prices for: {sec_list}")
            if len(missing_securities) > 5:
                recommendations.append(f"... and {len(missing_securities) - 5} more securities")
        
        if metric_type in ['beta', 'alpha', 'capture_ratio']:
            recommendations.append("Ensure Nifty benchmark data is updated for the period")
        
        if errors:
            recommendations.append("Fix data issues before calculating for accurate results")
        
        return recommendations
    
    @staticmethod
    def calculate_metric_with_period(
        client_id: int,
        metric_type: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        period_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Calculate a specific financial metric for a given time period
        Uses fallbacks to ensure calculation always returns a result
        """
        try:
            client = Client.query.get_or_404(client_id)
            
            if end_date is None:
                end_date = date.today()
            
            if start_date is None:
                if period_days is None:
                    period_days = FinancialAnalyticsService.DEFAULT_PERIODS.get(metric_type, 365)
                start_date = end_date - timedelta(days=period_days)
            
            # Get first trade date
            first_trade_date = FinancialAnalyticsService.get_client_first_trade_date(client_id)
            if first_trade_date and start_date < first_trade_date:
                start_date = first_trade_date
            
            # Get holdings
            holdings = Holding.query.filter_by(client_id=client_id).all()
            if not holdings:
                return {
                    'success': True,
                    'metric_type': metric_type,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'value': 0.0,
                    'data_quality': 'no_data',
                    'warnings': ['No holdings found for client'],
                    'fallback_used': True
                }
            
            # Check historical data availability first
            validation_info = None
            try:
                validation_result = FinancialAnalyticsService._validate_historical_prices(
                    holdings=holdings,
                    start_date=start_date,
                    end_date=end_date,
                    min_required_days=FinancialAnalyticsService.MIN_REQUIRED_DAYS.get(metric_type, 30),
                    strict_mode=False
                )
                validation_info = validation_result
                
                # Try to calculate with historical data first
                result = FinancialAnalyticsService._calculate_with_historical_data(
                    client_id, metric_type, start_date, end_date, holdings
                )
                if result and result.get('success'):
                    result['validation_info'] = validation_info
                    return result
            except Exception as e:
                logger.warning(f"Historical calculation failed for {metric_type}: {str(e)}, using fallback")
            
            # Fallback to simplified calculation
            try:
                result = FinancialAnalyticsService._calculate_with_fallback(
                    client_id, metric_type, start_date, end_date, holdings
                )
                if result and result.get('success'):
                    result['fallback_used'] = True
                    
                    # Add detailed missing data information
                    missing_data_details = []
                    if validation_info:
                        if validation_info.get('missing_securities'):
                            missing_securities = validation_info['missing_securities']
                            if len(missing_securities) > 0:
                                sec_list = ', '.join([s['symbol'] if isinstance(s, dict) else str(s) for s in missing_securities[:5]])
                                missing_data_details.append(f"Missing data for: {sec_list}")
                                if len(missing_securities) > 5:
                                    missing_data_details.append(f"... and {len(missing_securities) - 5} more securities")
                        
                        if validation_info.get('data_coverage'):
                            low_coverage = []
                            for sec_id, coverage in validation_info['data_coverage'].items():
                                if coverage['coverage_percent'] < 80:
                                    low_coverage.append(f"{coverage['symbol']} ({coverage['coverage_percent']:.1f}% coverage)")
                            if low_coverage:
                                missing_data_details.append(f"Low data coverage: {', '.join(low_coverage[:3])}")
                    
                    result['warnings'] = result.get('warnings', []) + [
                        f'Used fallback calculation method due to insufficient historical data'
                    ] + missing_data_details
                    
                    # Attach validation info for detailed missing data display
                    if validation_info:
                        result['validation_info'] = validation_info
                        # Include missing securities in a way that's easy to display
                        if validation_info.get('missing_securities'):
                            result['missing_securities_list'] = validation_info['missing_securities']
                    result['missing_data_details'] = missing_data_details
                    return result
            except Exception as e:
                logger.warning(f"Fallback calculation failed for {metric_type}: {str(e)}, using default")
            
            # Final fallback - use default/estimated value
            # Add missing data details
            missing_data_details = []
            try:
                validation_result = FinancialAnalyticsService._validate_historical_prices(
                    holdings=holdings,
                    start_date=start_date,
                    end_date=end_date,
                    min_required_days=FinancialAnalyticsService.MIN_REQUIRED_DAYS.get(metric_type, 30),
                    strict_mode=False
                )
                
                if validation_result.get('missing_securities'):
                    missing_securities = validation_result['missing_securities']
                    if len(missing_securities) > 0:
                        sec_list = ', '.join([s['symbol'] if isinstance(s, dict) else str(s) for s in missing_securities[:10]])
                        missing_data_details.append(f"Missing data for: {sec_list}")
                        if len(missing_securities) > 10:
                            missing_data_details.append(f"... and {len(missing_securities) - 10} more securities")
            except:
                pass
            
            # Get missing securities list for display
            missing_securities_list = []
            try:
                validation_result = FinancialAnalyticsService._validate_historical_prices(
                    holdings=holdings,
                    start_date=start_date,
                    end_date=end_date,
                    min_required_days=FinancialAnalyticsService.MIN_REQUIRED_DAYS.get(metric_type, 30),
                    strict_mode=False
                )
                missing_securities_list = validation_result.get('missing_securities', [])
            except:
                pass
            
            return {
                'success': True,
                'metric_type': metric_type,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'period_days': (end_date - start_date).days,
                'value': FinancialAnalyticsService._get_default_metric_value(metric_type),
                'data_quality': 'estimated',
                'warnings': [
                    'Insufficient data for accurate calculation. Using estimated/default value.',
                    'Please upload historical price data for more accurate results.'
                ] + missing_data_details,
                'fallback_used': True,
                'estimated': True,
                'missing_data_details': missing_data_details,
                'missing_securities_list': missing_securities_list,
                'calculation_timestamp': datetime.utcnow().isoformat(),
                'note': f'This calculation uses estimated values. Historical price data is required for period {start_date} to {end_date}. Please upload missing data for accurate results.'
            }
            
        except Exception as e:
            logger.error(f"Error calculating {metric_type} for client {client_id}: {str(e)}")
            # Even on error, return a result with default value
            return {
                'success': True,
                'metric_type': metric_type,
                'value': FinancialAnalyticsService._get_default_metric_value(metric_type),
                'data_quality': 'error',
                'warnings': [f'Calculation error: {str(e)}. Using default value.'],
                'fallback_used': True,
                'estimated': True
            }
    
    @staticmethod
    def _calculate_with_historical_data(
        client_id: int,
        metric_type: str,
        start_date: date,
        end_date: date,
        holdings: List
    ) -> Optional[Dict[str, Any]]:
        """Try to calculate using historical price data"""
        try:
            # Check if we have sufficient historical data
            validation_result = FinancialAnalyticsService._validate_historical_prices(
                holdings=holdings,
                start_date=start_date,
                end_date=end_date,
                min_required_days=FinancialAnalyticsService.MIN_REQUIRED_DAYS.get(metric_type, 30),
                strict_mode=False
            )
            
            # Check if we have enough data to proceed
            if validation_result['errors']:
                logger.info(f"Insufficient historical data for {metric_type}: {validation_result['errors']}")
                return None
            
            historical_context = None
            if metric_type in ['beta', 'alpha', 'sharpe_ratio', 'capture_ratio']:
                historical_context = FinancialAnalyticsService._build_historical_context(
                    client_id=client_id,
                    start_date=start_date,
                    end_date=end_date,
                    holdings=holdings,
                    validation_result=validation_result
                )

                if historical_context:
                    if metric_type == 'beta':
                        historical_result = FinancialAnalyticsService._calculate_beta_with_historical(
                            historical_context
                        )
                    elif metric_type == 'alpha':
                        historical_result = FinancialAnalyticsService._calculate_alpha_with_historical(
                            historical_context
                        )
                    elif metric_type == 'sharpe_ratio':
                        historical_result = FinancialAnalyticsService._calculate_sharpe_ratio_with_historical(
                            historical_context
                        )
                    else:  # capture_ratio
                        historical_result = FinancialAnalyticsService._calculate_capture_ratio_with_historical(
                            historical_context
                        )

                    if historical_result:
                        historical_result['validation_info'] = validation_result
                        return historical_result
                    # Fall through to fallback when historical calc not possible
                else:
                    logger.info(f"Historical context unavailable for {metric_type}; using fallback")
            
            # For now, return None to indicate we need to implement actual historical calculations
            # When historical data is available, we should calculate using it
            # For metrics that need historical data, we'll implement them one by one
            
            # Check data coverage
            total_coverage = 0
            coverage_count = 0
            for sec_id, coverage in validation_result['data_coverage'].items():
                total_coverage += coverage['coverage_percent']
                coverage_count += 1
            
            avg_coverage = total_coverage / coverage_count if coverage_count > 0 else 0
            
            # If we have good coverage (>= 80%), we can proceed with historical calculation
            # But for now, we'll still use fallback since actual historical calculations aren't fully implemented
            if avg_coverage >= 80:
                logger.info(f"Good historical data coverage ({avg_coverage:.1f}%) for {metric_type}, but historical calculation not yet implemented")
                # Return None to use fallback but with better messaging
                return None
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking historical data for {metric_type}: {str(e)}")
            return None
    
    @staticmethod
    def _build_historical_context(
        client_id: int,
        start_date: date,
        end_date: date,
        holdings: List,
        validation_result: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Prepare portfolio & benchmark return series for historical calculations"""
        try:
            if start_date >= end_date:
                return None

            min_days = max(
                FinancialAnalyticsService.MIN_REQUIRED_DAYS.get('beta', 30),
                20  # Ensure reasonable overlap
            )
            coverage_threshold = FinancialAnalyticsService.MIN_COVERAGE_PERCENT

            price_series_map = {}
            weight_map = {}
            total_value = 0.0

            for holding in holdings:
                security = holding.security
                if not security or not holding.quantity:
                    continue

                coverage_info = validation_result.get('data_coverage', {}).get(holding.security_id) if validation_result else None
                if coverage_info:
                    if coverage_info['coverage_percent'] < coverage_threshold or coverage_info['available_days'] < min_days:
                        continue

                historical_records = HistoricalPrice.query.filter(
                    HistoricalPrice.security_id == holding.security_id,
                    HistoricalPrice.date >= start_date,
                    HistoricalPrice.date <= end_date
                ).order_by(HistoricalPrice.date.asc()).all()

                if len(historical_records) < min_days:
                    continue

                dates = [record.date for record in historical_records]
                prices = [float(record.close_price) for record in historical_records]

                if not dates or not prices:
                    continue

                series = pd.Series(data=prices, index=pd.to_datetime(dates))
                if series.empty:
                    continue

                price_series_map[security.symbol] = series

                last_price = prices[-1]
                quantity = float(holding.quantity)
                if quantity <= 0 or last_price <= 0:
                    continue

                current_value = quantity * last_price
                weight_map[security.symbol] = current_value
                total_value += current_value

            if not price_series_map or total_value <= 0:
                return None

            price_df = pd.DataFrame(price_series_map)
            price_df = price_df.sort_index().ffill().dropna(how='any')
            if len(price_df) < min_days:
                return None

            returns_df = price_df.pct_change().dropna(how='any')
            if returns_df.empty or len(returns_df) < min_days:
                return None

            weights = {symbol: value / total_value for symbol, value in weight_map.items()}
            weights_series = pd.Series(weights)

            portfolio_returns = returns_df.mul(weights_series, axis=1).sum(axis=1)
            if portfolio_returns.empty:
                return None

            nifty_benchmark = BenchmarkService.get_nifty_benchmark()
            if not nifty_benchmark:
                logger.warning("Nifty benchmark not configured; cannot calculate historical metrics")
                return None

            benchmark_records = BenchmarkData.query.filter(
                BenchmarkData.benchmark_id == nifty_benchmark.id,
                BenchmarkData.date >= start_date,
                BenchmarkData.date <= end_date
            ).order_by(BenchmarkData.date.asc()).all()

            if len(benchmark_records) < min_days:
                logger.warning("Insufficient benchmark data for historical metrics")
                return None

            benchmark_series = pd.Series(
                data=[float(record.price) for record in benchmark_records],
                index=pd.to_datetime([record.date for record in benchmark_records])
            )
            benchmark_returns = benchmark_series.pct_change().dropna()
            if benchmark_returns.empty:
                return None

            combined = pd.concat(
                [portfolio_returns.rename('portfolio'), benchmark_returns.rename('benchmark')],
                axis=1,
                join='inner'
            ).dropna()

            if len(combined) < min_days:
                logger.warning("Not enough overlapping days for historical metrics")
                return None

            return {
                'start_date': start_date,
                'end_date': end_date,
                'combined': combined,
                'weights': weights,
                'total_value': total_value,
                'observations': len(combined),
                'returns_df': returns_df,
                'price_df': price_df
            }
        except Exception as e:
            logger.error(f"Error building historical context: {str(e)}")
            return None

    @staticmethod
    def _compute_beta_from_series(portfolio_series: pd.Series, benchmark_series: pd.Series) -> Optional[float]:
        variance = np.var(benchmark_series)
        if variance == 0:
            return None
        covariance = np.cov(portfolio_series, benchmark_series)[0][1]
        beta = covariance / variance
        if not np.isfinite(beta):
            return None
        return float(beta)

    @staticmethod
    def _calculate_beta_with_historical(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            beta_value = FinancialAnalyticsService._compute_beta_from_series(
                context['combined']['portfolio'],
                context['combined']['benchmark']
            )
            if beta_value is None:
                return None

            beta_value = round(beta_value, 3)
            notes = [
                'Calculated using historical daily returns (portfolio vs Nifty benchmark)',
                f'Data points used: {context["observations"]}'
            ]

            return {
                'success': True,
                'metric_type': 'beta',
                'start_date': context['start_date'].isoformat(),
                'end_date': context['end_date'].isoformat(),
                'period_days': (context['end_date'] - context['start_date']).days,
                'value': beta_value,
                'data_quality': 'historical',
                'warnings': [],
                'notes': notes,
                'fallback_used': False,
                'calculation_date': datetime.utcnow().isoformat(),
                'observations_used': context['observations'],
                'weights': context['weights']
            }
        except Exception as e:
            logger.error(f"Error calculating historical beta: {str(e)}")
            return None

    @staticmethod
    def _calculate_alpha_with_historical(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            portfolio_series = context['combined']['portfolio']
            benchmark_series = context['combined']['benchmark']
            beta_value = FinancialAnalyticsService._compute_beta_from_series(portfolio_series, benchmark_series)
            if beta_value is None:
                return None

            days = len(context['combined'])
            if days == 0:
                return None

            risk_free_rate = 0.065  # annual
            annual_factor = 252 / days

            portfolio_total_return = (1 + portfolio_series).prod()
            benchmark_total_return = (1 + benchmark_series).prod()
            if portfolio_total_return <= 0 or benchmark_total_return <= 0:
                return None

            portfolio_annual = portfolio_total_return ** annual_factor - 1
            benchmark_annual = benchmark_total_return ** annual_factor - 1

            expected_return = risk_free_rate + beta_value * (benchmark_annual - risk_free_rate)
            alpha = portfolio_annual - expected_return

            alpha_percent = round(alpha * 100, 2)
            notes = [
                'Calculated using annualized historical returns (portfolio vs Nifty benchmark)',
                f'Data points used: {context["observations"]}',
                f'Portfolio annualized return: {round(portfolio_annual * 100, 2)}%',
                f'Benchmark annualized return: {round(benchmark_annual * 100, 2)}%',
            ]

            return {
                'success': True,
                'metric_type': 'alpha',
                'start_date': context['start_date'].isoformat(),
                'end_date': context['end_date'].isoformat(),
                'period_days': (context['end_date'] - context['start_date']).days,
                'value': alpha_percent,
                'data_quality': 'historical',
                'warnings': [],
                'notes': notes,
                'fallback_used': False,
                'calculation_date': datetime.utcnow().isoformat(),
                'observations_used': context['observations'],
                'weights': context['weights']
            }
        except Exception as e:
            logger.error(f"Error calculating historical alpha: {str(e)}")
            return None

    @staticmethod
    def _calculate_sharpe_ratio_with_historical(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            portfolio_series = context['combined']['portfolio']
            if portfolio_series.empty:
                return None

            risk_free_rate = 0.065  # annual
            rf_daily = risk_free_rate / 252
            mean_daily = portfolio_series.mean()
            std_daily = portfolio_series.std(ddof=0)

            if std_daily == 0:
                return None

            sharpe = (mean_daily - rf_daily) * np.sqrt(252) / std_daily
            if not np.isfinite(sharpe):
                return None

            sharpe_value = round(float(sharpe), 3)
            notes = [
                'Calculated using historical daily portfolio returns',
                f'Data points used: {context["observations"]}',
                f'Mean daily return: {round(mean_daily * 100, 3)}%',
                f'Standard deviation (daily): {round(std_daily * 100, 3)}%'
            ]

            return {
                'success': True,
                'metric_type': 'sharpe_ratio',
                'start_date': context['start_date'].isoformat(),
                'end_date': context['end_date'].isoformat(),
                'period_days': (context['end_date'] - context['start_date']).days,
                'value': sharpe_value,
                'data_quality': 'historical',
                'warnings': [],
                'notes': notes,
                'fallback_used': False,
                'calculation_date': datetime.utcnow().isoformat(),
                'observations_used': context['observations'],
                'weights': context['weights']
            }
        except Exception as e:
            logger.error(f"Error calculating historical Sharpe ratio: {str(e)}")
            return None

    @staticmethod
    def _calculate_capture_ratio_with_historical(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            combined = context['combined']
            benchmark_positive = combined[combined['benchmark'] > 0]
            benchmark_negative = combined[combined['benchmark'] < 0]

            if benchmark_positive.empty or benchmark_negative.empty:
                logger.info("Insufficient positive/negative benchmark days for capture ratio")
                return None

            def capture_ratio(segment: pd.DataFrame) -> Optional[float]:
                bench_return = (1 + segment['benchmark']).prod() - 1
                port_return = (1 + segment['portfolio']).prod() - 1
                if bench_return == 0:
                    return None
                ratio = (port_return / bench_return) * 100
                if not np.isfinite(ratio):
                    return None
                return float(ratio)

            upside = capture_ratio(benchmark_positive)
            downside = capture_ratio(benchmark_negative)

            if upside is None or downside is None:
                return None

            notes = [
                'Calculated using historical daily returns',
                f'Up days: {len(benchmark_positive)} | Down days: {len(benchmark_negative)}',
                f'Data points used: {context["observations"]}'
            ]

            return {
                'success': True,
                'metric_type': 'capture_ratio',
                'start_date': context['start_date'].isoformat(),
                'end_date': context['end_date'].isoformat(),
                'period_days': (context['end_date'] - context['start_date']).days,
                'value': {
                    'upside': round(upside, 2),
                    'downside': round(downside, 2)
                },
                'data_quality': 'historical',
                'warnings': [],
                'notes': notes,
                'fallback_used': False,
                'calculation_date': datetime.utcnow().isoformat(),
                'observations_used': context['observations'],
                'weights': context['weights']
            }
        except Exception as e:
            logger.error(f"Error calculating historical capture ratio: {str(e)}")
            return None
    
    @staticmethod
    def _calculate_with_fallback(
        client_id: int,
        metric_type: str,
        start_date: date,
        end_date: date,
        holdings: List
    ) -> Optional[Dict[str, Any]]:
        """Calculate using simplified/fallback methods"""
        # Import locally to avoid circular imports
        from api.v1.performance import (
            calculate_risk_metrics,
            calculate_xirr,
            calculate_nifty_xirr,
            calculate_portfolio_beta_simplified,
            calculate_portfolio_alpha_simplified,
            calculate_portfolio_volatility_simplified,
            calculate_sharpe_ratio_simplified,
            calculate_treynor_ratio_simplified,
            calculate_information_ratio_simplified,
            calculate_sortino_ratio_simplified,
            calculate_calmar_ratio_simplified
        )
        
        try:
            # Prepare holding data
            holding_data = []
            total_value = 0
            
            for holding in holdings:
                if holding.security and holding.security.current_price:
                    quantity = float(holding.quantity) if holding.quantity else 0
                    current_price = float(holding.security.current_price) if holding.security.current_price else 0
                    current_value = quantity * current_price
                    total_value += current_value
                    
                    # Get sector
                    sector = 'Unknown'
                    if holding.security.meta_data:
                        try:
                            import json
                            meta_data = json.loads(holding.security.meta_data)
                            sector = meta_data.get('sector', meta_data.get('industry', 'Unknown'))
                        except:
                            pass
                    
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
                return None
            
            # Calculate weights
            weights = [h['value'] / total_value for h in holding_data]
            
            # Calculate based on metric type
            value = None
            warnings = []
            
            if metric_type == 'beta':
                value = calculate_portfolio_beta_simplified(holding_data, weights)
                if value is None:
                    warnings.append('Used sector-based estimates (some sectors unknown)')
                    value = 1.0  # Default beta
                else:
                    warnings.append('Calculated using sector-based estimates (historical data not available)')
            
            elif metric_type == 'alpha':
                try:
                    value = calculate_portfolio_alpha_simplified(holding_data, weights, client_id)
                    warnings.append('Calculated using simplified method')
                except:
                    return None
            
            elif metric_type == 'portfolio_volatility':
                value = calculate_portfolio_volatility_simplified(holding_data, weights)
                warnings.append('Calculated using sector-based estimates')
            
            elif metric_type == 'sharpe_ratio':
                try:
                    value = calculate_sharpe_ratio_simplified(holding_data, weights, client_id)
                    warnings.append('Calculated using simplified method')
                except:
                    return None
            
            elif metric_type == 'treynor_ratio':
                try:
                    value = calculate_treynor_ratio_simplified(holding_data, weights, client_id)
                    warnings.append('Calculated using simplified method')
                except:
                    return None
            
            elif metric_type == 'information_ratio':
                try:
                    value = calculate_information_ratio_simplified(holding_data, weights, client_id)
                    warnings.append('Calculated using simplified method')
                except:
                    return None
            
            elif metric_type == 'sortino_ratio':
                try:
                    value = calculate_sortino_ratio_simplified(holding_data, weights, client_id)
                    warnings.append('Calculated using simplified method')
                except:
                    return None
            
            elif metric_type == 'calmar_ratio':
                try:
                    value = calculate_calmar_ratio_simplified(holding_data, weights, client_id)
                    warnings.append('Calculated using simplified method')
                except:
                    return None
            
            elif metric_type == 'xirr':
                try:
                    from models import Cashflow
                    cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
                    if cashflows:
                        current_value = total_value
                        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
                        xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
                        value = xirr * 100  # Convert to percentage
                        warnings.append('Calculated using available cashflow data')
                    else:
                        return None
                except Exception as e:
                    logger.error(f"Error calculating XIRR: {str(e)}")
                    return None
            
            elif metric_type == 'benchmark_comparison':
                try:
                    from models import Cashflow
                    cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
                    if cashflows:
                        current_value = total_value
                        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
                        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
                        nifty_xirr, nifty_current_value, _ = calculate_nifty_xirr(client_id)
                        
                        value = {
                            'portfolio_xirr': portfolio_xirr * 100,
                            'nifty_xirr': nifty_xirr * 100,
                            'outperformance': (portfolio_xirr - nifty_xirr) * 100,
                            'portfolio_value': current_value,
                            'nifty_value': nifty_current_value
                        }
                        warnings.append('Calculated using available cashflow data')
                    else:
                        return None
                except Exception as e:
                    logger.error(f"Error calculating benchmark comparison: {str(e)}")
                    return None
            
            else:
                # Try to get from risk_metrics
                try:
                    risk_metrics = calculate_risk_metrics(holdings, client_id)
                    if metric_type in risk_metrics:
                        value = risk_metrics[metric_type]
                        warnings.append('Calculated using risk metrics')
                    else:
                        return None
                except:
                    return None
            
            if value is not None:
                return {
                    'success': True,
                    'metric_type': metric_type,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'period_days': (end_date - start_date).days,
                    'value': value,
                    'data_quality': 'fallback',
                    'warnings': warnings,
                    'fallback_used': True,
                    'calculation_date': datetime.utcnow().isoformat(),  # Ensure fresh calculation
                    'note': f'Note: This calculation uses current holdings and does not account for the specified date range ({start_date} to {end_date}). Historical data is required for period-specific calculations.'
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error in fallback calculation: {str(e)}")
            return None
    
    @staticmethod
    def _get_default_metric_value(metric_type: str) -> float:
        """Get default/estimated value for a metric when no calculation is possible"""
        defaults = {
            'beta': 1.0,
            'alpha': 0.0,
            'sharpe_ratio': 0.0,
            'treynor_ratio': 0.0,
            'sortino_ratio': 0.0,
            'information_ratio': 0.0,
            'calmar_ratio': 0.0,
            'portfolio_volatility': 20.0,
            'max_drawdown': 0.0,
            'var_95': 0.0,
            'xirr': 0.0,
            'upside_capture_ratio': 100.0,
            'downside_capture_ratio': 100.0,
            'capture_ratio': {'upside': 100.0, 'downside': 100.0}
        }
        return defaults.get(metric_type, 0.0)
    
    @staticmethod
    def calculate_multiple_metrics(
        client_id: int,
        metrics_config: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calculate multiple metrics with individual time periods
        Always returns results, even if fallbacks are used
        """
        results = {}
        warnings = []
        errors = []
        
        for metric_type, config in metrics_config.items():
            try:
                result = FinancialAnalyticsService.calculate_metric_with_period(
                    client_id=client_id,
                    metric_type=metric_type,
                    start_date=config.get('start_date'),
                    end_date=config.get('end_date'),
                    period_days=config.get('period_days')
                )
                
                # Always include result (even if fallback was used)
                # Add calculation timestamp to ensure freshness
                result['calculation_timestamp'] = datetime.utcnow().isoformat()
                results[metric_type] = result
                
                # Collect warnings and errors
                if result.get('warnings'):
                    warnings.extend([f"{metric_type}: {w}" for w in result.get('warnings', [])])
                
                if result.get('missing_data_details'):
                    warnings.extend([f"{metric_type}: {d}" for d in result.get('missing_data_details', [])])
                
                if result.get('fallback_used'):
                    warnings.append(f"{metric_type}: Used fallback calculation method")
                
                if result.get('estimated'):
                    warnings.append(f"{metric_type}: Value is estimated due to insufficient data")
                
                if not result.get('success', True):
                    errors.append({
                        'metric': metric_type,
                        'error': result.get('error', 'Unknown error')
                    })
                    
            except Exception as e:
                logger.error(f"Error calculating {metric_type}: {str(e)}")
                # Even on exception, return a result with default value
                results[metric_type] = {
                    'success': True,
                    'metric_type': metric_type,
                    'value': FinancialAnalyticsService._get_default_metric_value(metric_type),
                    'data_quality': 'error',
                    'warnings': [f'Calculation failed: {str(e)}. Using default value.'],
                    'fallback_used': True,
                    'estimated': True
                }
                errors.append({
                    'metric': metric_type,
                    'error': str(e)
                })
        
        # Always return success=True, but include warnings/errors in response
        # Force fresh calculation - no caching (use timestamp to ensure uniqueness)
        calculation_timestamp = datetime.utcnow()
        return {
            'success': True,  # Always true - we always return results
            'client_id': client_id,
            'results': results,
            'warnings': warnings,
            'errors': errors,  # For information only
            'has_warnings': len(warnings) > 0,
            'has_errors': len(errors) > 0,
            'calculated_at': calculation_timestamp.isoformat(),
            'cache_disabled': True,  # Ensure no caching
            'calculation_id': f"{client_id}_{calculation_timestamp.timestamp()}"  # Unique ID for each calculation
        }

