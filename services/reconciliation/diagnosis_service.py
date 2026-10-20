"""Investigate holding↔trade breaks using trades, reco, CA, cashflow notes, similar tickers."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple


def _as_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return None


def _is_buy(t_type: Optional[str]) -> bool:
    return (t_type or "").strip().upper() in ("BUY", "B", "PURCHASE")


def _is_sell(t_type: Optional[str]) -> bool:
    return (t_type or "").strip().upper() in ("SELL", "S", "SALE")


def corporate_actions_near(
    security_id: int, as_of: date, *, window_days: int = 45
) -> List[Dict[str, Any]]:
    from models import CorporateAction

    start = as_of - timedelta(days=window_days)
    end = as_of + timedelta(days=window_days)
    rows = (
        CorporateAction.query.filter(
            CorporateAction.security_id == int(security_id),
            CorporateAction.is_active.is_(True),
            CorporateAction.action_date >= start,
            CorporateAction.action_date <= end,
        )
        .order_by(CorporateAction.action_date.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": r.id,
            "action_type": r.action_type,
            "action_date": r.action_date.isoformat() if r.action_date else None,
            "ratio": float(r.ratio) if r.ratio is not None else None,
            "description": r.description,
        }
        for r in rows
    ]


def advisor_cashflow_notes_near(
    client_id: int, as_of: date, *, window_days: int = 45
) -> Dict[str, Any]:
    """Approved spreadsheet notes + Cashflow.description around as_of."""
    from models import Cashflow
    from services.cashflow_trade_advisor_notes_service import load_approved_notes

    art = load_approved_notes(int(client_id))
    start = as_of - timedelta(days=window_days)
    end = as_of + timedelta(days=window_days)
    note_rows = []
    if art:
        for r in art.get("rows") or []:
            d = _as_date(r.get("date"))
            if d and start <= d <= end:
                note_rows.append(
                    {
                        "date": d.isoformat(),
                        "amount_inr": r.get("amount_inr"),
                        "source": "advisor_notes_artifact",
                    }
                )

    cf_notes = []
    for cf in Cashflow.query.filter_by(client_id=int(client_id)).all():
        d = _as_date(cf.date)
        if not d or not (start <= d <= end):
            continue
        desc = (cf.description or "").strip()
        if not desc:
            continue
        cf_notes.append(
            {
                "id": cf.id,
                "date": d.isoformat(),
                "amount": float(cf.amount) if cf.amount is not None else None,
                "type": cf.type,
                "description": desc[:300],
                "source": "cashflow_description",
            }
        )

    net_notes = sum(float(r.get("amount_inr") or 0) for r in note_rows)
    summary_bits = []
    if note_rows:
        summary_bits.append(
            f"{len(note_rows)} advisor note row(s) near as-of (net ₹{net_notes:,.0f})"
        )
    if cf_notes:
        summary_bits.append(f"{len(cf_notes)} cashflow description(s) near as-of")
    if not summary_bits:
        if art and art.get("approved"):
            summary_bits.append(
                f"Advisor notes approved through {art.get('reconciled_through')} "
                f"— none in ±{window_days}d window"
            )
        else:
            summary_bits.append("No approved advisor notes / CF descriptions near as-of")

    return {
        "approved": bool(art and art.get("approved")),
        "reconciled_through": (art or {}).get("reconciled_through"),
        "approved_at": (art or {}).get("approved_at"),
        "row_count_saved": len((art or {}).get("rows") or []),
        "notes_near_as_of": note_rows,
        "notes_net_inr": net_notes,
        "cashflow_descriptions_near": cf_notes[:25],
        "summary": "; ".join(summary_bits) + ".",
        "ui_url": f"/clients/{int(client_id)}/cashflow-trade-integrity",
    }


def _client_recipient_emails(client_id: int) -> List[str]:
    """Primary + secondary client emails used to match EmailLog.recipient_email."""
    from models import Client

    c = Client.query.get(int(client_id))
    if not c:
        return []
    out: List[str] = []
    primary = (c.email or "").strip().lower()
    if primary:
        out.append(primary)
    for part in (c.secondary_emails or "").split(","):
        e = part.strip().lower()
        if e and e not in out:
            out.append(e)
    return out


_TAG_RE = re.compile(r"<[^>]+>")
_RECO_LINE_RE = re.compile(
    r"\b(BUY|SELL|HOLD)\b\s+([A-Z][A-Z0-9.&-]{1,20})\b"
    r"(?:\s+(?:qty|quantity|:)?\s*([0-9]+(?:\.[0-9]+)?))?",
    re.I,
)
# Junk from "Total Buy Recos", company names, headers — not NSE tickers
_JUNK_RECO_SYMBOLS = frozenset(
    {
        "RECOS",
        "RECO",
        "TOTAL",
        "COMPANY",
        "BUY",
        "SELL",
        "HOLD",
        "QTY",
        "QUANTITY",
        "PRICE",
        "VALUE",
        "SYMBOL",
        "PORTFOLIO",
        "INVESTMENT",
        "AUROBINDO",  # company word; ticker is AUROPHARMA
        "LIMITED",
        "INDIA",
        "PRIVATE",
    }
)


def _extract_reco_mentions_from_email_text(text: str) -> List[Dict[str, Any]]:
    """
    Lightweight scan of email body/subject — NOT full recommendation understanding.
    Prefer parse_reco_email_body table parse when available.
    """
    if not text:
        return []
    plain = _TAG_RE.sub(" ", text)
    plain = re.sub(r"\s+", " ", plain)
    hits = []
    seen = set()
    for m in _RECO_LINE_RE.finditer(plain):
        action, sym, qty = m.group(1).upper(), m.group(2).upper(), m.group(3)
        if sym in _JUNK_RECO_SYMBOLS or len(sym) < 2:
            continue
        key = (action, sym, qty)
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            {
                "action": action,
                "symbol": sym,
                "quantity": float(qty) if qty else None,
            }
        )
    return hits[:40]


def _dedupe_email_rows(emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prefer IMAP over EmailLog duplicates of the same subject+date."""
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    order: List[Tuple[str, str]] = []
    for e in emails:
        subj = re.sub(r"\s+", " ", (e.get("subject") or "").strip().lower())
        day = str(e.get("sent_at") or "")[:10]
        key = (day, subj)
        src = (e.get("source") or e.get("email_type") or "").lower()
        score = 2 if "imap" in src else 1
        if key not in best:
            best[key] = {**e, "_dedupe_score": score}
            order.append(key)
        elif score > best[key].get("_dedupe_score", 0):
            best[key] = {**e, "_dedupe_score": score}
    out = []
    for key in order:
        row = dict(best[key])
        row.pop("_dedupe_score", None)
        out.append(row)
    return out


def _load_email_body(rel_path: Optional[str], repo_root) -> str:
    """Load archived email HTML/text. Cap is high enough for full Sell tables."""
    if not rel_path:
        return ""
    try:
        from pathlib import Path

        p = Path(repo_root) / rel_path
        if p.is_file():
            # Nov-style reco tables are ~30–60 rows; 12KB cut mid-table and hid
            # KOTAKBANK / HDFCBANK sells from historical batch matching.
            return p.read_text(encoding="utf-8", errors="replace")[:200_000]
    except Exception:
        return ""
    return ""


