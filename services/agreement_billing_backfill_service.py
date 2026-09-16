"""
Backfill empty agreement billing fields from special_note heuristics.

Never overwrites non-empty structured billing data or existing rates.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from flask import current_app
from sqlalchemy.orm import joinedload

from extensions import db
from models import Agreement, AgreementVariables, BillingRateStructure, BillingSchedule, Client, Lead
from routes.agreements import _next_billing_date_from_start, _save_billing_rates
from services.agreement_overview_service import AgreementOverviewService
from services.agreement_pdf_paths import agreement_pdf_abs_path
from services.special_note_parser import (
    ParsedSpecialNote,
    parse_special_note,
    resolve_annual_rate_pct_for_storage,
)


def _get_special_note(agreement: Agreement) -> str:
    from services.agreement_billing_config_service import is_auto_generated_special_note

    for v in agreement.variables or []:
        if (v.variable_name or "").strip().lower() == "special_note":
            text = (v.variable_value or "").strip()
            if text and not is_auto_generated_special_note(text):
                return text
    if agreement.agreement_data:
        try:
            data = json.loads(agreement.agreement_data)
            return (data.get("special_note") or "").strip()
        except (json.JSONDecodeError, TypeError):
            pass
    return ""


def _vars_map(agreement: Agreement) -> Dict[str, str]:
    return {
        (v.variable_name or "").strip(): (v.variable_value or "").strip()
        for v in (agreement.variables or [])
    }


def _agreement_config(agreement: Agreement) -> Dict[str, Any]:
    if not agreement.agreement_data:
        return {}
    try:
        return json.loads(agreement.agreement_data) or {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _var_is_empty(agreement: Agreement, name: str) -> bool:
    return not (_vars_map(agreement).get(name) or "").strip()


def _config_key_empty(agreement: Agreement, key: str) -> bool:
    return not (_agreement_config(agreement).get(key) or "")


def _has_active_rates(agreement: Agreement) -> bool:
    return any(
        getattr(r, "is_active", True) and r.rate_percentage is not None
        for r in (agreement.billing_rates or [])
    )


def _has_active_schedule(agreement: Agreement) -> bool:
    return any(getattr(s, "is_active", False) for s in (agreement.billing_schedules or []))


def discover_agreement_pdf_abs(agreement: Agreement) -> Optional[str]:
    """Find agreement PDF on disk (stored path, agreements/, or uploads/)."""
    root = current_app.root_path

    if agreement.generated_pdf_path:
        found = agreement_pdf_abs_path(agreement.generated_pdf_path)
        if found:
            return found
        # Stored path may be prod absolute (/opt/...) — try basename under static/
        base = os.path.basename(str(agreement.generated_pdf_path).strip())
        if base.lower().endswith(".pdf"):
            for sub in ("static/agreements", "static/uploads/agreements"):
                candidate = os.path.join(root, sub, base)
                if os.path.isfile(candidate):
                    return candidate

    patterns = [
        os.path.join(root, "static", "agreements", f"agreement_{agreement.id}_*.pdf"),
        os.path.join(root, "static", "uploads", "agreements", f"agreement_{agreement.id}_*.pdf"),
    ]
    for pattern in patterns:
        files = glob.glob(pattern)
        if files:
            files.sort(key=os.path.getmtime, reverse=True)
            return files[0]
    return None


def canonical_pdf_web_path(abs_path: str) -> str:
    rel = os.path.relpath(abs_path, current_app.root_path).replace(os.sep, "/")
    return f"/{rel}"


def fix_agreement_pdf_link(agreement: Agreement, dry_run: bool = True) -> Optional[str]:
    """
    Set generated_pdf_path to a web path when the file exists on disk.
    Returns description of change, or None if nothing to do.
    """
    abs_path = discover_agreement_pdf_abs(agreement)
    if not abs_path:
        return None

    canonical = canonical_pdf_web_path(abs_path)
    current = (agreement.generated_pdf_path or "").strip()
    if current == canonical and agreement_pdf_abs_path(current):
        return None

    if not dry_run:
        agreement.generated_pdf_path = canonical
    return f"pdf_link: {current or '(empty)'} -> {canonical}"


def _upsert_var_if_empty(
    agreement: Agreement,
    lead_id: int,
    client_id: Optional[int],
    name: str,
    value: str,
    variable_type: str = "billing_config",
) -> bool:
    if not (value or "").strip():
        return False
    if not _var_is_empty(agreement, name):
        return False
    db.session.add(
        AgreementVariables(
            agreement_id=agreement.id,
            lead_id=lead_id,
            client_id=client_id,
            variable_name=name,
            variable_value=value.strip(),
            variable_type=variable_type,
        )
    )
    return True


def _merge_agreement_config_key(agreement: Agreement, key: str, value: Any) -> bool:
    if value is None or value == "":
        return False
    config = _agreement_config(agreement)
    if config.get(key) not in (None, "", []):
        return False
    config[key] = value
    agreement.agreement_data = json.dumps(config)
    return True


def _create_schedule_if_missing(
    agreement: Agreement,
    frequency: str,
    start: date,
) -> bool:
    if _has_active_schedule(agreement):
        return False
    next_bd = _next_billing_date_from_start(start, frequency)
    db.session.add(
        BillingSchedule(
            agreement_id=agreement.id,
            billing_start_date=start,
            next_billing_date=next_bd,
            billing_cycle_number=1,
            is_active=True,
        )
    )
    return True


def _asset_class_ids(agreement: Agreement) -> List[int]:
    config = _agreement_config(agreement)
    raw = config.get("asset_types") or []
    ids = []
    for x in raw:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            pass
    return ids


def backfill_agreement_from_note(
    agreement: Agreement,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Fill only empty billing fields from special_note. Never overwrites populated values.
    """
    actions: List[str] = []
    skipped: List[str] = []

    lead = agreement.lead
    if not lead:
        return {"agreement_id": agreement.id, "actions": actions, "skipped": ["no_lead"]}

    lead_id = lead.id
    client_id = lead.client_id

    pdf_change = fix_agreement_pdf_link(agreement, dry_run=dry_run)
    if pdf_change:
        actions.append(pdf_change)

    note = _get_special_note(agreement)
    if not note:
        return {"agreement_id": agreement.id, "actions": actions, "skipped": skipped or ["no_special_note"]}

    parsed = parse_special_note(note)
    config = _agreement_config(agreement)
    current_model = (config.get("advisory_model") or _vars_map(agreement).get("advisory_model") or "").strip().lower()

    # Skip noisy parser paths for model when already set
    if parsed.advisory_model and current_model and parsed.advisory_model != current_model:
        skipped.append(f"advisory_model: keep {current_model}")
        parsed.advisory_model = None

    if "mixed_aua_and_fixed" in parsed.flags and current_model == "aua":
        parsed.advisory_model = None
        parsed.fixed_annual_fee_inr = None

    annual_pct = resolve_annual_rate_pct_for_storage(note, parsed)

    if parsed.billing_frequency and _var_is_empty(agreement, "billing_frequency"):
        if not dry_run:
            _upsert_var_if_empty(
                agreement, lead_id, client_id, "billing_frequency", parsed.billing_frequency
            )
        actions.append(f"billing_frequency={parsed.billing_frequency}")

    if parsed.period_start_month is not None and _var_is_empty(agreement, "period_start_month"):
        if not dry_run:
            _upsert_var_if_empty(
                agreement,
                lead_id,
                client_id,
                "period_start_month",
                str(parsed.period_start_month),
            )
        actions.append(f"period_start_month={parsed.period_start_month}")

    if parsed.valuation_date_rule and _var_is_empty(agreement, "valuation_date_rule"):
        rule = parsed.valuation_date_rule
        if rule in ("prepaid", "postpaid"):
            if not dry_run:
                _upsert_var_if_empty(agreement, lead_id, client_id, "valuation_date_rule", rule)
            actions.append(f"valuation_date_rule={rule}")

    if parsed.advisory_model and _config_key_empty(agreement, "advisory_model"):
        if not dry_run:
            _merge_agreement_config_key(agreement, "advisory_model", parsed.advisory_model)
            _upsert_var_if_empty(
                agreement, lead_id, client_id, "advisory_model", parsed.advisory_model, "advisory_model"
            )
        actions.append(f"advisory_model={parsed.advisory_model}")

    if (
        parsed.fixed_annual_fee_inr is not None
        and _var_is_empty(agreement, "fixed_annual_fee")
        and (parsed.advisory_model == "fixed_fee" or current_model == "fixed_fee")
        and parsed.fixed_annual_fee_inr >= 100
    ):
        fee_s = str(int(parsed.fixed_annual_fee_inr))
        if not dry_run:
            _upsert_var_if_empty(agreement, lead_id, client_id, "fixed_annual_fee", fee_s)
        actions.append(f"fixed_annual_fee={fee_s}")

    freq = parsed.billing_frequency or _vars_map(agreement).get("billing_frequency") or "yearly"
    if not _has_active_schedule(agreement):
        start = agreement.signed_date
        if start and hasattr(start, "date"):
            start = start.date()
        if not isinstance(start, date):
            start = date.today()
        if not dry_run:
            _create_schedule_if_missing(agreement, freq, start)
            if _var_is_empty(agreement, "billing_start_date"):
                _upsert_var_if_empty(
                    agreement, lead_id, client_id, "billing_start_date", start.isoformat()
                )
        actions.append(f"billing_schedule start={start.isoformat()} freq={freq}")

    model = current_model or (parsed.advisory_model or "aua")
    if model == "aua" and not _has_active_rates(agreement) and annual_pct is not None:
        ac_ids = _asset_class_ids(agreement)
        if not ac_ids:
            skipped.append("billing_rates: no asset_types on agreement")
        else:
            rate_dec = Decimal(str(annual_pct)) / Decimal("100")
            if not dry_run:
                slabs_by_ac = {
                    ac_id: [
                        {
                            "min_amount": Decimal("0.00"),
                            "max_amount": None,
                            "rate_percentage": rate_dec,
                            "min_fee": Decimal("0.00"),
                            "max_fee": None,
                        }
                    ]
                    for ac_id in ac_ids
                }
                _save_billing_rates(agreement.id, slabs_by_ac)
            actions.append(f"billing_rates: {annual_pct}% annual on {len(ac_ids)} asset class(es)")

    return {
        "agreement_id": agreement.id,
        "client_id": client_id,
        "actions": actions,
        "skipped": skipped,
        "parser_confidence": parsed.confidence,
    }


