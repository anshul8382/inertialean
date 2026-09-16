"""
Historical Price Upload Routes
Handles uploading historical price data for stocks
"""

from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for, session
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import pandas as pd
import os
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from extensions import db
from models import Security, Transaction, HistoricalPrice, BenchmarkData
from sqlalchemy import func, distinct
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

historical_prices_bp = Blueprint('historical_prices', __name__)

MIN_COVERAGE_PERCENT = 80.0
MIN_REQUIRED_DAYS = 30
# Longest contiguous calendar-date block with no HistoricalPrice strictly between bracketing dates.
# Flag when that interior is **7 or more** calendar days.
MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG = 7


def _calendar_interior_gap_segments(
    security_id: int, span_start: date, span_end: date
) -> Tuple[List[Tuple[int, date, date]], int]:
    """
    Build contiguous calendar gaps with no HistoricalPrice strictly between brackets.

    Returns:
        segments: [(interior_days, bracket_left, bracket_right), ...] interior > 0 only
        quoted_distinct_days: count of distinct saved dates in span
    """
    if span_start > span_end:
        return [], 0

    rows = (
        db.session.query(HistoricalPrice.date)
        .filter(
            HistoricalPrice.security_id == security_id,
            HistoricalPrice.date >= span_start,
            HistoricalPrice.date <= span_end,
        )
        .distinct()
        .order_by(HistoricalPrice.date.asc())
        .all()
    )
    quoted = [_normalize_date(r[0]) for r in rows if r and r[0] is not None]

    boundaries: List[date] = [span_start] + quoted + [span_end]
    slim: List[date] = []
    for d in boundaries:
        if not slim or slim[-1] != d:
            slim.append(d)
    boundaries = slim

    segments: List[Tuple[int, date, date]] = []
    for i in range(len(boundaries) - 1):
        a, b = boundaries[i], boundaries[i + 1]
        if b <= a:
            continue
        interior = max(0, (b - a).days - 1)
        if interior > 0:
            segments.append((interior, a, b))

    return segments, len(quoted)


def historical_price_calendar_gap_report(
    security_id: int,
    span_start: date,
    span_end: date,
    flag_min_interior: Optional[int] = None,
) -> dict[str, Any]:
    """Aggregate gap stats for admin/CLI (totals + max hole + upload-threshold flag)."""
    thresh = (
        flag_min_interior
        if flag_min_interior is not None
        else MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG
    )
    segments, quoted_n = _calendar_interior_gap_segments(security_id, span_start, span_end)
    total_missing = sum(t[0] for t in segments)
    worst = max(segments, key=lambda t: t[0]) if segments else None
    max_interior = worst[0] if worst else 0
    return {
        "quoted_distinct_days": quoted_n,
        "span_start": span_start.isoformat(),
        "span_end": span_end.isoformat(),
        "missing_segment_count": len(segments),
        "total_missing_calendar_days": total_missing,
        "max_block_missing_calendar_days": max_interior,
        "max_block_left": worst[1].isoformat() if worst else None,
        "max_block_right": worst[2].isoformat() if worst else None,
        "flags_upload_alert": max_interior >= thresh,
    }


def _max_interior_calendar_days_without_price(
    security_id: int, span_start: date, span_end: date
) -> tuple[int, Optional[date], Optional[date]]:
    """
    Returns (max_interior_calendar_days, left_bracket, right_bracket) inside [span_start, span_end].
    """
    segments, _ = _calendar_interior_gap_segments(security_id, span_start, span_end)
    if not segments:
        return 0, None, None
    worst = max(segments, key=lambda t: t[0])
    return worst[0], worst[1], worst[2]


def _benchmark_id_for_historical_viewer(security: Security) -> Optional[int]:
    """
    Nifty 50 (and aliases) daily series live in ``benchmark_data``, not ``historical_price``.
    When the viewer is opened for that security, read from the benchmark table instead.
    """
    from services.benchmark_service import BenchmarkService

    nifty = BenchmarkService.get_nifty_benchmark()
    if not nifty:
        return None

    sym = (security.symbol or "").strip().upper()
    bench_sym = (nifty.symbol or "").strip().upper()
    if bench_sym and sym == bench_sym:
        return nifty.id
    if sym in ("NIFTY50", "NIFTY", "^NSEI"):
        return nifty.id
    return None


def _calculate_trading_days(start_date: date, end_date: date) -> int:
    """Return number of weekdays between two dates (inclusive)."""
    if not start_date or not end_date or start_date > end_date:
        return 0
    current_date = start_date
    trading_days = 0
    while current_date <= end_date:
        if current_date.weekday() < 5:
            trading_days += 1
        current_date += timedelta(days=1)
    return trading_days


def _normalize_date(value):
    """Convert datetime to date for consistent comparisons."""
    if isinstance(value, datetime):
        return value.date()
    return value


FORCE_PRICE_UPLOADS_SESSION_KEY = 'force_price_uploads'


def _parse_years_param(raw: Optional[str]) -> List[int]:
    """Parse comma-separated years from query string."""
    if not raw:
        return []
    years: List[int] = []
    for part in str(raw).split(','):
        part = part.strip()
        if not part:
            continue
        try:
            y = int(part)
        except ValueError:
            continue
        if 1990 <= y <= date.today().year + 1:
            years.append(y)
    return sorted(set(years))


def _get_force_upload_queue() -> List[Dict[str, Any]]:
    """Return session queue: [{security_id, years}, ...]."""
    raw = session.get(FORCE_PRICE_UPLOADS_SESSION_KEY) or []
    if not isinstance(raw, list):
        return []
    cleaned: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            sid = int(item.get('security_id'))
        except (TypeError, ValueError):
            continue
        years = item.get('years') or []
        if not isinstance(years, list):
            years = []
        years_int = sorted({
            int(y) for y in years
            if isinstance(y, int) or (isinstance(y, str) and y.isdigit())
        })
        cleaned.append({'security_id': sid, 'years': years_int})
    return cleaned


def _save_force_upload_queue(queue: List[Dict[str, Any]]) -> None:
    session[FORCE_PRICE_UPLOADS_SESSION_KEY] = queue
    session.modified = True


def _merge_force_upload_request(security_id: int, years: List[int]) -> None:
    """Add or merge a forced upload into the session queue."""
    queue = _get_force_upload_queue()
    for item in queue:
        if item['security_id'] == security_id:
            merged = sorted(set(item.get('years') or []) | set(years))
            item['years'] = merged
            _save_force_upload_queue(queue)
            return
    queue.append({'security_id': security_id, 'years': sorted(set(years))})
    _save_force_upload_queue(queue)


def _remove_force_upload_request(security_id: Optional[int] = None) -> None:
    """Remove one security from the queue, or clear all if security_id is None."""
    if security_id is None:
        session.pop(FORCE_PRICE_UPLOADS_SESSION_KEY, None)
        session.modified = True
        return
    queue = [q for q in _get_force_upload_queue() if q['security_id'] != security_id]
    _save_force_upload_queue(queue)


