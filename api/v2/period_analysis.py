"""
Period Analysis API V2 - Restructured for efficiency and maintainability

This API follows a clean architecture:
1. Data Collection & Validation
2. Core Performance Calculations
3. Enhanced Analytics
"""
import logging
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from api.core.response import APIResponse
from api.core.exceptions import ValidationError
from models import Client, Cashflow, Transaction

from api.v2.period_analysis_data import PeriodAnalysisDataCollector
from api.v2.period_performance_calculator import PeriodPerformanceCalculator
from api.v2.period_enhanced_analytics import PeriodEnhancedAnalyticsCalculator

logger = logging.getLogger(__name__)

# Create blueprint
period_analysis_v2_bp = Blueprint('period_analysis_v2', __name__)


class WarningCaptureHandler(logging.Handler):
    """Custom logging handler to capture warnings and errors for user display"""
    def __init__(self, warnings_list, level=logging.NOTSET):
        super().__init__(level)
        self.warnings_list = warnings_list
    
    def emit(self, record):
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


def convert_dates_to_strings(obj):
    """
    Recursively convert date and datetime objects to ISO format strings
    for JSON serialization compatibility
    Handles nested structures safely
    """
    from datetime import date, datetime
    import decimal
    
    # Handle None
    if obj is None:
        return None
    
    # Handle date/datetime objects
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    
    # Handle Decimal (common in financial data)
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    
    # Handle dict
    if isinstance(obj, dict):
        try:
            return {str(key): convert_dates_to_strings(value) for key, value in obj.items()}
        except (TypeError, AttributeError) as e:
            logger.warning(f"Error converting dict: {str(e)}")
            return str(obj)
    
    # Handle list
    if isinstance(obj, list):
        try:
            return [convert_dates_to_strings(item) for item in obj]
        except (TypeError, AttributeError) as e:
            logger.warning(f"Error converting list: {str(e)}")
            return [str(item) for item in obj]
    
    # Handle tuple
    if isinstance(obj, tuple):
        try:
            return tuple(convert_dates_to_strings(item) for item in obj)
        except (TypeError, AttributeError) as e:
            logger.warning(f"Error converting tuple: {str(e)}")
            return tuple(str(item) for item in obj)
    
    # Handle set
    if isinstance(obj, set):
        try:
            return [convert_dates_to_strings(item) for item in obj]
        except (TypeError, AttributeError) as e:
            logger.warning(f"Error converting set: {str(e)}")
            return [str(item) for item in obj]
    
    # Handle SQLAlchemy models or other objects with __dict__
    if hasattr(obj, '__dict__'):
        try:
            return convert_dates_to_strings(obj.__dict__)
        except (TypeError, AttributeError) as e:
            logger.warning(f"Error converting object: {str(e)}")
            return str(obj)
    
    # Handle other types (int, float, str, bool, etc.) - return as-is
    return obj


