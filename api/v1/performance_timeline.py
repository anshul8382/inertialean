"""
Performance Timeline API

Provides portfolio progression over time using the forward calculation engine
as the source of truth, with canonical cashflow-based net investment and
benchmark DCA comparison.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from flask import Blueprint, request

from api.core.response import APIResponse
from api.core.exceptions import ValidationError
from models import db, Client, Cashflow, Benchmark, BenchmarkData, Transaction
from api.v1.portfolio_reconstruction import PortfolioReconstructionService
from services.forward_holding_calculation_service import get_client_portfolio_by_date
from api.v1.performance import calculate_xirr

# Import datetime module to avoid confusion with date type
from datetime import datetime as dt_module

logger = logging.getLogger(__name__)

performance_timeline_bp = Blueprint('performance_timeline', __name__)


def _to_date(value) -> date:
    """Safely convert any date-like value to a date object."""
    if value is None:
        raise ValueError("Cannot convert None to date")
    
    # Check for date first (most common case)
    if isinstance(value, date) and not isinstance(value, dt_module):
        return value
    
    # Check for datetime and convert
    if isinstance(value, dt_module):
        return value.date()
    
    # Check if it has a date() method (like datetime objects)
    if hasattr(value, 'date') and callable(getattr(value, 'date', None)):
        try:
            result = value.date()
            # Ensure result is a date object, not datetime
            if isinstance(result, dt_module):
                result = result.date()
            if not isinstance(result, date) or isinstance(result, dt_module):
                raise ValueError(f"value.date() returned {type(result)}, expected date")
            return result
        except Exception as e:
            raise ValueError(f"Error calling date() on {value}: {e}")
    
    # Try to parse as string
    try:
        date_str = str(value).split()[0]  # Get first part (before space if datetime string)
        result = date.fromisoformat(date_str)
        if not isinstance(result, date) or isinstance(result, dt_module):
            raise ValueError(f"date.fromisoformat() returned {type(result)}, expected date")
        return result
    except Exception as e:
        raise ValueError(f"Cannot convert {value} (type: {type(value)}) to date: {e}")


def _parse_date(value: Optional[str], field: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"Invalid {field} format: {value}. Use YYYY-MM-DD")


def _month_end_dates(start: date, end: date) -> List[date]:
    """Return month-end dates between [start, end] inclusive."""
    # Ensure both are date objects, not datetime
    start = _to_date(start)
    end = _to_date(end)
    
    # Normalize start to first day of its month for iteration
    cursor = date(start.year, start.month, 1)
    out: List[date] = []

    while cursor <= end:
        # next month first day
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        month_end = next_month - timedelta(days=1)

        if month_end < start:
            cursor = next_month
            continue
        if month_end > end:
            # If end is within this month, use end as the last point
            out.append(end)
            break

        out.append(month_end)
        cursor = next_month

    # Ensure unique and sorted (end could equal month_end)
    # All items in out should be date objects now, but verify before sorting
    out_clean = []
    for d in out:
        try:
            d_clean = _to_date(d)
            if isinstance(d_clean, date) and not isinstance(d_clean, dt_module):
                out_clean.append(d_clean)
        except:
            continue
    out_clean = sorted(set(out_clean))
    return out_clean


def _build_benchmark_price_lookup(benchmark_id: int, start: date, end: date) -> Tuple[Dict[date, float], List[date]]:
    # BenchmarkData.date is db.Date column, ensure we're using date objects for comparison
    # Convert to date objects to be safe
    start_date = _to_date(start)
    end_date = _to_date(end)
    
    rows = BenchmarkData.query.filter(
        BenchmarkData.benchmark_id == benchmark_id,
        BenchmarkData.date >= (start_date - timedelta(days=10)),
        BenchmarkData.date <= end_date
    ).order_by(BenchmarkData.date).all()

    price_by_date: Dict[date, float] = {}
    dates: List[date] = []
    for r in rows:
        if r.date and r.price is not None:
            # Ensure we always store date objects, not datetime
            # BenchmarkData.date is db.Date, but SQLAlchemy might return datetime
            d_raw = r.date
            if isinstance(d_raw, dt_module):
                d = d_raw.date()
            elif isinstance(d_raw, date):
                d = d_raw
            else:
                # Try to extract date if it has a date() method
                try:
                    d = d_raw.date() if hasattr(d_raw, 'date') and callable(getattr(d_raw, 'date', None)) else date.fromisoformat(str(d_raw).split()[0])
                except:
                    logger.warning(f"Could not convert BenchmarkData.date to date: {d_raw} (type: {type(d_raw)})")
                    continue
            
            # Final verification: d must be a date object
            if not isinstance(d, date):
                logger.warning(f"BenchmarkData.date conversion failed, got {type(d)} instead of date")
                continue
                
            price_by_date[d] = float(r.price)
            dates.append(d)
    
    # Sort dates to ensure they're in order
    # Convert all dates to date objects before sorting to avoid comparison errors
    dates_clean = []
    for d in dates:
        try:
            d_clean = _to_date(d)
            if isinstance(d_clean, date):
                dates_clean.append(d_clean)
        except:
            continue
    dates_clean.sort()
    return price_by_date, dates_clean


def _benchmark_price_on_or_before(target: date, price_by_date: Dict[date, float], available_dates: List[date]) -> Optional[float]:
    """Get benchmark price at target date or closest prior date."""
    try:
        # Convert target to date object using helper
        target = _to_date(target)
        # Verify it's actually a date object
        if not isinstance(target, date):
            logger.warning(f"Failed to convert target to date: {target} is {type(target)}")
            return None
    except Exception as e:
        logger.warning(f"Error converting target to date: {e}")
        return None
    
    # Check direct match - convert all keys to date objects for comparison
    # Don't use 'in' operator directly as keys might be datetime objects
    for key in price_by_date:
        try:
            key_date = _to_date(key)
            if isinstance(key_date, date) and key_date == target:
                return price_by_date[key]
        except:
            continue
    
    # available_dates is sorted - find closest prior date
    # simple reverse scan from end for small monthly sets; for large series consider bisect
    for d in reversed(available_dates):
        try:
            # Convert d to date object using helper
            d_compare = _to_date(d)
            
            # Verify both are date objects before comparison
            if not isinstance(d_compare, date):
                logger.warning(f"d_compare is not a date: {type(d_compare)} = {d_compare}")
                continue
            if not isinstance(target, date):
                logger.warning(f"target is not a date: {type(target)} = {target}")
                break
            
            # Force both to be pure date objects - no datetime allowed
            # Double-check and convert if needed
            if isinstance(d_compare, dt_module):
                d_compare = d_compare.date()
            if isinstance(target, dt_module):
                target = target.date()
            
            # Final type check
            if not isinstance(d_compare, date) or isinstance(d_compare, dt_module):
                logger.warning(f"d_compare type issue: {type(d_compare)}")
                continue
            if not isinstance(target, date) or isinstance(target, dt_module):
                logger.warning(f"target type issue: {type(target)}")
                break
            
            # Compare using date.toordinal() to avoid any type issues
            try:
                d_ordinal = d_compare.toordinal() if isinstance(d_compare, date) else None
                target_ordinal = target.toordinal() if isinstance(target, date) else None
                if d_ordinal is None or target_ordinal is None:
                    continue
                comparison_result = d_ordinal <= target_ordinal
            except (TypeError, AttributeError) as te:
                logger.error(f"Error comparing dates using toordinal: d_compare={d_compare} (type: {type(d_compare)}), target={target} (type: {type(target)}), error: {te}")
                continue
            
            if comparison_result:
                # Try to get price - check all keys
                price = price_by_date.get(d_compare)
                if price is not None:
                    return price
                # Try original key as fallback
                price = price_by_date.get(d)
                if price is not None:
                    return price
                # Last resort: find any key that matches the date
                for key in price_by_date:
                    try:
                        key_date = _to_date(key)
                        if isinstance(key_date, date) and key_date == d_compare:
                            return price_by_date[key]
                    except:
                        continue
        except Exception as e:
            # Skip invalid dates and log the error
            logger.warning(f"Error processing date {d} in available_dates: {e}")
            continue
    return None


@performance_timeline_bp.route('/<int:client_id>/performance/timeline/debug/portfolio', methods=['GET'])
def get_portfolio_data_debug(client_id: int):
    """Debug endpoint: Show portfolio reconstruction data for each month-end."""
    try:
        client = Client.query.get_or_404(client_id)
        end_date_raw = _parse_date(request.args.get('end_date'), 'end_date') or date.today()
        start_date_raw = _parse_date(request.args.get('start_date'), 'start_date') or (end_date_raw - timedelta(days=365))
        end_date = _to_date(end_date_raw)
        start_date = _to_date(start_date_raw)
        
        points_dates = _month_end_dates(start_date, end_date)
        portfolio_data = []
        
        from services.forward_holding_calculation_service import get_client_portfolio_by_date
        from datetime import datetime as dt
        
        for d in points_dates:
            try:
                target_date = _to_date(d)
                
                # Use forward calculation service directly
                portfolio = get_client_portfolio_by_date(client_id, target_date)
                
                # Compute cashflows
                target_datetime = dt.combine(target_date, dt.max.time()) if isinstance(target_date, date) else target_date
                cashflows_to_date = Cashflow.query.filter(
                    Cashflow.client_id == client_id,
                    Cashflow.date <= target_datetime
                ).all()
                
                total_invested = sum(abs(float(cf.amount)) for cf in cashflows_to_date if float(cf.amount) < 0)
                total_withdrawn = sum(float(cf.amount) for cf in cashflows_to_date if float(cf.amount) > 0)
                net_investment = total_invested - total_withdrawn
                
                portfolio_data.append({
                    'date': target_date.isoformat(),
                    'state': {
                        'total_value': float(portfolio.get('total_value', 0.0) or 0.0),
                        'total_cost': float(portfolio.get('total_cost', 0.0) or 0.0),
                        'net_investment': net_investment,
                        'total_invested': total_invested,
                        'total_withdrawn': total_withdrawn,
                        'source': 'forward_calculation',
                        'holdings_count': len(portfolio.get('holdings', []))
                    }
                })
            except Exception as e:
                import traceback
                portfolio_data.append({
                    'date': d.isoformat() if hasattr(d, 'isoformat') else str(d),
                    'error': str(e),
                    'error_type': type(e).__name__,
                    'traceback': traceback.format_exc().split('\n')[-5:]
                })
        
        return APIResponse.success(
            data={
                'client_id': client_id,
                'client_name': client.name,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'portfolio_data': portfolio_data
            },
            message="Portfolio reconstruction data for debugging"
        )
    except Exception as e:
        logger.error(f"Error in portfolio debug endpoint: {e}", exc_info=True)
        return APIResponse.error(
            message=str(e),
            status_code=500,
            error_code="INTERNAL_ERROR"
        )


@performance_timeline_bp.route('/<int:client_id>/performance/timeline/debug/benchmark', methods=['GET'])
def get_benchmark_data_debug(client_id: int):
    """Debug endpoint: Show benchmark DCA simulation data."""
    try:
        client = Client.query.get_or_404(client_id)
        end_date_raw = _parse_date(request.args.get('end_date'), 'end_date') or date.today()
        start_date_raw = _parse_date(request.args.get('start_date'), 'start_date') or (end_date_raw - timedelta(days=365))
        end_date = _to_date(end_date_raw)
        start_date = _to_date(start_date_raw)
        benchmark_id = request.args.get('benchmark_id', 1, type=int)
        benchmark = Benchmark.query.get(benchmark_id)
        if not benchmark:
            raise ValidationError(f"Benchmark {benchmark_id} not found")
        
        # Preload cashflows
        from datetime import datetime as dt
        end_datetime_for_query = dt.combine(end_date, dt.max.time()) if isinstance(end_date, date) else end_date
        cashflows = Cashflow.query.filter(
            Cashflow.client_id == client_id,
            Cashflow.date <= end_datetime_for_query
        ).order_by(Cashflow.date).all()
        
        # Preload benchmark prices
        price_by_date, available_dates = _build_benchmark_price_lookup(benchmark_id, start_date, end_date)
        
        points_dates = _month_end_dates(start_date, end_date)
        benchmark_data = []
        
        for d in points_dates:
            try:
                target_date = _to_date(d)
                d_as_date = target_date
                
                # Simulate DCA
                benchmark_units = 0.0
                cashflow_events = []
                
                for cf in cashflows:
                    try:
                        cf_date_raw = cf.date
                        if isinstance(cf_date_raw, dt_module):
                            cf_date = cf_date_raw.date()
                        elif isinstance(cf_date_raw, date):
                            cf_date = cf_date_raw
                        else:
                            cf_date = _to_date(cf_date_raw)
                        
                        # Compare using ordinals
                        if isinstance(cf_date, date) and isinstance(d_as_date, date):
                            if cf_date.toordinal() > d_as_date.toordinal():
                                break
                        else:
                            continue
                        
                        amount = float(cf.amount)
                        px = _benchmark_price_on_or_before(cf_date, price_by_date, available_dates)
                        
                        if px and px > 0:
                            if amount < 0:  # investment
                                units_bought = abs(amount) / px
                                benchmark_units += units_bought
                                cashflow_events.append({
                                    'date': cf_date.isoformat(),
                                    'amount': amount,
                                    'price': px,
                                    'units_bought': units_bought,
                                    'total_units_after': benchmark_units
                                })
                            elif amount > 0:  # withdrawal
                                # Cap units_to_sell to available units (fix for 14 crore bug)
                                units_to_sell = min(amount / px, benchmark_units)
                                benchmark_units = max(0.0, benchmark_units - units_to_sell)
                                cashflow_events.append({
                                    'date': cf_date.isoformat(),
                                    'amount': amount,
                                    'price': px,
                                    'units_sold': units_to_sell,
                                    'total_units_after': benchmark_units
                                })
                    except Exception as cf_err:
                        continue
                
                px_d = _benchmark_price_on_or_before(d_as_date, price_by_date, available_dates)
                benchmark_value = (benchmark_units * px_d) if px_d else 0.0
                
                benchmark_data.append({
                    'date': target_date.isoformat(),
                    'benchmark_price': px_d,
                    'benchmark_units': benchmark_units,
                    'benchmark_value': float(benchmark_value),
                    'cashflow_events_count': len(cashflow_events),
                    'cashflow_events': cashflow_events[:5]  # First 5 events
                })
            except Exception as e:
                benchmark_data.append({
                    'date': d.isoformat() if hasattr(d, 'isoformat') else str(d),
                    'error': str(e),
                    'error_type': type(e).__name__
                })
        
        return APIResponse.success(
            data={
                'client_id': client_id,
                'benchmark_id': benchmark_id,
                'benchmark_name': benchmark.name,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'benchmark_data': benchmark_data,
                'benchmark_prices_available': len(price_by_date),
                'available_dates_count': len(available_dates)
            },
            message="Benchmark DCA simulation data for debugging"
        )
    except Exception as e:
        logger.error(f"Error in benchmark debug endpoint: {e}", exc_info=True)
        return APIResponse.error(
            message=str(e),
            status_code=500,
            error_code="INTERNAL_ERROR"
        )


@performance_timeline_bp.route('/<int:client_id>/performance/benchmark-xirr-equity', methods=['GET'])
def get_benchmark_equity_xirr(client_id: int):
    """
    Benchmark XIRR using EQUITY-only cashflows derived from trade data (transactions).

    This is meant for like-to-like comparison for Equity asset class:
      - Cashflows come from EQUITY BUY/SELL transactions (BUY negative, SELL positive).
      - Start value: equity portfolio value at start_date is treated as initial investment (negative).
      - Terminal value: benchmark_end_value at end_date (derived from benchmark unit simulation) is added as a positive cashflow.

    Query params:
      - start_date: YYYY-MM-DD (required)
      - end_date: YYYY-MM-DD (required)
      - benchmark_id: int (optional; default=1)
    """
    try:
        Client.query.get_or_404(client_id)

        start_date = _parse_date(request.args.get('start_date'), 'start_date')
        end_date = _parse_date(request.args.get('end_date'), 'end_date')
        if not start_date or not end_date:
            raise ValidationError("start_date and end_date are required")

        start_date = _to_date(start_date)
        end_date = _to_date(end_date)
        if start_date > end_date:
            raise ValidationError("start_date must be before end_date")

        benchmark_id = request.args.get('benchmark_id', 1, type=int)
        benchmark = Benchmark.query.get(benchmark_id)
        if not benchmark:
            raise ValidationError(f"Benchmark {benchmark_id} not found")

        # Equity-only portfolio start value
        start_portfolio = get_client_portfolio_by_date(client_id, start_date)
        def is_equity(h):
            return (h.get('asset_class') or '').strip().upper() == 'EQUITY'
        equity_start_value = sum(
            float(h.get('current_value', 0) or 0)
            for h in (start_portfolio.get('holdings') or [])
            if is_equity(h)
        )

        # Equity-only trade cashflows in the window
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        txns = (
            Transaction.query
            .filter(
                Transaction.client_id == client_id,
                Transaction.transaction_date >= start_dt,
                Transaction.transaction_date <= end_dt,
                Transaction.type.in_(['BUY', 'SELL'])
            )
            .order_by(Transaction.transaction_date.asc(), Transaction.id.asc())
            .all()
        )

        equity_trade_cashflows = []  # list of (date, signed_amount)
        equity_trade_invested = 0.0
        equity_trade_withdrawn = 0.0
        for t in txns:
            if not t.security or not getattr(t.security, 'asset_class', None):
                continue
            ac_name = (t.security.asset_class.name or '').strip().upper()
            if ac_name != 'EQUITY':
                continue

            amt = float(t.amount) if getattr(t, 'amount', None) is not None else float(t.quantity) * float(t.price)
            signed_amt = -amt if t.type == 'BUY' else amt
            equity_trade_cashflows.append((t.transaction_date, signed_amt))
            if signed_amt < 0:
                equity_trade_invested += abs(signed_amt)
            else:
                equity_trade_withdrawn += signed_amt

        # Load benchmark prices lookup once
        price_by_date, available_dates = _build_benchmark_price_lookup(benchmark_id, start_date, end_date)

        # Simulate benchmark units
        benchmark_units = 0.0

        # Initialize with start value invested on start_date
        start_px = _benchmark_price_on_or_before(start_date, price_by_date, available_dates)
        if equity_start_value > 0 and start_px and start_px > 0:
            benchmark_units += float(equity_start_value) / float(start_px)

        # Apply trade cashflows
        for cf_dt, amount in equity_trade_cashflows:
            cf_date = cf_dt.date() if hasattr(cf_dt, 'date') else _to_date(cf_dt)
            px = _benchmark_price_on_or_before(cf_date, price_by_date, available_dates)
            if not px or px <= 0:
                continue
            if amount < 0:
                benchmark_units += abs(float(amount)) / float(px)
            elif amount > 0:
                units_to_sell = min(float(amount) / float(px), benchmark_units)
                benchmark_units = max(0.0, benchmark_units - units_to_sell)

        end_px = _benchmark_price_on_or_before(end_date, price_by_date, available_dates)
        benchmark_end_value = (benchmark_units * end_px) if end_px else 0.0

        # Build XIRR cashflows (strictly [start_date, end_date])
        xirr_cashflows = []
        if equity_start_value > 0:
            xirr_cashflows.append((start_dt, -float(equity_start_value)))
        xirr_cashflows.extend([(d, float(a)) for d, a in equity_trade_cashflows])
        if benchmark_end_value > 0:
            xirr_cashflows.append((end_dt, float(benchmark_end_value)))

        xirr, total_invested, total_withdrawn, net_investment, absolute_return = calculate_xirr(xirr_cashflows, 0)

        return APIResponse.success(
            data={
                'client_id': client_id,
                'benchmark_id': benchmark_id,
                'benchmark_name': benchmark.name,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'xirr': xirr,
                'xirr_percent': xirr * 100,
                'benchmark_units': benchmark_units,
                'benchmark_end_price': end_px,
                'benchmark_end_value': float(benchmark_end_value),
                'equity_start_value': float(equity_start_value),
                'equity_trade_invested': float(equity_trade_invested),
                'equity_trade_withdrawn': float(equity_trade_withdrawn),
                'total_invested': float(total_invested) if not isinstance(total_invested, float) else total_invested,
                'total_withdrawn': float(total_withdrawn) if not isinstance(total_withdrawn, float) else total_withdrawn,
                'net_investment': float(net_investment) if not isinstance(net_investment, float) else net_investment,
                'absolute_return': float(absolute_return) if not isinstance(absolute_return, float) else absolute_return,
                'scope': 'equity_only_from_transactions'
            },
            message="Benchmark XIRR using equity-only trade cashflows"
        )

    except ValidationError as e:
        # User input / request validation errors should be 400s, not 500s
        return APIResponse.error(str(e), status_code=400)
    except Exception as e:
        logger.error(f"Error calculating equity benchmark XIRR for client {client_id}: {str(e)}", exc_info=True)
        return APIResponse.error("Internal server error", status_code=500)


@performance_timeline_bp.route('/<int:client_id>/performance/timeline', methods=['GET'])
def get_client_performance_timeline(client_id: int):
    """
    Get portfolio progression over time.

    Query params:
      - start_date: YYYY-MM-DD (optional; default = end_date - 365 days)
      - end_date: YYYY-MM-DD (optional; default = today)
      - frequency: 'monthly' (default) or 'daily' (not implemented yet)
      - benchmark_id: int (optional; default = 1)

    Returns:
      points: [
        {
          date,
          portfolio_value,
          portfolio_cost,
          net_investment,
          gains,
          benchmark_value,
          benchmark_gains,
        }, ...
      ]
    """
    try:
        client = Client.query.get_or_404(client_id)

        end_date_raw = _parse_date(request.args.get('end_date'), 'end_date') or date.today()
        start_date_raw = _parse_date(request.args.get('start_date'), 'start_date') or (end_date_raw - timedelta(days=365))
        
        # Ensure both are date objects (not datetime)
        end_date = _to_date(end_date_raw)
        start_date = _to_date(start_date_raw)
        
        if start_date > end_date:
            raise ValidationError("start_date must be before end_date")

        frequency = (request.args.get('frequency') or 'monthly').lower()
        if frequency not in ('monthly',):
            raise ValidationError("Only frequency=monthly is supported right now")

        benchmark_id = request.args.get('benchmark_id', 1, type=int)
        benchmark = Benchmark.query.get(benchmark_id)
        if not benchmark:
            # Keep it strict: benchmarking requires valid benchmark series
            raise ValidationError(f"Benchmark {benchmark_id} not found")

        # Generate timeline dates
        points_dates = _month_end_dates(start_date, end_date)
        if not points_dates:
            return APIResponse.success(
                data={
                    'client_id': client_id,
                    'benchmark_id': benchmark_id,
                    'benchmark_name': benchmark.name,
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'frequency': frequency,
                    'points': [],
                },
                message="No dates in requested range"
            )

        # Preload cashflows for benchmark simulation
        # Cashflow.date is DateTime column, convert end_date to datetime for query
        from datetime import datetime as dt
        end_datetime_for_query = dt.combine(end_date, dt.max.time()) if isinstance(end_date, date) else end_date
        cashflows = Cashflow.query.filter(
            Cashflow.client_id == client_id,
            Cashflow.date <= end_datetime_for_query
        ).order_by(Cashflow.date).all()
        
        # Find initial portfolio value (first investment/inflow)
        initial_portfolio_value = 0.0
        if cashflows:
            # Get first cashflow that's an investment (negative amount = inflow)
            first_investment = next((cf for cf in cashflows if float(cf.amount) < 0), None)
            if first_investment:
                initial_portfolio_value = abs(float(first_investment.amount))

        # Preload benchmark data
        price_by_date, available_dates = _build_benchmark_price_lookup(benchmark_id, start_date, end_date)
        if not available_dates:
            raise ValidationError(f"No benchmark data available for {benchmark.name} in selected range")

        # Simulate benchmark DCA with client's cashflows
        benchmark_units = 0.0
        benchmark_units_by_date = {}  # Track units at each date for accurate simulation
        
        # Need to recalculate benchmark units at each date point
        # because withdrawals affect units available
        
        # Check if debug mode is requested
        debug_mode = request.args.get('debug', 'false').lower() == 'true'
        
        # Build points
        points = []
        portfolio_debug_data = [] if debug_mode else None
        benchmark_debug_data = [] if debug_mode else None
        
        for d in points_dates:
            # Initialize variables
            portfolio_value = 0.0
            portfolio_cost = 0.0
            net_investment = 0.0
            gains = 0.0
            total_invested = 0.0
            total_withdrawn = 0.0
            target_date = None
            benchmark_value = 0.0
            benchmark_gains = 0.0
            
            # STEP 1: Portfolio calculation (independent try block)
            try:
                target_date = _to_date(d)
                
                # Call portfolio reconstruction (forward calculation - user confirmed this works)
                from services.forward_holding_calculation_service import get_client_portfolio_by_date
                portfolio = get_client_portfolio_by_date(client_id, target_date)
                
                # Net investment to this date (preloaded `cashflows` already scoped to end_date)
                from datetime import datetime as dt
                target_datetime = dt.combine(target_date, dt.max.time()) if isinstance(target_date, date) else target_date
                cashflows_to_date = [cf for cf in cashflows if cf.date <= target_datetime]

                total_invested = sum(abs(float(cf.amount)) for cf in cashflows_to_date if float(cf.amount) < 0)
                total_withdrawn = sum(float(cf.amount) for cf in cashflows_to_date if float(cf.amount) > 0)
                net_investment = total_invested - total_withdrawn
                
                portfolio_value = float(portfolio.get('total_value', 0.0) or 0.0)
                portfolio_cost = float(portfolio.get('total_cost', 0.0) or 0.0)
                gains = portfolio_value - net_investment
                
                # Save portfolio debug data IMMEDIATELY
                if debug_mode:
                    portfolio_debug_data.append({
                        'date': target_date.isoformat(),
                        'total_value': portfolio_value,
                        'total_cost': portfolio_cost,
                        'net_investment': net_investment,
                        'total_invested': total_invested,
                        'total_withdrawn': total_withdrawn,
                        'gains': gains,
                        'holdings_count': len(portfolio.get('holdings', []))
                    })
            except Exception as portfolio_err:
                logger.error(f"Portfolio calculation error for date {d}: {portfolio_err}", exc_info=True)
                if debug_mode:
                    portfolio_debug_data.append({
                        'date': str(d),
                        'error': str(portfolio_err),
                        'error_type': type(portfolio_err).__name__
                    })
            
            # STEP 2: Benchmark calculation (separate try block - errors here won't affect portfolio data)
            try:
                if target_date is None:
                    target_date = _to_date(d)
                
                d_as_date = target_date
                
                # ============================================================================
                # BENCHMARK SIMULATION LOGIC:
                # 1. Start with portfolio's initial value (on first date)
                # 2. Convert to benchmark units: initial_units = portfolio_value / benchmark_price
                # 3. For subsequent cashflows, adjust units based on inflows/outflows
                # ============================================================================
                
                # Check if this is the first date point - initialize benchmark units
                is_first_date = (d == points_dates[0])
                benchmark_units = 0.0
                
                if is_first_date:
                    # Get portfolio value on first date
                    first_date_portfolio_value = portfolio_value
                    
                    # Get benchmark price on first date
                    try:
                        first_date_benchmark_price = _benchmark_price_on_or_before(d_as_date, price_by_date, available_dates)
                    except Exception as e:
                        logger.warning(f"Error getting benchmark price for first date {d_as_date}: {e}")
                        first_date_benchmark_price = None
                    
                    # Initialize benchmark units: portfolio_start_value / benchmark_price_on_start_date
                    if first_date_portfolio_value > 0 and first_date_benchmark_price and first_date_benchmark_price > 0:
                        benchmark_units = first_date_portfolio_value / first_date_benchmark_price
                        if debug_mode:
                            logger.info(f"Initialized benchmark: portfolio_value={first_date_portfolio_value}, "
                                      f"benchmark_price={first_date_benchmark_price}, "
                                      f"initial_units={benchmark_units}")
                    else:
                        # Fallback: if we can't get portfolio value or benchmark price, start from 0
                        benchmark_units = 0.0
                        logger.warning(f"Could not initialize benchmark units: portfolio_value={first_date_portfolio_value}, "
                                    f"benchmark_price={first_date_benchmark_price}")
                else:
                    # For subsequent dates, we need to recalculate from the first date
                    # Get portfolio value on first date
                    first_date = points_dates[0]
                    try:
                        from services.forward_holding_calculation_service import get_client_portfolio_by_date
                        first_date_portfolio = get_client_portfolio_by_date(client_id, first_date)
                        first_date_portfolio_value = float(first_date_portfolio.get('total_value', 0.0) or 0.0)
                        
                        # Get benchmark price on first date
                        first_date_benchmark_price = _benchmark_price_on_or_before(first_date, price_by_date, available_dates)
                        
                        # Initialize benchmark units from first date
                        if first_date_portfolio_value > 0 and first_date_benchmark_price and first_date_benchmark_price > 0:
                            benchmark_units = first_date_portfolio_value / first_date_benchmark_price
                        else:
                            benchmark_units = 0.0
                    except Exception as e:
                        logger.warning(f"Error initializing benchmark units from first date: {e}")
                        benchmark_units = 0.0
                
                benchmark_cashflow_events = [] if debug_mode else None
                
                # Get first date for comparison - skip cashflows on or before first date
                # (they're already reflected in the initial portfolio value)
                first_date = points_dates[0]
                first_date_ord = first_date.toordinal() if isinstance(first_date, date) else _to_date(first_date).toordinal()
                
                # Process cashflows to adjust benchmark units (DCA simulation)
                # Skip cashflows on or before the first date (already in initial value)
                for cf in cashflows:
                    try:
                        # Convert cashflow date to date object using helper
                        cf_date_raw = cf.date
                        # Ensure it's converted to date object
                        if isinstance(cf_date_raw, dt_module):
                            cf_date = cf_date_raw.date()
                        elif isinstance(cf_date_raw, date):
                            cf_date = cf_date_raw
                        else:
                            cf_date = _to_date(cf_date_raw)
                        
                        # Explicit type check before comparison to catch issues early
                        if not isinstance(cf_date, date) or isinstance(cf_date, dt_module):
                            if isinstance(cf_date, dt_module):
                                cf_date = cf_date.date()
                            else:
                                logger.error(f"cf_date is not a date object: {type(cf_date)} = {cf_date}")
                                continue
                        
                        # Ensure d_as_date is also a date object
                        if not isinstance(d_as_date, date) or isinstance(d_as_date, dt_module):
                            if isinstance(d_as_date, dt_module):
                                d_as_date = d_as_date.date()
                            else:
                                logger.error(f"d_as_date is not a date object: {type(d_as_date)} = {d_as_date}")
                                break
                        
                        # Skip cashflows on or before the first date (already reflected in initial portfolio value)
                        try:
                            if not isinstance(cf_date, date):
                                continue
                            cf_ord = cf_date.toordinal()
                            if cf_ord <= first_date_ord:
                                # This cashflow is on or before first date, skip it
                                continue
                        except (TypeError, AttributeError) as cmp_err:
                            logger.warning(f"Error comparing cashflow date to first date: {cmp_err}")
                            continue
                        
                        # Compare using toordinal to avoid any datetime/date comparison issues
                        try:
                            if not isinstance(cf_date, date):
                                continue
                            if not isinstance(d_as_date, date):
                                break
                            cf_ord = cf_date.toordinal()
                            d_ord = d_as_date.toordinal()
                            if cf_ord > d_ord:
                                break
                        except (TypeError, AttributeError) as cmp_err:
                            logger.warning(f"Error comparing dates using toordinal: cf_date={cf_date} (type: {type(cf_date)}), d_as_date={d_as_date} (type: {type(d_as_date)}), error: {cmp_err}")
                            continue
                        
                        amount = float(cf.amount)
                        try:
                            px = _benchmark_price_on_or_before(cf_date, price_by_date, available_dates)
                        except (TypeError, ValueError) as te:
                            # Catch date comparison errors specifically
                            if "can't compare" in str(te) or "compare datetime" in str(te).lower():
                                logger.warning(f"Date comparison error in _benchmark_price_on_or_before for cf_date={cf_date} (type: {type(cf_date)}): {te}")
                            continue
                        if not px or px <= 0:
                            continue
                        if amount < 0:  # investment (inflow)
                            units_bought = abs(amount) / px
                            benchmark_units += units_bought
                            if debug_mode:
                                benchmark_cashflow_events.append({
                                    'date': cf_date.isoformat(),
                                    'amount': amount,
                                    'price': px,
                                    'units_bought': units_bought,
                                    'total_units_after': benchmark_units
                                })
                        elif amount > 0:  # withdrawal
                            # Cap units_to_sell to available units (fix for 14 crore bug)
                            units_to_sell = min(amount / px, benchmark_units)
                            benchmark_units = max(0.0, benchmark_units - units_to_sell)
                            if debug_mode:
                                benchmark_cashflow_events.append({
                                    'date': cf_date.isoformat(),
                                    'amount': amount,
                                    'price': px,
                                    'units_sold': units_to_sell,
                                    'total_units_after': benchmark_units
                                })
                    except (TypeError, ValueError) as te:
                        # Catch type comparison and conversion errors
                        if "can't compare" in str(te) or "compare datetime" in str(te).lower():
                            logger.warning(f"Date comparison error processing cashflow for client {client_id}, date {d_as_date}, cf.date={cf.date} (type: {type(cf.date)}), error: {te}")
                        continue
                    except Exception as cf_err:
                        # Skip this cashflow if processing fails
                        logger.warning(f"Error processing cashflow for client {client_id}, date {d}: {cf_err}")
                        continue

                # Get benchmark price for this date
                try:
                    px_d = _benchmark_price_on_or_before(d_as_date, price_by_date, available_dates)
                except TypeError as te:
                    # Catch date comparison errors
                    logger.error(f"TypeError getting benchmark price for d_as_date={d_as_date} (type: {type(d_as_date)}): {te}", exc_info=True)
                    px_d = None
                benchmark_value = (benchmark_units * px_d) if px_d else 0.0
                benchmark_gains = benchmark_value - net_investment
                
                # Save benchmark debug data
                if debug_mode:
                    benchmark_debug_data.append({
                        'date': target_date.isoformat(),
                        'benchmark_price': px_d,
                        'benchmark_units': benchmark_units,
                        'benchmark_value': float(benchmark_value),
                        'cashflow_events_count': len(benchmark_cashflow_events) if benchmark_cashflow_events else 0,
                        'cashflow_events': (benchmark_cashflow_events[:5] if benchmark_cashflow_events else [])
                    })
            except Exception as benchmark_err:
                logger.error(f"Benchmark calculation error for date {target_date or d}: {benchmark_err}", exc_info=True)
                if debug_mode:
                    benchmark_debug_data.append({
                        'date': target_date.isoformat() if target_date else str(d),
                        'error': str(benchmark_err),
                        'error_type': type(benchmark_err).__name__
                    })
                benchmark_value = 0.0
                benchmark_gains = 0.0
            
            # Add point to results (regardless of errors in individual steps)
            try:
                points.append({
                    'date': (target_date or _to_date(d)).isoformat(),
                    'portfolio_value': portfolio_value,
                    'portfolio_cost': portfolio_cost,
                    'net_investment': net_investment,
                    'gains': gains,
                    'benchmark_value': float(benchmark_value),
                    'benchmark_gains': float(benchmark_gains),
                })
            except Exception as point_err:
                logger.error(f"Error creating point for date {d}: {point_err}")
                points.append({
                    'date': str(d),
                    'portfolio_value': 0.0,
                    'portfolio_cost': 0.0,
                    'net_investment': 0.0,
                    'gains': 0.0,
                    'benchmark_value': 0.0,
                    'benchmark_gains': 0.0,
                    'error': str(point_err)
                })

        # Find initial portfolio value from first point for normalization
        initial_portfolio_value = 0.0
        if points:
            first_point = points[0]
            initial_portfolio_value = float(first_point.get('portfolio_value', 0.0) or 0.0)
            # If first point portfolio value is 0, try to get it from first cashflow
            if initial_portfolio_value == 0.0 and cashflows:
                first_investment = next((cf for cf in cashflows if float(cf.amount) < 0), None)
                if first_investment:
                    initial_portfolio_value = abs(float(first_investment.amount))
        
        response_data = {
            'client_id': client_id,
            'client_name': client.name,
            'benchmark_id': benchmark_id,
            'benchmark_name': benchmark.name,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'frequency': frequency,
            'points': points,
            'initial_portfolio_value': initial_portfolio_value,  # For frontend normalization
        }
        
        # Add debug data if requested
        if debug_mode:
            response_data['debug'] = {
                'portfolio_data': portfolio_debug_data or [],
                'benchmark_data': benchmark_debug_data or []
            }
            logger.info(f"Debug mode: portfolio_data entries: {len(portfolio_debug_data or [])}, benchmark_data entries: {len(benchmark_debug_data or [])}")
        
        return APIResponse.success(
            data=response_data,
            message="Performance timeline generated successfully"
        )
    except ValidationError as e:
        logger.error(f"Validation error in performance timeline: {str(e)}")
        return APIResponse.error(
            message=str(e),
            status_code=400,
            error_code="VALIDATION_ERROR"
        )
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logger.error(f"Unexpected error in performance timeline for client {client_id}: {type(e).__name__}: {str(e)}\n{error_traceback}", exc_info=True)
        from flask import current_app
        error_details = {
            "exception_type": type(e).__name__,
            "error_message": str(e)
        }
        # Include traceback in debug/development mode
        if current_app.debug or current_app.config.get('ENV') == 'development':
            error_details["traceback"] = error_traceback.split('\n')
        return APIResponse.error(
            message=f"An error occurred while generating performance timeline: {str(e)}",
            status_code=500,
            error_code="INTERNAL_ERROR",
            details=error_details
        )

