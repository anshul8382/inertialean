"""
Import historical invoices from Zoho Books (and similar) CSV/TSV/Excel exports.
Preview step: show file customer name vs suggested DB client; user confirms mappings.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Agreement, Client, Invoice, Lead

ZOHO_HEADER_MAP: Dict[str, str] = {
    "invoice date": "invoice_date",
    "issued date": "issued_date",
    "invoice number": "invoice_number",
    "invoice id": "external_invoice_id",
    "invoice status": "invoice_status",
    "customer id": "external_customer_id",
    "customer name": "customer_name",
    "company name": "company_name",
    "due date": "due_date",
    "subtotal": "subtotal",
    "total": "total",
    "balance": "balance",
    "notes": "notes",
    "item desc": "item_desc",
    "item name": "item_name",
    "primary contact emailid": "primary_email",
    "primary contact email": "primary_email",
    "last payment date": "last_payment_date",
    "expected payment date": "expected_payment_date",
    "item tax1 amount": "item_tax1_amount",
    "is inclusive tax": "is_inclusive_tax",
}


def preview_storage_dir() -> str:
    base = current_app.instance_path if current_app else "."
    d = os.path.join(base, "tmp", "invoice_import")
    os.makedirs(d, mode=0o755, exist_ok=True)
    return d


def _norm_header(h: str) -> str:
    if h is None:
        return ""
    return str(h).replace("\ufeff", "").strip().lower()


def _build_column_map(columns: List[str]) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for c in columns:
        internal = ZOHO_HEADER_MAP.get(_norm_header(c))
        if internal:
            m[c] = internal
    return m


def normalize_invoice_number(val: Any) -> str:
    """Strip whitespace for duplicate checks and storage."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip()


def _raw_export_from_series(series: pd.Series) -> Dict[str, str]:
    """Original column → string value for re-download / re-upload."""
    out: Dict[str, str] = {}
    for col in series.index:
        v = series[col]
        if pd.isna(v):
            out[str(col)] = ""
        elif isinstance(v, str):
            out[str(col)] = v.strip()
        else:
            out[str(col)] = str(v)
    return out


def existing_stripped_invoice_numbers_in_db(nums: List[str]) -> Set[str]:
    """
    Lowercase trimmed keys for invoice numbers already in DB (trim + case-insensitive).
    Matches MySQL unique constraints that use case-insensitive collation.
    """
    if not nums:
        return set()
    lower_unique = list({str(n).strip().lower() for n in nums if n and str(n).strip()})
    if not lower_unique:
        return set()
    out: Set[str] = set()
    chunk_size = 400
    for i in range(0, len(lower_unique), chunk_size):
        part = lower_unique[i : i + chunk_size]
        q = db.session.query(Invoice.invoice_number).filter(
            func.lower(func.trim(Invoice.invoice_number)).in_(part)
        )
        for (invn,) in q:
            out.add(str(invn).strip().lower())
    return out


def invoice_number_exists_in_db(inv_no: str) -> bool:
    """Whether an invoice already uses this number (trim, case-insensitive)."""
    key = (inv_no or "").strip().lower()
    if not key:
        return False
    return (
        Invoice.query.filter(func.lower(func.trim(Invoice.invoice_number)) == key).first()
        is not None
    )


def _exception_text_chain(exc: BaseException) -> str:
    """SQLAlchemy often wraps pymysql; inner text may not appear in str(exc)."""
    chunks: List[str] = []

    def walk(e: Optional[BaseException], depth: int) -> None:
        if e is None or depth > 10:
            return
        chunks.append(str(e))
        o = getattr(e, "orig", None)
        if o is not None:
            chunks.append(str(o))
        if e.__cause__ is not None:
            walk(e.__cause__, depth + 1)
        elif e.__context__ is not None:
            walk(e.__context__, depth + 1)

    walk(exc, 0)
    return " ".join(chunks).lower()


def _is_invoice_number_duplicate_error(exc: BaseException) -> bool:
    """Detect MySQL 1062 / unique on invoice_number whether wrapped or not."""
    if isinstance(exc, IntegrityError):
        return True
    blob = _exception_text_chain(exc)
    if "duplicate entry" in blob and "invoice" in blob:
        return True
    if "1062" in blob and "duplicate" in blob:
        return True
    if "unique constraint" in blob and "invoice" in blob:
        return True
    return False


