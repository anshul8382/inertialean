"""
Regulatory client master sheet — active clients, practice AUA, quarter-end archives.

Columns:
  S no. | Client Name | Clients AUA (in Lakhs) | Link to Agreement |
  Agreement start date | Pan Card number | Email ID | Contact Number

- Practice AUA (holdings × price, advisory exclusions).
- Active clients only (current ``is_active``); blank PAN/agreement still listed.
- PAN: agreement variable → lead KYC → extract from agreement PDF (PyPDF2).
- Start date: ``agreement_date`` variable / signed / sent via agreement_date_service.
- Quarter-end snapshots stored under instance/regulatory_client_master/ for download.
"""

from __future__ import annotations

import json
import logging
import re
from calendar import monthrange
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from flask import current_app, url_for
from sqlalchemy.orm import joinedload

from extensions import db
from models import Agreement, AgreementVariables, Client, Holding, Lead
from services.agreement_date_service import format_agreement_date_iso, resolve_agreement_date
from services.agreement_pdf_paths import agreement_pdf_abs_path
from services.holding_advisory_scope_service import SCOPE_PRACTICE_AUA, get_excluded_map

logger = logging.getLogger(__name__)

LAKH = 100_000.0
_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")

COLUMNS = [
    "S no.",
    "Client Name",
    "Clients AUA (in Lakhs)",
    "Link to Agreement",
    "Agreement start date",
    "Pan Card number",
    "Email ID",
    "Contact Number",
]


def parse_as_of_param(raw: Optional[str]) -> Tuple[date, str]:
    today = date.today()
    s = (raw or "").strip().lower()
    if not s or s in ("live", "today", "current"):
        return today, "live"
    if len(s) >= 6 and s[4] == "-" and s[5] in "qQ":
        try:
            y = int(s[:4])
            q = int(s[6:])
            if q not in (1, 2, 3, 4):
                raise ValueError("bad quarter")
            d = date(y, q * 3, monthrange(y, q * 3)[1])
            return d, f"{y}-Q{q}"
        except Exception:
            return today, "live"
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d").date()
        return d, d.isoformat()
    except Exception:
        return today, "live"


def is_quarter_end(d: date) -> bool:
    return d.month in (3, 6, 9, 12) and d.day == monthrange(d.year, d.month)[1]


def available_as_of_options(*, earliest_year: int = 2020) -> List[Dict[str, str]]:
    today = date.today()
    opts: List[Dict[str, str]] = [
        {"value": "live", "label": f"Live (as of {today.isoformat()})"},
    ]
    for year in range(earliest_year, today.year + 1):
        for q in (1, 2, 3, 4):
            d = date(year, q * 3, monthrange(year, q * 3)[1])
            if d > today:
                continue
            opts.append(
                {
                    "value": d.isoformat(),
                    "label": f"Q{q} {year} (ended {d.isoformat()})",
                }
            )
    head, rest = opts[:1], opts[1:]
    rest.reverse()
    return head + rest


def _as_float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _snapshot_root() -> Path:
    try:
        root = Path(current_app.instance_path) / "regulatory_client_master"
    except RuntimeError:
        root = Path(__file__).resolve().parents[1] / "instance" / "regulatory_client_master"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _live_practice_aua_by_client(client_ids: Sequence[int]) -> Dict[int, float]:
    if not client_ids:
        return {}
    holdings = (
        Holding.query.filter(Holding.client_id.in_(list(client_ids)))
        .options(joinedload(Holding.security))
        .all()
    )
    excluded_map = get_excluded_map(list(client_ids), SCOPE_PRACTICE_AUA)
    out: Dict[int, float] = {cid: 0.0 for cid in client_ids}
    for h in holdings:
        if h.security_id in excluded_map.get(h.client_id, set()):
            continue
        sec = h.security
        if not sec:
            continue
        price = _as_float(sec.current_price)
        if price <= 0:
            continue
        out[h.client_id] = out.get(h.client_id, 0.0) + _as_float(h.quantity) * price
    return out


def _historical_practice_aua_by_client(client_ids: Sequence[int], as_of: date) -> Dict[int, float]:
    from services.forward_holding_calculation_service import get_client_portfolio_by_date

    excluded_map = get_excluded_map(list(client_ids), SCOPE_PRACTICE_AUA)
    out: Dict[int, float] = {}
    for cid in client_ids:
        try:
            result = get_client_portfolio_by_date(cid, as_of) or {}
            excl = excluded_map.get(cid, set())
            total = 0.0
            for row in result.get("holdings") or []:
                if row.get("security_id") in excl:
                    continue
                total += _as_float(row.get("current_value"))
            out[cid] = total
        except Exception as exc:
            logger.warning("regulatory AUA as-of failed client=%s as_of=%s: %s", cid, as_of, exc)
            out[cid] = 0.0
    return out


def _extract_pan_from_pdf(agreement: Agreement) -> str:
    return extract_pan_from_agreement_pdf(agreement)


