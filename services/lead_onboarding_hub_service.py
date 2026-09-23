"""Lead onboarding hub context for the lead view."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import url_for


def _status_done(status: Optional[str], *names: str) -> bool:
    s = (status or "").lower()
    return s in {n.lower() for n in names}


# Statuses that imply "already contacted"
_CONTACTED_OR_LATER = frozenset(
    {
        "contacted",
        "qualified",
        "risk_profile_sent",
        "risk_profile_received",
        "proposal_sent",
        "proposal_reviewed",
        "proposal_revised",
        "agreement_sent",
        "agreement_reviewed",
        "agreement_signed",
        "kyc_uploaded",
        "onboarding_started",
        "onboarding_completed",
        "meeting",
        "risk_profile",
        "proposal",
        "acceptance",
        "agreement",
        "agreement_signing",
        "kyc",
        "invoice",
        "payment",
        "onboarding",
    }
)

# Statuses that imply meeting already happened / scheduled in pipeline
_MEETING_OR_LATER = frozenset(
    {
        "qualified",
        "risk_profile_sent",
        "risk_profile_received",
        "proposal_sent",
        "proposal_reviewed",
        "proposal_revised",
        "agreement_sent",
        "agreement_reviewed",
        "agreement_signed",
        "kyc_uploaded",
        "onboarding_started",
        "onboarding_completed",
        "meeting",
        "risk_profile",
        "proposal",
        "acceptance",
        "agreement",
        "agreement_signing",
        "kyc",
        "invoice",
        "payment",
        "onboarding",
    }
)


def _enrich_steps_with_workflow(steps: List[Dict[str, Any]], out: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mark done / current / upcoming and attach the single next action for the UI.
    First incomplete step is current; everything after is upcoming.
    """
    current_idx = len(steps)
    for i, step in enumerate(steps):
        if not step.get("done"):
            current_idx = i
            break

    done_count = sum(1 for s in steps if s.get("done"))
    total = len(steps) or 1
    fully = bool(out.get("fully_converted"))

    for i, step in enumerate(steps):
        if fully or step.get("done"):
            step["state"] = "done"
        elif i == current_idx:
            step["state"] = "current"
        else:
            step["state"] = "upcoming"
        step["index"] = i + 1

    if fully or current_idx >= len(steps):
        next_action: Dict[str, Any] = {
            "step_key": "convert",
            "step_label": "Convert",
            "step_index": total,
            "title": "Onboarding complete",
            "description": "This lead has finished onboarding. Open the client record if needed.",
            "primary_label": "View client" if out.get("client_url") else None,
            "primary_url": out.get("client_url"),
            "primary_kind": "link",
            "secondary": [],
        }
    else:
        cur = steps[current_idx]
        key = cur["key"]
        cta = _next_action_for_step(key, out, cur)
        next_action = {
            "step_key": key,
            "step_label": cur["label"],
            "step_index": current_idx + 1,
            "title": cta["title"],
            "description": cta["description"],
            "primary_label": cta.get("primary_label"),
            "primary_url": cta.get("primary_url"),
            "primary_kind": cta.get("primary_kind", "link"),
            "primary_form_action": cta.get("primary_form_action"),
            "primary_form_fields": cta.get("primary_form_fields") or {},
            "primary_modal": cta.get("primary_modal"),
            "secondary": cta.get("secondary") or [],
            "blocked_reason": cta.get("blocked_reason"),
        }

    out["steps"] = steps
    out["steps_by_key"] = {s["key"]: s for s in steps}
    out["current_step_index"] = current_idx if not fully else total - 1
    out["current_step"] = steps[current_idx] if current_idx < len(steps) and not fully else steps[-1]
    out["next_action"] = next_action
    out["progress"] = {
        "done": done_count if not fully else total,
        "total": total,
        "percent": int(round(100 * (done_count if not fully else total) / total)),
        "label": f"{min(done_count, total)} of {total} steps complete",
    }
    return out