def _first_transaction_date_for_security(security_id: int) -> Optional[date]:
    first = (
        db.session.query(func.min(Transaction.transaction_date))
        .filter(
            Transaction.security_id == security_id,
            Transaction.transaction_date > date(2000, 1, 1),
        )
        .scalar()
    )
    return _normalize_date(first) if first else None


def _build_year_range_entry(
    security_id: int,
    year: int,
    first_date: Optional[date],
    span_end: date,
    *,
    force_pending: bool = False,
) -> Dict[str, Any]:
    """Coverage stats for one year; force_pending always shows Upload button."""
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    if first_date and year == first_date.year:
        start_date = first_date
    else:
        start_date = year_start
    end_date = span_end if year == span_end.year else year_end
    if start_date > end_date:
        start_date = year_start

    expected_days = _calculate_trading_days(start_date, end_date)
    available_days = (
        db.session.query(func.count(distinct(HistoricalPrice.date)))
        .filter(
            HistoricalPrice.security_id == security_id,
            HistoricalPrice.date >= start_date,
            HistoricalPrice.date <= end_date,
        )
        .scalar()
        or 0
    )
    coverage_percent = (
        (available_days / expected_days * 100) if expected_days > 0 else 0.0
    )
    if expected_days < MIN_REQUIRED_DAYS:
        has_year_data = coverage_percent >= MIN_COVERAGE_PERCENT
    else:
        has_year_data = (
            coverage_percent >= MIN_COVERAGE_PERCENT
            and available_days >= MIN_REQUIRED_DAYS
        )

    return {
        'year': year,
        'start_date': start_date,
        'end_date': end_date,
        'has_historical_data': False if force_pending else has_year_data,
        'coverage_percent': round(coverage_percent, 1),
        'available_days': int(available_days),
        'expected_days': int(expected_days),
        'forced_upload': force_pending,
    }


def _build_forced_stock_entry(
    security: Security, years: List[int]
) -> Optional[Dict[str, Any]]:
    """Build a stocks_data card for a manually requested security."""
    current_year = datetime.now().year
    span_end = date.today()
    first_date = _first_transaction_date_for_security(security.id)

    if not years:
        start_year = first_date.year if first_date else max(current_year - 2, 2014)
        years = list(range(start_year, current_year + 1))

    year_ranges = [
        _build_year_range_entry(
            security.id, y, first_date, span_end, force_pending=True
        )
        for y in years
        if 1990 <= y <= current_year
    ]
    if not year_ranges:
        return None

    return {
        'security_id': security.id,
        'symbol': security.symbol,
        'name': security.name,
        'security_type': security.security_type,
        'asset_class_id': security.asset_class_id,
        'first_transaction_date': first_date,
        'year_ranges': year_ranges,
        'gap_strip_note': None,
        'requested_manually': True,
        'request_note': (
            'Manual upload request — use to correct incorrect prices even when coverage looks complete.'
        ),
    }