def extract_pan_from_agreement_pdf(agreement: Agreement) -> str:
    path = agreement_pdf_abs_path(agreement.generated_pdf_path)
    if not path:
        return ""
    try:
        import PyPDF2

        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text = "".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        logger.debug("PAN PDF extract failed agreement=%s: %s", agreement.id, exc)
        return ""
    # Prefer line context mentioning PAN
    upper = text.upper()
    for m in _PAN_RE.finditer(upper):
        pan = m.group(1)
        ctx_start = max(0, m.start() - 40)
        ctx = upper[ctx_start : m.end() + 10]
        if "PAN" in ctx or m.start() > 0:
            return pan
    m = _PAN_RE.search(upper)
    return m.group(1) if m else ""


def get_best_agreement_for_client(client_id: int) -> Optional[Agreement]:
    return _best_agreement_by_client([client_id]).get(client_id)


def _pan_by_client(
    client_ids: Sequence[int],
    ag_by: Dict[int, Agreement],
) -> Dict[int, str]:
    out: Dict[int, str] = {}
    if not client_ids:
        return out
    rows = (
        AgreementVariables.query.filter(
            AgreementVariables.client_id.in_(list(client_ids)),
            AgreementVariables.variable_name == "pan",
        )
        .order_by(AgreementVariables.updated_at.desc(), AgreementVariables.id.desc())
        .all()
    )
    for r in rows:
        if r.client_id and r.client_id not in out:
            val = (r.variable_value or "").strip().upper()
            if val and _PAN_RE.fullmatch(val):
                out[r.client_id] = val

    missing = [cid for cid in client_ids if cid not in out]
    if missing:
        leads = Lead.query.filter(Lead.client_id.in_(missing)).all()
        try:
            from models.lead_onboarding import LeadKycProfile

            lead_ids = [L.id for L in leads]
            profiles = (
                LeadKycProfile.query.filter(LeadKycProfile.lead_id.in_(lead_ids)).all()
                if lead_ids
                else []
            )
            by_lead = {p.lead_id: (p.pan or "").strip().upper() for p in profiles}
            for L in leads:
                pan = by_lead.get(L.id) or ""
                if pan and _PAN_RE.fullmatch(pan) and L.client_id and L.client_id not in out:
                    out[L.client_id] = pan
        except Exception as exc:
            logger.debug("KYC PAN lookup skipped: %s", exc)

        lead_ids = [L.id for L in leads]
        lead_to_client = {L.id: L.client_id for L in leads}
        if lead_ids:
            lead_pans = (
                AgreementVariables.query.filter(
                    AgreementVariables.lead_id.in_(lead_ids),
                    AgreementVariables.variable_name == "pan",
                )
                .order_by(AgreementVariables.updated_at.desc())
                .all()
            )
            for r in lead_pans:
                cid = r.client_id or lead_to_client.get(r.lead_id)
                if not cid or cid in out:
                    continue
                val = (r.variable_value or "").strip().upper()
                if val and _PAN_RE.fullmatch(val):
                    out[cid] = val

    # PDF fallback for still-missing
    for cid in client_ids:
        if cid in out:
            continue
        ag = ag_by.get(cid)
        if not ag:
            continue
        pan = _extract_pan_from_pdf(ag)
        if pan:
            out[cid] = pan
    return out


def _best_agreement_by_client(client_ids: Sequence[int]) -> Dict[int, Agreement]:
    if not client_ids:
        return {}
    leads = Lead.query.filter(Lead.client_id.in_(list(client_ids))).all()
    lead_to_client = {L.id: L.client_id for L in leads if L.client_id}
    if not lead_to_client:
        return {}
    # MySQL has no NULLS LAST — sort signed/updated nulls last in Python.
    agreements = (
        Agreement.query.options(joinedload(Agreement.variables))
        .filter(Agreement.lead_id.in_(list(lead_to_client.keys())))
        .all()
    )
    agreements.sort(
        key=lambda a: (
            a.signed_date is None,
            -(a.signed_date.toordinal() if a.signed_date else 0),
            a.updated_at is None,
            -(a.updated_at.timestamp() if a.updated_at else 0),
            -(a.id or 0),
        )
    )
    out: Dict[int, Agreement] = {}
    for ag in agreements:
        cid = lead_to_client.get(ag.lead_id)
        if not cid or cid in out:
            continue
        status = (ag.status or "").lower()
        if status in ("signed", "completed", "active") or ag.signed_date:
            out[cid] = ag
    for ag in agreements:
        cid = lead_to_client.get(ag.lead_id)
        if cid and cid not in out:
            out[cid] = ag
    return out


def _agreement_link(agreement: Optional[Agreement], *, absolute: bool = False) -> str:
    if not agreement:
        return ""
    try:
        return url_for(
            "agreements.view_agreement",
            agreement_id=agreement.id,
            _external=absolute,
        )
    except Exception:
        return f"/agreements/{agreement.id}"


