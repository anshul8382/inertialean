"""
BNI TY Notes weekly email: clients with recommendations activity in the window,
consolidated per client, amounts from ``RecommendationSession.investment_amount``
when linked to the workflow (fallback: workflow action / per-session line sums if unset).

Includes:
- ``WorkflowAction`` ``RECOS_GENERATED`` (monthly investment UI), and
- client trade recommendations with ``sent_at`` in the window (unified flow /
  clients already past RECOS/NOTIFY/EXEC still appear when sends land in-range).

``WorkflowAction.action_date`` and ``Recommendation.sent_at`` are treated as **naive UTC**
when converting to IST (consistent with ``monthly_investments`` using ``datetime.utcnow()``).
"""
from __future__ import annotations

import logging
from calendar import month_abbr
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from flask import current_app
from sqlalchemy.orm import joinedload

from extensions import db
from utils.ist_datetime import (
    ist_calendar_bounds_as_utc_naive,
    ist_date_span_days,
)
from models import (
    BniTyNotesState,
    Client,
    ClientBniReferral,
    MonthlyInvestment,
    Recommendation,
    RecommendationSession,
    User,
    Workflow,
    WorkflowAction,
)
from services.recommendation_workflow_link_service import extract_workflow_id_from_notes

logger = logging.getLogger(__name__)


def utc_bounds_for_calendar_days(start_d: date, end_d: date) -> Tuple[datetime, datetime]:
    """Inclusive IST calendar [start_d, end_d] as naive UTC (since, until) for SQL on UTC-naive columns."""
    return ist_calendar_bounds_as_utc_naive(start_d, end_d)


STATE_ROW_ID = 1
FIRST_RUN_LOOKBACK_DAYS = 90
MAX_ON_DEMAND_RANGE_DAYS = 120
SUBJECT = "BNI TY Notes"
DEFAULT_BNI_MAIL_SENDER = "anshul@equities4wealth.com"


def _signed_line_amount(rec: Recommendation) -> Decimal:
    amt = rec.total_amount
    if amt is None:
        return Decimal("0")
    action = (rec.action or "").strip().lower()
    val = Decimal(str(amt))
    if action == "sell":
        return -val
    if action == "buy":
        return val
    return Decimal("0")


def _line_sum_for_session(session_id: int, client_id: int) -> Decimal:
    recs = Recommendation.query.filter_by(session_id=session_id, client_id=client_id).all()
    return sum((_signed_line_amount(r) for r in recs), Decimal("0"))


def _session_investment_decimal(session: Optional[RecommendationSession], client_id: int) -> Decimal:
    """Prefer ``RecommendationSession.investment_amount``; else signed line-sum for that session."""
    if not session:
        return Decimal("0")
    if session.investment_amount is not None:
        return Decimal(str(session.investment_amount))
    return _line_sum_for_session(session.id, client_id)


def _net_recommendations_for_workflow(workflow_id: int, client_id: int) -> Tuple[Decimal, bool]:
    """
    Sum session-level investment for each ``RecommendationSession`` linked to ``workflow_id``
    via ``WORKFLOW_ID`` in session notes.
    """
    sessions = RecommendationSession.query.filter_by(client_id=client_id).all()
    linked = [s for s in sessions if extract_workflow_id_from_notes(s.notes) == workflow_id]
    if not linked:
        return Decimal("0"), False
    total = sum((_session_investment_decimal(s, client_id) for s in linked), Decimal("0"))
    return total, True


def _fallback_amount_for_action(action: WorkflowAction) -> Decimal:
    if action.amount is not None and action.amount != 0:
        return Decimal(str(action.amount))
    wf = action.workflow
    if wf and wf.actual_amount is not None:
        return Decimal(str(wf.actual_amount))
    return Decimal("0")


def _net_for_workflow_action(action: WorkflowAction) -> Decimal:
    wf = action.workflow
    if not wf or not wf.monthly_investment:
        return Decimal("0")
    client_id = wf.monthly_investment.client_id
    net, found = _net_recommendations_for_workflow(wf.id, client_id)
    if found:
        return net
    return _fallback_amount_for_action(action)


