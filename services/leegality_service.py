"""Leegality Document Execution API — send agreement PDFs for e-sign and process webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlencode, urljoin

import requests
from flask import current_app
from werkzeug.utils import secure_filename

from extensions import db
from services.agreement_pdf_paths import agreement_pdf_abs_path

logger = logging.getLogger(__name__)

LEEGALITY_META_KEY = "leegality"
LEEGALITY_DOCUMENT_ID_VAR = "leegality_document_id"
LEEGALITY_SIGN_URL_VAR = "leegality_sign_url"
LEEGALITY_IRN_VAR = "leegality_irn"


class LeegalityError(Exception):
    """Raised when Leegality config or API calls fail in a user-visible way."""


def _cfg(key: str, default: str = "") -> str:
    try:
        val = current_app.config.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    except RuntimeError:
        pass
    return (os.environ.get(key) or default).strip()


def is_configured() -> bool:
    return bool(_cfg("LEEGALITY_AUTH_TOKEN") and _cfg("LEEGALITY_PROFILE_ID"))


def api_base() -> str:
    base = _cfg("LEEGALITY_API_BASE", "https://app1.leegality.com/api")
    return base.rstrip("/") + "/"


def auth_token() -> str:
    token = _cfg("LEEGALITY_AUTH_TOKEN")
    if not token:
        raise LeegalityError("LEEGALITY_AUTH_TOKEN is not set in .env")
    return token


def private_salt() -> str:
    salt = _cfg("LEEGALITY_PRIVATE_SALT")
    if not salt:
        raise LeegalityError("LEEGALITY_PRIVATE_SALT is not set in .env")
    return salt


def profile_id() -> str:
    pid = _cfg("LEEGALITY_PROFILE_ID")
    if not pid:
        raise LeegalityError("LEEGALITY_PROFILE_ID is not set in .env")
    return pid


def _headers(*, json_body: bool = True) -> dict:
    headers = {
        "X-Auth-Token": auth_token(),
        "Accept": "application/json",
    }
    # Do NOT send Content-Type: application/json on GET — some gateways then
    # ignore query params and report documentId as null.
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def load_agreement_json(agreement) -> dict:
    if not agreement.agreement_data:
        return {}
    try:
        data = json.loads(agreement.agreement_data)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def get_leegality_meta(agreement) -> dict:
    data = load_agreement_json(agreement)
    meta = data.get(LEEGALITY_META_KEY) or {}
    if not isinstance(meta, dict):
        meta = {}
    else:
        meta = dict(meta)
    # Durable fallback when agreement_data JSON was rewritten
    for var_name, meta_key in (
        (LEEGALITY_DOCUMENT_ID_VAR, "document_id"),
        (LEEGALITY_SIGN_URL_VAR, "client_sign_url"),
        (LEEGALITY_IRN_VAR, "irn"),
    ):
        if meta.get(meta_key):
            continue
        val = _agreement_variable_value(agreement, var_name)
        if val:
            meta[meta_key] = val
    return meta


def _agreement_variable_value(agreement, name: str) -> str:
    try:
        for var in getattr(agreement, "variables", None) or []:
            if (getattr(var, "variable_name", None) or "").strip() == name:
                text = (getattr(var, "variable_value", None) or "").strip()
                if text:
                    return text
        from models import AgreementVariables

        row = AgreementVariables.query.filter_by(
            agreement_id=agreement.id, variable_name=name
        ).first()
        if row and (row.variable_value or "").strip():
            return (row.variable_value or "").strip()
    except Exception:
        pass
    return ""


def _upsert_agreement_variable(agreement, name: str, value: str) -> None:
    from models import AgreementVariables

    value = (value or "").strip()
    if not value:
        return
    existing = None
    for var in getattr(agreement, "variables", None) or []:
        if (getattr(var, "variable_name", None) or "").strip() == name:
            existing = var
            break
    if existing is None:
        try:
            existing = AgreementVariables.query.filter_by(
                agreement_id=agreement.id, variable_name=name
            ).first()
        except Exception:
            existing = None
    lead_id = getattr(agreement, "lead_id", None)
    client_id = None
    try:
        lead = getattr(agreement, "lead", None)
        if lead is not None:
            client_id = getattr(lead, "client_id", None)
    except Exception:
        pass
    if existing:
        existing.variable_value = value
        existing.variable_type = "leegality"
    else:
        db.session.add(
            AgreementVariables(
                agreement_id=agreement.id,
                lead_id=lead_id or 0,
                client_id=client_id,
                variable_name=name,
                variable_value=value,
                variable_type="leegality",
            )
        )


def preserve_leegality_in_agreement_data(agreement, new_data: dict) -> dict:
    """Keep e-sign metadata when other flows rewrite agreement_data JSON."""
    if not isinstance(new_data, dict):
        new_data = {}
    else:
        new_data = dict(new_data)
    if LEEGALITY_META_KEY not in new_data:
        prev = get_leegality_meta(agreement)
        if prev:
            new_data[LEEGALITY_META_KEY] = prev
    return new_data


def resolve_stored_document_id(meta: dict) -> str:
    """Best-effort documentId from saved Leegality meta."""
    if not isinstance(meta, dict):
        return ""
    raw = meta.get("document_id") or meta.get("documentId")
    if not raw and isinstance(meta.get("raw_create"), dict):
        raw = meta["raw_create"].get("documentId") or meta["raw_create"].get("document_id")
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text or text.lower() in ("null", "none"):
        return ""
    return text


def save_leegality_meta(agreement, updates: dict, *, commit: bool = False) -> dict:
    data = load_agreement_json(agreement)
    meta = dict(data.get(LEEGALITY_META_KEY) or {})
    meta.update({k: v for k, v in updates.items() if v is not None})
    data[LEEGALITY_META_KEY] = meta
    agreement.agreement_data = json.dumps(data)
    agreement.updated_at = datetime.utcnow()
    if commit:
        db.session.commit()
    return meta


def clear_leegality_send(agreement, *, commit: bool = True) -> dict:
    """Drop active document id so a new send is allowed (keeps history fields)."""
    data = load_agreement_json(agreement)
    meta = dict(data.get(LEEGALITY_META_KEY) or {})
    old_id = meta.get("document_id") or _agreement_variable_value(
        agreement, LEEGALITY_DOCUMENT_ID_VAR
    )
    prior = meta.get("prior_document_ids") or []
    if not isinstance(prior, list):
        prior = []
    if old_id and old_id not in prior:
        prior.append(old_id)
    for key in (
        "document_id",
        "client_sign_url",
        "document_status",
        "expiry_date",
        "raw_create",
    ):
        meta.pop(key, None)
    meta["prior_document_ids"] = prior[-10:]
    meta["status"] = "cleared"
    meta["cleared_at"] = datetime.utcnow().isoformat() + "Z"
    data[LEEGALITY_META_KEY] = meta
    agreement.agreement_data = json.dumps(data)
    agreement.updated_at = datetime.utcnow()
    # Clear durable vars so Refresh cannot call details with a stale id after re-send prep
    for name in (LEEGALITY_DOCUMENT_ID_VAR, LEEGALITY_SIGN_URL_VAR):
        try:
            for var in getattr(agreement, "variables", None) or []:
                if (getattr(var, "variable_name", None) or "").strip() == name:
                    var.variable_value = ""
        except Exception:
            pass
    if commit:
        db.session.commit()
    return meta


# Lead statuses at or past e-sign completion — do not regress these.
_LEAD_STATUSES_PAST_SIGNED = frozenset(
    {
        "agreement_signed",
        "onboarding_started",
        "onboarding_completed",
        "dropped",
    }
)


def _apply_signed_statuses(agreement) -> None:
    """Mark agreement signed and promote lead to Agreement Signed when e-sign completes."""
    agreement.status = "signed"
    if not agreement.signed_date:
        agreement.signed_date = datetime.utcnow()

    lead = getattr(agreement, "lead", None)
    if lead is None and getattr(agreement, "lead_id", None):
        try:
            from models import Lead

            lead = Lead.query.get(agreement.lead_id)
        except Exception:
            lead = None
    if not lead:
        return

    current = (getattr(lead, "status", None) or "").strip().lower()
    if current not in _LEAD_STATUSES_PAST_SIGNED:
        lead.status = "agreement_signed"
        logger.info(
            "Lead %s status set to agreement_signed after Leegality completion (agreement %s)",
            getattr(lead, "id", None),
            getattr(agreement, "id", None),
        )
    try:
        from services.lead_client_shell_service import ensure_client_shell_for_lead

        ensure_client_shell_for_lead(lead)
    except Exception:
        logger.warning(
            "Client shell creation skipped for lead %s after agreement signed",
            getattr(lead, "id", None),
            exc_info=True,
        )


def _promote_lead_agreement_sent(agreement) -> None:
    """When sending for e-sign, move lead to Agreement Sent if still earlier."""
    lead = getattr(agreement, "lead", None)
    if not lead:
        return
    current = (getattr(lead, "status", None) or "").strip().lower()
    if current in _LEAD_STATUSES_PAST_SIGNED or current == "agreement_sent":
        return
    # Only auto-promote from nearby pre-sign statuses (avoid jumping from "new")
    if current in (
        "agreement",
        "agreement_signing",
        "agreement_reviewed",
        "kyc_uploaded",
        "proposal_accepted",
        "acceptance",
        "",
    ):
        lead.status = "agreement_sent"


def verify_webhook_mac(document_id: str, mac: str) -> bool:
    """HMAC-SHA1(documentId, privateSalt) — key=salt, message=documentId."""
    if not document_id or not mac:
        return False
    salt = _cfg("LEEGALITY_PRIVATE_SALT")
    if not salt:
        logger.warning("LEEGALITY_PRIVATE_SALT missing; rejecting webhook")
        return False
    computed = hmac.new(
        salt.encode("utf-8"),
        str(document_id).encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    return hmac.compare_digest(computed.lower(), str(mac).strip().lower())


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        return digits
    return None


def _agreement_field_map(agreement) -> dict[str, str]:
    """Flatten AgreementVariables + agreement_data string fields (lowercased keys)."""
    by_name: dict[str, str] = {}
    try:
        for var in getattr(agreement, "variables", None) or []:
            key = (getattr(var, "variable_name", None) or "").strip().lower()
            text = (getattr(var, "variable_value", None) or "").strip()
            if key and text:
                by_name[key] = text
    except Exception:
        pass
    data = load_agreement_json(agreement)
    for key, raw in data.items():
        if not isinstance(key, str) or key == LEEGALITY_META_KEY:
            continue
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if text:
            by_name[key.strip().lower()] = text
    return by_name


def _first_agreement_field(fields: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        val = (fields.get(key) or "").strip()
        if val:
            return val
    return ""


_AGREEMENT_NAME_KEYS = (
    "client_name",
    "name_as_per_pan",
    "client_signature",
    "client",
)
_AGREEMENT_EMAIL_KEYS = (
    "client_email",
    "email",
    "email_id",
    "e_mail",
)
_AGREEMENT_PHONE_KEYS = (
    "client_mobile",
    "aadhaar_mobile",
    "client_phone",
    "mobile",
    "phone",
    "contact_number",
)


def _agreement_client_display_name(agreement, lead=None) -> str:
    """Name for Leegality: agreement fields only (then optional linked Client / lead)."""
    fields = _agreement_field_map(agreement)
    name = _first_agreement_field(fields, _AGREEMENT_NAME_KEYS)
    if name:
        return name
    # Name-only fallbacks — contact must still come from agreement
    client = None
    if lead is not None:
        client = getattr(lead, "client", None)
    if client is None and lead is None:
        lead = getattr(agreement, "lead", None)
        client = getattr(lead, "client", None) if lead is not None else None
    if client is not None:
        text = (getattr(client, "name", None) or "").strip()
        if text:
            return text
    if lead is not None:
        return (getattr(lead, "name", None) or "").strip()
    return ""


def invitee_from_agreement(agreement, lead=None) -> dict:
    """
    Build the API invitee for workflow slot 2 (client).

    Name, email, and mobile come from **agreement data only** (variables /
    agreement_data). CRM lead/client contact is not used — Aadhaar-linked
    mobile often differs from the CRM number.
    """
    lead = lead if lead is not None else getattr(agreement, "lead", None)
    fields = _agreement_field_map(agreement)

    name = _first_agreement_field(fields, _AGREEMENT_NAME_KEYS) or _agreement_client_display_name(
        agreement, lead
    )
    email = _first_agreement_field(fields, _AGREEMENT_EMAIL_KEYS) or None
    phone = normalize_phone(_first_agreement_field(fields, _AGREEMENT_PHONE_KEYS) or None)

    if not name:
        raise LeegalityError(
            "Client name on the agreement is required to send for e-sign "
            "(set client / client_name / name as per PAN in agreement variables)."
        )
    missing = []
    if not email:
        missing.append("email (client_email)")
    if not phone:
        missing.append("mobile (client_mobile / aadhaar_mobile)")
    if missing:
        raise LeegalityError(
            "Agreement is missing " + " and ".join(missing) + ". "
            "Enter the Aadhaar-linked email and mobile on the agreement "
            "(E-sign card or Edit Variables) — CRM lead contact is not used."
        )
    return {"name": name[:255], "email": email, "phone": phone}


def invitee_from_lead(lead) -> dict:
    """Test/compat helper: map lead contact into synthetic agreement fields."""
    data = {
        "client": (getattr(lead, "name", None) or "").strip(),
        "client_email": (getattr(lead, "email", None) or "").strip(),
        "client_mobile": (getattr(lead, "phone", None) or "").strip(),
    }
    return invitee_from_agreement(
        SimpleNamespace(variables=[], agreement_data=json.dumps(data), lead=lead),
        lead,
    )


def save_agreement_signing_contact(
    agreement,
    *,
    email: Optional[str] = None,
    mobile: Optional[str] = None,
    commit: bool = False,
) -> dict:
    """Persist Aadhaar/signing email + mobile onto the agreement (not CRM)."""
    email_text = (email or "").strip()
    phone_text = normalize_phone(mobile) or ""
    if email_text:
        _upsert_agreement_variable(agreement, "client_email", email_text)
    if phone_text:
        _upsert_agreement_variable(agreement, "client_mobile", phone_text)
        _upsert_agreement_variable(agreement, "aadhaar_mobile", phone_text)
    # Keep JSON copy in sync for fill/download paths that read agreement_data
    data = load_agreement_json(agreement)
    if email_text:
        data["client_email"] = email_text
    if phone_text:
        data["client_mobile"] = phone_text
        data["aadhaar_mobile"] = phone_text
    # Preserve leegality meta
    if LEEGALITY_META_KEY not in data:
        prev = get_leegality_meta(agreement)
        if prev:
            data[LEEGALITY_META_KEY] = prev
    agreement.agreement_data = json.dumps(data)
    agreement.updated_at = datetime.utcnow()
    if commit:
        db.session.commit()
    return {"client_email": email_text or None, "client_mobile": phone_text or None}


def lead_contact_defaults(lead) -> dict[str, str]:
    """Email/mobile defaults from lead (then linked Client) for prefilling the form."""
    if lead is None:
        return {"email": "", "mobile": ""}
    client = getattr(lead, "client", None)

    def _pick(attr: str) -> str:
        lead_val = (getattr(lead, attr, None) or "").strip()
        if lead_val:
            return lead_val
        if client is not None:
            return (getattr(client, attr, None) or "").strip()
        return ""

    email = _pick("email")
    mobile = normalize_phone(_pick("phone")) or ""
    return {"email": email, "mobile": mobile}


def signing_contact_for_form(agreement, lead=None) -> dict[str, str]:
    """
    Values for the E-sign email/mobile inputs.

    Prefer agreement-stored values; if blank, copy from lead so the user only
    edits when Aadhaar-linked contact differs.
    """
    lead = lead if lead is not None else getattr(agreement, "lead", None)
    fields = _agreement_field_map(agreement)
    defaults = lead_contact_defaults(lead)
    name = _first_agreement_field(fields, _AGREEMENT_NAME_KEYS) or _agreement_client_display_name(
        agreement, lead
    )
    email = _first_agreement_field(fields, _AGREEMENT_EMAIL_KEYS) or defaults["email"]
    mobile = _first_agreement_field(fields, _AGREEMENT_PHONE_KEYS) or defaults["mobile"]
    return {
        "name": name,
        "email": email,
        "mobile": mobile,
        "email_from_lead": not bool(_first_agreement_field(fields, _AGREEMENT_EMAIL_KEYS))
        and bool(defaults["email"]),
        "mobile_from_lead": not bool(_first_agreement_field(fields, _AGREEMENT_PHONE_KEYS))
        and bool(defaults["mobile"]),
    }


def firm_invitee_from_config() -> Optional[dict]:
    """
    Optional Inertia/firm signer from env — only when Anshul is also an API
    invitee (not Fixed). For the usual workflow (Anshul Fixed + client API),
    leave LEEGALITY_FIRM_SIGNER_* empty.
    """
    name = _cfg("LEEGALITY_FIRM_SIGNER_NAME")
    email = _cfg("LEEGALITY_FIRM_SIGNER_EMAIL") or None
    phone = normalize_phone(_cfg("LEEGALITY_FIRM_SIGNER_PHONE") or None)
    if not name:
        return None
    if not email and not phone:
        raise LeegalityError(
            "LEEGALITY_FIRM_SIGNER_NAME is set but email/phone is missing "
            "(set LEEGALITY_FIRM_SIGNER_EMAIL and/or LEEGALITY_FIRM_SIGNER_PHONE)"
        )
    out: dict[str, Any] = {"name": name[:255]}
    if email:
        out["email"] = email
    if phone:
        out["phone"] = phone
    return out


def build_invitees_for_send(agreement, lead=None) -> tuple[list[dict], dict]:
    """
    Build invitee list matching Leegality workflow order.

    Workflow layout (your dashboard):
      Invitee 1 = Anshul (filled / Fixed)
      Invitee 2 = Invitee(2.1).* API placeholders

    The Create API maps invitees[] **by position**. Sending only [client] fills
    slot 1 (overridden back to Anshul) and leaves Invitee(2.1) empty — client
    never gets invited. So we must send [firm, client].
    """
    lead = lead if lead is not None else getattr(agreement, "lead", None)
    client = invitee_from_agreement(agreement, lead)
    firm = firm_invitee_from_config()
    order = (_cfg("LEEGALITY_INVITEE_ORDER") or "firm_first").strip().lower()

    if order in ("client_only", "api_client_only"):
        return [client], {"client": client, "firm": None, "order": "client_only"}

    if not firm:
        raise LeegalityError(
            "This Leegality workflow has 2 invitees (Anshul then client). "
            "Set LEEGALITY_FIRM_SIGNER_NAME, LEEGALITY_FIRM_SIGNER_EMAIL, and "
            "LEEGALITY_FIRM_SIGNER_PHONE in .env so the app can send "
            "[Anshul, client] in order. Without that, only Anshul is invited."
        )

    if order == "client_first":
        return [client, firm], {"client": client, "firm": firm, "order": "client_first"}

    return [firm, client], {"client": client, "firm": firm, "order": "firm_first"}


def _invitees_list(data: dict) -> list[dict]:
    raw = data.get("invitees") or data.get("requests") or []
    if not isinstance(raw, list):
        return []
    return [i for i in raw if isinstance(i, dict)]


def _invitee_contact_key(inv: dict) -> tuple[str, str, str]:
    email = (inv.get("email") or "").strip().lower()
    phone = normalize_phone(inv.get("phone")) or ""
    name = (inv.get("name") or "").strip().lower()
    return email, phone, name


def _invitee_matches(inv: dict, target: dict) -> bool:
    if not target:
        return False
    ie, ip, iname = _invitee_contact_key(inv)
    te, tp, tname = _invitee_contact_key(target)
    if te and ie and te == ie:
        return True
    if tp and ip and tp == ip:
        return True
    if tname and iname and tname == iname:
        return True
    return False


def _summarize_invitees(invitees: list[dict]) -> list[dict]:
    out = []
    for inv in invitees:
        out.append(
            {
                "name": inv.get("name"),
                "email": inv.get("email"),
                "phone": inv.get("phone"),
                "sign_url": inv.get("signUrl") or inv.get("invitationUrl"),
                "active": inv.get("active"),
                "expiry_date": inv.get("expiryDate"),
            }
        )
    return out


def _client_sign_url(data: dict, client_invitee: Optional[dict] = None) -> Optional[str]:
    """Prefer the invitee that matches the client/lead; never blindly take slot 1 (often Anshul)."""
    invitees = _invitees_list(data)
    if not invitees:
        return None

    if client_invitee:
        for inv in invitees:
            if _invitee_matches(inv, client_invitee):
                url = inv.get("signUrl") or inv.get("invitationUrl")
                if url:
                    return url

    # Fallback: last invitee with a URL (client is usually 2nd when firm is first)
    for inv in reversed(invitees):
        url = inv.get("signUrl") or inv.get("invitationUrl")
        if url:
            return url
    return None


def _assert_client_invited(data: dict, client_invitee: dict) -> None:
    invitees = _invitees_list(data)
    if not invitees:
        raise LeegalityError(
            "Leegality returned no invitees. Check the workflow has invitee slots "
            "and that API invitees are allowed."
        )
    names = ", ".join((i.get("name") or i.get("email") or "?") for i in invitees)
    if len(invitees) < 2:
        raise LeegalityError(
            f"Leegality only created {len(invitees)} invitee ({names}). "
            "The client was not added — the document will complete after you sign alone. "
            "In the Leegality workflow add a 2nd invitee (empty name/email, type API/dynamic, "
            "Aadhaar eSign), keep Anshul as 1st Fixed, and enable fixed signing order. "
            "Then re-publish the workflow and Re-send."
        )
    for inv in invitees:
        if _invitee_matches(inv, client_invitee):
            return
    raise LeegalityError(
        f"Client ({client_invitee.get('name')}) was not among Leegality invitees "
        f"(got: {names}). The 2nd workflow invitee must be an API/dynamic slot so the "
        "app can fill the client's name, email, and mobile."
    )


def pdf_to_base64(pdf_abs_path: str) -> str:
    size = os.path.getsize(pdf_abs_path)
    if size > 15 * 1024 * 1024:
        raise LeegalityError("PDF exceeds Leegality's 15 MB limit")
    with open(pdf_abs_path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def create_sign_request(
    *,
    pdf_abs_path: str,
    file_name: str,
    invitees: list[dict],
    irn: Optional[str] = None,
) -> dict:
    """POST /v3.0/sign/request — returns parsed data object on success."""
    payload: dict[str, Any] = {
        "profileId": profile_id(),
        "file": {
            "name": _safe_file_name(file_name),
            "file": pdf_to_base64(pdf_abs_path),
        },
        "invitees": invitees,
    }
    if irn:
        payload["irn"] = str(irn)[:255]

    url = urljoin(api_base(), "v3.0/sign/request")
    logger.info("Leegality create sign request irn=%s url=%s", irn, url)
    resp = requests.post(url, headers=_headers(json_body=True), json=payload, timeout=90)
    try:
        body = resp.json()
    except ValueError:
        raise LeegalityError(f"Leegality returned non-JSON ({resp.status_code})")

    status = body.get("status")
    if resp.status_code >= 400 or status == 0:
        messages = body.get("messages") or body.get("data") or body
        raise LeegalityError(f"Leegality rejected the request: {messages}")

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    doc_id = resolve_stored_document_id(
        {"document_id": data.get("documentId") or data.get("document_id"), "raw_create": data}
    )
    if not doc_id:
        raise LeegalityError(f"Leegality response missing documentId: {body}")
    # Normalize for callers
    data = dict(data)
    data["documentId"] = doc_id
    return data


def fetch_transaction_status(document_id: str) -> dict:
    """GET /v3.2/sign/request?documentId=… — lightweight status + file URLs."""
    doc_id = str(document_id or "").strip()
    if not doc_id or doc_id.lower() in ("null", "none"):
        raise LeegalityError(
            "No Leegality document ID on this agreement. Send to Leegality first "
            "(or re-send if status was cleared)."
        )
    query = urlencode({"documentId": doc_id})
    url = urljoin(api_base(), f"v3.2/sign/request?{query}")
    logger.info("Leegality transaction status documentId=%s", doc_id)
    resp = requests.get(url, headers=_headers(json_body=False), timeout=60)
    try:
        body = resp.json()
    except ValueError:
        raise LeegalityError(f"Transaction status non-JSON ({resp.status_code})")
    if resp.status_code >= 400 or body.get("status") == 0:
        raise LeegalityError(
            f"Document status failed for {doc_id}: {body.get('messages') or body}"
        )
    data = body.get("data")
    return data if isinstance(data, dict) else body


def fetch_document_details(document_id: str, *, include_file: bool = False) -> dict:
    """GET /v3.3/document/details — richer details; prefer transaction status for refresh."""
    doc_id = str(document_id or "").strip()
    if not doc_id or doc_id.lower() in ("null", "none"):
        raise LeegalityError(
            "No Leegality document ID on this agreement. Send to Leegality first "
            "(or re-send if status was cleared)."
        )
    params: dict[str, str] = {"documentId": doc_id}
    if include_file:
        params["file"] = "true"
        params["auditTrail"] = "true"
    # Embed documentId in the URL as well as params (belt-and-suspenders).
    query = urlencode(params)
    url = urljoin(api_base(), f"v3.3/document/details?{query}")
    logger.info("Leegality document details documentId=%s include_file=%s", doc_id, include_file)
    resp = requests.get(url, headers=_headers(json_body=False), timeout=60)
    try:
        body = resp.json()
    except ValueError:
        raise LeegalityError(f"Document details non-JSON ({resp.status_code})")
    if resp.status_code >= 400 or body.get("status") == 0:
        raise LeegalityError(
            f"Document details failed for {doc_id}: {body.get('messages') or body}"
        )
    data = body.get("data")
    return data if isinstance(data, dict) else body


def _normalize_status_payload(details: dict) -> dict:
    """
    Map either document/details or transaction-status payloads into a common shape
    used by refresh_agreement_from_leegality.
    """
    if not isinstance(details, dict):
        return {}
    out = dict(details)

    # Transaction status uses requests[] + boolean signed flags
    requests_list = details.get("requests")
    if isinstance(requests_list, list) and requests_list:
        out.setdefault("invitations", requests_list)
        signed_flags = [
            bool(r.get("signed"))
            for r in requests_list
            if isinstance(r, dict)
        ]
        rejected = any(
            bool(r.get("rejected")) for r in requests_list if isinstance(r, dict)
        )
        if signed_flags and all(signed_flags):
            out["documentStatus"] = "Completed"
        elif rejected:
            out["documentStatus"] = "Rejected"
        elif any(bool(r.get("expired")) for r in requests_list if isinstance(r, dict)):
            out["documentStatus"] = "Expired"
        else:
            out["documentStatus"] = details.get("documentStatus") or "Sent"

    # files may be a list (transaction status) or file string (details)
    file_url = details.get("file")
    if not file_url:
        files = details.get("files")
        if isinstance(files, list) and files:
            first = files[0]
            if isinstance(first, str) and first.startswith("http"):
                file_url = first
            elif isinstance(first, dict):
                file_url = first.get("file") or first.get("url")
    if file_url:
        out["file"] = file_url

    return out


def download_url_to_agreements_folder(url: str, filename: str) -> str:
    """Download CDN URL immediately (15s TTL) into static/agreements/. Returns web path."""
    from flask import current_app

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    folder = os.path.join(current_app.root_path, "static", "agreements")
    os.makedirs(folder, exist_ok=True)
    safe = secure_filename(filename) or f"signed_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    abs_path = os.path.join(folder, safe)
    with open(abs_path, "wb") as fh:
        fh.write(resp.content)
    return f"/static/agreements/{safe}"


def _safe_file_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9+\|\-:(),_.\[\]&/@ ]", "_", name or "agreement")
    cleaned = cleaned.strip()[:200] or "agreement"
    return cleaned


def send_agreement_for_esign(agreement) -> dict:
    """Generate send to Leegality from agreement PDF + lead invitee. Persists meta."""
    if not is_configured():
        raise LeegalityError(
            "Leegality is not configured. Set LEEGALITY_AUTH_TOKEN, "
            "LEEGALITY_PRIVATE_SALT, and LEEGALITY_PROFILE_ID in .env"
        )

    existing = get_leegality_meta(agreement)
    if existing.get("document_id") and (existing.get("status") or "").lower() not in (
        "rejected",
        "error",
        "expired",
        "cancelled",
    ):
        doc_status = (existing.get("document_status") or existing.get("status") or "").lower()
        if doc_status not in ("completed", "rejected", "expired", "error"):
            raise LeegalityError(
                f"Already sent to Leegality (document {existing.get('document_id')}). "
                "Use Refresh status, or clear the prior send before re-sending."
            )

    pdf_abs = agreement_pdf_abs_path(agreement.generated_pdf_path)
    if not pdf_abs:
        raise LeegalityError("Agreement PDF is missing. Generate or upload the PDF first.")

    lead = agreement.lead
    if not lead:
        raise LeegalityError("Agreement has no linked lead")

    invitees, invitee_plan = build_invitees_for_send(agreement, lead)
    client_invitee = invitee_plan["client"]
    lead_slug = secure_filename(lead.name or "client")[:40] or "client"
    file_name = f"Agreement_{agreement.id}_{lead_slug}"
    irn = f"AGR-{agreement.id}"

    logger.info(
        "Leegality send agreement=%s order=%s invitees=%s",
        agreement.id,
        invitee_plan.get("order"),
        [
            {"name": i.get("name"), "email": i.get("email"), "phone": i.get("phone")}
            for i in invitees
        ],
    )

    data = create_sign_request(
        pdf_abs_path=pdf_abs,
        file_name=file_name,
        invitees=invitees,
        irn=irn,
    )

    _assert_client_invited(data, client_invitee)

    now = datetime.utcnow()
    document_id = resolve_stored_document_id(
        {"document_id": data.get("documentId"), "raw_create": data}
    )
    if not document_id:
        raise LeegalityError(f"Leegality response missing documentId: {data}")

    response_invitees = _summarize_invitees(_invitees_list(data))
    sign_url = _client_sign_url(data, client_invitee)
    irn_saved = data.get("irn") or irn
    meta = save_leegality_meta(
        agreement,
        {
            "document_id": document_id,
            "irn": irn_saved,
            "status": "sent",
            "document_status": "Sent",
            "client_sign_url": sign_url,
            "expiry_date": data.get("expiryDate"),
            "sent_at": now.isoformat() + "Z",
            "unsigned_pdf_path": agreement.generated_pdf_path,
            "invitee": client_invitee,
            "invitee_plan": invitee_plan.get("order"),
            "firm_invitee": invitee_plan.get("firm"),
            "invitees": response_invitees,
            "raw_create": {
                "documentId": document_id,
                "irn": irn_saved,
                "expiryDate": data.get("expiryDate"),
                "invitees": response_invitees,
            },
        },
    )
    _upsert_agreement_variable(agreement, LEEGALITY_DOCUMENT_ID_VAR, document_id)
    if sign_url:
        _upsert_agreement_variable(agreement, LEEGALITY_SIGN_URL_VAR, sign_url)
    if irn_saved:
        _upsert_agreement_variable(agreement, LEEGALITY_IRN_VAR, str(irn_saved))
    agreement.status = "sent"
    agreement.sent_date = now
    _promote_lead_agreement_sent(agreement)
    db.session.commit()
    return meta


def refresh_agreement_from_leegality(agreement, document_id: Optional[str] = None) -> dict:
    """Poll document details; if completed, download signed PDF."""
    meta = get_leegality_meta(agreement)
    document_id = resolve_stored_document_id(
        {"document_id": document_id} if document_id else meta
    ) or resolve_stored_document_id(meta)
    if not document_id:
        raise LeegalityError(
            "No Leegality document ID on this agreement. "
            "Send to Leegality first. If you already sent, regenerating the agreement "
            "may have cleared e-sign metadata — use Re-send, or paste the Document ID "
            "from the Leegality dashboard."
        )

    try:
        raw = fetch_transaction_status(document_id)
        details = _normalize_status_payload(raw)
    except LeegalityError as status_exc:
        logger.warning(
            "Leegality transaction status failed (%s); falling back to document details",
            status_exc,
        )
        raw = fetch_document_details(document_id, include_file=True)
        details = _normalize_status_payload(raw)
    doc_status = details.get("documentStatus") or details.get("status") or ""
    updates: dict[str, Any] = {
        "document_status": doc_status,
        "last_refreshed_at": datetime.utcnow().isoformat() + "Z",
        "document_id": document_id,
    }

    invitations = (
        details.get("invitations")
        or details.get("invitees")
        or details.get("requests")
        or []
    )
    if isinstance(invitations, list) and invitations:
        summary = []
        for i in invitations:
            if not isinstance(i, dict):
                continue
            status = i.get("invitationStatus") or i.get("action") or i.get("status")
            if status is None:
                if i.get("signed"):
                    status = "Signed"
                elif i.get("rejected"):
                    status = "Rejected"
                elif i.get("expired"):
                    status = "Expired"
                else:
                    status = "Pending"
            summary.append(
                {
                    "name": i.get("name"),
                    "email": i.get("email"),
                    "status": status,
                    "signed_at": i.get("signedAt") or i.get("signDate"),
                    "sign_url": i.get("signUrl") or i.get("invitationUrl"),
                    "active": i.get("active"),
                }
            )
        updates["invitations_summary"] = summary
        updates["invitees"] = _summarize_invitees(invitations)

    file_url = details.get("file")
    completed = str(doc_status).lower() in ("completed", "complete", "signed")
    if completed and file_url and isinstance(file_url, str) and file_url.startswith("http"):
        lead_name = secure_filename(getattr(agreement.lead, "name", None) or "client")[:40]
        web_path = download_url_to_agreements_folder(
            file_url,
            f"{lead_name}_signed_{agreement.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf",
        )
        updates["signed_pdf_path"] = web_path
        updates["status"] = "completed"
        updates["completed_at"] = datetime.utcnow().isoformat() + "Z"
        if not meta.get("unsigned_pdf_path") and agreement.generated_pdf_path:
            updates["unsigned_pdf_path"] = agreement.generated_pdf_path
        agreement.generated_pdf_path = web_path
        _apply_signed_statuses(agreement)
    elif completed:
        updates["status"] = "completed"
        _apply_signed_statuses(agreement)

    _upsert_agreement_variable(agreement, LEEGALITY_DOCUMENT_ID_VAR, document_id)
    return save_leegality_meta(agreement, updates, commit=True)


def apply_webhook_payload(payload: dict) -> dict:
    """
    Process a verified Leegality webhook.
    Returns {ok, agreement_id?, action}.
    """
    from models import Agreement

    document_id = payload.get("documentId")
    mac = payload.get("mac")
    if not verify_webhook_mac(str(document_id or ""), str(mac or "")):
        return {"ok": False, "error": "invalid_mac"}

    agreement = _find_agreement_by_document_id(str(document_id))
    if not agreement:
        logger.warning("Leegality webhook for unknown documentId=%s", document_id)
        return {"ok": True, "action": "ignored_unknown_document"}

    doc_status = payload.get("documentStatus") or ""
    req = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    updates = {
        "document_status": doc_status,
        "last_webhook_at": datetime.utcnow().isoformat() + "Z",
        "last_webhook_type": payload.get("webhookType"),
        "last_invitee_action": req.get("action"),
    }
    if req.get("invitationUrl"):
        updates["client_sign_url"] = req.get("invitationUrl")

    save_leegality_meta(agreement, updates, commit=True)

    if str(doc_status).lower() in ("completed", "complete"):
        try:
            refresh_agreement_from_leegality(agreement)
            return {"ok": True, "agreement_id": agreement.id, "action": "completed"}
        except Exception as exc:
            logger.exception("Failed to fetch signed PDF after webhook: %s", exc)
            return {
                "ok": True,
                "agreement_id": agreement.id,
                "action": "completed_meta_only",
                "error": str(exc),
            }

    return {"ok": True, "agreement_id": agreement.id, "action": "updated"}


def _find_agreement_by_document_id(document_id: str):
    from models import Agreement, AgreementVariables

    document_id = str(document_id or "").strip()
    if not document_id:
        return None

    try:
        var = (
            AgreementVariables.query.filter_by(
                variable_name=LEEGALITY_DOCUMENT_ID_VAR,
                variable_value=document_id,
            )
            .order_by(AgreementVariables.id.desc())
            .first()
        )
        if var and var.agreement_id:
            agr = Agreement.query.get(var.agreement_id)
            if agr:
                return agr
    except Exception:
        logger.exception("Leegality documentId variable lookup failed")

    # Prefer IRN AGR-{id} when present in recent rows; fall back to JSON scan.
    needle = f'"document_id": "{document_id}"'
    needle_alt = f'"document_id":"{document_id}"'
    q = Agreement.query.filter(
        Agreement.agreement_data.isnot(None),
        db.or_(
            Agreement.agreement_data.contains(needle),
            Agreement.agreement_data.contains(needle_alt),
        ),
    ).order_by(Agreement.id.desc())
    for agr in q.limit(50):
        meta = get_leegality_meta(agr)
        if resolve_stored_document_id(meta) == document_id:
            return agr
    return None
