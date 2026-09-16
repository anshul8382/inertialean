"""Zoho Books API — create invoice, sync status, record payment, fetch PDF (env-gated).

Defaults to Zoho Books. Set ZOHO_API_BASE / ZOHO_ACCOUNTS_URL for your DC
(e.g. https://www.zohoapis.com/books/v3 + https://accounts.zoho.com).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

import requests
from flask import current_app

logger = logging.getLogger(__name__)

_token_cache: Dict[str, Any] = {"access_token": None, "expires_at": 0.0}

_ZOHO_INV_RE = re.compile(r"\[zoho_invoice_id=([^\]]+)\]")
_ZOHO_PAY_RE = re.compile(r"\[zoho_payment_id=([^\]]+)\]")

# Zoho status → Inertia invoice.status
_STATUS_MAP = {
    "draft": "draft",
    "sent": "sent",
    "overdue": "overdue",
    "paid": "paid",
    "partially_paid": "sent",
    "viewed": "sent",
    "void": "cancelled",
    "unpaid": "sent",
}


def zoho_configured() -> bool:
    cfg = current_app.config
    return bool(
        cfg.get("ZOHO_CLIENT_ID")
        and cfg.get("ZOHO_CLIENT_SECRET")
        and cfg.get("ZOHO_REFRESH_TOKEN")
        and cfg.get("ZOHO_ORGANIZATION_ID")
    )


def _accounts_base() -> str:
    return (current_app.config.get("ZOHO_ACCOUNTS_URL") or "https://accounts.zoho.in").rstrip("/")


def _api_base() -> str:
    return (
        current_app.config.get("ZOHO_BOOKS_API_BASE")
        or "https://www.zohoapis.in/books/v3"
    ).rstrip("/")


def _org_id() -> str:
    return str(current_app.config.get("ZOHO_ORGANIZATION_ID") or "").strip()


def get_zoho_invoice_id(invoice) -> Optional[str]:
    m = _ZOHO_INV_RE.search(invoice.notes or "")
    return m.group(1).strip() if m else None


def get_zoho_payment_id(invoice) -> Optional[str]:
    m = _ZOHO_PAY_RE.search(invoice.notes or "")
    return m.group(1).strip() if m else None


def _set_note_marker(invoice, key: str, value: str) -> None:
    marker = f"[{key}={value}]"
    notes = invoice.notes or ""
    pattern = re.compile(rf"\[{re.escape(key)}=[^\]]*\]")
    if pattern.search(notes):
        invoice.notes = pattern.sub(marker, notes)
    else:
        invoice.notes = f"{notes}\n{marker}".strip() if notes else marker


def _get_access_token() -> Tuple[Optional[str], Optional[str]]:
    now = time.time()
    if _token_cache.get("access_token") and _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["access_token"], None

    if not zoho_configured():
        return None, (
            "Zoho is not configured (set ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, "
            "ZOHO_REFRESH_TOKEN, ZOHO_ORGANIZATION_ID)."
        )

    url = f"{_accounts_base()}/oauth/v2/token"
    data = {
        "refresh_token": current_app.config["ZOHO_REFRESH_TOKEN"],
        "client_id": current_app.config["ZOHO_CLIENT_ID"],
        "client_secret": current_app.config["ZOHO_CLIENT_SECRET"],
        "grant_type": "refresh_token",
    }
    try:
        resp = requests.post(url, data=data, timeout=30)
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "access_token" not in payload:
            msg = payload.get("error") or payload.get("message") or resp.text[:200]
            return None, f"Zoho OAuth failed: {msg}"
        _token_cache["access_token"] = payload["access_token"]
        _token_cache["expires_at"] = now + float(payload.get("expires_in", 3600))
        return _token_cache["access_token"], None
    except requests.RequestException as exc:
        return None, f"Zoho OAuth network error: {exc}"


def _request(
    method: str,
    path: str,
    *,
    json_body: dict = None,
    params: dict = None,
    raw: bool = False,
) -> Tuple[Any, Optional[str]]:
    token, err = _get_access_token()
    if err:
        return None, err
    url = f"{_api_base()}{path}"
    q = dict(params or {})
    q["organization_id"] = _org_id()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    if raw:
        headers["Accept"] = "application/pdf"
    try:
        resp = requests.request(
            method, url, headers=headers, params=q, json=json_body, timeout=60
        )
        if raw:
            if resp.status_code >= 400:
                try:
                    data = resp.json()
                    msg = data.get("message") or data.get("code") or resp.text[:300]
                except Exception:
                    msg = resp.text[:300]
                return None, f"Zoho API error ({resp.status_code}): {msg}"
            return resp.content, None
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            msg = data.get("message") or data.get("code") or resp.text[:300]
            return None, f"Zoho API error ({resp.status_code}): {msg}"
        return data, None
    except requests.RequestException as exc:
        return None, f"Zoho API network error: {exc}"


def find_or_create_contact(*, name: str, email: str, phone: str = "") -> Tuple[Optional[str], Optional[str]]:
    """Return Zoho contact_id."""
    if email:
        data, err = _request("GET", "/contacts", params={"email": email})
        if err:
            return None, err
        contacts = (data or {}).get("contacts") or []
        if contacts:
            return str(contacts[0]["contact_id"]), None

    body = {
        "contact_name": name or email or "Client",
        "contact_type": "customer",
        "email": email or None,
        "phone": phone or None,
    }
    data, err = _request("POST", "/contacts", json_body=body)
    if err:
        return None, err
    contact = (data or {}).get("contact") or {}
    cid = contact.get("contact_id")
    if not cid:
        return None, "Zoho did not return contact_id"
    return str(cid), None


def fetch_zoho_invoice(zoho_invoice_id: str) -> Tuple[Optional[dict], Optional[str]]:
    data, err = _request("GET", f"/invoices/{zoho_invoice_id}")
    if err:
        return None, err
    inv = (data or {}).get("invoice") or {}
    if not inv:
        return None, "Zoho invoice not found"
    return inv, None


def push_invoice_to_zoho(invoice, *, send_email: bool = True) -> Dict[str, Any]:
    """
    Create a Zoho Books invoice from an Inertia Invoice (idempotent if already linked).
    Optionally email the customer via Zoho.
    """
    if not zoho_configured():
        return {"error": "Zoho is not configured."}

    from extensions import db
    from models import Client

    existing = get_zoho_invoice_id(invoice)
    if existing:
        zoho_inv, err = fetch_zoho_invoice(existing)
        if err:
            return {"error": err}
        emailed = False
        email_err = None
        client = Client.query.get(invoice.client_id) if invoice.client_id else None
        if send_email and client and client.email:
            _, eerr = _request(
                "POST",
                f"/invoices/{existing}/email",
                json_body={
                    "to_mail_ids": [client.email],
                    "subject": f"Invoice {invoice.invoice_number}",
                    "body": "Please find your advisory fee invoice attached.",
                },
            )
            if eerr:
                email_err = eerr
            else:
                emailed = True
                if (invoice.status or "").lower() == "draft":
                    invoice.status = "sent"
                    db.session.commit()
        out = {
            "success": True,
            "zoho_invoice_id": existing,
            "already_linked": True,
            "zoho_status": (zoho_inv or {}).get("status"),
            "emailed": emailed,
            "invoice_number": invoice.invoice_number,
        }
        if email_err:
            out["email_warning"] = (
                f"{email_err}. Ask a manager to send the invoice from Zoho Books if needed."
            )
        return out

    client = Client.query.get(invoice.client_id) if invoice.client_id else None
    if not client:
        return {"error": "Invoice has no client; cannot push to Zoho."}

    contact_id, err = find_or_create_contact(
        name=client.name or "",
        email=client.email or "",
        phone=getattr(client, "phone", None) or "",
    )
    if err:
        return {"error": err}

    amount = invoice.total_amount if invoice.total_amount is not None else Decimal("0")
    line_desc = f"Advisory fee — {invoice.invoice_number}"
    try:
        from models import InvoiceLineItem

        items = InvoiceLineItem.query.filter_by(invoice_id=invoice.id).all()
        if items:
            line_desc = getattr(items[0], "description", None) or items[0].asset_class_name or line_desc
    except Exception:
        pass

    zoho_body = {
        "customer_id": contact_id,
        "reference_number": invoice.invoice_number,
        "date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "line_items": [
            {
                "name": "Investment Advisory Fee",
                "description": line_desc,
                "rate": float(amount),
                "quantity": 1,
            }
        ],
        "notes": f"Synced from Inertia invoice {invoice.invoice_number}",
    }
    data, err = _request("POST", "/invoices", json_body=zoho_body)
    if err:
        return {"error": err}

    zoho_inv = (data or {}).get("invoice") or {}
    zoho_id = zoho_inv.get("invoice_id")
    if not zoho_id:
        return {"error": "Zoho did not return invoice_id", "raw": data}

    _set_note_marker(invoice, "zoho_invoice_id", str(zoho_id))

    # Keep Zoho draft until email succeeds (manager can send from Zoho if API email fails)
    emailed = False
    email_err = None
    if send_email and client.email:
        _, eerr = _request(
            "POST",
            f"/invoices/{zoho_id}/email",
            json_body={
                "to_mail_ids": [client.email],
                "subject": f"Invoice {invoice.invoice_number}",
                "body": "Please find your advisory fee invoice attached.",
            },
        )
        if eerr:
            email_err = eerr
        else:
            emailed = True
            _, sent_err = _request("POST", f"/invoices/{zoho_id}/status/sent")
            if sent_err:
                logger.info("Zoho mark-sent after email failed for %s: %s", zoho_id, sent_err)

    if emailed and (invoice.status or "").lower() == "draft":
        invoice.status = "sent"

    db.session.commit()
    out = {
        "success": True,
        "zoho_invoice_id": str(zoho_id),
        "already_linked": False,
        "zoho_status": "sent" if emailed else "draft",
        "emailed": emailed,
        "invoice_number": invoice.invoice_number,
        "left_as_draft": not emailed,
    }
    if email_err:
        out["email_warning"] = (
            f"{email_err}. Invoice left as Zoho draft — ask a manager to send from Zoho Books."
        )
    return out


def sync_invoice_status_from_zoho(invoice, *, apply_local: bool = True) -> Dict[str, Any]:
    """Fetch Zoho invoice status and optionally update local Invoice."""
    if not zoho_configured():
        return {"error": "Zoho is not configured."}

    zoho_id = get_zoho_invoice_id(invoice)
    if not zoho_id:
        return {"error": "Invoice is not linked to Zoho yet. Create/send to Zoho first."}

    zoho_inv, err = fetch_zoho_invoice(zoho_id)
    if err:
        return {"error": err}

    z_status = (zoho_inv.get("status") or "").lower()
    local_status = _STATUS_MAP.get(z_status, invoice.status)
    balance = zoho_inv.get("balance")
    total = zoho_inv.get("total")
    invoice_url = zoho_inv.get("invoice_url")

    out: Dict[str, Any] = {
        "success": True,
        "zoho_invoice_id": zoho_id,
        "zoho_status": z_status,
        "local_status": local_status,
        "balance": balance,
        "total": total,
        "invoice_url": invoice_url,
        "applied": False,
    }

    if not apply_local:
        return out

    from extensions import db

    prev = (invoice.status or "").lower()
    invoice.status = local_status
    if local_status == "paid":
        if not invoice.paid_date:
            # Zoho may expose last_payment_date
            paid_raw = zoho_inv.get("last_payment_date") or zoho_inv.get("payment_expected_date")
            if paid_raw:
                try:
                    invoice.paid_date = datetime.strptime(str(paid_raw)[:10], "%Y-%m-%d").date()
                except ValueError:
                    invoice.paid_date = date.today()
            else:
                invoice.paid_date = date.today()
    db.session.commit()
    out["applied"] = True
    out["previous_local_status"] = prev
    return out


def _map_payment_mode(method: str) -> str:
    m = (method or "").strip().lower()
    mapping = {
        "cash": "cash",
        "cheque": "check",
        "check": "check",
        "bank": "banktransfer",
        "bank transfer": "banktransfer",
        "banktransfer": "banktransfer",
        "neft": "banktransfer",
        "rtgs": "banktransfer",
        "imps": "banktransfer",
        "upi": "banktransfer",
        "card": "creditcard",
        "credit card": "creditcard",
        "creditcard": "creditcard",
        "debit card": "banktransfer",
    }
    return mapping.get(m, "banktransfer")


def record_payment_in_zoho(
    invoice,
    *,
    amount: Optional[Decimal] = None,
    payment_date: Optional[date] = None,
    payment_reference: str = "",
    payment_method: str = "",
    mark_local_paid: bool = True,
) -> Dict[str, Any]:
    """
    Record a customer payment against the linked Zoho invoice.
    Optionally mark the local invoice paid.
    """
    if not zoho_configured():
        return {"error": "Zoho is not configured."}

    from extensions import db
    from models import Client

    zoho_id = get_zoho_invoice_id(invoice)
    if not zoho_id:
        # Auto-create in Zoho first (no email) so payment can be applied
        created = push_invoice_to_zoho(invoice, send_email=False)
        if created.get("error"):
            return {"error": f"Could not create Zoho invoice before payment: {created['error']}"}
        zoho_id = created.get("zoho_invoice_id")

    existing_pay = get_zoho_payment_id(invoice)
    if existing_pay and (invoice.status or "").lower() == "paid":
        return {
            "success": True,
            "already_paid": True,
            "zoho_payment_id": existing_pay,
            "zoho_invoice_id": zoho_id,
        }

    zoho_inv, err = fetch_zoho_invoice(zoho_id)
    if err:
        return {"error": err}

    customer_id = str(zoho_inv.get("customer_id") or "")
    if not customer_id:
        client = Client.query.get(invoice.client_id) if invoice.client_id else None
        if client:
            customer_id, cerr = find_or_create_contact(
                name=client.name or "",
                email=client.email or "",
                phone=getattr(client, "phone", None) or "",
            )
            if cerr:
                return {"error": cerr}
        else:
            return {"error": "Zoho invoice has no customer_id."}

    pay_amount = amount
    if pay_amount is None:
        bal = zoho_inv.get("balance")
        if bal is not None:
            pay_amount = Decimal(str(bal))
        else:
            pay_amount = Decimal(str(invoice.total_amount or 0))

    if pay_amount <= 0:
        return {"error": "Payment amount must be greater than zero (invoice may already be paid in Zoho)."}

    body = {
        "customer_id": customer_id,
        "payment_mode": _map_payment_mode(payment_method),
        "amount": float(pay_amount),
        "date": (payment_date or date.today()).isoformat(),
        "reference_number": (payment_reference or invoice.invoice_number or "")[:100],
        "description": f"Payment for Inertia invoice {invoice.invoice_number}",
        "invoices": [{"invoice_id": zoho_id, "amount_applied": float(pay_amount)}],
    }
    data, err = _request("POST", "/customerpayments", json_body=body)
    if err:
        return {"error": err}

    payment = (data or {}).get("payment") or {}
    payment_id = payment.get("payment_id")
    if payment_id:
        _set_note_marker(invoice, "zoho_payment_id", str(payment_id))

    if mark_local_paid:
        invoice.status = "paid"
        invoice.paid_date = payment_date or date.today()
        if payment_reference:
            invoice.payment_reference = payment_reference[:100]
        if payment_method:
            invoice.payment_method = payment_method[:50]

    db.session.commit()

    # Refresh status after payment
    sync = sync_invoice_status_from_zoho(invoice, apply_local=True)

    return {
        "success": True,
        "zoho_invoice_id": zoho_id,
        "zoho_payment_id": str(payment_id) if payment_id else None,
        "amount": float(pay_amount),
        "sync": {k: sync.get(k) for k in ("zoho_status", "local_status", "balance") if k in sync},
    }


def download_zoho_invoice_pdf(invoice) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """
    Return (pdf_bytes, filename, error).
    Zoho invoice PDF doubles as the official tax invoice / receipt document.
    """
    if not zoho_configured():
        return None, None, "Zoho is not configured."
    zoho_id = get_zoho_invoice_id(invoice)
    if not zoho_id:
        return None, None, "Invoice is not linked to Zoho yet."

    pdf, err = _request("GET", f"/invoices/{zoho_id}", params={"accept": "pdf"}, raw=True)
    if err:
        # Fallback path some DCs use
        pdf, err2 = _request("GET", f"/invoices/pdf/{zoho_id}", raw=True)
        if err2:
            return None, None, err
    name = f"{invoice.invoice_number or zoho_id}_zoho.pdf".replace(" ", "_")
    return pdf, name, None


def download_zoho_payment_receipt_pdf(invoice) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Download payment receipt PDF if a Zoho payment is linked."""
    if not zoho_configured():
        return None, None, "Zoho is not configured."
    pay_id = get_zoho_payment_id(invoice)
    if not pay_id:
        return None, None, "No Zoho payment recorded yet. Mark paid via Zoho first."

    pdf, err = _request("GET", f"/customerpayments/{pay_id}", params={"accept": "pdf"}, raw=True)
    if err:
        return None, None, err
    name = f"{invoice.invoice_number or pay_id}_receipt.pdf".replace(" ", "_")
    return pdf, name, None


