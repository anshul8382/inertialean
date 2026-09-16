"""
Effective period price return for best/worst performer rankings.

For holdings held at period start: period-start adjusted market price.
For shares bought during the period: VWAP of period BUY transactions,
restated for splits/bonuses onto the period-end share basis.
For increased positions: quantity-weighted blend of the two (not calendar-period
market move from a date before the client owned the incremental shares).
When start_qty > 0, same-day start buys are excluded from VWAP (already in start).

End price: period-end adjusted market for positions still held; last SELL date
adjusted market (or sell VWAP fallback) when the position is exited during the
period — aligned with Section 2D portfolio holdings comparison.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _txn_date(txn: Any) -> Optional[date]:
    raw = getattr(txn, "transaction_date", None)
    if raw is None:
        return None
    if hasattr(raw, "date"):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return None


def vwap_from_sell_transactions(sell_txns: List[Any]) -> Tuple[float, float]:
    """Return (total_quantity, vwap) for SELL transactions."""
    total_qty = 0.0
    total_amount = 0.0
    for txn in sell_txns or []:
        qty = float(txn.quantity or 0)
        price = float(txn.price or 0)
        if qty <= 0:
            continue
        total_qty += qty
        total_amount += qty * price
    if total_qty <= 0:
        return 0.0, 0.0
    return total_qty, total_amount / total_qty


def vwap_from_buy_transactions(buy_txns: List[Any]) -> Tuple[float, float]:
    """Return (total_quantity, vwap) for BUY transactions."""
    total_qty = 0.0
    total_amount = 0.0
    for txn in buy_txns or []:
        qty = float(txn.quantity or 0)
        price = float(txn.price or 0)
        if qty <= 0:
            continue
        total_qty += qty
        total_amount += qty * price
    if total_qty <= 0:
        return 0.0, 0.0
    return total_qty, total_amount / total_qty


def vwap_from_transactions_ca_adjusted(
    txns: List[Any],
    security_id: int,
    through_date: date,
    corporate_actions: Optional[List[Any]] = None,
) -> Tuple[float, float]:
    """
    VWAP with each trade's price restated to ``through_date`` share basis.

    Transaction rows store pre-split/bonus prices. Market end/start prices for
    rankings are restated via corporate_actions — VWAP must use the same basis
    or blended returns mix incompatible levels (e.g. ₹2,050 with ₹378).
    """
    from services.corporate_action_price_restatement import (
        lookup_corporate_actions_for_security,
        restatement_factor_from_actions,
    )

    total_qty = 0.0
    total_amount = 0.0
    actions = corporate_actions
    if actions is None:
        # Load CAs from earliest possible txn day through end (caller may pass a cache).
        earliest: Optional[date] = None
        for txn in txns or []:
            d = _txn_date(txn)
            if d and (earliest is None or d < earliest):
                earliest = d
        if earliest is None:
            return 0.0, 0.0
        actions = lookup_corporate_actions_for_security(
            security_id, earliest - timedelta(days=1), through_date
        )

    for txn in txns or []:
        qty = float(getattr(txn, "quantity", 0) or 0)
        price = float(getattr(txn, "price", 0) or 0)
        if qty <= 0 or price <= 0:
            continue
        txn_date = _txn_date(txn)
        if not txn_date:
            continue
        factor = restatement_factor_from_actions(
            actions, txn_date, through=through_date
        )
        total_qty += qty
        total_amount += qty * (price * factor)

    if total_qty <= 0:
        return 0.0, 0.0
    return total_qty, total_amount / total_qty


def filter_period_buy_txns_for_holder_return(
    buy_txns: List[Any],
    start_date: date,
    start_qty: float,
) -> List[Any]:
    """
    Buys that contribute to the incremental / new-money VWAP leg.

    When start_qty > 0, holdings as of start_date already include same-day buys
    (portfolio reconstruction uses txn_date <= as_of). Including those again in
    period VWAP double-counts weight and pulls start price toward raw trade
    prices. Use buys strictly after start_date in that case.
    """
    if float(start_qty or 0) <= 0:
        return list(buy_txns or [])
    out: List[Any] = []
    for txn in buy_txns or []:
        d = _txn_date(txn)
        if d and d > start_date:
            out.append(txn)
    return out


def compute_holder_period_price_return(
    *,
    period_start_market: float,
    period_end_market: float,
    start_qty: float,
    period_buy_qty: float,
    period_buy_vwap: float,
    end_avg_price: float = 0.0,
) -> Optional[Dict[str, float]]:
    """
    Compute display start price and % change for a holder's period experience.

    Returns None when period_end_market is missing/invalid or no valid start basis.
    """
    period_end_market = float(period_end_market or 0)
    if period_end_market <= 0:
        return None

    start_qty = max(0.0, float(start_qty or 0))
    period_buy_qty = max(0.0, float(period_buy_qty or 0))
    period_start_market = float(period_start_market or 0)
    period_buy_vwap = float(period_buy_vwap or 0)
    end_avg_price = float(end_avg_price or 0)

    if start_qty > 0 and period_buy_qty > 0 and period_start_market > 0 and period_buy_vwap > 0:
        effective_start = (
            start_qty * period_start_market + period_buy_qty * period_buy_vwap
        ) / (start_qty + period_buy_qty)
        return_basis = "blended"
    elif start_qty > 0 and period_start_market > 0:
        effective_start = period_start_market
        return_basis = "period_start_market"
    elif period_buy_qty > 0 and period_buy_vwap > 0:
        effective_start = period_buy_vwap
        return_basis = "period_purchase_vwap"
    elif end_avg_price > 0:
        effective_start = end_avg_price
        return_basis = "holding_average_cost"
    elif period_start_market > 0:
        effective_start = period_start_market
        return_basis = "period_start_market"
    else:
        return None

    price_change = period_end_market - effective_start
    price_percent_change = (
        (price_change / effective_start) * 100 if effective_start > 0 else 0.0
    )

    return {
        "start_price": effective_start,
        "end_price": period_end_market,
        "price_change": price_change,
        "price_percent_change": price_percent_change,
        "return_basis": return_basis,
        "period_start_market": period_start_market,
    }


def _holding_qty(holding: Optional[Dict[str, Any]]) -> float:
    if not holding:
        return 0.0
    return float(holding.get("quantity") or 0)


def _holding_avg_price(holding: Optional[Dict[str, Any]]) -> float:
    if not holding:
        return 0.0
    return float(holding.get("average_price") or 0)


def _period_sell_transactions(
    client_id: int, security_id: int, start_datetime: datetime, end_datetime: datetime
) -> List[Any]:
    from models import Transaction

    return (
        Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.security_id == security_id,
            Transaction.type == "SELL",
            Transaction.transaction_date >= start_datetime,
            Transaction.transaction_date <= end_datetime,
        )
        .order_by(Transaction.transaction_date.desc())
        .all()
    )


def _exit_date_from_sells(sell_txns: List[Any]) -> Optional[date]:
    """Last SELL date in the period (matches Section 2D exit handling)."""
    if not sell_txns:
        return None
    return _txn_date(sell_txns[0])


def should_use_exit_date_for_end_price(end_qty: float, sell_txns: List[Any]) -> bool:
    """Use exit-date pricing when flat at period end but sold during the period."""
    return float(end_qty or 0) <= 0 and bool(sell_txns)


def _adjusted_market_price(security_id: int, as_of) -> float:
    from services.price_service import PriceService

    data = PriceService.get_price(
        security_id, as_of, use_adjusted=True, allow_fallback=True
    )
    if data and data.is_valid and data.price:
        return float(data.price)
    return 0.0


def _resolve_market_prices(
    security_id: int,
    start_date,
    end_date_for_price,
    start_holding: Optional[Dict[str, Any]],
    end_holding: Optional[Dict[str, Any]],
) -> Tuple[float, float]:
    """
    Prefer corporate_actions-table restatement of raw closes when dilutive CAs
    exist in the period; otherwise PriceService use_adjusted=True.
    """
    try:
        from services.corporate_action_price_restatement import (
            resolve_period_prices_with_corporate_actions,
        )

        start_market, end_market = resolve_period_prices_with_corporate_actions(
            security_id, start_date, end_date_for_price
        )
    except Exception as e:
        logger.warning(
            "CA-aware period price resolve failed for security_id=%s: %s",
            security_id,
            e,
        )
        start_market = _adjusted_market_price(security_id, start_date)
        end_market = _adjusted_market_price(security_id, end_date_for_price)

    if start_market <= 0 and start_holding:
        start_market = float(start_holding.get("current_price") or 0)
    if end_market <= 0 and end_holding:
        end_market = float(end_holding.get("current_price") or 0)

    return start_market, end_market


def compute_security_period_price_return(
    client_id: int,
    security_id: int,
    start_date,
    end_date,
    start_holding: Optional[Dict[str, Any]] = None,
    end_holding: Optional[Dict[str, Any]] = None,
    *,
    start_quantity: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Full price-return metrics for one security in a review period.

    Optional start_quantity overrides start_holding quantity (MTM rows).
    """
    from models import Transaction

    if start_quantity is not None:
        start_qty = float(start_quantity or 0)
    else:
        start_qty = _holding_qty(start_holding)

    end_qty = _holding_qty(end_holding)
    end_avg = _holding_avg_price(end_holding)

    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    buy_txns = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.security_id == security_id,
        Transaction.type == "BUY",
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime,
    ).all()
    sell_txns = _period_sell_transactions(
        client_id, security_id, start_datetime, end_datetime
    )

    end_date_for_price = end_date
    end_price_basis = "period_end_market"
    exit_date = None
    if should_use_exit_date_for_end_price(end_qty, sell_txns):
        exit_date = _exit_date_from_sells(sell_txns)
        if exit_date:
            end_date_for_price = exit_date
            end_price_basis = "exit_date_market"

    # Incremental buys only (avoid double-counting start-date lots already in start_qty).
    buy_txns_for_vwap = filter_period_buy_txns_for_holder_return(
        buy_txns, start_date, start_qty
    )
    # Restate trade prices onto end share basis (same as adjusted market prices).
    period_buy_qty, period_buy_vwap = vwap_from_transactions_ca_adjusted(
        buy_txns_for_vwap, security_id, end_date_for_price
    )
    period_sell_qty, period_sell_vwap_raw = vwap_from_sell_transactions(sell_txns)
    period_sell_qty_adj, period_sell_vwap = vwap_from_transactions_ca_adjusted(
        sell_txns, security_id, end_date_for_price
    )
    if period_sell_qty_adj > 0:
        period_sell_qty = period_sell_qty_adj
    else:
        period_sell_vwap = period_sell_vwap_raw

    start_market, end_market = _resolve_market_prices(
        security_id,
        start_date,
        end_date_for_price,
        start_holding,
        end_holding,
    )
    if end_market <= 0 and period_sell_vwap > 0 and end_qty <= 0:
        end_market = period_sell_vwap
        end_price_basis = "exit_sell_vwap"
    if end_market <= 0:
        return None

    metrics = compute_holder_period_price_return(
        period_start_market=start_market,
        period_end_market=end_market,
        start_qty=start_qty,
        period_buy_qty=period_buy_qty,
        period_buy_vwap=period_buy_vwap,
        end_avg_price=end_avg,
    )
    if not metrics:
        return None

    metrics["period_buy_qty"] = period_buy_qty
    metrics["period_buy_vwap"] = period_buy_vwap
    metrics["period_sell_qty"] = period_sell_qty
    metrics["period_sell_vwap"] = period_sell_vwap
    metrics["end_price_basis"] = end_price_basis
    metrics["effective_end_date"] = (
        exit_date.isoformat() if exit_date else end_date.isoformat()
    )
    return metrics


def period_net_investment(
    client_id: int, security_id: int, start_date, end_date
) -> float:
    """Net BUY - SELL cash in the period for value_contribution."""
    from models import Transaction

    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    buy_txns = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.security_id == security_id,
        Transaction.type == "BUY",
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime,
    ).all()
    sell_txns = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.security_id == security_id,
        Transaction.type == "SELL",
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date <= end_datetime,
    ).all()

    total_buy = sum(float(t.quantity) * float(t.price) for t in buy_txns)
    total_sell = sum(float(t.quantity) * float(t.price) for t in sell_txns)
    return total_buy - total_sell
