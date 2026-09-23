"""
Capture PAN + agreement start date during lead onboarding for regulatory reporting.

Sources of truth going forward:
- LeadKycProfile.pan (KYC step)
- AgreementVariables ``pan`` and ``agreement_date`` (agreement step)

Call sites should sync KYC → agreement variables whenever either side is saved.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from extensions import db

logger = logging.getLogger(__name__)

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def normalize_pan(raw: Optional[str]) -> str:
    return (raw or "").strip().upper()[:10]


def is_valid_pan(raw: Optional[str]) -> bool:
    pan = normalize_pan(raw)
    return bool(pan and _PAN_RE.fullmatch(pan))


def get_lead_kyc_pan(lead_id: int) -> str:
    try:
        from services.lead_kyc_profile_service import get_or_create_profile, kyc_table_ready

        if not kyc_table_ready():
            return ""
        profile = get_or_create_profile(lead_id)
        if not profile:
            return ""
        pan = normalize_pan(profile.pan)
        return pan if is_valid_pan(pan) else ""
    except Exception:
        logger.debug("KYC PAN read failed lead=%s", lead_id, exc_info=True)
        return ""


def lead_has_regulatory_pan(lead_id: int) -> bool:
    return bool(get_lead_kyc_pan(lead_id))


def upsert_agreement_variable(
    *,
    agreement_id: int,
    lead_id: Optional[int],
    client_id: Optional[int],
    variable_name: str,
    variable_value: str,
    variable_type: str = "identity",
    force: bool = False,
) -> None:
    from models import AgreementVariables

    value = (variable_value or "").strip()
    if not value:
        return
    existing = AgreementVariables.query.filter_by(
        agreement_id=agreement_id,
        variable_name=variable_name,
    ).first()
    if existing:
        if force or not (existing.variable_value or "").strip():
            existing.variable_value = value
        elif variable_name == "pan" and not is_valid_pan(existing.variable_value):
            existing.variable_value = value
        elif variable_name == "agreement_date":
            existing.variable_value = value
        if client_id and not existing.client_id:
            existing.client_id = client_id
        if lead_id and not existing.lead_id:
            existing.lead_id = lead_id
        return
    db.session.add(
        AgreementVariables(
            agreement_id=agreement_id,
            lead_id=lead_id,
            client_id=client_id,
            variable_name=variable_name,
            variable_value=value,
            variable_type=variable_type,
        )
    )


def save_pan_for_agreement(
    agreement,
    lead_id: Optional[int],
    client_id: Optional[int],
    pan: Optional[str] = None,
    *,
    force: bool = False,
) -> str:
    """Persist PAN on agreement variables. Prefer explicit pan, else KYC."""
    resolved = normalize_pan(pan) if pan else ""
    if not is_valid_pan(resolved) and lead_id:
        resolved = get_lead_kyc_pan(lead_id)
    if not is_valid_pan(resolved):
        return ""
    upsert_agreement_variable(
        agreement_id=agreement.id,
        lead_id=lead_id,
        client_id=client_id,
        variable_name="pan",
        variable_value=resolved,
        variable_type="identity",
        force=force,
    )
    return resolved


def save_agreement_start_date_var(
    agreement,
    lead_id: Optional[int],
    client_id: Optional[int],
) -> str:
    from services.agreement_date_service import format_agreement_date_iso, resolve_agreement_date

    d = resolve_agreement_date(agreement)
    iso = format_agreement_date_iso(d)
    if not iso:
        return ""
    upsert_agreement_variable(
        agreement_id=agreement.id,
        lead_id=lead_id,
        client_id=client_id,
        variable_name="agreement_date",
        variable_value=iso,
        variable_type="agreement_meta",
        force=True,
    )
    return iso


def ensure_regulatory_fields_on_agreement(
    agreement,
    lead_id: Optional[int],
    client_id: Optional[int],
    *,
    pan: Optional[str] = None,
) -> Dict[str, Any]:
    """Write PAN + agreement_date onto the agreement (best-effort)."""
    saved_pan = save_pan_for_agreement(agreement, lead_id, client_id, pan=pan)
    if not saved_pan:
        from services.regulatory_client_master_service import extract_pan_from_agreement_pdf

        pdf_pan = extract_pan_from_agreement_pdf(agreement)
        if pdf_pan:
            saved_pan = save_pan_for_agreement(
                agreement, lead_id, client_id, pan=pdf_pan
            )
    saved_date = save_agreement_start_date_var(agreement, lead_id, client_id)
    return {"pan": saved_pan, "agreement_date": saved_date}


def _sync_pan_to_kyc_if_empty(lead_id: Optional[int], pan: str) -> None:
    if not lead_id or not is_valid_pan(pan):
        return
    try:
        from services.lead_kyc_profile_service import get_or_create_profile, kyc_table_ready

        if not kyc_table_ready():
            return
        profile = get_or_create_profile(lead_id)
        if profile and not normalize_pan(profile.pan):
            profile.pan = normalize_pan(pan)
    except Exception:
        logger.debug("KYC PAN backfill skipped lead=%s", lead_id, exc_info=True)


def _client_has_stored_pan(client_id: int) -> bool:
    from models import AgreementVariables, Lead

    row = (
        AgreementVariables.query.filter(
            AgreementVariables.client_id == client_id,
            AgreementVariables.variable_name == "pan",
        )
        .order_by(AgreementVariables.id.desc())
        .first()
    )
    if row and is_valid_pan(row.variable_value):
        return True
    for L in Lead.query.filter_by(client_id=client_id).all():
        if get_lead_kyc_pan(L.id):
            return True
        lead_row = (
            AgreementVariables.query.filter(
                AgreementVariables.lead_id == L.id,
                AgreementVariables.variable_name == "pan",
            )
            .order_by(AgreementVariables.id.desc())
            .first()
        )
        if lead_row and is_valid_pan(lead_row.variable_value):
            return True
    return False


def refresh_client_identity_from_agreement(
    client_id: int,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Re-read best agreement PDF for this client and persist PAN + start date.

    Use after uploading/replacing an agreement. ``force`` overwrites an existing
    valid PAN variable with the PDF value.
    """
    from models import Client
    from services.agreement_date_service import format_agreement_date_iso, resolve_agreement_date
    from services.regulatory_client_master_service import (
        extract_pan_from_agreement_pdf,
        get_best_agreement_for_client,
    )

    client = Client.query.get(client_id)
    if not client:
        return {"success": False, "error": "Client not found."}

    ag = get_best_agreement_for_client(client_id)
    if not ag:
        return {
            "success": False,
            "error": "No agreement on file for this client. Record/upload an agreement first.",
            "client_id": client_id,
            "client_name": client.name,
        }

    lead_id = ag.lead_id
    pdf_pan = extract_pan_from_agreement_pdf(ag)

    existing_pan = ""
    for v in ag.variables or []:
        if (v.variable_name or "") == "pan" and is_valid_pan(v.variable_value):
            existing_pan = normalize_pan(v.variable_value)
            break
    if not existing_pan and lead_id:
        existing_pan = get_lead_kyc_pan(lead_id)

    pan_to_save = ""
    if pdf_pan and (force or not existing_pan):
        pan_to_save = save_pan_for_agreement(
            ag, lead_id, client_id, pan=pdf_pan, force=force
        )
    elif existing_pan:
        pan_to_save = save_pan_for_agreement(
            ag, lead_id, client_id, pan=existing_pan
        )

    if pan_to_save:
        _sync_pan_to_kyc_if_empty(lead_id, pan_to_save)

    start = save_agreement_start_date_var(ag, lead_id, client_id)
    if not start:
        try:
            start = format_agreement_date_iso(resolve_agreement_date(ag))
        except Exception:
            start = ""

    db.session.commit()

    if not pan_to_save and not pdf_pan:
        return {
            "success": True,
            "client_id": client_id,
            "client_name": client.name,
            "agreement_id": ag.id,
            "pan": "",
            "pan_from_pdf": "",
            "agreement_start_date": start or "",
            "warning": "Agreement PDF found but no PAN text could be extracted. Enter PAN on KYC or the agreement form.",
        }

    return {
        "success": True,
        "client_id": client_id,
        "client_name": client.name,
        "agreement_id": ag.id,
        "pan": pan_to_save or existing_pan or "",
        "pan_from_pdf": pdf_pan or "",
        "agreement_start_date": start or "",
    }


