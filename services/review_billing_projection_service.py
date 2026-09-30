"""
Review cashflow projection — next 12 months of review dues with agreement
fee estimates on current portfolio values.

Audience: anshul@equities4wealth.com only (UI + Sunday email).
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

OPEN_STATUSES = frozenset({"initiated", "sent", "meeting"})
ALLOWED_EMAIL = "anshul@equities4wealth.com"
HORIZON_MONTHS = 12


def user_may_view_review_cashflow(user=None) -> bool:
    """Hard gate: login email must be anshul@equities4wealth.com."""
    if user is None:
        try:
            from flask_login import current_user

            user = current_user
        except Exception:
            return False
    if not user or not getattr(user, "is_authenticated", False):
        return False
    email = (getattr(user, "email", None) or "").strip().lower()
    return email == ALLOWED_EMAIL


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, 1)


def month_windows(today: Optional[date] = None, months: int = HORIZON_MONTHS) -> List[Tuple[date, date, str]]:
    """List of (month_start, month_end, 'YYYY-MM') for current month + next N-1."""
    today = today or date.today()
    start0 = _month_start(today)
    out: List[Tuple[date, date, str]] = []
    for i in range(max(1, months)):
        start = _add_months(start0, i)
        end = _month_end(start)
        out.append((start, end, start.strftime("%Y-%m")))
    return out


def _status(workflow) -> str:
    return (getattr(workflow, "status", None) or "").strip().lower()


def _is_open(workflow) -> bool:
    return _status(workflow) in OPEN_STATUSES


def _client_id(workflow) -> Any:
    cid = getattr(workflow, "client_id", None)
    if cid is not None:
        return cid
    client = getattr(workflow, "client", None)
    return getattr(client, "id", None)


def _pick_primary_for_month(workflows: List[Any]) -> Optional[Any]:
    """Earliest dated open review in the month (one per client)."""
    dated = [w for w in workflows if getattr(w, "review_date", None)]
    if not dated:
        return None
    dated.sort(
        key=lambda w: (
            w.review_date,
            getattr(w, "id", 0) or 0,
        )
    )
    return dated[0]


def _estimate_for_client(client_id: int, as_of: date, cache: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    empty = {
        "estimated_fee": 0.0,
        "next_billing_date": None,
        "billing_frequency": None,
        "agreement_id": None,
        "estimate_error": "No active agreement",
        "has_estimate": False,
    }
    if cache.get("__loaded__"):
        return cache.get(client_id, empty)

    from models import Agreement, Lead
    from sqlalchemy import func
    from sqlalchemy.orm import joinedload
    from services.billing_manager_service import BillingManagerService

    agreements = (
        Agreement.query.options(
            joinedload(Agreement.lead).joinedload(Lead.client),
            joinedload(Agreement.variables),
            joinedload(Agreement.billing_schedules),
        )
        .filter(func.lower(Agreement.status).in_(list(BillingManagerService.ACTIVE_AGREEMENT_STATUSES)))
        .all()
    )
    by_client: Dict[int, Dict[str, Any]] = {}
    for ag in agreements:
        opp = BillingManagerService._build_opportunity(ag, as_of)
        cid = opp.get("client_id")
        if cid is None:
            continue
        est = float(opp.get("estimated_invoice_value") or 0.0)
        prev = by_client.get(cid)
        if prev is None or est > float(prev.get("estimated_fee") or 0.0):
            err = opp.get("estimate_error")
            if not err and opp.get("has_hard_block") and est <= 0:
                flags = opp.get("flags") or []
                err = (
                    "; ".join(f.get("message") or f.get("code") or "" for f in flags)
                    or "Estimate blocked"
                )
            by_client[int(cid)] = {
                "estimated_fee": round(est, 2),
                "next_billing_date": opp.get("next_billing_date"),
                "billing_frequency": opp.get("billing_frequency"),
                "agreement_id": opp.get("agreement_id"),
                "estimate_error": err,
                "has_estimate": bool(est > 0 and not err),
            }
    cache.clear()
    cache.update(by_client)
    cache["__loaded__"] = {"has_estimate": False}  # type: ignore[assignment]
    return cache.get(client_id, empty)


def build_review_billing_projection(
    workflows: List[Any],
    today: Optional[date] = None,
    months: int = HORIZON_MONTHS,
) -> Dict[str, Any]:
    """
    Group open review dues into next ``months`` calendar months and attach
    agreement fee estimates on current values (as_of=today).

    Overdue open reviews (due before current month start) land in the current month.
    """
    today = today or date.today()
    windows = month_windows(today, months)
    horizon_start = windows[0][0]
    horizon_end = windows[-1][1]

    # client_id -> ym -> list of open workflows
    buckets: Dict[str, Dict[Any, List[Any]]] = {ym: {} for _, _, ym in windows}
    date_missing: List[Any] = []

    for w in workflows:
        if not _is_open(w):
            continue
        cid = _client_id(w)
        if cid is None:
            continue
        rd = getattr(w, "review_date", None)
        if rd is None:
            date_missing.append(w)
            continue
        # Overdue before horizon → current month; beyond horizon → skip
        if rd < horizon_start:
            ym = windows[0][2]
        elif rd > horizon_end:
            continue
        else:
            ym = rd.strftime("%Y-%m")
            if ym not in buckets:
                continue
        buckets[ym].setdefault(cid, []).append(w)

    estimate_cache: Dict[int, Dict[str, Any]] = {}
    # Warm cache once
    all_cids = set()
    for ym_map in buckets.values():
        all_cids.update(ym_map.keys())
    for cid in all_cids:
        _estimate_for_client(int(cid), today, estimate_cache)

    month_sections: List[Dict[str, Any]] = []
    grand_reviews = 0
    grand_fee = 0.0
    clients_with_fee = 0
    clients_missing_fee = 0

    for start, end, ym in windows:
        rows: List[Dict[str, Any]] = []
        for cid, wlist in buckets[ym].items():
            primary = _pick_primary_for_month(wlist)
            if primary is None:
                continue
            est = estimate_cache.get(int(cid)) or {
                "estimated_fee": 0.0,
                "next_billing_date": None,
                "billing_frequency": None,
                "agreement_id": None,
                "estimate_error": "No active agreement",
                "has_estimate": False,
            }
            client = getattr(primary, "client", None)
            assigned = getattr(primary, "assigned_user", None)
            fee = float(est.get("estimated_fee") or 0.0)
            has_est = bool(est.get("has_estimate"))
            if has_est:
                clients_with_fee += 1
                grand_fee += fee
            else:
                clients_missing_fee += 1
            rows.append(
                {
                    "client_id": int(cid),
                    "client_name": getattr(client, "name", None) or f"Client {cid}",
                    "workflow_id": getattr(primary, "id", None),
                    "review_date": getattr(primary, "review_date", None),
                    "status": _status(primary),
                    "assigned": getattr(assigned, "username", None) or "—",
                    "estimated_fee": fee if has_est else 0.0,
                    "has_estimate": has_est,
                    "next_billing_date": est.get("next_billing_date"),
                    "billing_frequency": est.get("billing_frequency"),
                    "agreement_id": est.get("agreement_id"),
                    "estimate_error": est.get("estimate_error"),
                    "overdue_into_month": bool(
                        getattr(primary, "review_date", None)
                        and primary.review_date < start
                        and ym == windows[0][2]
                    ),
                }
            )
        rows.sort(
            key=lambda r: (
                r["review_date"] is None,
                r["review_date"] or date.max,
                (r["client_name"] or "").lower(),
            )
        )
        month_fee = round(sum(r["estimated_fee"] for r in rows if r["has_estimate"]), 2)
        grand_reviews += len(rows)
        month_sections.append(
            {
                "year_month": ym,
                "label": start.strftime("%B %Y"),
                "month_start": start,
                "month_end": end,
                "review_count": len(rows),
                "estimated_fee_total": month_fee,
                "rows": rows,
            }
        )

    date_missing_rows = []
    for w in date_missing:
        cid = _client_id(w)
        client = getattr(w, "client", None)
        date_missing_rows.append(
            {
                "client_id": cid,
                "client_name": getattr(client, "name", None) or f"Client {cid}",
                "workflow_id": getattr(w, "id", None),
                "status": _status(w),
            }
        )
    date_missing_rows.sort(key=lambda r: (r["client_name"] or "").lower())

    return {
        "as_of": today,
        "horizon_start": horizon_start,
        "horizon_end": horizon_end,
        "months": month_sections,
        "summary": {
            "month_count": len(month_sections),
            "review_count": grand_reviews,
            "estimated_fee_total": round(grand_fee, 2),
            "clients_with_fee": clients_with_fee,
            "clients_missing_fee": clients_missing_fee,
        },
        "date_missing": date_missing_rows,
    }


def build_projection_from_db(today: Optional[date] = None) -> Dict[str, Any]:
    """Load open ReviewWorkflows and build the 12-month projection."""
    from models import ReviewWorkflow
    from sqlalchemy.orm import joinedload

    today = today or date.today()
    workflows = (
        ReviewWorkflow.query.options(
            joinedload(ReviewWorkflow.client),
            joinedload(ReviewWorkflow.assigned_user),
        )
        .filter(ReviewWorkflow.status.in_(list(OPEN_STATUSES)))
        .all()
    )
    return build_review_billing_projection(workflows, today=today)


def render_projection_email_html(projection: Dict[str, Any]) -> str:
    """HTML body for the Sunday digest (Anshul only)."""
    from services.ops_digest_email_service import _esc, _html_table, _wrap

    summary = projection.get("summary") or {}
    intro = (
        f"Next 12 months review dues with agreement fee estimates on current values "
        f"(as of {projection.get('as_of')}). "
        f"Reviews: {summary.get('review_count', 0)}; "
        f"estimated fees: ₹{summary.get('estimated_fee_total', 0):,.2f}."
    )
    parts: List[str] = []
    # Month-wise summary table
    sum_rows = []
    for m in projection.get("months") or []:
        sum_rows.append(
            [
                _esc(m.get("label")),
                str(m.get("review_count") or 0),
                f"₹{float(m.get('estimated_fee_total') or 0):,.2f}",
            ]
        )
    parts.append("<h3 style='margin:16px 0 8px;font-size:14px'>Month-wise summary</h3>")
    parts.append(_html_table(["Month", "Reviews", "Est. fee"], sum_rows))

    for m in projection.get("months") or []:
        rows = m.get("rows") or []
        parts.append(
            f"<h3 style='margin:16px 0 8px;font-size:14px'>"
            f"{_esc(m.get('label'))} — {len(rows)} reviews · "
            f"₹{float(m.get('estimated_fee_total') or 0):,.2f}</h3>"
        )
        if not rows:
            parts.append("<p style='color:#666'>None</p>")
            continue
        table_rows = []
        for r in rows:
            rd = r.get("review_date")
            rd_s = rd.strftime("%Y-%m-%d") if rd else "—"
            fee_s = (
                f"₹{float(r.get('estimated_fee') or 0):,.2f}"
                if r.get("has_estimate")
                else (_esc(r.get("estimate_error")) or "—")
            )
            table_rows.append(
                [
                    _esc(r.get("client_name")),
                    rd_s,
                    _esc(r.get("status")),
                    _esc(r.get("assigned")),
                    fee_s,
                    _esc(r.get("next_billing_date") or "—"),
                ]
            )
        parts.append(
            _html_table(
                ["Client", "Review due", "Status", "Assigned", "Est. fee", "Next billing"],
                table_rows,
            )
        )

    missing = projection.get("date_missing") or []
    if missing:
        parts.append(
            f"<h3 style='margin:16px 0 8px;font-size:14px'>Date missing ({len(missing)})</h3>"
        )
        parts.append(
            _html_table(
                ["Client", "Status", "Review ID"],
                [
                    [_esc(r.get("client_name")), _esc(r.get("status")), str(r.get("workflow_id") or "")]
                    for r in missing
                ],
            )
        )

    return _wrap("Review cashflow projection (12 months)", intro, "\n".join(parts))


def send_review_billing_projection_weekly(
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Build projection and email only to ALLOWED_EMAIL."""
    from services.ops_digest_email_service import _send_html

    projection = build_projection_from_db()
    html_body = render_projection_email_html(projection)
    summary = projection.get("summary") or {}
    subject = (
        f"Review cashflow — {summary.get('review_count', 0)} dues / "
        f"₹{float(summary.get('estimated_fee_total') or 0):,.0f} est. (12 mo)"
    )
    recipients = [ALLOWED_EMAIL]
    sent = False
    if dry_run:
        return {
            "dry_run": True,
            "recipients": recipients,
            "subject": subject,
            "summary": summary,
            "html_chars": len(html_body),
        }
    sent = _send_html(subject, html_body, recipients)
    return {
        "sent": sent,
        "recipients": recipients,
        "subject": subject,
        "summary": summary,
    }
