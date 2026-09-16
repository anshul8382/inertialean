"""Heavy client-detail sections loaded on demand (expand to calculate)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from models import BenchmarkData, Client

logger = logging.getLogger(__name__)


def nifty_benchmark_context(client_id: int, current_value: float = 0.0, portfolio_xirr: float = 0.0) -> Dict[str, Any]:
    """Run Nifty benchmark simulation for client detail partial."""
    from services.benchmark_service import BenchmarkService

    ctx: Dict[str, Any] = {
        "client_id": client_id,
        "current_value": current_value,
        "xirr": portfolio_xirr,
        "nifty_xirr": 0.0,
        "nifty_current_value": 0.0,
        "nifty_absolute_return": 0.0,
        "nifty_benchmark_breakdown": None,
        "nifty_index_price": None,
        "nifty_index_date": None,
        "error": None,
    }
    try:
        row = BenchmarkData.query.filter_by(benchmark_id=1).order_by(BenchmarkData.date.desc()).first()
        if row:
            ctx["nifty_index_price"] = float(row.price)
            ctx["nifty_index_date"] = row.date
    except Exception as exc:
        logger.debug("Latest Nifty row: %s", exc)

    try:
        result = BenchmarkService.calculate_benchmark_xirr(client_id)
        ctx["nifty_xirr"] = result.get("nifty_xirr", 0.0)
        ctx["nifty_current_value"] = result.get("nifty_current_value", 0.0)
        ctx["nifty_absolute_return"] = result.get("nifty_absolute_return", 0.0)
        if result.get("success"):
            ctx["nifty_benchmark_breakdown"] = result
        else:
            ctx["error"] = result.get("error") or "Benchmark calculation failed"
    except Exception as exc:
        logger.exception("Nifty benchmark for client %s", client_id)
        ctx["error"] = str(exc)
    return ctx


def imputed_dividends_context(client_id: int) -> Dict[str, Any]:
    from services.imputed_dividend_service import compute_imputed_dividends

    try:
        data = compute_imputed_dividends(client_id)
        data["deferred"] = False
        return data
    except Exception as exc:
        logger.exception("Imputed dividends for client %s", client_id)
        return {
            "rows": [],
            "by_year": {},
            "grand_total": 0.0,
            "meta": {"eligibility_note": f"Could not compute: {exc}", "start_date": None, "end_date": None},
            "deferred": False,
            "error": str(exc),
        }


def calendar_year_xirr_context(client_id: int) -> Dict[str, Any]:
    from services.calendar_year_xirr_service import CalendarYearXirrService

    try:
        return CalendarYearXirrService.calculate(client_id)
    except Exception as exc:
        logger.exception("Calendar-year XIRR for client %s", client_id)
        return {"success": False, "rows": [], "error": str(exc)}


def portfolio_xirr_and_value(client_id: int) -> tuple[float, float]:
    """Lightweight portfolio value + XIRR for nifty partial (holdings + cashflows)."""
    from api.v1.performance import calculate_xirr
    from models import Cashflow, Holding
    from services.price_service import PriceService
    from models import Security

    current_value = 0.0
    holdings = Holding.query.filter_by(client_id=client_id).all()
    for holding in holdings:
        qty = float(holding.quantity or 0)
        if qty <= 0:
            continue
        sec = Security.query.get(holding.security_id)
        if not sec:
            continue
        try:
            pd = PriceService.get_price(sec.id)
            price = float(pd.price) if getattr(pd, "is_valid", False) and pd.price else 0.0
        except Exception:
            price = float(sec.current_price or 0)
        current_value += qty * price

    cfs = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
    xirr = 0.0
    if cfs:
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cfs]
        xirr, _, _, _, _ = calculate_xirr(cashflow_data, float(current_value or 0))
    return current_value, xirr
