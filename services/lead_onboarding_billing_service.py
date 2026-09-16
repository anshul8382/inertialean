"""Onboarding invoice helpers for leads (reuse InvoiceGenerationService)."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from models import Agreement, Invoice
from services.lead_conversion_service import lead_convertible_agreements

logger = logging.getLogger(__name__)


def invoices_for_lead(lead_id: int) -> List[Invoice]:
    agreement_ids = [a.id for a in Agreement.query.filter_by(lead_id=lead_id).all()]
    if not agreement_ids:
        return []
    return (
        Invoice.query.filter(Invoice.agreement_id.in_(agreement_ids))
        .order_by(Invoice.created_at.desc())
        .all()
    )


def lead_has_paid_invoice(lead) -> bool:
    if lead is None:
        return False
    for inv in invoices_for_lead(lead.id):
        if (inv.status or "").lower() == "paid":
            return True
    return False


def latest_invoice_for_lead(lead_id: int) -> Optional[Invoice]:
    rows = invoices_for_lead(lead_id)
    return rows[0] if rows else None


def preferred_agreement_for_invoice(lead) -> Optional[Agreement]:
    """Only signed/convertible agreements may create onboarding invoices."""
    convertible = lead_convertible_agreements(lead)
    return convertible[0] if convertible else None


def generate_onboarding_invoice(
    lead,
    *,
    user_id: int,
    billing_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Ensure client shell, then generate draft invoice for the preferred agreement.
    """
    from invoice_generation_service import InvoiceGenerationService
    from services.lead_client_shell_service import ensure_client_shell_for_lead

    existing_invoice = latest_invoice_for_lead(lead.id)
    if existing_invoice:
        return {
            "success": True,
            "existing": True,
            "invoice_id": existing_invoice.id,
            "invoice_number": existing_invoice.invoice_number,
            "status": existing_invoice.status,
        }

    agreement = preferred_agreement_for_invoice(lead)
    if not agreement:
        return {"error": "No signed agreement found for this lead. Create and sign the agreement first."}

    try:
        ensure_client_shell_for_lead(lead, acting_user_id=user_id)
        from extensions import db

        db.session.commit()
    except Exception as exc:
        from extensions import db

        db.session.rollback()
        return {"error": f"Could not prepare client for invoicing: {exc}"}

    # Re-load lead so client relationship is present
    from models import Lead

    lead = Lead.query.get(lead.id)
    result = InvoiceGenerationService.generate_invoice(
        agreement_id=agreement.id,
        billing_date=billing_date or date.today(),
        user_id=user_id,
    )
    return result


def mark_invoice_paid(
    invoice_id: int,
    *,
    payment_reference: str = "",
    payment_method: str = "",
    sync_zoho: bool = True,
) -> Dict[str, Any]:
    from invoice_generation_service import InvoiceGenerationService
    from models import Invoice

    invoice = Invoice.query.get(invoice_id)
    if not invoice:
        return {"error": "Invoice not found."}

    zoho_result = None
    if sync_zoho:
        try:
            from services.zoho_books_service import (
                get_zoho_invoice_id,
                push_invoice_to_zoho,
                record_payment_in_zoho,
                zoho_configured,
            )

            if zoho_configured():
                if not get_zoho_invoice_id(invoice):
                    created = push_invoice_to_zoho(invoice, send_email=False)
                    if created.get("error"):
                        return {
                            "error": (
                                f"Could not sync invoice to Zoho before marking paid: "
                                f"{created['error']}"
                            ),
                            "zoho": created,
                        }
                zoho_result = record_payment_in_zoho(
                    invoice,
                    payment_reference=payment_reference,
                    payment_method=payment_method,
                    mark_local_paid=True,
                )
                if zoho_result.get("error"):
                    return {"error": zoho_result["error"], "zoho": zoho_result}
                return {
                    "success": True,
                    "invoice_id": invoice_id,
                    "zoho": zoho_result,
                }
        except Exception as exc:
            logger.warning("Zoho payment sync failed for invoice %s: %s", invoice_id, exc)
            return {"error": f"Zoho payment failed: {exc}"}

    ok = InvoiceGenerationService.update_invoice_status(
        invoice_id,
        "paid",
        payment_reference=payment_reference or None,
        payment_method=payment_method or None,
    )
    if not ok:
        return {"error": "Could not update invoice status."}
    return {"success": True, "invoice_id": invoice_id, "zoho": zoho_result}
