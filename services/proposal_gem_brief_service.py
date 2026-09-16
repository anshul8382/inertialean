"""Build a rich Gem handoff brief from lead CRM context."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional


def _fmt_dt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    try:
        return str(value)[:19]
    except Exception:
        return ""


def _clip(text: Optional[str], limit: int = 1200) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def compose_proposal_gem_brief(lead, proposal=None, *, max_calls: int = 8, max_meetings: int = 8) -> str:
    """
    Collect lead contact, proposal fields, risk quiz, notes, call logs, and meetings
    into one pasteable brief for Google Gem.
    """
    sections: List[str] = []

    # --- Identity ---
    lines = [
        "=== LEAD / CLIENT CONTEXT (for investment proposal) ===",
        f"Name: {(lead.name or '').strip() or '—'}",
        f"Email: {(lead.email or '').strip() or '—'}",
        f"Phone: {(lead.phone or '').strip() or '—'}",
        f"Status: {(lead.status or '').strip() or '—'}",
        f"Source: {(lead.source or '').strip() or '—'}",
        f"Referral by: {(lead.referral_by or '').strip() or '—'}",
        f"Temperature (1–5): {getattr(lead, 'temperature', None) or '—'}",
    ]
    try:
        if lead.user and getattr(lead.user, "username", None):
            lines.append(f"Assigned advisor: {lead.user.username}")
    except Exception:
        pass
    sections.append("\n".join(lines))

    # --- Proposal form fields ---
    details = (proposal.details_json if proposal else None) or {}
    detail_bits = []
    for key, label in (
        ("aum", "Approx. AUM / corpus"),
        ("fee", "Fee"),
        ("horizon", "Horizon"),
        ("objective", "Objective"),
    ):
        val = (details.get(key) or "").strip() if isinstance(details, dict) else ""
        if val:
            detail_bits.append(f"{label}: {val}")
    if proposal and (proposal.title or "").strip():
        detail_bits.insert(0, f"Proposal title: {proposal.title.strip()}")
    if detail_bits:
        sections.append("=== PROPOSAL INPUTS ===\n" + "\n".join(detail_bits))

    # --- Risk assessment ---
    try:
        from services.risk_assessment_service import latest_for_lead

        risk = latest_for_lead(lead.id)
    except Exception:
        risk = None
    if risk:
        rlines = [
            "=== RISK ASSESSMENT ===",
            f"Profile: {risk.risk_profile}",
            f"Time horizon score: {risk.time_horizon_score}",
            f"Risk tolerance score: {risk.risk_tolerance_score}",
            f"Suggested max equity: {risk.max_equity_pct}%",
        ]
        answers = risk.answers_json or {}
        narrative = (answers.get("investments_narrative") or "").strip()
        if narrative:
            rlines.append(f"Current investments (client): {_clip(narrative, 800)}")
        holdings = answers.get("holdings") or []
        if holdings:
            rlines.append("Holdings selected: " + ", ".join(str(h) for h in holdings))
        sections.append("\n".join(rlines))

    # --- Lead notes (human-readable only) ---
    try:
        from services.content_intelligence_person_source import strip_ic_markers

        notes = strip_ic_markers(lead.notes or "").strip()
    except Exception:
        notes = (lead.notes or "").strip()
    if notes:
        sections.append("=== LEAD NOTES ===\n" + _clip(notes, 2500))

    # --- Call logs ---
    try:
        from models import LeadCallLog

        calls = (
            LeadCallLog.query.filter_by(lead_id=lead.id)
            .order_by(LeadCallLog.call_date.desc())
            .limit(max_calls)
            .all()
        )
    except Exception:
        calls = []
    if calls:
        clines = ["=== CALL LOGS (newest first) ==="]
        for c in calls:
            note = _clip(c.notes, 600)
            if not note:
                continue
            clines.append(
                f"- {_fmt_dt(c.call_date)} [{c.call_type or 'call'}]: {note}"
            )
        if len(clines) > 1:
            sections.append("\n".join(clines))

    # --- Meetings ---
    try:
        from models import Meeting

        meetings = (
            Meeting.query.filter_by(lead_id=lead.id)
            .order_by(Meeting.meeting_date.desc())
            .limit(max_meetings)
            .all()
        )
    except Exception:
        meetings = []
    if meetings:
        mlines = ["=== MEETINGS (newest first) ==="]
        for m in meetings:
            body_parts = []
            if (m.description or "").strip():
                body_parts.append(_clip(m.description, 400))
            if (m.notes or "").strip():
                body_parts.append(_clip(m.notes, 600))
            body = " | ".join(body_parts) if body_parts else "(no notes)"
            mlines.append(
                f"- {_fmt_dt(m.meeting_date)} {m.title or 'Meeting'} "
                f"[{m.status or 'n/a'}]: {body}"
            )
        sections.append("\n".join(mlines))

    sections.append(
        "=== INSTRUCTION ===\n"
        "Using the context above, draft a clear investment proposal write-up "
        "suitable for an Inertia client proposal document. Use professional tone; "
        "structure with short section headings."
    )
    return "\n\n".join(sections)
