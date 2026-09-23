"""
Advisory register — SEBI-style register of investment advice.

Columns: Client | Date of Advice | Nature of advice | Products/Securities | Fee
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import joinedload

from extensions import db
from services.db_cutover import advisory_register_enabled

logger = logging.getLogger(__name__)

RECO_SUBJECT_MARKER = "Portfolio Investment Recos"
FY_25_26_START = date(2025, 4, 1)
FY_25_26_END = date(2026, 3, 31)  # inclusive
# Gmail Sent import is only for FY 2025-26 (and earlier if ever needed).
# From FY 2026-27 (1 Apr 2026) onward: DB + live send hook only.
GMAIL_IMPORT_UNTIL_FY_START = date(2026, 4, 1)  # exclusive — no Gmail from this FY start onward
ALL_APP_WINDOW_DAYS = 14

COLUMNS = [
    "Client",
    "Date of Advice",
    "Nature of advice",
    "Products/Securities in which advise was given",
    "Fee charged for advise",
]

SOURCE_INERTIA = "inertia_send"
SOURCE_GMAIL = "gmail_import"
SOURCE_DB = "db_backfill"


def indian_fy_bounds(fy_label: Optional[str] = None) -> Tuple[date, date]:
    """Return (start, end inclusive) for an Indian FY label like '2025-26' or 'FY25-26'."""
    raw = (fy_label or "2025-26").strip().upper().replace("FY", "").replace(" ", "")
    m = re.match(r"^(\d{4})-(\d{2})$", raw)
    if m:
        start_y = int(m.group(1))
        return date(start_y, 4, 1), date(start_y + 1, 3, 31)
    m2 = re.match(r"^(\d{2})-(\d{2})$", raw)
    if m2:
        start_y = 2000 + int(m2.group(1))
        return date(start_y, 4, 1), date(start_y + 1, 3, 31)
    return FY_25_26_START, FY_25_26_END


def available_fy_options() -> List[Dict[str, str]]:
    today = date.today()
    # Current FY start year
    fy_start = today.year if today.month >= 4 else today.year - 1
    opts = []
    for y in range(fy_start, 2024, -1):
        label = f"{y}-{str(y + 1)[2:]}"
        opts.append({"value": label, "label": f"FY {label}"})
    if not any(o["value"] == "2025-26" for o in opts):
        opts.append({"value": "2025-26", "label": "FY 2025-26"})
    return opts


def _fmt_inr(amount: float) -> str:
    """Compact INR for nature-of-advice (lakhs when ≥ 1L)."""
    a = abs(amount)
    if a >= 100_000:
        return f"₹{a / 100_000:.2f}L".rstrip("0").rstrip(".")
    if a >= 1000:
        return f"₹{a:,.0f}"
    return f"₹{a:,.2f}".rstrip("0").rstrip(".")


def _products_hash(products: str) -> str:
    return hashlib.sha256((products or "").strip().encode("utf-8")).hexdigest()[:32]


def format_nature_and_products(
    lines: Sequence[Dict[str, Any]],
) -> Tuple[str, str]:
    """
    lines: dicts with keys action (buy/sell), symbol, name, amount, asset_class
    Returns (nature_of_advice, products_securities).
    """
    by_class: Dict[str, Dict[str, float]] = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
    buys: List[str] = []
    sells: List[str] = []
    seen_buy: Set[str] = set()
    seen_sell: Set[str] = set()

    for row in lines:
        action = (row.get("action") or "").strip().lower()
        if action not in ("buy", "sell"):
            continue
        label = (row.get("symbol") or row.get("name") or "").strip()
        if not label:
            continue
        ac = (row.get("asset_class") or "Unclassified").strip() or "Unclassified"
        amt = float(row.get("amount") or 0)
        by_class[ac][action] += amt
        if action == "buy" and label not in seen_buy:
            buys.append(label)
            seen_buy.add(label)
        elif action == "sell" and label not in seen_sell:
            sells.append(label)
            seen_sell.add(label)

    nature_parts: List[str] = []
    for ac in sorted(by_class.keys()):
        bits = []
        if by_class[ac]["buy"]:
            bits.append(f"Buy {_fmt_inr(by_class[ac]['buy'])}")
        if by_class[ac]["sell"]:
            bits.append(f"Sell {_fmt_inr(by_class[ac]['sell'])}")
        if bits:
            nature_parts.append(f"{ac}: {' / '.join(bits)}")
    nature = "; ".join(nature_parts) if nature_parts else "Investment advice (see securities)"

    prod_parts = []
    if buys:
        prod_parts.append("BUY: " + ", ".join(buys))
    if sells:
        prod_parts.append("SELL: " + ", ".join(sells))
    products = "; ".join(prod_parts) if prod_parts else ""
    return nature, products


def _rec_line_dicts(recommendations: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in recommendations:
        action = (getattr(rec, "action", None) or "").strip().lower()
        if action not in ("buy", "sell"):
            continue
        if getattr(rec, "quantity", None) is None:
            continue
        sec = getattr(rec, "security", None)
        symbol = (getattr(sec, "symbol", None) or "").strip() if sec else ""
        name = (getattr(sec, "name", None) or "").strip() if sec else ""
        ac_obj = getattr(rec, "asset_class", None)
        if ac_obj is None and sec is not None:
            ac_obj = getattr(sec, "asset_class", None)
        ac_name = (getattr(ac_obj, "name", None) or "Unclassified") if ac_obj else "Unclassified"
        qty = float(rec.quantity or 0)
        price = float(rec.actual_price or rec.target_price or 0)
        if price == 0 and sec is not None and getattr(sec, "current_price", None):
            price = float(sec.current_price or 0)
        amount = qty * price
        out.append(
            {
                "action": action,
                "symbol": symbol,
                "name": name,
                "amount": amount,
                "asset_class": ac_name,
            }
        )
    return out


def session_is_reliable(session_id: int, *, advice_date: Optional[date] = None) -> bool:
    """True when session has executable trades and evidence of send."""
    from models import EmailLog, Recommendation, RecommendationSession

    session = RecommendationSession.query.get(session_id)
    if not session or not session.client_id:
        return False

    recs = (
        Recommendation.query.options(
            joinedload(Recommendation.security),
            joinedload(Recommendation.asset_class),
        )
        .filter_by(session_id=session_id)
        .all()
    )
    lines = _rec_line_dicts(recs)
    if not lines:
        return False
    _, products = format_nature_and_products(lines)
    if not products:
        return False

    has_send_evidence = False
    for rec in recs:
        st = (rec.status or "").strip().lower()
        if st in ("sent", "executed") or rec.sent_at:
            has_send_evidence = True
            break

    if not has_send_evidence:
        ref = advice_date or date.today()
        q = EmailLog.query.filter(
            EmailLog.client_id == session.client_id,
            EmailLog.status == "sent",
            EmailLog.subject.ilike(f"%{RECO_SUBJECT_MARKER}%"),
        )
        for elog in q.order_by(EmailLog.sent_at.desc()).limit(50):
            if not elog.sent_at:
                continue
            d = elog.sent_at.date() if isinstance(elog.sent_at, datetime) else elog.sent_at
            if abs((d - ref).days) <= 1:
                has_send_evidence = True
                break

    return has_send_evidence


def build_fields_from_session(
    session_id: int,
) -> Optional[Dict[str, Any]]:
    """Build register field dict from a recommendation session, or None if empty."""
    from models import Client, EmailLog, Recommendation, RecommendationSession, Security

    session = RecommendationSession.query.options(
        joinedload(RecommendationSession.client),
    ).get(session_id)
    if not session:
        return None

    recs = (
        Recommendation.query.options(
            joinedload(Recommendation.security).joinedload(Security.asset_class),
            joinedload(Recommendation.asset_class),
        )
        .filter_by(session_id=session_id)
        .all()
    )
    lines = _rec_line_dicts(recs)
    if not lines:
        return None
    nature, products = format_nature_and_products(lines)
    if not products:
        return None

    advice_dt: Optional[datetime] = None
    for rec in recs:
        if rec.sent_at and (advice_dt is None or rec.sent_at > advice_dt):
            advice_dt = rec.sent_at

    subject = None
    email_log_id = None
    if session.client_id:
        elog = (
            EmailLog.query.filter(
                EmailLog.client_id == session.client_id,
                EmailLog.subject.ilike(f"%{RECO_SUBJECT_MARKER}%"),
                EmailLog.status == "sent",
            )
            .order_by(EmailLog.sent_at.desc())
            .first()
        )
        # Prefer log near sent_at
        if advice_dt:
            candidates = (
                EmailLog.query.filter(
                    EmailLog.client_id == session.client_id,
                    EmailLog.subject.ilike(f"%{RECO_SUBJECT_MARKER}%"),
                    EmailLog.status == "sent",
                )
                .order_by(EmailLog.sent_at.desc())
                .limit(20)
                .all()
            )
            for c in candidates:
                if c.sent_at and abs((c.sent_at - advice_dt).total_seconds()) <= 86400 * 2:
                    elog = c
                    break
        if elog:
            email_log_id = elog.id
            subject = elog.subject
            if advice_dt is None and elog.sent_at:
                advice_dt = elog.sent_at

    if advice_dt is None:
        advice_dt = getattr(session, "created_at", None) or datetime.utcnow()

    client = session.client or (Client.query.get(session.client_id) if session.client_id else None)
    name = (getattr(client, "name", None) or "").strip() or f"Client #{session.client_id}"

    return {
        "client_id": session.client_id,
        "client_name_snapshot": name,
        "advice_date": advice_dt.date() if isinstance(advice_dt, datetime) else advice_dt,
        "nature_of_advice": nature,
        "products_securities": products,
        "fee_charged": Decimal("0"),
        "products_hash": _products_hash(products),
        "recommendation_session_id": session_id,
        "email_log_id": email_log_id,
        "subject": subject,
        "import_confidence": "high",
    }


def _existing_for_session(session_id: int) -> Optional[Any]:
    from models.advisory_register import AdvisoryRegisterEntry

    return AdvisoryRegisterEntry.query.filter_by(
        recommendation_session_id=session_id
    ).first()


def _existing_gmail(gmail_message_id: str) -> Optional[Any]:
    from models.advisory_register import AdvisoryRegisterEntry

    if not gmail_message_id:
        return None
    return AdvisoryRegisterEntry.query.filter_by(gmail_message_id=gmail_message_id).first()


def _soft_dupe(
    client_id: Optional[int],
    advice_date: date,
    products_hash: Optional[str],
) -> Optional[Any]:
    from models.advisory_register import AdvisoryRegisterEntry

    if not client_id or not products_hash:
        return None
    return (
        AdvisoryRegisterEntry.query.filter_by(
            client_id=client_id,
            advice_date=advice_date,
            products_hash=products_hash,
        ).first()
    )


def append_from_session(
    session_id: int,
    *,
    email_log_id: Optional[int] = None,
    user_id: Optional[int] = None,
    source: str = SOURCE_INERTIA,
    gmail_message_id: Optional[str] = None,
    commit: bool = True,
) -> Optional[Any]:
    """
    Create or refresh an advisory register row from a recommendation session.
    Idempotent for the same session_id.
    """
    if not advisory_register_enabled():
        return None

    from models.advisory_register import AdvisoryRegisterEntry

    fields = build_fields_from_session(session_id)
    if not fields:
        return None

    existing = _existing_for_session(session_id)
    if existing:
        if gmail_message_id and not existing.gmail_message_id:
            existing.gmail_message_id = gmail_message_id
        if email_log_id and not existing.email_log_id:
            existing.email_log_id = email_log_id
        if commit:
            db.session.commit()
        return existing

    if gmail_message_id:
        g_ex = _existing_gmail(gmail_message_id)
        if g_ex:
            g_ex.recommendation_session_id = session_id
            g_ex.source = source
            g_ex.nature_of_advice = fields["nature_of_advice"]
            g_ex.products_securities = fields["products_securities"]
            g_ex.products_hash = fields["products_hash"]
            g_ex.import_confidence = "high"
            if commit:
                db.session.commit()
            return g_ex

    soft = _soft_dupe(
        fields["client_id"], fields["advice_date"], fields["products_hash"]
    )
    if soft:
        soft.recommendation_session_id = session_id
        if gmail_message_id and not soft.gmail_message_id:
            soft.gmail_message_id = gmail_message_id
        if commit:
            db.session.commit()
        return soft

    entry = AdvisoryRegisterEntry(
        client_id=fields["client_id"],
        client_name_snapshot=fields["client_name_snapshot"],
        advice_date=fields["advice_date"],
        nature_of_advice=fields["nature_of_advice"],
        products_securities=fields["products_securities"],
        fee_charged=0,
        products_hash=fields["products_hash"],
        source=source,
        recommendation_session_id=session_id,
        email_log_id=email_log_id or fields.get("email_log_id"),
        gmail_message_id=gmail_message_id,
        subject=fields.get("subject"),
        import_confidence=fields.get("import_confidence") or "high",
        created_by_user_id=user_id,
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry


def append_from_parsed_email(
    *,
    client_id: Optional[int],
    client_name: str,
    advice_date: date,
    nature: str,
    products: str,
    subject: str,
    gmail_message_id: str,
    raw_excerpt: str = "",
    confidence: str = "medium",
    user_id: Optional[int] = None,
    commit: bool = True,
) -> Optional[Any]:
    if not advisory_register_enabled():
        return None
    from models.advisory_register import AdvisoryRegisterEntry

    if _existing_gmail(gmail_message_id):
        return _existing_gmail(gmail_message_id)

    ph = _products_hash(products)
    soft = _soft_dupe(client_id, advice_date, ph)
    if soft:
        if not soft.gmail_message_id:
            soft.gmail_message_id = gmail_message_id
        if commit:
            db.session.commit()
        return soft

    entry = AdvisoryRegisterEntry(
        client_id=client_id,
        client_name_snapshot=(client_name or "").strip() or "Unknown",
        advice_date=advice_date,
        nature_of_advice=nature or "Investment advice (see securities)",
        products_securities=products or "",
        fee_charged=0,
        products_hash=ph,
        source=SOURCE_GMAIL,
        gmail_message_id=gmail_message_id,
        subject=subject[:500] if subject else None,
        raw_excerpt=(raw_excerpt or "")[:4000],
        import_confidence=confidence,
        created_by_user_id=user_id,
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry


def find_matching_reliable_session(
    *,
    client_id: Optional[int],
    advice_date: date,
    symbols: Optional[Set[str]] = None,
    subject: Optional[str] = None,
) -> Optional[int]:
    """Return session_id of a reliable match, or None."""
    from models import EmailLog, Recommendation, RecommendationSession

    if not client_id:
        return None

    # EmailLog subject match first
    if subject and RECO_SUBJECT_MARKER.lower() in subject.lower():
        logs = (
            EmailLog.query.filter(
                EmailLog.client_id == client_id,
                EmailLog.subject.ilike(f"%{RECO_SUBJECT_MARKER}%"),
                EmailLog.status == "sent",
            )
            .order_by(EmailLog.sent_at.desc())
            .limit(30)
            .all()
        )
        for elog in logs:
            if not elog.sent_at:
                continue
            d = elog.sent_at.date() if isinstance(elog.sent_at, datetime) else elog.sent_at
            if abs((d - advice_date).days) > 1:
                continue
            # Find session with sent recs that day
            recs = (
                Recommendation.query.filter(
                    Recommendation.client_id == client_id,
                    Recommendation.sent_at.isnot(None),
                    Recommendation.session_id.isnot(None),
                )
                .filter(
                    db.func.date(Recommendation.sent_at) >= advice_date - timedelta(days=1),
                    db.func.date(Recommendation.sent_at) <= advice_date + timedelta(days=1),
                )
                .all()
            )
            session_ids = {r.session_id for r in recs if r.session_id}
            for sid in session_ids:
                if session_is_reliable(sid, advice_date=advice_date):
                    return sid

    # Date + client sessions
    sessions = (
        RecommendationSession.query.filter_by(client_id=client_id)
        .order_by(RecommendationSession.id.desc())
        .limit(40)
        .all()
    )
    symbols_u = {s.upper() for s in (symbols or set()) if s}

    for sess in sessions:
        if not session_is_reliable(sess.id, advice_date=advice_date):
            continue
        fields = build_fields_from_session(sess.id)
        if not fields:
            continue
        if abs((fields["advice_date"] - advice_date).days) > 1:
            continue
        if symbols_u:
            prod = (fields["products_securities"] or "").upper()
            overlap = sum(1 for s in symbols_u if s in prod)
            if overlap == 0:
                continue
        return sess.id
    return None


def detect_gmail_cutover_date(
    messages: Sequence[Dict[str, Any]],
    match_results: Sequence[bool],
) -> Optional[date]:
    """
    Earliest C such that window [C-13, C] has ≥1 mail and every mail in window matched.
    Returns C (end of window); caller uses C+1 as stop-Gmail-from date.
    messages/match_results must be same length, chronologically sorted by advice_date.
    """
    if len(messages) != len(match_results) or not messages:
        return None

    # Index messages by date
    by_day: Dict[date, List[bool]] = defaultdict(list)
    for msg, matched in zip(messages, match_results):
        d = msg.get("advice_date")
        if isinstance(d, datetime):
            d = d.date()
        if not isinstance(d, date):
            continue
        by_day[d].append(bool(matched))

    if not by_day:
        return None

    min_d = min(by_day.keys())
    max_d = max(by_day.keys())
    cur = min_d + timedelta(days=ALL_APP_WINDOW_DAYS - 1)
    while cur <= max_d:
        window_start = cur - timedelta(days=ALL_APP_WINDOW_DAYS - 1)
        flags: List[bool] = []
        d = window_start
        while d <= cur:
            flags.extend(by_day.get(d, []))
            d += timedelta(days=1)
        if flags and all(flags):
            return cur
        cur += timedelta(days=1)
    return None


def _import_meta_path() -> Path:
    try:
        from flask import current_app

        root = Path(current_app.instance_path) / "advisory_register"
    except Exception:
        root = Path(__file__).resolve().parents[1] / "instance" / "advisory_register"
    root.mkdir(parents=True, exist_ok=True)
    return root / "import_meta.json"


def load_import_meta() -> Dict[str, Any]:
    path = _import_meta_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_import_meta(meta: Dict[str, Any]) -> None:
    path = _import_meta_path()
    path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")


def resolve_client_from_name_or_email(
    name: Optional[str],
    email: Optional[str] = None,
) -> Tuple[Optional[int], str]:
    from models import Client

    if email:
        em = email.strip().lower()
        c = Client.query.filter(db.func.lower(Client.email) == em).first()
        if c:
            return c.id, c.name or name or ""

    if name:
        n = name.strip()
        c = Client.query.filter(db.func.lower(Client.name) == n.lower()).first()
        if c:
            return c.id, c.name or n
        # fuzzy contains
        c = Client.query.filter(Client.name.ilike(f"%{n}%")).first()
        if c:
            return c.id, c.name or n
    return None, (name or "").strip()


def parse_client_name_from_subject(subject: str) -> Optional[str]:
    """Extract client name from 'Portfolio Investment Recos for {name} {Mon YYYY}'."""
    if not subject:
        return None
    m = re.search(
        r"Portfolio Investment Recos for\s+(.+?)\s+([A-Za-z]{3}\s+\d{4})\s*$",
        subject.strip(),
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    m2 = re.search(
        r"Portfolio Investment Recos for\s+(.+)$",
        subject.strip(),
        re.IGNORECASE,
    )
    if m2:
        # Drop trailing month year if present
        name = re.sub(r"\s+[A-Za-z]{3}\s+\d{4}\s*$", "", m2.group(1)).strip()
        return name or None
    return None


def list_register_entries(
    *,
    fy_label: str = "2025-26",
) -> Dict[str, Any]:
    start, end = indian_fy_bounds(fy_label)
    enabled = advisory_register_enabled()
    rows: List[Dict[str, Any]] = []
    if enabled:
        from models.advisory_register import AdvisoryRegisterEntry

        entries = (
            AdvisoryRegisterEntry.query.filter(
                AdvisoryRegisterEntry.advice_date >= start,
                AdvisoryRegisterEntry.advice_date <= end,
            )
            .order_by(AdvisoryRegisterEntry.advice_date.asc(), AdvisoryRegisterEntry.id.asc())
            .all()
        )
        for i, e in enumerate(entries, start=1):
            rows.append(
                {
                    "s_no": i,
                    "id": e.id,
                    "client_id": e.client_id,
                    "client": e.client_name_snapshot,
                    "advice_date": e.advice_date.isoformat() if e.advice_date else "",
                    "nature_of_advice": e.nature_of_advice,
                    "products_securities": e.products_securities,
                    "fee_charged": float(e.fee_charged or 0),
                    "source": e.source,
                    "import_confidence": e.import_confidence,
                    "subject": e.subject,
                }
            )
    meta = load_import_meta()
    return {
        "columns": COLUMNS,
        "rows": rows,
        "row_count": len(rows),
        "fy_label": fy_label,
        "fy_start": start.isoformat(),
        "fy_end": end.isoformat(),
        "enabled": enabled,
        "gmail_import_allowed": gmail_import_allowed_for_fy(start),
        "gmail_cutover_date": meta.get("gmail_cutover_date"),
        "gmail_stop_from": meta.get("gmail_stop_from"),
    }


def build_workbook_bytes(payload: Dict[str, Any]) -> Tuple[bytes, str]:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Advisory Register"
    ws.append(COLUMNS)
    for r in payload.get("rows") or []:
        ws.append(
            [
                r.get("client"),
                r.get("advice_date"),
                r.get("nature_of_advice"),
                r.get("products_securities"),
                r.get("fee_charged", 0),
            ]
        )
    meta = wb.create_sheet("Meta")
    meta.append(["fy", payload.get("fy_label")])
    meta.append(["row_count", payload.get("row_count")])
    meta.append(["gmail_cutover_date", payload.get("gmail_cutover_date") or ""])
    meta.append(["gmail_stop_from", payload.get("gmail_stop_from") or ""])
    meta.append(["generated_at", datetime.utcnow().isoformat() + "Z"])
    buf = BytesIO()
    wb.save(buf)
    fname = f"advisory_register_{payload.get('fy_label', 'fy')}.xlsx"
    return buf.getvalue(), fname


def backfill_db_sessions(
    *,
    fy_start: date,
    fy_end: date,
    user_id: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Add register rows for reliable sent sessions in FY that have no row yet."""
    from models import Recommendation

    if not advisory_register_enabled():
        return {"added": 0, "skipped": 0, "enabled": False}

    recs = (
        Recommendation.query.filter(
            Recommendation.client_id.isnot(None),
            Recommendation.session_id.isnot(None),
            Recommendation.sent_at.isnot(None),
            db.func.date(Recommendation.sent_at) >= fy_start,
            db.func.date(Recommendation.sent_at) <= fy_end,
        )
        .all()
    )
    session_ids = sorted({r.session_id for r in recs if r.session_id})
    added = 0
    skipped = 0
    for sid in session_ids:
        if not session_is_reliable(sid):
            skipped += 1
            continue
        if _existing_for_session(sid):
            skipped += 1
            continue
        if dry_run:
            added += 1
            continue
        entry = append_from_session(
            sid, user_id=user_id, source=SOURCE_DB, commit=True
        )
        if entry:
            added += 1
        else:
            skipped += 1
    return {"added": added, "skipped": skipped, "candidates": len(session_ids)}


