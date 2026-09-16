"""Map LeadProposal details into agreement create form defaults."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple


def _parse_money(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Keep digits and one decimal; strip currency words
    cleaned = re.sub(r"[^\d.]", "", s.replace(",", ""))
    if not cleaned:
        return None
    try:
        val = float(cleaned)
        if val <= 0:
            return None
        return f"{val:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        return None


def latest_proposal_for_lead(lead_id: int):
    try:
        from sqlalchemy import inspect
        from extensions import db
        from models.lead_onboarding import LeadProposal

        if not inspect(db.engine).has_table("lead_proposal"):
            return None
        return (
            LeadProposal.query.filter_by(lead_id=lead_id)
            .order_by(LeadProposal.created_at.desc())
            .first()
        )
    except Exception:
        return None


def proposal_prefill_for_agreement(lead_id: int) -> Dict[str, Any]:
    """
    Returns template/context defaults from the latest proposal.
    Keys: advisory_model, fixed_annual_fee, aum_note, horizon, objective, notes_extra, source_proposal_id
    """
    proposal = latest_proposal_for_lead(lead_id)
    if not proposal:
        return {}

    details = proposal.details_json or {}
    fee = _parse_money(details.get("fee"))
    aum = (details.get("aum") or "").strip()
    horizon = (details.get("horizon") or "").strip()
    objective = (details.get("objective") or "").strip()

    notes_parts = []
    if aum:
        notes_parts.append(f"Proposed AUM / corpus: {aum}")
    if horizon:
        notes_parts.append(f"Horizon: {horizon}")
    if objective:
        notes_parts.append(f"Objective: {objective}")
    if proposal.title:
        notes_parts.append(f"From proposal: {proposal.title}")

    out: Dict[str, Any] = {
        "source_proposal_id": proposal.id,
        "advisory_model": "fixed_fee" if fee else None,
        "fixed_annual_fee": fee,
        "first_year_fixed_annual_fee": fee,
        "notes_extra": "\n".join(notes_parts),
        "aum": aum,
        "horizon": horizon,
        "objective": objective,
        "fee_raw": details.get("fee") or "",
    }
    return {k: v for k, v in out.items() if v}


def apply_prefill_to_agreement_form(form, prefill: Dict[str, Any]) -> None:
    """Set WTForms defaults when empty (GET)."""
    if not prefill:
        return
    if prefill.get("advisory_model") and not form.advisory_model.data:
        form.advisory_model.data = prefill["advisory_model"]
    if prefill.get("notes_extra"):
        existing = (form.notes.data or "").strip()
        if not existing:
            form.notes.data = prefill["notes_extra"]
        elif prefill["notes_extra"] not in existing:
            form.notes.data = f"{existing}\n{prefill['notes_extra']}"
    if hasattr(form, "first_year_fixed_annual_fee") and prefill.get("first_year_fixed_annual_fee"):
        if not form.first_year_fixed_annual_fee.data:
            form.first_year_fixed_annual_fee.data = prefill["first_year_fixed_annual_fee"]
