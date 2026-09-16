"""
Ops digests: per-advisor emails + consolidated manager/admin copies.

Used by:
  - Data integrity open-issues digest (after DI daily)
  - Daily alert report (active/acknowledged alerts)
  - Daily review-workflow report (open reviews)

Env:
  DI_CREATE_OPS_TASKS — when false (default), no OpsTasks from DataIntegrityIssue
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OPEN_DI_STATUSES = ("open", "baseline")
OPEN_REVIEW_STATUSES = ("initiated", "sent", "meeting")
OPEN_ALERT_STATUSES = ("active", "acknowledged")


def di_create_ops_tasks_enabled() -> bool:
    """Default false — prefer advisor digests over /tasks for DI issues."""
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            val = current_app.config.get("DI_CREATE_OPS_TASKS")
            if isinstance(val, bool):
                return val
            if val is not None and str(val).strip() != "":
                return str(val).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    raw = (os.environ.get("DI_CREATE_OPS_TASKS") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _manager_admin_emails() -> List[str]:
    from models import ReportRecipient, User

    emails: List[str] = []
    for job_id in (
        "ops_digest_consolidated",
        "daily_alert_report",
        "data_integrity_advisor_digest",
        "review_workflow_daily_report",
    ):
        try:
            for r in ReportRecipient.query.filter_by(job_id=job_id, is_active=True).all():
                e = (r.email or "").strip()
                if e and e not in emails:
                    emails.append(e)
        except Exception:
            pass
    try:
        users = User.query.filter(User.is_active.is_(True)).all()
        for u in users:
            role = (getattr(u, "role", None) or "").lower()
            if getattr(u, "is_admin", False) or role in ("admin", "manager", "ops_manager"):
                e = (u.email or "").strip()
                if e and e not in emails:
                    emails.append(e)
    except Exception as exc:
        logger.warning("manager/admin email lookup failed: %s", exc)
    if not emails:
        emails = ["anshul@equities4wealth.com"]
    return emails


def _send_html(subject: str, html: str, recipients: List[str]) -> bool:
    if not recipients:
        return False
    try:
        from flask import current_app
        from flask_mail import Message
        from extensions import mail

        msg = Message(
            subject=subject,
            recipients=recipients,
            html=html,
            sender=current_app.config.get("MAIL_DEFAULT_SENDER")
            or current_app.config.get("MAIL_USERNAME"),
        )
        mail.send(msg)
        logger.info("Digest sent to %s: %s", recipients, subject)
        return True
    except Exception as exc:
        logger.exception("Digest email failed (%s): %s", subject, exc)
        return False


def _html_table(headers: List[str], rows: List[List[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>")
    tbody = "\n".join(body) if body else f'<tr><td colspan="{len(headers)}">None</td></tr>'
    return (
        '<table border="1" cellpadding="6" cellspacing="0" '
        'style="border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:13px;">'
        f"<thead><tr style='background:#eee'>{th}</tr></thead><tbody>{tbody}</tbody></table>"
    )


def _wrap(title: str, intro: str, table_html: str) -> str:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;padding:16px">
<h2>{title}</h2>
<p style="color:#555">{intro}</p>
<p style="color:#888;font-size:12px">Generated {now}</p>
{table_html}
</body></html>"""


# ----- Data integrity open issues -----


def _advisor_for_client(client) -> Optional[Any]:
    if client is None:
        return None
    return getattr(client, "advisor", None)