def _apply_force_uploads_to_stocks_data(stocks_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge session-forced securities into the upload list."""
    queue = _get_force_upload_queue()
    if not queue:
        return stocks_data

    by_id: Dict[int, Dict[str, Any]] = {s['security_id']: s for s in stocks_data}
    current_year = datetime.now().year
    span_end = date.today()

    for item in queue:
        sid = item['security_id']
        years = item.get('years') or []
        security = Security.query.get(sid)
        if not security:
            continue

        if sid in by_id:
            entry = by_id[sid]
            entry['requested_manually'] = True
            entry['request_note'] = (
                'Manual upload request — re-upload to correct incorrect prices.'
            )
            existing_years = {yr['year']: yr for yr in entry.get('year_ranges') or []}
            first_date = entry.get('first_transaction_date') or _first_transaction_date_for_security(sid)
            if not years:
                years = list(existing_years.keys()) or list(
                    range(
                        first_date.year if first_date else max(current_year - 2, 2014),
                        current_year + 1,
                    )
                )
            for y in years:
                if y < 1990 or y > current_year:
                    continue
                forced = _build_year_range_entry(
                    sid, y, first_date, span_end, force_pending=True
                )
                existing_years[y] = forced
            entry['year_ranges'] = [existing_years[y] for y in sorted(existing_years)]
        else:
            forced_entry = _build_forced_stock_entry(security, years)
            if forced_entry:
                stocks_data.append(forced_entry)
                by_id[sid] = forced_entry

    return stocks_data


@historical_prices_bp.route('/')
@login_required
def upload_page():
    """Display the historical price upload page"""
    try:
        # Option A: query/session force upload for correcting incorrect prices
        clear_force = request.args.get('clear_force')
        if clear_force is not None:
            if clear_force in ('1', 'all', 'true'):
                _remove_force_upload_request(None)
            else:
                try:
                    _remove_force_upload_request(int(clear_force))
                except (TypeError, ValueError):
                    pass
            return redirect(url_for('historical_prices.upload_page'))

        force_sid_raw = request.args.get('force_security_id')
        if force_sid_raw:
            try:
                force_sid = int(force_sid_raw)
            except (TypeError, ValueError):
                force_sid = None
            if force_sid and Security.query.get(force_sid):
                years = _parse_years_param(request.args.get('years'))
                _merge_force_upload_request(force_sid, years)
                return redirect(url_for('historical_prices.upload_page'))

        # Securities with real transactions — all asset classes (equity, REITs, ETFs, debt, …).
        securities_with_transactions = db.session.query(
            Security.id,
            Security.symbol,
            Security.name,
            Security.security_type,
            Security.asset_class_id,
            func.min(Transaction.transaction_date).label('first_transaction_date')
        ).join(
            Transaction, Security.id == Transaction.security_id
        ).filter(
            # Exclude dummy dates - only consider real trade dates after 2000-01-01
            Transaction.transaction_date > date(2000, 1, 1)
        ).group_by(
            Security.id, Security.symbol, Security.name, Security.security_type, Security.asset_class_id
        ).order_by(
            Security.symbol
        ).all()

        securities_needing_upload = []
        for security in securities_with_transactions:
            first_date = _normalize_date(security.first_transaction_date)
            current_year = datetime.now().year
            span_end = date.today()

            # --- Coarse yearly coverage (easy to satisfy even with multi-week holes).
            has_complete_data = True
            for year in range(first_date.year, current_year + 1):
                year_start = date(year, 1, 1)
                year_end = date(year, 12, 31)

                available_days = db.session.query(
                    func.count(distinct(HistoricalPrice.date))
                ).filter(
                    HistoricalPrice.security_id == security.id,
                    HistoricalPrice.date >= year_start,
                    HistoricalPrice.date <= year_end
                ).scalar() or 0

                expected_days = _calculate_trading_days(year_start, min(year_end, span_end))
                coverage_percent = (
                    (available_days / expected_days * 100) if expected_days > 0 else 0.0
                )

                if expected_days == 0:
                    has_year_data = False
                elif expected_days < MIN_REQUIRED_DAYS:
                    has_year_data = coverage_percent >= MIN_COVERAGE_PERCENT
                else:
                    has_year_data = (
                        coverage_percent >= MIN_COVERAGE_PERCENT
                        and available_days >= MIN_REQUIRED_DAYS
                    )

                if not has_year_data:
                    has_complete_data = False
                    break

            max_interior, gap_left, gap_right = _max_interior_calendar_days_without_price(
                security.id, first_date, span_end
            )
            hole_in_series = max_interior >= MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG

            if not has_complete_data or hole_in_series:
                securities_needing_upload.append(
                    (
                        security,
                        {
                            'max_interior_calendar_gap': max_interior,
                            'gap_left': gap_left,
                            'gap_right': gap_right,
                            'hole_in_series': hole_in_series,
                        },
                    )
                )

        # Prepare data for template
        stocks_data = []
        current_year = datetime.now().year
        span_end = date.today()

        for item in securities_needing_upload:
            security, gap_meta = item
            first_date = _normalize_date(security.first_transaction_date)
            first_year = first_date.year if first_date else current_year

            year_ranges = []
            for year in range(first_year, current_year + 1):
                year_start = date(year, 1, 1)
                year_end = date(year, 12, 31)

                if year == first_year and first_date:
                    start_date = first_date
                else:
                    start_date = year_start

                if year == current_year:
                    end_date = span_end
                else:
                    end_date = year_end

                expected_days = _calculate_trading_days(start_date, end_date)
                available_days = db.session.query(
                    func.count(distinct(HistoricalPrice.date))
                ).filter(
                    HistoricalPrice.security_id == security.id,
                    HistoricalPrice.date >= start_date,
                    HistoricalPrice.date <= end_date
                ).scalar() or 0

                coverage_percent = (
                    (available_days / expected_days * 100) if expected_days > 0 else 0.0
                )

                if expected_days < MIN_REQUIRED_DAYS:
                    has_year_data = coverage_percent >= MIN_COVERAGE_PERCENT
                else:
                    has_year_data = coverage_percent >= MIN_COVERAGE_PERCENT and available_days >= MIN_REQUIRED_DAYS

                year_ranges.append({
                    'year': year,
                    'start_date': start_date,
                    'end_date': end_date,
                    'has_historical_data': has_year_data,
                    'coverage_percent': round(coverage_percent, 1),
                    'available_days': int(available_days),
                    'expected_days': int(expected_days),
                })

            pending_years = [yr for yr in year_ranges if not yr['has_historical_data']]

            gl, gr = gap_meta.get('gap_left'), gap_meta.get('gap_right')
            if gap_meta.get('hole_in_series') and not pending_years and gl is not None and gr is not None:
                overlap_years = range(
                    min(gl.year, first_year),
                    max(gr.year, first_year, current_year) + 1,
                )
                for y in overlap_years:
                    if y < first_year or y > current_year:
                        continue
                    base = next((x for x in year_ranges if x['year'] == y), None)
                    if base:
                        pend = dict(base)
                        pend['has_historical_data'] = False
                        pend['gap_strip_note'] = (
                            f"Largest hole (no closes on consecutive calendar dates): "
                            f"{gap_meta['max_interior_calendar_gap']} days strictly between quotes "
                            f"(≈ {gl} ↔ {gr})"
                        )
                        pending_years.append(pend)

            if pending_years:
                gap_note = None
                if gap_meta.get('hole_in_series') and gap_meta.get('gap_left') and gap_meta.get('gap_right'):
                    gap_note = (
                        f"Largest consecutive calendar-date block with no saved close "
                        f"({gap_meta['max_interior_calendar_gap']} days strictly between brackets "
                        f"≈ {gap_meta['gap_left']} ↔ {gap_meta['gap_right']}); "
                        f"yearly coverage can still show green."
                    )
                stocks_data.append({
                    'security_id': security.id,
                    'symbol': security.symbol,
                    'name': security.name,
                    'security_type': security.security_type,
                    'asset_class_id': security.asset_class_id,
                    'first_transaction_date': first_date,
                    'year_ranges': pending_years,
                    'gap_strip_note': gap_note,
                })
        
        # If no securities with transactions found, check for securities without transactions
        if not securities_with_transactions:
            logger.info("No securities with transactions found. Checking for securities without transactions...")
            
            # Get all securities that might need historical data
            all_securities = Security.query.filter(
                Security.symbol.like('%BBETF%')
            ).all()
            
            logger.info(f"Found {len(all_securities)} BBETF securities without transactions:")
            for sec in all_securities:
                logger.info(f"  - {sec.symbol} ({sec.security_type})")
                
                # Add to stocks_data even without transactions
                stocks_data.append({
                    'security_id': sec.id,
                    'symbol': sec.symbol,
                    'name': sec.name,
                    'security_type': sec.security_type,
                    'asset_class_id': sec.asset_class_id,
                    'first_transaction_date': None,
                    'year_ranges': [{
                        'year': current_year,
                        'start_date': date(current_year, 1, 1),
                        'end_date': date.today(),
                        'has_historical_data': False
                    }]
                })

        # Merge manually requested companies (session / prior force_security_id)
        stocks_data = _apply_force_uploads_to_stocks_data(stocks_data)
        
        # Get Nifty data status
        try:
            from services.benchmark_service import BenchmarkService
            nifty_status = BenchmarkService.get_all_years_status(benchmark_id=1, start_year=2014)
        except Exception as e:
            logger.warning(f"Could not load Nifty status: {e}")
            nifty_status = {}

        force_queue = _get_force_upload_queue()
        
        return render_template('historical_prices/upload.html', 
                             stocks_data=stocks_data,
                             securities_data=stocks_data,  # Alias for template
                             current_year=current_year,
                             nifty_status=nifty_status,
                             force_upload_queue=force_queue)
        
    except Exception as e:
        logger.error(f"Error loading historical prices upload page: {str(e)}")
        flash(f"Error loading page: {str(e)}", 'error')
        return render_template('historical_prices/upload.html', 
                             stocks_data=[], 
                             current_year=datetime.now().year,
                             nifty_status={},
                             force_upload_queue=[])

@historical_prices_bp.route('/view')
@login_required
def view_historical_prices():
    """Display the historical price viewer page"""
    try:
        # Get all securities for dropdown
        all_securities = Security.query.order_by(Security.symbol).all()
        
        return render_template('historical_prices/view.html', 
                             securities=all_securities)
        
    except Exception as e:
        logger.error(f"Error loading historical prices viewer page: {str(e)}")
        flash(f"Error loading page: {str(e)}", 'error')
        return render_template('historical_prices/view.html', 
                             securities=[])

@historical_prices_bp.route('/api/securities/search')
@login_required
def search_securities():
    """Search securities with autocomplete.

    - No ``q`` or blank ``q``: first 50 securities by symbol (browse / sanity check in browser).
    - Single character: prefix match on symbol or name.
    - Two or more characters: substring match on symbol or name.
    """
    try:
        from sqlalchemy import or_

        query = request.args.get('q', '').strip()

        q = Security.query
        if len(query) >= 2:
            securities = (
                q.filter(
                    or_(
                        Security.symbol.ilike(f'%{query}%'),
                        Security.name.ilike(f'%{query}%'),
                    )
                )
                .order_by(Security.symbol.asc())
                .limit(50)
                .all()
            )
        elif len(query) == 1:
            securities = (
                q.filter(
                    or_(
                        Security.symbol.ilike(f'{query}%'),
                        Security.name.ilike(f'{query}%'),
                    )
                )
                .order_by(Security.symbol.asc())
                .limit(50)
                .all()
            )
        else:
            securities = q.order_by(Security.symbol.asc()).limit(50).all()
        
        # Build response and collect security IDs that need fallback price
        security_ids_needing_fallback = [s.id for s in securities if s.current_price is None or (hasattr(s.current_price, '__float__') and float(s.current_price) == 0)]
        latest_prices = {}
        if security_ids_needing_fallback:
            # Fallback: use latest historical close price when Security.current_price is missing
            subq = db.session.query(
                HistoricalPrice.security_id,
                db.func.max(HistoricalPrice.date).label('max_date')
            ).filter(HistoricalPrice.security_id.in_(security_ids_needing_fallback)).group_by(HistoricalPrice.security_id).subquery()
            rows = db.session.query(HistoricalPrice.security_id, HistoricalPrice.close_price).join(
                subq,
                db.and_(
                    HistoricalPrice.security_id == subq.c.security_id,
                    HistoricalPrice.date == subq.c.max_date
                )
            ).all()
            latest_prices = {r.security_id: float(r.close_price) for r in rows if r.close_price is not None}
        
        def price_for(s):
            if s.current_price is not None and float(s.current_price) > 0:
                return float(s.current_price)
            return latest_prices.get(s.id)
        
        return jsonify({
            'securities': [{
                'id': s.id,
                'symbol': s.symbol,
                'name': s.name,
                'type': s.security_type or 'STOCK',
                'current_price': price_for(s)
            } for s in securities]
        })
        
    except Exception as e:
        logger.error(f"Error searching securities: {str(e)}")
        return jsonify({'securities': []}), 500

@historical_prices_bp.route('/api/security/<int:security_id>/prices')
@login_required
def get_security_prices(security_id):
    """
    Get historical prices for a specific security using PriceService
    
    Supports two modes:
    1. Single date lookup (when 'date' parameter is provided) - uses PriceService
    2. Paginated list (when 'date' parameter is not provided) - direct database query
    
    Query Parameters:
    - date: Single date to lookup (YYYY-MM-DD) - uses PriceService
    - adjusted: Apply corporate action adjustments (default: false)
    - fallback: Allow 4-day window fallback (default: false for single lookup)
    - page: Page number for paginated results
    - per_page: Results per page
    - date_from: Start date filter
    - date_to: End date filter
    """
    try:
        from services.price_service import PriceService
        
        # Get query parameters
        single_date = request.args.get('date')
        use_adjusted = request.args.get('adjusted', 'false').lower() == 'true'
        allow_fallback = request.args.get('fallback', 'false').lower() == 'true'
        
        # Get security info
        security = Security.query.get_or_404(security_id)
        benchmark_series_id = _benchmark_id_for_historical_viewer(security)

        # SINGLE DATE LOOKUP MODE - Use PriceService
        if single_date:
            try:
                target_date = datetime.strptime(single_date, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'success': False, 'message': 'Invalid date format. Use YYYY-MM-DD'}), 400

            if benchmark_series_id:
                from services.benchmark_service import BenchmarkService

                pv = BenchmarkService.get_price_on_date(benchmark_series_id, target_date)
                return jsonify({
                    'success': True,
                    'security': {
                        'id': security.id,
                        'symbol': security.symbol,
                        'name': security.name,
                        'security_type': security.security_type
                    },
                    'price_data': {
                        'price': pv,
                        'source': 'benchmark' if pv is not None else 'none',
                        'is_valid': pv is not None,
                        'adjustment_applied': False,
                        'last_updated': target_date.isoformat() if pv is not None else None
                    },
                    'target_date': target_date.isoformat(),
                    'use_adjusted': False,
                    'allow_fallback': allow_fallback,
                    'series_read_only': True,
                })

            # Use PriceService for single date lookup
            price_data = PriceService.get_price(
                security_id=security_id,
                target_date=target_date,
                use_adjusted=use_adjusted,
                allow_fallback=allow_fallback
            )

            return jsonify({
                'success': True,
                'security': {
                    'id': security.id,
                    'symbol': security.symbol,
                    'name': security.name,
                    'security_type': security.security_type
                },
                'price_data': {
                    'price': price_data.price,
                    'source': price_data.source,
                    'is_valid': price_data.is_valid,
                    'adjustment_applied': price_data.adjustment_applied,
                    'last_updated': price_data.last_updated.isoformat() if price_data.last_updated else None
                },
                'target_date': target_date.isoformat(),
                'use_adjusted': use_adjusted,
                'allow_fallback': allow_fallback
            })

        # PAGINATED LIST MODE - Direct database query (existing behavior)
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 100, type=int), 1000)
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        if benchmark_series_id:
            query = BenchmarkData.query.filter_by(benchmark_id=benchmark_series_id)

            if date_from:
                try:
                    query = query.filter(BenchmarkData.date >= datetime.strptime(date_from, '%Y-%m-%d').date())
                except ValueError:
                    return jsonify({'success': False, 'message': 'Invalid date_from format. Use YYYY-MM-DD'}), 400

            if date_to:
                try:
                    query = query.filter(BenchmarkData.date <= datetime.strptime(date_to, '%Y-%m-%d').date())
                except ValueError:
                    return jsonify({'success': False, 'message': 'Invalid date_to format. Use YYYY-MM-DD'}), 400

            total = query.count()
            rows = query.order_by(BenchmarkData.date.desc()).offset(
                (page - 1) * per_page
            ).limit(per_page).all()

            prices_data = []
            for bd in rows:
                prices_data.append({
                    'id': None,
                    'benchmark_data_id': bd.id,
                    'date': bd.date.isoformat(),
                    'close_price': float(bd.price),
                    'source': 'benchmark',
                    'created_at': bd.created_at.isoformat() if bd.created_at else None,
                    'updated_at': None,
                })

            return jsonify({
                'success': True,
                'series_read_only': True,
                'security': {
                    'id': security.id,
                    'symbol': security.symbol,
                    'name': security.name,
                    'security_type': security.security_type
                },
                'prices': prices_data,
                'total_count': total,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page if total > 0 else 0
                }
            })

        # Build query with filters
        query = HistoricalPrice.query.filter_by(security_id=security_id)
        
        if date_from:
            try:
                query = query.filter(HistoricalPrice.date >= datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                return jsonify({'success': False, 'message': 'Invalid date_from format. Use YYYY-MM-DD'}), 400
        
        if date_to:
            try:
                query = query.filter(HistoricalPrice.date <= datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                return jsonify({'success': False, 'message': 'Invalid date_to format. Use YYYY-MM-DD'}), 400
        
        # Get total count
        total = query.count()
        
        # Apply pagination
        prices = query.order_by(HistoricalPrice.date.desc()).offset(
            (page - 1) * per_page
        ).limit(per_page).all()
        
        prices_data = []
        for price in prices:
            prices_data.append({
                'id': price.id,
                'date': price.date.isoformat(),
                'close_price': float(price.close_price),
                'source': price.source,
                'created_at': price.created_at.isoformat() if price.created_at else None,
                'updated_at': price.updated_at.isoformat() if price.updated_at else None
            })
        
        return jsonify({
            'success': True,
            'series_read_only': False,
            'security': {
                'id': security.id,
                'symbol': security.symbol,
                'name': security.name,
                'security_type': security.security_type
            },
            'prices': prices_data,
            'total_count': total,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': (total + per_page - 1) // per_page if total > 0 else 0
            }
        })

    except Exception as e:
        logger.error(f"Error getting security prices: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@historical_prices_bp.route('/api/price/<int:price_id>', methods=['PUT'])
@login_required
def update_price(price_id):
    """Update a specific historical price"""
    try:
        data = request.get_json()
        
        # Get the price record
        price = HistoricalPrice.query.get_or_404(price_id)
        
        # Update fields
        if 'close_price' in data:
            price.close_price = float(data['close_price'])
        
        if 'date' in data:
            price.date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        
        price.updated_at = datetime.now()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Price updated successfully',
            'price': {
                'id': price.id,
                'date': price.date.isoformat(),
                'close_price': float(price.close_price),
                'source': price.source,
                'updated_at': price.updated_at.isoformat()
            }
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating price: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@historical_prices_bp.route('/api/price/<int:price_id>', methods=['DELETE'])
@login_required
def delete_price(price_id):
    """Delete a specific historical price"""
    try:
        # Get the price record
        price = HistoricalPrice.query.get_or_404(price_id)
        
        db.session.delete(price)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Price deleted successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting price: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@historical_prices_bp.route('/api/security/<int:security_id>/prices', methods=['POST'])
@login_required
def add_price(security_id):
    """Add a new historical price for a security"""
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data.get('date') or not data.get('close_price'):
            return jsonify({'success': False, 'message': 'Date and close_price are required'}), 400
        
        # Check if price already exists for this date
        existing_price = HistoricalPrice.query.filter_by(
            security_id=security_id,
            date=datetime.strptime(data['date'], '%Y-%m-%d').date()
        ).first()
        
        if existing_price:
            return jsonify({'success': False, 'message': 'Price already exists for this date'}), 400
        
        # Create new price record
        new_price = HistoricalPrice(
            security_id=security_id,
            date=datetime.strptime(data['date'], '%Y-%m-%d').date(),
            close_price=float(data['close_price']),
            source='manual',
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        db.session.add(new_price)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Price added successfully',
            'price': {
                'id': new_price.id,
                'date': new_price.date.isoformat(),
                'close_price': float(new_price.close_price),
                'source': new_price.source,
                'created_at': new_price.created_at.isoformat(),
                'updated_at': new_price.updated_at.isoformat()
            }
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding price: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@historical_prices_bp.route('/upload', methods=['POST'])
@login_required
def upload_historical_prices():
    """Handle historical price file upload"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        security_id = request.form.get('security_id')
        year = request.form.get('year')
        
        if not security_id or not year:
            return jsonify({'success': False, 'message': 'Missing security_id or year'}), 400
        
        security_id = int(security_id)
        year = int(year)
        
        # Get the security to verify against file name
        security = Security.query.get(security_id)
        if not security:
            return jsonify({'success': False, 'message': 'Security not found'}), 404
        
        # Validate file type
        if not file.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
            return jsonify({'success': False, 'message': 'Invalid file type. Please upload CSV or Excel file'}), 400
        
        # ✅ VERIFY FILE NAME FORMAT: Quote-Equity-SYMBOL-EQ-DD-MM-YYYY-to-DD-MM-YYYY
        filename_base = file.filename.rsplit('.', 1)[0]  # Remove extension
        logger.info(f"Verifying file name: {filename_base}")
        
        # Parse file name format: Quote-Equity-DIXON-EQ-01-01-2025-to-19-10-2025
        parts = filename_base.split('-')
        
        if len(parts) >= 11:  # Quote-Equity-SYMBOL-EQ-DD-MM-YYYY-to-DD-MM-YYYY
            try:
                # Extract symbol from file name (position 2)
                file_symbol = parts[2].upper()
                
                # Extract date range from file name
                # Format: DD-MM-YYYY-to-DD-MM-YYYY (positions 4-10)
                start_day = parts[4]
                start_month = parts[5]
                start_year = parts[6]
                # parts[7] is 'to'
                end_day = parts[8]
                end_month = parts[9]
                end_year = parts[10]
                
                file_start_date = f"{start_day}-{start_month}-{start_year}"
                file_end_date = f"{end_day}-{end_month}-{end_year}"
                
                # Parse dates
                file_start = datetime.strptime(file_start_date, '%d-%m-%Y').date()
                file_end = datetime.strptime(file_end_date, '%d-%m-%Y').date()
                
                logger.info(f"File name parsed - Symbol: {file_symbol}, Date Range: {file_start} to {file_end}")
                
                # Verify stock symbol matches (allow LTIM filename when security is LTM — NSE rename)
                expected_symbol = security.symbol.split(':')[-1].upper()  # Remove exchange prefix if present
                symbol_ok = file_symbol == expected_symbol or (
                    expected_symbol == 'LTM' and file_symbol == 'LTIM'
                )
                if not symbol_ok:
                    return jsonify({
                        'success': False,
                        'message': f'⚠️ STOCK SYMBOL MISMATCH!\n\nExpected: {security.symbol}\nFile contains: {file_symbol}\n\nPlease upload the correct file for {security.symbol}'
                    }), 400
                
                # Verify year is within the file's date range
                year_start = date(year, 1, 1)
                year_end = date(year, 12, 31)
                
                # Check if the requested year overlaps with file date range
                if file_end < year_start or file_start > year_end:
                    return jsonify({
                        'success': False,
                        'message': f'⚠️ DATE RANGE MISMATCH!\n\nRequested year: {year}\nFile date range: {file_start} to {file_end}\n\nThe file date range must include data for year {year}'
                    }), 400
                
                logger.info(f"✅ File name verification passed for {security.symbol} year {year}")
                
            except (IndexError, ValueError) as e:
                logger.warning(f"Could not parse file name format: {filename_base}. Error: {str(e)}")
                # Don't fail if file name doesn't match expected format - just log warning
        else:
            logger.warning(f"File name does not match expected format: {filename_base}")
        
        # Read the file
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        # Check for required columns (flexible column name matching with whitespace trimming)
        available_columns = list(df.columns)
        
        # Find Date column (case insensitive, whitespace trimmed)
        date_column = None
        for col in df.columns:
            col_clean = col.strip().lower()
            if col_clean in ['date', 'dates']:
                date_column = col
                break
        
        # Find close column (case insensitive, whitespace trimmed)
        close_column = None
        for col in df.columns:
            col_clean = col.strip().lower()
            if col_clean in ['close', 'closing', 'close price']:
                close_column = col
                break
        
        if not date_column or not close_column:
            missing = []
            if not date_column:
                missing.append('Date')
            if not close_column:
                missing.append('close')
            return jsonify({
                'success': False, 
                'message': f'Missing required columns: {", ".join(missing)}. Available columns: {", ".join(available_columns)}'
            }), 400
        
        # Debug: Show first few rows
        logger.info(f"DataFrame shape: {df.shape}")
        logger.info(f"First 3 rows:")
        for i in range(min(3, len(df))):
            logger.info(f"Row {i}: {dict(df.iloc[i])}")
        
        # Process the data
        processed_count = 0
        error_count = 0
        
        for index, row in df.iterrows():
            try:
                # Parse date using detected column name
                date_value = row[date_column]
                logger.info(f"Processing row {index}: Date='{date_value}', Close='{row[close_column]}'")
                
                if isinstance(date_value, str):
                    # Try different date formats (including DD-Mon-YY format)
                    date_formats = ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d', '%d-%b-%Y', '%d %b %Y', '%d-%b-%y']
                    price_date = None
                    for fmt in date_formats:
                        try:
                            price_date = datetime.strptime(date_value.strip(), fmt).date()
                            break
                        except ValueError:
                            continue
                    if price_date is None:
                        raise ValueError(f"Unable to parse date: '{date_value}'")
                else:
                    price_date = date_value.date()
                
                # Get close price using detected column name
                close_value = row[close_column]
                logger.info(f"Close value: '{close_value}', type: {type(close_value)}")
                # Handle comma-separated numbers
                if isinstance(close_value, str):
                    close_value = close_value.replace(',', '')
                close_price = float(close_value)
                
                # Check if record already exists
                existing = HistoricalPrice.query.filter_by(
                    security_id=security_id,
                    date=price_date
                ).first()
                
                if existing:
                    # Update existing record
                    existing.close_price = close_price
                    existing.source = 'upload'
                    existing.updated_at = datetime.now()
                else:
                    # Create new record
                    historical_price = HistoricalPrice(
                        security_id=security_id,
                        date=price_date,
                        close_price=close_price,
                        source='upload',
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    db.session.add(historical_price)
                
                processed_count += 1
                
            except Exception as e:
                logger.error(f"Error processing row {index}: {str(e)}")
                logger.error(f"Row data: {dict(row)}")
                error_count += 1
                continue
        
        # Commit all changes
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Successfully processed {processed_count} records',
            'processed_count': processed_count,
            'error_count': error_count
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error uploading historical prices: {str(e)}")
        return jsonify({'success': False, 'message': f'Upload failed: {str(e)}'}), 500

@historical_prices_bp.route('/upload-nifty', methods=['POST'])
@login_required
def upload_nifty_data():
    """Handle Nifty benchmark data upload for a specific year"""
    try:
        from services.benchmark_service import BenchmarkService
        
        year = request.form.get('year', type=int)
        if not year:
            return jsonify({'success': False, 'error': 'Year is required'}), 400
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        # Read file
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        # Find date and close columns (case-insensitive)
        date_col = None
        price_col = None
        
        for col in df.columns:
            col_lower = col.lower().strip()
            if 'date' in col_lower and not date_col:
                date_col = col
            if 'close' in col_lower and not price_col:
                price_col = col
        
        if not date_col or not price_col:
            return jsonify({
                'success': False, 
                'error': f'Required columns not found. Found: {list(df.columns)}'
            }), 400
        
        # Clean and validate data
        df_clean = df[[date_col, price_col]].copy()
        df_clean = df_clean.dropna()
        
        # Parse dates
        df_clean[date_col] = pd.to_datetime(df_clean[date_col], errors='coerce')
        df_clean = df_clean.dropna(subset=[date_col])
        
        # Convert price to numeric
        df_clean[price_col] = pd.to_numeric(df_clean[price_col], errors='coerce')
        df_clean = df_clean.dropna(subset=[price_col])
        
        # Filter for the requested year
        df_year = df_clean[df_clean[date_col].dt.year == year]
        
        if len(df_year) == 0:
            return jsonify({
                'success': False, 
                'error': f'No data found for year {year} in the uploaded file'
            }), 400
        
        # Clear existing data for this year first (overwrite mode)
        clear_result = BenchmarkService.clear_year_data(benchmark_id=1, year=year)
        cleared_count = clear_result.get('deleted_count', 0)
        
        # Upload using BenchmarkService with overwrite=True
        uploaded_count = 0
        skipped_count = 0
        
        for _, row in df_year.iterrows():
            price_date = row[date_col].date()
            price = float(row[price_col])
            
            result = BenchmarkService.create_or_update_price(
                benchmark_id=1,  # Nifty 50
                price_date=price_date,
                price=price,
                overwrite=True  # Always overwrite in upload mode
            )
            
            if result['success']:
                if result['action'] == 'created':
                    uploaded_count += 1
                elif result['action'] == 'updated':
                    uploaded_count += 1
                else:
                    skipped_count += 1
        
        db.session.commit()
        
        logger.info(f"Cleared {cleared_count} old records and uploaded {uploaded_count} Nifty records for year {year}")
        
        return jsonify({
            'success': True,
            'uploaded': uploaded_count,
            'cleared': cleared_count,
            'skipped': skipped_count,
            'year': year,
            'message': f'Cleared {cleared_count} old records, uploaded {uploaded_count} new records for year {year}'
        })
    
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error uploading Nifty data: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@historical_prices_bp.route('/upload-mutual-fund', methods=['POST'])
@login_required
def upload_mutual_fund():
    """Handle mutual fund historical price file upload"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        fund_symbol = request.form.get('fund_symbol')
        year = request.form.get('year', type=int)
        
        if not fund_symbol or not year:
            return jsonify({'success': False, 'error': 'Missing fund_symbol or year'}), 400
        
        # Find or create the mutual fund security
        security = Security.query.filter_by(symbol=fund_symbol).first()
        if not security:
            # Create the mutual fund security if it doesn't exist
            from models import AssetClass
            mutual_fund_asset_class = AssetClass.query.filter_by(name='Mutual Fund').first()
            if not mutual_fund_asset_class:
                mutual_fund_asset_class = AssetClass(
                    name='Mutual Fund',
                    description='Mutual Fund securities',
                    created_by=current_user.id,
                )
                db.session.add(mutual_fund_asset_class)
                db.session.flush()
            
            security = Security(
                symbol=fund_symbol,
                name=f'{fund_symbol} Mutual Fund',
                security_type='Mutual Fund',
                asset_class_id=mutual_fund_asset_class.id,
                created_by=current_user.id,
            )
            db.session.add(security)
            db.session.flush()
            logger.info(f"Created new mutual fund security: {fund_symbol}")
        
        # Validate file type
        if not file.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
            return jsonify({'success': False, 'error': 'Invalid file type. Please upload CSV or Excel file'}), 400
        
        # Read the file
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        # Find required columns (flexible column name matching)
        date_column = None
        close_column = None
        
        for col in df.columns:
            col_clean = col.strip().lower()
            if col_clean in ['date', 'dates'] and not date_column:
                date_column = col
            if col_clean in ['close', 'closing', 'close price', 'nav', 'net asset value'] and not close_column:
                close_column = col
        
        if not date_column or not close_column:
            missing = []
            if not date_column:
                missing.append('Date')
            if not close_column:
                missing.append('close/nav')
            return jsonify({
                'success': False, 
                'error': f'Missing required columns: {", ".join(missing)}. Available columns: {", ".join(list(df.columns))}'
            }), 400
        
        # Clear existing data for this year first (overwrite mode)
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        
        existing_prices = HistoricalPrice.query.filter(
            HistoricalPrice.security_id == security.id,
            HistoricalPrice.date >= year_start,
            HistoricalPrice.date <= year_end
        ).all()
        
        cleared_count = len(existing_prices)
        for price in existing_prices:
            db.session.delete(price)
        
        # Process the data
        uploaded_count = 0
        error_count = 0
        
        for index, row in df.iterrows():
            try:
                # Parse date
                date_value = row[date_column]
                
                if isinstance(date_value, str):
                    # Try different date formats
                    date_formats = ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d', '%d-%b-%Y', '%d %b %Y', '%d-%b-%y']
                    price_date = None
                    for fmt in date_formats:
                        try:
                            price_date = datetime.strptime(date_value.strip(), fmt).date()
                            break
                        except ValueError:
                            continue
                    if price_date is None:
                        raise ValueError(f"Unable to parse date: '{date_value}'")
                else:
                    price_date = date_value.date()
                
                # Filter for the requested year
                if price_date.year != year:
                    continue
                
                # Get close price (NAV for mutual funds)
                close_value = row[close_column]
                if isinstance(close_value, str):
                    close_value = close_value.replace(',', '')
                close_price = float(close_value)
                
                # Create new record
                historical_price = HistoricalPrice(
                    security_id=security.id,
                    date=price_date,
                    close_price=close_price,
                    source='mutual_fund_upload',
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                db.session.add(historical_price)
                uploaded_count += 1
                
            except Exception as e:
                logger.error(f"Error processing row {index}: {str(e)}")
                error_count += 1
                continue
        
        # Commit all changes
        db.session.commit()
        
        logger.info(f"Uploaded {uploaded_count} mutual fund records for {fund_symbol} year {year}")
        
        return jsonify({
            'success': True,
            'uploaded': uploaded_count,
            'cleared': cleared_count,
            'error_count': error_count,
            'fund_symbol': fund_symbol,
            'year': year,
            'message': f'Cleared {cleared_count} old records, uploaded {uploaded_count} new records for {fund_symbol} year {year}'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error uploading mutual fund data: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@historical_prices_bp.route('/upload-debt-etf', methods=['POST'])
@login_required
def upload_debt_etf():
    """Handle debt ETF historical price file upload"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        etf_symbol = request.form.get('etf_symbol')
        year = request.form.get('year', type=int)
        
        if not etf_symbol or not year:
            return jsonify({'success': False, 'error': 'Missing etf_symbol or year'}), 400
        
        # Find or create the debt ETF security
        security = Security.query.filter_by(symbol=etf_symbol).first()
        if not security:
            # Create the debt ETF security if it doesn't exist
            from models import AssetClass
            debt_asset_class = AssetClass.query.filter_by(name='Debt').first()
            if not debt_asset_class:
                debt_asset_class = AssetClass(
                    name='Debt',
                    description='Debt securities',
                    created_by=current_user.id,
                )
                db.session.add(debt_asset_class)
                db.session.flush()
            
            security = Security(
                symbol=etf_symbol,
                name=f'{etf_symbol} Debt ETF',
                security_type='ETF',
                asset_class_id=debt_asset_class.id,
                created_by=current_user.id,
            )
            db.session.add(security)
            db.session.flush()
            logger.info(f"Created new debt ETF security: {etf_symbol}")
        
        # Validate file type
        if not file.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
            return jsonify({'success': False, 'error': 'Invalid file type. Please upload CSV or Excel file'}), 400
        
        # Read the file
        if file.filename.lower().endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        
        # Find required columns (flexible column name matching)
        date_column = None
        close_column = None
        
        for col in df.columns:
            col_clean = col.strip().lower()
            if col_clean in ['date', 'dates'] and not date_column:
                date_column = col
            if col_clean in ['close', 'closing', 'close price'] and not close_column:
                close_column = col
        
        if not date_column or not close_column:
            missing = []
            if not date_column:
                missing.append('Date')
            if not close_column:
                missing.append('close')
            return jsonify({
                'success': False, 
                'error': f'Missing required columns: {", ".join(missing)}. Available columns: {", ".join(list(df.columns))}'
            }), 400
        
        # Clear existing data for this year first (overwrite mode)
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        
        existing_prices = HistoricalPrice.query.filter(
            HistoricalPrice.security_id == security.id,
            HistoricalPrice.date >= year_start,
            HistoricalPrice.date <= year_end
        ).all()
        
        cleared_count = len(existing_prices)
        for price in existing_prices:
            db.session.delete(price)
        
        # Process the data
        uploaded_count = 0
        error_count = 0
        
        for index, row in df.iterrows():
            try:
                # Parse date
                date_value = row[date_column]
                
                if isinstance(date_value, str):
                    # Try different date formats
                    date_formats = ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d', '%d-%b-%Y', '%d %b %Y', '%d-%b-%y']
                    price_date = None
                    for fmt in date_formats:
                        try:
                            price_date = datetime.strptime(date_value.strip(), fmt).date()
                            break
                        except ValueError:
                            continue
                    if price_date is None:
                        raise ValueError(f"Unable to parse date: '{date_value}'")
                else:
                    price_date = date_value.date()
                
                # Filter for the requested year
                if price_date.year != year:
                    continue
                
                # Get close price
                close_value = row[close_column]
                if isinstance(close_value, str):
                    close_value = close_value.replace(',', '')
                close_price = float(close_value)
                
                # Create new record
                historical_price = HistoricalPrice(
                    security_id=security.id,
                    date=price_date,
                    close_price=close_price,
                    source='debt_etf_upload',
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                db.session.add(historical_price)
                uploaded_count += 1
                
            except Exception as e:
                logger.error(f"Error processing row {index}: {str(e)}")
                error_count += 1
                continue
        
        # Commit all changes
        db.session.commit()
        
        logger.info(f"Uploaded {uploaded_count} debt ETF records for {etf_symbol} year {year}")
        
        return jsonify({
            'success': True,
            'uploaded': uploaded_count,
            'cleared': cleared_count,
            'error_count': error_count,
            'etf_symbol': etf_symbol,
            'year': year,
            'message': f'Cleared {cleared_count} old records, uploaded {uploaded_count} new records for {etf_symbol} year {year}'
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error uploading debt ETF data: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@historical_prices_bp.route('/api/amfi/sync-daily', methods=['POST'])
@login_required
def amfi_sync_daily_nav():
    """Fetch AMFI NAVAll.txt and upsert today's NAV for mutual funds linked via meta_data.amfi_scheme_code."""
    try:
        from services import amfi_nav_service

        amfi_nav_service.clear_nav_all_cache()
        stats = amfi_nav_service.sync_daily_nav_for_linked_securities(
            db_session=db,
            Security=Security,
            HistoricalPrice=HistoricalPrice,
            fetch_fresh=True,
        )
        db.session.commit()
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        db.session.rollback()
        logger.exception("AMFI sync failed")
        return jsonify({'success': False, 'error': str(e)}), 500


@historical_prices_bp.route('/api/amfi/schemes', methods=['GET'])
@login_required
def amfi_search_schemes():
    """Search schemes from cached AMFI NAVAll (10 min TTL). Query param ``q`` required (min 2 chars)."""
    try:
        from services import amfi_nav_service

        q = (request.args.get('q') or '').strip()
        if len(q) < 2:
            return jsonify({'success': False, 'error': 'Query q must be at least 2 characters'}), 400
        limit = request.args.get('limit', default=50, type=int)
        limit = max(1, min(limit, 200))
        text = amfi_nav_service.fetch_nav_all_text_cached()
        rows = amfi_nav_service.parse_nav_all_text(text)
        matches = amfi_nav_service.search_schemes(rows, q, limit=limit)
        return jsonify({'success': True, 'count': len(matches), 'schemes': matches})
    except Exception as e:
        logger.exception("AMFI scheme search failed")
        return jsonify({'success': False, 'error': str(e)}), 500


@historical_prices_bp.route('/api/security/<int:security_id>/amfi-link', methods=['PUT'])
@login_required
def amfi_link_security(security_id: int):
    """JSON body: { \"amfi_scheme_code\": \"120503\", \"amfi_isin\": optional }"""
    try:
        from services import amfi_nav_service

        security = Security.query.get_or_404(security_id)
        data = request.get_json(silent=True) or {}
        code = (data.get('amfi_scheme_code') or '').strip()
        if not code:
            return jsonify({'success': False, 'error': 'amfi_scheme_code is required'}), 400
        isin = data.get('amfi_isin')
        amfi_nav_service.link_security_to_amfi_scheme(
            security=security,
            amfi_scheme_code=code,
            amfi_isin=str(isin).strip() if isin else None,
        )
        db.session.commit()
        return jsonify({'success': True, 'security_id': security.id, 'symbol': security.symbol})
    except Exception as e:
        db.session.rollback()
        logger.exception("AMFI link failed")
        return jsonify({'success': False, 'error': str(e)}), 500


@historical_prices_bp.route('/api/security/<int:security_id>/amfi-backfill', methods=['POST'])
@login_required
def amfi_backfill_security(security_id: int):
    """Optional body: { \"replace_existing\": false } — uses mfapi.in historical NAV."""
    try:
        from services import amfi_nav_service

        security = Security.query.get_or_404(security_id)
        data = request.get_json(silent=True) or {}
        replace_existing = bool(data.get('replace_existing'))
        stats = amfi_nav_service.backfill_historical_from_mfapi(
            db_session=db,
            security=security,
            HistoricalPrice=HistoricalPrice,
            replace_existing=replace_existing,
        )
        db.session.commit()
        return jsonify({'success': True, 'stats': stats})
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception("AMFI mfapi backfill failed")
        return jsonify({'success': False, 'error': str(e)}), 500


@historical_prices_bp.route('/api/amfi/create-mutual-fund-security', methods=['POST'])
@login_required
def amfi_create_mutual_fund_security():
    """
    JSON body: { \"amfi_scheme_code\": \"120503\", \"symbol\": optional }.
    Creates Mutual Fund security from latest AMFI row (symbol defaults to MF{code}).
    """
    try:
        from models import AssetClass
        from services import amfi_nav_service

        data = request.get_json(silent=True) or {}
        code = (data.get('amfi_scheme_code') or '').strip()
        if not code:
            return jsonify({'success': False, 'error': 'amfi_scheme_code is required'}), 400
        symbol = data.get('symbol')
        sec, row = amfi_nav_service.create_mutual_fund_security_from_amfi(
            db_session=db,
            Security=Security,
            AssetClass=AssetClass,
            HistoricalPrice=HistoricalPrice,
            scheme_code=code,
            user_id=current_user.id,
            symbol=str(symbol).strip() if symbol else None,
        )
        db.session.commit()
        return jsonify(
            {
                'success': True,
                'security_id': sec.id,
                'symbol': sec.symbol,
                'name': sec.name,
                'nav_date': row.nav_date.isoformat(),
                'nav': str(row.nav),
            }
        )
    except ValueError as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        logger.exception("AMFI create security failed")
        return jsonify({'success': False, 'error': str(e)}), 500


@historical_prices_bp.route('/status')
@login_required
def get_upload_status():
    """Get current upload status for all stocks"""
    try:
        # Get all securities with transactions (excluding dummy dates like Jan 1, 2000)
        securities_with_transactions = db.session.query(
            Security.id,
            Security.symbol,
            Security.name,
            func.min(Transaction.transaction_date).label('first_transaction_date')
        ).join(
            Transaction, Security.id == Transaction.security_id
        ).filter(
            Transaction.transaction_date > date(2000, 1, 1)  # Exclude dummy dates
        ).group_by(
            Security.id, Security.symbol, Security.name
        ).order_by(
            Security.symbol
        ).all()
        
        # Filter to only show securities that are missing historical price data for their transaction period
        securities_needing_upload = []
        for security in securities_with_transactions:
            first_date = _normalize_date(security.first_transaction_date)
            current_year = datetime.now().year
            
            # Check if we have complete historical data from first transaction to current year
            has_complete_data = True
            
            # Check each year from first transaction year to current year
            for year in range(first_date.year, current_year + 1):
                year_start = date(year, 1, 1)
                year_end = date(year, 12, 31)
                
                # Check if we have any data for this year
                has_year_data = HistoricalPrice.query.filter(
                    HistoricalPrice.security_id == security.id,
                    HistoricalPrice.date >= year_start,
                    HistoricalPrice.date <= year_end
                ).first() is not None
                
                if not has_year_data:
                    has_complete_data = False
                    break
            
            if not has_complete_data:
                securities_needing_upload.append(security)
        
        securities_with_transactions = securities_needing_upload
        
        # Get historical price counts per security
        historical_counts = db.session.query(
            HistoricalPrice.security_id,
            func.count(HistoricalPrice.id).label('count'),
            func.min(HistoricalPrice.date).label('earliest_date'),
            func.max(HistoricalPrice.date).label('latest_date')
        ).group_by(
            HistoricalPrice.security_id
        ).all()
        
        historical_counts_dict = {
            row.security_id: {
                'count': row.count,
                'earliest_date': row.earliest_date,
                'latest_date': row.latest_date
            }
            for row in historical_counts
        }
        
        # Prepare response
        status_data = []
        for security in securities_with_transactions:
            historical_info = historical_counts_dict.get(security.id, {
                'count': 0,
                'earliest_date': None,
                'latest_date': None
            })
            
            status_data.append({
                'security_id': security.id,
                'symbol': security.symbol,
                'name': security.name,
                'first_transaction_date': security.first_transaction_date.isoformat() if security.first_transaction_date else None,
                'historical_count': historical_info['count'],
                'earliest_date': historical_info['earliest_date'].isoformat() if historical_info['earliest_date'] else None,
                'latest_date': historical_info['latest_date'].isoformat() if historical_info['latest_date'] else None,
                'has_historical_data': historical_info['count'] > 0
            })
        
        return jsonify({
            'success': True,
            'data': status_data
        })
        
    except Exception as e:
        logger.error(f"Error getting upload status: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500