def _next_action_for_step(key: str, out: Dict[str, Any], step: Dict[str, Any]) -> Dict[str, Any]:
    if key == "contact":
        return {
            "title": "Make first contact",
            "description": (
                "Call or WhatsApp the lead, then mark them contacted (or log a call). "
                "This step completes when status is Contacted (or later) or a call is logged."
            ),
            "primary_label": "WhatsApp follow-up draft",
            "primary_kind": "modal",
            "primary_modal": "#waFollowModal",
            "secondary": [
                {"label": "Log a call", "url": "#call-logs", "kind": "link"},
                {
                    "label": "Mark contacted",
                    "kind": "form",
                    "form_action": out.get("mark_contacted_url"),
                },
            ],
        }
    if key == "meeting":
        return {
            "title": "Set up a meeting",
            "description": "Schedule a meeting with this lead. Step completes when a meeting exists on the lead.",
            "primary_label": "Schedule meeting",
            "primary_kind": "link",
            "primary_url": out.get("new_meeting_url"),
            "secondary": [
                {"label": "Meetings on this lead ↓", "url": "#lead-meetings", "kind": "link"},
            ],
        }
    if key == "risk":
        return {
            "title": "Get the risk profile",
            "description": (
                "Send the quiz link on WhatsApp (or mark sent after you share it). "
                "Completes when the client submits the quiz."
            ),
            "primary_label": "WhatsApp risk draft",
            "primary_kind": "modal",
            "primary_modal": "#waRiskModal",
            "secondary": [
                {"label": "Open quiz link", "url": out["quiz_url"], "kind": "link", "external": True},
                {
                    "label": "Mark quiz sent",
                    "kind": "form",
                    "form_action": out.get("mark_risk_sent_url"),
                },
            ],
        }
    if key == "proposal":
        return {
            "title": "Create and send the proposal",
            "description": "Build the DOCX proposal, then mark it sent.",
            "primary_label": "Open proposal",
            "primary_kind": "link",
            "primary_url": out["proposal_url"],
            "secondary": [],
        }
    if key == "kyc":
        return {
            "title": "Collect KYC",
            "description": (
                "Collect KYC before the agreement. Save PAN fields, upload documents, mark complete."
            ),
            "primary_label": "Go to KYC form",
            "primary_kind": "link",
            "primary_url": "#onboarding-step-kyc",
            "secondary": [
                {
                    "label": "Mark KYC complete",
                    "kind": "form",
                    "form_action": out["mark_kyc_url"],
                },
            ],
        }
    if key == "agreement":
        warn = None
        if not out.get("has_kyc"):
            warn = "KYC not complete yet — you can still send the agreement; finish KYC before closing onboarding."
        return {
            "title": "Get the agreement signed",
            "description": "Create the agreement, send for e-sign, wait until signed. KYC can run in parallel (warn only).",
            "primary_label": "Create agreement",
            "primary_kind": "link",
            "primary_url": out["create_agreement_url"],
            "blocked_reason": None,
            "warning": warn,
            "secondary": [
                {"label": "All agreements", "url": out["agreements_url"], "kind": "link"},
            ],
        }
    if key == "invoice":
        blocked = None
        if not out.get("has_signed_agreement"):
            blocked = "Agreement should be signed before generating the invoice."
        return {
            "title": "Generate the invoice",
            "description": (
                "Create the Inertia invoice"
                + (", then push to Zoho if configured." if out.get("zoho_configured") else ".")
            ),
            "primary_label": "Generate invoice",
            "primary_kind": "form",
            "primary_form_action": out["generate_invoice_url"],
            "blocked_reason": blocked,
            "secondary": (
                [{"label": "View invoice", "url": out["invoice_view_url"], "kind": "link"}]
                if out.get("invoice_view_url")
                else []
            ),
        }
    if key == "payment":
        if not out.get("latest_invoice"):
            return {
                "title": "Record payment",
                "description": "Generate an invoice first, then mark it paid.",
                "primary_label": None,
                "blocked_reason": "No invoice yet — complete the Invoice step first.",
                "secondary": [],
            }
        return {
            "title": "Collect / record payment",
            "description": (
                "Mark the invoice paid when money is received"
                + (" (also records payment in Zoho when linked)." if out.get("zoho_configured") else ".")
            ),
            "primary_label": "Go to payment",
            "primary_kind": "link",
            "primary_url": "#onboarding-step-payment",
            "secondary": (
                [
                    {
                        "label": "Create in Zoho",
                        "kind": "form",
                        "form_action": out["send_zoho_url"],
                    }
                ]
                if out.get("zoho_configured") and not out.get("zoho_linked")
                else []
            ),
        }
    if key == "convert":
        has_client = bool(out.get("client_url") or out.get("has_client_profile"))
        open_items = out.get("onboarding_open_items") or []
        desc = (
            "Create / activate the client profile so you can send the first recommendation. "
            "KYC, agreement, and payment can finish afterward — checklist stays open."
        )
        if has_client and open_items:
            desc = (
                "Client profile is active. Still open: "
                + ", ".join(open_items)
                + ". Finish these or mark onboarding complete when done."
            )
        elif has_client:
            desc = "Client profile is active and onboarding checklist is complete."
        return {
            "title": "Convert to client",
            "description": desc,
            "primary_label": None if has_client else "Convert to client",
            "primary_kind": "link",
            "primary_url": out.get("convert_url") if not has_client else None,
            "blocked_reason": None,
            "secondary": (
                [{"label": "Open client", "url": out["client_url"], "kind": "link"}]
                if out.get("client_url")
                else []
            ),
        }
    return {
        "title": step.get("label") or key,
        "description": step.get("detail") or "",
        "primary_label": None,
        "secondary": [],
    }