def build_skipped_rows_for_csv(preview_rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Rows with status skip_duplicate or error, plus Import_* columns for the CSV."""
    result: List[Dict[str, str]] = []
    for pr in preview_rows:
        st = pr.get("status")
        if st not in ("skip_duplicate", "error"):
            continue
        raw = dict(pr.get("raw_export") or {})
        msg = pr.get("message") or st
        raw["Import_Status"] = "skipped_duplicate" if st == "skip_duplicate" else "validation_error"
        raw["Import_Note"] = str(msg)
        result.append({str(k): "" if v is None else str(v) for k, v in raw.items()})
    return result


def failure_commit_rows_for_csv(errors: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Turn commit errors (with raw_export) into CSV rows."""
    result: List[Dict[str, str]] = []
    for e in errors:
        raw = dict(e.get("raw_export") or {})
        if not raw:
            raw["Source_Row"] = str(e.get("row") or "")
        raw["Import_Status"] = "commit_failed"
        raw["Import_Note"] = str(e.get("message") or "")
        result.append({str(k): "" if v is None else str(v) for k, v in raw.items()})
    return result


def invoice_export_rows_to_csv_bytes(rows: List[Dict[str, str]]) -> bytes:
    if not rows:
        return b""
    fieldnames: List[str] = []
    seen: Set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    for special in ("Import_Status", "Import_Note"):
        if special in fieldnames:
            fieldnames.remove(special)
            fieldnames.append(special)
    sio = io.StringIO()
    w = csv.DictWriter(sio, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") or "" for k in fieldnames})
    return sio.getvalue().encode("utf-8-sig")


FAIL_EXPORT_PREFIX = "import_fail_"


def save_invoice_import_failure_export(rows: List[Dict[str, str]]) -> Optional[str]:
    if not rows:
        return None
    fid = uuid.uuid4().hex
    path = os.path.join(preview_storage_dir(), f"{FAIL_EXPORT_PREFIX}{fid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return fid


def consume_invoice_import_failure_export(fid: str) -> Optional[List[Dict[str, str]]]:
    path = os.path.join(preview_storage_dir(), f"{FAIL_EXPORT_PREFIX}{fid}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _parse_date(val: Any) -> Optional[date]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    if s.isdigit() and len(s) <= 2:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    try:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def _parse_decimal(val: Any) -> Optional[Decimal]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return Decimal(str(val))
        except InvalidOperation:
            return None
    s = str(val).strip().replace(",", "")
    if not s or s.lower() in ("nan", "none"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _normalize_person_name(name: str) -> str:
    n = (name or "").strip().lower()
    n = re.sub(r"^(mr\.?|mrs\.?|ms\.?|dr\.?)\s+", "", n)
    return re.sub(r"\s+", " ", n)


def _map_zoho_status(status_raw: str, balance: Optional[Decimal]) -> str:
    s = (status_raw or "").strip().lower()
    if s in ("paid", "closed"):
        if balance is not None and balance > 0:
            return "sent"
        return "paid"
    if s in ("sent", "open", "partially_paid", "partially paid"):
        return "sent"
    if s in ("overdue",):
        return "overdue"
    if s in ("draft", "void"):
        return "draft"
    if s in ("cancelled", "canceled"):
        return "cancelled"
    if not s:
        return "draft"
    return "sent"


def _load_accessible_clients(accessible_client_ids: Optional[Set[int]]) -> List[Client]:
    q = Client.query
    if accessible_client_ids is not None:
        q = q.filter(Client.id.in_(accessible_client_ids))
    return q.order_by(Client.name).all()


def suggest_client_match(
    customer_name: Optional[str],
    primary_email: Optional[str],
    accessible_client_ids: Optional[Set[int]],
) -> Tuple[Optional[int], Optional[str], str, List[Dict[str, Any]]]:
    """
    Returns (suggested_client_id, suggested_client_name, match_reason, ranked_candidates).
    ranked_candidates: [{id, name, score}] top 8 by name similarity.
    match_reason: 'email' | 'exact_name' | 'fuzzy' | 'none'
    """
    clients = _load_accessible_clients(accessible_client_ids)
    ranked: List[Dict[str, Any]] = []

    if primary_email:
        em = str(primary_email).strip().lower()
        if em:
            for c in clients:
                if (c.email or "").strip().lower() == em:
                    return c.id, c.name, "email", [{"id": c.id, "name": c.name, "score": 1.0}]

    norm_file = _normalize_person_name(str(customer_name or ""))
    if norm_file:
        for c in clients:
            cn = _normalize_person_name(c.name or "")
            if cn == norm_file:
                return c.id, c.name, "exact_name", [{"id": c.id, "name": c.name, "score": 1.0}]
        for c in clients:
            cn = _normalize_person_name(c.name or "")
            if norm_file in cn or cn in norm_file:
                return c.id, c.name, "fuzzy", [{"id": c.id, "name": c.name, "score": 0.95}]

    if norm_file:
        for c in clients:
            cn = _normalize_person_name(c.name or "")
            ratio = SequenceMatcher(None, norm_file, cn).ratio() if cn else 0.0
            ranked.append({"id": c.id, "name": c.name, "score": round(ratio, 3)})
        ranked.sort(key=lambda x: -x["score"])
        ranked = ranked[:8]
        best = ranked[0] if ranked else None
        if best and best["score"] >= 0.72:
            return best["id"], best["name"], "fuzzy", ranked

    return None, None, "none", ranked[:8]


def resolve_agreement_id_for_client(client_id: int) -> Optional[int]:
    lead = (
        Lead.query.filter_by(client_id=client_id).order_by(Lead.id.desc()).first()
    )
    if not lead:
        return None
    ag = (
        Agreement.query.filter(
            Agreement.lead_id == lead.id,
            func.lower(Agreement.status).in_(["signed", "completed", "active"]),
        )
        .order_by(Agreement.id.desc())
        .first()
    )
    if ag:
        return ag.id
    ag = (
        Agreement.query.filter(Agreement.lead_id == lead.id)
        .filter(func.lower(Agreement.status) != "draft")
        .order_by(Agreement.id.desc())
        .first()
    )
    return ag.id if ag else None


def _read_dataframe(file_content: bytes, filename: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    low = (filename or "").lower()
    buf = io.BytesIO(file_content)

    if low.endswith(".xlsx"):
        try:
            df = pd.read_excel(buf, engine="openpyxl", dtype=str)
        except Exception as e:
            return None, f"Could not parse Excel (.xlsx): {e}"
    elif low.endswith(".xls"):
        try:
            df = pd.read_excel(buf, engine="xlrd", dtype=str)
        except Exception as e:
            return None, f"Could not parse Excel (.xls): {e}"
    else:
        try:
            df = pd.read_csv(buf, sep=None, engine="python", encoding="utf-8-sig", dtype=str)
        except Exception:
            buf.seek(0)
            try:
                df = pd.read_csv(buf, sep="\t", encoding="utf-8-sig", dtype=str)
            except Exception as e2:
                return None, f"Could not parse file: {e2}"

    df.columns = [str(c).strip() for c in df.columns]
    return df, None


def build_invoice_import_preview(
    file_content: bytes,
    filename: str,
    accessible_client_ids: Optional[Set[int]],
) -> Dict[str, Any]:
    """
    Parse file and build preview rows with file vs suggested DB client.
    Returns { ok, error?, preview_id?, row_count?, skipped_duplicate?, skipped_empty? }
    On success preview_id is written to disk; load with load_invoice_import_preview.
    """
    df, err = _read_dataframe(file_content, filename)
    if err:
        return {"ok": False, "error": err}

    col_map = _build_column_map(list(df.columns))
    if "invoice_number" not in col_map.values():
        return {
            "ok": False,
            "error": "Missing required column 'Invoice Number' (check export headers).",
        }

    preview_rows: List[Dict[str, Any]] = []
    skipped_duplicate = 0
    skipped_empty = 0
    preview_index = 0

    candidates: List[Tuple[int, Dict[str, Any], Dict[str, str], str]] = []
    for idx, series in df.iterrows():
        source_row_num = int(idx) + 2
        raw_export = _raw_export_from_series(series)
        row: Dict[str, Any] = {}
        for orig, internal in col_map.items():
            if orig in series.index:
                v = series[orig]
                row[internal] = None if pd.isna(v) else v

        inv_no = normalize_invoice_number(row.get("invoice_number"))
        if not inv_no:
            skipped_empty += 1
            continue
        candidates.append((source_row_num, row, raw_export, inv_no))

    unique_nums = list({c[3] for c in candidates})
    existing_keys_db = existing_stripped_invoice_numbers_in_db(unique_nums)
    # Only invoice numbers we've already accepted as "ready" (avoids blocking a valid row after a bad one)
    seen_valid_lower: Set[str] = set()

    for source_row_num, row, raw_export, inv_no in candidates:
        inv_lower = inv_no.lower()
        if inv_lower in existing_keys_db:
            skipped_duplicate += 1
            preview_rows.append(
                {
                    "preview_index": -1,
                    "source_row_num": source_row_num,
                    "invoice_number": inv_no,
                    "file_customer_name": str(row.get("customer_name") or "").strip(),
                    "file_email": str(row.get("primary_email") or "").strip(),
                    "status": "skip_duplicate",
                    "message": "Already in database",
                    "payload": None,
                    "suggested_client_id": None,
                    "suggested_client_name": None,
                    "match_reason": "none",
                    "candidates": [],
                    "raw_export": raw_export,
                }
            )
            continue

        inv_date = _parse_date(row.get("invoice_date")) or _parse_date(row.get("issued_date"))
        total = _parse_decimal(row.get("total"))
        if not inv_date or total is None:
            preview_rows.append(
                {
                    "preview_index": -1,
                    "source_row_num": source_row_num,
                    "invoice_number": inv_no,
                    "file_customer_name": str(row.get("customer_name") or "").strip(),
                    "file_email": str(row.get("primary_email") or "").strip(),
                    "status": "error",
                    "message": "Missing invoice date or total",
                    "payload": None,
                    "suggested_client_id": None,
                    "suggested_client_name": None,
                    "match_reason": "none",
                    "candidates": [],
                    "raw_export": raw_export,
                }
            )
            continue

        if inv_lower in seen_valid_lower:
            skipped_duplicate += 1
            preview_rows.append(
                {
                    "preview_index": -1,
                    "source_row_num": source_row_num,
                    "invoice_number": inv_no,
                    "file_customer_name": str(row.get("customer_name") or "").strip(),
                    "file_email": str(row.get("primary_email") or "").strip(),
                    "status": "skip_duplicate",
                    "message": "Duplicate invoice number in this file",
                    "payload": None,
                    "suggested_client_id": None,
                    "suggested_client_name": None,
                    "match_reason": "none",
                    "candidates": [],
                    "raw_export": raw_export,
                }
            )
            continue
        seen_valid_lower.add(inv_lower)

        due_raw = _parse_date(row.get("due_date"))
        due_date = due_raw or inv_date
        sub = _parse_decimal(row.get("subtotal"))
        if sub is None:
            sub = total
        balance = _parse_decimal(row.get("balance"))
        tax_from_col = _parse_decimal(row.get("item_tax1_amount"))

        tax_amount = Decimal("0")
        if tax_from_col is not None and tax_from_col > 0:
            tax_amount = tax_from_col
        elif total > sub:
            tax_amount = total - sub

        net_amount = sub
        tax_rate = Decimal("0")
        if sub and sub > 0 and tax_amount > 0:
            try:
                tax_rate = (tax_amount / sub).quantize(Decimal("0.0001"))
            except Exception:
                tax_rate = Decimal("0")

        zoho_status = _map_zoho_status(str(row.get("invoice_status") or ""), balance)
        paid_date = _parse_date(row.get("last_payment_date"))
        if zoho_status == "paid" and not paid_date:
            paid_date = _parse_date(row.get("expected_payment_date")) or inv_date

        note_parts = []
        ext_id = row.get("external_invoice_id")
        if ext_id:
            note_parts.append(f"Imported from Zoho (Invoice ID: {ext_id})")
        nd = row.get("notes")
        if nd and str(nd).strip():
            note_parts.append(str(nd).strip())
        for key in ("item_desc", "item_name"):
            v = row.get(key)
            if v and str(v).strip():
                note_parts.append(str(v).strip())
                break
        notes = "\n\n".join(note_parts) if note_parts else None

        payload = {
            "invoice_number": inv_no[:50],
            "invoice_date": inv_date.isoformat(),
            "due_date": due_date.isoformat(),
            "total_amount": str(total),
            "tax_rate": str(tax_rate),
            "tax_amount": str(tax_amount),
            "net_amount": str(net_amount),
            "status": zoho_status,
            "paid_date": paid_date.isoformat() if paid_date and zoho_status == "paid" else None,
            "notes": notes,
        }

        fn = str(row.get("customer_name") or "").strip() or None
        fe = str(row.get("primary_email") or "").strip() or None
        sid, sname, reason, cands = suggest_client_match(fn, fe, accessible_client_ids)

        preview_rows.append(
            {
                "preview_index": preview_index,
                "source_row_num": source_row_num,
                "invoice_number": inv_no,
                "file_customer_name": fn or "—",
                "file_email": fe or "—",
                "status": "ready",
                "message": "",
                "payload": payload,
                "suggested_client_id": sid,
                "suggested_client_name": sname,
                "match_reason": reason,
                "candidates": cands,
                "raw_export": raw_export,
            }
        )
        preview_index += 1

    preview_id = str(uuid.uuid4())
    path = os.path.join(preview_storage_dir(), f"{preview_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "filename": filename,
                "rows": preview_rows,
            },
            f,
            ensure_ascii=False,
        )

    return {
        "ok": True,
        "preview_id": preview_id,
        "row_count": preview_index,
        "skipped_duplicate": skipped_duplicate,
        "skipped_empty": skipped_empty,
        "total_lines": len(preview_rows),
    }


def load_invoice_import_preview(preview_id: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(preview_storage_dir(), f"{preview_id}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def delete_invoice_import_preview(preview_id: str) -> None:
    path = os.path.join(preview_storage_dir(), f"{preview_id}.json")
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def commit_invoice_import_with_mappings(
    preview_rows: List[Dict[str, Any]],
    client_id_by_preview_index: Dict[int, int],
    include_preview_index: Set[int],
    user_id: int,
) -> Dict[str, Any]:
    """
    client_id_by_preview_index: preview_index -> chosen client id
    include_preview_index: which ready rows to import
    """
    created = 0
    errors: List[Dict[str, Any]] = []
    skipped_duplicate_at_commit: List[Dict[str, Any]] = []
    missing_agreement_by_client_id: Dict[int, str] = {}
    committed_invoice_keys: Set[str] = set()

    for pr in preview_rows:
        if pr.get("status") != "ready":
            continue
        pidx = pr["preview_index"]
        if pidx not in include_preview_index:
            continue
        client_id = client_id_by_preview_index.get(pidx)
        if not client_id:
            errors.append(
                {
                    "row": pr.get("source_row_num"),
                    "message": f"{pr.get('invoice_number')}: No client selected",
                    "raw_export": pr.get("raw_export") or {},
                }
            )
            continue

        payload = pr.get("payload")
        if not payload:
            continue

        inv_num = normalize_invoice_number(payload["invoice_number"])
        inv_key = inv_num.lower()
        if inv_key in committed_invoice_keys:
            skipped_duplicate_at_commit.append(
                {
                    "row": pr.get("source_row_num"),
                    "invoice_number": inv_num,
                    "message": "Duplicate invoice number in this import (same file / batch)",
                }
            )
            continue
        if invoice_number_exists_in_db(inv_num):
            skipped_duplicate_at_commit.append(
                {
                    "row": pr.get("source_row_num"),
                    "invoice_number": inv_num,
                    "message": "Already in database",
                }
            )
            continue

        agreement_id = resolve_agreement_id_for_client(client_id)
        if not agreement_id:
            cl = Client.query.get(client_id)
            if cl:
                missing_agreement_by_client_id[client_id] = cl.name or f"Client #{client_id}"

        inv_date = date.fromisoformat(payload["invoice_date"])
        due_date = date.fromisoformat(payload["due_date"])
        paid_date = None
        if payload.get("paid_date"):
            paid_date = date.fromisoformat(payload["paid_date"])

        invoice = Invoice(
            agreement_id=agreement_id,  # may be None until agreement exists
            client_id=client_id,
            invoice_number=inv_num[:50],
            invoice_date=inv_date,
            billing_period_start=inv_date,
            billing_period_end=inv_date,
            total_amount=Decimal(payload["total_amount"]),
            tax_rate=Decimal(payload["tax_rate"]),
            tax_amount=Decimal(payload["tax_amount"]),
            net_amount=Decimal(payload["net_amount"]),
            status=payload["status"],
            due_date=due_date,
            paid_date=paid_date if payload["status"] == "paid" else None,
            notes=payload.get("notes"),
            created_by=user_id,
        )

        try:
            db.session.add(invoice)
            db.session.commit()
            created += 1
            committed_invoice_keys.add(inv_key)
        except Exception as e:
            db.session.rollback()
            if _is_invoice_number_duplicate_error(e):
                skipped_duplicate_at_commit.append(
                    {
                        "row": pr.get("source_row_num"),
                        "invoice_number": inv_num,
                        "message": "Duplicate invoice number (database constraint)",
                    }
                )
            else:
                errors.append(
                    {
                        "row": pr.get("source_row_num"),
                        "message": f"{inv_num}: {str(e)}",
                        "raw_export": pr.get("raw_export") or {},
                    }
                )

    missing_list = [
        {"id": cid, "name": name}
        for cid, name in sorted(
            missing_agreement_by_client_id.items(), key=lambda x: (x[1] or "").lower()
        )
    ]
    return {
        "created": created,
        "errors": errors,
        "clients_missing_agreement": missing_list,
        "skipped_duplicate_at_commit": skipped_duplicate_at_commit,
    }