def _norm_email_symbol(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _symbol_mentioned(symbol: str, email_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return body_reco_hints for this NSE symbol (exact symbol match)."""
    sym = _norm_email_symbol(symbol)
    if not sym:
        return []
    hits = []
    for h in email_row.get("body_reco_hints") or []:
        if _norm_email_symbol(h.get("symbol")) == sym:
            hits.append({**h, "symbol": sym})
    if hits:
        return hits
    blob = email_row.get("_blob") or ""
    # whole-token symbol in blob
    if re.search(rf"\b{re.escape(sym)}\b", blob):
        return [{"action": "?", "symbol": sym, "quantity": None}]
    return []


def _email_hints_from_live_log(
    client_id: int,
    as_of: date,
    *,
    window_days: int = 45,
) -> Dict[str, Any]:
    """Live EmailLog query (fallback when archive empty)."""
    from models import EmailLog
    from sqlalchemy import or_

    addresses = _client_recipient_emails(client_id)
    start = datetime.combine(as_of - timedelta(days=window_days), datetime.min.time())
    end = datetime.combine(as_of + timedelta(days=window_days), datetime.max.time())

    clauses = [EmailLog.client_id == int(client_id)]
    for addr in addresses:
        clauses.append(EmailLog.recipient_email.ilike(f"%{addr}%"))

    rows = (
        EmailLog.query.filter(
            or_(*clauses),
            EmailLog.sent_at >= start,
            EmailLog.sent_at <= end,
        )
        .order_by(EmailLog.sent_at.desc())
        .limit(200)
        .all()
    )
    seen_ids = set()
    unique_rows = []
    for e in rows:
        if e.id in seen_ids:
            continue
        seen_ids.add(e.id)
        unique_rows.append(e)

    emails = []
    reco_emails = 0
    failed = 0
    body_mentions_total = 0
    for e in unique_rows:
        sent = _as_date(e.sent_at)
        etype = (e.email_type or "").strip().lower()
        is_reco = etype in ("recommendation", "recommendations", "reco")
        if is_reco:
            reco_emails += 1
        if (e.status or "").lower() == "failed":
            failed += 1
        subj = (e.subject or "")[:200]
        html = e.email_html or ""
        custom = e.custom_message or ""
        body_hits = _extract_reco_mentions_from_email_text(
            f"{subj}\n{custom}\n{html[:8000]}"
        )
        body_mentions_total += len(body_hits)
        blob = f"{subj} {custom[:500]} {html[:2000]}".upper()
        match_how = "client_id" if e.client_id == int(client_id) else "recipient_email"
        if e.client_id == int(client_id) and addresses:
            recip = (e.recipient_email or "").lower()
            if any(a in recip for a in addresses):
                match_how = "client_id+recipient_email"
        emails.append(
            {
                "id": e.id,
                "sent_at": sent.isoformat() if sent else None,
                "email_type": e.email_type,
                "status": e.status,
                "subject": subj,
                "is_recommendation": is_reco,
                "recipient": (e.recipient_email or "")[:200],
                "match_how": match_how,
                "body_reco_hints": body_hits[:8],
                "_blob": blob,
            }
        )

    if not emails:
        addr_hint = ", ".join(addresses[:3]) if addresses else "no client email on file"
        summary = (
            f"No emails logged near as-of for client_id={client_id} "
            f"or recipient matching [{addr_hint}]."
        )
    else:
        summary = (
            f"{len(emails)} email(s) near as-of from live EmailLog "
            f"({reco_emails} typed recommendation"
            f"{', ' + str(failed) + ' failed' if failed else ''}"
            f"; {body_mentions_total} BUY/SELL symbol hint(s) from body scan)."
        )

    public = [{k: v for k, v in e.items() if k != "_blob"} for e in emails[:40]]
    return {
        "emails": public,
        "_all": emails,
        "client_emails_filter": addresses,
        "total_near": len(emails),
        "recommendation_count": reco_emails,
        "failed_count": failed,
        "body_reco_hint_count": body_mentions_total,
        "summary": summary,
        "source": "email_log",
        "parses_recommendations_from_email": False,
        "needs_drive_details": False,
        "drive_flag": None,
        "note": (
            "Structured recos come from Recommendation table (sent_at). "
            "EmailLog proves delivery; body scan only finds BUY/SELL SYMBOL text hints."
        ),
    }


def _enrich_archive_rows(rows: List[Dict[str, Any]], repo_root) -> List[Dict[str, Any]]:
    emails = []
    for m in rows:
        etype = (m.get("email_type") or "").strip().lower()
        subj = (m.get("subject") or "")[:200]
        is_reco = etype in ("recommendation", "recommendations", "reco") or (
            "portfolio investment recos" in subj.lower()
        )
        body = _load_email_body(m.get("html_path"), repo_root) or _load_email_body(
            m.get("text_path"), repo_root
        )
        body_hits: List[Dict[str, Any]] = []
        # Prefer structured Buy/Sell table parse — skip regex (avoids BUY RECOS / company names)
        structured_ok = False
        if is_reco and body and "<" in body[:500]:
            try:
                from services.gmail_sent_imap_service import parse_reco_email_body

                parsed = parse_reco_email_body(body)
                for line in parsed.get("lines") or []:
                    action = (line.get("action") or "").upper()
                    sym = _norm_email_symbol(line.get("symbol"))
                    if not action or not sym or sym in _JUNK_RECO_SYMBOLS:
                        continue
                    body_hits.append(
                        {
                            "action": action,
                            "symbol": sym,
                            "quantity": line.get("quantity"),
                            "name": line.get("name"),
                            "amount": line.get("amount"),
                        }
                    )
                structured_ok = bool(body_hits)
            except Exception:
                structured_ok = False
        if not structured_ok:
            body_hits = _extract_reco_mentions_from_email_text(f"{subj}\n{body}")
        # de-dupe hints
        uniq = []
        seen_h = set()
        for h in body_hits:
            key = (h.get("action"), h.get("symbol"), h.get("quantity"))
            if key in seen_h:
                continue
            seen_h.add(key)
            uniq.append(h)
        body_hits = uniq
        blob = f"{subj} {body[:4000]}".upper()
        emails.append(
            {
                "id": m.get("message_id") or m.get("email_log_id"),
                "sent_at": m.get("sent_at"),
                "email_type": m.get("email_type") or m.get("source"),
                "source": m.get("source") or m.get("email_type"),
                "status": m.get("status") or "sent",
                "subject": subj,
                "is_recommendation": is_reco,
                "recipient": (m.get("recipient") or "")[:200],
                "match_how": "archive",
                "body_reco_hints": body_hits[:80],
                "drive_file_id": m.get("drive_file_id"),
                "_blob": blob,
            }
        )
    return _dedupe_email_rows(emails)


def _email_hints_from_archive(
    client_id: int,
    as_of: date,
    *,
    window_days: int = 45,
) -> Optional[Dict[str, Any]]:
    """Prefer durable client email archive when present."""
    try:
        from services.client_email_archive_service import (
            archive_status,
            list_archived_emails,
        )
    except Exception:
        return None

    from pathlib import Path

    status = archive_status(client_id)
    if not status.get("has_archive"):
        return None

    repo_root = Path(__file__).resolve().parents[2]
    # Full archive for symbol search; near-as-of for "start here" batch
    full_pack = list_archived_emails(client_id, near_as_of=None, limit=200)
    near_pack = list_archived_emails(
        client_id, near_as_of=as_of, window_days=window_days, limit=80
    )
    addresses = full_pack.get("client_emails") or status.get("client_emails") or []
    all_emails = _enrich_archive_rows(full_pack.get("emails") or [], repo_root)
    near_emails = _enrich_archive_rows(near_pack.get("emails") or [], repo_root)

    near_reco = [e for e in near_emails if e.get("is_recommendation")]
    batch_hints: List[Dict[str, Any]] = []
    seen_h = set()
    for e in near_reco:
        for h in e.get("body_reco_hints") or []:
            key = (h.get("action"), h.get("symbol"), h.get("quantity"))
            if key in seen_h:
                continue
            seen_h.add(key)
            batch_hints.append({**h, "email_sent_at": e.get("sent_at")})

    total_arch = int(full_pack.get("total_archived") or status.get("total_archived") or 0)
    # FYI only — not part of investigation narrative
    fyi_summary = (
        f"Archive has {total_arch} email(s) on file. "
        f"Within ±{window_days}d of as-of {as_of.isoformat()}: "
        f"{len(near_emails)} unique after dedupe "
        f"({len(near_reco)} recommendation); "
        f"{len(batch_hints)} parsed Buy/Sell line(s) in those reco email(s)."
    )
    if status.get("needs_drive_details"):
        fyi_summary += " Drive folder not linked — store Drive details on client."

    public_near = [{k: v for k, v in e.items() if k != "_blob"} for e in near_emails[:20]]
    return {
        "emails": public_near,
        "_all": all_emails,
        "_near": near_emails,
        "batch_hints": batch_hints,
        "client_emails_filter": addresses,
        "total_near": len(near_emails),
        "total_archived": total_arch,
        "window_days": window_days,
        "as_of": as_of.isoformat(),
        "recommendation_count": len(near_reco),
        "failed_count": 0,
        "body_reco_hint_count": len(batch_hints),
        "summary": None,  # investigation uses cluster / sell-gap lines, not archive census
        "fyi": fyi_summary,
        "source": "client_email_archive",
        "last_synced_at": status.get("last_synced_at"),
        "last_sent_at": status.get("last_sent_at"),
        "parses_recommendations_from_email": True,
        "needs_drive_details": bool(status.get("needs_drive_details")),
        "drive_flag": status.get("drive_flag"),
        "drive_folder_url": status.get("drive_folder_url"),
        "note": (
            f"FYI: ±{window_days}d of as-of (not all {total_arch} archived); "
            "includes emails after as-of. Per-security rows only list emails "
            "that mention that symbol."
        ),
    }


def email_hints_near(
    client_id: int,
    as_of: date,
    *,
    window_days: int = 45,
) -> Dict[str, Any]:
    """
    Search all available email nodes for investigation:

    - local client archive (IMAP + prior EmailLog + Drive imports)
    - live EmailLog (merged even when archive exists — fills gaps)
    - Drive folder tree is pulled into the archive before diagnose runs

    Does NOT fully parse recommendation emails into trades.
    """
    sources_checked: List[str] = []
    archived = _email_hints_from_archive(
        client_id, as_of, window_days=window_days
    )
    live = _email_hints_from_live_log(client_id, as_of, window_days=window_days)

    try:
        from services.client_email_archive_service import archive_status

        st = archive_status(client_id)
    except Exception:
        st = {}

    if archived is not None:
        sources_checked.append("local_archive")
    if (live.get("emails") or live.get("_all") or live.get("total_near")):
        sources_checked.append("emaillog")

    if archived is None:
        live["needs_drive_details"] = bool(st.get("needs_drive_details"))
        live["drive_flag"] = st.get("drive_flag")
        live["drive_folder_url"] = st.get("drive_folder_url")
        live["sources_checked"] = sources_checked or ["emaillog"]
        live["source"] = "emaillog"
        return live

    # Merge live EmailLog rows into archive pack (dedupe by subject+date)
    arch_all = list(archived.get("_all") or [])
    arch_near = list(archived.get("_near") or archived.get("emails") or [])
    # Live pack already has body_reco_hints / _blob from EmailLog scan
    live_all = list(live.get("_all") or live.get("emails") or [])
    for e in live_all:
        e.setdefault("source", "emaillog")
    merged_all = _dedupe_email_rows(arch_all + live_all)
    # Live log is near-as-of only — fold into near window
    merged_near = _dedupe_email_rows(arch_near + live_all)

    near_reco = [e for e in merged_near if e.get("is_recommendation")]
    batch_hints: List[Dict[str, Any]] = []
    seen_h = set()
    for e in near_reco:
        for h in e.get("body_reco_hints") or []:
            key = (h.get("action"), h.get("symbol"), h.get("quantity"))
            if key in seen_h:
                continue
            seen_h.add(key)
            batch_hints.append({**h, "email_sent_at": e.get("sent_at")})

    public_near = [
        {k: v for k, v in e.items() if k != "_blob"} for e in merged_near[:20]
    ]
    out = dict(archived)
    out["emails"] = public_near
    out["_all"] = merged_all
    out["_near"] = merged_near
    out["batch_hints"] = batch_hints
    out["total_near"] = len(merged_near)
    out["recommendation_count"] = len(near_reco)
    out["body_reco_hint_count"] = len(batch_hints)
    out["sources_checked"] = sources_checked + (
        ["drive_tree"] if any(
            (e.get("source") or "") == "drive_tree" for e in merged_all
        )
        else []
    )
    out["source"] = "+".join(out["sources_checked"]) or "client_email_archive"
    out["needs_drive_details"] = bool(st.get("needs_drive_details"))
    out["drive_flag"] = st.get("drive_flag")
    out["drive_folder_url"] = st.get("drive_folder_url")
    return out


def call_notes_near(
    client_id: int, as_of: date, *, window_days: int = 45
) -> Dict[str, Any]:
    """CallLog notes near as-of (diagnostic)."""
    from models import CallLog

    start = datetime.combine(as_of - timedelta(days=window_days), datetime.min.time())
    end = datetime.combine(as_of + timedelta(days=window_days), datetime.max.time())
    rows = (
        CallLog.query.filter(
            CallLog.client_id == int(client_id),
            CallLog.call_date >= start,
            CallLog.call_date <= end,
        )
        .order_by(CallLog.call_date.desc())
        .limit(20)
        .all()
    )
    calls = []
    for c in rows:
        note = (c.notes or "").strip()
        if not note:
            continue
        d = _as_date(c.call_date)
        calls.append(
            {
                "id": c.id,
                "date": d.isoformat() if d else None,
                "call_type": c.call_type,
                "notes": note[:280],
            }
        )
    if not calls:
        summary = "No call notes near as-of."
    else:
        summary = f"{len(calls)} call note(s) near as-of."
    return {"calls": calls[:10], "summary": summary}


def similar_ticker_hints(
    *,
    client_id: int,
    break_row: Dict[str, Any],
    as_of: date,
    window_days: int = 14,
) -> List[Dict[str, Any]]:
    """Find other securities with trades near as_of whose symbols look similar."""
    from models import Security, Transaction

    symbol = (break_row.get("symbol") or "").upper().strip()
    if not symbol or len(symbol) < 3:
        return []
    start = datetime.combine(as_of - timedelta(days=window_days), datetime.min.time())
    end = datetime.combine(as_of + timedelta(days=window_days), datetime.max.time())
    txns = (
        Transaction.query.filter(
            Transaction.client_id == int(client_id),
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
        )
        .limit(200)
        .all()
    )
    sid_break = break_row.get("security_id")
    hints = []
    seen = set()
    for t in txns:
        if sid_break and int(t.security_id) == int(sid_break):
            continue
        sec = Security.query.get(t.security_id)
        if not sec or not sec.symbol:
            continue
        other = sec.symbol.upper()
        if other in seen:
            continue
        ratio = SequenceMatcher(None, symbol, other).ratio()
        if ratio < 0.72 and not (
            symbol[:3] == other[:3] and abs(len(symbol) - len(other)) <= 2
        ):
            continue
        seen.add(other)
        td = _as_date(t.transaction_date)
        hints.append(
            {
                "security_id": sec.id,
                "symbol": sec.symbol,
                "similarity": round(ratio, 3),
                "trade_id": t.id,
                "trade_date": td.isoformat() if td else None,
                "trade_qty": float(t.quantity) if t.quantity is not None else None,
                "trade_type": t.type,
                "hint": f"Similar ticker {sec.symbol} traded near break date (score {ratio:.2f})",
            }
        )
    hints.sort(key=lambda h: -h["similarity"])
    return hints[:8]


def reco_hints(
    *,
    client_id: int,
    security_id: Optional[int],
    as_of: date,
    statement_qty: float,
    system_qty: float,
    window_days: int = 45,
) -> Dict[str, Any]:
    """Sent recommendations near as-of for this security — diagnostic only."""
    from models import Recommendation

    empty = {
        "recos": [],
        "hypothesis": None,
        "sent_count": 0,
        "executed_count": 0,
        "pending_sent_count": 0,
        "buy_qty_sent": 0.0,
        "sell_qty_sent": 0.0,
        "summary": "No sent recommendations near as-of for this security.",
    }
    if not security_id:
        return empty
    start = as_of - timedelta(days=window_days)
    end = as_of + timedelta(days=window_days)
    recs = (
        Recommendation.query.filter(
            Recommendation.client_id == int(client_id),
            Recommendation.security_id == int(security_id),
            Recommendation.sent_at.isnot(None),
        )
        .order_by(Recommendation.sent_at.desc())
        .limit(40)
        .all()
    )
    nearby = []
    buy_qty = 0.0
    sell_qty = 0.0
    executed = 0
    pending_sent = 0
    for r in recs:
        sent = _as_date(r.sent_at)
        if not sent or not (start <= sent <= end):
            continue
        action = (getattr(r, "action", None) or "").strip().lower()
        qty = float(r.quantity) if r.quantity is not None else None
        status = (r.status or "").strip().lower()
        if action == "buy" and qty is not None:
            buy_qty += qty
        elif action == "sell" and qty is not None:
            sell_qty += qty
        if status == "executed" or r.executed_at:
            executed += 1
        elif status in ("sent", "pending", "active"):
            pending_sent += 1
        nearby.append(
            {
                "id": r.id,
                "sent_at": sent.isoformat(),
                "action": action or None,
                "quantity": qty,
                "status": r.status,
                "executed_at": _as_date(r.executed_at).isoformat()
                if r.executed_at
                else None,
                "session_id": getattr(r, "session_id", None),
            }
        )

    hypothesis = None
    if nearby:
        q = nearby[0].get("quantity")
        if q is not None:
            if abs(q - float(statement_qty)) <= 0.001 and abs(q - float(system_qty)) > 0.001:
                hypothesis = "BOOKING_ERROR"
            elif abs(q - float(system_qty)) <= 0.001 and abs(q - float(statement_qty)) > 0.001:
                hypothesis = "PHANTOM_OR_MISSED_EXEC"
            elif abs(float(system_qty)) <= 0.001 and abs(float(statement_qty)) <= 0.001:
                hypothesis = "UNEXECUTED_RECO"

    if not nearby:
        summary = empty["summary"]
    else:
        bits = [
            f"{len(nearby)} reco(s) sent in ±{window_days}d",
            f"{executed} executed",
            f"{pending_sent} still open/sent",
        ]
        if buy_qty:
            bits.append(f"buy qty sent {buy_qty:g}")
        if sell_qty:
            bits.append(f"sell qty sent {sell_qty:g}")
        summary = "; ".join(bits) + "."

    return {
        "recos": nearby[:10],
        "hypothesis": hypothesis,
        "sent_count": len(nearby),
        "executed_count": executed,
        "pending_sent_count": pending_sent,
        "buy_qty_sent": buy_qty,
        "sell_qty_sent": sell_qty,
        "summary": summary,
    }


def fetch_security_trades(
    *,
    client_id: int,
    security_id: int,
    as_of: date,
    lookback_days: int = 180,
    lookahead_days: int = 21,
) -> List[Dict[str, Any]]:
    """Trades for one security around as-of (newest first)."""
    from models import Transaction

    start = datetime.combine(as_of - timedelta(days=lookback_days), datetime.min.time())
    end = datetime.combine(as_of + timedelta(days=lookahead_days), datetime.max.time())
    rows = (
        Transaction.query.filter(
            Transaction.client_id == int(client_id),
            Transaction.security_id == int(security_id),
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
        )
        .order_by(Transaction.transaction_date.desc())
        .limit(80)
        .all()
    )
    out = []
    for t in rows:
        td = _as_date(t.transaction_date)
        out.append(
            {
                "id": t.id,
                "date": td.isoformat() if td else None,
                "type": t.type,
                "quantity": float(t.quantity) if t.quantity is not None else None,
                "price": float(t.price) if t.price is not None else None,
                "after_as_of": bool(td and td > as_of),
            }
        )
    return out


def explain_gap_by_trailing_trades(
    *,
    statement_qty: float,
    system_qty: float,
    trades: List[Dict[str, Any]],
    as_of: date,
    max_n: int = 6,
) -> Optional[Dict[str, Any]]:
    """
    If removing the last N book trades (newest first) would make books ≈ statement,
    return that explanation.

    Example (Subhodeep): books 40, holdings 30, last trade BUY 10 in Sep →
    “If you remove the last 1 trade (BUY 10 on …), books match holdings.”
    """
    delta = float(statement_qty) - float(system_qty)
    if abs(delta) <= 0.001:
        return None
    # Signed effect on books: BUY +, SELL −. Undo trailing ⇒ subtract signed sum.
    # Match when signed_sum ≈ system − statement = −delta.
    target = -delta

    ordered = []
    for t in trades or []:
        d = _as_date(t.get("date"))
        if not d or t.get("quantity") is None:
            continue
        # Prefer trades on/before as-of (statement cut-off); allow slight after
        if d > as_of + timedelta(days=3):
            continue
        signed = float(t["quantity"])
        if _is_sell(t.get("type")):
            signed = -signed
        elif not _is_buy(t.get("type")):
            continue
        ordered.append({**t, "_d": d, "_signed": signed})

    if not ordered:
        return None

    def _close(a: float, b: float) -> bool:
        if abs(a - b) <= 0.51:
            return True
        if b and abs(a - b) / abs(b) <= 0.02:
            return True
        return False

    # Try last 1..max_n trades
    running = 0.0
    picked: List[Dict[str, Any]] = []
    for i, t in enumerate(ordered[:max_n]):
        running += float(t["_signed"])
        picked.append(t)
        if _close(running, target):
            labels = [
                f"{(x.get('type') or '?').upper()} {float(x.get('quantity') or 0):g} on {x.get('date')}"
                for x in picked
            ]
            months = sorted(
                {
                    f"{x['_d'].year:04d}-{x['_d'].month:02d}"
                    for x in picked
                }
            )
            n = len(picked)
            return {
                "ok": True,
                "mode": "trailing_trades",
                "trade_count": n,
                "months": months,
                "signed_sum": running,
                "target": target,
                "trades": [
                    {
                        "date": x.get("date"),
                        "type": x.get("type"),
                        "quantity": x.get("quantity"),
                        "id": x.get("id"),
                    }
                    for x in picked
                ],
                "summary": (
                    f"If you remove the last {n} trade(s) "
                    f"({'; '.join(labels)}), books match holdings "
                    f"(gap {delta:+g} = stmt {statement_qty:g} − books {system_qty:g})."
                ),
            }

    # Month cluster: all on/before-as-of trades in the newest trade's month
    newest = ordered[0]["_d"]
    ym = f"{newest.year:04d}-{newest.month:02d}"
    month_trades = [
        t
        for t in ordered
        if t["_d"].year == newest.year and t["_d"].month == newest.month
    ]
    if month_trades:
        msum = sum(float(t["_signed"]) for t in month_trades)
        if _close(msum, target):
            labels = [
                f"{(x.get('type') or '?').upper()} {float(x.get('quantity') or 0):g} on {x.get('date')}"
                for x in month_trades[:8]
            ]
            return {
                "ok": True,
                "mode": "month_cluster",
                "trade_count": len(month_trades),
                "months": [ym],
                "signed_sum": msum,
                "target": target,
                "trades": [
                    {
                        "date": x.get("date"),
                        "type": x.get("type"),
                        "quantity": x.get("quantity"),
                        "id": x.get("id"),
                    }
                    for x in month_trades[:12]
                ],
                "summary": (
                    f"All {ym} trades for this name "
                    f"({'; '.join(labels)}"
                    f"{'…' if len(month_trades) > 8 else ''}) "
                    f"sum to the gap — if you remove those {len(month_trades)} "
                    f"trade(s), books match holdings."
                ),
            }
    return None


def analyze_trade_story(
    *,
    statement_qty: float,
    system_qty: float,
    as_of: date,
    trades: List[Dict[str, Any]],
    gap_days: int = 21,
) -> Dict[str, Any]:
    """
    Pure helper: turn trade list + qty break into a readable story.
    delta = statement - system (negative ⇒ system holds more).
    """
    delta = float(statement_qty) - float(system_qty)
    abs_delta = abs(delta)
    buys_before = []
    sells_before = []
    after = []
    for t in trades:
        d = _as_date(t.get("date"))
        if not d:
            continue
        row = {**t, "_d": d}
        if d > as_of:
            after.append(row)
        elif _is_buy(t.get("type")):
            buys_before.append(row)
        elif _is_sell(t.get("type")):
            sells_before.append(row)

    last_trade_d = None
    last_trade = None
    for t in trades:
        d = _as_date(t.get("date"))
        if d and d <= as_of:
            last_trade_d = d
            last_trade = t
            break

    days_since_trade = (as_of - last_trade_d).days if last_trade_d else None
    gap = bool(days_since_trade is not None and days_since_trade >= gap_days)

    # Match |delta| to a single trade qty (tolerance 0.01 or 1%)
    def _qty_close(a: float, b: float) -> bool:
        if abs(a - b) <= 0.01:
            return True
        if b and abs(a - b) / abs(b) <= 0.02:
            return True
        return False

    matching_sells = [
        t
        for t in sells_before
        if t.get("quantity") is not None and _qty_close(float(t["quantity"]), abs_delta)
    ]
    matching_buys = [
        t
        for t in buys_before
        if t.get("quantity") is not None and _qty_close(float(t["quantity"]), abs_delta)
    ]
    after_buys = [t for t in after if _is_buy(t.get("type"))]
    after_sells = [t for t in after if _is_sell(t.get("type"))]

    # Busy dates (same day multi-leg) before as_of
    by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in trades:
        d = _as_date(t.get("date"))
        if d and d <= as_of:
            by_day[d.isoformat()].append(t)
    busy_days = sorted(
        (
            {
                "date": day,
                "trade_count": len(rows),
                "types": Counter((r.get("type") or "?").upper() for r in rows),
            }
            for day, rows in by_day.items()
            if len(rows) >= 2
        ),
        key=lambda x: -x["trade_count"],
    )[:5]

    trailing = explain_gap_by_trailing_trades(
        statement_qty=statement_qty,
        system_qty=system_qty,
        trades=trades,
        as_of=as_of,
    )

    notes: List[str] = []
    direction = None
    if abs_delta <= 0.001:
        notes.append("Quantities already match within tolerance.")
    elif delta < 0:
        direction = "SYSTEM_HIGHER"
        notes.append(
            f"System holds {abs_delta:g} more than statement "
            f"(stmt {statement_qty:g} vs sys {system_qty:g})."
        )
        if trailing:
            notes.insert(0, "RECENT TRADES: " + trailing["summary"])
        elif matching_sells:
            t = matching_sells[0]
            notes.append(
                f"There is a SELL of {t.get('quantity'):g} on {t.get('date')} "
                f"≈ the gap — check if statement is before that sell settled, "
                f"or sell was booked but demat not updated yet."
            )
        else:
            notes.append(
                f"No book SELL of ~{abs_delta:g} before as-of — possible missed sell "
                f"in the system, or statement is older than a demat transfer/sell."
            )
        if after_sells:
            q = sum(float(t.get("quantity") or 0) for t in after_sells)
            notes.append(
                f"{len(after_sells)} sell(s) after as-of totaling {q:g} — "
                f"as-of may be early vs broker statement."
            )
    else:
        direction = "STATEMENT_HIGHER"
        notes.append(
            f"Statement holds {abs_delta:g} more than system "
            f"(stmt {statement_qty:g} vs sys {system_qty:g})."
        )
        if trailing:
            notes.insert(0, "RECENT TRADES: " + trailing["summary"])
        elif matching_buys:
            t = matching_buys[0]
            notes.append(
                f"There is a BUY of {t.get('quantity'):g} on {t.get('date')} ≈ the gap — "
                f"check booking date vs statement as-of."
            )
        else:
            notes.append(
                f"No book BUY of ~{abs_delta:g} before as-of — possible missed buy "
                f"in the system, or demat credit not yet booked."
            )
        if after_buys:
            q = sum(float(t.get("quantity") or 0) for t in after_buys)
            notes.append(
                f"{len(after_buys)} buy(s) after as-of totaling {q:g}."
            )

    if last_trade:
        notes.append(
            f"Last trade on/before as-of: {last_trade.get('type')} "
            f"{last_trade.get('quantity'):g} on {last_trade.get('date')}"
            + (
                f" ({days_since_trade}d before as-of)."
                if days_since_trade is not None
                else "."
            )
        )
    else:
        notes.append("No trades found for this security in the lookback window.")

    buy_n = len(buys_before)
    sell_n = len(sells_before)
    buy_qty = sum(float(t.get("quantity") or 0) for t in buys_before)
    sell_qty = sum(float(t.get("quantity") or 0) for t in sells_before)

    # New addition: only buys (no sells) and last trade is a relatively recent BUY
    new_addition = bool(
        sell_n == 0
        and buy_n >= 1
        and last_trade
        and _is_buy(last_trade.get("type"))
        and days_since_trade is not None
        and days_since_trade <= 180
    )

    if new_addition:
        notes.append(
            f"Looks like a new / recent addition: last book trade is BUY "
            f"{last_trade.get('quantity'):g} on {last_trade.get('date')} "
            f"({days_since_trade}d before as-of); no sells in lookback — "
            f"not a stale quiet period."
        )
    elif gap and last_trade_d:
        notes.append(
            f"Trade gap: no activity for {days_since_trade} days before as-of "
            f"(since {last_trade_d.isoformat()})."
        )

    if buy_n or sell_n:
        notes.append(
            f"Trades before as-of: {buy_n} buy ({buy_qty:g}) / {sell_n} sell ({sell_qty:g})."
        )

    if busy_days:
        top = busy_days[0]
        notes.append(
            f"Busiest trade day before as-of: {top['date']} "
            f"({top['trade_count']} legs)."
        )

    hyp = None
    if trailing and trailing.get("ok"):
        hyp = "RECENT_TRADES_EXPLAIN_GAP"
    elif new_addition:
        hyp = "NEW_ADDITION"
    elif direction == "SYSTEM_HIGHER" and not matching_sells:
        hyp = "POSSIBLE_MISSED_SELL"
    elif direction == "STATEMENT_HIGHER" and not matching_buys:
        hyp = "POSSIBLE_MISSED_BUY"
    elif matching_sells or matching_buys:
        hyp = "TRADE_DATE_VS_AS_OF"
    elif gap:
        hyp = "STALE_TRADE_GAP"

    return {
        "delta": delta,
        "direction": direction,
        "days_since_last_trade": days_since_trade,
        "trade_gap": gap,
        "last_trade": {
            "date": last_trade.get("date"),
            "type": last_trade.get("type"),
            "quantity": last_trade.get("quantity"),
        }
        if last_trade
        else None,
        "buys_before_count": buy_n,
        "sells_before_count": sell_n,
        "buy_qty_before": buy_qty,
        "sell_qty_before": sell_qty,
        "matching_sells": [
            {"date": t.get("date"), "quantity": t.get("quantity"), "id": t.get("id")}
            for t in matching_sells[:3]
        ],
        "matching_buys": [
            {"date": t.get("date"), "quantity": t.get("quantity"), "id": t.get("id")}
            for t in matching_buys[:3]
        ],
        "trades_after_as_of": len(after),
        "busy_days": busy_days,
        "recent_trades": [
            {
                "date": t.get("date"),
                "type": t.get("type"),
                "quantity": t.get("quantity"),
                "after_as_of": t.get("after_as_of"),
            }
            for t in trades[:8]
        ],
        "gap_explained_by_trades": trailing,
        "hypothesis": hyp,
        "notes": notes,
        "new_addition": new_addition,
    }


def _compose_investigation_notes(diagnosis: Dict[str, Any]) -> List[str]:
    """Flatten structured diagnosis into advisor-facing bullets.

    Order: email / reco first (starting point once a qty gap exists), then trades.
    Per-security email notes only include messages that mention this symbol.
    """
    notes: List[str] = []
    # 1) Emails for THIS symbol only
    em = diagnosis.get("emails") or {}
    sym_hits = em.get("symbol_hints") or []
    if diagnosis.get("in_email_cluster"):
        notes.append(
            "EMAIL CLUSTER: this name is part of the 1–2 reco email(s) that explain "
            "most qty gaps — investigate that recommendation batch first."
        )
    if em.get("for_symbol"):
        if sym_hits:
            notes.append(
                f"EMAIL for {em['for_symbol']}: found in archive — "
                + "; ".join(
                    f"{h.get('action')} {h.get('symbol')}"
                    + (f" qty {h.get('quantity'):g}" if h.get("quantity") is not None else "")
                    + (f" (email {h.get('email_sent_at')})" if h.get("email_sent_at") else "")
                    for h in sym_hits[:6]
                )
            )
            for e in (em.get("emails") or [])[:3]:
                only = [
                    h
                    for h in (e.get("body_reco_hints") or [])
                    if (h.get("symbol") or "").upper() == em["for_symbol"]
                ]
                line = ", ".join(
                    f"{h.get('action')} {h.get('symbol')}"
                    + (f" {h.get('quantity'):g}" if h.get("quantity") is not None else "")
                    for h in only[:4]
                )
                notes.append(
                    f"Email {e.get('sent_at')}: {(e.get('subject') or '')[:70]}"
                    + (f" — {line}" if line else "")
                )
        else:
            notes.append(
                f"EMAIL for {em['for_symbol']}: no Sent email in archive mentions this symbol "
                f"(searched {em.get('archive_searched', 0)} archived message(s))."
            )
    elif em.get("summary"):
        notes.append("EMAIL: " + em["summary"])
    if em.get("needs_drive_details") and em.get("drive_flag"):
        notes.append(em["drive_flag"])

    # 1b) If reco booked, would holdings match?
    ira = diagnosis.get("if_reco_applied") or {}
    if ira.get("message"):
        notes.append(("IF RECO BOOKED: " if ira.get("explains_gap") else "RECO vs GAP: ") + ira["message"])

    # 1c) 3-way email ↔ reco ↔ trade
    tw = diagnosis.get("three_way") or {}
    for n in (tw.get("notes") or [])[:6]:
        notes.append(n)

    # 2) Structured recommendations
    reco = diagnosis.get("reco") or {}
    if reco.get("sent_count"):
        notes.append(
            "RECO: " + (reco.get("summary") or f"{reco['sent_count']} reco(s) sent near as-of.")
        )
        for r in (reco.get("recos") or [])[:4]:
            bits = [r.get("action") or "?", f"qty {r.get('quantity')}", f"sent {r.get('sent_at')}"]
            if r.get("status"):
                bits.append(f"status {r['status']}")
            if r.get("executed_at"):
                bits.append(f"executed {r['executed_at']}")
            notes.append("Reco: " + ", ".join(str(b) for b in bits if b is not None))
    elif reco.get("summary"):
        notes.append("RECO: " + reco["summary"])

    # 3) Trade books / qty story — trailing trades that cover the gap first
    story = diagnosis.get("trade_story") or {}
    gxt = story.get("gap_explained_by_trades") or {}
    if gxt.get("ok") and gxt.get("summary"):
        # Already often first in story.notes; ensure it is not buried under emails
        if not any(
            isinstance(n, str) and n.startswith("RECENT TRADES:") for n in notes
        ):
            notes.insert(0, "RECENT TRADES: " + gxt["summary"])
    for n in story.get("notes") or []:
        if isinstance(n, str) and n.startswith("RECENT TRADES:") and any(
            isinstance(x, str) and x.startswith("RECENT TRADES:") for x in notes
        ):
            continue  # already promoted
        notes.append(n)

    an = diagnosis.get("advisor_notes") or {}
    if an.get("summary"):
        notes.append(an["summary"])
    if an.get("notes_near_as_of"):
        for r in an["notes_near_as_of"][:3]:
            amt = r.get("amount_inr")
            notes.append(
                f"Advisor note {r.get('date')}: "
                + (f"₹{float(amt):,.0f}" if amt is not None else "amount n/a")
            )
    if an.get("cashflow_descriptions_near"):
        for first in an["cashflow_descriptions_near"][:2]:
            notes.append(
                f"Cashflow {first.get('date')} {first.get('type')}: "
                f"{(first.get('description') or '')[:120]}"
            )
    calls = diagnosis.get("call_notes") or {}
    if calls.get("calls"):
        notes.append(calls.get("summary") or "Call notes near as-of.")
        for c in calls["calls"][:2]:
            notes.append(f"Call {c.get('date')}: {(c.get('notes') or '')[:140]}")
    cas = diagnosis.get("corporate_actions") or []
    if cas:
        notes.append(
            "Corporate actions: "
            + "; ".join(
                f"{c.get('action_type')} {c.get('action_date')}"
                + (f" ratio {c.get('ratio')}" if c.get("ratio") is not None else "")
                for c in cas[:4]
            )
        )
    sims = diagnosis.get("similar_tickers") or []
    if sims:
        notes.append(
            "Similar tickers traded near date: "
            + ", ".join(s.get("symbol") or "?" for s in sims[:4])
        )
    if diagnosis.get("timing_batch") and diagnosis.get("timing_message"):
        notes.insert(0, diagnosis["timing_message"])
    return notes


def infer_primary_cause(
    *,
    as_of: date,
    matched_count: int,
    break_count: int,
    system_higher: int,
    statement_higher: int,
    total_abs_delta: float,
    timing: Optional[Dict[str, Any]],
    email_cluster: Optional[Dict[str, Any]],
    reco_if_executed_match: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    One portfolio-level verdict so ops are not lost in per-name email noise.
    """
    total = matched_count + break_count
    if break_count <= 0:
        return {
            "code": "CLEAN",
            "title": "Holdings match",
            "summary": "No qty breaks.",
            "checks": [],
        }

    cluster = email_cluster or {}
    if_exec = reco_if_executed_match or []
    timing = timing or {}

    # 1) Settlement lag already proven
    if timing.get("applied"):
        return {
            "code": "SETTLEMENT_LAG",
            "title": "Settlement / as-of lag (portfolio-wide)",
            "summary": timing.get("message")
            or "Shifting as-of clears most lines — not single-stock booking errors.",
            "checks": [],
        }

    # 2) 1–2 reco emails explain most gaps
    if cluster.get("found") and cluster.get("message"):
        return {
            "code": "RECO_EMAIL_CLUSTER",
            "title": "Most gaps tied to 1–2 recommendation emails",
            "summary": cluster["message"],
            "checks": [],
        }

    # 3) Many names close if recommended trades were booked
    if len(if_exec) >= max(3, break_count // 3):
        return {
            "code": "MISSED_RECO_EXECUTION",
            "title": "Recommended trades would close many gaps if booked",
            "summary": (
                f"{len(if_exec)} of {break_count} breaks close when recommended "
                f"BUY/SELL qty is applied to books — focus on unbooked/missed execution."
            ),
            "checks": [r.get("symbol") for r in if_exec[:12]],
        }

    # 4) Portfolio-wide: system long everywhere (this Raghu CDSL case)
    if (
        break_count >= 5
        and system_higher >= break_count
        and statement_higher == 0
        and (total == 0 or system_higher / max(break_count, 1) >= 0.9)
    ):
        checks = [
            "This is one demat/broker statement vs full Inertia books — "
            "shares may sit in another DP/account for the same names.",
            "Sells/transfers out of this demat were never booked (no near-as-of SELL "
            "emails for most gaps; as-of shift does not clear).",
            "Confirm statement as-of/header is the free-balance cut-off you intend "
            f"(current as-of {as_of.isoformat()}; short settlement lag already tested).",
        ]
        return {
            "code": "PORTFOLIO_BOOKS_LONG",
            "title": "Books systematically long vs this statement",
            "summary": (
                f"Every break is system > statement ({system_higher}/{break_count}; "
                f"statement higher on 0; total |Δ qty| {total_abs_delta:g}; "
                f"{matched_count} names already match). "
                f"Near-as-of BUY emails and old per-name BUY lines do not explain "
                f"system-long gaps. This is a portfolio / account / booking issue, "
                f"not a single-stock reco miss."
            ),
            "checks": checks,
        }

    # 5) Portfolio-wide statement long
    if (
        break_count >= 5
        and statement_higher >= break_count
        and system_higher == 0
    ):
        return {
            "code": "PORTFOLIO_STATEMENT_LONG",
            "title": "Statement systematically long vs books",
            "summary": (
                f"Every break is statement > system ({statement_higher}/{break_count}). "
                f"Likely missed buys in books, or statement includes another account."
            ),
            "checks": [
                "Check recent BUY reco emails / unbooked buys.",
                "Confirm statement is the same demat Inertia tracks.",
            ],
        }

    return {
        "code": "MIXED_BREAKS",
        "title": "Mixed per-security breaks",
        "summary": (
            f"{break_count} breaks (system higher {system_higher}, "
            f"statement higher {statement_higher}). Investigate security rows; "
            f"no single portfolio-wide pattern dominates."
        ),
        "checks": [],
    }


def build_overall_summary(
    *,
    as_of: date,
    matched_count: int,
    breaks: List[Dict[str, Any]],
    timing: Optional[Dict[str, Any]] = None,
    statement_security_count: int = 0,
    client_emails: Optional[Dict[str, Any]] = None,
    client_notes: Optional[Dict[str, Any]] = None,
    client_calls: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Portfolio-level story across all breaks (pure from diagnosed rows)."""
    status_counts: Counter = Counter()
    hyp_counts: Counter = Counter()
    system_higher = 0
    statement_higher = 0
    missing_sys = 0
    missing_stmt = 0
    total_abs_delta = 0.0
    busy_day_counter: Counter = Counter()
    gap_names: List[str] = []
    new_addition_names: List[str] = []
    missed_sell_names: List[str] = []
    missed_buy_names: List[str] = []
    missing_stmt_syms: List[str] = []
    recent_trade_explain: List[Dict[str, Any]] = []
    recent_trade_by_month: Dict[str, List[str]] = defaultdict(list)
    reco_sent_total = 0
    reco_executed_total = 0
    reco_open_total = 0
    sell_qty_sent = 0.0
    buy_qty_sent = 0.0

    for br in breaks:
        status_counts[br.get("status") or "UNKNOWN"] += 1
        d = br.get("diagnosis") or {}
        hyp_counts[d.get("top_hypothesis") or "UNKNOWN"] += 1
        delta = float(br.get("delta") or 0)
        total_abs_delta += abs(delta)
        if delta < -0.001:
            system_higher += 1
        elif delta > 0.001:
            statement_higher += 1
        if br.get("status") == "MISSING_IN_SYSTEM":
            missing_sys += 1
        elif br.get("status") == "MISSING_IN_STATEMENT":
            missing_stmt += 1
            missing_stmt_syms.append(br.get("symbol") or str(br.get("security_id")))
        story = d.get("trade_story") or {}
        sym = br.get("symbol") or str(br.get("security_id"))
        if story.get("new_addition") or story.get("hypothesis") == "NEW_ADDITION":
            new_addition_names.append(sym)
        elif story.get("trade_gap"):
            gap_names.append(sym)
        if story.get("hypothesis") == "POSSIBLE_MISSED_SELL":
            missed_sell_names.append(sym)
        if story.get("hypothesis") == "POSSIBLE_MISSED_BUY":
            missed_buy_names.append(sym)
        gxt = story.get("gap_explained_by_trades") or {}
        if gxt.get("ok"):
            recent_trade_explain.append(
                {
                    "symbol": sym,
                    "delta": br.get("delta"),
                    "summary": gxt.get("summary"),
                    "trade_count": gxt.get("trade_count"),
                    "months": gxt.get("months") or [],
                    "mode": gxt.get("mode"),
                    "trades": gxt.get("trades") or [],
                }
            )
            for ym in gxt.get("months") or ["?"]:
                recent_trade_by_month[ym].append(sym)
        for day in story.get("busy_days") or []:
            busy_day_counter[day["date"]] += day.get("trade_count") or 1
        reco = d.get("reco") or {}
        reco_sent_total += int(reco.get("sent_count") or 0)
        reco_executed_total += int(reco.get("executed_count") or 0)
        reco_open_total += int(reco.get("pending_sent_count") or 0)
        sell_qty_sent += float(reco.get("sell_qty_sent") or 0)
        buy_qty_sent += float(reco.get("buy_qty_sent") or 0)

    break_count = len(breaks)
    total_names = matched_count + break_count
    match_pct = round(100.0 * matched_count / total_names, 1) if total_names else 100.0

    em = client_emails or {}
    fyi: List[str] = []
    if em.get("fyi"):
        fyi.append(em["fyi"])
    if em.get("note"):
        fyi.append(em["note"])

    primary = infer_primary_cause(
        as_of=as_of,
        matched_count=matched_count,
        break_count=break_count,
        system_higher=system_higher,
        statement_higher=statement_higher,
        total_abs_delta=total_abs_delta,
        timing=timing,
        email_cluster=em.get("email_cluster"),
        reco_if_executed_match=em.get("reco_if_executed_match"),
    )

    headlines: List[str] = []
    headlines.append(
        f"PRIMARY: {primary.get('title')} — {primary.get('summary')}"
    )
    for chk in (primary.get("checks") or [])[:5]:
        headlines.append(f"Check: {chk}")
    headlines.append(
        f"As-of {as_of.isoformat()}: {matched_count} matched, {break_count} breaks "
        f"({match_pct}% clean"
        + (
            f" of {statement_security_count} statement names"
            if statement_security_count
            else ""
        )
        + ")."
    )
    if recent_trade_explain:
        headlines.append(
            f"RECENT TRADES explain {len(recent_trade_explain)} break(s): "
            "removing last trade(s) on those names makes books match holdings."
        )
        for ym, syms in sorted(recent_trade_by_month.items(), reverse=True)[:4]:
            headlines.append(
                f"  {ym}: {', '.join(syms[:12])}"
                + (f" (+{len(syms)-12})" if len(syms) > 12 else "")
            )
        for row in recent_trade_explain[:6]:
            headlines.append(
                f"  · {row['symbol']} Δ {row.get('delta')}: {row.get('summary')}"
            )

    # Investigation detail — skip email-buy noise when books are portfolio-long
    portfolio_long = primary.get("code") == "PORTFOLIO_BOOKS_LONG"
    if break_count:
        cluster = em.get("email_cluster") or {}
        if cluster.get("found") and cluster.get("message") and not portfolio_long:
            headlines.append("EMAIL CLUSTER (start here): " + cluster["message"])
            if cluster.get("explained_symbols"):
                headlines.append(
                    "Names explained by that email cluster: "
                    + ", ".join(cluster["explained_symbols"][:20])
                    + (
                        f" (+{len(cluster['explained_symbols'])-20} more)"
                        if len(cluster["explained_symbols"]) > 20
                        else ""
                    )
                    + "."
                )
        # Post-as-of BUY tables cannot explain system-long — keep in FYI only
        if em.get("batch_hint_lines"):
            batch_line = (
                "Near-as-of reco email Buy/Sell table (parsed qty): "
                + "; ".join(em["batch_hint_lines"][:20])
                + ("…" if len(em.get("batch_hint_lines") or []) > 20 else "")
                + " — recommended trades in that email, not holdings."
            )
            if portfolio_long:
                fyi.append(
                    batch_line
                    + " (Post-as-of / BUY-only lines do not explain system-long gaps.)"
                )
            else:
                headlines.append(batch_line)
        sell_long = em.get("emailed_sell_still_long_detail") or []
        if sell_long:
            parts = []
            for row in sell_long[:8]:
                sq = row.get("sell_qty")
                dlt = row.get("delta")
                bit = row.get("symbol") or "?"
                if sq is not None:
                    bit += f" SELL {sq:g}"
                if dlt is not None:
                    bit += f" (Δ {dlt:g}"
                    if row.get("explains_gap"):
                        bit += " — explains gap if booked"
                    else:
                        bit += " — does not fully explain"
                    bit += ")"
                parts.append(bit)
            headlines.append(
                "Email/reco SELL vs system still long: " + "; ".join(parts) + "."
            )
        elif em.get("emailed_sell_still_long"):
            # legacy symbol list
            sample = ", ".join(em["emailed_sell_still_long"][:10])
            headlines.append(
                f"Email recommended SELL but system still long vs statement: {sample}."
            )
        if_applied = em.get("reco_if_executed_match") or []
        if if_applied:
            parts = [
                f"{r.get('symbol')} ({r.get('message')})"
                for r in if_applied[:8]
                if r.get("message")
            ]
            if parts:
                headlines.append(
                    "If recommended trades were booked, these gaps close: "
                    + "; ".join(parts)
                    + "."
                )
        if em.get("missed_sell_no_email"):
            sample = ", ".join(em["missed_sell_no_email"][:10])
            more = (
                f" (+{len(em['missed_sell_no_email'])-10} more)"
                if len(em["missed_sell_no_email"]) > 10
                else ""
            )
            headlines.append(
                f"Possible missed sells with NO matching SELL in Sent archive for: "
                f"{sample}{more}."
            )
        if em.get("emailed_break_symbols") is not None:
            headlines.append(
                f"Break names in near-as-of reco email table: "
                f"{len(em.get('emailed_break_symbols') or [])} of {break_count}."
            )
        if em.get("needs_drive_details"):
            fyi.append(
                em.get("drive_flag")
                or "Client Google Drive folder is not linked — add Drive details."
            )
    if system_higher or statement_higher:
        headlines.append(
            f"Direction: system higher on {system_higher} name(s); "
            f"statement higher on {statement_higher} name(s) "
            f"(total |Δ qty| {total_abs_delta:g})."
        )
    if missing_stmt:
        sample = ", ".join(missing_stmt_syms[:8])
        headlines.append(
            f"{missing_stmt} name(s) in system but not on this statement"
            + (f" ({sample})" if sample else "")
            + " — may be sold/transferred after cut-off, wrong as-of, "
            f"or client holdings outside this demat/broker (non-Inertia / other account)."
        )
    if missing_sys:
        headlines.append(
            f"{missing_sys} name(s) on statement but zero in system — "
            f"possible missed buys or mapping still wrong."
        )
    if missed_sell_names:
        sample = ", ".join(missed_sell_names[:8])
        more = f" (+{len(missed_sell_names)-8} more)" if len(missed_sell_names) > 8 else ""
        headlines.append(
            f"Possible missed sells (system still long, no matching sell in books): "
            f"{sample}{more}."
        )
    if missed_buy_names:
        sample = ", ".join(missed_buy_names[:8])
        more = f" (+{len(missed_buy_names)-8} more)" if len(missed_buy_names) > 8 else ""
        headlines.append(
            f"Possible missed buys (statement long, no matching buy in books): "
            f"{sample}{more}."
        )
    if new_addition_names:
        sample = ", ".join(new_addition_names[:8])
        headlines.append(
            f"New / recent additions (recent BUY, no sells — not a stale quiet period): "
            f"{sample}."
        )
    if gap_names:
        sample = ", ".join(gap_names[:8])
        headlines.append(
            f"{len(gap_names)} break name(s) have a long quiet period before as-of "
            f"(no recent trades; excluding new additions): {sample}."
        )
    if busy_day_counter:
        top_days = busy_day_counter.most_common(5)
        headlines.append(
            "Busiest trade days among break names: "
            + "; ".join(f"{d} ({n} legs)" for d, n in top_days)
            + "."
        )
    if reco_sent_total:
        headlines.append(
            f"Recommendations near as-of on break names: {reco_sent_total} sent, "
            f"{reco_executed_total} executed, {reco_open_total} still open"
            + (
                f"; sell qty sent {sell_qty_sent:g}"
                if sell_qty_sent
                else ""
            )
            + (f"; buy qty sent {buy_qty_sent:g}" if buy_qty_sent else "")
            + "."
        )
    else:
        headlines.append(
            "No sent recommendations near as-of on the break names — "
            "breaks are more likely booking/as-of/trade-gap issues than unexecuted recos."
        )
    notes = client_notes or {}
    if notes.get("summary"):
        headlines.append(notes["summary"])
    calls = client_calls or {}
    if calls.get("calls"):
        headlines.append(calls.get("summary") or "Call notes near as-of.")
        for c in calls["calls"][:2]:
            headlines.append(f"Call {c.get('date')}: {(c.get('notes') or '')[:120]}")
    if timing and timing.get("applied"):
        headlines.insert(1, timing.get("message") or "Settlement lag looks portfolio-wide.")
    elif timing and timing.get("message"):
        pass
    else:
        if (
            not portfolio_long
            and break_count
            and system_higher > statement_higher * 2
            and system_higher >= 3
        ):
            headlines.append(
                "Pattern leans portfolio-wide: many names where system > statement — "
                "check whether statement as-of is earlier than booked sells, "
                "or a batch of sells was missed."
            )

    top_hyp = [
        {"hypothesis": h, "count": c} for h, c in hyp_counts.most_common(8)
    ]
    return {
        "as_of": as_of.isoformat(),
        "matched_count": matched_count,
        "break_count": break_count,
        "match_pct": match_pct,
        "status_counts": dict(status_counts),
        "hypothesis_counts": top_hyp,
        "system_higher_count": system_higher,
        "statement_higher_count": statement_higher,
        "missing_in_system_count": missing_sys,
        "missing_in_statement_count": missing_stmt,
        "total_abs_delta_qty": total_abs_delta,
        "busy_trade_days": [
            {"date": d, "leg_count": n} for d, n in busy_day_counter.most_common(8)
        ],
        "possible_missed_sells": missed_sell_names,
        "possible_missed_buys": missed_buy_names,
        "trade_gap_symbols": gap_names,
        "recent_trade_explain": recent_trade_explain[:40],
        "recent_trade_explain_by_month": {
            ym: syms for ym, syms in sorted(recent_trade_by_month.items(), reverse=True)
        },
        "reco_totals": {
            "sent": reco_sent_total,
            "executed": reco_executed_total,
            "open": reco_open_total,
            "buy_qty_sent": buy_qty_sent,
            "sell_qty_sent": sell_qty_sent,
        },
        "emails": em,
        "advisor_notes": notes,
        "call_notes": calls,
        "headlines": headlines,
        "fyi": fyi,
        "primary_cause": primary,
        "timing": timing,
    }


def _cashflow_month_cues(
    notes_pack: Dict[str, Any], as_of: date
) -> List[Dict[str, Any]]:
    """Month buckets near as-of from advisor CF notes / descriptions."""
    cues: List[Dict[str, Any]] = []
    months: Dict[str, float] = defaultdict(float)
    for r in notes_pack.get("notes_near_as_of") or []:
        d = _as_date(r.get("date") or r.get("txn_date"))
        if not d:
            continue
        ym = f"{d.year:04d}-{d.month:02d}"
        amt = r.get("amount_inr") or r.get("amount") or r.get("net_inr")
        try:
            months[ym] += float(amt or 0)
        except Exception:
            months[ym] += 0.0
    # Also tag as-of month even if empty (for alignment checks)
    as_ym = f"{as_of.year:04d}-{as_of.month:02d}"
    if as_ym not in months:
        months[as_ym] = 0.0
    for ym, net in sorted(months.items(), reverse=True)[:6]:
        cues.append(
            {
                "month": ym,
                "net_inr": net,
                "detail": f"Advisor/CF note activity {ym}: net ≈ ₹{net:,.0f}",
                "summary": f"{ym} net ₹{net:,.0f}",
            }
        )
    if notes_pack.get("summary"):
        cues.append(
            {
                "month": as_ym,
                "detail": str(notes_pack.get("summary"))[:200],
                "summary": "CF notes summary",
            }
        )
    return cues


def _price_accuracy_cues_for_breaks(
    breaks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Read latest price-accuracy findings for break security_ids (no rescan)."""
    sids = {
        int(b["security_id"])
        for b in breaks
        if b.get("security_id") is not None
    }
    if not sids:
        return []
    try:
        from services.price_accuracy_service import latest_run_snapshot

        snap = latest_run_snapshot()
    except Exception:
        return []
    if not snap:
        return []
    out = []
    for f in snap.get("findings") or []:
        try:
            sid = int(f.get("security_id"))
        except Exception:
            continue
        if sid not in sids:
            continue
        out.append(f)
        if len(out) >= 40:
            break
    return out


def _holdings_notes_cues(
    client_id: int,
    matched_rows: List[Dict[str, Any]],
    breaks: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Approved advisor holdings paste vs books → drift evidence + meta."""
    meta: Dict[str, Any] = {"has_notes": False}
    try:
        from services.reconciliation.advisor_holdings_notes_service import (
            load_approved_notes,
        )
    except Exception:
        return [], meta
    art = load_approved_notes(client_id)
    if not art:
        return [], meta
    drift = list(art.get("drift") or [])
    if art.get("rows"):
        # Always re-compare to current books (paste may have been approved earlier)
        sys_map: Dict[str, float] = {}
        for r in matched_rows:
            sym = (r.get("symbol") or "").upper().strip()
            if sym:
                sys_map[sym] = float(r.get("system_qty") or r.get("statement_qty") or 0)
        for b in breaks:
            sym = (b.get("symbol") or "").upper().strip()
            if sym:
                sys_map[sym] = float(b.get("system_qty") or 0)
        from services.reconciliation.advisor_holdings_notes_service import (
            compare_to_system,
        )

        drift = compare_to_system(art.get("rows") or [], sys_map)
    meta = {
        "has_notes": True,
        "approved_at": art.get("approved_at"),
        "sync_through": art.get("sync_through"),
        "row_count": art.get("row_count") or len(art.get("rows") or []),
        "drift_count": len(drift),
        "matches_books": len(drift) == 0 and bool(art.get("rows")),
    }
    return drift, meta


def diagnose_breaks(
    *,
    client_id: int,
    as_of: date,
    breaks: List[Dict[str, Any]],
    timing: Optional[Dict[str, Any]] = None,
    matched_count: int = 0,
    matched_rows: Optional[List[Dict[str, Any]]] = None,
    statement_security_count: int = 0,
    progress_cb=None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Attach investigation hits to each break row + overall summary."""

    def _prog(pct: int, label: str, stage: str = "") -> None:
        if progress_cb:
            try:
                progress_cb(pct, label, stage)
            except Exception:
                pass

    _prog(64, "Loading advisor notes & archived emails…", "diagnose_load")
    notes_pack = advisor_cashflow_notes_near(client_id, as_of)
    emails_pack = email_hints_near(client_id, as_of)
    calls_pack = call_notes_near(client_id, as_of)

    def _email_slice_for_symbol(symbol: Optional[str]) -> Dict[str, Any]:
        """Only emails that mention this symbol — never dump unrelated batch emails."""
        sym = (symbol or "").upper().strip()
        all_emails = list(emails_pack.get("_all") or [])
        matching_emails = []
        symbol_hints: List[Dict[str, Any]] = []
        for e in all_emails:
            hits = _symbol_mentioned(sym, e) if sym else []
            if not hits:
                continue
            row = {k: v for k, v in e.items() if k != "_blob"}
            row["mentions_symbol"] = True
            row["body_reco_hints"] = [
                h
                for h in (e.get("body_reco_hints") or [])
                if (h.get("symbol") or "").upper() == sym
            ] or hits
            matching_emails.append(row)
            for h in row["body_reco_hints"]:
                symbol_hints.append({**h, "email_sent_at": e.get("sent_at")})
        if sym and matching_emails:
            summary = (
                f"{len(matching_emails)} archived email(s) mention {sym} "
                f"({len(symbol_hints)} BUY/SELL line(s) for {sym})."
            )
        elif sym:
            summary = (
                f"No archived email mentions {sym} "
                f"(searched {len(all_emails)} unique message(s) in archive)."
            )
        else:
            summary = emails_pack.get("summary") or "No symbol for email filter."
        return {
            "emails": matching_emails[:8],
            "for_symbol": sym or None,
            "symbol_hints": symbol_hints[:12],
            "archive_searched": len(all_emails),
            "total_near": len(matching_emails),
            "recommendation_count": sum(
                1 for e in matching_emails if e.get("is_recommendation")
            ),
            "failed_count": 0,
            "summary": summary,
            "needs_drive_details": emails_pack.get("needs_drive_details"),
            "drive_flag": emails_pack.get("drive_flag"),
        }

    # Batch-level: which break symbols appear in near-as-of reco email bodies
    batch_hints = list(emails_pack.get("batch_hints") or [])
    batch_by_sym = defaultdict(list)
    for h in batch_hints:
        batch_by_sym[(h.get("symbol") or "").upper()].append(h)

    from services.reconciliation.reco_delta_match import (
        find_best_historical_sell_batch,
        find_explaining_email_cluster,
        if_reco_applied_explains_gap,
        three_way_explain,
    )

    def _email_as_cluster_row(e: Dict[str, Any]) -> Dict[str, Any]:
        lines = e.get("body_reco_hints") or []
        # Normalize NSE:SYM → SYM for matching
        norm_lines = []
        for h in lines:
            norm_lines.append(
                {**h, "symbol": _norm_email_symbol(h.get("symbol"))}
            )
        return {
            "id": e.get("id"),
            "sent_at": e.get("sent_at"),
            "subject": e.get("subject"),
            "lines": norm_lines,
            "body_reco_hints": norm_lines,
        }

    # Near-as-of cluster (buys/sells close to statement date)
    near_for_cluster = list(emails_pack.get("_near") or emails_pack.get("emails") or [])
    cluster_emails = [
        _email_as_cluster_row(e)
        for e in near_for_cluster
        if e.get("is_recommendation") or (e.get("body_reco_hints") or [])
    ]
    email_cluster = find_explaining_email_cluster(cluster_emails, breaks)

    # Full archive historical SELL batch (e.g. Nov 2025) — beyond ±45d
    all_for_sell = list(emails_pack.get("_all") or near_for_cluster)
    sell_batch_emails = [
        _email_as_cluster_row(e)
        for e in all_for_sell
        if e.get("is_recommendation") or (e.get("body_reco_hints") or [])
    ]
    historical_sell_batch = find_best_historical_sell_batch(sell_batch_emails, breaks)

    # Primary advisor process: mismatch → email → fixes → remaining → repeat
    # Prefer Sent archive; also feed DB recommendation sessions (covers months
    # missing from IMAP lookback / Drive Emails/, e.g. Jun 2026 still in DB).
    from services.reconciliation.simple_investigate import run_simple_loop
    from services.reconciliation.reco_sessions_as_emails import (
        recommendation_sessions_as_emails,
    )

    try:
        db_session_emails = recommendation_sessions_as_emails(
            int(client_id), lookback_days=400, as_of=as_of
        )
    except Exception:
        db_session_emails = []
    simple_emails = list(sell_batch_emails or []) + list(cluster_emails or [])
    # Dedup by id; DB sessions fill gaps when archive has no Jun etc.
    seen_ids = {e.get("id") for e in simple_emails if e.get("id")}
    for e in db_session_emails:
        eid = e.get("id")
        if eid and eid in seen_ids:
            continue
        # Skip if same subject+sent already present from IMAP
        key = ((e.get("subject") or ""), (e.get("sent_at") or "")[:10])
        if any(
            ((x.get("subject") or ""), (x.get("sent_at") or "")[:10]) == key
            for x in simple_emails
        ):
            continue
        simple_emails.append(e)
        if eid:
            seen_ids.add(eid)
    simple_loop = run_simple_loop(breaks, simple_emails)

    out = []
    for br in breaks:
        sid = br.get("security_id")
        symbol = br.get("symbol")
        trades = (
            fetch_security_trades(
                client_id=client_id, security_id=int(sid), as_of=as_of
            )
            if sid
            else []
        )
        story = analyze_trade_story(
            statement_qty=float(br.get("statement_qty") or 0),
            system_qty=float(br.get("system_qty") or 0),
            as_of=as_of,
            trades=trades,
        )
        email_slice = _email_slice_for_symbol(symbol)
        reco_pack = reco_hints(
            client_id=client_id,
            security_id=int(sid) if sid else None,
            as_of=as_of,
            statement_qty=float(br.get("statement_qty") or 0),
            system_qty=float(br.get("system_qty") or 0),
        )
        # Widen reco window slightly for 3-way when few breaks
        three_way = three_way_explain(
            symbol=symbol or "",
            delta=float(br.get("delta") or 0),
            statement_qty=float(br.get("statement_qty") or 0),
            system_qty=float(br.get("system_qty") or 0),
            email_hints=email_slice.get("symbol_hints") or [],
            recos=reco_pack.get("recos") or [],
            trades=trades,
        )
        if_applied = if_reco_applied_explains_gap(
            symbol=symbol or "",
            statement_qty=float(br.get("statement_qty") or 0),
            system_qty=float(br.get("system_qty") or 0),
            email_hints=email_slice.get("symbol_hints") or [],
            recos=reco_pack.get("recos") or [],
        )
        pack = {
            **br,
            "diagnosis": {
                "timing_batch": bool(timing and timing.get("applied")),
                "timing_message": (timing or {}).get("message"),
                "corporate_actions": corporate_actions_near(int(sid), as_of)
                if sid
                else [],
                "advisor_notes": {
                    "approved": notes_pack.get("approved"),
                    "reconciled_through": notes_pack.get("reconciled_through"),
                    "notes_near_as_of": notes_pack.get("notes_near_as_of"),
                    "notes_net_inr": notes_pack.get("notes_net_inr"),
                    "cashflow_descriptions_near": notes_pack.get(
                        "cashflow_descriptions_near"
                    ),
                    "summary": notes_pack.get("summary"),
                    "ui_url": notes_pack.get("ui_url"),
                    "row_count_saved": notes_pack.get("row_count_saved"),
                },
                "emails": email_slice,
                "call_notes": calls_pack,
                "similar_tickers": similar_ticker_hints(
                    client_id=client_id, break_row=br, as_of=as_of
                ),
                "reco": reco_pack,
                "trade_story": story,
                "three_way": three_way,
                "if_reco_applied": if_applied,
                "in_email_cluster": (symbol or "").upper()
                in set(email_cluster.get("explained_symbols") or []),
            },
        }
        hyp = None
        email_has_sym = bool(email_slice.get("symbol_hints"))
        email_sell = any(
            (h.get("action") or "").upper() == "SELL" for h in email_slice.get("symbol_hints") or []
        )
        email_buy = any(
            (h.get("action") or "").upper() == "BUY" for h in email_slice.get("symbol_hints") or []
        )
        delta_f = float(br.get("delta") or 0)
        tw_hyp = three_way.get("hypothesis")
        # Never let BUY-side email hyp win on system-long (negative) deltas
        if delta_f < -0.001 and tw_hyp in (
            "EMAIL_BUY_NO_TRADE",
            "EMAIL_BUY_EXPLAINS_DELTA",
            "TRADE_BUY_EXPLAINS_DELTA",
        ):
            tw_hyp = None
        if delta_f > 0.001 and tw_hyp in (
            "EMAIL_SELL_NO_TRADE",
            "EMAIL_SELL_EXPLAINS_DELTA",
            "TRADE_SELL_EXPLAINS_DELTA",
        ):
            tw_hyp = None

        # Email / reco / 3-way first once a gap exists (after portfolio-wide timing)
        # Recent trailing trades that exactly cover |Δ| beat vague "missed sell"
        if timing and timing.get("applied"):
            hyp = "TIMING_SETTLEMENT_BATCH"
        elif story.get("gap_explained_by_trades") and story.get("gap_explained_by_trades").get(
            "ok"
        ):
            hyp = "RECENT_TRADES_EXPLAIN_GAP"
        elif if_applied.get("explains_gap") and if_applied.get("hypothesis"):
            hyp = if_applied["hypothesis"]
        elif pack["diagnosis"].get("in_email_cluster") and email_cluster.get("found"):
            hyp = "BATCH_RECO_EMAIL_CLUSTER"
        elif tw_hyp:
            hyp = tw_hyp
        elif story.get("hypothesis") == "NEW_ADDITION":
            hyp = "NEW_ADDITION"
        elif reco_pack.get("hypothesis"):
            hyp = reco_pack["hypothesis"]
        elif email_sell and delta_f < 0:
            # Only if sell qty roughly explains; else fall through to trade story
            sell_q = sum(
                float(h["quantity"])
                for h in (email_slice.get("symbol_hints") or [])
                if (h.get("action") or "").upper() == "SELL" and h.get("quantity") is not None
            )
            if sell_q > 0 and abs(sell_q - abs(delta_f)) <= max(0.51, abs(delta_f) * 0.05):
                hyp = "EMAIL_SELL_NOT_IN_BOOKS"
            else:
                hyp = story.get("hypothesis") or "POSSIBLE_MISSED_SELL"
        elif email_buy and delta_f > 0:
            hyp = "EMAIL_BUY_NOT_IN_BOOKS"
        elif story.get("hypothesis"):
            hyp = story["hypothesis"]
        elif email_has_sym:
            hyp = "CHECK_SENT_EMAIL"
        elif reco_pack.get("pending_sent_count"):
            hyp = "EMAIL_SENT_EXEC_OPEN"
        elif pack["diagnosis"]["corporate_actions"]:
            hyp = "CORP_ACTION"
        elif pack["diagnosis"]["similar_tickers"]:
            hyp = "WRONG_SECURITY_NAME"
        elif pack["diagnosis"]["advisor_notes"].get("notes_near_as_of") or pack[
            "diagnosis"
        ]["advisor_notes"].get("cashflow_descriptions_near"):
            hyp = "CHECK_CASHFLOW_NOTES"
        elif calls_pack.get("calls"):
            hyp = "CHECK_CALL_NOTES"
        else:
            hyp = br.get("status") or "UNKNOWN"
        pack["diagnosis"]["top_hypothesis"] = hyp
        pack["diagnosis"]["investigation_notes"] = _compose_investigation_notes(
            pack["diagnosis"]
        )
        out.append(pack)

    # Attach batch crosswalk onto emails_pack for overall
    break_syms = {(b.get("symbol") or "").upper() for b in breaks if b.get("symbol")}
    emailed_break_syms = sorted(s for s in break_syms if s and s in batch_by_sym)
    missed_sell_no_email = []
    emailed_sell_still_long = []
    emailed_sell_still_long_detail = []
    reco_if_executed_match = []
    for br in out:
        sym = (br.get("symbol") or "").upper()
        d = br.get("diagnosis") or {}
        story = d.get("trade_story") or {}
        hints = (d.get("emails") or {}).get("symbol_hints") or []
        reco_pack = d.get("reco") or {}
        if_applied = d.get("if_reco_applied") or {}
        if if_applied.get("explains_gap") and if_applied.get("message"):
            reco_if_executed_match.append(
                {
                    "symbol": sym,
                    "message": if_applied.get("message"),
                    "sell_qty": if_applied.get("sell_qty"),
                    "buy_qty": if_applied.get("buy_qty"),
                    "delta": float(br.get("delta") or 0),
                }
            )
        if story.get("hypothesis") == "POSSIBLE_MISSED_SELL":
            sell_hints = [
                h for h in hints if (h.get("action") or "").upper() == "SELL"
            ]
            sell_qty = sum(
                float(h["quantity"])
                for h in sell_hints
                if h.get("quantity") is not None
            )
            if sell_qty <= 0 and reco_pack.get("sell_qty_sent"):
                sell_qty = float(reco_pack["sell_qty_sent"])
            if sell_hints or sell_qty > 0:
                delta = float(br.get("delta") or 0)
                explains = bool(
                    if_applied.get("explains_gap")
                    or (
                        sell_qty > 0
                        and abs(delta) > 0.001
                        and abs(sell_qty - abs(delta)) <= 0.51
                    )
                )
                emailed_sell_still_long.append(sym)
                emailed_sell_still_long_detail.append(
                    {
                        "symbol": sym,
                        "sell_qty": sell_qty if sell_qty > 0 else None,
                        "delta": delta,
                        "statement_qty": float(br.get("statement_qty") or 0),
                        "system_qty": float(br.get("system_qty") or 0),
                        "explains_gap": explains,
                        "message": if_applied.get("message"),
                    }
                )
            else:
                missed_sell_no_email.append(sym)
    emails_pack = {
        **emails_pack,
        "emailed_break_symbols": emailed_break_syms,
        "missed_sell_no_email": missed_sell_no_email,
        "emailed_sell_still_long": emailed_sell_still_long,
        "emailed_sell_still_long_detail": emailed_sell_still_long_detail,
        "reco_if_executed_match": reco_if_executed_match,
        "batch_hint_lines": [
            f"{(h.get('action') or '').upper()} {h.get('symbol')}"
            + (f" {h.get('quantity'):g}" if h.get("quantity") is not None else "")
            for h in batch_hints[:30]
            if (h.get("symbol") or "").upper() not in _JUNK_RECO_SYMBOLS
        ],
        "email_cluster": email_cluster,
        "historical_sell_batch": historical_sell_batch,
    }

    emails_public = {
        k: v for k, v in emails_pack.items() if k not in ("_all", "_near")
    }
    # Stamp portfolio-long on rows so the table matches the PRIMARY verdict
    if (
        len(out) >= 5
        and all(float(b.get("delta") or 0) < -0.001 for b in out)
        and not (timing and timing.get("applied"))
        and not (email_cluster.get("found"))
    ):
        for pack in out:
            d = pack.get("diagnosis") or {}
            if d.get("if_reco_applied", {}).get("explains_gap"):
                continue
            hyp = d.get("top_hypothesis")
            if hyp in (
                "EMAIL_BUY_NO_TRADE",
                "EMAIL_BUY_NOT_IN_BOOKS",
                "CHECK_SENT_EMAIL",
                "EMAIL_SELL_NOT_IN_BOOKS",
            ):
                d["top_hypothesis"] = "PORTFOLIO_BOOKS_LONG"
                notes = list(d.get("investigation_notes") or [])
                notes.insert(
                    0,
                    "PORTFOLIO: system long on all breaks — treat as books vs this "
                    "demat/account (or unbooked sells), not this email BUY line.",
                )
                d["investigation_notes"] = notes

    overall = build_overall_summary(
        as_of=as_of,
        matched_count=matched_count,
        breaks=out,
        timing=timing,
        statement_security_count=statement_security_count,
        client_emails=emails_public,
        client_notes=notes_pack,
        client_calls=calls_pack,
    )

    # Multi-pass investigation record + deterministic template narrative
    from services.reconciliation.coverage_ledger import apply_primary_cause_trust
    from services.reconciliation.investigation_confidence import (
        assess_investigation_confidence,
    )
    from services.reconciliation.investigation_loop import run_investigation_passes
    from services.reconciliation.narrative import narrate_investigation
    from services.reconciliation.security_reco_thread import build_security_reco_thread

    _prog(72, "Running investigation passes…", "investigate")
    loop = run_investigation_passes(
        as_of=as_of,
        matched_count=matched_count,
        breaks=out,
        timing=timing,
        email_cluster=email_cluster,
        reco_if_executed_match=reco_if_executed_match,
        primary_cause=overall.get("primary_cause"),
        historical_sell_batch=historical_sell_batch,
    )
    overall["investigation_passes"] = loop.get("investigation_passes") or []

    # Thread B: matched/mismatched inventory → security-wise or reco concentration
    thread_emails = [
        {
            "id": e.get("id"),
            "sent_at": e.get("sent_at"),
            "subject": e.get("subject"),
            "is_recommendation": e.get("is_recommendation"),
            "lines": e.get("body_reco_hints") or [],
            "body_reco_hints": e.get("body_reco_hints") or [],
        }
        for e in list(emails_pack.get("_all") or [])
    ]
    security_reco_thread = build_security_reco_thread(
        matched_rows=list(matched_rows or []),
        breaks=out,
        emails=thread_emails,
        matched_count=matched_count,
    )
    overall["security_reco_thread"] = security_reco_thread

    # Detective allocation: evidence bag → clear executed recos → focus 1–2
    from services.reconciliation.allocation_engine import run_allocation
    from services.reconciliation.rules_loader import load_rules

    _prog(82, "Allocating mismatches to reco sets…", "allocate")
    try:
        alloc_cfg = (load_rules().get("allocation") or {})
    except Exception:
        alloc_cfg = {}
    trades_by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    prices_by_symbol: Dict[str, float] = {}
    for pack in out:
        sym = (pack.get("symbol") or "").upper().strip()
        if not sym:
            continue
        story = (pack.get("diagnosis") or {}).get("trade_story") or {}
        for t in story.get("recent_trades") or []:
            trades_by_symbol[sym].append(t)
            px = t.get("price") or t.get("rate")
            if px is not None and sym not in prices_by_symbol:
                try:
                    prices_by_symbol[sym] = float(px)
                except Exception:
                    pass
        # also pull from fetch if empty
        if not trades_by_symbol[sym] and pack.get("security_id"):
            try:
                trades_by_symbol[sym] = fetch_security_trades(
                    client_id=client_id,
                    security_id=int(pack["security_id"]),
                    as_of=as_of,
                )
                for t in trades_by_symbol[sym]:
                    px = t.get("price") or t.get("rate")
                    if px is not None and sym not in prices_by_symbol:
                        try:
                            prices_by_symbol[sym] = float(px)
                        except Exception:
                            pass
            except Exception:
                pass
        for key in ("price", "current_price", "market_price"):
            if pack.get(key) is not None and sym not in prices_by_symbol:
                try:
                    prices_by_symbol[sym] = float(pack[key])
                    break
                except Exception:
                    pass

    cashflow_month_cues = _cashflow_month_cues(notes_pack, as_of)
    price_findings = _price_accuracy_cues_for_breaks(out)
    holdings_notes_drift, holdings_notes_meta = _holdings_notes_cues(
        client_id, matched_rows or [], out
    )

    allocation = run_allocation(
        breaks=out,
        matched_rows=list(matched_rows or []),
        emails=thread_emails,
        trades_by_symbol=dict(trades_by_symbol),
        cashflow_month_cues=cashflow_month_cues,
        price_findings=price_findings,
        holdings_notes_drift=holdings_notes_drift,
        holdings_notes_meta=holdings_notes_meta,
        cashflow_notes_meta={
            "approved": notes_pack.get("approved"),
            "reconciled_through": notes_pack.get("reconciled_through"),
        },
        prices_by_symbol=prices_by_symbol,
        clear_min_evidences=int(alloc_cfg.get("clear_min_evidences") or 3),
        clear_line_ratio=float(alloc_cfg.get("clear_line_ratio") or 0.5),
        rupee_materiality=float(alloc_cfg.get("rupee_materiality") or 500000),
        max_iterations=int(alloc_cfg.get("max_iterations") or 5),
    )
    from services.reconciliation.allocation_engine import build_advisor_story

    # Prefer simple loop (mismatch → email → fixes → next); fall back to allocation
    advisor_story = (simple_loop or {}).get("advisor_story") or {}
    if not (advisor_story.get("fixes") or []):
        advisor_story = allocation.get("advisor_story") or {}
    if not (advisor_story.get("fixes") or []):
        advisor_story = build_advisor_story(
            focus_recos=allocation.get("focus_recos") or [],
            residuals=allocation.get("residuals") or [],
            breaks=out,
            email_cluster=email_cluster,
        )
    allocation = dict(allocation)
    allocation["advisor_story"] = advisor_story
    if advisor_story.get("headline"):
        allocation["summary"] = advisor_story["headline"]
        allocation["action"] = " ".join(
            x for x in (advisor_story.get("headline"), advisor_story.get("next")) if x
        )

    from services.reconciliation.remaining_mismatch_checks import build_remaining_notes
    from services.reconciliation.execution_evidence import (
        check_execution_and_workflow,
        enrich_story_counts,
    )

    advisor_story = enrich_story_counts(advisor_story, breaks=out)
    remaining_notes = build_remaining_notes(
        list(advisor_story.get("remaining") or []),
        out,
    )
    advisor_story = dict(advisor_story)
    advisor_story["remaining_notes"] = remaining_notes

    # Trade window (reco month → next month) + open reco/workflow evidence
    fix_details = []
    steps0 = ((simple_loop or {}).get("steps") or [{}])[0] if (simple_loop or {}).get("steps") else {}
    for d in steps0.get("details") or []:
        if (d.get("symbol") or "").upper() in {
            (s or "").upper() for s in (advisor_story.get("fixes") or [])
        }:
            fix_details.append(d)
    if not fix_details:
        # Fallback: build from email cluster / story fixes with break deltas as qty
        for sym in advisor_story.get("fixes") or []:
            br = next(
                (
                    b
                    for b in out
                    if (b.get("symbol") or "").upper() == (sym or "").upper()
                ),
                None,
            )
            if not br:
                continue
            fix_details.append(
                {
                    "symbol": sym,
                    "action": "buy" if float(br.get("delta") or 0) > 0 else "sell",
                    "quantity": abs(float(br.get("delta") or 0)),
                }
            )
    sid_by_sym = {
        (b.get("symbol") or "").upper(): int(b["security_id"])
        for b in out
        if b.get("symbol") and b.get("security_id")
    }
    execution_check = {"checked": False, "message": None}
    if advisor_story.get("fixes"):
        try:
            execution_check = check_execution_and_workflow(
                client_id=int(client_id),
                story=advisor_story,
                fix_details=fix_details,
                security_ids_by_symbol=sid_by_sym,
            )
        except Exception as exc:
            execution_check = {
                "checked": False,
                "message": f"Execution check skipped: {exc}",
            }
    advisor_story["execution_check"] = execution_check

    # High-conf yellow + status/hypothesis: FOUND_MISSING_RECOMMENDATION
    # (e.g. Jun 2026 reco) — not TIMING_SETTLEMENT / raw match status.
    from services.reconciliation.execution_evidence import (
        apply_found_missing_recommendation_labels,
    )

    apply_found_missing_recommendation_labels(out, advisor_story)

    # Broker contract notes (Drive trade folder) vs books vs sent recos
    broker_evidence: Dict[str, Any] = {"ok": False, "skipped": True}
    try:
        from services.reconciliation.broker_trade_evidence import (
            run_broker_evidence_for_breaks,
        )

        _prog(88, "Checking Drive contract notes vs recos…", "broker_evidence")
        broker_evidence = run_broker_evidence_for_breaks(
            int(client_id), out, as_of=as_of
        )
        broker_evidence["skipped"] = False
    except Exception as exc:
        logger = __import__("logging").getLogger(__name__)
        logger.warning("broker trade evidence failed: %s", exc)
        broker_evidence = {
            "ok": False,
            "skipped": False,
            "error": str(exc),
            "findings": [],
            "summary": f"Broker evidence skipped: {exc}",
        }
    overall["broker_evidence"] = broker_evidence

    # Liquid Bees: system behind + Δ ≈ 4% interest units → book Δ @ ~₹0
    liquid_bees_pack: Dict[str, Any] = {"checked": False, "hits": []}
    try:
        from services.reconciliation.liquid_bees_interest import (
            apply_liquid_bees_to_breaks,
        )

        liquid_bees_pack = apply_liquid_bees_to_breaks(
            int(client_id), out, as_of=as_of
        )
    except Exception as exc:
        liquid_bees_pack = {
            "checked": False,
            "hits": [],
            "error": str(exc),
            "summary": f"Liquid Bees check skipped: {exc}",
        }
    overall["liquid_bees"] = liquid_bees_pack

    # Holdings > reco / Δ matches reco → prior hold, over-buy, or duplicate order
    holdings_vs_reco_pack: Dict[str, Any] = {"checked": False, "hits": []}
    try:
        from services.reconciliation.holdings_vs_reco import (
            apply_holdings_vs_reco_to_breaks,
        )

        holdings_vs_reco_pack = apply_holdings_vs_reco_to_breaks(out)
    except Exception as exc:
        holdings_vs_reco_pack = {
            "checked": False,
            "hits": [],
            "error": str(exc),
            "summary": f"Holdings-vs-reco check skipped: {exc}",
        }
    # Dedupe: hide Confirm-with-client / pending prose already shown in story_groups
    try:
        from services.reconciliation.broker_trade_evidence import (
            filter_hvr_hits_against_broker,
            slim_remaining_notes_against_broker,
        )

        holdings_vs_reco_pack = filter_hvr_hits_against_broker(
            holdings_vs_reco_pack, broker_evidence
        )
        remaining_notes = slim_remaining_notes_against_broker(
            remaining_notes, broker_evidence
        )
        if isinstance(advisor_story, dict):
            advisor_story["remaining_notes"] = remaining_notes
    except Exception:
        pass
    overall["holdings_vs_reco"] = holdings_vs_reco_pack

    # Tentative ₹ buckets: matched / found sources / still need check
    from services.reconciliation.amount_buckets import build_amount_dispute_summary

    matched_for_amt = [dict(m) for m in (matched_rows or [])]
    amount_dispute = build_amount_dispute_summary(
        matched_rows=matched_for_amt,
        breaks=out,
        advisor_story=advisor_story,
        prices_by_symbol=prices_by_symbol,
        as_of=as_of,
        fetch_missing_prices=True,
    )
    overall["amount_dispute"] = amount_dispute
    overall["matched_rows_enriched"] = matched_for_amt

    overall["simple_investigate"] = {
        "steps": (simple_loop or {}).get("steps") or [],
        "advisor_story": advisor_story,
        "remaining_notes": remaining_notes,
        "execution_check": execution_check,
    }
    overall["allocation"] = {
        "summary": allocation.get("summary"),
        "action": allocation.get("action"),
        "advisor_story": advisor_story,
        "cleared_recos": allocation.get("cleared_recos") or [],
        "focus_recos": allocation.get("focus_recos") or [],
        "specific_security_issues": allocation.get("specific_security_issues") or [],
        "residuals": allocation.get("residuals") or [],
        "hypotheses": allocation.get("hypotheses") or [],
        "iterations": allocation.get("iterations") or [],
        "evidence_count": allocation.get("evidence_count"),
        "coverage_abs_delta": allocation.get("coverage_abs_delta"),
        "amount_gap_inr": allocation.get("amount_gap_inr"),
        "large_amount_gap": allocation.get("large_amount_gap"),
        "notes_focus": allocation.get("notes_focus") or {},
        "holdings_notes": {
            **holdings_notes_meta,
            **(allocation.get("notes_focus") or {}),
        },
        "cashflow_month_cues": cashflow_month_cues[:8],
        "price_findings_count": len(price_findings),
    }
    overall["advisor_story"] = advisor_story
    overall["remaining_notes"] = remaining_notes

    _prog(90, "Scoring confidence & coverage ledger…", "confidence")
    confidence = assess_investigation_confidence(
        breaks=out,
        email_cluster=email_cluster,
        historical_sell_batch=historical_sell_batch,
        security_reco_thread=security_reco_thread,
        timing=timing,
        allocation=allocation,
        matched_count=matched_count,
    )
    overall["investigation_confidence"] = confidence
    overall["coverage_ledger"] = {
        "combined_coverage_abs": confidence.get("combined_coverage_abs"),
        "attributions": confidence.get("attributions") or [],
        "unattributed_symbols": confidence.get("unattributed_symbols") or [],
        "trust_primary_cause": confidence.get("trust_primary_cause"),
        "reasons": confidence.get("reasons") or [],
    }

    pack = dict(loop.get("evidence_pack") or {})
    # Enrich evidence pack (facts only)
    pack["break_samples"] = [
        {
            "symbol": b.get("symbol"),
            "statement_qty": b.get("statement_qty"),
            "system_qty": b.get("system_qty"),
            "delta": b.get("delta"),
            "status": b.get("status"),
            "hypothesis": (b.get("diagnosis") or {}).get("top_hypothesis"),
            "if_reco_explains": bool(
                ((b.get("diagnosis") or {}).get("if_reco_applied") or {}).get(
                    "explains_gap"
                )
            ),
        }
        for b in sorted(
            out, key=lambda x: -abs(float(x.get("delta") or 0))
        )[:25]
    ]
    pack["security_reco_thread"] = {
        "mode": security_reco_thread.get("mode"),
        "mode_reason": security_reco_thread.get("mode_reason"),
        "matched_count": security_reco_thread.get("matched_count"),
        "mismatch_count": security_reco_thread.get("mismatch_count"),
        "explainable_count": security_reco_thread.get("explainable_count"),
        "unexplained_count": security_reco_thread.get("unexplained_count"),
        "matched_symbols": [m.get("symbol") for m in (security_reco_thread.get("matched") or [])[:40]],
        "mismatched_symbols": [
            f"{m.get('symbol')} (Δ {m.get('delta')})"
            for m in (security_reco_thread.get("mismatched") or [])[:25]
        ],
        "highlighted_recos": security_reco_thread.get("highlighted_recos") or [],
        "issue_recos": [
            {
                "subject": r.get("subject"),
                "sent_at": r.get("sent_at"),
                "break_name_count": r.get("break_name_count"),
                "issue_symbols": r.get("issue_symbols"),
            }
            for r in (security_reco_thread.get("issue_recos") or [])[:8]
        ],
        "findings": security_reco_thread.get("findings") or [],
    }
    pack["investigation_confidence"] = {
        "level": confidence.get("level"),
        "score": confidence.get("score"),
        "summary": confidence.get("summary"),
        "action": confidence.get("action"),
        "process_ok": confidence.get("process_ok"),
        "trust_primary_cause": confidence.get("trust_primary_cause"),
        "combined_coverage_abs": confidence.get("combined_coverage_abs"),
        "unattributed_symbols": (confidence.get("unattributed_symbols") or [])[:20],
        "top_reasons": [
            {
                "title": r.get("title"),
                "depth": r.get("depth"),
                "coverage_abs_delta": r.get("coverage_abs_delta"),
                "one_liner": r.get("one_liner"),
                "sent_ats": r.get("sent_ats"),
                "subjects": r.get("subjects"),
            }
            for r in (confidence.get("top_reasons") or [])[:2]
        ],
    }
    pack["coverage_ledger"] = overall["coverage_ledger"]
    pack["allocation"] = {
        "summary": overall["allocation"].get("summary"),
        "action": overall["allocation"].get("action"),
        "cleared_recos": overall["allocation"].get("cleared_recos"),
        "focus_recos": overall["allocation"].get("focus_recos"),
        "specific_security_issues": overall["allocation"].get(
            "specific_security_issues"
        ),
        "residuals": overall["allocation"].get("residuals"),
        "hypotheses": (overall["allocation"].get("hypotheses") or [])[:20],
        "evidence_count": overall["allocation"].get("evidence_count"),
        "coverage_abs_delta": overall["allocation"].get("coverage_abs_delta"),
        "large_amount_gap": overall["allocation"].get("large_amount_gap"),
        "amount_gap_inr": overall["allocation"].get("amount_gap_inr"),
    }
    overall["evidence_pack"] = pack
    overall["stopped_at_pass"] = loop.get("stopped_at_pass")
    overall["ranked_explanations"] = loop.get("ranked_explanations") or []
    overall["gap_decomposition"] = loop.get("gap_decomposition")
    if loop.get("primary_cause"):
        overall["primary_cause"] = apply_primary_cause_trust(
            loop["primary_cause"], confidence
        )
    elif overall.get("primary_cause"):
        overall["primary_cause"] = apply_primary_cause_trust(
            overall["primary_cause"], confidence
        )
    overall["narrative"] = narrate_investigation(pack)
    return out, overall