def build_onboarding_hub(lead) -> Dict[str, Any]:
    from services.lead_conversion_service import (
        lead_has_client_profile,
        lead_is_fully_converted,
        onboarding_checklist_incomplete,
    )
    from services.lead_kyc_service import lead_has_kyc_documents, list_kyc_documents
    from services.lead_onboarding_billing_service import (
        latest_invoice_for_lead,
        lead_has_paid_invoice,
    )
    from services.lead_message_playbook_service import suggested_draft_for_lead
    from services.lead_whatsapp_draft_service import followup_draft, risk_profile_draft
    from services.risk_assessment_service import latest_for_lead, lead_invite_token

    quiz_url = url_for(
        "risk_assessment.risk_assessment_form",
        t=lead_invite_token(lead.id),
        _external=True,
    )
    sender = "Inertia"
    try:
        if lead.user and getattr(lead.user, "username", None):
            sender = lead.user.username
    except Exception:
        pass

    risk_sub = None
    try:
        risk_sub = latest_for_lead(lead.id)
    except Exception:
        risk_sub = None

    latest_proposal = None
    try:
        from models.lead_onboarding import LeadProposal
        from sqlalchemy import inspect
        from extensions import db

        if inspect(db.engine).has_table("lead_proposal"):
            latest_proposal = (
                LeadProposal.query.filter_by(lead_id=lead.id)
                .order_by(LeadProposal.created_at.desc())
                .first()
            )
    except Exception:
        latest_proposal = None

    has_signed_agreement = False
    try:
        from services.lead_conversion_service import lead_has_convertible_agreement

        has_signed_agreement = lead_has_convertible_agreement(lead)
        if not has_signed_agreement:
            for agr in lead.agreements or []:
                if (getattr(agr, "status", None) or "").lower() in ("signed", "completed", "active"):
                    has_signed_agreement = True
                    break
    except Exception:
        pass

    has_calls = False
    try:
        from models import LeadCallLog

        has_calls = LeadCallLog.query.filter_by(lead_id=lead.id).first() is not None
    except Exception:
        has_calls = False

    has_meeting = False
    meeting_count = 0
    try:
        from models import Meeting

        meeting_count = Meeting.query.filter_by(lead_id=lead.id).count()
        has_meeting = meeting_count > 0
    except Exception:
        pass

    kyc_docs = []
    has_kyc = False
    has_kyc_pan = False
    try:
        kyc_docs = list_kyc_documents(lead.id)
        has_kyc = bool(kyc_docs) or lead_has_kyc_documents(lead.id)
        from services.lead_kyc_service import lead_has_kyc_pan

        has_kyc_pan = lead_has_kyc_pan(lead.id)
        # Regulatory: KYC step is only "done" when docs + PAN are captured.
        has_kyc = has_kyc and has_kyc_pan
    except Exception:
        pass

    latest_invoice = None
    has_paid = False
    try:
        latest_invoice = latest_invoice_for_lead(lead.id)
        has_paid = lead_has_paid_invoice(lead)
    except Exception:
        pass

    fully_converted = False
    has_client_profile = bool(getattr(lead, "client_id", None))
    onboarding_open_items: List[str] = []
    try:
        fully_converted = lead_is_fully_converted(lead)
        has_client_profile = lead_has_client_profile(lead)
        onboarding_open_items = onboarding_checklist_incomplete(lead)
    except Exception:
        fully_converted = bool(lead.client_id and (lead.status or "").lower() == "onboarding_completed")
        has_client_profile = bool(lead.client_id)

    st = (lead.status or "").lower()
    contact_done = has_calls or st in _CONTACTED_OR_LATER
    meeting_done = has_meeting or st in _MEETING_OR_LATER

    risk_display = None
    if risk_sub:
        try:
            from services.risk_profile_scoring_service import format_risk_submission_for_display

            risk_display = format_risk_submission_for_display(risk_sub)
        except Exception:
            risk_display = None

    invoice_approval = {"status": "draft", "is_approved": False, "is_pending": False}
    if latest_invoice:
        try:
            from services.onboarding_approval_service import get_invoice_approval

            invoice_approval = get_invoice_approval(latest_invoice)
        except Exception:
            pass

    steps: List[Dict[str, Any]] = [
        {
            "key": "contact",
            "label": "Contact",
            "done": contact_done,
            "detail": (
                "Contacted"
                if contact_done
                else "Not contacted yet"
            ),
            "how": "WhatsApp playbook (system picks stage) → edit → send / mark contacted",
        },
        {
            "key": "meeting",
            "label": "Meeting",
            "done": meeting_done,
            "detail": (
                f"{meeting_count} meeting(s)"
                if has_meeting
                else ("Pipeline past meeting" if meeting_done else "No meeting scheduled")
            ),
            "how": "Schedule a meeting with the lead",
        },
        {
            "key": "risk",
            "label": "Risk profile",
            "done": bool(risk_sub)
            or _status_done(
                st,
                "risk_profile_received",
                "proposal_sent",
                "agreement_signed",
                "kyc_uploaded",
                "onboarding_completed",
            ),
            "detail": (
                f"{risk_sub.risk_profile} · equity {risk_sub.max_equity_pct}%"
                if risk_sub
                else (
                    "Quiz sent — waiting for client"
                    if _status_done(st, "risk_profile_sent")
                    else "Not received yet"
                )
            ),
            "how": "Send quiz → client submits → profile appears here",
        },
        {
            "key": "proposal",
            "label": "Proposal",
            "done": _status_done(
                st,
                "proposal_sent",
                "proposal_reviewed",
                "agreement_sent",
                "agreement_signed",
                "kyc_uploaded",
                "onboarding_completed",
            )
            or (latest_proposal and latest_proposal.status in ("sent", "approved")),
            "detail": (f"Status: {latest_proposal.status}" if latest_proposal else "Not created"),
            "how": "Draft → generate DOCX → approve → email to client",
        },
        {
            "key": "kyc",
            "label": "KYC",
            "done": has_kyc or (
                _status_done(st, "kyc_uploaded", "onboarding_completed") and has_kyc_pan
            ),
            "detail": (
                f"{len(kyc_docs)} document(s); PAN saved"
                if kyc_docs and has_kyc_pan
                else (
                    f"{len(kyc_docs)} document(s) — PAN still required"
                    if kyc_docs
                    else (
                        "PAN saved — upload documents"
                        if has_kyc_pan
                        else "Documents / PAN not complete"
                    )
                )
            ),
            "how": "Complete KYC (parallel with agreement) — PAN required + docs",
        },
        {
            "key": "agreement",
            "label": "Agreement",
            "done": has_signed_agreement
            or _status_done(st, "agreement_signed", "onboarding_completed"),
            "detail": "Signed" if has_signed_agreement else "Not signed yet",
            "how": "Create agreement → e-sign (KYC warn only; can run in parallel)",
        },
        {
            "key": "invoice",
            "label": "Invoice",
            "done": bool(latest_invoice)
            and (invoice_approval.get("is_approved") or has_paid),
            "detail": (
                f"{latest_invoice.invoice_number} ({latest_invoice.status}"
                f"{', approval: ' + invoice_approval['status'] if invoice_approval else ''})"
                if latest_invoice
                else ("Awaiting signed agreement" if not has_signed_agreement else "Not generated")
            ),
            "how": "Generate after agreement is signed → approve → send via Zoho",
        },
        {
            "key": "payment",
            "label": "Payment",
            "done": has_paid,
            "detail": "Paid" if has_paid else "Awaiting payment",
            "how": "Mark paid when received (syncs to Zoho if configured)",
        },
        {
            "key": "convert",
            "label": "Convert",
            "done": has_client_profile,
            "detail": (
                "Client active"
                + (
                    f" — still open: {', '.join(onboarding_open_items)}"
                    if onboarding_open_items and has_client_profile
                    else (
                        " — checklist complete"
                        if fully_converted
                        else ""
                    )
                )
                if has_client_profile
                else "Create client to send first recommendation"
            ),
            "how": "Activate client profile early; finish KYC/agreement/payment in parallel",
        },
    ]

    invoice_view_url = None
    if latest_invoice:
        invoice_view_url = url_for("invoices.view_invoice", invoice_id=latest_invoice.id)

    client_url = None
    if lead.client_id:
        try:
            client_url = url_for("clients.client_details", client_id=lead.client_id)
        except Exception:
            client_url = None

    can_generate_invoice = (
        has_signed_agreement
        and latest_invoice is None
        and not has_paid
    )
    inv_status = (latest_invoice.status or "").lower() if latest_invoice else ""
    invoice_send_ok = bool(latest_invoice and invoice_approval.get("is_approved"))

    can_approve_invoice = False
    can_submit_invoice_approval = False
    if latest_invoice:
        try:
            from flask_login import current_user
            from models import User
            from services.onboarding_approval_service import can_approve, can_request_approval

            creator = User.query.get(latest_invoice.created_by) if latest_invoice.created_by else None
            if invoice_approval.get("is_pending"):
                can_approve_invoice = can_approve(current_user, creator)
            if not invoice_approval.get("is_approved"):
                can_submit_invoice_approval = can_request_approval(
                    current_user, latest_invoice.created_by
                )
        except Exception:
            pass

    contact_draft = suggested_draft_for_lead(
        lead,
        sender_name=sender,
        quiz_url=quiz_url,
        has_risk=bool(risk_sub),
        has_meeting=has_meeting,
    )

    risk_history = []
    try:
        from models.lead_onboarding import RiskAssessmentSubmission
        from sqlalchemy import inspect
        from extensions import db

        if inspect(db.engine).has_table("risk_assessment_submission"):
            risk_history = (
                RiskAssessmentSubmission.query.filter_by(lead_id=lead.id)
                .order_by(RiskAssessmentSubmission.created_at.desc())
                .limit(20)
                .all()
            )
    except Exception:
        risk_history = []

    out = {
        "steps": steps,
        "quiz_url": quiz_url,
        "risk_draft": risk_profile_draft(lead, quiz_url=quiz_url, sender_name=sender),
        "followup_draft": followup_draft(lead, sender_name=sender),
        "contact_draft": contact_draft,
        "message_stages": contact_draft.get("stages") or [],
        "risk_submission": risk_sub,
        "risk_display": risk_display,
        "risk_history": risk_history,
        "latest_proposal": latest_proposal,
        "latest_invoice": latest_invoice,
        "invoice_approval": invoice_approval,
        "can_generate_invoice": can_generate_invoice,
        "can_approve_invoice": can_approve_invoice,
        "can_submit_invoice_approval": can_submit_invoice_approval,
        "invoice_status": inv_status,
        "invoice_send_ok": invoice_send_ok,
        "kyc_docs": kyc_docs,
        "upload_document_url": url_for("leads.upload_lead_document", lead_id=lead.id),
        "has_kyc": has_kyc,
        "has_paid": has_paid,
        "has_signed_agreement": has_signed_agreement,
        "fully_converted": fully_converted,
        "has_client_profile": has_client_profile,
        "onboarding_open_items": onboarding_open_items,
        "client_url": client_url,
        "proposal_url": url_for("leads.lead_proposal", lead_id=lead.id),
        "agreements_url": url_for("agreements.lead_agreements", lead_id=lead.id),
        "create_agreement_url": url_for("agreements.create_agreement", lead_id=lead.id),
        "generate_invoice_url": url_for("leads.generate_onboarding_invoice", lead_id=lead.id),
        "mark_kyc_url": url_for("leads.mark_kyc_complete", lead_id=lead.id),
        "mark_risk_sent_url": url_for("leads.mark_risk_sent", lead_id=lead.id),
        "mark_contacted_url": url_for("leads.mark_lead_contacted", lead_id=lead.id),
        "new_meeting_url": url_for("meetings.new_meeting", lead_id=lead.id),
        "invoice_view_url": invoice_view_url,
        "mark_paid_url": (
            url_for("leads.mark_onboarding_invoice_paid", lead_id=lead.id)
            if latest_invoice and not has_paid
            else None
        ),
        "request_invoice_approval_url": url_for(
            "leads.request_invoice_approval", lead_id=lead.id
        ),
        "approve_invoice_url": url_for("leads.approve_onboarding_invoice", lead_id=lead.id),
        "convert_url": (
            url_for("leads.convert_lead", lead_id=lead.id) if not has_client_profile else None
        ),
        "close_onboarding_url": url_for("leads.close_onboarding", lead_id=lead.id),
        "send_wa_url": url_for("leads.send_onboarding_whatsapp", lead_id=lead.id),
        "send_zoho_url": url_for("leads.send_zoho_invoice", lead_id=lead.id),
        "sync_zoho_url": url_for("leads.sync_zoho_invoice", lead_id=lead.id),
        "zoho_pdf_url": url_for("leads.zoho_invoice_pdf", lead_id=lead.id),
        "zoho_receipt_url": url_for("leads.zoho_payment_receipt", lead_id=lead.id),
        "save_kyc_profile_url": url_for("leads.save_kyc_profile", lead_id=lead.id),
        "preview_message_url": url_for("leads.preview_onboarding_message", lead_id=lead.id),
        "zoho_configured": False,
        "zoho_linked": False,
        "zoho_invoice_id": None,
        "zoho_payment_id": None,
        "wa_configured": False,
        "kyc_profile": None,
        "lead_status": lead.status,
        "meeting_count": meeting_count,
    }

    try:
        from services.zoho_books_service import invoice_zoho_summary, zoho_configured

        out["zoho_configured"] = zoho_configured()
        if latest_invoice:
            summary = invoice_zoho_summary(latest_invoice)
            out["zoho_linked"] = summary.get("linked")
            out["zoho_invoice_id"] = summary.get("zoho_invoice_id")
            out["zoho_payment_id"] = summary.get("zoho_payment_id")
    except Exception:
        pass
    try:
        from flask import current_app

        out["wa_configured"] = bool(
            current_app.config.get("WHATSAPP_ACCESS_TOKEN")
            and current_app.config.get("WHATSAPP_PHONE_NUMBER_ID")
        )
    except Exception:
        pass
    try:
        from services.lead_kyc_profile_service import get_or_create_profile, kyc_table_ready

        if kyc_table_ready():
            out["kyc_profile"] = get_or_create_profile(lead.id)
    except Exception:
        pass

    return _enrich_steps_with_workflow(steps, out)