def validate_and_parse_request(client_id, request_obj):
    """
    Validate and parse request parameters
    
    Returns:
        (start_date, end_date, basis, matching, analysis_type, prev_period_start_date)
    """
    start_date_str = request_obj.args.get('start_date')
    end_date_str = request_obj.args.get('end_date')
    analysis_type = request_obj.args.get('analysis_type', 'CUSTOM')
    basis = request_obj.args.get('basis', 'adjusted').lower()
    matching = request_obj.args.get('matching', 'fifo').lower()
    prev_period_start_date_str = request_obj.args.get('prev_period_start_date')
    
    # Validate end_date
    if not end_date_str:
        raise ValidationError("end_date is required")
    
    try:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError as e:
        raise ValidationError(f"Invalid end_date format. Use YYYY-MM-DD: {str(e)}")
    
    # Handle start_date (can be empty for lifetime)
    if not start_date_str:
        lifetime_types = {'ALL', 'LIFETIME', 'LIFE', 'FULL'}
        if analysis_type and analysis_type.upper() in lifetime_types:
            # Derive from earliest cashflow/transaction
            earliest_cf = Cashflow.query.filter_by(client_id=client_id)\
                .order_by(Cashflow.date.asc()).first()
            earliest_txn = Transaction.query.filter_by(client_id=client_id)\
                .order_by(Transaction.transaction_date.asc()).first()
            
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
            else:
                raise ValidationError(
                    "Lifetime analysis is not available because the client has no historical cashflows or transactions"
                )
        else:
            raise ValidationError("start_date is required")
    else:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError as e:
            raise ValidationError(f"Invalid start_date format. Use YYYY-MM-DD: {str(e)}")
    
    # Validate date range
    if start_date > end_date:
        raise ValidationError("start_date must be before end_date")
    
    # Parse prev_period_start_date if provided
    prev_period_start_date = None
    if prev_period_start_date_str:
        try:
            prev_period_start_date = datetime.strptime(prev_period_start_date_str, '%Y-%m-%d').date()
        except ValueError as e:
            raise ValidationError(f"Invalid prev_period_start_date format. Use YYYY-MM-DD: {str(e)}")
    
    # Validate basis and matching
    if basis not in ['adjusted', 'unadjusted']:
        raise ValidationError(f"Invalid basis: {basis}. Must be 'adjusted' or 'unadjusted'")
    
    if matching not in ['fifo', 'lifo']:
        raise ValidationError(f"Invalid matching: {matching}. Must be 'fifo' or 'lifo'")
    
    return start_date, end_date, basis, matching, analysis_type, prev_period_start_date


