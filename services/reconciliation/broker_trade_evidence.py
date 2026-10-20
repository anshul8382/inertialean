"""
Broker trade evidence from a client Drive “trade details” folder.

Investigation we did manually for Subhodeep/Marico:
  1. List contract-note PDFs (+ xlsx / order screenshots) on Drive
  2. Decrypt with client PDF passwords (often PAN)
  3. Extract fills for mismatch symbols
  4. Compare to system Transactions + Recommendations
     → e.g. May MARICO BUY 35 sent, no book trade, no June contract fill
        ⇒ if holdings show the shares: executed but not recorded in books;
          if holdings do not: reco may not have been executed

Config (no DB migration): var/holdings_reconciliation/trade_drive_links.json
  { "<client_id>": { "folder_id": "...", "pdf_passwords": ["..."] } }
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_ISIN_RE = re.compile(r"\b(INE[0-9A-Z]{9,12})\b", re.I)
_DATE_IN_NAME_RE = re.compile(
    r"(?P<d>\d{1,2})[-_/](?P<m>\d{1,2})[-_/](?P<y>20\d{2})"
)
_SYM_QTY_RE = re.compile(
    r"(?P<sym>[A-Z][A-Z0-9]{1,15})\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<px>\d+\.\d{2,4})",
    re.I,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def links_path() -> Path:
    root = _repo_root() / "var" / "holdings_reconciliation"
    root.mkdir(parents=True, exist_ok=True)
    return root / "trade_drive_links.json"


def load_trade_drive_link(client_id: int) -> Dict[str, Any]:
    path = links_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    row = data.get(str(int(client_id))) or data.get(int(client_id)) or {}
    return dict(row) if isinstance(row, dict) else {}


def save_trade_drive_link(
    client_id: int,
    *,
    folder_id: Optional[str] = None,
    pdf_passwords: Optional[List[str]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    path = links_path()
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            data = {}
    key = str(int(client_id))
    row = dict(data.get(key) or {})
    if folder_id is not None:
        from services.client_google_drive_service import normalize_folder_id

        row["folder_id"] = normalize_folder_id(folder_id) or None
    if pdf_passwords is not None:
        row["pdf_passwords"] = [
            str(p).strip() for p in pdf_passwords if str(p).strip()
        ]
    if note is not None:
        row["note"] = (note or "")[:500]
    row["updated_at"] = datetime.utcnow().isoformat() + "Z"
    data[key] = row
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return row


def _guess_date_from_name(name: str) -> Optional[date]:
    m = _DATE_IN_NAME_RE.search(name or "")
    if not m:
        # also 17-06-2026 style already covered; try Month Year
        return None
    try:
        d, mo, y = int(m.group("d")), int(m.group("m")), int(m.group("y"))
        return date(y, mo, d)
    except Exception:
        return None


def _extract_text_from_pdf(data: bytes, passwords: List[str]) -> Tuple[str, Optional[str]]:
    """Return (text, password_used_or_none)."""
    try:
        from io import BytesIO
        from PyPDF2 import PdfReader
    except ImportError:
        return "", None
    bio = BytesIO(data)
    try:
        reader = PdfReader(bio)
    except Exception as exc:
        logger.debug("PdfReader failed: %s", exc)
        return "", None
    used = None
    if getattr(reader, "is_encrypted", False):
        unlocked = False
        for pw in [None] + list(passwords or []):
            if pw is None:
                continue
            try:
                bio2 = BytesIO(data)
                reader = PdfReader(bio2)
                if reader.decrypt(str(pw)) == 0:
                    continue
                used = str(pw)
                unlocked = True
                break
            except Exception:
                continue
        if not unlocked:
            return "", None
    try:
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        logger.debug("PDF extract_text failed: %s", exc)
        return "", used
    return text, used


def _parse_fills_from_text(
    text: str,
    *,
    source_name: str,
    trade_date: Optional[date],
) -> List[Dict[str, Any]]:
    """Heuristic fill lines from Angel One–style contract notes."""
    fills: List[Dict[str, Any]] = []
    # Prefer explicit MARICO / PAYTM style: ISIN SYMBOL qty price
    for ln in (text or "").splitlines():
        raw = ln.strip()
        if not raw:
            continue
        isin_m = _ISIN_RE.search(raw)
        if not isin_m:
            continue
        isin = isin_m.group(1).upper()
        rest = raw[isin_m.end() :]
        # Symbol: first token letters (allow spaced P A Y TM)
        rest_u = rest.upper()
        sym = None
        if "MARICO" in rest_u:
            sym = "MARICO"
        elif "PAYTM" in rest_u.replace(" ", "") or "P A Y TM" in rest_u:
            sym = "PAYTM"
        else:
            msym = re.match(r"\s*([A-Z][A-Z0-9.&-]{1,20})", rest_u)
            if msym:
                cand = msym.group(1).replace(".", "")
                if cand not in ("EQ", "B", "S", "NSE", "BSE", "BUY", "SELL"):
                    sym = cand
        # qty + price after symbol
        nums = re.findall(r"\d+(?:\.\d+)?", rest.replace(",", ""))
        qty = None
        px = None
        # Contract note summary lines often: SYMBOL qty price ...
        m2 = re.search(
            r"([A-Z][A-Z0-9]{1,15}|MARICO|PAYTM)\s+(\d+(?:\.\d+)?)\s+(\d+\.\d{2,4})",
            rest_u,
        )
        if m2:
            if not sym:
                sym = m2.group(1)
            try:
                qty = float(m2.group(2))
                px = float(m2.group(3))
            except Exception:
                pass
        side = None
        if re.search(r"\bB\b|BUY|/B\s", rest_u) and not re.search(
            r"\bSELL\b|\bS\b.*/S", rest_u
        ):
            # Order lines: ... B NSE qty
            if " B NSE" in rest_u or " B BSE" in rest_u or raw.count(" B ") >= 1:
                side = "buy"
        if re.search(r"\bS\b NSE|\bS\b BSE|SELL", rest_u):
            side = "sell"
        # Net obligation lines: buy qty in col then sell — if "−N" net buy
        if not sym or qty is None:
            continue
        # Skip tiny noise / brokerage-only
        if qty <= 0:
            continue
        fills.append(
            {
                "symbol": sym.upper() if sym != "PAYTM" else "PAYTM",
                "isin": isin,
                "quantity": qty,
                "price": px,
                "side": side or "buy",
                "trade_date": trade_date.isoformat() if trade_date else None,
                "source_file": source_name,
                "raw": raw[:240],
            }
        )
    # Dedup identical
    seen = set()
    out = []
    for f in fills:
        key = (
            f.get("symbol"),
            f.get("quantity"),
            f.get("price"),
            f.get("trade_date"),
            f.get("source_file"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _parse_xlsx_fills(data: bytes, source_name: str) -> List[Dict[str, Any]]:
    try:
        from io import BytesIO
        import openpyxl
    except ImportError:
        return []
    try:
        wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
    except Exception:
        return []
    fills = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        for row in rows:
            vals = [c for c in row if c is not None]
            if len(vals) < 4:
                continue
            # Heuristic: symbol, isin, date, ..., side, qty, price
            sym = None
            isin = None
            side = None
            qty = None
            px = None
            td = None
            for c in vals:
                if isinstance(c, str):
                    u = c.strip().upper()
                    if _ISIN_RE.fullmatch(u):
                        isin = u
                    elif u in ("BUY", "SELL"):
                        side = u.lower()
                    elif re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", u) and u not in (
                        "NSE",
                        "BSE",
                        "EQ",
                    ):
                        if not sym:
                            sym = u
                elif isinstance(c, (int, float)) and not isinstance(c, bool):
                    if qty is None and float(c) > 0:
                        qty = float(c)
                    elif px is None and float(c) > 1:
                        px = float(c)
                elif isinstance(c, datetime):
                    td = c.date()
                elif isinstance(c, date):
                    td = c
            if sym and qty:
                fills.append(
                    {
                        "symbol": sym,
                        "isin": isin,
                        "quantity": qty,
                        "price": px,
                        "side": side or "buy",
                        "trade_date": td.isoformat() if td else None,
                        "source_file": source_name,
                        "raw": "xlsx",
                    }
                )
    return fills


def collect_broker_fills_from_drive(
    client_id: int,
    *,
    symbols: Optional[List[str]] = None,
    max_files: int = 40,
) -> Dict[str, Any]:
    """
    Download trade-folder files and extract fills (optionally filtered by symbols).
    """
    link = load_trade_drive_link(client_id)
    folder_id = (link.get("folder_id") or "").strip()
    passwords = list(link.get("pdf_passwords") or [])
    if not folder_id:
        return {
            "ok": False,
            "reason": "trade_folder_not_linked",
            "message": (
                "No trade-details Drive folder linked for this client. "
                "Paste the folder URL on Holdings recon (Trade evidence) "
                "or save via trade_drive_links.json."
            ),
            "fills": [],
            "files_scanned": 0,
            "screenshots": [],
        }

    try:
        from services.client_google_drive_service import (
            download_file_bytes,
            walk_folder_tree,
        )
    except Exception as exc:
        return {"ok": False, "reason": "drive_import", "error": str(exc), "fills": []}

    walked = walk_folder_tree(folder_id, max_depth=3, max_nodes=200)
    files = list(walked.get("files") or [])[: int(max_files)]
    want = {_norm(s) for s in (symbols or []) if s}
    fills: List[Dict[str, Any]] = []
    screenshots: List[Dict[str, Any]] = []
    scanned = 0
    decrypt_ok = 0
    decrypt_fail = 0

    for f in files:
        name = f.get("name") or ""
        mime = (f.get("mimeType") or "").lower()
        fid = f.get("id")
        if not fid:
            continue
        if mime.startswith("image/") or name.lower().endswith(
            (".jpeg", ".jpg", ".png", ".webp")
        ):
            screenshots.append(
                {
                    "name": name,
                    "id": fid,
                    "mimeType": mime,
                    "note": "Order-status screenshot — open in Drive to review (OCR optional).",
                }
            )
            continue
        dl = download_file_bytes(fid)
        if not dl.get("ok"):
            continue
        data = dl.get("content") or b""
        scanned += 1
        tdate = _guess_date_from_name(name)
        if name.lower().endswith(".xlsx") or "sheet" in mime:
            fills.extend(_parse_xlsx_fills(data, name))
            continue
        if data[:5] != b"%PDF-" and "pdf" not in mime and not name.lower().endswith(
            ".pdf"
        ):
            # nameless PDF exports
            if data[:5] != b"%PDF-":
                continue
        text, used = _extract_text_from_pdf(data, passwords)
        if not text:
            decrypt_fail += 1
            continue
        decrypt_ok += 1
        # Prefer Trade Date inside PDF
        m = re.search(
            r"Trade\s*Date\s*:\s*(\d{1,2})/(\d{1,2})/(20\d{2})",
            text,
            re.I,
        )
        if m:
            try:
                tdate = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except Exception:
                pass
        fills.extend(
            _parse_fills_from_text(text, source_name=name, trade_date=tdate)
        )

    if want:
        fills = [x for x in fills if _norm(x.get("symbol")) in want]

    return {
        "ok": True,
        "folder_id": folder_id,
        "files_scanned": scanned,
        "decrypt_ok": decrypt_ok,
        "decrypt_fail": decrypt_fail,
        "fills": fills,
        "screenshots": screenshots,
        "passwords_configured": bool(passwords),
    }


def _norm(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _system_trades_for_symbols(
    client_id: int, symbols: Set[str]
) -> Dict[str, List[Dict[str, Any]]]:
    from models import Security, Transaction

    if not symbols:
        return {}
    secs = Security.query.filter(Security.symbol.in_(list(symbols))).all()
    by_id = {int(s.id): _norm(s.symbol) for s in secs}
    if not by_id:
        return {}
    rows = (
        Transaction.query.filter(
            Transaction.client_id == int(client_id),
            Transaction.security_id.in_(list(by_id.keys())),
        )
        .order_by(Transaction.transaction_date.asc())
        .all()
    )
    out: Dict[str, List[Dict[str, Any]]] = {s: [] for s in symbols}
    for t in rows:
        sym = by_id.get(int(t.security_id))
        if not sym:
            continue
        td = t.transaction_date.date() if t.transaction_date else None
        out.setdefault(sym, []).append(
            {
                "date": td.isoformat() if td else None,
                "type": (t.type or "").upper(),
                "quantity": float(t.quantity) if t.quantity is not None else None,
                "price": float(t.price) if t.price is not None else None,
                "id": t.id,
                "source": "system",
            }
        )
    return out


def _recos_for_symbols(
    client_id: int, symbols: Set[str]
) -> Dict[str, List[Dict[str, Any]]]:
    from models import Recommendation, RecommendationSession, Security

    if not symbols:
        return {}
    secs = Security.query.filter(Security.symbol.in_(list(symbols))).all()
    by_id = {int(s.id): _norm(s.symbol) for s in secs}
    if not by_id:
        return {}
    rows = (
        Recommendation.query.filter(
            Recommendation.client_id == int(client_id),
            Recommendation.security_id.in_(list(by_id.keys())),
        )
        .order_by(Recommendation.id.asc())
        .limit(200)
        .all()
    )
    sess_ids = {int(r.session_id) for r in rows if r.session_id}
    names = {}
    if sess_ids:
        for s in RecommendationSession.query.filter(
            RecommendationSession.id.in_(list(sess_ids))
        ).all():
            names[int(s.id)] = s.session_name
    out: Dict[str, List[Dict[str, Any]]] = {s: [] for s in symbols}
    for r in rows:
        sym = by_id.get(int(r.security_id))
        if not sym:
            continue
        sent = r.sent_at.date() if r.sent_at else None
        out.setdefault(sym, []).append(
            {
                "sent_at": sent.isoformat() if sent else None,
                "action": (r.action or "").lower(),
                "quantity": float(r.quantity) if r.quantity is not None else None,
                "status": r.status,
                "executed_at": (
                    r.executed_at.isoformat() if r.executed_at else None
                ),
                "session_id": r.session_id,
                "session_name": names.get(int(r.session_id)) if r.session_id else None,
                "id": r.id,
            }
        )
    return out


def _qty_close(a: float, b: float, tol: float = 0.51) -> bool:
    return abs(float(a) - float(b)) <= max(tol, abs(b) * 0.02)


def analyze_reco_vs_broker_and_books(
    client_id: int,
    *,
    symbols: List[str],
    as_of: Optional[date] = None,
    break_context: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    For each symbol: list system trades, recos, broker fills; flag sent recos
    with no matching book trade and no broker fill near sent date.

    break_context[sym] may include statement_qty, system_qty, delta, status.
    When holdings are ahead of books for a BUY, the story is
    **executed but not recorded** (not “not executed”).
    """
    syms = {_norm(s) for s in symbols if s}
    break_context = break_context or {}
    broker = collect_broker_fills_from_drive(client_id, symbols=list(syms))
    sys_trades = {}
    recos = {}
    try:
        sys_trades = _system_trades_for_symbols(client_id, syms)
        recos = _recos_for_symbols(client_id, syms)
    except Exception as exc:
        logger.warning("system trade/reco load failed: %s", exc)

    broker_by_sym: Dict[str, List[Dict[str, Any]]] = {s: [] for s in syms}
    for f in broker.get("fills") or []:
        s = _norm(f.get("symbol"))
        if s in broker_by_sym:
            broker_by_sym[s].append(f)

    per_symbol: List[Dict[str, Any]] = []
    findings: List[str] = []

    for sym in sorted(syms):
        books = sys_trades.get(sym) or []
        rs = recos.get(sym) or []
        bfs = broker_by_sym.get(sym) or []
        ctx = break_context.get(sym) or {}
        stmt_qty = ctx.get("statement_qty")
        sys_qty_ctx = ctx.get("system_qty")
        delta_ctx = ctx.get("delta")
        try:
            stmt_f = float(stmt_qty) if stmt_qty is not None else None
        except Exception:
            stmt_f = None
        try:
            sys_f = (
                float(sys_qty_ctx)
                if sys_qty_ctx is not None
                else None
            )
        except Exception:
            sys_f = None
        try:
            delta_f = (
                float(delta_ctx)
                if delta_ctx is not None
                else (
                    (stmt_f - sys_f)
                    if stmt_f is not None and sys_f is not None
                    else None
                )
            )
        except Exception:
            delta_f = None

        book_qty = 0.0
        for t in books:
            q = float(t.get("quantity") or 0)
            if (t.get("type") or "").upper() in ("SELL", "S"):
                book_qty -= q
            else:
                book_qty += q
        if sys_f is None:
            sys_f = book_qty

        missing_from_books = []
        for r in rs:
            st = (r.get("status") or "").lower()
            if st in ("executed", "closed", "cancelled"):
                continue
            if r.get("executed_at"):
                continue
            act = (r.get("action") or "").lower()
            qty = r.get("quantity")
            if act not in ("buy", "sell") or qty is None:
                continue
            # Matching book trade near sent_at (±45d)
            sent = None
            try:
                sent = date.fromisoformat(str(r.get("sent_at") or "")[:10])
            except Exception:
                sent = None
            def _side_ok_book(tt: str) -> bool:
                tt = (tt or "").lower()
                if act == "buy":
                    return tt in ("buy", "b")
                if act == "sell":
                    return tt in ("sell", "s")
                return False

            def _near(d_iso: Optional[str], center: Optional[date], days: int = 45) -> bool:
                if not center or not d_iso:
                    return True
                try:
                    td = date.fromisoformat(str(d_iso)[:10])
                    return abs((td - center).days) <= days
                except Exception:
                    return True

            matched_book = False
            # Single fill
            for t in books:
                if not _side_ok_book(t.get("type") or ""):
                    continue
                if not _near(t.get("date"), sent):
                    continue
                if _qty_close(float(t.get("quantity") or 0), float(qty)):
                    matched_book = True
                    break
            # Split fills same day / ±3d (e.g. PAYTM 24+6 = 30)
            if not matched_book:
                window = []
                for t in books:
                    if not _side_ok_book(t.get("type") or ""):
                        continue
                    if not _near(t.get("date"), sent, days=45):
                        continue
                    window.append(float(t.get("quantity") or 0))
                if window and _qty_close(sum(window), float(qty), tol=1.0):
                    matched_book = True
                elif sent:
                    # cluster by calendar day
                    by_day: Dict[str, float] = {}
                    for t in books:
                        if not _side_ok_book(t.get("type") or ""):
                            continue
                        if not _near(t.get("date"), sent, days=3):
                            continue
                        day = str(t.get("date") or "")[:10]
                        by_day[day] = by_day.get(day, 0.0) + float(
                            t.get("quantity") or 0
                        )
                    if any(_qty_close(v, float(qty), tol=1.0) for v in by_day.values()):
                        matched_book = True

            matched_broker = False
            for f in bfs:
                side = (f.get("side") or "buy").lower()
                if act != side:
                    continue
                if not _near(f.get("trade_date"), sent):
                    continue
                if _qty_close(float(f.get("quantity") or 0), float(qty)):
                    matched_broker = True
                    break
            if not matched_broker and sent:
                by_day_b: Dict[str, float] = {}
                for f in bfs:
                    side = (f.get("side") or "buy").lower()
                    if act != side:
                        continue
                    if not _near(f.get("trade_date"), sent, days=3):
                        continue
                    day = str(f.get("trade_date") or "")[:10]
                    by_day_b[day] = by_day_b.get(day, 0.0) + float(
                        f.get("quantity") or 0
                    )
                if any(_qty_close(v, float(qty), tol=1.0) for v in by_day_b.values()):
                    matched_broker = True
            if matched_book or matched_broker:
                continue

            # Holdings vs books decides the story for missing book/broker fill.
            # BUY + holdings ahead of books → executed, missed recording.
            # BUY + holdings not ahead → may not have been executed.
            # SELL + holdings still high → sell likely not executed.
            # SELL + holdings lower / books still long → sell executed, not booked.
            when = r.get("sent_at") or r.get("session_name") or "reco"
            holdings_ahead = bool(
                delta_f is not None
                and delta_f > 0.01
                or (
                    stmt_f is not None
                    and sys_f is not None
                    and stmt_f > sys_f + 0.01
                )
            )
            gap_matches = bool(
                delta_f is not None and _qty_close(abs(delta_f), float(qty))
            )

            if act == "buy" and (holdings_ahead or gap_matches):
                verdict = "EXECUTED_NOT_RECORDED"
                detail = (
                    f"{sym} {act.upper()} {qty:g} ({when}): recommended and appears "
                    f"in holdings, but no matching system trade"
                    f"{'' if matched_broker else ' (and no fill in linked contract notes)'}"
                    f" — likely executed; we missed recording it. "
                    f"Add BUY {qty:g} to books (or confirm booking)."
                )
            elif act == "sell" and holdings_ahead:
                # Still long on statement vs books expecting a sell
                verdict = "RECO_NOT_EXECUTED"
                detail = (
                    f"{sym} {act.upper()} {qty:g} ({when}): recommended sell, "
                    f"holdings still look long / no matching system trade — "
                    f"looks not executed."
                )
            elif act == "sell" and delta_f is not None and delta_f < -0.01:
                verdict = "EXECUTED_NOT_RECORDED"
                detail = (
                    f"{sym} {act.upper()} {qty:g} ({when}): books still long vs "
                    f"holdings with no matching system trade — likely sell was "
                    f"executed; we missed recording it. Add SELL {qty:g} to books."
                )
            else:
                verdict = "RECO_NOT_EXECUTED"
                detail = (
                    f"{sym} {act.upper()} {qty:g} ({when}): no matching system trade "
                    f"and no matching fill in linked contract notes, and holdings "
                    f"do not show the extra qty — looks not executed."
                )

            missing_from_books.append(
                {
                    **r,
                    "book_match": matched_book,
                    "broker_match": matched_broker,
                    "verdict": verdict,
                    "detail": detail,
                }
            )
            findings.append(detail)

        per_symbol.append(
            {
                "symbol": sym,
                "book_qty": book_qty,
                "statement_qty": stmt_f,
                "delta": delta_f,
                "system_trades": books,
                "recommendations": rs,
                "broker_fills": bfs,
                # keep key for callers; includes both executed-not-recorded & not-executed
                "unexecuted_recos": missing_from_books,
                "missing_from_books": missing_from_books,
            }
        )

    n_missed = sum(
        1
        for f in findings
        if "missed recording" in f.lower() or "missed recording" in f
    )
    n_not_exec = len(findings) - n_missed
    if findings:
        parts = []
        if n_missed:
            parts.append(
                f"{n_missed} reco(s) look executed but not recorded in books"
            )
        if n_not_exec:
            parts.append(f"{n_not_exec} reco(s) look not executed")
        summary = "; ".join(parts) + "."
    else:
        summary = (
            "No reco/book/broker gaps for focus symbols "
            "(or trade folder not linked / empty)."
            if broker.get("ok")
            else (broker.get("message") or "Trade folder not linked.")
        )

    return {
        "ok": bool(broker.get("ok") or sys_trades or recos),
        "broker": {
            k: broker.get(k)
            for k in (
                "ok",
                "reason",
                "message",
                "folder_id",
                "files_scanned",
                "decrypt_ok",
                "decrypt_fail",
                "screenshots",
                "passwords_configured",
            )
        },
        "per_symbol": per_symbol,
        "findings": findings[:40],
        "summary": summary,
    }


