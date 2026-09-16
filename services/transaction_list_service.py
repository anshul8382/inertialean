"""V2 transaction list filters, buy/sell summary, and traded-security lookup."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from extensions import db
from models import Security, Transaction

_MONEY = Decimal("0.01")


def _as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value: Decimal) -> float:
    return float(value.quantize(_MONEY, rounding=ROUND_HALF_UP))


def _row_parts(row: Any) -> Tuple[str, Decimal, Decimal]:
    if isinstance(row, (tuple, list)) or hasattr(row, "_mapping"):
        typ = row[0]
        qty = row[1]
        price = row[2]
    else:
        typ = getattr(row, "type", "")
        qty = getattr(row, "quantity", 0)
        price = getattr(row, "price", 0)
    return (typ or ""), _as_decimal(qty), _as_decimal(price)


def summarize_trade_amounts(rows: Iterable[Any]) -> dict:
    """Buy/sell totals from (type, quantity, price) rows. Amount = qty × price."""
    buy_count = 0
    sell_count = 0
    other_count = 0
    buy_amount = Decimal("0")
    sell_amount = Decimal("0")
    transaction_count = 0

    for row in rows:
        transaction_count += 1
        typ, qty, price = _row_parts(row)
        amount = qty * price
        key = typ.strip().upper()
        if key == "BUY":
            buy_count += 1
            buy_amount += amount
        elif key == "SELL":
            sell_count += 1
            sell_amount += amount
        else:
            other_count += 1

    return {
        "transaction_count": transaction_count,
        "buy_count": buy_count,
        "buy_amount": _money(buy_amount),
        "sell_count": sell_count,
        "sell_amount": _money(sell_amount),
        "net_amount": _money(buy_amount - sell_amount),
        "other_count": other_count,
    }


def parse_v2_list_filters(raw: Mapping[str, Any]) -> Tuple[dict, Optional[str]]:
    """
    Parse V2 list query args into kwargs for build_transactions_v2_query
    (excluding client_id). Returns (filters, error_message).
    """
    filters: dict = {
        "transaction_type": "",
        "security_id": None,
        "date_from": None,
        "date_to": None,
    }

    type_raw = raw.get("transaction_type")
    if type_raw is not None:
        filters["transaction_type"] = str(type_raw).strip().upper()

    security_raw = raw.get("security_id")
    if security_raw not in (None, ""):
        try:
            filters["security_id"] = int(security_raw)
        except (TypeError, ValueError):
            return {}, "Invalid security_id"

    date_from, err = _parse_iso_date(raw.get("date_from"), "date_from")
    if err:
        return {}, err
    date_to, err = _parse_iso_date(raw.get("date_to"), "date_to")
    if err:
        return {}, err
    filters["date_from"] = date_from
    filters["date_to"] = date_to
    return filters, None


def _parse_iso_date(value: Any, field: str) -> Tuple[Optional[date], Optional[str]]:
    if value in (None, ""):
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"Invalid {field} format. Use YYYY-MM-DD"


def summarize_filtered_query(query) -> dict:
    """Apply the same formula to every row matching the filtered query (not just one page)."""
    rows = query.with_entities(
        Transaction.type,
        Transaction.quantity,
        Transaction.price,
    ).all()
    return summarize_trade_amounts(rows)


def list_traded_securities(client_id: int, session=None) -> Sequence[dict]:
    """Distinct securities the client has traded, ordered by symbol."""
    sess = session or db.session
    rows = (
        sess.query(Security.id, Security.symbol, Security.name)
        .join(Transaction, Transaction.security_id == Security.id)
        .filter(Transaction.client_id == client_id)
        .distinct()
        .order_by(Security.symbol.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "symbol": row.symbol or "",
            "name": row.name or "",
        }
        for row in rows
    ]