@period_analysis_v2_bp.route('/<int:client_id>/period-analysis', methods=['GET'])
@login_required
def get_period_analysis(client_id):
    """
    Get comprehensive period analysis for a client (V2 - Restructured)
    
    Query Parameters:
        - start_date (string, optional): Period start date (YYYY-MM-DD)
        - end_date (string, required): Period end date (YYYY-MM-DD)
        - basis (string, optional): 'adjusted' or 'unadjusted' (default: 'adjusted')
        - matching (string, optional): 'fifo' or 'lifo' (default: 'fifo')
        - analysis_type (string, optional): 'CUSTOM', '6M', '12M', 'LIFETIME' (default: 'CUSTOM')
        - prev_period_start_date (string, optional): Previous period start date for tracking
    
    Returns comprehensive analysis including:
    - Core performance metrics (MTM, trade gains, investment breakdown, gains breakdown, XIRR)
    - Enhanced analytics (risk, benchmark, holdings, sectors, value attribution, etc.)
    """
    # Initialize warnings list at the start
    warnings = []
    
    # Initialize a custom logging handler to capture warnings and errors
    warning_handler = WarningCaptureHandler(warnings)
    
    # Define which loggers to capture messages from
    loggers_to_capture = [
        logging.getLogger('api.v2.period_analysis'),
        logging.getLogger('api.v2.period_enhanced_analytics'),
        logging.getLogger('services.forward_holding_calculation_service'),
        logging.getLogger('services.portfolio_snapshot_service'),
        logging.getLogger('api.v1.portfolio_reconstruction'),
    ]
    
    # Add handler to all relevant loggers
    for log in loggers_to_capture:
        if warning_handler not in log.handlers:
            log.addHandler(warning_handler)
    
    # Check access control
    try:
        from access_control import get_accessible_clients
        accessible_clients = get_accessible_clients()
        if accessible_clients is None or client_id not in [c.id for c in accessible_clients]:
            return APIResponse.error("Access denied", status_code=403)
    except Exception as access_error:
        logger.warning(f"Access control check failed: {str(access_error)}")
        # Continue - access control might not be fully configured
    
    try:
        # Step 1: Validate and parse request
        start_date, end_date, basis, matching, analysis_type, prev_period_start_date = \
            validate_and_parse_request(client_id, request)
        
        logger.info(f"Period analysis V2 request - Client: {client_id}, Period: {start_date} to {end_date}")
        
        # Step 2: Collect and validate all data
        data_collector = PeriodAnalysisDataCollector(client_id, start_date, end_date)
        success, errors = data_collector.collect_and_validate()
        
        if not success:
            return APIResponse.error(
                f"Data collection failed: {', '.join(errors)}",
                status_code=400
            )
        
        logger.info(f"Data collection completed successfully: {data_collector.get_data_summary()}")

        from services.period_data_warnings import (
            collect_period_portfolio_warnings,
            merge_warnings,
        )

        merge_warnings(
            warnings,
            collect_period_portfolio_warnings(
                data_collector.start_portfolio,
                data_collector.end_portfolio,
                start_date,
                end_date,
            ),
        )
        
        # Step 3: Calculate core performance metrics
        performance_calculator = PeriodPerformanceCalculator(data_collector)
        performance_results = performance_calculator.calculate_all(basis, matching)
        
        # Log MTM data immediately after calculation to verify it's correct
        mtm_from_calc = performance_results.get('mtm_analysis', {})
        logger.info(f"✅ MTM data from calculator - Period: {start_date} to {end_date}, total_start_value: {mtm_from_calc.get('total_start_value')}, total_end_value: {mtm_from_calc.get('total_end_value')}")
        
        logger.info("Core performance calculations completed")
        
        # Log MTM data after core calculations but before enhanced analytics
        mtm_before_enhanced = performance_results.get('mtm_analysis', {})
        logger.info(f"🔍 MTM data BEFORE enhanced analytics - Period: {start_date} to {end_date}, total_start_value: {mtm_before_enhanced.get('total_start_value')}")
        
        # Step 4: Calculate enhanced analytics (pass warnings list to collect warnings)
        enhanced_calculator = PeriodEnhancedAnalyticsCalculator(data_collector)
        enhanced_results, _ = enhanced_calculator.calculate_all(
            performance_results,
            prev_period_start_date,
            warnings_list=warnings  # Pass warnings list to collect warnings (same reference, no need to extend)
        )
        
        # Log MTM data after enhanced analytics to check if it was modified
        mtm_after_enhanced = performance_results.get('mtm_analysis', {})
        logger.info(f"🔍 MTM data AFTER enhanced analytics - Period: {start_date} to {end_date}, total_start_value: {mtm_after_enhanced.get('total_start_value')}")
        
        logger.info("Enhanced analytics calculations completed")
        
        # Step 5: AI summaries (LOCAL ONLY - privacy safe)
        # DISABLED: AI summaries are now generated on-demand via UI button to prevent timeouts
        # Uses AIInsightsService which is designed to use local Ollama if available,
        # otherwise it falls back to deterministic, privacy-safe summaries.
        ai_text_summaries = None
        
        # Step 6: Build comprehensive response
        # Log TWR data before including in response
        twr_data = performance_results.get('twr', {})
        logger.info(f"📊 TWR in API Response - client_id: {client_id}, twr_data: {twr_data}, twr_percent: {twr_data.get('twr_percent', 'N/A') if isinstance(twr_data, dict) else 'N/A'}")
        
        response_data = {
            'client_id': client_id,
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'analysis_type': analysis_type,
                'duration_days': (end_date - start_date).days,
                'duration_months': round((end_date - start_date).days / 30.44, 1)
            },
            
            # Core performance metrics
            'mark_to_market_gains': performance_results.get('mtm_analysis', {}),
            'trade_gains': performance_results.get('trade_analysis', {}),
            'investment_breakdown': performance_results.get('investment_breakdown', {}),
            'gains_breakdown': performance_results.get('gains_breakdown', {}),
            'period_xirr': performance_results.get('period_xirr', {}),
            'twr': twr_data,  # Time-Weighted Return
            
            # Enhanced analytics
            'period_risk_metrics': enhanced_results.get('period_risk_metrics', {}),
            'benchmark_comparison': enhanced_results.get('benchmark_comparison', {}),
            'summary_metrics': enhanced_results.get('summary_metrics', {}),
            'holdings_changes': enhanced_results.get('holdings_changes', {}),
            'cashflow_analysis': enhanced_results.get('cashflow_analysis', {}),
            'trade_analytics': enhanced_results.get('trade_analytics', {}),
            'sector_performance': enhanced_results.get('sector_performance', {}),
            'value_attribution': enhanced_results.get('value_attribution', {}),
            'best_worst': enhanced_results.get('best_worst', {}),
            'stocks_most_bought': enhanced_results.get('stocks_most_bought', []),
            'prev_period_most_bought_performance': enhanced_results.get('prev_period_most_bought_performance', []),
            'stocks_sold': enhanced_results.get('stocks_sold', []),
            'transaction_quality': enhanced_results.get('transaction_quality', {}),
            'sector_analysis': enhanced_results.get('sector_analysis', {}),
            'industry_performance': enhanced_results.get('industry_performance', {}),
            'holdings_evolution': enhanced_results.get('holdings_evolution', {}),
            'concentration_risk': enhanced_results.get('concentration_risk', {}),
            'mtm_trades_analysis': enhanced_results.get('mtm_trades_analysis', {}),
            'best_performers': enhanced_results.get('best_performers', {}),
            'worst_performers': enhanced_results.get('worst_performers', {}),
            'segment_wise_xirr': enhanced_results.get('segment_wise_xirr', {}),
            'portfolio_holdings_comparison': enhanced_results.get('portfolio_holdings_comparison', []),
            'pathway': enhanced_results.get('pathway'),
            'ai_text_summaries': ai_text_summaries,
            
            # Add warnings to response
            'warnings': warnings,
            'has_warnings': len(warnings) > 0
        }

        # AI summaries generation - DISABLED: Now generated on-demand via UI button to prevent timeouts
        # The AI generation code has been moved to /enhanced-review/api/performance-analysis/generate-ai-summaries
        # This prevents timeout errors during initial period analysis load
        # Users can click "Generate AI Insights" button after data loads to get AI summaries on demand
        # 
        # Original AI generation code commented out below:
        # try:
        #     from services.ai_insights_service import AIInsightsService
        #     ... (all AI generation logic)
        # except Exception as e:
        #     logger.warning(f"AI summaries generation failed (continuing without AI): {str(e)}", exc_info=True)

        # ------------------------------------------------------------------
        # Previous review period stock activity
        # Use user-provided prev_period_start_date if available, otherwise calculate based on actual period duration
        # All calculations should be based on user-defined values only
        # ------------------------------------------------------------------
        try:
            # Check if user provided prev_period_start_date
            if prev_period_start_date:
                # User has explicitly provided the previous period start date
                # Previous period end date is current period start date
                prev_start = prev_period_start_date
                prev_end = start_date
                
                logger.info(f"Using user-provided previous period: {prev_start} to {prev_end}")
                
                from api.v1.period_analysis import (
                    calculate_previous_period_stocks_most_bought,
                    calculate_previous_period_stocks_sold_analysis
                )
                
                prev_duration_days = (prev_end - prev_start).days
                response_data['previous_review_period'] = {
                    'start_date': prev_start.isoformat(),
                    'end_date': prev_end.isoformat(),
                    'duration_days': prev_duration_days,
                    'duration_months': round(prev_duration_days / 30.44, 1)
                }
                
                response_data['previous_review_period_stocks_most_bought'] = calculate_previous_period_stocks_most_bought(
                    client_id=client_id,
                    prev_period_start=prev_start,
                    prev_period_end=prev_end,
                    current_end_date=end_date,
                    current_end_map=data_collector.end_map
                )
                
                response_data['previous_review_period_stocks_sold'] = calculate_previous_period_stocks_sold_analysis(
                    client_id=client_id,
                    prev_period_start=prev_start,
                    prev_period_end=prev_end,
                    current_end_date=end_date
                )
            else:
                # No prev_period_start_date provided - calculate based on actual period duration
                # Previous period should be the same duration as current period, ending at current period start
                period_duration_days = (end_date - start_date).days
                
                if period_duration_days > 0:
                    from api.v1.period_analysis import (
                        calculate_previous_period_stocks_most_bought,
                        calculate_previous_period_stocks_sold_analysis
                    )
                    from datetime import timedelta
                    
                    # Previous period: same duration as current period, ending at current period start
                    prev_end = start_date
                    prev_start = start_date - timedelta(days=period_duration_days)
                    
                    logger.info(f"Calculating previous period based on period duration ({period_duration_days} days): {prev_start} to {prev_end}")
                    
                    prev_duration_days = (prev_end - prev_start).days
                    response_data['previous_review_period'] = {
                        'start_date': prev_start.isoformat(),
                        'end_date': prev_end.isoformat(),
                        'duration_days': prev_duration_days,
                        'duration_months': round(prev_duration_days / 30.44, 1)
                    }
                    
                    response_data['previous_review_period_stocks_most_bought'] = calculate_previous_period_stocks_most_bought(
                        client_id=client_id,
                        prev_period_start=prev_start,
                        prev_period_end=prev_end,
                        current_end_date=end_date,
                        current_end_map=data_collector.end_map
                    )
                    
                    response_data['previous_review_period_stocks_sold'] = calculate_previous_period_stocks_sold_analysis(
                        client_id=client_id,
                        prev_period_start=prev_start,
                        prev_period_end=prev_end,
                        current_end_date=end_date
                    )
        except Exception as e:
            logger.warning(f"Could not compute previous review period stock activity: {str(e)}", exc_info=True)
        
        logger.info(f"Period analysis V2 completed successfully for client {client_id}")
        
        # Log MTM data before serialization for debugging
        mtm_data = response_data.get('mark_to_market_gains', {})
        logger.info(f"Response MTM data before serialization - Period: {start_date} to {end_date}, total_start_value: {mtm_data.get('total_start_value')}, total_end_value: {mtm_data.get('total_end_value')}, reconstruction_source_start: {mtm_data.get('reconstruction_source_start')}")
        
        # Verify the period matches
        if mtm_data.get('total_start_value') == 0 and start_date != date(2022, 4, 7):
            logger.warning(f"⚠️ WARNING: MTM start_value is 0 for period {start_date} to {end_date}, but this is NOT the first transaction date. This may indicate wrong period's data is being used!")
        
        # Ensure all dates are serialized to strings for JSON compatibility
        try:
            response_data = convert_dates_to_strings(response_data)
            logger.debug("Date serialization completed successfully")
            
            # Log MTM data after serialization for debugging
            mtm_data_after = response_data.get('mark_to_market_gains', {})
            logger.info(f"Response MTM data after serialization - total_start_value: {mtm_data_after.get('total_start_value')}, total_end_value: {mtm_data_after.get('total_end_value')}")
        except Exception as serialize_error:
            logger.warning(f"Error during date serialization (non-fatal): {str(serialize_error)}", exc_info=True)
            # Continue anyway - Flask's jsonify should handle basic date serialization
        
        # Try to serialize response to catch any JSON errors before returning
        try:
            import json
            # Test serialization
            json.dumps(response_data, default=str)
            logger.debug("Response data JSON serialization test passed")
        except (TypeError, ValueError) as json_error:
            logger.error(f"JSON serialization error detected: {str(json_error)}", exc_info=True)
            # Try to fix common issues
            try:
                response_data = convert_dates_to_strings(response_data)
                json.dumps(response_data, default=str)
                logger.info("Fixed JSON serialization issue")
            except Exception as fix_error:
                logger.error(f"Could not fix JSON serialization: {str(fix_error)}")
                raise ValueError(f"Response data contains non-serializable objects: {str(json_error)}")
        
        response_payload = {
            "success": True,
            "message": "Success",
            "data": response_data,
            "timestamp": datetime.utcnow().isoformat(),
            "version": "v2"
        }
        return jsonify(response_payload), 200
        
    except ValidationError as e:
        logger.warning(f"Validation error in period analysis V2 for client {client_id}: {str(e)}")
        return APIResponse.error(str(e), status_code=400)
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        error_type = type(e).__name__
        error_msg = str(e)
        logger.error(f"Error in period analysis V2 for client {client_id}: {error_type}: {error_msg}")
        logger.error(f"Full traceback:\n{error_trace}")
        # Return detailed error for debugging (can be removed in production)
        return APIResponse.error(
            f"Internal server error: {error_type}: {error_msg}",
            status_code=500,
            details={'client_id': client_id, 'error_type': error_type} if current_app.config.get('DEBUG', False) else None
        )
    finally:
        # Remove the custom handler to prevent duplicate logging in subsequent requests
        for log in loggers_to_capture:
            if warning_handler in log.handlers:
                log.removeHandler(warning_handler)
