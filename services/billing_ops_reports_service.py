"""
Companion ops reports for Billing Manager Plan.

Reuses / complements existing surfaces:
- Reviews: no dedicated “on track < 7 months” list elsewhere (dashboard has overdue/upcoming).
- Billing slip: overlaps Billing Manager “Lagging” (enriched here with days since last bill).
- Missing agreements: same population as IA statement / Practice Analytics
  (`live_missing_agreement_review`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import func

from models import Client, ReviewWorkflow


REVIEW_ON_TRACK_MONTHS = 7


def _coerce_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _months_between(earlier: date, later: date) -> float:
    """Approximate calendar months between two dates (day-aware)."""
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    # fractional hint via days in residual month — keep int-ish for threshold
    return float(months)


def _last_closed_review_by_client(
    client_ids: Sequence[int],
) -> Dict[int, date]:
    if not client_ids:
        return {}
    rows = (
        ReviewWorkflow.query.filter(
            ReviewWorkflow.client_id.in_(list(client_ids)),
            ReviewWorkflow.status == "closed",
        )
        .with_entities(
            ReviewWorkflow.client_id,
            func.max(ReviewWorkflow.review_date),
        )
        .group_by(ReviewWorkflow.client_id)
        .all()
    )
    out: Dict[int, date] = {}
    for cid, rd in rows:
        d = _coerce_date(rd)
        if cid is not None and d is not None:
            out[int(cid)] = d
    return out


def build_reviews_on_track(
    *,
    accessible_client_ids: Sequence[int],
    as_of: Optional[date] = None,
    max_months: int = REVIEW_ON_TRACK_MONTHS,
) -> Dict[str, Any]:
    """Clients whose last closed review is within ``max_months`` (default 7)."""
    as_of = as_of or date.today()
    ids = [int(i) for i in accessible_client_ids if i]
    if not ids:
        return {
            "max_months": max_months,
            "as_of": as_of.isoformat(),
            "rows": [],
            "count": 0,
            "source_note": "Last closed ReviewWorkflow.review_date within threshold.",
        }

    clients = (
        Client.query.filter(Client.id.in_(ids), Client.is_active.is_(True))
        .order_by(Client.name)
        .all()
    )
    last_by = _last_closed_review_by_client([c.id for c in clients])
    rows: List[Dict[str, Any]] = []
    for cl in clients:
        last = last_by.get(cl.id)
        if not last:
            continue
        months = _months_between(last, as_of)
        if months < 0:
            continue
        if months <= max_months:
            rows.append(
                {
                    "client_id": cl.id,
                    "client_name": cl.name,
                    "last_review_date": last.isoformat(),
                    "months_since_review": round(months, 1),
                    "days_since_review": (as_of - last).days,
                }
            )
    rows.sort(key=lambda r: (r["months_since_review"], r["client_name"] or ""))
    return {
        "max_months": max_months,
        "as_of": as_of.isoformat(),
        "rows": rows,
        "count": len(rows),
        "source_note": (
            "New list — not previously a dedicated report. "
            "Related: dashboard overdue/upcoming reviews; review schedules."
        ),
    }


def build_billing_slipping(
    *,
    as_of: Optional[date] = None,
    lagging_rows: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Clients lagging on billing, with days since last billed.

    Prefers Billing Manager ``status_report.lagging`` when provided (same engine).
    """
    as_of = as_of or date.today()
    rows: List[Dict[str, Any]] = []
    for r in lagging_rows or []:
        last = _coerce_date(r.get("last_billed_date"))
        if last is None and isinstance(r.get("last_billed_date"), str):
            try:
                last = date.fromisoformat(r["last_billed_date"])
            except ValueError:
                last = None
        days_since = (as_of - last).days if last else None
        rows.append(
            {
                "client_id": r.get("client_id"),
                "client_name": r.get("client_name"),
                "household_name": r.get("household_name"),
                "agreement_id": r.get("agreement_id"),
                "last_billed_date": r.get("last_billed_date"),
                "next_billing_date": r.get("next_billing_date"),
                "days_overdue": r.get("days_overdue", 0),
                "days_since_last_billing": days_since,
                "estimated_invoice_value": r.get("estimated_invoice_value", 0),
                "outstanding_unpaid": r.get("outstanding_unpaid", 0),
            }
        )
    rows.sort(
        key=lambda x: (
            -(x.get("days_since_last_billing") or 0),
            -(x.get("days_overdue") or 0),
            x.get("client_name") or "",
        )
    )
    return {
        "as_of": as_of.isoformat(),
        "rows": rows,
        "count": len(rows),
        "source_note": (
            "Same population as Billing Manager → Lagging (household-aware). "
            "Adds days since last billing."
        ),
    }


def build_missing_agreements_for_slippage(
    *,
    accessible_client_ids: Sequence[int],
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Active clients with no signed agreement — cannot judge billing slippage from schedules.

    Reuses IA statement helper ``live_missing_agreement_review``.
    """
    as_of = as_of or date.today()
    allowed: Set[int] = {int(i) for i in accessible_client_ids if i}
    try:
        from services.ia_regulatory_statement_service import live_missing_agreement_review

        raw = live_missing_agreement_review(as_of=as_of)
    except Exception as exc:
        return {
            "as_of": as_of.isoformat(),
            "rows": [],
            "count": 0,
            "error": str(exc),
            "source_note": "IA Regulatory Statement / Practice Analytics missing-agreement list.",
        }

    rows = []
    for r in raw:
        cid = r.get("client_id")
        if allowed and cid not in allowed:
            continue
        rows.append(
            {
                "client_id": cid,
                "client_name": r.get("client_name"),
                "notes": r.get("notes") or "No signed agreement",
                "bucket": r.get("bucket"),
            }
        )
    rows.sort(key=lambda x: (x.get("client_name") or "").lower())
    return {
        "as_of": as_of.isoformat(),
        "rows": rows,
        "count": len(rows),
        "source_note": (
            "Reuses IA Regulatory Statement → Missing agreement "
            "(also reconciled in Practice Analytics). Filtered to your accessible clients."
        ),
    }


def build_billing_ops_reports(
    *,
    accessible_client_ids: Sequence[int],
    as_of: Optional[date] = None,
    status_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Bundle the three companion reports for the Billing Manager Plan UI."""
    as_of = as_of or date.today()
    lagging = (status_report or {}).get("lagging") or []
    reviews = build_reviews_on_track(
        accessible_client_ids=accessible_client_ids, as_of=as_of
    )
    slipping = build_billing_slipping(as_of=as_of, lagging_rows=lagging)
    missing = build_missing_agreements_for_slippage(
        accessible_client_ids=accessible_client_ids, as_of=as_of
    )
    return {
        "as_of": as_of.isoformat(),
        "reviews_on_track": reviews,
        "billing_slipping": slipping,
        "missing_agreements": missing,
        "summary": {
            "reviews_on_track": reviews["count"],
            "billing_slipping": slipping["count"],
            "missing_agreements": missing["count"],
        },
    }
