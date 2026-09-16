"""
Client Health Priority Service
==============================
Deterministic ranking for the Client Health Status card.

Decisions live here (and in CLIENT_HEALTH_GUIDELINES.md). The LLM must not
change rank, invent alerts, or create tasks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

MAX_VIOLATIONS = 3
MAX_RECOMMENDATIONS = 3

# Domain tie-break (lower = higher priority). Matches guidelines MD.
DOMAIN_ORDER = {
    "investment_ops": 0,
    "integrity": 0,
    "workflow": 1,
    "billing": 2,
    "reviews": 2,
    "communication": 3,
    "agreement": 4,
    "performance": 5,
    "other": 6,
}

# Substrings / alert types that map to priority bands when severity alone is weak.
P0_CHECK_HINTS = (
    "duplicate_transaction",
    "future_date",
    "negative_holding",
    "negative_price",
    "agreement_missing",
    "agreement_pdf_missing",
    "unexecuted_recommendations_batch",
)
P1_TYPES = frozenset(
    {"workflow_sla", "recommendation", "billing", "review_overdue"}
)
P2_HINTS = (
    "agreement_not_active",
    "agreement_signed_date_missing",
    "agreement_name_mismatch",
    "agreement_pdf_name_not_found",
    "agreement_pdf_mismatch",
    "underperformance",
)
DROP_HINTS = (
    "agreement_pdf_unreadable",
    "amount_mismatch",
)

# Questionnaire signal_id → default band (overrides weak severity mapping).
SIGNAL_BAND = {
    "A1": "P1",
    "A2": "P0",
    "A3": "P1",
    "A4": "P0",
    "A5": "P2",
    "B1": "P0",
    "C1": "P0",
    "C2": "P1",
    "D1": "P2",
    "D2": "P1",
    "D3": "P1",
    "E1": "P1",
    "F1": "P1",
    "G1": "P0",
    "G2": "P0",
    "G3": "P0",
    "G4": "P0",
    "G5": "P0",
    "G6": "P1",
    "G7": "P1",
    "G8": "P1",
    "H1": None,  # prefer A*; drop from card
    "H2": "P2",
    "I1": "P1",
    "J1": "P0",
    "J2": "P0",
    "J3": "P2",
    "J4": "P2",
    "J5": "P1",
    "J6": None,
    "J7": "P1",
    "J8": "P2",
    "P1": "P1",  # price accuracy family
}


def _blob(item: Dict[str, Any]) -> str:
    parts = [
        str(item.get("type") or ""),
        str(item.get("subtype") or ""),
        str(item.get("title") or ""),
        str(item.get("description") or ""),
        str(item.get("check_name") or ""),
        str(item.get("signal") or ""),
        str(item.get("signal_id") or ""),
    ]
    return " ".join(parts).lower()


def _domain_key(item: Dict[str, Any]) -> str:
    domain = (item.get("domain") or "other").strip().lower()
    if domain in DOMAIN_ORDER:
        return domain
    sid = (item.get("signal_id") or "").upper()
    if sid.startswith("A") or sid == "B1":
        return "investment_ops"
    if sid.startswith("C") or sid == "D3":
        return "reviews"
    if sid.startswith("G") or sid == "H1":
        return "integrity"
    if sid == "I1":
        return "performance"
    if sid.startswith("J"):
        return "agreement"
    blob = _blob(item)
    if "agreement" in blob:
        return "agreement"
    if "underperformance" in blob or "xirr" in blob:
        return "performance"
    if item.get("type") in ("workflow_sla", "recommendation"):
        return "investment_ops"
    return "other"


def assign_priority_band(item: Dict[str, Any]) -> Optional[str]:
    """Return 'P0'|'P1'|'P2' or None to drop from the card."""
    severity = (item.get("severity") or "").strip().lower()
    age_hours = float(item.get("age_hours") or item.get("facts", {}).get("business_hours") or 0)
    blob = _blob(item)
    sid = (item.get("signal_id") or "").upper()

    if severity == "info" and sid != "D3":
        return None
    if any(h in blob for h in DROP_HINTS) and severity != "critical":
        return None

    if sid in SIGNAL_BAND:
        return SIGNAL_BAND[sid]  # may be None → drop from card

    if severity == "critical":
        return "P0"
    if any(h in blob for h in P0_CHECK_HINTS):
        return "P0"

    alert_type = (item.get("type") or "").strip().lower()
    if alert_type in P1_TYPES:
        return "P1"
    if "workflow_stalled" in blob:
        return "P1"
    if severity == "warning" and age_hours > 48:
        return "P1"

    if any(h in blob for h in P2_HINTS):
        return "P2"
    if severity == "warning":
        return "P2"

    return None


def _band_rank(band: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2}.get(band, 9)


def _severity_rank(severity: str) -> int:
    return {"critical": 0, "warning": 1, "info": 2}.get((severity or "").lower(), 3)


def rank_violations(violations: List[Dict[str, Any]], *, limit: int = MAX_VIOLATIONS) -> List[Dict[str, Any]]:
    """Filter + sort violations per guidelines; attach priority_band; cap at limit."""
    scored: List[Dict[str, Any]] = []
    for v in violations:
        band = assign_priority_band(v)
        if not band:
            continue
        item = dict(v)
        item["priority_band"] = band
        item["domain"] = _domain_key(item)
        scored.append(item)

    scored.sort(
        key=lambda x: (
            _band_rank(x["priority_band"]),
            DOMAIN_ORDER.get(x.get("domain") or "other", 6),
            _severity_rank(x.get("severity") or ""),
            -float(x.get("age_hours") or 0),
        )
    )
    return scored[: max(0, limit)]


def deterministic_improvement_action(item: Dict[str, Any]) -> str:
    """Fallback action text when LLM is unavailable."""
    blob = _blob(item)
    sid = (item.get("signal_id") or "").upper()
    facts = item.get("facts") or {}
    age = float(item.get("age_hours") or 0)

    if sid in ("A2", "B1") or "recommendation_not_sent" in blob or "recos_delay" in blob:
        if facts.get("zero_investment_grace_week") or facts.get("investment_amount") == 0:
            return "₹0 investment this cycle — recommendation can wait up to one week; still track on card."
        return (
            "Funds are ready — generate and send the recommendation within 24–48 business hours "
            "(weekends excluded)."
        )
    if sid == "A1" or "funds_delay" in blob:
        if facts.get("notes_updated") and facts.get("delay_requested"):
            return "Client delay noted — keep monitoring funds; notes already updated."
        if not facts.get("notes_updated"):
            return "Check with the client on funds and update workflow notes (asked / delay / confirmed)."
        return "Follow up on funds confirmation and keep notes current."
    if sid == "D3" or "meeting_notes" in blob:
        return "Meeting is done — update meeting notes within 24 hours."
    if sid in ("C1", "C2") or "review" in blob:
        return (
            "Advance the review pipeline (Review → Meeting → Billing → Closure); "
            "do not leave the stuck stage idle."
        )
    if "agreement_missing" in blob:
        return "Record the signed agreement under Clients → client → Record existing agreement."
    if "agreement_pdf_missing" in blob or ("pdf" in blob and "missing" in blob):
        return "Locate the signed PDF (e.g. Google Drive) and upload via the agreement Update details page."
    if "agreement" in blob:
        return "Open the agreement Update details page and reconcile status, dates, and names with the PDF."
    if item.get("type") == "workflow_sla" or "workflow" in blob:
        if "funds" in blob:
            return "Confirm funds received and advance the monthly investment workflow."
        if "exec" in blob:
            return "Execute pending recommendations and update the workflow stage."
        if "notify" in blob or "recos" in blob:
            return "Send or complete pending recommendations/notifications for this workflow."
        return "Clear the stalled workflow stage and update status."
    if item.get("type") == "billing" or "invoice" in blob:
        return "Follow up on the overdue invoice and update payment status."
    if "underperformance" in blob:
        return "Review portfolio vs benchmark drivers before the next client touchpoint."
    if age > 72:
        return "Resolve this critical/aged item on the client card today."
    return "Open the client health item and complete the required corrective action."


def deterministic_recommendations(ranked: List[Dict[str, Any]]) -> List[str]:
    """Build ≤3 one-line recommendations from ranked violations (no coaching essays)."""
    if not ranked:
        return ["No critical actions"]
    lines: List[str] = []
    for item in ranked[:MAX_RECOMMENDATIONS]:
        action = (item.get("improvement_action") or deterministic_improvement_action(item)).strip()
        title = (item.get("title") or "Issue").strip()
        # Keep one line; avoid duplicating the full title if action is already specific.
        if action:
            lines.append(action)
        else:
            lines.append(f"Address: {title}")
    # Dedupe while preserving order
    seen = set()
    out = []
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out[:MAX_RECOMMENDATIONS]