def build_regulatory_client_master(
    *,
    as_of: Optional[date] = None,
    mode: str = "live",
) -> Dict[str, Any]:
    as_of = as_of or date.today()
    is_live = mode == "live" or as_of >= date.today()

    clients = (
        Client.query.filter(Client.is_active.is_(True))
        .order_by(Client.name.asc())
        .all()
    )
    client_ids = [c.id for c in clients]

    if is_live:
        aua_by = _live_practice_aua_by_client(client_ids)
        mode_out = "live"
    else:
        aua_by = _historical_practice_aua_by_client(client_ids, as_of)
        mode_out = "quarter_end"

    ag_by = _best_agreement_by_client(client_ids)
    pan_by = _pan_by_client(client_ids, ag_by)

    rows: List[Dict[str, Any]] = []
    for i, c in enumerate(clients, start=1):
        aua_inr = aua_by.get(c.id, 0.0)
        ag = ag_by.get(c.id)
        start_iso = ""
        if ag:
            try:
                start_iso = format_agreement_date_iso(resolve_agreement_date(ag))
            except Exception:
                start_iso = ""
        rows.append(
            {
                "s_no": i,
                "client_id": c.id,
                "client_name": c.name or "",
                "aua_inr": round(aua_inr, 2),
                "aua_lakhs": round(aua_inr / LAKH, 2),
                "agreement_id": ag.id if ag else None,
                "agreement_status": (ag.status if ag else "") or "",
                "agreement_link": _agreement_link(ag, absolute=False),
                "agreement_link_absolute": _agreement_link(ag, absolute=True),
                "agreement_start_date": start_iso,
                "pan": pan_by.get(c.id, ""),
                "email": c.email or "",
                "phone": (c.phone or c.whatsapp_number or "") or "",
            }
        )

    return {
        "as_of": as_of.isoformat(),
        "mode": mode_out,
        "active_only": True,
        "row_count": len(rows),
        "total_aua_lakhs": round(sum(r["aua_lakhs"] for r in rows), 2),
        "rows": rows,
        "columns": list(COLUMNS),
    }


def build_regulatory_client_master_workbook_bytes(
    payload: Dict[str, Any],
) -> Tuple[bytes, str]:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Client master"
    ws.append(payload["columns"])
    for r in payload["rows"]:
        ws.append(
            [
                r["s_no"],
                r["client_name"],
                r["aua_lakhs"],
                r.get("agreement_link_absolute") or r.get("agreement_link") or "",
                r.get("agreement_start_date") or "",
                r["pan"],
                r["email"],
                r["phone"],
            ]
        )
    ws2 = wb.create_sheet("Meta")
    for k in ("as_of", "mode", "active_only", "row_count", "total_aua_lakhs"):
        ws2.append([k, payload.get(k)])
    ws2.append(["generated_at_utc", datetime.utcnow().isoformat() + "Z"])
    ws2.append(["aua_definition", "practice_aua (holdings x price, advisory exclusions)"])

    buf = BytesIO()
    wb.save(buf)
    fname = f"regulatory_client_master_{payload.get('as_of', 'live')}.xlsx"
    return buf.getvalue(), fname


def freeze_quarter_snapshot(
    as_of: date,
    *,
    user_id: Optional[int] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Build as-of sheet and persist Excel + meta under instance/regulatory_client_master/.

    Prefer quarter-end dates; non-QE allowed with force=True (labelled accordingly).
    """
    if not is_quarter_end(as_of) and not force:
        return {
            "success": False,
            "error": f"{as_of.isoformat()} is not a calendar quarter end (Mar/Jun/Sep/Dec). Pass force=True to archive anyway.",
        }

    mode = "live" if as_of >= date.today() else "quarter_end"
    payload = build_regulatory_client_master(as_of=as_of, mode=mode)
    data, _ = build_regulatory_client_master_workbook_bytes(payload)

    root = _snapshot_root()
    stem = f"as_of_{as_of.isoformat()}"
    xlsx_path = root / f"{stem}.xlsx"
    meta_path = root / f"{stem}.meta.json"
    xlsx_path.write_bytes(data)
    meta = {
        "as_of": as_of.isoformat(),
        "mode": payload["mode"],
        "row_count": payload["row_count"],
        "total_aua_lakhs": payload["total_aua_lakhs"],
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "generated_by_user_id": user_id,
        "file": xlsx_path.name,
        "is_quarter_end": is_quarter_end(as_of),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"success": True, "meta": meta, "path": str(xlsx_path)}


def list_quarter_snapshots() -> List[Dict[str, Any]]:
    root = _snapshot_root()
    items: List[Dict[str, Any]] = []
    for meta_path in sorted(root.glob("as_of_*.meta.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        xlsx = root / (meta.get("file") or meta_path.name.replace(".meta.json", ".xlsx"))
        meta["exists"] = xlsx.is_file()
        meta["download_key"] = meta.get("as_of") or ""
        items.append(meta)
    return items


def get_snapshot_xlsx_path(as_of_key: str) -> Optional[Path]:
    d, _ = parse_as_of_param(as_of_key)
    path = _snapshot_root() / f"as_of_{d.isoformat()}.xlsx"
    return path if path.is_file() else None
