"""
Shared query helpers for Transactions V2.

Goal: keep a single source of truth for how we filter transactions by date/type/security.
Both the V2 Transactions API and other modules (e.g. Period Analysis V2) should use this.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from models import Transaction


def _to_start_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time())


def _to_end_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.max.time())


def build_transactions_v2_query(
    *,
    client_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    transaction_type: str = "",
    security_id: Optional[int] = None,
):
    """
    Build the canonical V2 transactions query.

    Important: date filters are inclusive and treat date_from/date_to as whole-day bounds.
    """
    query = Transaction.query.filter_by(client_id=client_id)

    if transaction_type:
        query = query.filter_by(type=transaction_type)

    if security_id:
        query = query.filter_by(security_id=security_id)

    if date_from:
        query = query.filter(Transaction.transaction_date >= _to_start_of_day(date_from))

    if date_to:
        query = query.filter(Transaction.transaction_date <= _to_end_of_day(date_to))

    return query




