"""
Public website contact form API (equities4wealth.com).
Creates a lead in Inertia and emails the team — no login required.

Career applications from /careers are stored on the same table but classified
separately (source=equities4wealth.com/careers) and excluded from the sales
leads report.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request
from flask_mail import Message
from werkzeug.datastructures import FileStorage

from extensions import db, mail
from models import Lead
from services.lead_classification_service import (
    CAREER_SOURCE,
    canonical_source,
    is_career_application_fields,
    prepare_career_notes,
)

logger = logging.getLogger(__name__)

public_contact_bp = Blueprint("public_contact", __name__, url_prefix="/public")

SITUATION_LABELS = {
    "starting-out": "Just starting to invest",
    "review": "Have a portfolio to review",
    "holistic": "Looking for holistic financial planning",
    "nri": "NRI investor",
    "second-opinion": "Want a second opinion",
    "other": "Other",
}

NOTIFY_EMAILS = [
    "onboarding@equities4wealth.com",
    "anshul@equities4wealth.com",
]


def _authorized() -> bool:
    expected = (current_app.config.get("WEBSITE_CONTACT_API_KEY") or "").strip()
    if not expected:
        logger.error("WEBSITE_CONTACT_API_KEY is not configured")
        return False
    provided = (request.headers.get("X-Website-Contact-Key") or "").strip()
    return provided == expected


def _situation_label(code: str) -> str:
    return SITUATION_LABELS.get(code, code or "Not specified")


def _build_notes(situation: str, message: str) -> str:
    label = _situation_label(situation)
    return f"Situation: {label}\n\n{message.strip()}"


def _send_notification_email(
    name: str,
    email: str,
    phone: str,
    situation: str,
    message: str,
    lead_id: int,
    *,
    is_career: bool = False,
    file_saved: bool = False,
) -> bool:
    phone_display = phone.strip() or "—"
    email_display = email.strip() or "—"
    if is_career:
        body_html = f"""
        <h2>New career application</h2>
        <p><strong>Record ID:</strong> {lead_id}</p>
        <table cellpadding="6" style="border-collapse:collapse;">
          <tr><td><strong>Name</strong></td><td>{html.escape(name)}</td></tr>
          <tr><td><strong>Email</strong></td><td>{html.escape(email_display)}</td></tr>
          <tr><td><strong>Phone</strong></td><td>{html.escape(phone_display)}</td></tr>
        </table>
        <p><strong>Application</strong></p>
        <p style="white-space:pre-wrap;">{html.escape(message)}</p>
        {("<p><strong>Resume:</strong> attached on the lead record in Inertia.</p>" if file_saved else "")}
        <p style="color:#666;font-size:12px;">Source: {html.escape(CAREER_SOURCE)}</p>
        """
        subject = f"Career application: {name}"
    else:
        situation_label = _situation_label(situation)
        body_html = f"""
        <h2>New contact form submission</h2>
        <p><strong>Lead ID:</strong> {lead_id}</p>
        <table cellpadding="6" style="border-collapse:collapse;">
          <tr><td><strong>Name</strong></td><td>{html.escape(name)}</td></tr>
          <tr><td><strong>Email</strong></td><td>{html.escape(email)}</td></tr>
          <tr><td><strong>Phone</strong></td><td>{html.escape(phone_display)}</td></tr>
          <tr><td><strong>Situation</strong></td><td>{html.escape(situation_label)}</td></tr>
        </table>
        <p><strong>Message</strong></p>
        <p style="white-space:pre-wrap;">{html.escape(message)}</p>
        <p style="color:#666;font-size:12px;">Source: equities4wealth.com contact form</p>
        """
        subject = f"Website inquiry: {name}"
    msg_kwargs = {
        "subject": subject,
        "recipients": NOTIFY_EMAILS,
        "html": body_html,
    }
    if email:
        msg_kwargs["reply_to"] = email
    msg = Message(**msg_kwargs)
    mail.send(msg)
    return True


def _create_lead_workflow(lead: Lead, name: str, source: str) -> None:
    try:
        from workflow_service import WorkflowService

        WorkflowService.create_workflow(
            module_type="lead",
            record_id=lead.id,
            initial_stage="REFERRAL",
            target_date=datetime.utcnow().date() + timedelta(hours=24),
            notes=f"Lead created from {source}" if source else "Lead created",
        )
        workflow = WorkflowService.get_workflow("lead", lead.id)
        if workflow:
            WorkflowService.add_action(
                workflow.id,
                "LEAD_CREATED",
                f"Lead {name} created from website contact form",
            )
    except Exception as exc:
        logger.warning("Workflow creation failed for lead %s: %s", lead.id, exc)


_PUBLIC_FILE_KEYS = ("resume", "cv", "document", "file", "attachment")


def _request_payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict(flat=True) or {}


def _uploaded_file() -> FileStorage | None:
    for key in _PUBLIC_FILE_KEYS:
        uploaded = request.files.get(key)
        if uploaded and uploaded.filename:
            return uploaded
    for uploaded in request.files.values():
        if uploaded and uploaded.filename:
            return uploaded
    return None


def _save_public_lead_file(lead_id: int, uploaded: FileStorage, *, is_career: bool) -> bool:
    from services.lead_document_service import save_lead_document

    doc_type = (request.form.get("document_type") or "").strip()
    if not doc_type:
        doc_type = "Resume" if is_career else "Other"
    try:
        save_lead_document(
            lead_id,
            uploaded,
            document_type=doc_type,
            source="website_careers" if is_career else "website_contact",
        )
        return True
    except ValueError as exc:
        logger.warning("Public form file rejected for lead %s: %s", lead_id, exc)
        return False
    except Exception:
        logger.exception("Public form file save failed for lead %s", lead_id)
        return False


def _submit_public_form(*, force_career: bool = False):
    if not _authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = _request_payload()
    # Honeypot — bots only (never use name "company"; browsers autofill it)
    if (data.get("_hp_verify") or data.get("company") or "").strip():
        return jsonify({"success": True})

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    situation = (data.get("situation") or "").strip()
    message = (data.get("message") or "").strip()
    source_in = (data.get("source") or "").strip()
    page = (data.get("page") or "").strip()
    form_type = (data.get("form_type") or "").strip()
    role = (data.get("role") or "").strip()
    location = (data.get("location") or data.get("current_location") or "").strip()
    available_to_join = (data.get("available_to_join") or "").strip()

    is_career = force_career or is_career_application_fields(
        source=source_in,
        notes=message,
        page=page,
        form_type=form_type,
    )
    if role or (force_career and (location or available_to_join)):
        is_career = True

    if len(name) < 2:
        return jsonify({"success": False, "error": "Please enter your full name."}), 400
    if not is_career:
        if "@" not in email or "." not in email.split("@")[-1]:
            return jsonify({"success": False, "error": "Please enter a valid email address."}), 400
        if not situation:
            return jsonify({"success": False, "error": "Please select a situation."}), 400
        if len(message) < 10:
            return jsonify({"success": False, "error": "Please enter a longer message."}), 400
    elif email and ("@" not in email or "." not in email.split("@")[-1]):
        return jsonify({"success": False, "error": "Please enter a valid email address."}), 400
    elif not message and not role:
        return jsonify({"success": False, "error": "Please include the role or a short message."}), 400

    if is_career:
        notes = prepare_career_notes(
            message,
            role=role,
            location=location,
            available_to_join=available_to_join,
        )
        source = CAREER_SOURCE
    else:
        notes = _build_notes(situation, message)
        source = canonical_source(source=source_in or "equities4wealth.com", notes=notes)

    lead = Lead(
        name=name,
        email=email,
        phone=phone,
        source=source,
        status="new",
        notes=notes,
        user_id=1,
    )
    db.session.add(lead)
    db.session.commit()

    uploaded = _uploaded_file()
    file_saved = False
    if uploaded:
        file_saved = _save_public_lead_file(lead.id, uploaded, is_career=is_career)

    if not is_career:
        _create_lead_workflow(lead, name, source)

    email_sent = False
    try:
        email_sent = _send_notification_email(
            name,
            email,
            phone,
            situation,
            notes,
            lead.id,
            is_career=is_career,
            file_saved=file_saved,
        )
    except Exception as exc:
        logger.exception(
            "Contact form notification email failed for lead %s: %s", lead.id, exc
        )

    return jsonify(
        {
            "success": True,
            "lead_id": lead.id,
            "email_sent": email_sent,
            "category": "career" if is_career else "sales",
            "file_saved": file_saved,
        }
    )


@public_contact_bp.route("/contact", methods=["POST"])
def submit_contact():
    return _submit_public_form(force_career=False)


@public_contact_bp.route("/careers", methods=["POST"])
def submit_career():
    return _submit_public_form(force_career=True)
