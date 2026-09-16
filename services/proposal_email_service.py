"""Send approved lead proposals via the same SMTP identity as recommendations (MAIL_USERNAME)."""
from __future__ import annotations

import logging
import os
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _smtp_credentials() -> Dict[str, Any]:
    env = os.environ
    user = (env.get("MAIL_USERNAME") or env.get("SMTP_USERNAME") or "").strip()
    raw = env.get("MAIL_PASSWORD") or env.get("SMTP_PASSWORD") or ""
    pwd = str(raw).strip().strip("\"'").replace(" ", "").replace("\r", "").replace("\n", "")
    try:
        from flask import current_app

        user = user or (current_app.config.get("MAIL_USERNAME") or "").strip()
        pwd = pwd or (current_app.config.get("MAIL_PASSWORD") or "").strip()
        server = (
            env.get("MAIL_SERVER")
            or env.get("SMTP_SERVER")
            or current_app.config.get("MAIL_SERVER")
            or "smtp.gmail.com"
        )
        port = int(
            env.get("MAIL_PORT")
            or env.get("SMTP_PORT")
            or current_app.config.get("MAIL_PORT")
            or 587
        )
        use_tls = str(
            env.get("MAIL_USE_TLS") or current_app.config.get("MAIL_USE_TLS") or "true"
        ).lower() in ("true", "1", "on")
        use_ssl = str(
            env.get("MAIL_USE_SSL") or current_app.config.get("MAIL_USE_SSL") or "false"
        ).lower() in ("true", "1", "on")
    except Exception:
        server = env.get("MAIL_SERVER") or env.get("SMTP_SERVER") or "smtp.gmail.com"
        port = int(env.get("MAIL_PORT") or env.get("SMTP_PORT") or 587)
        use_tls = (env.get("MAIL_USE_TLS") or "true").lower() in ("true", "1", "on")
        use_ssl = (env.get("MAIL_USE_SSL") or "false").lower() in ("true", "1", "on")
    return {
        "username": user,
        "password": pwd,
        "server": server,
        "port": port,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
    }


def _proposal_cc_list(*, lead=None, include_advisor: bool = True) -> List[str]:
    ccs: List[str] = []
    raw = (os.environ.get("PROPOSAL_EMAIL_CC") or "").strip()
    try:
        from flask import current_app

        raw = raw or (current_app.config.get("PROPOSAL_EMAIL_CC") or "").strip()
    except Exception:
        pass
    try:
        from models import BillingConfiguration

        row = BillingConfiguration.query.filter_by(
            config_key="proposal_email_cc", is_active=True
        ).first()
        if row and (row.config_value or "").strip():
            raw = (row.config_value or "").strip()
    except Exception:
        pass
    for part in re.split(r"[,;\s]+", raw) if raw else []:
        e = part.strip()
        if e and "@" in e and e not in ccs:
            ccs.append(e)
    if include_advisor and lead is not None:
        try:
            user = getattr(lead, "user", None)
            email = (getattr(user, "email", None) or "").strip()
            if email and email not in ccs:
                ccs.append(email)
        except Exception:
            pass
    return ccs


def send_proposal_email(
    *,
    lead,
    proposal,
    recipient_emails: Optional[List[str]] = None,
    subject: Optional[str] = None,
    message_body: Optional[str] = None,
    cc_emails: Optional[List[str]] = None,
    include_advisor_cc: bool = True,
) -> Dict[str, Any]:
    """
    Email the proposal DOCX from MAIL_USERNAME (Anshul / firm mailbox).
    Caller must ensure proposal is approved and DOCX exists.
    """
    path = getattr(proposal, "docx_path", None) or ""
    if not path or not os.path.isfile(path):
        return {"error": "No proposal DOCX found. Generate the document first."}

    recipients = [e.strip() for e in (recipient_emails or []) if e and e.strip()]
    if not recipients:
        email = (getattr(lead, "email", None) or "").strip()
        if email:
            recipients = [email]
    if not recipients:
        return {"error": "Lead has no email address."}

    creds = _smtp_credentials()
    if not creds["username"] or not creds["password"]:
        return {
            "error": "Email not configured. Set MAIL_USERNAME and MAIL_PASSWORD (same as recommendations)."
        }

    lead_name = getattr(lead, "name", None) or "Client"
    title = getattr(proposal, "title", None) or "Investment Proposal"
    subj = subject or f"Investment proposal — {title}"
    body = (message_body or "").strip() or (
        f"Dear {lead_name},\n\n"
        f"Please find attached our investment proposal.\n\n"
        f"Best regards,\nInertia / Equities4Wealth\n"
    )
    try:
        from services.email_source_tag import tag_body, tag_subject

        subj = tag_subject(subj)
        body = tag_body(body) or body
    except Exception:
        pass

    msg = MIMEMultipart()
    msg["From"] = creds["username"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subj
    ccs = cc_emails if cc_emails is not None else _proposal_cc_list(
        lead=lead, include_advisor=include_advisor_cc
    )
    # Don't CC primary recipients
    ccs = [c for c in ccs if c.lower() not in {r.lower() for r in recipients}]
    if ccs:
        msg["Cc"] = ", ".join(ccs)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(path, "rb") as fh:
        part = MIMEApplication(fh.read(), Name=os.path.basename(path) or "proposal.docx")
    part["Content-Disposition"] = f'attachment; filename="{os.path.basename(path) or "proposal.docx"}"'
    msg.attach(part)

    try:
        if creds["use_ssl"]:
            smtp = smtplib.SMTP_SSL(creds["server"], creds["port"], timeout=30)
        else:
            smtp = smtplib.SMTP(creds["server"], creds["port"], timeout=30)
            if creds["use_tls"]:
                smtp.starttls()
        smtp.login(creds["username"], creds["password"])
        all_rcpt = list(recipients) + list(ccs)
        smtp.sendmail(creds["username"], all_rcpt, msg.as_string().encode("utf-8"))
        smtp.quit()
    except Exception as exc:
        logger.exception("Proposal email failed for lead %s", getattr(lead, "id", None))
        return {"error": str(exc)}

    return {
        "success": True,
        "recipients": recipients,
        "cc": ccs,
        "sender": creds["username"],
    }
