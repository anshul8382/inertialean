"""
Ops digests: per-advisor emails + consolidated manager/admin copies.

Used by:
  - Data integrity open-issues digest (after DI daily)
  - Daily alert report (active/acknowledged alerts)
  - Daily review-workflow report (open reviews)

Daily alert layout intent: docs/DAILY_ALERT_REPORT_PRIORITIES.md

Env:
  DI_CREATE_OPS_TASKS — when false (default), no OpsTasks from DataIntegrityIssue
"""
from __future__ import annotations

import html
import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

OPEN_DI_STATUSES = ("open", "baseline")
OPEN_REVIEW_STATUSES = ("initiated", "sent", "meeting")
OPEN_ALERT_STATUSES = ("active", "acknowledged")

# Spec: docs/DAILY_ALERT_REPORT_PRIORITIES.md
ADVISOR_MAX_ROWS_PER_SECTION = 25
CONSOLIDATED_MAX_ROWS_PER_SECTION = 40
REVIEW_ALERT_TYPES = frozenset({"review_overdue", "review_upcoming"})
LEAD_SLA_ALERT_TYPES = frozenset({"lead_sla"})
IST = ZoneInfo("Asia/Kolkata")
# Mon=0 … Sun=6 — Thu/Fri/Sat = 3,4,5
MEETING_HIGHLIGHT_WEEKDAYS_IST = frozenset({3, 4, 5})


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
    th = "".join(
        f'<th style="word-break:break-word;overflow-wrap:anywhere;">{h}</th>' for h in headers
    )
    body = []
    for row in rows:
        body.append(
            "<tr>"
            + "".join(
                f'<td style="word-break:break-word;overflow-wrap:anywhere;">{c}</td>' for c in row
            )
            + "</tr>"
        )
    tbody = "\n".join(body) if body else f'<tr><td colspan="{len(headers)}">None</td></tr>'
    return (
        '<table border="1" cellpadding="6" cellspacing="0" '
        'style="border-collapse:collapse;width:100%;max-width:100%;'
        'font-family:Arial,sans-serif;font-size:12px;table-layout:auto;">'
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


def _esc(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _hours_label(hours: Optional[float]) -> str:
    if hours is None:
        return "—"
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "—"
    if h < 0:
        h = 0
    if h < 48:
        return f"{int(round(h))}h"
    days = h / 24.0
    if days < 14:
        return f"{days:.1f}d"
    return f"{int(round(days))}d"


def _severity_rank(severity: Optional[str]) -> int:
    s = (severity or "").lower()
    if s == "critical":
        return 0
    if s == "warning":
        return 1
    return 2


def _sort_alerts(alerts: Sequence[Any]) -> List[Any]:
    return sorted(
        alerts,
        key=lambda a: (
            _severity_rank(getattr(a, "severity", None)),
            -(getattr(a, "current_delay", None) or 0),
            getattr(a, "created_at", None) or datetime.min,
        ),
    )


def classify_alert_section(alert: Any) -> str:
    """Map an alert to a layout section key (see DAILY_ALERT_REPORT_PRIORITIES.md)."""
    at = (getattr(alert, "alert_type", None) or "").strip().lower()
    sub = (getattr(alert, "alert_subtype", None) or "").strip().lower()
    title = (getattr(alert, "title", None) or "").strip()

    if at == "service_ticket":
        return "service_ticket"
    if at == "billing" and sub == "invoice_payment_delay":
        return "payment_delay"
    if title.lower().startswith("payment delay"):
        return "payment_delay"
    if at == "communication" and sub == "meeting_cadence":
        return "meeting_not_scheduled"
    if title == "Meeting Not Scheduled":
        return "meeting_not_scheduled"
    if at == "workflow_sla":
        return "workflow_sla"
    if at == "agent_orchestrated":
        return "agent_orchestrated"
    if at in REVIEW_ALERT_TYPES:
        return "reviews_fyi"
    if at in LEAD_SLA_ALERT_TYPES:
        return "lead_sla"
    return "other"


def workflow_stage_from_alert(alert: Any) -> str:
    sub = (getattr(alert, "alert_subtype", None) or "").strip()
    if sub.endswith("_delay"):
        return sub[: -len("_delay")].upper() or "UNKNOWN"
    wf = getattr(alert, "workflow", None)
    stage = getattr(wf, "current_stage", None) if wf is not None else None
    if stage:
        return str(stage).upper()
    return "UNKNOWN"


def extract_agent_issue_detail(description: Optional[str], max_len: int = 220) -> str:
    """Pull concrete issue lines from orchestrator description; fall back to trimmed text."""
    text = (description or "").strip()
    if not text:
        return "—"
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Orchestrator sample issues: "    - message"
        if line.startswith("- "):
            lines.append(line[2:].strip())
        elif re.match(r"^[-•]\s+", line):
            lines.append(re.sub(r"^[-•]\s+", "", line).strip())
        elif line.startswith("Issues by Agent") or line.endswith(":"):
            continue
        elif "critical," in line.lower() and "warning" in line.lower():
            continue
    if lines:
        joined = "; ".join(lines[:4])
    else:
        # Drop long orchestrator boilerplate headers
        joined = re.sub(
            r"(?is)^Orchestrator detected.*?(?=\n|$)",
            "",
            text,
        ).strip() or text
        joined = " ".join(joined.split())
    if len(joined) > max_len:
        return joined[: max_len - 1] + "…"
    return joined


def meeting_highlight_today(now: Optional[datetime] = None) -> bool:
    """True on Thu/Fri/Sat in Asia/Kolkata."""
    dt = now or datetime.now(IST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(IST)
    else:
        dt = dt.astimezone(IST)
    return dt.weekday() in MEETING_HIGHLIGHT_WEEKDAYS_IST


def _client_name(alert: Any) -> str:
    try:
        from alert_service import AlertService

        return AlertService._client_name_for_alert_row(alert) or "—"
    except Exception:
        c = getattr(alert, "client", None)
        return getattr(c, "name", None) or "—"


def _advisor_label(alert: Any) -> str:
    adv = _advisor_for_client(getattr(alert, "client", None))
    if not adv:
        adv = getattr(alert, "assigned_user", None)
    if not adv:
        return "—"
    return getattr(adv, "username", None) or getattr(adv, "email", None) or "—"


def _aging_hours(alert: Any) -> Optional[float]:
    delay = getattr(alert, "current_delay", None)
    if delay is not None:
        try:
            return float(delay)
        except (TypeError, ValueError):
            pass
    created = getattr(alert, "created_at", None)
    if created:
        try:
            return (datetime.utcnow() - created).total_seconds() / 3600.0
        except Exception:
            pass
    return None


def _partition_alerts_for_audience(
    alerts: Sequence[Any], audience: str
) -> Dict[str, List[Any]]:
    """Bucket alerts; drop lead_sla for advisors."""
    buckets: Dict[str, List[Any]] = defaultdict(list)
    for a in alerts:
        key = classify_alert_section(a)
        if audience == "advisor" and key == "lead_sla":
            continue
        buckets[key].append(a)
    for key in list(buckets.keys()):
        buckets[key] = _sort_alerts(buckets[key])
    return buckets


def _section_block(
    heading: str,
    intro: str,
    headers: List[str],
    rows: List[List[str]],
    *,
    total: int,
    shown: int,
    emphasize: bool = False,
) -> str:
    if not rows:
        return ""
    style = (
        "border-left:4px solid #c45c26;padding-left:10px;margin:18px 0 8px;"
        if emphasize
        else "margin:18px 0 8px;"
    )
    note = ""
    if shown < total:
        note = (
            f'<p style="color:#666;font-size:12px">Showing {shown} of {total}. '
            "View Alerts dashboard for the rest.</p>"
        )
    return (
        f'<div style="{style}"><h3 style="margin:0 0 6px">{_esc(heading)}</h3>'
        f'<p style="color:#555;margin:0 0 8px;font-size:13px">{intro}</p>'
        f"{_html_table(headers, rows)}{note}</div>"
    )


def build_alert_report_sections_html(
    alerts: Sequence[Any],
    audience: str,
    *,
    include_advisor_col: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """
    Build ordered HTML sections. Omits empty sections.
    audience: 'advisor' | 'consolidated'
    """
    max_rows = (
        ADVISOR_MAX_ROWS_PER_SECTION
        if audience == "advisor"
        else CONSOLIDATED_MAX_ROWS_PER_SECTION
    )
    buckets = _partition_alerts_for_audience(alerts, audience)
    parts: List[str] = []
    meeting_hot = meeting_highlight_today(now)

    def take(key: str) -> Tuple[List[Any], int]:
        full = buckets.get(key) or []
        return full[:max_rows], len(full)

    # 1. Service tickets
    chunk, total = take("service_ticket")
    if chunk:
        headers = ["Client"]
        if include_advisor_col:
            headers.append("Advisor")
        headers.extend(["Severity", "Title", "Aging", "Status"])
        rows = []
        for a in chunk:
            row = [_esc(_client_name(a))]
            if include_advisor_col:
                row.append(_esc(_advisor_label(a)))
            row.extend(
                [
                    _esc(getattr(a, "severity", None)),
                    _esc((getattr(a, "title", None) or "")[:80]),
                    _esc(_hours_label(_aging_hours(a))),
                    _esc(getattr(a, "status", None)),
                ]
            )
            rows.append(row)
        parts.append(
            _section_block(
                "1. Service tickets",
                "Open service ticket SLA alerts — start here.",
                headers,
                rows,
                total=total,
                shown=len(chunk),
            )
        )

    # 2. Payment delays
    chunk, total = take("payment_delay")
    if chunk:
        headers = ["Client"]
        if include_advisor_col:
            headers.append("Advisor")
        headers.extend(["Severity", "Invoice / title", "Overdue", "Status"])
        rows = []
        for a in chunk:
            row = [_esc(_client_name(a))]
            if include_advisor_col:
                row.append(_esc(_advisor_label(a)))
            row.extend(
                [
                    _esc(getattr(a, "severity", None)),
                    _esc((getattr(a, "title", None) or "")[:80]),
                    _esc(_hours_label(_aging_hours(a))),
                    _esc(getattr(a, "status", None)),
                ]
            )
            rows.append(row)
        parts.append(
            _section_block(
                "2. Payment delays",
                "Highlighted — follow up on overdue invoices promptly.",
                headers,
                rows,
                total=total,
                shown=len(chunk),
                emphasize=True,
            )
        )

    # 3. Meeting not scheduled
    chunk, total = take("meeting_not_scheduled")
    if chunk:
        if meeting_hot:
            intro = (
                "<strong>Priority today (Thu–Sat IST):</strong> schedule meetings "
                "before the weekend so clients are covered."
            )
        else:
            intro = "Clients with no upcoming meeting on the cadence SLA."
        headers = ["Client"]
        if include_advisor_col:
            headers.append("Advisor")
        headers.extend(["Severity", "Detail", "Since", "Status"])
        rows = []
        for a in chunk:
            detail = (getattr(a, "description", None) or getattr(a, "title", None) or "")[
                :120
            ]
            row = [_esc(_client_name(a))]
            if include_advisor_col:
                row.append(_esc(_advisor_label(a)))
            row.extend(
                [
                    _esc(getattr(a, "severity", None)),
                    _esc(detail),
                    _esc(_hours_label(_aging_hours(a))),
                    _esc(getattr(a, "status", None)),
                ]
            )
            rows.append(row)
        parts.append(
            _section_block(
                "3. Meeting not scheduled",
                intro,
                headers,
                rows,
                total=total,
                shown=len(chunk),
                emphasize=meeting_hot,
            )
        )

    # 4. Workflow SLA — separate table per stage
    wf_all = buckets.get("workflow_sla") or []
    if wf_all:
        by_stage: Dict[str, List[Any]] = defaultdict(list)
        for a in wf_all:
            by_stage[workflow_stage_from_alert(a)].append(a)
        stage_order = sorted(
            by_stage.keys(),
            key=lambda s: (
                0 if s in ("FUNDS", "RECOS", "NOTIFY", "UPDATE", "EXEC") else 1,
                s,
            ),
        )
        stage_blocks = [
            '<div style="margin:18px 0 8px"><h3 style="margin:0 0 6px">4. Workflow SLA</h3>'
            '<p style="color:#555;margin:0 0 8px;font-size:13px">'
            "Stage-wise delays — aging is time in (or attributed to) that stage.</p></div>"
        ]
        for stage in stage_order:
            full = _sort_alerts(by_stage[stage])
            chunk = full[:max_rows]
            headers = ["Client"]
            if include_advisor_col:
                headers.append("Advisor")
            headers.extend(
                ["Stage", "Aging in stage", "SLA (hours)", "Severity", "Status"]
            )
            rows = []
            for a in chunk:
                sla = getattr(a, "sla_timeline", None)
                row = [_esc(_client_name(a))]
                if include_advisor_col:
                    row.append(_esc(_advisor_label(a)))
                row.extend(
                    [
                        _esc(stage),
                        _esc(_hours_label(_aging_hours(a))),
                        _esc(sla if sla is not None else "—"),
                        _esc(getattr(a, "severity", None)),
                        _esc(getattr(a, "status", None)),
                    ]
                )
                rows.append(row)
            note = ""
            if len(chunk) < len(full):
                note = (
                    f'<p style="color:#666;font-size:12px">Showing {len(chunk)} of '
                    f"{len(full)} for stage {html.escape(stage)}.</p>"
                )
            stage_blocks.append(
                f'<h4 style="margin:12px 0 4px">Stage: {_esc(stage)} '
                f"({len(full)})</h4>{_html_table(headers, rows)}{note}"
            )
        parts.append("\n".join(stage_blocks))

    # 5. Agent orchestrated — show issue detail
    chunk, total = take("agent_orchestrated")
    if chunk:
        headers = ["Client"]
        if include_advisor_col:
            headers.append("Advisor")
        headers.extend(["Severity", "Issue detail", "Status"])
        rows = []
        for a in chunk:
            detail = extract_agent_issue_detail(getattr(a, "description", None))
            row = [_esc(_client_name(a))]
            if include_advisor_col:
                row.append(_esc(_advisor_label(a)))
            row.extend(
                [
                    _esc(getattr(a, "severity", None)),
                    _esc(detail),
                    _esc(getattr(a, "status", None)),
                ]
            )
            rows.append(row)
        parts.append(
            _section_block(
                "5. Critical / agent-detected issues",
                "What is wrong — not just that issues were detected.",
                headers,
                rows,
                total=total,
                shown=len(chunk),
            )
        )

    # 6. Other actionable
    chunk, total = take("other")
    if chunk:
        headers = ["Client"]
        if include_advisor_col:
            headers.append("Advisor")
        headers.extend(["Type", "Severity", "Title", "Status"])
        rows = []
        for a in chunk:
            row = [_esc(_client_name(a))]
            if include_advisor_col:
                row.append(_esc(_advisor_label(a)))
            row.extend(
                [
                    _esc(getattr(a, "alert_type", None)),
                    _esc(getattr(a, "severity", None)),
                    _esc((getattr(a, "title", None) or "")[:80]),
                    _esc(getattr(a, "status", None)),
                ]
            )
            rows.append(row)
        parts.append(
            _section_block(
                "6. Other alerts",
                "Remaining actionable alerts.",
                headers,
                rows,
                total=total,
                shown=len(chunk),
            )
        )

    # 7. Reviews FYI (bottom)
    chunk, total = take("reviews_fyi")
    if chunk:
        headers = ["Client"]
        if include_advisor_col:
            headers.append("Advisor")
        headers.extend(["Type", "Title", "Status"])
        rows = []
        for a in chunk:
            row = [_esc(_client_name(a))]
            if include_advisor_col:
                row.append(_esc(_advisor_label(a)))
            row.extend(
                [
                    _esc(getattr(a, "alert_type", None)),
                    _esc((getattr(a, "title", None) or "")[:80]),
                    _esc(getattr(a, "status", None)),
                ]
            )
            rows.append(row)
        parts.append(
            _section_block(
                "7. Reviews — for your information",
                "Informational only — not an urgent ops queue. Plan reviews when capacity allows.",
                headers,
                rows,
                total=total,
                shown=len(chunk),
            )
        )

    # 8. Lead SLA — consolidated only
    if audience == "consolidated":
        chunk, total = take("lead_sla")
        if chunk:
            headers = [
                "Lead / client",
                "Advisor",
                "Severity",
                "Title",
                "Status",
            ]
            rows = []
            for a in chunk:
                rows.append(
                    [
                        _esc(_client_name(a)),
                        _esc(_advisor_label(a)),
                        _esc(getattr(a, "severity", None)),
                        _esc((getattr(a, "title", None) or "")[:80]),
                        _esc(getattr(a, "status", None)),
                    ]
                )
            parts.append(
                _section_block(
                    "8. Lead SLA (managers / admins)",
                    "Lead pipeline SLA — for manager and admin follow-up.",
                    headers,
                    rows,
                    total=total,
                    shown=len(chunk),
                )
            )

    if not parts:
        return '<p style="color:#555">No active/acknowledged alerts in scope.</p>'
    return "\n".join(parts)


def _wrap_sections(title: str, intro: str, sections_html: str) -> str:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    body = sections_html.strip() or (
        '<p style="color:#666">No alerts in the priority sections for this audience.</p>'
    )
    return f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,sans-serif;-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;">
<tr><td align="center" style="padding:12px 8px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
 style="width:100%;max-width:640px;background:#fff;padding:16px;">
<tr><td style="padding:4px 8px 16px;">
<h2 style="margin:0 0 8px;font-size:20px;">{html.escape(title)}</h2>
<p style="color:#555;margin:0 0 8px;font-size:14px;">{intro}</p>
<p style="color:#888;font-size:12px;margin:0 0 12px;">Generated {now}. Spec: DAILY_ALERT_REPORT_PRIORITIES.md</p>
{body}
<p style="color:#666;font-size:12px;margin-top:20px">Manage alerts in the Alerts dashboard.</p>
</td></tr></table>
</td></tr></table>
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
        sections = build_alert_report_sections_html(
            alist, "advisor", include_advisor_col=False
        )
        mail_html = _wrap_sections(
            f"Daily alerts — {advisor.username or advisor.email}",
            f"{len(alist)} active/acknowledged alert(s) for your book "
            "(lead SLA alerts are on the consolidated report only).",
            sections,
        )
        if _send_html(
            f"Daily Alert Report ({len(alist)}) — {datetime.utcnow().strftime('%Y-%m-%d')}",
            mail_html,
            [advisor.email.strip()],
        ):
            sent += 1

    cons_sections = build_alert_report_sections_html(
        alerts, "consolidated", include_advisor_col=True
    )
    orphan_note = ""
    if orphan:
        orphan_note = (
            f" {len(orphan)} alert(s) have no client advisor / assignee email."
        )
    cons_html = _wrap_sections(
        "Daily Alert Report — consolidated",
        f"Total: {len(alerts)}. Advisor emails: {sent}.{orphan_note}",
        cons_sections,
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
