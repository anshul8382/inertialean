"""In-app notification centre — undecided findings for the logged-in user.

Computed view (not a second mirror table). Decisions live in
``finding_notification_decision`` (snooze / ignore / commit).

Guidelines: docs/assistant_modules/FINDING_PROCESSING_GUIDELINES.md
Gate: services.db_cutover.finding_notification_enabled()
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from extensions import db
from services.db_cutover import finding_notification_enabled
from services.working_hours import wall_hours_since, working_hours_since

logger = logging.getLogger(__name__)

# Signals where ignore is not allowed (snooze only, and snooze must not cross floor).
IGNORE_NOT_ALLOWED: Set[str] = {
    "A1",
    "A2",
    "A3",
    "A5",
    "A6",
    "ATT1",
    "B1",
    "C1",
    "D2",
    "D3",
    "E1",
    "F1",
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    # G6 / H2: one_off ignore allowed (guidelines)
    "G7",
    "J1",
    "J2",
    "J5",
    "J7",
    "K2",
    "K3",
    "P1",
    "T1",
}

# Sort: urgent first, then important. Lower = higher on list.
URGENCY_RANK = {
    "A3": 10,  # 1h wall-clock
    "G6": 20,  # wrong/partial trade
    "A2": 30,
    "B1": 30,
    "F1": 40,  # important, not urgent
    "T1": 42,  # pending OpsTask
    "G5": 45,  # silence
    "G7": 50,
    "A5": 55,
    "D3": 60,
    "C1": 65,
    "E1": 70,
    "G1": 75,
    "G2": 75,
    "G3": 75,
    "G4": 75,
    "H2": 80,
    "J1": 85,
    "J2": 85,
    "A1": 90,
    "A6": 95,
    "D2": 100,
    "K2": 110,
    "K3": 110,
    "ATT1": 150,  # month-end attendance
    "I1": 200,
    "D1": 210,
    "C2": 220,
}

# Manager/admin firm radar — only these when not the assignee.
RADAR_SIGNALS: Set[str] = {"A2", "B1", "E1", "F1", "G7"}

# Priority bands for collapsible UI (urgent is derived from item.urgent).
IMPORTANT_SIGNALS: Set[str] = set(RADAR_SIGNALS) | {"A3", "G6", "T1", "K2"}
INFO_SIGNALS: Set[str] = {"C2", "ATT1", "D1", "MSG"}

SECTION_META = (
    ("urgent", "Urgent", False),  # always expanded
    ("important", "Important / radar", True),
    ("aging", "Aging", True),
    ("info", "Informational", True),
)


@dataclass
class NotificationItem:
    signal_id: str
    entity_type: str  # finding | review | workflow | ticket | lead
    entity_id: int
    title: str
    nudge_text: str
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    severity: str = "warning"
    age_hours: float = 0.0
    age_label: str = ""
    app_path: Optional[str] = None
    ignore_allowed: bool = False
    urgent: bool = False
    facts: Dict[str, Any] = field(default_factory=dict)
    # Grouped card: e.g. G6 batch lines ("Quantity: DIVISLAB …", "Price: NAUKRI, HDFC …")
    detail_lines: List[str] = field(default_factory=list)
    member_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _client_name(client) -> str:
    if not client:
        return "Unknown client"
    return getattr(client, "name", None) or f"Client #{client.id}"


def app_path_for_monthly_workflow(workflow) -> Optional[str]:
    """Deep-link to the monthly investment workflow record (canonical UI)."""
    if not workflow:
        return None
    mi_id = getattr(workflow, "monthly_investment_id", None)
    if mi_id:
        return f"/monthly-investments/{int(mi_id)}"
    return None


def app_path_for_workflow_id(workflow_id: Optional[int]) -> Optional[str]:
    if not workflow_id:
        return None
    try:
        from models import Workflow

        wf = Workflow.query.get(int(workflow_id))
        return app_path_for_monthly_workflow(wf)
    except Exception:
        return None


def app_path_for_recommendation_id(recommendation_id: Optional[int]) -> Optional[str]:
    """Resolve reco → monthly investment workflow record when possible."""
    if not recommendation_id:
        return None
    try:
        from models import Recommendation, Workflow
        from services.recommendation_issue_hierarchy_service import (
            workflow_id_for_recommendation,
        )

        rec = Recommendation.query.get(int(recommendation_id))
        if not rec:
            return None
        wid = workflow_id_for_recommendation(rec)
        path = app_path_for_workflow_id(wid)
        if path:
            return path
        # Fallback: edit that recommendation line
        return f"/edit-recommendation/{int(recommendation_id)}"
    except Exception:
        return f"/edit-recommendation/{int(recommendation_id)}"


def app_path_for_finding(issue) -> Optional[str]:
    """Recommendation / execution findings → workflow record; else hub."""
    details = issue.details if isinstance(getattr(issue, "details", None), dict) else {}
    check = (issue.check_name or "").lower()
    # Direct monthly investment id (H2 and some rec-exec payloads)
    mi_id = details.get("monthly_investment_id")
    if mi_id:
        return f"/monthly-investments/{int(mi_id)}"
    # Prefer explicit workflow_id in details
    wid = details.get("workflow_id")
    path = app_path_for_workflow_id(wid)
    if path:
        return path
    rid = issue.recommendation_id or details.get("recommendation_id")
    if rid and check in (
        "trade_execution_mismatch",
        "unexecuted_recommendations_batch",
        "unrecorded_superseded_session",
        "amount_mismatch",
        "workflow_stalled",
    ):
        path = app_path_for_recommendation_id(rid)
        if path:
            return path
    # Batch G5: first related recommendation
    related = details.get("related_items") or details.get("recommendation_ids") or []
    if isinstance(related, list) and related:
        first = related[0]
        if isinstance(first, dict):
            rid2 = first.get("recommendation_id") or first.get("id")
        else:
            rid2 = first
        if rid2:
            path = app_path_for_recommendation_id(int(rid2))
            if path:
                return path
    if check.startswith("agreement"):
        cid = issue.client_id
        return f"/clients/{cid}" if cid else "/hub/"
    return "/hub/"


def _stage_entered_at(workflow, stage: str) -> datetime:
    """Best-effort: latest WorkflowAction for stage, else updated_at/created_at."""
    try:
        from models import WorkflowAction

        action = (
            WorkflowAction.query.filter_by(workflow_id=workflow.id)
            .filter(WorkflowAction.action_type.ilike(f"%{stage}%"))
            .order_by(WorkflowAction.action_date.desc())
            .first()
        )
        if action and action.action_date:
            return action.action_date
    except Exception:
        pass
    return workflow.updated_at or workflow.created_at or datetime.utcnow()


def _is_oversight_user(user) -> bool:
    return bool(getattr(user, "is_admin", False) or getattr(user, "is_manager", False))


def _normalize_scope(user, scope: Optional[str]) -> str:
    """Advisors always 'mine'. Managers/admins: 'mine' (default) or 'firm'."""
    raw = (scope or "mine").lower().strip()
    if raw not in ("mine", "firm"):
        raw = "mine"
    if raw == "firm" and not _is_oversight_user(user):
        return "mine"
    return raw


def _visible_to_user(
    user,
    assignee_id: Optional[int],
    signal_id: str,
    *,
    scope: str = "mine",
) -> bool:
    """Assignee always sees their items. Oversight firm scope adds RADAR_SIGNALS only."""
    if assignee_id is not None and int(assignee_id) == int(user.id):
        return True
    if not _is_oversight_user(user):
        return False
    if (scope or "mine").lower() != "firm":
        return False
    return signal_id in RADAR_SIGNALS


def _priority_band(item: NotificationItem) -> str:
    if item.urgent:
        return "urgent"
    if item.signal_id in IMPORTANT_SIGNALS:
        return "important"
    if item.signal_id in INFO_SIGNALS:
        return "info"
    return "aging"


def _build_sections(items: List[NotificationItem]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[NotificationItem]] = {
        "urgent": [],
        "important": [],
        "aging": [],
        "info": [],
    }
    for item in items:
        buckets[_priority_band(item)].append(item)
    sections = []
    for sid, label, collapsed_default in SECTION_META:
        bucket = buckets[sid]
        sections.append(
            {
                "id": sid,
                "label": label,
                "collapsed": collapsed_default if sid != "urgent" else False,
                "count": len(bucket),
                "items": [x.to_dict() for x in bucket],
            }
        )
    return sections


def collect_live_items(
    user, now: Optional[datetime] = None, *, scope: str = "mine"
) -> List[NotificationItem]:
    """Build undecided notification candidates from live DB state (ungrouped)."""
    now = now or datetime.utcnow()
    scope = _normalize_scope(user, scope)
    items: List[NotificationItem] = []
    items.extend(_from_workflows(user, now, scope=scope))
    items.extend(_from_findings(user, now, scope=scope))
    items.extend(_from_reviews(user, now, scope=scope))
    items.extend(_from_tickets(user, now, scope=scope))
    items.extend(_from_ops_tasks(user, now))
    items.extend(_from_leads(user, now, scope=scope))
    items.extend(_from_attendance_month_end(user, now))
    return items


def _from_workflows(user, now: datetime, *, scope: str = "mine") -> List[NotificationItem]:
    from models import Client, Workflow
    from sqlalchemy.orm import joinedload

    out: List[NotificationItem] = []
    workflows = (
        Workflow.query.options(joinedload(Workflow.monthly_investment))
        .filter(Workflow.is_archived == False, Workflow.current_stage != "COMPLETED")  # noqa: E712
        .all()
    )
    for wf in workflows:
        mi = wf.monthly_investment
        if not mi:
            continue
        client = Client.query.get(mi.client_id)
        if client and hasattr(client, "is_active") and not client.is_active:
            continue
        assignee = client.advisor_id if client else None
        stage = (wf.current_stage or "FUNDS").upper()
        if stage in ("ICR", "INVESTMENT/CHANGES/REDEMPTION"):
            stage = "FUNDS"
        cname = _client_name(client)
        entered = _stage_entered_at(wf, stage)
        wh = working_hours_since(entered, now)
        wall = wall_hours_since(entered, now)

        if stage == "FUNDS":
            if not _visible_to_user(user, assignee, "A1", scope=scope):
                continue
            out.append(
                NotificationItem(
                    signal_id="A1",
                    entity_type="workflow",
                    entity_id=wf.id,
                    title=f"{cname} — FUNDS pending",
                    nudge_text=f"{cname} — funds not confirmed. Check with client; update notes.",
                    client_id=mi.client_id,
                    client_name=cname,
                    severity="warning",
                    age_hours=wh,
                    age_label=f"{wh:.0f} working hrs",
                    app_path=app_path_for_monthly_workflow(wf) or "/monthly-investments",
                    ignore_allowed=False,
                    facts={
                        "stage": stage,
                        "workflow_id": wf.id,
                        "monthly_investment_id": wf.monthly_investment_id,
                        "assignee_id": assignee,
                    },
                )
            )
        elif stage == "RECOS":
            if not _visible_to_user(user, assignee, "A2", scope=scope):
                continue
            urgent = wh >= 48
            sev = "critical" if wh >= 72 else ("warning" if wh >= 24 else "info")
            out.append(
                NotificationItem(
                    signal_id="A2",
                    entity_type="workflow",
                    entity_id=wf.id,
                    title=f"{cname} — recommendation not sent",
                    nudge_text=(
                        f"{cname} — funds ready; send recommendation "
                        f"(target ≤48 working hrs; escalate at 72). Now {wh:.0f}h."
                    ),
                    client_id=mi.client_id,
                    client_name=cname,
                    severity=sev,
                    age_hours=wh,
                    age_label=f"{wh:.0f} working hrs",
                    app_path=app_path_for_monthly_workflow(wf) or "/monthly-investments",
                    ignore_allowed=False,
                    urgent=urgent or wh >= 72,
                    facts={
                        "stage": stage,
                        "workflow_id": wf.id,
                        "monthly_investment_id": wf.monthly_investment_id,
                        "working_hours": round(wh, 1),
                        "assignee_id": assignee,
                    },
                )
            )
        elif stage == "NOTIFY":
            if not _visible_to_user(user, assignee, "A3", scope=scope):
                continue
            breached = wall >= 1.0
            out.append(
                NotificationItem(
                    signal_id="A3",
                    entity_type="workflow",
                    entity_id=wf.id,
                    title=f"{cname} — notify client now",
                    nudge_text=(
                        f"{cname} — recommendations just sent. Notify the client now "
                        f"(within 1 hour). Elapsed {wall * 60:.0f} min."
                    ),
                    client_id=mi.client_id,
                    client_name=cname,
                    severity="critical" if breached else "warning",
                    age_hours=wall,
                    age_label=(
                        f"{wall * 60:.0f} min"
                        if wall < 48
                        else f"{wall / 24:.1f}d"
                    ),
                    app_path=app_path_for_monthly_workflow(wf) or "/monthly-investments",
                    ignore_allowed=False,
                    urgent=breached,
                    facts={
                        "stage": stage,
                        "workflow_id": wf.id,
                        "monthly_investment_id": wf.monthly_investment_id,
                        "wall_hours": round(wall, 2),
                        "assignee_id": assignee,
                    },
                )
            )
        elif stage == "UPDATE":
            if not _visible_to_user(user, assignee, "A5", scope=scope):
                continue
            out.append(
                NotificationItem(
                    signal_id="A5",
                    entity_type="workflow",
                    entity_id=wf.id,
                    title=f"{cname} — UPDATE pending",
                    nudge_text=(
                        f"{cname} — UPDATE pending. Record the trade "
                        f"(ideally within 24 hours). Elapsed {wh:.0f} working hrs."
                    ),
                    client_id=mi.client_id,
                    client_name=cname,
                    severity="warning" if wh >= 24 else "info",
                    age_hours=wh,
                    age_label=f"{wh:.0f} working hrs",
                    app_path=app_path_for_monthly_workflow(wf) or "/monthly-investments",
                    ignore_allowed=False,
                    facts={
                        "stage": stage,
                        "workflow_id": wf.id,
                        "monthly_investment_id": wf.monthly_investment_id,
                        "assignee_id": assignee,
                    },
                )
            )
        # A4 ≡ G5 — EXEC silence covered by G5 findings / unexecuted checks, not a parallel A4
    return out


def _from_findings(user, now: datetime, *, scope: str = "mine") -> List[NotificationItem]:
    from models import Client, DataIntegrityIssue

    signal_map = {
        "duplicate_transaction": ("G1", False),
        "future_date": ("G2", False),
        "negative_holding": ("G3", False),
        "negative_price": ("G4", False),
        "unexecuted_recommendations_batch": ("G5", False),
        "unrecorded_superseded_session": ("G5", False),
        "trade_execution_mismatch": ("G6", True),
        "orphan_cashflow": ("G7", False),
        "underperformance_vs_benchmark": ("I1", False),
        "agreement_missing": ("J1", False),
        "agreement_pdf_missing": ("J2", False),
        "amount_mismatch": ("H2", False),
        # workflow_stalled (H1) folded into A* — skip parallel
    }
    nudge = {
        "G1": "Duplicate transaction — resolve the duplicate.",
        "G2": "Future-dated transaction — correct the date.",
        "G3": "Negative holding — reconcile quantity.",
        "G4": "Negative price — fix the price data.",
        "G5": "No matching trade (silence). Check WhatsApp, then send reminder.",
        "G6": "Trade differs from recommendation — reconcile or escalate.",
        "G7": "Cash inflow without matching buys — explain or invest within 48h.",
        "I1": "Portfolio underperforming benchmark — prep for next touchpoint.",
        "J1": "No agreement on file — record the signed agreement.",
        "J2": "Signed PDF missing — upload it.",
        "H2": "Actual investment differs from planned — reconcile (recurring → revise plan).",
    }

    out: List[NotificationItem] = []
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.status.in_(["open", "baseline"])
    ).all()
    for issue in issues:
        mapped = signal_map.get(issue.check_name or "")
        if not mapped:
            continue
        signal_id, urgent = mapped
        if signal_id == "H1":
            continue
        client = Client.query.get(issue.client_id)
        if client and hasattr(client, "is_active") and not client.is_active:
            continue
        assignee = issue.assigned_to or (client.advisor_id if client else None)
        if not _visible_to_user(user, assignee, signal_id, scope=scope):
            continue
        cname = _client_name(client)
        detected = issue.detected_at or now
        age = working_hours_since(detected, now) if signal_id != "G6" else wall_hours_since(detected, now)
        ignore_ok = signal_id not in IGNORE_NOT_ALLOWED
        details = issue.details if isinstance(issue.details, dict) else {}
        symbol = (
            details.get("security_symbol")
            or details.get("symbol")
            or _symbol_from_message(issue.message)
        )
        mismatch_kind = _g6_mismatch_kind(details, issue.message)
        out.append(
            NotificationItem(
                signal_id=signal_id,
                entity_type="finding",
                entity_id=issue.id,
                title=f"{cname} — {(issue.message or issue.check_name)[:80]}",
                nudge_text=f"{cname} — {nudge.get(signal_id, issue.suggested_action or issue.message or '')}",
                client_id=issue.client_id,
                client_name=cname,
                severity=issue.severity or ("critical" if urgent else "warning"),
                age_hours=age,
                age_label=f"{age:.0f}h",
                app_path=app_path_for_finding(issue),
                ignore_allowed=ignore_ok,
                urgent=urgent or (signal_id == "G7" and age >= 48),
                facts={
                    "issue_id": issue.id,
                    "check_name": issue.check_name,
                    "group_key": issue.group_key,
                    "recommendation_id": issue.recommendation_id,
                    "symbol": symbol,
                    "mismatch_kind": mismatch_kind,
                    "message": issue.message,
                    "details": details,
                    "assignee_id": assignee,
                },
            )
        )
    return out


def _symbol_from_message(message: Optional[str]) -> Optional[str]:
    if not message:
        return None
    # "DIVISLAB: quantity …" or "HDFCAMC: price target …"
    head = message.split(":", 1)[0].strip()
    if head and len(head) <= 24 and " " not in head:
        return head
    return None


def _g6_mismatch_kind(details: Dict[str, Any], message: Optional[str]) -> str:
    if details.get("quantity"):
        return "quantity"
    if details.get("price"):
        return "price"
    msg = (message or "").lower()
    if "quantity" in msg:
        return "quantity"
    if "price" in msg:
        return "price"
    return "other"


def _batch_label_from_group_key(group_key: Optional[str]) -> str:
    """RECOMMENDATION_MATCH_trade_execution_mismatch_2026_05 → May 2026 batch."""
    if not group_key:
        return "batch"
    import re

    m = re.search(r"(20\d{2})_(\d{2})$", group_key)
    if not m:
        return group_key[-20:]
    year, month = m.group(1), int(m.group(2))
    months = (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    )
    mon = months[month - 1] if 1 <= month <= 12 else str(month)
    return f"{mon} {year} reco batch"


def _sev_rank(sev: str) -> int:
    return {"critical": 3, "warning": 2, "info": 1}.get((sev or "").lower(), 0)


def group_same_client_signal(items: List[NotificationItem]) -> List[NotificationItem]:
    """Merge same client + signal (+ recommendation batch group_key) into one card.

    G6 example: Ishan Jain — 4 trade mismatches (May 2026 reco batch)
      Quantity: DIVISLAB (3→6, 100%)
      Price: HDFCAMC, NAUKRI, LALPATHLAB (with brief %)
    """
    buckets: Dict[tuple, List[NotificationItem]] = {}
    passthrough: List[NotificationItem] = []

    for item in items:
        # Only club findings (and optionally same-signal workflows later).
        if item.entity_type != "finding":
            passthrough.append(item)
            continue
        gkey = (item.facts or {}).get("group_key") or ""
        # Same client + signal + batch (group_key). No group_key → still club by client+signal.
        key = (item.client_id, item.signal_id, gkey)
        buckets.setdefault(key, []).append(item)

    grouped: List[NotificationItem] = []
    for (_cid, signal_id, gkey), members in buckets.items():
        if len(members) == 1:
            m = members[0]
            m.member_count = 1
            m.facts = dict(m.facts or {})
            m.facts["member_ids"] = [m.entity_id]
            grouped.append(m)
            continue
        grouped.append(_merge_finding_group(members, signal_id, gkey))

    return grouped + passthrough


def _merge_finding_group(
    members: List[NotificationItem], signal_id: str, gkey: str
) -> NotificationItem:
    members = sorted(members, key=lambda m: (-_sev_rank(m.severity), m.entity_id))
    primary = members[0]
    cname = primary.client_name or "Client"
    n = len(members)
    member_ids = [m.entity_id for m in members]
    max_age = max(m.age_hours for m in members)
    age_label = max(members, key=lambda m: m.age_hours).age_label
    worst_sev = max(members, key=lambda m: _sev_rank(m.severity)).severity
    urgent = any(m.urgent for m in members)
    batch = _batch_label_from_group_key(gkey) if gkey else "same type"

    if signal_id == "G6":
        qty_lines: List[str] = []
        price_bits: List[str] = []
        other_lines: List[str] = []
        for m in members:
            kind = (m.facts or {}).get("mismatch_kind") or "other"
            sym = (m.facts or {}).get("symbol") or "?"
            msg = (m.facts or {}).get("message") or m.title
            details = (m.facts or {}).get("details") or {}
            if kind == "quantity":
                q = details.get("quantity") or {}
                if q:
                    qty_lines.append(
                        f"{sym}: qty {q.get('expected_qty')}→{q.get('actual_qty')} "
                        f"({q.get('variance_pct', 0):.0f}%)"
                    )
                else:
                    qty_lines.append(msg.split("—")[-1].strip() if "—" in msg else msg)
            elif kind == "price":
                p = details.get("price") or {}
                if p:
                    price_bits.append(
                        f"{sym} ({p.get('variance_pct', 0):.1f}%: "
                        f"₹{p.get('target_price')}→₹{p.get('actual_price')})"
                    )
                else:
                    price_bits.append(sym)
            else:
                other_lines.append(f"{sym}: {msg}"[:120])

        detail_lines: List[str] = []
        if qty_lines:
            detail_lines.append("Quantity: " + "; ".join(qty_lines))
        if price_bits:
            detail_lines.append("Price: " + "; ".join(price_bits))
        detail_lines.extend(other_lines)

        title = f"{cname} — {n} trade mismatches ({batch})"
        nudge = (
            f"{cname} — trades differ from recommendation. "
            "Reconcile or escalate this reco batch."
        )
    elif signal_id == "G5":
        syms = [
            (m.facts or {}).get("symbol")
            or _symbol_from_message((m.facts or {}).get("message"))
            or "?"
            for m in members
        ]
        uniq = []
        for s in syms:
            if s not in uniq:
                uniq.append(s)
        detail_lines = [f"Schemes: {', '.join(uniq[:12])}" + ("…" if len(uniq) > 12 else "")]
        title = f"{cname} — {n} unexecuted recommendations ({batch})"
        nudge = f"{cname} — no matching trades (silence). Check WhatsApp, then remind."
    else:
        detail_lines = [
            ((m.facts or {}).get("message") or m.title)[:140] for m in members[:8]
        ]
        if n > 8:
            detail_lines.append(f"…and {n - 8} more")
        title = f"{cname} — {n}× {signal_id} ({batch})"
        nudge = primary.nudge_text

    facts = dict(primary.facts or {})
    facts.update(
        {
            "is_group": True,
            "member_ids": member_ids,
            "group_key": gkey or None,
            "member_count": n,
        }
    )
    # Prefer a workflow deep-link from any member
    app_path = primary.app_path
    for m in members:
        if m.app_path and "/monthly-investments/" in (m.app_path or ""):
            app_path = m.app_path
            break
    return NotificationItem(
        signal_id=signal_id,
        entity_type="finding",
        entity_id=min(member_ids),  # representative; decisions fan out via member_ids
        title=title,
        nudge_text=nudge,
        client_id=primary.client_id,
        client_name=cname,
        severity=worst_sev,
        age_hours=max_age,
        age_label=age_label,
        app_path=app_path,
        ignore_allowed=primary.ignore_allowed,
        urgent=urgent,
        facts=facts,
        detail_lines=detail_lines,
        member_count=n,
    )


def _from_reviews(user, now: datetime, *, scope: str = "mine") -> List[NotificationItem]:
    from models import Client, ReviewWorkflow

    out: List[NotificationItem] = []
    today = now.date()
    open_rows = ReviewWorkflow.query.filter(
        ReviewWorkflow.status.in_(["initiated", "sent", "meeting", "planned"])
    ).all()
    for rw in open_rows:
        client = Client.query.get(rw.client_id)
        if client and hasattr(client, "is_active") and not client.is_active:
            continue
        assignee = rw.assigned_to or (client.advisor_id if client else None)
        cname = _client_name(client)
        status = (rw.status or "").lower()
        review_date = rw.review_date
        days_until = (review_date - today).days if review_date else None

        # D3: meeting done, notes missing
        if status == "meeting" or (rw.meeting_date and not (rw.meeting_notes or "").strip()):
            if rw.meeting_date and not (rw.meeting_notes or "").strip():
                if _visible_to_user(user, assignee, "D3", scope=scope):
                    age = wall_hours_since(rw.meeting_date, now)
                    out.append(
                        NotificationItem(
                            signal_id="D3",
                            entity_type="review",
                            entity_id=rw.id,
                            title=f"{cname} — meeting notes required",
                            nudge_text=(
                                f"{cname} — meeting done; notes required before close "
                                f"(elapsed {age:.0f}h)."
                            ),
                            client_id=rw.client_id,
                            client_name=cname,
                            severity="critical" if age >= 24 else "warning",
                            age_hours=age,
                            age_label=f"{age:.0f}h",
                            app_path=f"/clients/review-workflow/{rw.id}/update",
                            ignore_allowed=False,
                            urgent=age >= 24,
                            facts={"review_id": rw.id, "status": status},
                        )
                    )

        if days_until is not None and days_until <= 0 and status != "closed":
            if _visible_to_user(user, assignee, "C1", scope=scope):
                out.append(
                    NotificationItem(
                        signal_id="C1",
                        entity_type="review",
                        entity_id=rw.id,
                        title=f"{cname} — review overdue",
                        nudge_text=f"{cname} — open review needs attention (status: {status}).",
                        client_id=rw.client_id,
                        client_name=cname,
                        severity="critical",
                        age_hours=float(abs(days_until) * 24),
                        age_label=f"{abs(days_until)}d overdue",
                        app_path=f"/clients/review-workflow/{rw.id}/update",
                        ignore_allowed=False,
                        facts={"review_id": rw.id, "status": status, "days_until": days_until},
                    )
                )
        elif days_until is not None and 0 < days_until <= 30 and status in ("planned", "initiated"):
            if _visible_to_user(user, assignee, "C2", scope=scope):
                out.append(
                    NotificationItem(
                        signal_id="C2",
                        entity_type="review",
                        entity_id=rw.id,
                        title=f"{cname} — review upcoming — plan now",
                        nudge_text=(
                            f"{cname} — review in {days_until} day(s). "
                            "Plan for this review: confirm date, agenda, and prep notes."
                        ),
                        client_id=rw.client_id,
                        client_name=cname,
                        severity="info",
                        age_hours=0,
                        age_label=f"in {days_until}d",
                        app_path=f"/clients/review-workflow/{rw.id}/update",
                        ignore_allowed=True,
                        facts={
                            "review_id": rw.id,
                            "days_until": days_until,
                            "done_means": "planned",
                            "done_label": "Planned",
                        },
                        detail_lines=[
                            "Done = you have planned for this review (not that the review is finished).",
                        ],
                    )
                )
    return out


def _from_tickets(user, now: datetime, *, scope: str = "mine") -> List[NotificationItem]:
    """F1 — open service tickets (SLA-near/past highlighted; assignee sees all open)."""
    from models import Client, ServiceTicket

    out: List[NotificationItem] = []
    tickets = ServiceTicket.query.filter(
        ServiceTicket.status.in_(["open", "in_progress", "snoozed"])
    ).all()
    for t in tickets:
        if (
            t.status == "snoozed"
            and t.snoozed_until
            and t.snoozed_until > now
        ):
            continue
        assignee = t.assigned_to
        near = False
        past = bool(t.sla_breached or getattr(t, "is_overdue", False))
        age_h = 0.0
        if t.sla_deadline:
            rem = (t.sla_deadline - now).total_seconds() / 3600.0
            near = rem <= 24 or rem < 0
            past = past or rem < 0
            age_h = max(0.0, -rem) if rem < 0 else (24 - rem if rem <= 24 else 0)
        sla_hot = near or past

        if sla_hot:
            if not _visible_to_user(user, assignee, "F1", scope=scope):
                continue
        else:
            # Open but not near SLA — assignee only (avoid manager flood).
            if assignee != user.id:
                continue

        client = Client.query.get(t.client_id) if t.client_id else None
        cname = _client_name(client) if client else "Ticket"
        title_snip = (t.title or "")[:80]
        if past:
            age_label = "past SLA"
            severity = "critical"
            nudge = f"{cname} — ticket past SLA — resolve or update now. {title_snip}"
        elif near:
            age_label = "near SLA"
            severity = "warning"
            nudge = f"{cname} — ticket approaching SLA — resolve or update now. {title_snip}"
        else:
            age_label = "open"
            severity = "info"
            created = t.created_at or now
            age_h = wall_hours_since(created, now)
            nudge = f"{cname} — open ticket needs attention. {title_snip}"
        out.append(
            NotificationItem(
                signal_id="F1",
                entity_type="ticket",
                entity_id=t.id,
                title=f"{cname} — ticket {t.ticket_number}",
                nudge_text=nudge,
                client_id=t.client_id,
                client_name=cname,
                severity=severity,
                age_hours=age_h,
                age_label=age_label,
                app_path=f"/tickets/{t.id}",
                ignore_allowed=False,
                urgent=past,
                facts={
                    "ticket_id": t.id,
                    "ticket_number": t.ticket_number,
                    "done_label": "Done",
                    "done_means": "resolved",
                },
            )
        )
    return out


def _from_ops_tasks(user, now: datetime) -> List[NotificationItem]:
    """T1 — pending OpsTasks assigned to the user (notification centre only)."""
    from models import Client, OpsTask

    out: List[NotificationItem] = []
    tasks = (
        OpsTask.query.filter(
            OpsTask.assigned_to == user.id,
            OpsTask.status.in_(["pending", "in_progress"]),
        )
        .order_by(OpsTask.deadline.asc())
        .limit(200)
        .all()
    )
    for task in tasks:
        if task.snoozed_until and task.snoozed_until > now:
            continue
        overdue = bool(getattr(task, "is_overdue", False)) or (
            task.deadline and task.deadline < now
        )
        due_soon = False
        age_h = 0.0
        if task.deadline:
            rem = (task.deadline - now).total_seconds() / 3600.0
            due_soon = 0 <= rem <= 24
            age_h = max(0.0, -rem) if rem < 0 else max(0.0, 24 - rem if rem <= 24 else 0)
        client = Client.query.get(task.client_id) if task.client_id else None
        cname = _client_name(client) if client else None
        name = (task.name or f"Task #{task.id}")[:120]
        title = f"{cname} — task" if cname else f"Task — {name}"
        if cname:
            title = f"{cname} — {name}"
        if overdue:
            nudge = f"Overdue task: {name}. Open and complete or reschedule."
            severity = "critical"
            age_label = "overdue"
        elif due_soon:
            nudge = f"Due within 24h: {name}."
            severity = "warning"
            age_label = "due soon"
        else:
            nudge = f"Pending task: {name}."
            severity = "info"
            age_label = "pending"
            created = task.created_at or now
            age_h = wall_hours_since(created, now)
        out.append(
            NotificationItem(
                signal_id="T1",
                entity_type="ops_task",
                entity_id=task.id,
                title=title,
                nudge_text=nudge,
                client_id=task.client_id,
                client_name=cname,
                severity=severity,
                age_hours=age_h,
                age_label=age_label,
                app_path=f"/tasks/{task.id}",
                ignore_allowed=False,
                urgent=overdue,
                facts={
                    "ops_task_id": task.id,
                    "deadline": task.deadline.isoformat() + "Z" if task.deadline else None,
                    "done_label": "Done",
                    "done_means": "completed",
                    "member_ids": [task.id],
                },
            )
        )
    return out


def _ist_today(now: datetime):
    """Calendar date in IST (UTC+5:30) for attendance / month-end checks."""
    from datetime import date as date_cls

    ist = now + timedelta(hours=5, minutes=30)
    return date_cls(ist.year, ist.month, ist.day)


def _month_end_date(year: int, month: int):
    import calendar
    from datetime import date as date_cls

    return date_cls(year, month, calendar.monthrange(year, month)[1])


def _from_attendance_month_end(user, now: datetime) -> List[NotificationItem]:
    """ATT1 — remind every user to mark attendance on/around month end."""
    from datetime import date as date_cls

    from models import Attendance

    today = _ist_today(now)
    last = _month_end_date(today.year, today.month)

    # Last calendar day of month, or first 3 days of next month for prior-month gaps.
    if today == last:
        year, month = today.year, today.month
        end_check = today
    elif today.day <= 3:
        if today.month == 1:
            year, month = today.year - 1, 12
        else:
            year, month = today.year, today.month - 1
        end_check = _month_end_date(year, month)
    else:
        return []

    start = date_cls(year, month, 1)
    # Expected days: calendar days in month through end_check (include all; leave is a mark).
    expected = []
    cursor = start
    while cursor <= end_check:
        expected.append(cursor)
        cursor = cursor + timedelta(days=1)

    marked = {
        r.date
        for r in Attendance.query.filter(
            Attendance.user_id == user.id,
            Attendance.date >= start,
            Attendance.date <= end_check,
        ).all()
    }
    missing = [d for d in expected if d not in marked]
    if not missing:
        return []

    entity_id = year * 100 + month
    month_label = date_cls(year, month, 1).strftime("%b %Y")
    return [
        NotificationItem(
            signal_id="ATT1",
            entity_type="attendance_month",
            entity_id=entity_id,
            title=f"Mark attendance — {month_label}",
            nudge_text=(
                f"Month-end: {len(missing)} day(s) still unmarked for {month_label}. "
                "Open Mark month and complete attendance."
            ),
            severity="warning",
            age_hours=float(len(missing) * 24),
            age_label=f"{len(missing)} days missing",
            app_path=f"/attendance/mark-month?year={year}&month={month}",
            ignore_allowed=False,
            urgent=today == last,
            facts={
                "year": year,
                "month": month,
                "missing_count": len(missing),
                "done_label": "Done",
                "done_means": "attendance marked",
                "member_ids": [entity_id],
            },
        )
    ]


def _from_leads(user, now: datetime, *, scope: str = "mine") -> List[NotificationItem]:
    """K2/K3 — lead SLA, follow-ups, and stuck lead workflows (notification centre only)."""
    out: List[NotificationItem] = []
    try:
        from alert_system_models import Alert
        from models import GenericWorkflow, Lead
    except Exception:
        return out

    # --- Alerts tied to a lead (SLA, follow-up, meeting reminders) ---
    try:
        alerts = (
            Alert.query.filter(Alert.status == "active", Alert.lead_id.isnot(None))
            .order_by(Alert.created_at.desc())
            .limit(300)
            .all()
        )
    except Exception as exc:
        logger.debug("lead alerts query skipped: %s", exc)
        alerts = []

    for a in alerts:
        lead_id = a.lead_id
        assignee = a.user_id
        alert_type = (a.alert_type or "").lower()
        signal_id = "K2" if alert_type == "lead_sla" else "K3"
        if not _visible_to_user(user, assignee, signal_id, scope=scope):
            continue
        lead = Lead.query.get(lead_id) if lead_id else None
        if lead and hasattr(lead, "is_active") and lead.is_active is False:
            continue
        name = (lead.name if lead else None) or (a.title or "Lead")
        created = a.created_at or now
        age = working_hours_since(created, now)
        subtype = a.alert_subtype or ""
        desc = (a.description or a.title or "")[:140]
        title = f"{name} — lead {('SLA' if signal_id == 'K2' else 'follow-up')}"
        if subtype:
            title = f"{name} — {subtype.replace('_', ' ')}"
        out.append(
            NotificationItem(
                signal_id=signal_id,
                entity_type="lead_alert",
                entity_id=int(a.id),
                title=title,
                nudge_text=f"{desc}",
                client_id=lead.client_id if lead else a.client_id,
                client_name=name,
                severity=a.severity or ("critical" if signal_id == "K2" else "warning"),
                age_hours=age,
                age_label=f"{age:.0f} working hrs",
                app_path=f"/workflows/lead/{lead_id}" if lead_id else f"/leads/{lead_id}",
                ignore_allowed=False,
                urgent=signal_id == "K2" and age >= 24,
                facts={
                    "alert_id": a.id,
                    "lead_id": lead_id,
                    "alert_type": a.alert_type,
                    "alert_subtype": subtype,
                    "member_ids": [a.id],
                },
                detail_lines=[desc] if desc else [],
            )
        )

    # --- Active lead GenericWorkflow overdue / due soon ---
    try:
        today = now.date()
        gws = (
            GenericWorkflow.query.filter_by(module_type="lead", status="active")
            .limit(400)
            .all()
        )
    except Exception as exc:
        logger.debug("lead workflows query skipped: %s", exc)
        gws = []

    for gw in gws:
        lead = Lead.query.get(gw.record_id)
        if not lead or (hasattr(lead, "is_active") and lead.is_active is False):
            continue
        assignee = lead.user_id
        if not _visible_to_user(user, assignee, "K3", scope=scope):
            continue
        target = gw.target_completion_date
        if not target:
            continue
        days_left = (target - today).days
        # Nudge when overdue or due within 3 days
        if days_left > 3:
            continue
        overdue = days_left < 0
        stage = (gw.current_stage or "stage").replace("_", " ")
        name = lead.name or f"Lead #{lead.id}"
        age = working_hours_since(gw.updated_at or gw.created_at or now, now)
        out.append(
            NotificationItem(
                signal_id="K3",
                entity_type="lead_workflow",
                entity_id=int(gw.id),
                title=f"{name} — lead workflow: {stage}",
                nudge_text=(
                    f"{name} — workflow stage '{stage}' "
                    + (
                        f"overdue by {abs(days_left)} day(s). Advance or update the lead workflow."
                        if overdue
                        else f"due in {days_left} day(s). Plan next step on the lead workflow."
                    )
                ),
                client_id=lead.client_id,
                client_name=name,
                severity="critical" if overdue else "warning",
                age_hours=age,
                age_label=(
                    f"{abs(days_left)}d overdue" if overdue else f"due in {days_left}d"
                ),
                app_path=f"/workflows/lead/{lead.id}",
                ignore_allowed=False,
                urgent=overdue,
                facts={
                    "lead_id": lead.id,
                    "generic_workflow_id": gw.id,
                    "stage": gw.current_stage,
                    "target_completion_date": target.isoformat(),
                    "done_means": "advanced",
                    "done_label": "Advanced",
                    "member_ids": [gw.id],
                },
                detail_lines=[
                    f"Stage: {stage}",
                    f"Target: {target.isoformat()}",
                    "Done = you advanced / updated this lead workflow step.",
                ],
            )
        )

    return out


def _decision_key(entity_type: str, entity_id: int) -> tuple:
    return (entity_type, int(entity_id))


def _load_decisions(user_id: int) -> Dict[tuple, Any]:
    if not finding_notification_enabled():
        return {}
    from models.finding_notification_decision import FindingNotificationDecision

    rows = FindingNotificationDecision.query.filter_by(user_id=user_id).all()
    return {_decision_key(r.entity_type, r.entity_id): r for r in rows}


def list_for_user(
    user, *, view: str = "open", scope: Optional[str] = None
) -> Dict[str, Any]:
    """Return notification items for the user.

    view:
      open     — undecided findings (default)
      snoozed  — snoozed findings
      messages — peer inbox
    scope:
      mine — assignee-only (default; used for bell badge)
      firm — mine + RADAR_SIGNALS (managers/admins only)
    """
    view = (view or "open").lower().strip()
    if view not in ("open", "snoozed", "messages"):
        view = "open"
    scope = _normalize_scope(user, scope)
    can_firm = _is_oversight_user(user)

    from services import peer_message_service as pms
    from services.db_cutover import peer_messages_enabled

    msg_open = pms.list_inbox(user, view="open")
    message_count = msg_open.get("count", 0) if msg_open.get("enabled") else 0

    if view == "messages":
        return {
            "enabled": peer_messages_enabled(),
            "view": "messages",
            "scope": scope,
            "can_firm_scope": can_firm,
            "count": 0,
            "urgent_count": 0,
            "snoozed_count": 0,
            "message_count": message_count,
            "peer_enabled": peer_messages_enabled(),
            "sections": [],
            "items": msg_open.get("items") or [],
            "message": None
            if peer_messages_enabled()
            else "Peer messages waiting on user_peer_message table.",
        }

    if not finding_notification_enabled():
        return {
            "enabled": False,
            "view": view,
            "scope": scope,
            "can_firm_scope": can_firm,
            "count": 0,
            "urgent_count": 0,
            "snoozed_count": 0,
            "message_count": message_count,
            "peer_enabled": peer_messages_enabled(),
            "sections": [],
            "items": [],
            "message": "Notification centre waiting on finding_notification_decision table.",
        }

    now = datetime.utcnow()
    decisions = _load_decisions(user.id)
    live = collect_live_items(user, now, scope=scope)

    open_items: List[NotificationItem] = []
    snoozed_items: List[NotificationItem] = []

    for item in live:
        key = _decision_key(item.entity_type, item.entity_id)
        # For grouped findings, any member may be snoozed — check representative + members later
        member_ids = (item.facts or {}).get("member_ids") or [item.entity_id]
        member_keys = [_decision_key(item.entity_type, mid) for mid in member_ids]

        # If all members committed/ignored → drop entirely
        statuses = []
        snooze_untils = []
        for mk in member_keys:
            dec = decisions.get(mk)
            if not dec:
                statuses.append(None)
                continue
            if dec.decision == "committed" or dec.decision == "ignored":
                statuses.append(dec.decision)
            elif dec.decision == "snoozed" and dec.snooze_until and dec.snooze_until > now:
                statuses.append("snoozed")
                snooze_untils.append(dec.snooze_until)
            else:
                statuses.append(None)  # expired snooze → open again

        if statuses and all(s in ("committed", "ignored") for s in statuses):
            continue

        if statuses and all(s == "snoozed" for s in statuses if s is not None) and any(
            s == "snoozed" for s in statuses
        ):
            # Entire batch actively snoozed
            until = max(snooze_untils) if snooze_untils else None
            item.facts = dict(item.facts or {})
            item.facts["snooze_until"] = until.isoformat() + "Z" if until else None
            item.facts["decision"] = "snoozed"
            snoozed_items.append(item)
            continue

        if any(s == "snoozed" for s in statuses) and any(s is None for s in statuses):
            # Partial batch snoozed — keep open with remaining members only (re-group later)
            pass

        # Skip if representative alone is committed/ignored (non-group)
        dec = decisions.get(key)
        if dec:
            if dec.decision in ("committed", "ignored"):
                continue
            if dec.decision == "snoozed" and dec.snooze_until and dec.snooze_until > now:
                item.facts = dict(item.facts or {})
                item.facts["snooze_until"] = dec.snooze_until.isoformat() + "Z"
                item.facts["decision"] = "snoozed"
                snoozed_items.append(item)
                continue
        open_items.append(item)

    open_items = group_same_client_signal(open_items)
    snoozed_items = group_same_client_signal(snoozed_items)

    def _sort(items: List[NotificationItem]) -> List[NotificationItem]:
        items.sort(
            key=lambda x: (
                0 if x.urgent else 1,
                URGENCY_RANK.get(x.signal_id, 500),
                -x.age_hours,
            )
        )
        return items

    open_items = _sort(open_items)
    snoozed_items = _sort(snoozed_items)
    chosen = snoozed_items if view == "snoozed" else open_items
    sections = _build_sections(chosen)

    return {
        "enabled": True,
        "view": view,
        "scope": scope,
        "can_firm_scope": can_firm,
        "count": len(open_items),
        "urgent_count": sum(1 for x in open_items if x.urgent),
        "snoozed_count": len(snoozed_items),
        "message_count": message_count,
        "peer_enabled": peer_messages_enabled(),
        "sections": sections,
        "items": [x.to_dict() for x in chosen],
    }


def apply_decision(
    user,
    *,
    entity_type: str,
    entity_id: int,
    decision: str,
    signal_id: Optional[str] = None,
    snooze_until: Optional[datetime] = None,
    reason: Optional[str] = None,
    create_task: bool = False,
    task_deadline: Optional[datetime] = None,
    member_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Record snooze / ignore / commit. Enforces ignore_not_allowed.

    For grouped cards, pass ``member_ids`` so the decision applies to every
    finding in the batch (not only the representative entity_id).
    """
    if not finding_notification_enabled():
        return {"ok": False, "error": "notification_centre_disabled"}

    decision = (decision or "").lower().strip()
    if decision not in ("snoozed", "ignored", "committed", "unsnooze"):
        return {"ok": False, "error": "invalid_decision"}

    if decision == "ignored" and signal_id and signal_id in IGNORE_NOT_ALLOWED:
        return {
            "ok": False,
            "error": "ignore_not_allowed",
            "message": f"{signal_id} cannot be ignored — snooze or act.",
        }

    if decision == "snoozed":
        if not snooze_until:
            snooze_until = datetime.utcnow() + timedelta(hours=4)
        if signal_id == "A3" and snooze_until > datetime.utcnow() + timedelta(minutes=30):
            snooze_until = datetime.utcnow() + timedelta(minutes=30)

    ids = [int(x) for x in (member_ids or [entity_id]) if x is not None]
    if entity_id and int(entity_id) not in ids:
        ids.append(int(entity_id))
    ids = sorted(set(ids))

    from models.finding_notification_decision import FindingNotificationDecision

    if decision == "unsnooze":
        FindingNotificationDecision.query.filter(
            FindingNotificationDecision.user_id == user.id,
            FindingNotificationDecision.entity_type == entity_type,
            FindingNotificationDecision.entity_id.in_(ids),
            FindingNotificationDecision.decision == "snoozed",
        ).delete(synchronize_session=False)
        db.session.commit()
        return {"ok": True, "decision": "unsnooze", "applied_to": ids}

    ops_task_id = None
    for eid in ids:
        row = FindingNotificationDecision.query.filter_by(
            user_id=user.id, entity_type=entity_type, entity_id=eid
        ).first()
        if not row:
            row = FindingNotificationDecision(
                user_id=user.id,
                entity_type=entity_type,
                entity_id=eid,
                decision=decision,
            )
            db.session.add(row)
        row.decision = decision
        row.snooze_until = snooze_until if decision == "snoozed" else None
        row.reason = reason
        row.updated_at = datetime.utcnow()

        if decision == "committed" and create_task and ops_task_id is None:
            ops_task_id = _opt_in_task(
                user, entity_type, eid, signal_id, task_deadline, reason
            )
            row.ops_task_id = ops_task_id

    db.session.commit()
    return {
        "ok": True,
        "decision": decision,
        "ops_task_id": ops_task_id,
        "applied_to": ids,
    }


def _opt_in_task(
    user,
    entity_type: str,
    entity_id: int,
    signal_id: Optional[str],
    deadline: Optional[datetime],
    reason: Optional[str],
) -> Optional[int]:
    """User-opted OpsTask — never auto from rules (rule 6)."""
    try:
        from models import OpsTask

        deadline = deadline or (datetime.utcnow() + timedelta(days=2))
        name = f"Notification commit — {signal_id or entity_type} #{entity_id}"
        notes = (
            f"User-created from notification centre. "
            f"entity={entity_type}:{entity_id}. {reason or ''}"
        ).strip()
        task = OpsTask(
            name=name[:200],
            deadline=deadline,
            assigned_to=user.id,
            created_by=user.id,
            status="pending",
            notes=notes,
        )
        if entity_type == "finding":
            task.data_integrity_issue_id = entity_id
        elif entity_type == "review":
            task.review_workflow_id = entity_id
        db.session.add(task)
        db.session.flush()
        return task.id
    except Exception as exc:
        logger.warning("opt-in task create failed: %s", exc)
        return None
