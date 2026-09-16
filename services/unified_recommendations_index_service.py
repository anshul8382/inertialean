"""GET filters for the unified recommendations index (client, dates, status, type)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, List, Optional

INDEX_STATUS_CHOICES = ("pending", "sent", "accepted", "executed", "cancelled")
INDEX_TYPE_CHOICES = ("adhoc", "rebalancing", "new_investment", "withdrawal")


def parse_unified_index_filters(args) -> dict:
    """Parse GET filters for the unified recommendations index."""
    raw_status = (args.get("status") or "").strip().lower()
    raw_type = (args.get("session_type") or args.get("type") or "").strip().lower()
    q = (args.get("q") or "").strip()
    client_id = args.get("client_id", type=int)
    date_from = None
    date_to = None
    df = (args.get("date_from") or "").strip()
    dt = (args.get("date_to") or "").strip()
    if df:
        try:
            date_from = datetime.strptime(df[:10], "%Y-%m-%d").date()
        except ValueError:
            date_from = None
    if dt:
        try:
            date_to = datetime.strptime(dt[:10], "%Y-%m-%d").date()
        except ValueError:
            date_to = None
    return {
        "client_id": client_id if client_id else None,
        "q": q or None,
        "status": raw_status if raw_status in INDEX_STATUS_CHOICES else None,
        "session_type": raw_type if raw_type in INDEX_TYPE_CHOICES else None,
        "date_from": date_from,
        "date_to": date_to,
    }


def filter_unified_index_rows(
    rows: Iterable[Any],
    *,
    status: Optional[str] = None,
    date_from=None,
    date_to=None,
    client_id: Optional[int] = None,
    client_q: Optional[str] = None,
) -> List[Any]:
    """Apply display-level filters (status / visible date / client)."""
    qn = (client_q or "").strip().lower()
    out: List[Any] = []
    for row in rows:
        if status and (getattr(row, "display_status", None) or "").strip().lower() != status:
            continue
        if client_id and getattr(row, "client_id", None) != client_id:
            continue
        if qn:
            client = getattr(row, "client", None)
            name = (getattr(client, "name", None) or "") if client else ""
            if qn not in name.lower():
                continue
        disp = getattr(row, "display_date", None)
        if date_from or date_to:
            if not disp:
                continue
            d = disp.date() if hasattr(disp, "date") else disp
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
        out.append(row)
    return out
