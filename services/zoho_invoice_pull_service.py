"""Pull Zoho Books invoices into local Invoice rows (create + status refresh)."""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import func, inspect

from extensions import db
from models import Client, Invoice
from services.invoice_import_service import (
    _map_zoho_status,
    _normalize_person_name,
    normalize_invoice_number,
    resolve_agreement_id_for_client,
    suggest_client_match,
)
from services.zoho_books_service import (
    _set_note_marker,
    find_zoho_contact_by_email,
    find_zoho_contact_by_name,
    list_zoho_contacts,
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


def list_household_peer_clients(client: Client) -> List[Client]:
    """Other clients in the same household (ClientGroup), excluding self."""
    if not client or not getattr(client, "id", None):
        return []
    try:
        if not inspect(db.engine).has_table("client_group_member"):
            return []
        from models import ClientGroupMember

        membership = ClientGroupMember.query.filter_by(client_id=client.id).first()
        if not membership:
            return []
        peer_ids = [
            m.client_id
            for m in ClientGroupMember.query.filter_by(group_id=membership.group_id).all()
            if m.client_id != client.id
        ]
        if not peer_ids:
            return []
        return Client.query.filter(Client.id.in_(peer_ids)).all()
    except Exception:
        logger.warning("list_household_peer_clients failed for client %s", client.id, exc_info=True)
        return []


def clients_share_household(client_a_id: int, client_b_id: int) -> bool:
    if not client_a_id or not client_b_id or client_a_id == client_b_id:
        return False
    try:
        if not inspect(db.engine).has_table("client_group_member"):
            return False
        from models import ClientGroupMember

        a = ClientGroupMember.query.filter_by(client_id=client_a_id).first()
        b = ClientGroupMember.query.filter_by(client_id=client_b_id).first()
        return bool(a and b and a.group_id == b.group_id)
    except Exception:
        return False


def get_household_zoho_mapping(client: Client) -> Optional[Dict[str, Any]]:
    """
    Resolve Zoho contact via self or household peer.

    Returns dict with contact_id, owner (Client who stores the id), reason, household_name?
    Does not invent a mapping — only reads stored zoho_contact_id.
    """
    if not client:
        return None
    stored = get_stored_zoho_contact_id(client)
    if stored:
        return {
            "contact_id": stored,
            "owner": client,
            "owner_client_id": client.id,
            "owner_name": client.name,
            "reason": "stored",
            "household_name": None,
        }

    peers = list_household_peer_clients(client)
    if not peers:
        return None

    household_name = None
    try:
        from models import ClientGroupMember

        m = ClientGroupMember.query.filter_by(client_id=client.id).first()
        if m and m.group:
            household_name = m.group.name
    except Exception:
        pass

    for peer in peers:
        zid = get_stored_zoho_contact_id(peer)
        if zid:
            return {
                "contact_id": zid,
                "owner": peer,
                "owner_client_id": peer.id,
                "owner_name": peer.name,
                "reason": "household_peer",
                "household_name": household_name,
            }
    return None


def resolve_zoho_contact_for_client(client: Client) -> Dict[str, Any]:
    """
    Resolve Zoho Books contact_id for an Inertia client.

    Order: stored zoho_contact_id → household peer's stored id → email → name
    → peer email/name. Household peer maps are not copied onto this client
    (invoice matching stays on the owner); pull uses mapped_client_id.
    """
    if not zoho_configured():
        return {"ok": False, "error": "Zoho is not configured.", "contact_id": None}

    hh = get_household_zoho_mapping(client)
    if hh:
        return {
            "ok": True,
            "contact_id": hh["contact_id"],
            "match_reason": hh["reason"],
            "mapped_client_id": hh["owner_client_id"],
            "mapped_client_name": hh["owner_name"],
            "household_name": hh.get("household_name"),
            "mapping_column": client_zoho_contact_column_exists(),
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
            "mapped_client_id": client.id,
            "mapped_client_name": client.name,
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
            "mapped_client_id": client.id,
            "mapped_client_name": client.name,
            "mapping_column": client_zoho_contact_column_exists(),
        }

    # Try household peers' email/name against Zoho (still persist on the peer who matched).
    for peer in list_household_peer_clients(client):
        if peer.email:
            cid, err = find_zoho_contact_by_email(peer.email)
            if err:
                return {"ok": False, "error": err, "contact_id": None}
            if cid:
                persist_zoho_contact_id(peer, cid)
                return {
                    "ok": True,
                    "contact_id": cid,
                    "match_reason": "household_peer_email",
                    "mapped_client_id": peer.id,
                    "mapped_client_name": peer.name,
                    "mapping_column": client_zoho_contact_column_exists(),
                }
        if peer.name:
            cid, err = find_zoho_contact_by_name(peer.name)
            if err:
                return {"ok": False, "error": err, "contact_id": None}
            if cid:
                persist_zoho_contact_id(peer, cid)
                return {
                    "ok": True,
                    "contact_id": cid,
                    "match_reason": "household_peer_name",
                    "mapped_client_id": peer.id,
                    "mapped_client_name": peer.name,
                    "mapping_column": client_zoho_contact_column_exists(),
                }

    return {
        "ok": False,
        "error": (
            f"No Zoho contact matched for {client.name!r} "
            f"(email {client.email or '—'}) or household peers. "
            f"Set zoho_contact_id on the billed family member or fix email in Zoho."
        ),
        "contact_id": None,
    }


def _score_zoho_contact_name(client_name: str, zoho_name: str) -> float:
    a = _normalize_person_name(client_name or "")
    b = _normalize_person_name(zoho_name or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return max(0.92, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def suggest_zoho_contacts_for_client(
    client: Client, *, limit: int = 12
) -> Dict[str, Any]:
    """
    Rank Zoho Books contacts for this client (email hit first, then name similarity).

    Returns {ok, error?, matches:[{contact_id, name, email, score, reason}], best?}
    Does not persist a mapping — caller asks the user to confirm.
    """
    if not zoho_configured():
        return {"ok": False, "error": "Zoho is not configured.", "matches": []}

    matches: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    client_email = (client.email or "").strip().lower()
    client_name = (client.name or "").strip()

    def _add(contact: dict, score: float, reason: str) -> None:
        cid = str(contact.get("contact_id") or "").strip()
        if not cid or cid in seen:
            return
        seen.add(cid)
        email = (
            contact.get("email")
            or contact.get("email_id")
            or ((contact.get("contact_persons") or [{}])[0] or {}).get("email")
            or ""
        )
        matches.append(
            {
                "contact_id": cid,
                "name": (contact.get("contact_name") or "").strip() or "—",
                "email": (email or "").strip() or "—",
                "score": round(float(score), 3),
                "reason": reason,
            }
        )

    # 0) Household peer already linked to Zoho — prefer that shared billing contact.
    hh = get_household_zoho_mapping(client)
    if hh and hh.get("reason") == "household_peer":
        _add(
            {
                "contact_id": hh["contact_id"],
                "contact_name": f"{hh.get('owner_name') or 'Household'} (Zoho)",
                "email": "",
            },
            1.0,
            "household_peer",
        )

    # 1) Exact email
    if client_email:
        data_cid, err = find_zoho_contact_by_email(client.email or "")
        if err:
            return {"ok": False, "error": err, "matches": []}
        if data_cid:
            # Re-fetch name via name search / list filter is heavy; stub from email hit
            _add(
                {
                    "contact_id": data_cid,
                    "contact_name": client_name or data_cid,
                    "email": client.email,
                },
                1.0,
                "email",
            )

    # 2) Name contains searches (full name + first token) + broader list for scoring
    tokens = [t for t in _normalize_person_name(client_name).split() if len(t) > 1]
    search_needles: List[Optional[str]] = []
    if client_name:
        search_needles.append(client_name)
    if tokens:
        search_needles.append(tokens[0].title())
    search_needles.append(None)  # unfiltered pages as fallback pool

    pool: List[dict] = []
    pool_ids: Set[str] = set()
    for needle in search_needles:
        max_pages = 3 if needle else 5
        batch, err = list_zoho_contacts(
            contact_name_contains=needle, per_page=200, max_pages=max_pages
        )
        if err and not pool:
            return {"ok": False, "error": err, "matches": []}
        if err:
            break
        for c in batch:
            cid = str(c.get("contact_id") or "").strip()
            if cid and cid not in pool_ids:
                pool_ids.add(cid)
                pool.append(c)
        if len(pool) >= 400:
            break

    for c in pool:
        zname = (c.get("contact_name") or "").strip()
        zemail = (
            (c.get("email") or c.get("email_id") or "")
            or ((c.get("contact_persons") or [{}])[0] or {}).get("email")
            or ""
        )
        zemail_l = str(zemail).strip().lower()
        if client_email and zemail_l == client_email:
            _add(c, 1.0, "email")
            continue
        score = _score_zoho_contact_name(client_name, zname)
        if score >= 0.45:
            reason = "exact_name" if score >= 0.999 else "name"
            _add(c, score, reason)

    matches.sort(key=lambda m: (-m["score"], m["name"].lower()))
    matches = matches[: max(1, min(int(limit or 12), 25))]
    best = matches[0] if matches else None
    return {
        "ok": True,
        "matches": matches,
        "best": best,
        "client_name": client_name,
        "client_email": client.email or "",
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
    """Pull all Zoho invoices for one client (resolve contact, then filter by customer_id).

    When Zoho is linked on a household peer (e.g. Ajith), invoices attach to that
    peer — family billing stays under one name.
    """
    resolved = resolve_zoho_contact_for_client(client)
    if not resolved.get("ok"):
        return {
            "ok": False,
            "error": resolved.get("error") or "Could not resolve Zoho contact.",
            "resolve": resolved,
        }
    owner_id = int(resolved.get("mapped_client_id") or client.id)
    peer_ids = {client.id, owner_id}
    for p in list_household_peer_clients(client):
        peer_ids.add(p.id)
    result = pull_zoho_invoices(
        date_start=date_start,
        date_end=date_end,
        user_id=user_id,
        accessible_client_ids=peer_ids,
        update_existing=update_existing,
        force_client_id=owner_id,
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


def _zoho_contact_email(contact: dict) -> str:
    email = (
        contact.get("email")
        or contact.get("email_id")
        or ((contact.get("contact_persons") or [{}])[0] or {}).get("email")
        or ""
    )
    return str(email).strip()


def build_zoho_client_mapping_report(
    *,
    accessible_client_ids: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    """
    Compare Inertia clients ↔ Zoho contacts.

    Returns mapped links, Inertia clients with no Zoho map, Zoho contacts with no
    Inertia client, and name mismatches on stored links.
    """
    if not zoho_configured():
        return {"ok": False, "error": "Zoho is not configured."}
    if not client_zoho_contact_column_exists():
        return {
            "ok": False,
            "error": "Run migrations/add_client_zoho_contact_id.py first.",
        }

    q = Client.query.order_by(Client.name)
    if accessible_client_ids is not None:
        q = q.filter(Client.id.in_(accessible_client_ids))
    clients = q.all()

    contacts, err = list_zoho_contacts(per_page=200, max_pages=15)
    if err:
        return {"ok": False, "error": err}

    by_zoho_id: Dict[str, dict] = {}
    for c in contacts:
        cid = str(c.get("contact_id") or "").strip()
        if cid:
            by_zoho_id[cid] = c

    mapped: List[Dict[str, Any]] = []
    mismatched: List[Dict[str, Any]] = []
    unmapped_clients: List[Dict[str, Any]] = []
    household_linked: List[Dict[str, Any]] = []
    used_zoho_ids: Set[str] = set()

    for cl in clients:
        zid = get_stored_zoho_contact_id(cl)
        if not zid:
            hh = get_household_zoho_mapping(cl)
            if hh and hh.get("reason") == "household_peer":
                zc = by_zoho_id.get(hh["contact_id"])
                zname = (zc.get("contact_name") if zc else None) or "—"
                household_linked.append(
                    {
                        "client_id": cl.id,
                        "client_name": cl.name,
                        "email": cl.email or "—",
                        "zoho_contact_id": hh["contact_id"],
                        "zoho_name": zname,
                        "via_client_id": hh["owner_client_id"],
                        "via_client_name": hh["owner_name"],
                        "household_name": hh.get("household_name") or "—",
                    }
                )
                used_zoho_ids.add(hh["contact_id"])
                continue

            sug_id = None
            sug_name = None
            sug_score = 0.0
            sug_reason = "none"
            cl_email = (cl.email or "").strip().lower()
            for zc in contacts:
                zemail = _zoho_contact_email(zc).lower()
                zname = (zc.get("contact_name") or "").strip()
                if cl_email and zemail and cl_email == zemail:
                    sug_id = str(zc.get("contact_id") or "")
                    sug_name = zname
                    sug_score = 1.0
                    sug_reason = "email"
                    break
                score = _score_zoho_contact_name(cl.name or "", zname)
                if score > sug_score:
                    sug_score = score
                    sug_id = str(zc.get("contact_id") or "")
                    sug_name = zname
                    sug_reason = "name"
            if sug_score < 0.72:
                sug_id = None
                sug_name = None
                sug_reason = "none"
                sug_score = 0.0
            unmapped_clients.append(
                {
                    "client_id": cl.id,
                    "client_name": cl.name,
                    "email": cl.email or "—",
                    "suggested_zoho_id": sug_id,
                    "suggested_zoho_name": sug_name,
                    "suggested_score": round(sug_score, 3) if sug_id else None,
                    "suggested_reason": sug_reason,
                }
            )
            continue

        used_zoho_ids.add(zid)
        zc = by_zoho_id.get(zid)
        zname = (zc.get("contact_name") if zc else None) or "—"
        zemail = _zoho_contact_email(zc) if zc else "—"
        score = _score_zoho_contact_name(cl.name or "", zname if zname != "—" else "")
        row = {
            "client_id": cl.id,
            "client_name": cl.name,
            "email": cl.email or "—",
            "zoho_contact_id": zid,
            "zoho_name": zname,
            "zoho_email": zemail or "—",
            "name_score": round(score, 3) if zc else None,
            "zoho_found": bool(zc),
        }
        mapped.append(row)
        # Mismatch: linked id not in Zoho, or names diverge badly
        if not zc or (zc and score < 0.72):
            mismatched.append(
                {
                    **row,
                    "issue": (
                        "Zoho contact ID not found in org"
                        if not zc
                        else f"Name mismatch ({int(score * 100)}% similar)"
                    ),
                }
            )

    unmapped_zoho: List[Dict[str, Any]] = []
    for zid, zc in by_zoho_id.items():
        if zid in used_zoho_ids:
            continue
        zname = (zc.get("contact_name") or "").strip() or "—"
        zemail = _zoho_contact_email(zc)
        sid, sname, reason, cands = suggest_client_match(
            zname, zemail or None, accessible_client_ids
        )
        unmapped_zoho.append(
            {
                "zoho_contact_id": zid,
                "zoho_name": zname,
                "zoho_email": zemail or "—",
                "suggested_client_id": sid,
                "suggested_client_name": sname,
                "match_reason": reason,
                "top_score": (cands[0]["score"] if cands else None),
            }
        )

    unmapped_zoho.sort(key=lambda r: (r["zoho_name"] or "").lower())
    mapped.sort(key=lambda r: (r["client_name"] or "").lower())
    unmapped_clients.sort(key=lambda r: (r["client_name"] or "").lower())
    mismatched.sort(key=lambda r: (r["client_name"] or "").lower())
    household_linked.sort(key=lambda r: (r["client_name"] or "").lower())

    contact_choices = []
    for zid, zc in sorted(
        by_zoho_id.items(),
        key=lambda kv: ((kv[1].get("contact_name") or "").strip().lower(), kv[0]),
    ):
        contact_choices.append(
            {
                "zoho_contact_id": zid,
                "zoho_name": (zc.get("contact_name") or "").strip() or "—",
                "zoho_email": _zoho_contact_email(zc) or "—",
            }
        )

    inertia_choices = [
        {"client_id": cl.id, "client_name": cl.name or f"#{cl.id}"}
        for cl in clients
    ]

    return {
        "ok": True,
        "zoho_contacts_fetched": len(contacts),
        "mapped": mapped,
        "mismatched": mismatched,
        "household_linked": household_linked,
        "unmapped_clients": unmapped_clients,
        "unmapped_zoho": unmapped_zoho,
        "contact_choices": contact_choices,
        "inertia_choices": inertia_choices,
        "counts": {
            "mapped": len(mapped),
            "mismatched": len(mismatched),
            "household_linked": len(household_linked),
            "unmapped_clients": len(unmapped_clients),
            "unmapped_zoho": len(unmapped_zoho),
        },
    }
