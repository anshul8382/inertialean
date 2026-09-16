"""
Holdings Report Service

Computes holding quantities split into short-term (bought in last 1 year) and long-term (rest)
using FIFO lot tracking over transactions and corporate actions (SPLIT, BONUS).
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
import math
import logging

logger = logging.getLogger(__name__)

# Lots are (purchase_date, quantity) — quantity can be float after SPLIT
def _to_date(d) -> date:
    if d is None:
        return date.today()
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    if hasattr(d, "date"):
        return d.date()
    return d


def get_short_long_term_quantities(
    client_id: int,
    security_id: int,
    as_of_date: Optional[date] = None,
    short_term_cutoff_days: int = 365,
    db_session=None,
) -> Dict[str, Any]:
    """
    Compute current holding quantity split into short-term and long-term using FIFO.

    Short-term = quantity from lots purchased within the last `short_term_cutoff_days` (default 1 year).
    Long-term = remaining quantity.

    Returns:
        {
            "short_term_quantity": float,
            "long_term_quantity": float,
            "total_quantity": float,
            "short_term_cutoff_date": date,
            "as_of_date": date,
        }
    """
    as_of_date = as_of_date or date.today()
    cutoff_date = as_of_date - timedelta(days=short_term_cutoff_days)

    try:
        from models import Transaction, CorporateAction

        if db_session is not None:
            transactions = db_session.query(Transaction).filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.transaction_date <= as_of_date,
            ).order_by(Transaction.transaction_date).all()
        else:
            transactions = Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.security_id == security_id,
                Transaction.transaction_date <= as_of_date,
            ).order_by(Transaction.transaction_date).all()

        # Corporate actions (SPLIT, BONUS only for lot tracking; MERGER/DEMERGER skipped for simplicity)
        if db_session is not None:
            corporate_actions = db_session.query(CorporateAction).filter(
                CorporateAction.security_id == security_id,
                CorporateAction.action_date <= as_of_date,
                CorporateAction.is_active == True,
                CorporateAction.action_type.in_(("SPLIT", "BONUS")),
            ).order_by(CorporateAction.action_date).all()
        else:
            corporate_actions = CorporateAction.query.filter(
                CorporateAction.security_id == security_id,
                CorporateAction.action_date <= as_of_date,
                CorporateAction.is_active == True,
                CorporateAction.action_type.in_(("SPLIT", "BONUS")),
            ).order_by(CorporateAction.action_date).all()

        # Build events (date, type, payload)
        events: List[Tuple[date, str, Any]] = []
        for t in transactions:
            d = _to_date(t.transaction_date)
            events.append((d, "transaction", t))
        for ca in corporate_actions:
            d = _to_date(ca.action_date)
            events.append((d, "corporate_action", ca))

        def event_priority(item):
            _, typ, payload = item
            if typ == "transaction" and getattr(payload, "type", None) == "SELL":
                return 0
            if typ == "transaction":
                return 1
            return 2

        events.sort(key=lambda x: (x[0], event_priority(x)))

        # FIFO lots: list of (purchase_date, quantity)
        lots: List[Tuple[date, float]] = []

        for event_date, event_type, event in events:
            if event_type == "transaction":
                if event.type == "BUY":
                    qty = float(event.quantity)
                    lots.append((event_date, qty))
                elif event.type == "SELL":
                    to_sell = float(event.quantity)
                    while to_sell > 0 and lots:
                        pd, q = lots[0]
                        if q <= to_sell:
                            to_sell -= q
                            lots.pop(0)
                        else:
                            lots[0] = (pd, q - to_sell)
                            to_sell = 0
            elif event_type == "corporate_action":
                if event.action_type == "SPLIT":
                    ratio = float(event.ratio)
                    lots = [(pd, q * ratio) for pd, q in lots]
                elif event.action_type == "BONUS":
                    # Section 55(2)(aa): bonus shares are a separate lot at action date (holding
                    # period for ST/LT starts from allotment), not merged into old purchase dates.
                    total_qty = sum(q for _, q in lots)
                    if total_qty > 0:
                        bonus_ratio = float(event.ratio)
                        bonus_shares = math.floor(total_qty * bonus_ratio)
                        if bonus_shares > 0:
                            lots = list(lots)
                            lots.append((event_date, float(bonus_shares)))

        short_term = sum(q for pd, q in lots if pd >= cutoff_date)
        long_term = sum(q for pd, q in lots if pd < cutoff_date)
        total = short_term + long_term

        return {
            "short_term_quantity": round(short_term, 4),
            "long_term_quantity": round(long_term, 4),
            "total_quantity": round(total, 4),
            "short_term_cutoff_date": cutoff_date,
            "as_of_date": as_of_date,
        }
    except Exception as e:
        logger.exception("get_short_long_term_quantities failed for client_id=%s security_id=%s", client_id, security_id)
        return {
            "short_term_quantity": 0.0,
            "long_term_quantity": 0.0,
            "total_quantity": 0.0,
            "short_term_cutoff_date": cutoff_date,
            "as_of_date": as_of_date,
            "error": str(e),
        }


def get_holdings_report_rows(
    client_name_filter: Optional[str] = None,
    security_name_filter: Optional[str] = None,
    mask_sensitive: bool = False,
) -> List[Dict[str, Any]]:
    """
    Build list of holding rows for the tools report: client, security, total/short/long quantities.
    Applies optional filters on client name and security name (case-insensitive partial match).
    """
    from extensions import db
    from models import Holding, Client, Security
    from services.pii_masking import mask_client_name

    q = (
        db.session.query(Holding, Client, Security)
        .join(Client, Holding.client_id == Client.id)
        .join(Security, Holding.security_id == Security.id)
    )
    if client_name_filter and client_name_filter.strip():
        q = q.filter(Client.name.ilike(f"%{client_name_filter.strip()}%"))
    if security_name_filter and security_name_filter.strip():
        q = q.filter(
            Security.name.ilike(f"%{security_name_filter.strip()}%")
            | Security.symbol.ilike(f"%{security_name_filter.strip()}%")
        )
    rows_data = q.order_by(Client.name, Security.name).all()

    result = []
    for holding, client, security in rows_data:
        stl = get_short_long_term_quantities(holding.client_id, holding.security_id, db_session=db.session)
        result.append({
            "client_id": client.id,
            "client_name": mask_client_name(client.name) if mask_sensitive else client.name,
            "security_id": security.id,
            "security_name": security.name,
            "security_symbol": security.symbol,
            "total_quantity": "***" if mask_sensitive else float(holding.quantity),
            "average_price": "***" if mask_sensitive else float(holding.average_price),
            "short_term_quantity": "***" if mask_sensitive else stl["short_term_quantity"],
            "long_term_quantity": "***" if mask_sensitive else stl["long_term_quantity"],
            "short_term_cutoff_date": stl.get("short_term_cutoff_date"),
        })
    return result
