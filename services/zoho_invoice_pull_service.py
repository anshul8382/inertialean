"""Pull Zoho Books invoices into local Invoice rows (create + status refresh)."""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, inspect

from extensions import db
from models import Client, Invoice
from services.invoice_import_service import (
    _map_zoho_status,
    normalize_invoice_number,
    resolve_agreement_id_for_client,
    suggest_client_match,
)
from services.zoho_books_service import (
    _set_note_marker,
    find_zoho_contact_by_email,
    find_zoho_contact_by_name,
    list_zoho_invoices,
    zoho_configured,
)

logger = logging.getLogger(__name__)

_zoho_contact_col_checked: Optional[bool] = None


def client_zoho_contact_column_exists() -> bool:
    """True when client.zoho_contact_id is present (migration applied)."""
    global _zoho_contact_col_checked
    if _zoho_contact_col_checked is not None:
        return _zoho_contact_col_checked
    try:
        cols = {c["name"] for c in inspect(db.engine).get_columns("client")}
        _zoho_contact_col_checked = "zoho_contact_id" in cols
    except Exception:
        _zoho_contact_col_checked = False
    return _zoho_contact_col_checked


def get_stored_zoho_contact_id(client: Client) -> Optional[str]:
    if not client_zoho_contact_column_exists():
        return None
    return (getattr(client, "zoho_contact_id", None) or "").strip() or None


def persist_zoho_contact_id(client: Client, zoho_contact_id: str) -> bool:
    """Save durable Client ↔ Zoho contact map when column exists. Returns True if saved."""
    zid = (zoho_contact_id or "").strip()
    if not zid or not client_zoho_contact_column_exists():
        return False
    current = (getattr(client, "zoho_contact_id", None) or "").strip()
    if current == zid:
        return False
    client.zoho_contact_id = zid[:64]
    try:
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        logger.exception("Failed to persist zoho_contact_id for client %s", client.id)
        return False


def resolve_zoho_contact_for_client(client: Client) -> Dict[str, Any]:
    """
    Resolve Zoho Books contact_id for an Inertia client.

    Order: stored zoho_contact_id → email lookup → name lookup.
    """
    if not zoho_configured():
        return {"ok": False, "error": "Zoho is not configured.", "contact_id": None}

    stored = get_stored_zoho_contact_id(client)
    if stored:
        return {
            "ok": True,
            "contact_id": stored,
            "match_reason": "stored",
            "mapping_column": True,
        }

    cid, err = find_zoho_contact_by_email(client.email or "")
    if err:
        return {"ok": False, "error": err, "contact_id": None}
    if cid:
        persist_zoho_contact_id(client, cid)
        return {
            "ok": True,
            "contact_id": cid,
            "match_reason": "email",
            "mapping_column": client_zoho_contact_column_exists(),
        }

    cid, err = find_zoho_contact_by_name(client.name or "")
    if err:
        return {"ok": False, "error": err, "contact_id": None}
    if cid:
        persist_zoho_contact_id(client, cid)
        return {
            "ok": True,
            "contact_id": cid,
            "match_reason": "name",
            "mapping_column": client_zoho_contact_column_exists(),
        }

    return {
        "ok": False,
        "error": (
            f"No Zoho contact matched for {client.name!r} "
            f"(email {client.email or '—'}). Set zoho_contact_id manually or fix email in Zoho."
        ),
        "contact_id": None,
    }


def _parse_date(val: Any) -> Optional[date]:
    if val is None or val == "":
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    s = str(val).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _dec(val: Any) -> Optional[Decimal]:
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _find_by_zoho_id(zoho_id: str) -> Optional[Invoice]:
    zid = (zoho_id or "").strip()
    if not zid:
        return None
    marker = f"[zoho_invoice_id={zid}]"
    return Invoice.query.filter(Invoice.notes.contains(marker)).first()


def _amounts_from_zoho(row: dict) -> Dict[str, Decimal]:
    total = _dec(row.get("total")) or Decimal("0")
    sub = _dec(row.get("sub_total"))
    if sub is None:
        sub = _dec(row.get("subtotal"))
    tax = _dec(row.get("tax_total"))
    if tax is None:
        tax = _dec(row.get("tax_amount"))
    if sub is None:
        if tax is not None and total >= tax:
            sub = total - tax
        else:
            sub = total
    if tax is None:
        tax = (total - sub) if total > sub else Decimal("0")
    net = sub
    tax_rate = Decimal("0")
    if net > 0 and tax > 0:
        try:
            tax_rate = (tax / net).quantize(Decimal("0.0001"))
        except Exception:
            tax_rate = Decimal("0")
    return {
        "total_amount": total,
        "net_amount": net,
        "tax_amount": tax,
        "tax_rate": tax_rate,
    }