def _get_watermark_start(default_until: datetime) -> datetime:
    row = BniTyNotesState.query.get(STATE_ROW_ID)
    if row and row.last_success_end_at:
        return row.last_success_end_at
    return default_until - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)


def _format_date_cell(dates: List[datetime]) -> str:
    if not dates:
        return "—"
    norm = [d.date() if hasattr(d, "date") else d for d in dates]
    mn, mx = min(norm), max(norm)
    y0 = datetime.utcnow().year

    def day_mon(d, with_year: bool) -> str:
        return f"{d.day} {month_abbr[d.month]}" + (f" {d.year}" if with_year else "")

    if mn == mx:
        return day_mon(mn, mn.year != y0)
    if mn.year != mx.year:
        return f"{day_mon(mn, True)} – {day_mon(mx, True)}"
    if mn.year == y0:
        return f"{day_mon(mn, False)} – {day_mon(mx, False)}"
    return f"{day_mon(mn, True)} – {day_mon(mx, True)}"


def _recipient_emails() -> List[str]:
    users = User.query.filter(User.is_active.is_(True), User.email.isnot(None)).all()
    out = []
    for u in users:
        if not (getattr(u, "is_admin", False) or getattr(u, "is_ops_manager", False)):
            continue
        em = (u.email or "").strip()
        if em:
            out.append(em)
    return sorted(set(out))


def _workflow_bucket_key(client_id: int, workflow_id: Optional[int], session_id: Optional[int]) -> int:
    if workflow_id is not None:
        return workflow_id
    if session_id is not None:
        return -int(session_id)
    return 0