def run_broker_evidence_for_breaks(
    client_id: int,
    breaks: List[Dict[str, Any]],
    *,
    as_of: Optional[date] = None,
    max_symbols: int = 25,
) -> Dict[str, Any]:
    """Entry used by diagnose_breaks — focus on mismatch symbols (prefer high-conf)."""
    hi = []
    other = []
    for b in breaks or []:
        sym = _norm(b.get("symbol"))
        if not sym:
            continue
        d = b.get("diagnosis") or {}
        if (
            b.get("high_confidence")
            or d.get("high_confidence")
            or (b.get("status") or "") == "FOUND_MISSING_RECOMMENDATION"
        ):
            hi.append(sym)
        else:
            other.append(sym)
    # unique preserve order
    ordered = []
    for s in hi + other:
        if s not in ordered:
            ordered.append(s)
    ordered = ordered[: int(max_symbols)]
    break_context: Dict[str, Dict[str, Any]] = {}
    for b in breaks or []:
        sym = _norm(b.get("symbol"))
        if not sym:
            continue
        break_context[sym] = {
            "statement_qty": b.get("statement_qty"),
            "system_qty": b.get("system_qty"),
            "delta": b.get("delta"),
            "status": b.get("status") or b.get("status_display"),
        }
    pack = analyze_reco_vs_broker_and_books(
        int(client_id),
        symbols=ordered,
        as_of=as_of,
        break_context=break_context,
    )
    # Attach short note onto each break diagnosis
    by_sym = {p["symbol"]: p for p in pack.get("per_symbol") or []}
    for b in breaks or []:
        sym = _norm(b.get("symbol"))
        info = by_sym.get(sym)
        if not info:
            continue
        d = dict(b.get("diagnosis") or {})
        notes = list(d.get("investigation_notes") or [])
        for u in info.get("missing_from_books") or info.get("unexecuted_recos") or []:
            notes.append(u.get("detail"))
            verdict = u.get("verdict") or "EXECUTED_NOT_RECORDED"
            label = (
                f"{verdict} — {u.get('sent_at') or u.get('session_name') or 'reco'}"
            )
            # Prefer executed-not-recorded over timing / generic
            hyp = str(d.get("top_hypothesis") or "")
            if (
                not hyp
                or hyp in ("UNKNOWN", "QTY_BREAK", "MISSING_IN_SYSTEM", "CHECK_SENT_EMAIL")
                or hyp.startswith("TIMING")
                or hyp.startswith("RECO_NOT_EXECUTED")
                or verdict == "EXECUTED_NOT_RECORDED"
            ):
                if hyp and not d.get("prior_hypothesis") and hyp != label:
                    d["prior_hypothesis"] = hyp
                d["top_hypothesis"] = label
        if info.get("broker_fills"):
            notes.append(
                f"Broker contract notes: {len(info['broker_fills'])} fill(s) for {sym}."
            )
        elif pack.get("broker", {}).get("ok"):
            notes.append(
                f"No {sym} fill found in linked Drive contract notes "
                f"(scanned {pack['broker'].get('files_scanned')} file(s))."
            )
        d["broker_evidence"] = {
            "broker_fills": info.get("broker_fills") or [],
            "unexecuted_recos": info.get("unexecuted_recos") or [],
            "missing_from_books": info.get("missing_from_books") or [],
            "system_trade_count": len(info.get("system_trades") or []),
        }
        d["investigation_notes"] = notes
        b["diagnosis"] = d
    pack["symbols"] = ordered
    return pack