def _local_status_from_zoho_row(row: dict) -> str:
    balance = _dec(row.get("balance"))
    return _map_zoho_status(str(row.get("status") or ""), balance)


def _paid_date_from_zoho_row(row: dict, status: str, inv_date: date) -> Optional[date]:
    if status != "paid":
        return None
    return (
        _parse_date(row.get("last_payment_date"))
        or _parse_date(row.get("payment_expected_date"))
        or inv_date
    )


def _match_client_for_row(
    row: dict,
    *,
    force_client_id: Optional[int],
    accessible_client_ids: Optional[Set[int]],
) -> Tuple[Optional[int], Optional[str], str]:
    if force_client_id:
        cl = Client.query.get(force_client_id)
        return force_client_id, (cl.name if cl else None), "forced_client"

    # Prefer durable map: Zoho customer_id → client.zoho_contact_id
    z_cust = str(row.get("customer_id") or "").strip()
    if z_cust and client_zoho_contact_column_exists():
        mapped = Client.query.filter_by(zoho_contact_id=z_cust).first()
        if mapped and (
            accessible_client_ids is None or mapped.id in accessible_client_ids
        ):
            return mapped.id, mapped.name, "zoho_contact_id"

    customer_name = (row.get("customer_name") or "").strip() or None
    email = (row.get("email") or row.get("customer_email") or "").strip() or None
    sid, sname, reason, _cands = suggest_client_match(
        customer_name, email, accessible_client_ids
    )
    return sid, sname, reason