def backfill_missing_identity_from_agreements(
    *,
    only_missing: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Batch refresh for active clients that have an agreement.

    Default ``only_missing=True`` only opens PDFs for clients without a stored PAN
    (fast path — typically the ~60 blanks, not all 116).
    """
    from models import Client
    from services.regulatory_client_master_service import _best_agreement_by_client

    clients = (
        Client.query.filter(Client.is_active.is_(True)).order_by(Client.name.asc()).all()
    )
    client_ids = [c.id for c in clients]
    ag_by = _best_agreement_by_client(client_ids)

    targets: List[int] = []
    for c in clients:
        if c.id not in ag_by:
            continue
        if only_missing and not force and _client_has_stored_pan(c.id):
            continue
        targets.append(c.id)

    results = []
    filled = 0
    failed = 0
    warnings = 0
    for cid in targets:
        try:
            res = refresh_client_identity_from_agreement(cid, force=force)
            results.append(res)
            if not res.get("success"):
                failed += 1
            elif res.get("pan"):
                filled += 1
            elif res.get("warning"):
                warnings += 1
        except Exception as exc:
            logger.exception("backfill identity failed client=%s", cid)
            failed += 1
            results.append({"success": False, "client_id": cid, "error": str(exc)})

    return {
        "success": True,
        "scanned": len(targets),
        "filled_pan": filled,
        "warnings": warnings,
        "failed": failed,
        "skipped_already_have_pan": len(client_ids) - len(targets),
    }


def sync_kyc_pan_to_lead_agreements(lead_id: int, *, pan: Optional[str] = None) -> int:
    """Push KYC (or explicit) PAN onto all agreements for this lead."""
    from models import Agreement, Lead

    resolved = normalize_pan(pan) if pan else get_lead_kyc_pan(lead_id)
    if not is_valid_pan(resolved):
        return 0
    lead = Lead.query.get(lead_id)
    client_id = lead.client_id if lead else None
    updated = 0
    for ag in Agreement.query.filter_by(lead_id=lead_id).all():
        save_pan_for_agreement(ag, lead_id, client_id, pan=resolved)
        updated += 1
    return updated


def regulatory_gaps_for_lead(lead) -> list:
    """Human-readable gaps for onboarding close / checklist."""
    gaps = []
    if lead is None or lead.id is None:
        return ["lead"]
    if not lead_has_regulatory_pan(lead.id):
        gaps.append("PAN on KYC profile")
    try:
        from services.lead_conversion_service import lead_convertible_agreements

        agreements = lead_convertible_agreements(lead)
    except Exception:
        agreements = []
    if not agreements:
        gaps.append("signed agreement")
    else:
        has_date = False
        for ag in agreements:
            try:
                from services.agreement_date_service import resolve_agreement_date

                if resolve_agreement_date(ag):
                    has_date = True
                    break
            except Exception:
                continue
        if not has_date:
            gaps.append("agreement start date (signed/sent date)")
    return gaps