def gmail_import_allowed_for_fy(fy_start: date) -> bool:
    """True only for FYs before FY 2026-27 (historical mailbox backfill)."""
    return fy_start < GMAIL_IMPORT_UNTIL_FY_START


def run_fy_import(
    *,
    fy_label: str = "2025-26",
    user_id: Optional[int] = None,
    dry_run: bool = False,
    fetch_gmail: bool = True,
) -> Dict[str, Any]:
    """
    FY import: Gmail Sent + reliable DB for FY 2025-26 only.
    From FY 2026-27 onward: DB backfill only (live sends already append via hook).
    Within FY 2025-26, stop Gmail after the 2-week all-app window.
    """
    from services.gmail_sent_imap_service import (
        fetch_portfolio_reco_sent,
        parse_reco_email_body,
    )

    if not advisory_register_enabled():
        return {"ok": False, "error": "advisory_register_entry table missing or deferred"}

    fy_start, fy_end = indian_fy_bounds(fy_label)
    use_gmail = bool(fetch_gmail) and gmail_import_allowed_for_fy(fy_start)
    meta = load_import_meta()
    stop_from_s = meta.get("gmail_stop_from")
    stop_from: Optional[date] = None
    if stop_from_s:
        try:
            stop_from = date.fromisoformat(str(stop_from_s)[:10])
        except Exception:
            stop_from = None

    gmail_stats = {
        "fetched": 0,
        "from_db": 0,
        "from_gmail_parse": 0,
        "skipped_after_cutover": 0,
        "unmatched": 0,
        "skipped_fy_policy": not use_gmail and bool(fetch_gmail),
    }
    messages: List[Dict[str, Any]] = []
    match_flags: List[bool] = []

    if use_gmail:
        end_exclusive = fy_end + timedelta(days=1)
        try:
            raw_msgs = fetch_portfolio_reco_sent(
                since=fy_start,
                before=end_exclusive,
            )
        except Exception as exc:
            logger.exception("Gmail IMAP fetch failed; continuing with DB backfill only")
            raw_msgs = []
            gmail_stats["error"] = str(exc)
        gmail_stats["fetched"] = len(raw_msgs)

        for msg in sorted(raw_msgs, key=lambda m: m.get("advice_date") or date.min):
            advice_date = msg.get("advice_date") or fy_start
            if isinstance(advice_date, datetime):
                advice_date = advice_date.date()
            msg["advice_date"] = advice_date

            if stop_from and advice_date >= stop_from:
                gmail_stats["skipped_after_cutover"] += 1
                continue

            subject = msg.get("subject") or ""
            client_name = parse_client_name_from_subject(subject) or ""
            to_email = msg.get("to_email")
            client_id, resolved_name = resolve_client_from_name_or_email(
                client_name, to_email
            )
            parsed = parse_reco_email_body(msg.get("html") or msg.get("text") or "")
            symbols = set(parsed.get("symbols") or [])

            sid = find_matching_reliable_session(
                client_id=client_id,
                advice_date=advice_date,
                symbols=symbols,
                subject=subject,
            )
            matched = sid is not None
            messages.append(msg)
            match_flags.append(matched)

            if dry_run:
                if matched:
                    gmail_stats["from_db"] += 1
                else:
                    gmail_stats["from_gmail_parse"] += 1
                    if not client_id or not parsed.get("products"):
                        gmail_stats["unmatched"] += 1
                continue

            mid = msg.get("gmail_message_id") or ""
            if matched and sid:
                append_from_session(
                    sid,
                    user_id=user_id,
                    source=SOURCE_DB,
                    gmail_message_id=mid or None,
                    commit=True,
                )
                gmail_stats["from_db"] += 1
            else:
                nature = parsed.get("nature") or ""
                products = parsed.get("products") or ""
                conf = "medium"
                if not client_id or not products:
                    conf = "low"
                    gmail_stats["unmatched"] += 1
                append_from_parsed_email(
                    client_id=client_id,
                    client_name=resolved_name or client_name,
                    advice_date=advice_date,
                    nature=nature,
                    products=products,
                    subject=subject,
                    gmail_message_id=mid or f"fallback-{advice_date}-{client_name}",
                    raw_excerpt=(msg.get("text") or "")[:2000],
                    confidence=conf,
                    user_id=user_id,
                    commit=True,
                )
                gmail_stats["from_gmail_parse"] += 1

        # Detect cutover from chronologically processed match results (before stop_from filter)
        # Re-run match detection across ALL fetched messages for cutover detection
        if raw_msgs:
            all_msgs = []
            all_flags = []
            for msg in sorted(raw_msgs, key=lambda m: m.get("advice_date") or date.min):
                advice_date = msg.get("advice_date") or fy_start
                if isinstance(advice_date, datetime):
                    advice_date = advice_date.date()
                subject = msg.get("subject") or ""
                client_name = parse_client_name_from_subject(subject) or ""
                client_id, _ = resolve_client_from_name_or_email(
                    client_name, msg.get("to_email")
                )
                parsed = parse_reco_email_body(msg.get("html") or msg.get("text") or "")
                sid = find_matching_reliable_session(
                    client_id=client_id,
                    advice_date=advice_date,
                    symbols=set(parsed.get("symbols") or []),
                    subject=subject,
                )
                all_msgs.append({"advice_date": advice_date})
                all_flags.append(sid is not None)

            cutover_c = detect_gmail_cutover_date(all_msgs, all_flags)
            if cutover_c:
                new_stop = cutover_c + timedelta(days=1)
                meta["gmail_cutover_date"] = cutover_c.isoformat()
                meta["gmail_stop_from"] = new_stop.isoformat()
                meta["detected_at"] = datetime.utcnow().isoformat() + "Z"
                meta["fy_label"] = fy_label
                if not dry_run:
                    save_import_meta(meta)
                stop_from = new_stop

    db_stats = backfill_db_sessions(
        fy_start=fy_start, fy_end=fy_end, user_id=user_id, dry_run=dry_run
    )

    return {
        "ok": True,
        "dry_run": dry_run,
        "fy_label": fy_label,
        "gmail_used": use_gmail,
        "gmail": gmail_stats,
        "db_backfill": db_stats,
        "gmail_cutover_date": meta.get("gmail_cutover_date"),
        "gmail_stop_from": meta.get("gmail_stop_from")
        or (stop_from.isoformat() if stop_from else None),
    }
