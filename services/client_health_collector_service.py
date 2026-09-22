"""
Client Health observation collector
===================================
Read-only fact gathering for the nightly cycle. Does **not** create Alerts
(see ``alert_creation_policy``). Emits observation dicts for the JSON pack.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from services.client_health_observations import make_observation

logger = logging.getLogger(__name__)


def _business_hours_since(start: datetime, now: datetime) -> float:
    """Working hours: Saturday counts; Sunday excluded (CAPTURED 2026-09)."""
    from services.working_hours import working_hours_since

    return working_hours_since(start, now)


def _planned_amount(workflow) -> float:
    try:
        return float(workflow.planned_amount or 0)
    except Exception:
        return 0.0


def collect_issue_observations() -> List[Dict[str, Any]]:
    """Open DataIntegrityIssue rows → observations (signal map)."""
    from models import DataIntegrityIssue, Client

    signal_map = {
        "duplicate_transaction": ("G1", "critical"),
        "future_date": ("G2", "critical"),
        "negative_holding": ("G3", "critical"),
        "negative_price": ("G4", "critical"),
        "unexecuted_recommendations_batch": ("G5", "warning"),
        "unrecorded_superseded_session": ("G5", "info"),
        "trade_execution_mismatch": ("G6", "critical"),
        "orphan_cashflow": ("G7", "warning"),
        "underperformance_vs_benchmark": ("I1", "warning"),
        "agreement_missing": ("J1", "critical"),
        "agreement_pdf_missing": ("J2", "critical"),
        "workflow_stalled": ("H1", "warning"),  # folded into A* for notify; keep observation for debug
        "amount_mismatch": ("H2", "warning"),
    }

    out: List[Dict[str, Any]] = []
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.status.in_(["open", "baseline"])
    ).all()
    for issue in issues:
        client = Client.query.get(issue.client_id)
        if client and hasattr(client, "is_active") and not client.is_active:
            continue
        name = issue.check_name or ""
        mapped = signal_map.get(name)
        if not mapped:
            # Still include unknown issues as generic facts
            signal_id, default_sev = ("X", issue.severity or "warning")
        else:
            signal_id, default_sev = mapped
        severity = issue.severity or default_sev
        if name == "underperformance_vs_benchmark":
            # Never promote to Alert later; card fact only
            signal_id = "I1"
        out.append(
            make_observation(
                client_id=issue.client_id,
                signal_id=signal_id,
                source="data_integrity_issue",
                signal=name,
                severity=severity,
                title=(issue.message or name)[:120],
                assignee_user_id=getattr(issue, "assigned_to", None)
                or (client.advisor_id if client else None),
                facts={
                    "issue_id": issue.id,
                    "check_category": issue.check_category,
                    "check_name": name,
                    "suggested_action": issue.suggested_action,
                },
                ref_id=f"issue:{issue.id}",
            )
        )
    return out


def collect_workflow_observations(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Monthly investment FUNDS / RECOS observations (A1, A2/B1). No Alert create."""
    from models import Workflow, Client
    from sqlalchemy.orm import joinedload

    now = now or datetime.utcnow()
    out: List[Dict[str, Any]] = []
    workflows = (
        Workflow.query.options(joinedload(Workflow.monthly_investment))
        .filter(Workflow.is_archived == False, Workflow.current_stage != "COMPLETED")  # noqa: E712
        .all()
    )
    for wf in workflows:
        mi = wf.monthly_investment
        if not mi:
            continue
        client_id = mi.client_id
        client = Client.query.get(client_id)
        if client and hasattr(client, "is_active") and not client.is_active:
            continue
        assignee = client.advisor_id if client else None
        stage = (wf.current_stage or "FUNDS").upper()
        if stage in ("ICR", "INVESTMENT/CHANGES/REDEMPTION"):
            stage = "FUNDS"
        amount = _planned_amount(wf)
        age_start = wf.updated_at or wf.created_at or now
        biz_hours = _business_hours_since(age_start, now)
        notes = (wf.notes or "") + "\n" + (wf.schedule_notes or "")
        notes_updated = bool((wf.notes or "").strip())
        # Heuristic: delay mentioned in notes
        delay_requested = "delay" in notes.lower()

        if stage == "FUNDS":
            out.append(
                make_observation(
                    client_id=client_id,
                    signal_id="A1",
                    source="monthly_workflow",
                    signal="FUNDS_delay",
                    severity="warning",
                    title=f"FUNDS pending (planned ₹{amount:,.0f})",
                    assignee_user_id=assignee,
                    facts={
                        "workflow_id": wf.id,
                        "stage": stage,
                        "investment_amount": amount,
                        "notes_updated": notes_updated,
                        "delay_requested": delay_requested,
                        "business_hours": round(biz_hours, 1),
                        "zero_investment": amount == 0,
                    },
                    ref_id=f"wf:{wf.id}:FUNDS",
                )
            )
        elif stage == "RECOS":
            # A2/B1: immediate radar; target 48 working hrs; escalate 72. Lean week = planning, not grace.
            severity = "critical" if biz_hours >= 72 else ("warning" if biz_hours >= 48 else "info")
            out.append(
                make_observation(
                    client_id=client_id,
                    signal_id="A2",
                    source="monthly_workflow",
                    signal="RECOS_delay",
                    severity=severity,
                    title="Funds ready; recommendation not sent",
                    assignee_user_id=assignee,
                    facts={
                        "workflow_id": wf.id,
                        "stage": stage,
                        "investment_amount": amount,
                        "funds_ready": True,
                        "working_hours": round(biz_hours, 1),
                        "sla_working_hours_target": 48,
                        "sla_working_hours_escalate": 72,
                        "lean_week_planning": True,
                    },
                    ref_id=f"wf:{wf.id}:RECOS",
                )
            )
            out.append(
                make_observation(
                    client_id=client_id,
                    signal_id="B1",
                    source="monthly_workflow",
                    signal="recommendation_not_sent",
                    severity=severity,
                    title="Funds ready; recommendation not sent",
                    assignee_user_id=assignee,
                    facts={
                        "workflow_id": wf.id,
                        "funds_ready": True,
                        "investment_amount": amount,
                        "working_hours": round(biz_hours, 1),
                    },
                    ref_id=f"wf:{wf.id}:B1",
                )
            )
        elif stage == "NOTIFY":
            from services.working_hours import wall_hours_since

            wall = wall_hours_since(age_start, now)
            severity = "critical" if wall >= 1.0 else "warning"
            out.append(
                make_observation(
                    client_id=client_id,
                    signal_id="A3",
                    source="monthly_workflow",
                    signal="NOTIFY_delay",
                    severity=severity,
                    title="Recommendations sent; notify client within 1 hour",
                    assignee_user_id=assignee,
                    facts={
                        "workflow_id": wf.id,
                        "stage": stage,
                        "wall_hours": round(wall, 2),
                        "sla_wall_hours": 1,
                    },
                    ref_id=f"wf:{wf.id}:NOTIFY",
                )
            )
        elif stage == "UPDATE":
            out.append(
                make_observation(
                    client_id=client_id,
                    signal_id="A5",
                    source="monthly_workflow",
                    signal="UPDATE_pending",
                    severity="warning" if biz_hours >= 24 else "info",
                    title="UPDATE pending — record the trade",
                    assignee_user_id=assignee,
                    facts={
                        "workflow_id": wf.id,
                        "stage": stage,
                        "working_hours": round(biz_hours, 1),
                    },
                    ref_id=f"wf:{wf.id}:UPDATE",
                )
            )
        # EXEC (former A4) ≡ G5 silence — covered by unexecuted-recommendation findings, not a parallel A4
    return out


