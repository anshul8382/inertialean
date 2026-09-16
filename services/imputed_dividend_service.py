"""
Imputed (gross) dividends from corporate action DIVIDEND rows × reconstructed holdings.

Eligible quantity is holdings as of the last weekday strictly before action_date (ex-date),
matching common Indian equity convention (buy on ex-date does not receive the dividend).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _eligibility_date_for_ex_date(ex_date: date) -> date:
    """Last calendar day before ex_date, then skip backward over weekend days only."""
    d = ex_date - timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d


def parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s or not str(s).strip():
        return None
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def compute_imputed_dividends(
    client_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Returns:
      rows: list of dicts (sorted by ex date, symbol)
      by_year: { year: total float }
      grand_total: float
      meta: { start_date, end_date, eligibility_note }
    """
    from models import CorporateAction, Security, Transaction
    from extensions import db
    from services.cashflow_service import DUMMY_DATES
    from services.corporate_action_rights_detection import text_suggests_rights_issue
    from services.forward_holding_calculation_service import get_holding_quantity_by_date

    # Default range: first non-dummy transaction date → today
    today = date.today()
    txn_dates = []
    for row in (
        Transaction.query.filter(Transaction.client_id == client_id)
        .with_entities(Transaction.transaction_date)
        .all()
    ):
        td = row[0]
        txn_dates.append(td.date() if hasattr(td, "date") else td)
    real_dates = [d for d in txn_dates if d not in DUMMY_DATES]
    if not real_dates:
        return {
            "rows": [],
            "by_year": {},
            "grand_total": 0.0,
            "meta": {
                "start_date": None,
                "end_date": None,
                "eligibility_note": "No transactions for this client.",
            },
        }

    default_start = min(real_dates)

    eff_start = start_date or default_start
    eff_end = end_date or today
    if eff_start > eff_end:
        eff_start, eff_end = eff_end, eff_start

    security_ids = [
        r[0]
        for r in db.session.query(Transaction.security_id)
        .filter(Transaction.client_id == client_id)
        .distinct()
        .all()
    ]
    if not security_ids:
        return {
            "rows": [],
            "by_year": {},
            "grand_total": 0.0,
            "meta": {
                "start_date": eff_start.isoformat(),
                "end_date": eff_end.isoformat(),
                "eligibility_note": "No security_ids in transaction history.",
            },
        }

    div_actions = (
        CorporateAction.query.filter(
            CorporateAction.security_id.in_(security_ids),
            CorporateAction.action_type == "DIVIDEND",
            CorporateAction.is_active.is_(True),
            CorporateAction.action_date >= eff_start,
            CorporateAction.action_date <= eff_end,
        )
        .order_by(CorporateAction.action_date, CorporateAction.security_id, CorporateAction.id)
        .all()
    )

    sec_cache: Dict[int, Security] = {}
    rows: List[Dict[str, Any]] = []
    by_year: Dict[int, float] = defaultdict(float)

    for ca in div_actions:
        if text_suggests_rights_issue(getattr(ca, "description", None) or ""):
            continue
        sid = int(ca.security_id)
        ex_dt = ca.action_date
        if hasattr(ex_dt, "date"):
            ex_dt = ex_dt.date()
        elig = _eligibility_date_for_ex_date(ex_dt)

        try:
            hq = get_holding_quantity_by_date(client_id, sid, elig)
            qty = float(hq.get("quantity") or 0.0)
        except Exception as e:
            logger.warning(
                "Imputed dividend: holding lookup failed client=%s security=%s as_of=%s: %s",
                client_id,
                sid,
                elig,
                e,
            )
            qty = 0.0

        per_share = float(ca.ratio or 0)
        gross = qty * per_share
        if gross <= 0:
            continue

        if sid not in sec_cache:
            sec_cache[sid] = Security.query.get(sid)
        sec = sec_cache[sid]
        symbol = sec.symbol if sec else str(sid)
        name = (sec.name if sec else "") or ""

        y = int(ex_dt.year)
        by_year[y] += gross

        rows.append(
            {
                "corporate_action_id": ca.id,
                "security_id": sid,
                "symbol": symbol,
                "security_name": name,
                "ex_date": ex_dt.isoformat(),
                "eligibility_date": elig.isoformat(),
                "per_share": per_share,
                "eligible_quantity": qty,
                "gross_amount": round(gross, 2),
                "description": (ca.description or "")[:255],
            }
        )

    grand_total = sum(r["gross_amount"] for r in rows)
    by_year_out = {str(k): round(v, 2) for k, v in sorted(by_year.items())}

    return {
        "rows": rows,
        "by_year": by_year_out,
        "grand_total": round(grand_total, 2),
        "meta": {
            "start_date": eff_start.isoformat(),
            "end_date": eff_end.isoformat(),
            "eligibility_note": (
                "Gross dividend = eligible quantity × per-share rate. "
                "Eligible quantity is forward-reconstructed holdings on the last weekday "
                "before ex-date (corporate action date)."
            ),
        },
    }