def fix_all_agreement_pdf_links(*, dry_run: bool = True) -> Dict[str, Any]:
    """Repair generated_pdf_path for every agreement when a PDF file exists on disk."""
    fixed = []
    for agreement in Agreement.query.order_by(Agreement.id).all():
        change = fix_agreement_pdf_link(agreement, dry_run=dry_run)
        if change:
            fixed.append({"agreement_id": agreement.id, "change": change})
    if not dry_run:
        db.session.commit()
    return {"dry_run": dry_run, "pdf_links_fixed": len(fixed), "details": fixed}


def backfill_all_clients(*, dry_run: bool = True) -> Dict[str, Any]:
    """Run backfill for every client with a lead agreement."""
    summary = {
        "dry_run": dry_run,
        "agreements_processed": 0,
        "agreements_updated": 0,
        "pdf_links_fixed": 0,
        "details": [],
    }

    clients = Client.query.order_by(Client.name).all()
    for client in clients:
        lead = Lead.query.filter_by(client_id=client.id).first()
        if not lead:
            continue
        agreements = (
            Agreement.query.options(
                joinedload(Agreement.variables),
                joinedload(Agreement.billing_rates),
                joinedload(Agreement.billing_schedules),
                joinedload(Agreement.lead),
            )
            .filter_by(lead_id=lead.id)
            .all()
        )
        if not agreements:
            continue

        preferred = [
            a
            for a in agreements
            if (a.status or "").strip().lower()
            in AgreementOverviewService.ACTIVE_AGREEMENT_STATUSES
        ]
        agreement = AgreementOverviewService._pick_primary_agreement(preferred or agreements)

        result = backfill_agreement_from_note(agreement, dry_run=dry_run)
        summary["agreements_processed"] += 1
        if result.get("actions"):
            summary["agreements_updated"] += 1
            if any(a.startswith("pdf_link:") for a in result["actions"]):
                summary["pdf_links_fixed"] += 1
            summary["details"].append(
                {
                    "client_id": client.id,
                    "client_name": client.name,
                    **result,
                }
            )

    pdf_summary = fix_all_agreement_pdf_links(dry_run=dry_run)
    summary["pdf_links_fixed"] = summary.get("pdf_links_fixed", 0) + pdf_summary["pdf_links_fixed"]
    summary["pdf_fix_details"] = pdf_summary.get("details", [])

    if not dry_run:
        db.session.commit()

    return summary