def collect_review_pipeline_observations(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Review → Meeting → Billing → Closure style facts from ReviewWorkflow + notes."""
    from models import ReviewWorkflow, Client

    now = now or datetime.utcnow()
    today = now.date()
    out: List[Dict[str, Any]] = []
    open_statuses = ("initiated", "sent", "meeting")
    workflows = ReviewWorkflow.query.filter(ReviewWorkflow.status.in_(open_statuses)).all()

    for rw in workflows:
        client = Client.query.get(rw.client_id)
        if client and hasattr(client, "is_active") and not client.is_active:
            continue
        assignee = rw.assigned_to or (client.advisor_id if client else None)
        status = (rw.status or "").lower()
        review_date = rw.review_date
        days_until = (review_date - today).days if review_date else None

        # Annual / nearing (C1/C2 family): due within 30d or overdue
        if days_until is not None and days_until <= 30:
            signal_id = "C1" if days_until <= 0 else "C2"
            severity = "critical" if days_until <= 0 else "warning"
            out.append(
                make_observation(
                    client_id=rw.client_id,
                    signal_id=signal_id,
                    source="review_workflow",
                    signal="review_due" if days_until <= 0 else "review_upcoming",
                    severity=severity,
                    title=(
                        f"Review overdue ({abs(days_until)}d)"
                        if days_until <= 0
                        else f"Review upcoming in {days_until}d"
                    ),
                    assignee_user_id=assignee,
                    facts={
                        "review_workflow_id": rw.id,
                        "status": status,
                        "review_date": str(review_date),
                        "days_until": days_until,
                        "pipeline": "review→meeting→billing→closure",
                    },
                    ref_id=f"rw:{rw.id}:due",
                )
            )

        # Meeting done / past meeting_date but notes missing within 24h (D3)
        if rw.meeting_date:
            hours_since_meeting = (now - rw.meeting_date).total_seconds() / 3600
            notes_empty = not (rw.meeting_notes or "").strip()
            if notes_empty and hours_since_meeting >= 0:
                sev = "warning" if hours_since_meeting >= 24 else "info"
                if hours_since_meeting >= 1:  # only after meeting started/done
                    out.append(
                        make_observation(
                            client_id=rw.client_id,
                            signal_id="D3",
                            source="review_workflow",
                            signal="meeting_notes_missing",
                            severity=sev,
                            title="Meeting done; notes not updated within 24h",
                            assignee_user_id=assignee,
                            facts={
                                "review_workflow_id": rw.id,
                                "meeting_date": rw.meeting_date.isoformat(),
                                "hours_since_meeting": round(hours_since_meeting, 1),
                                "notes_due_within_hours": 24,
                            },
                            ref_id=f"rw:{rw.id}:notes",
                        )
                    )

        # Stage SLA hints (sent → meeting within 1–2 weeks of send)
        if status == "sent" and rw.updated_at:
            days_since_sent = (now - rw.updated_at).total_seconds() / 86400
            if days_since_sent >= 7:
                sev = "critical" if days_since_sent >= 14 else "warning"
                out.append(
                    make_observation(
                        client_id=rw.client_id,
                        signal_id="C1",
                        source="review_workflow",
                        signal="meeting_after_review_delay",
                        severity=sev,
                        title=(
                            "Meeting not done within 1 week of review sent"
                            if days_since_sent < 14
                            else "Meeting still open >2 weeks after review sent — concern"
                        ),
                        assignee_user_id=assignee,
                        facts={
                            "review_workflow_id": rw.id,
                            "days_since_sent": round(days_since_sent, 1),
                            "stage": "meeting",
                            "owner_hint": "admin_meeting_user_followup",
                        },
                        ref_id=f"rw:{rw.id}:meeting_sla",
                    )
                )

    return out


def collect_price_accuracy_observations() -> List[Dict[str, Any]]:
    """
    Latest PriceAccuracyFinding rows → P1 observations per client holding that security.
    Facts only; no Alert create.

    Batched queries (no per-finding Holding/Client lookups).

    Fan-out control:
    - SPIKE: one observation per (finding, client) — actionable anomalies.
    - MISSING/ZERO: one aggregated observation per client (counts only) — avoids
      tens of thousands of rows from global gap scans.
    """
    from collections import defaultdict

    from models import (
        Client,
        Holding,
        PriceAccuracyFinding,
        PriceAccuracyRun,
        PriceAccuracySpikeAck,
    )

    run = PriceAccuracyRun.query.order_by(PriceAccuracyRun.id.desc()).first()
    if not run:
        return []

    findings = PriceAccuracyFinding.query.filter_by(run_id=run.id).all()
    if not findings:
        return []

    ack_keys = set()
    try:
        from services.price_accuracy_service import load_spike_ack_keys

        ack_keys = load_spike_ack_keys()
    except Exception:
        try:
            for ack in PriceAccuracySpikeAck.query.all():
                ack_keys.add((ack.security_id, ack.date_prev, ack.date_next))
        except Exception:
            pass

    suppress_pack = None
    is_price_alert_closed = None
    try:
        from services.price_accuracy_sheet_suppress_service import (
            is_price_alert_closed,
            load_suppressions,
        )

        suppress_pack = load_suppressions()
    except Exception:
        suppress_pack = None

    spikes: List[Any] = []
    gap_findings: List[Any] = []  # MISSING / ZERO
    for f in findings:
        if is_price_alert_closed is not None and is_price_alert_closed(
            f, ack_keys=ack_keys, pack=suppress_pack
        ):
            continue
        if (f.kind or "").upper() == "SPIKE":
            spikes.append(f)
        else:
            gap_findings.append(f)

    security_ids = {f.security_id for f in spikes} | {f.security_id for f in gap_findings}
    if not security_ids:
        return []

    holdings = (
        Holding.query.filter(
            Holding.security_id.in_(security_ids),
            Holding.quantity > 0,
        ).all()
    )
    sec_to_clients: Dict[int, set] = defaultdict(set)
    all_client_ids: set = set()
    for h in holdings:
        sec_to_clients[h.security_id].add(h.client_id)
        all_client_ids.add(h.client_id)

    clients_by_id: Dict[int, Any] = {}
    if all_client_ids:
        for c in Client.query.filter(Client.id.in_(all_client_ids)).all():
            if hasattr(c, "is_active") and not c.is_active:
                continue
            clients_by_id[c.id] = c

    out: List[Dict[str, Any]] = []

    # SPIKE: one rollup per client (sample worst % moves) — Hub keeps row-level detail
    client_spike: Dict[int, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "samples": []}
    )
    for f in spikes:
        pct = float(f.pct_change) if f.pct_change is not None else 0.0
        sample = {
            "symbol": f.symbol,
            "pct_change": pct,
            "finding_id": f.id,
            "date_prev": f.date_prev.isoformat() if f.date_prev else None,
            "date_next": f.date_next.isoformat() if f.date_next else None,
        }
        for cid in sec_to_clients.get(f.security_id) or set():
            if cid not in clients_by_id:
                continue
            bucket = client_spike[cid]
            bucket["count"] += 1
            samples = bucket["samples"]
            samples.append(sample)
            samples.sort(key=lambda s: abs(s.get("pct_change") or 0), reverse=True)
            bucket["samples"] = samples[:5]

    for cid, bucket in client_spike.items():
        client = clients_by_id.get(cid)
        if client is None or bucket["count"] <= 0:
            continue
        top = bucket["samples"][0] if bucket["samples"] else {}
        title = f"Price spikes in holdings: {bucket['count']} (e.g. {top.get('symbol', '?')})"
        out.append(
            make_observation(
                client_id=cid,
                signal_id="P1",
                source="price_accuracy",
                signal="price_spikes_rollup",
                severity="warning",
                title=title[:120],
                assignee_user_id=client.advisor_id,
                facts={
                    "run_id": run.id,
                    "kind": "SPIKE_ROLLUP",
                    "spike_count": bucket["count"],
                    "samples": bucket["samples"],
                },
                ref_id=f"price:spikes:c{cid}:r{run.id}",
            )
        )

    # MISSING/ZERO: one rollup per client
    client_gap: Dict[int, Dict[str, Any]] = defaultdict(
        lambda: {"missing": 0, "zero": 0, "symbols": []}
    )
    for f in gap_findings:
        kind_key = (f.kind or "").lower()
        for cid in sec_to_clients.get(f.security_id) or set():
            if cid not in clients_by_id:
                continue
            bucket = client_gap[cid]
            if kind_key == "missing":
                bucket["missing"] += 1
            elif kind_key == "zero":
                bucket["zero"] += 1
            if f.symbol and len(bucket["symbols"]) < 8:
                if f.symbol not in bucket["symbols"]:
                    bucket["symbols"].append(f.symbol)

    for cid, bucket in client_gap.items():
        client = clients_by_id.get(cid)
        if client is None:
            continue
        total = int(bucket["missing"]) + int(bucket["zero"])
        if total <= 0:
            continue
        title = (
            f"Price gaps in holdings: {bucket['missing']} missing, {bucket['zero']} zero"
        )
        out.append(
            make_observation(
                client_id=cid,
                signal_id="P1",
                source="price_accuracy",
                signal="price_gaps_rollup",
                severity="info",
                title=title[:120],
                assignee_user_id=client.advisor_id,
                facts={
                    "run_id": run.id,
                    "kind": "GAPS_ROLLUP",
                    "missing_count": bucket["missing"],
                    "zero_count": bucket["zero"],
                    "sample_symbols": bucket["symbols"],
                },
                ref_id=f"price:gaps:c{cid}:r{run.id}",
            )
        )

    return out


def collect_cashflow_trade_observations() -> List[Dict[str, Any]]:
    """
    G8: material post-cutoff cashflow vs trade totals mismatch.
    Suggest-only; does not create Alerts.
    """
    from models import Client
    from services.cashflow_trade_mismatch_report_service import build_totals_mismatch_report

    report = build_totals_mismatch_report(
        active_only=True,
        diagnose_mismatches=True,
        material_only=True,
    )
    out: List[Dict[str, Any]] = []
    for row in report.get("mismatches") or []:
        if row.get("status") != "mismatch_material":
            continue
        cid = int(row["client_id"])
        client = Client.query.get(cid)
        diff = row.get("difference") or 0
        thresh = row.get("materiality_threshold") or 0
        out.append(
            make_observation(
                client_id=cid,
                signal_id="G8",
                source="cashflow_trade",
                signal="cashflow_trade_totals_mismatch",
                severity="warning",
                title=(
                    f"Cashflow vs trades mismatch "
                    f"(gap {diff:,.0f}; threshold {thresh:,.0f})"
                )[:120],
                facts={
                    "status": row.get("status"),
                    "recorded_net": row.get("recorded_net_cashflow"),
                    "post_cutoff_trade_net": row.get("post_cutoff_trade_net"),
                    "opening_trade_net": row.get("opening_trade_net"),
                    "difference": diff,
                    "materiality_threshold": thresh,
                    "guess_summary": (row.get("guess_summary") or "")[:300],
                    "resolution_url": f"/clients/{cid}/cashflow-trade-integrity",
                },
                assignee_user_id=client.advisor_id if client else None,
                ref_id=f"G8:cashflow_trade:{cid}",
            )
        )
    return out


def collect_all_observations(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Run all collectors. Fail soft per source."""
    now = now or datetime.utcnow()
    all_obs: List[Dict[str, Any]] = []
    for name, fn in (
        ("issues", collect_issue_observations),
        ("workflows", lambda: collect_workflow_observations(now)),
        ("reviews", lambda: collect_review_pipeline_observations(now)),
        ("price", collect_price_accuracy_observations),
        ("cashflow_trade", collect_cashflow_trade_observations),
    ):
        try:
            part = fn()
            all_obs.extend(part)
            logger.info("Client health collector %s: %s observations", name, len(part))
        except Exception as exc:
            logger.exception("Collector %s failed: %s", name, exc)
    return all_obs