def send_data_integrity_open_issues_digests() -> Dict[str, Any]:
    """Email each advisor their clients' open DI issues; consolidated to managers/admins."""
    from models import Client, DataIntegrityIssue
    from sqlalchemy.orm import joinedload

    issues = (
        DataIntegrityIssue.query.options(
            joinedload(DataIntegrityIssue.client).joinedload(Client.advisor),
        )
        .filter(DataIntegrityIssue.status.in_(list(OPEN_DI_STATUSES)))
        .order_by(DataIntegrityIssue.severity.desc(), DataIntegrityIssue.created_at.desc())
        .all()
    )

    by_advisor: Dict[int, List[Any]] = defaultdict(list)
    unassigned: List[Any] = []
    for iss in issues:
        adv = _advisor_for_client(getattr(iss, "client", None))
        if adv and getattr(adv, "id", None) and (adv.email or "").strip():
            by_advisor[adv.id].append(iss)
        else:
            unassigned.append(iss)

    sent = 0
    for advisor_id, iss_list in by_advisor.items():
        advisor = iss_list[0].client.advisor
        rows = []
        for iss in iss_list[:200]:
            cname = getattr(iss.client, "name", None) or f"Client {iss.client_id}"
            rows.append(
                [
                    cname,
                    iss.check_category or "—",
                    iss.severity or "—",
                    iss.status or "—",
                    (iss.message or "")[:120],
                ]
            )
        html = _wrap(
            f"Open data integrity issues — {advisor.username or advisor.email}",
            f"{len(iss_list)} open/baseline issue(s) for your clients.",
            _html_table(["Client", "Category", "Severity", "Status", "Message"], rows),
        )
        if _send_html(
            f"Data integrity issues ({len(iss_list)}) — {datetime.utcnow().strftime('%Y-%m-%d')}",
            html,
            [advisor.email.strip()],
        ):
            sent += 1

    # Consolidated
    all_rows = []
    for iss in issues[:300]:
        cname = getattr(iss.client, "name", None) or f"Client {iss.client_id}"
        adv = _advisor_for_client(iss.client)
        adv_name = (getattr(adv, "username", None) or getattr(adv, "email", None) or "—")
        all_rows.append(
            [
                cname,
                adv_name,
                iss.check_category or "—",
                iss.severity or "—",
                iss.status or "—",
                (iss.message or "")[:100],
            ]
        )
    if unassigned:
        all_rows.append(["—", "UNASSIGNED", f"{len(unassigned)} issue(s) without advisor", "", "", ""])

    cons_html = _wrap(
        "Data integrity — consolidated open issues",
        f"Total open/baseline: {len(issues)}. Advisors emailed: {sent}.",
        _html_table(
            ["Client", "Advisor", "Category", "Severity", "Status", "Message"],
            all_rows,
        ),
    )
    cons_ok = _send_html(
        f"[Consolidated] Data integrity issues ({len(issues)}) — {datetime.utcnow().strftime('%Y-%m-%d')}",
        cons_html,
        _manager_admin_emails(),
    )
    return {
        "issues": len(issues),
        "advisor_emails_sent": sent,
        "consolidated_sent": cons_ok,
        "unassigned": len(unassigned),
    }


# ----- Alerts -----


def send_alert_advisor_digests() -> Dict[str, Any]:
    """Per-advisor active alerts + consolidated (replaces single-blast daily_alert_report)."""
    from models import Client
    from sqlalchemy.orm import joinedload

    try:
        from alert_system_models import Alert as AlertModel
    except ImportError:
        from models import Alert as AlertModel  # type: ignore

    alerts = (
        AlertModel.query.options(
            joinedload(AlertModel.client).joinedload(Client.advisor),
            joinedload(AlertModel.assigned_user),
        )
        .filter(AlertModel.status.in_(list(OPEN_ALERT_STATUSES)))
        .order_by(AlertModel.created_at.desc())
        .all()
    )

    by_advisor: Dict[int, List[Any]] = defaultdict(list)
    orphan: List[Any] = []
    for a in alerts:
        # Group by client's advisor (book ownership), same as task assignment
        adv = _advisor_for_client(getattr(a, "client", None))
        if not adv:
            # Fall back to alert assignee
            adv = getattr(a, "assigned_user", None)
        if adv and getattr(adv, "id", None) and (adv.email or "").strip():
            by_advisor[adv.id].append(a)
        else:
            orphan.append(a)

    sent = 0
    for _aid, alist in by_advisor.items():
        advisor = None
        for a in alist:
            advisor = _advisor_for_client(getattr(a, "client", None)) or getattr(
                a, "assigned_user", None
            )
            if advisor:
                break
        if not advisor or not (advisor.email or "").strip():
            continue
        rows = []
        for a in alist[:150]:
            cname = "—"
            try:
                from alert_service import AlertService

                cname = AlertService._client_name_for_alert_row(a)
            except Exception:
                c = getattr(a, "client", None)
                cname = getattr(c, "name", None) or "—"
            rows.append(
                [
                    a.alert_type or "—",
                    a.severity or "—",
                    a.status or "—",
                    (a.title or "")[:80],
                    cname,
                ]
            )
        html = _wrap(
            f"Daily alerts — {advisor.username or advisor.email}",
            f"{len(alist)} active/acknowledged alert(s) for your book.",
            _html_table(["Type", "Severity", "Status", "Title", "Client"], rows),
        )
        if _send_html(
            f"Daily Alert Report ({len(alist)}) — {datetime.utcnow().strftime('%Y-%m-%d')}",
            html,
            [advisor.email.strip()],
        ):
            sent += 1

    cons_rows = []
    for a in alerts[:200]:
        cname = "—"
        try:
            from alert_service import AlertService

            cname = AlertService._client_name_for_alert_row(a)
        except Exception:
            pass
        adv = _advisor_for_client(getattr(a, "client", None))
        cons_rows.append(
            [
                a.alert_type or "—",
                a.severity or "—",
                a.status or "—",
                (a.title or "")[:60],
                cname,
                getattr(adv, "username", None) or "—",
            ]
        )
    cons_html = _wrap(
        "Daily Alert Report — consolidated",
        f"Total: {len(alerts)}. Advisor emails: {sent}.",
        _html_table(
            ["Type", "Severity", "Status", "Title", "Client", "Advisor"],
            cons_rows,
        ),
    )
    cons_ok = _send_html(
        f"[Consolidated] Daily Alert Report ({len(alerts)}) — {datetime.utcnow().strftime('%Y-%m-%d')}",
        cons_html,
        _manager_admin_emails(),
    )
    return {
        "alerts": len(alerts),
        "advisor_emails_sent": sent,
        "consolidated_sent": cons_ok,
        "orphan": len(orphan),
    }