def build_report_rows(since: datetime, until: datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Amounts use ``RecommendationSession.investment_amount`` per linked session (not sums of
    per-security lines), with per-session line-sum fallback when ``investment_amount`` is unset.
    """
    actions = (
        WorkflowAction.query.filter(
            WorkflowAction.action_type == "RECOS_GENERATED",
            WorkflowAction.action_date > since,
            WorkflowAction.action_date <= until,
        )
        .join(Workflow, WorkflowAction.workflow_id == Workflow.id)
        .join(MonthlyInvestment, Workflow.monthly_investment_id == MonthlyInvestment.id)
        .order_by(WorkflowAction.action_date.asc())
        .all()
    )

    wf_ids_by_client: Dict[int, set] = defaultdict(set)
    dates_by_client: Dict[int, List[datetime]] = defaultdict(list)
    net_by_client_wf: Dict[Tuple[int, int], Decimal] = {}
    keys_from_recos_generated: Set[Tuple[int, int]] = set()

    for action in actions:
        wf = action.workflow
        if not wf or not wf.monthly_investment:
            continue
        cid = wf.monthly_investment.client_id
        bucket = wf.id
        wf_ids_by_client[cid].add(bucket)
        dates_by_client[cid].append(action.action_date)
        key = (cid, bucket)
        if key not in net_by_client_wf:
            net_by_client_wf[key] = _net_for_workflow_action(action)
            keys_from_recos_generated.add(key)

    sent_recs = (
        Recommendation.query.options(joinedload(Recommendation.session))
        .filter(
            Recommendation.client_id.isnot(None),
            Recommendation.sent_at.isnot(None),
            Recommendation.sent_at > since,
            Recommendation.sent_at <= until,
            Recommendation.quantity.isnot(None),
        )
        .order_by(Recommendation.sent_at.asc())
        .all()
    )

    sent_session_buckets: Set[Tuple[int, int, int]] = set()

    for rec in sent_recs:
        cid = rec.client_id
        if cid is None:
            continue
        sess = rec.session
        wf_from_notes = extract_workflow_id_from_notes(sess.notes) if sess else None
        bucket = _workflow_bucket_key(cid, wf_from_notes, rec.session_id)
        wf_ids_by_client[cid].add(bucket)
        dates_by_client[cid].append(rec.sent_at)

        key = (cid, bucket)
        if wf_from_notes is not None and key in keys_from_recos_generated:
            continue

        sid = int(rec.session_id) if rec.session_id is not None else 0
        sb = (cid, bucket, sid)
        if sb in sent_session_buckets:
            continue
        sent_session_buckets.add(sb)

        chunk = _session_investment_decimal(sess, cid)
        if key in net_by_client_wf:
            net_by_client_wf[key] = net_by_client_wf[key] + chunk
        else:
            net_by_client_wf[key] = chunk

    bni_rows: List[Dict[str, Any]] = []
    non_bni: List[Dict[str, Any]] = []

    cids = list(wf_ids_by_client.keys())
    clients_by_id = {c.id: c for c in Client.query.filter(Client.id.in_(cids)).all()} if cids else {}

    for cid in sorted(cids, key=lambda i: (clients_by_id.get(i).name or "").lower() if clients_by_id.get(i) else ""):
        client = clients_by_id.get(cid)
        if not client:
            continue
        total = Decimal("0")
        for wid in wf_ids_by_client[cid]:
            total += net_by_client_wf.get((cid, wid), Decimal("0"))
        date_label = _format_date_cell(dates_by_client[cid])
        name = client.name or f"Client #{cid}"
        lookup = ClientBniReferral.query.filter_by(client_id=cid).first()
        row_common = {"client_name": name, "amount": total, "date_label": date_label}
        if lookup and (lookup.referral_name or "").strip():
            bni_rows.append({**row_common, "referral_name": lookup.referral_name.strip()})
        else:
            non_bni.append(row_common)

    bni_rows.sort(key=lambda r: r["client_name"].lower())
    non_bni.sort(key=lambda r: r["client_name"].lower())
    return bni_rows, non_bni


def render_html_email(
    bni_rows: List[Dict[str, Any]],
    non_bni_rows: List[Dict[str, Any]],
    since: datetime,
    until: datetime,
) -> str:
    def money(d: Decimal) -> str:
        return f"{float(d):,.2f}"

    def sum_amounts(rows: List[Dict[str, Any]]) -> Decimal:
        return sum((r["amount"] for r in rows), Decimal("0"))

    bni_table = ""
    if bni_rows:
        bni_total = sum_amounts(bni_rows)
        bni_table = "<table class='t'><thead><tr><th>Client</th><th>Amount</th><th>Reco generated</th><th>BNI referral</th></tr></thead><tbody>"
        for r in bni_rows:
            bni_table += (
                f"<tr><td>{r['client_name']}</td><td>{money(r['amount'])}</td>"
                f"<td>{r['date_label']}</td><td>{r['referral_name']}</td></tr>"
            )
        bni_table += (
            "</tbody><tfoot><tr class='total-row'>"
            f"<td><strong>Total</strong></td><td><strong>{money(bni_total)}</strong></td>"
            "<td></td><td></td></tr></tfoot></table>"
        )
    else:
        bni_table = (
            "<p><em>No BNI referral matches in this period.</em></p>"
            f"<p class='total-line'><strong>Total:</strong> {money(Decimal('0'))}</p>"
        )

    non_table = ""
    if non_bni_rows:
        non_total = sum_amounts(non_bni_rows)
        non_table = "<table class='t'><thead><tr><th>Client</th><th>Amount</th><th>Reco generated</th></tr></thead><tbody>"
        for r in non_bni_rows:
            non_table += (
                f"<tr><td>{r['client_name']}</td><td>{money(r['amount'])}</td><td>{r['date_label']}</td></tr>"
            )
        non_table += (
            "</tbody><tfoot><tr class='total-row'>"
            f"<td><strong>Total</strong></td><td><strong>{money(non_total)}</strong></td>"
            "<td></td></tr></tfoot></table>"
        )
    else:
        non_table = (
            "<p><em>No non-BNI investments in this period.</em></p>"
            f"<p class='total-line'><strong>Total:</strong> {money(Decimal('0'))}</p>"
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<style>
body {{ font-family: Arial, sans-serif; margin: 16px; color: #222; }}
h2 {{ color: #1a5276; }}
.t {{ border-collapse: collapse; width: 100%; max-width: 900px; margin-bottom: 24px; }}
.t th, .t td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
.t th {{ background: #f4f6f8; }}
.t tfoot td {{ background: #eef2f6; border-top: 2px solid #bbb; }}
.total-line {{ margin-top: 8px; }}
.meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
</style></head><body>
<p class="meta">Window (UTC): {since.strftime('%Y-%m-%d %H:%M')} → {until.strftime('%Y-%m-%d %H:%M')}<br/>
Trigger: <code>RECOS_GENERATED</code> and/or <code>Recommendation.sent_at</code> (trade recs).<br/>
Amount: <code>RecommendationSession.investment_amount</code> per session (line-sum fallback if unset).</p>
<h2>1. BNI referrals TY</h2>
{bni_table}
<h2>2. Non-BNI investments in this reporting window</h2>
{non_table}
<p class="meta">Inertia — BNI TY Notes (automated)</p>
</body></html>"""


def validate_on_demand_range(since: datetime, until: datetime) -> Optional[str]:
    if until < since:
        return "End date must be on or after start date."
    span = ist_date_span_days(since, until)
    if span > MAX_ON_DEMAND_RANGE_DAYS:
        return f"Range too long (max {MAX_ON_DEMAND_RANGE_DAYS} IST calendar days between start and end)."
    return None


def compose_bni_ty_notes(since: datetime, until: datetime) -> Dict[str, Any]:
    bni_rows, non_bni_rows = build_report_rows(since, until)
    html = render_html_email(bni_rows, non_bni_rows, since, until)
    return {
        "since": since,
        "until": until,
        "since_iso": since.isoformat(),
        "until_iso": until.isoformat(),
        "html": html,
        "bni_count": len(bni_rows),
        "non_bni_count": len(non_bni_rows),
    }


def deliver_bni_ty_notes_email(
    since: datetime,
    until: datetime,
    *,
    dry_run: bool = False,
    update_watermark: bool = False,
) -> Dict[str, Any]:
    payload = compose_bni_ty_notes(since, until)
    html = payload["html"]
    recipients = _recipient_emails()

    result: Dict[str, Any] = {
        "since": payload["since_iso"],
        "until": payload["until_iso"],
        "bni_count": payload["bni_count"],
        "non_bni_count": payload["non_bni_count"],
        "recipients": recipients,
        "dry_run": dry_run,
        "sent": False,
        "update_watermark": update_watermark,
    }

    if dry_run:
        logger.info("BNI TY Notes dry_run: would send to %s", recipients)
        return result

    if not recipients:
        logger.error("BNI TY Notes: no admin/ops_manager recipients with email; skipping send")
        result["error"] = "no_recipients"
        return result

    from flask_mail import Message
    from extensions import mail

    cfg = current_app.config
    sender_email = (
        (cfg.get("MAIL_USERNAME") or "").strip()
        or (cfg.get("MAIL_DEFAULT_SENDER") or "").strip()
        or DEFAULT_BNI_MAIL_SENDER
    )
    msg = Message(
        subject=SUBJECT,
        recipients=recipients,
        html=html,
        sender=sender_email,
    )
    mail.send(msg)

    if update_watermark:
        row = BniTyNotesState.query.get(STATE_ROW_ID)
        if not row:
            row = BniTyNotesState(id=STATE_ROW_ID)
            db.session.add(row)
        row.last_success_end_at = until
        db.session.commit()
    result["sent"] = True
    logger.info("BNI TY Notes sent to %s (update_watermark=%s)", recipients, update_watermark)
    return result


def run_bni_ty_notes_email(dry_run: bool = False) -> Dict[str, Any]:
    until = datetime.utcnow()
    since = _get_watermark_start(until)
    return deliver_bni_ty_notes_email(since, until, dry_run=dry_run, update_watermark=not dry_run)