def invoice_zoho_summary(invoice) -> Dict[str, Any]:
    """UI helper: linked ids + configured flag (no live API call)."""
    return {
        "configured": zoho_configured(),
        "zoho_invoice_id": get_zoho_invoice_id(invoice),
        "zoho_payment_id": get_zoho_payment_id(invoice),
        "linked": bool(get_zoho_invoice_id(invoice)),
    }


def list_organizations() -> Tuple[list, Optional[str]]:
    """Return (organizations, error). Does not require organization_id."""
    token, terr = _get_access_token()
    if terr:
        return [], terr
    try:
        resp = requests.get(
            f"{_api_base()}/organizations",
            headers={"Authorization": f"Zoho-oauthtoken {token}"},
            timeout=45,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            return [], data.get("message") or f"HTTP {resp.status_code}"
        return list((data or {}).get("organizations") or []), None
    except requests.RequestException as exc:
        return [], str(exc)


def oauth_authorize_url(*, state: str) -> Tuple[Optional[str], Optional[str]]:
    cfg = current_app.config
    client_id = (cfg.get("ZOHO_CLIENT_ID") or "").strip()
    redirect_uri = (cfg.get("ZOHO_REDIRECT_URI") or "").strip()
    scope = (cfg.get("ZOHO_OAUTH_SCOPE") or "ZohoBooks.fullaccess.all").strip()
    if not client_id:
        return None, "ZOHO_CLIENT_ID is not set."
    if not redirect_uri:
        return None, "ZOHO_REDIRECT_URI is not set."
    from urllib.parse import urlencode

    q = urlencode(
        {
            "scope": scope,
            "client_id": client_id,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{_accounts_base()}/oauth/v2/auth?{q}", None


def exchange_authorization_code(code: str) -> Tuple[Optional[dict], Optional[str]]:
    cfg = current_app.config
    client_id = (cfg.get("ZOHO_CLIENT_ID") or "").strip()
    client_secret = (cfg.get("ZOHO_CLIENT_SECRET") or "").strip()
    redirect_uri = (cfg.get("ZOHO_REDIRECT_URI") or "").strip()
    if not all([client_id, client_secret, redirect_uri, code]):
        return None, "Missing client credentials, redirect URI, or code."
    try:
        resp = requests.post(
            f"{_accounts_base()}/oauth/v2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=30,
        )
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400 or (
            "refresh_token" not in payload and "access_token" not in payload
        ):
            msg = payload.get("error") or payload.get("error_description") or resp.text[:200]
            return None, f"Token exchange failed: {msg}"
        return payload, None
    except requests.RequestException as exc:
        return None, f"Token exchange network error: {exc}"


def persist_refresh_token_to_env(refresh_token: str) -> Optional[str]:
    """Update ZOHO_REFRESH_TOKEN in project .env (local/dev helper)."""
    from pathlib import Path

    env_path = Path(current_app.root_path) / ".env"
    if not env_path.is_file():
        return f".env not found at {env_path}"
    rt = (refresh_token or "").strip()
    if not rt:
        return "Empty refresh token."
    lines = []
    found = False
    for line in env_path.read_text().splitlines(keepends=True):
        if line.startswith("ZOHO_REFRESH_TOKEN="):
            nl = "\n" if line.endswith("\n") else ""
            lines.append(f"ZOHO_REFRESH_TOKEN={rt}{nl}")
            found = True
        else:
            lines.append(line)
    if not found:
        if lines and not str(lines[-1]).endswith("\n"):
            lines[-1] = str(lines[-1]) + "\n"
        lines.append(f"ZOHO_REFRESH_TOKEN={rt}\n")
    env_path.write_text("".join(lines))
    current_app.config["ZOHO_REFRESH_TOKEN"] = rt
    _token_cache["access_token"] = None
    _token_cache["expires_at"] = 0.0
    return None


def diagnose_connection() -> Dict[str, Any]:
    """Lightweight status for Settings UI."""
    cfg = current_app.config
    out: Dict[str, Any] = {
        "client_id_set": bool(cfg.get("ZOHO_CLIENT_ID")),
        "client_secret_set": bool(cfg.get("ZOHO_CLIENT_SECRET")),
        "refresh_token_set": bool(cfg.get("ZOHO_REFRESH_TOKEN")),
        "organization_id": (cfg.get("ZOHO_ORGANIZATION_ID") or "").strip(),
        "api_base": _api_base(),
        "accounts_url": _accounts_base(),
        "redirect_uri": (cfg.get("ZOHO_REDIRECT_URI") or "").strip(),
        "scope": (cfg.get("ZOHO_OAUTH_SCOPE") or "").strip(),
        "configured": zoho_configured(),
        "organizations": [],
        "error": None,
    }
    if not out["refresh_token_set"]:
        out["error"] = "Refresh token not set."
        return out
    # Smoke: list one contact under configured org
    data, err = _request("GET", "/contacts", params={"per_page": 1})
    if err:
        out["error"] = err
        return out
    contacts = (data or {}).get("contacts") or []
    out["smoke_contacts"] = len(contacts)
    orgs, oerr = list_organizations()
    if not oerr:
        out["organizations"] = [
            {"id": str(o.get("organization_id")), "name": o.get("name")} for o in orgs
        ]
    if out["organization_id"] and not err:
        out["error"] = None
    return out