# ----- Review workflows -----


def send_review_workflow_daily_digests() -> Dict[str, Any]:
    """Open ReviewWorkflows: per client-advisor + consolidated manager/admin."""
    from models import Client, ReviewWorkflow
    from sqlalchemy.orm import joinedload

    reviews = (
        ReviewWorkflow.query.options(
            joinedload(ReviewWorkflow.client).joinedload(Client.advisor),
        )
        .filter(ReviewWorkflow.status.in_(list(OPEN_REVIEW_STATUSES)))
        .order_by(ReviewWorkflow.status, ReviewWorkflow.updated_at.desc())
        .all()
    )

    by_advisor: Dict[int, List[Any]] = defaultdict(list)
    unassigned: List[Any] = []
    for rw in reviews:
        # Always group by the client's advisor (book ownership)
        adv = _advisor_for_client(getattr(rw, "client", None))
        if adv and getattr(adv, "id", None) and (adv.email or "").strip():
            by_advisor[adv.id].append((rw, adv))
        else:
            unassigned.append(rw)

    sent = 0
    for advisor_id, pairs in by_advisor.items():
        advisor = pairs[0][1]
        rows = []
        for rw, _ in pairs[:150]:
            cname = getattr(rw.client, "name", None) or f"Client {rw.client_id}"
            updated = ""
            if getattr(rw, "updated_at", None):
                updated = rw.updated_at.strftime("%Y-%m-%d")
            rows.append([cname, rw.status or "—", updated, str(getattr(rw, "id", ""))])
        html = _wrap(
            f"Open client reviews — {advisor.username or advisor.email}",
            f"{len(pairs)} open review(s) (initiated/sent/meeting).",
            _html_table(["Client", "Status", "Updated", "Review ID"], rows),
        )
        if _send_html(
            f"Client reviews ({len(pairs)}) — {datetime.utcnow().strftime('%Y-%m-%d')}",
            html,
            [advisor.email.strip()],
        ):
            sent += 1

    cons_rows = []
    for rw in reviews[:200]:
        cname = getattr(rw.client, "name", None) or f"Client {rw.client_id}"
        adv = _advisor_for_client(rw.client)
        cons_rows.append(
            [
                cname,
                rw.status or "—",
                getattr(adv, "username", None) or "—",
                str(rw.id),
            ]
        )
    if unassigned:
        cons_rows.append(["—", f"{len(unassigned)} unassigned", "—", "—"])

    cons_html = _wrap(
        "Client reviews — consolidated",
        f"Open reviews: {len(reviews)}. Advisor emails: {sent}.",
        _html_table(["Client", "Status", "Advisor", "Review ID"], cons_rows),
    )
    cons_ok = _send_html(
        f"[Consolidated] Client reviews ({len(reviews)}) — {datetime.utcnow().strftime('%Y-%m-%d')}",
        cons_html,
        _manager_admin_emails(),
    )
    return {
        "reviews": len(reviews),
        "advisor_emails_sent": sent,
        "consolidated_sent": cons_ok,
        "unassigned": len(unassigned),
    }