def pull_zoho_invoices(
    *,
    date_start: Optional[date] = None,
    date_end: Optional[date] = None,
    user_id: int,
    accessible_client_ids: Optional[Set[int]] = None,
    update_existing: bool = True,
    create_only: bool = False,
    force_client_id: Optional[int] = None,
    zoho_customer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch Zoho invoices and upsert into Inertia.

    - force_client_id + zoho_customer_id: per-client pull (all invoices for that Zoho customer).
    - create_only: skip updates to existing local invoices (sync missing only).
    - Matched via zoho_contact_id map, else email / name fuzzy match.
    """
    if not zoho_configured():
        return {"ok": False, "error": "Zoho is not configured."}
    if date_start and date_end and date_end < date_start:
        return {"ok": False, "error": "End date must be on or after start date."}
    if create_only:
        update_existing = False

    rows, err = list_zoho_invoices(
        date_start=date_start,
        date_end=date_end,
        customer_id=zoho_customer_id,
    )
    if err:
        return {"ok": False, "error": err}

    created = 0
    updated = 0
    skipped_existing = 0
    skipped_void = 0
    unmatched: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    mappings_saved = 0
    seen_zoho: Set[str] = set()

    for row in rows:
        zoho_id = str(row.get("invoice_id") or "").strip()
        inv_no = normalize_invoice_number(row.get("invoice_number"))
        if not inv_no:
            errors.append({"zoho_invoice_id": zoho_id, "message": "Missing invoice number"})
            continue
        if zoho_id and zoho_id in seen_zoho:
            continue
        if zoho_id:
            seen_zoho.add(zoho_id)

        z_status_raw = (row.get("status") or "").strip().lower()
        if z_status_raw in ("void", "draft"):
            skipped_void += 1
            continue

        inv_date = _parse_date(row.get("date")) or _parse_date(row.get("created_date"))
        if not inv_date:
            errors.append(
                {
                    "invoice_number": inv_no,
                    "zoho_invoice_id": zoho_id,
                    "message": "Missing invoice date",
                }
            )
            continue

        due_date = _parse_date(row.get("due_date")) or inv_date
        amounts = _amounts_from_zoho(row)
        local_status = _local_status_from_zoho_row(row)
        paid_date = _paid_date_from_zoho_row(row, local_status, inv_date)
        customer_name = (row.get("customer_name") or "").strip() or None
        email = (row.get("email") or row.get("customer_email") or "").strip() or None
        z_cust = str(row.get("customer_id") or "").strip()

        existing = None
        if zoho_id:
            existing = _find_by_zoho_id(zoho_id)
        if not existing:
            existing = Invoice.query.filter(
                func.lower(Invoice.invoice_number) == inv_no.lower()
            ).first()

        if existing:
            if not update_existing:
                skipped_existing += 1
                continue
            try:
                existing.status = local_status
                existing.total_amount = amounts["total_amount"]
                existing.net_amount = amounts["net_amount"]
                existing.tax_amount = amounts["tax_amount"]
                existing.tax_rate = amounts["tax_rate"]
                existing.due_date = due_date
                if local_status == "paid":
                    existing.paid_date = paid_date or existing.paid_date or inv_date
                elif local_status != "paid":
                    existing.paid_date = None
                if zoho_id:
                    _set_note_marker(existing, "zoho_invoice_id", zoho_id)
                db.session.commit()
                updated += 1
            except Exception as exc:
                db.session.rollback()
                logger.exception("Zoho pull update failed for %s", inv_no)
                errors.append(
                    {
                        "invoice_number": inv_no,
                        "zoho_invoice_id": zoho_id,
                        "message": str(exc),
                    }
                )
            continue

        sid, sname, reason = _match_client_for_row(
            row,
            force_client_id=force_client_id,
            accessible_client_ids=accessible_client_ids,
        )
        if not sid:
            unmatched.append(
                {
                    "invoice_number": inv_no,
                    "zoho_invoice_id": zoho_id,
                    "customer_name": customer_name or "—",
                    "email": email or "—",
                    "zoho_customer_id": z_cust or "—",
                    "total": str(amounts["total_amount"]),
                    "date": inv_date.isoformat(),
                }
            )
            continue

        if z_cust:
            cl = Client.query.get(sid)
            if cl and persist_zoho_contact_id(cl, z_cust):
                mappings_saved += 1

        agreement_id = resolve_agreement_id_for_client(sid)
        notes = f"Pulled from Zoho Books ({inv_date.isoformat()})"
        invoice = Invoice(
            agreement_id=agreement_id,
            client_id=sid,
            invoice_number=inv_no[:50],
            invoice_date=inv_date,
            billing_period_start=inv_date,
            billing_period_end=inv_date,
            total_amount=amounts["total_amount"],
            tax_rate=amounts["tax_rate"],
            tax_amount=amounts["tax_amount"],
            net_amount=amounts["net_amount"],
            status=local_status,
            due_date=due_date,
            paid_date=paid_date if local_status == "paid" else None,
            notes=notes,
            created_by=user_id,
        )
        if zoho_id:
            _set_note_marker(invoice, "zoho_invoice_id", zoho_id)
        try:
            db.session.add(invoice)
            db.session.commit()
            created += 1
        except Exception as exc:
            db.session.rollback()
            logger.exception("Zoho pull create failed for %s", inv_no)
            errors.append(
                {
                    "invoice_number": inv_no,
                    "zoho_invoice_id": zoho_id,
                    "message": str(exc),
                    "matched_client": sname,
                    "match_reason": reason,
                }
            )

    return {
        "ok": True,
        "fetched": len(rows),
        "created": created,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_void_or_draft": skipped_void,
        "unmatched": unmatched,
        "errors": errors,
        "mappings_saved": mappings_saved,
        "date_start": date_start.isoformat() if date_start else None,
        "date_end": date_end.isoformat() if date_end else None,
        "force_client_id": force_client_id,
        "zoho_customer_id": zoho_customer_id,
        "create_only": create_only,
    }


def pull_zoho_invoices_for_client(
    client: Client,
    *,
    user_id: int,
    date_start: Optional[date] = None,
    date_end: Optional[date] = None,
    update_existing: bool = True,
) -> Dict[str, Any]:
    """Pull all Zoho invoices for one client (resolve contact, then filter by customer_id)."""
    resolved = resolve_zoho_contact_for_client(client)
    if not resolved.get("ok"):
        return {
            "ok": False,
            "error": resolved.get("error") or "Could not resolve Zoho contact.",
            "resolve": resolved,
        }
    result = pull_zoho_invoices(
        date_start=date_start,
        date_end=date_end,
        user_id=user_id,
        accessible_client_ids={client.id},
        update_existing=update_existing,
        force_client_id=client.id,
        zoho_customer_id=resolved["contact_id"],
    )
    result["resolve"] = resolved
    return result


def sync_missing_zoho_invoices(
    *,
    user_id: int,
    accessible_client_ids: Optional[Set[int]] = None,
    date_start: Optional[date] = None,
    date_end: Optional[date] = None,
) -> Dict[str, Any]:
    """Org-wide: create local invoices that exist in Zoho but not yet in Inertia."""
    return pull_zoho_invoices(
        date_start=date_start,
        date_end=date_end,
        user_id=user_id,
        accessible_client_ids=accessible_client_ids,
        create_only=True,
        update_existing=False,
    )
