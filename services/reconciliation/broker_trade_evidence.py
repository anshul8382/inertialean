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


def build_booking_gap_story(
    *,
    symbol: str,
    action: str,
    quantity: float,
    when: str,
    book_has_trade: bool,
    trade_details_has: bool,
    trade_details_file: Optional[str] = None,
    trade_folder_linked: bool = True,
    has_screenshots: bool = False,
    holdings_ok: bool = True,
    held_qty: Optional[float] = None,
    partial: bool = False,
) -> Dict[str, Any]:
    """
    Plain-language story for advisor UI.

    Pipeline: Recommended → Trade (our books) → Holding
    Green = ok, Red = missing / problem.
    """
    sym = (symbol or "").upper().strip()
    act = (action or "buy").strip().lower()
    act_label = "BUY" if act in ("buy", "b") else "SELL"
    qty = float(quantity or 0)
    held = float(held_qty) if held_qty is not None else None
    when_s = (when or "").strip()
    # Dates stay short; session names must not be sliced to "Portfolio "
    if when_s and len(when_s) >= 8 and when_s[4:5] == "-" and when_s[7:8] == "-":
        when_bit = f" ({when_s[:10]})"
    elif when_s:
        when_bit = f" ({when_s})"
    else:
        when_bit = ""

    recommended_ok = True
    trade_ok = bool(book_has_trade)
    holding_ok = bool(holdings_ok)

    hold_text = "Shown on statement" if holding_ok else "Not on statement"
    if holding_ok and partial and held is not None:
        hold_text = f"{held:g} of {qty:g} on statement"
    elif holding_ok and held is not None:
        hold_text = f"{held:g} on statement"

    reco_text = f"{act_label} {qty:g}{when_bit}"
    if partial and held is not None:
        reco_text = f"{act_label} {qty:g}{when_bit} — {held:g} done so far"

    pipeline = [
        {
            "key": "recommended",
            "label": "Reco found",
            "ok": recommended_ok,
            "text": reco_text,
        },
        {
            "key": "trade_books",
            "label": "Trade in books",
            "ok": trade_ok,
            "text": "Recorded" if trade_ok else "Missing",
        },
        {
            "key": "trade_statement",
            "label": "Trade statement",
            "ok": bool(trade_details_has),
            "text": (
                (trade_details_file or "Matched")
                if trade_details_has
                else "Not found"
            ),
        },
        {
            "key": "holding",
            "label": "Holding sync",
            "ok": holding_ok,
            "text": hold_text,
        },
    ]

    # Short headline — no jargon
    if holding_ok and not trade_ok and partial and held is not None:
        headline = (
            f"{sym}: recommended {act_label} {qty:g}{when_bit}; "
            f"holdings show {held:g} of that (partial). "
            f"Those {held:g} are not in our books yet."
        )
        verdict = "EXECUTED_NOT_RECORDED"
        book_qty = held
    elif holding_ok and not trade_ok:
        headline = (
            f"{sym}: we recommended {act_label} {qty:g}{when_bit}, "
            f"the client holds it, but this trade is not in our books."
        )
        verdict = "EXECUTED_NOT_RECORDED"
        book_qty = qty
    elif not holding_ok and not trade_ok:
        headline = (
            f"{sym}: we recommended {act_label} {qty:g}{when_bit}, "
            f"but it does not show in holdings and is not in our books."
        )
        verdict = "RECO_NOT_EXECUTED"
        book_qty = qty
    else:
        headline = f"{sym}: {act_label} {qty:g}{when_bit}"
        verdict = "OK" if trade_ok else "EXECUTED_NOT_RECORDED"
        book_qty = qty

    next_steps: List[str] = []
    evidence_note = None

    if holding_ok and not trade_ok:
        look_qty = book_qty
        if trade_details_has:
            fname = trade_details_file or "the Trade details folder"
            evidence_note = f"Found this buy/sell in: {fname}"
            next_steps = [
                f"Open {fname} and confirm {sym} {act_label} ~{look_qty:g}.",
                f"Add {act_label} {look_qty:g} into our books "
                f"(trade happened; we did not record it).",
            ]
            if partial and held is not None and held + 0.51 < qty:
                next_steps.append(
                    f"Remaining {qty - held:g} of the {qty:g} recommendation is still open."
                )
        elif not trade_folder_linked:
            next_steps = [
                "Link the client’s Trade details Drive folder (Upload / trade evidence), then refresh.",
                "Or ask the client to share broker trade statements for this period.",
            ]
        else:
            next_steps = [
                f"Look in Trade details for {sym} {act_label} ~{look_qty:g}"
                + (" (partial of the larger reco)." if partial else "."),
                "Check order screenshots or other PDFs in that folder.",
                "Check any multi-month trade Excel (broker export) if available.",
                "If still nothing — ask the client to share trades for this period.",
            ]
            if has_screenshots:
                next_steps.insert(
                    1,
                    "There are order screenshots in Trade details — open them and look for this name.",
                )
            if partial and held is not None and held + 0.51 < qty:
                next_steps.append(
                    f"Remaining {qty - held:g} of recommended {qty:g} still looks open."
                )
    elif not holding_ok and not trade_ok:
        next_steps = [
            "Looks like this recommendation was not done (or not yet).",
            "Confirm with the advisor / client before booking anything.",
        ]

    detail = headline
    if evidence_note:
        detail = f"{headline} {evidence_note}."
    if next_steps:
        detail = detail + " Next: " + " ".join(next_steps[:2])

    return {
        "verdict": verdict,
        "headline": headline,
        "detail": detail,
        "evidence_note": evidence_note,
        "next_steps": next_steps,
        "pipeline": pipeline,
        "trade_details_has": bool(trade_details_has),
        "trade_details_file": trade_details_file,
        "symbol": sym,
        "action": act_label,
        "quantity": qty,
        "held_qty": held,
        "partial": bool(partial),
        "sent_at": when_s,
    }


def _dedupe_keep_order(values: List[Any]) -> List[Any]:
    seen: Set[str] = set()
    out: List[Any] = []
    for v in values or []:
        key = str(v).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def build_reco_trade_map_rows(
    per_symbol: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    Flat rows for a collapsible Reco → Trade → Holding detail table.

    Columns: reco found, trade in books, trade statement match, holding in sync.
    """
    rows: List[Dict[str, Any]] = []
    for p in per_symbol or []:
        sym = (p.get("symbol") or "").strip().upper()
        if not sym:
            continue
        recos = list(p.get("recommendations") or [])
        books = list(p.get("system_trades") or [])
        fills = list(p.get("broker_fills") or [])
        missing = list(p.get("missing_from_books") or p.get("unexecuted_recos") or [])
        try:
            delta = float(p["delta"]) if p.get("delta") is not None else None
        except (TypeError, ValueError):
            delta = None

        reco_bits = []
        for r in recos[:4]:
            act = (r.get("action") or "").strip().upper()
            q = r.get("quantity")
            bit = act or "RECO"
            if q is not None:
                try:
                    bit = f"{bit} {float(q):g}"
                except (TypeError, ValueError):
                    pass
            when = (r.get("sent_at") or r.get("when_label") or "")[:10]
            if when:
                bit = f"{bit} ({when})"
            reco_bits.append(bit)

        stmt_ok = bool(fills) or any(bool(m.get("broker_match")) for m in missing)
        stmt_file = next(
            (
                str(m.get("trade_details_file") or "").strip()
                for m in missing
                if m.get("broker_match") and m.get("trade_details_file")
            ),
            None,
        )
        if not stmt_file and fills:
            stmt_file = str(
                fills[0].get("file") or fills[0].get("filename") or ""
            ).strip() or None

        sync_ok = delta is not None and abs(delta) < 0.011
        verdict = None
        for m in missing:
            if m.get("verdict"):
                verdict = m.get("verdict")
                break
        if not verdict:
            verdict = "IN_SYNC" if sync_ok else ("HAS_RECO" if recos else "OPEN")

        rows.append(
            {
                "symbol": sym,
                "statement_qty": p.get("statement_qty"),
                "book_qty": p.get("book_qty"),
                "delta": delta,
                "reco_found": bool(recos),
                "reco_text": ", ".join(reco_bits) if reco_bits else "—",
                "trade_books_found": bool(books),
                "trade_books_text": (
                    f"{len(books)} in books" if books else "None in books"
                ),
                "trade_statement_match": stmt_ok,
                "trade_statement_text": (
                    (stmt_file or (f"{len(fills)} fill(s)" if fills else "Matched"))
                    if stmt_ok
                    else "Not in Trade details"
                ),
                "holding_in_sync": sync_ok,
                "holding_text": (
                    "In sync"
                    if sync_ok
                    else (
                        f"Δ {delta:g}"
                        if delta is not None
                        else "—"
                    )
                ),
                "verdict": verdict,
            }
        )
    return rows


def _canonical_next_steps(*, verdict: str, trade_details_has: bool) -> List[str]:
    """One shared instruction list per conclusion (no per-name variants)."""
    if verdict == "EXECUTED_NOT_RECORDED":
        if trade_details_has:
            return [
                "Open the Trade details file named for each line and confirm the trade.",
                "Add that trade into our books (it happened; we did not record it).",
            ]
        return [
            "Look in Trade details (PDFs / Excel / screenshots) for these names.",
            "If found — add the trade to books.",
            "If not — ask the client to share trades for this period.",
        ]
    if verdict == "RECO_NOT_EXECUTED":
        return [
            "Confirm with advisor/client whether these recommendations were executed.",
            "Do not book until trade proof exists.",
        ]
    return []


def group_booking_gap_stories(
    stories: List[Dict[str, Any]],
    *,
    remaining_symbols: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Collapse many same-conclusion rows into one card.

    Group key is coarse: verdict + whether Trade details found the trade.
    Names are listed once; instructions appear once per group.
    """
    remaining = {
        str(s or "").upper().strip() for s in (remaining_symbols or set()) if s
    }
    buckets: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    order: List[Tuple[Any, ...]] = []
    item_seen: Dict[Tuple[Any, ...], Set[str]] = {}

    for raw in stories or []:
        s = raw.get("story") if isinstance(raw.get("story"), dict) else raw
        if not isinstance(s, dict):
            continue
        verdict = s.get("verdict") or raw.get("verdict") or "EXECUTED_NOT_RECORDED"
        has_td = bool(s.get("trade_details_has") or raw.get("broker_match"))
        key = (verdict, has_td)
        if key not in buckets:
            buckets[key] = {
                "verdict": verdict,
                "trade_details_has": has_td,
                "evidence_note": s.get("evidence_note") or raw.get("evidence_note"),
                "items": [],
                "reco_dates": [],
            }
            order.append(key)
            item_seen[key] = set()
        elif not buckets[key].get("evidence_note"):
            buckets[key]["evidence_note"] = s.get("evidence_note") or raw.get(
                "evidence_note"
            )

        sym = (s.get("symbol") or raw.get("symbol") or "").upper().strip()
        act = s.get("action") or raw.get("action") or "BUY"
        qty = s.get("quantity")
        if qty is None:
            qty = raw.get("quantity")
        when_raw = s.get("sent_at") or raw.get("sent_at") or ""
        when = str(when_raw)[:10] if when_raw else ""
        # Prefer date-looking when; drop truncated session junk like "Portfolio "
        if when and not (len(when) >= 8 and when[4:5] == "-"):
            when = ""
        held = s.get("held_qty")
        if held is None:
            held = raw.get("held_qty")
        partial = bool(s.get("partial") or raw.get("partial"))
        label = f"{sym} {act}"
        if qty is not None:
            try:
                qf = float(qty)
                if partial and held is not None:
                    label = f"{sym} {act} {float(held):g} of {qf:g}"
                else:
                    label = f"{sym} {act} {qf:g}"
            except (TypeError, ValueError):
                label = f"{sym} {act} {qty}"
        if when:
            label = f"{label} ({when})"
        dedupe_key = f"{sym}|{act}|{qty}|{when}|{held}"
        if dedupe_key in item_seen[key]:
            continue
        item_seen[key].add(dedupe_key)
        buckets[key]["items"].append(
            {
                "symbol": sym,
                "action": act,
                "quantity": qty,
                "sent_at": when,
                "label": label,
                "in_remaining": (not remaining) or (sym in remaining),
            }
        )
        if when and when not in buckets[key]["reco_dates"]:
            buckets[key]["reco_dates"].append(when)

    groups: List[Dict[str, Any]] = []
    for key in order:
        g = buckets[key]
        items = g["items"]
        items.sort(
            key=lambda it: (0 if it.get("in_remaining") else 1, it.get("label") or "")
        )
        n = len(items)
        verdict = g["verdict"]
        has_td = bool(g.get("trade_details_has"))
        dates = g["reco_dates"]
        date_bit = ""
        if len(dates) == 1:
            date_bit = f" · reco {dates[0]}"
        elif len(dates) > 1:
            date_bit = f" · {len(dates)} reco dates"

        if verdict == "EXECUTED_NOT_RECORDED":
            title = (
                f"{n} name(s): recommended + in holdings, "
                f"trade missing from books{date_bit}"
            )
            badge = "Trade missing from books"
            pipe = [
                {"key": "recommended", "label": "Recommended", "ok": True, "text": "Yes"},
                {
                    "key": "trade",
                    "label": "Trade in our books",
                    "ok": False,
                    "text": "Missing",
                },
                {"key": "holding", "label": "Holding", "ok": True, "text": "On statement"},
            ]
        elif verdict == "RECO_NOT_EXECUTED":
            title = f"{n} name(s): recommendation may not have been done{date_bit}"
            badge = "May not be done"
            pipe = [
                {"key": "recommended", "label": "Recommended", "ok": True, "text": "Yes"},
                {
                    "key": "trade",
                    "label": "Trade in our books",
                    "ok": False,
                    "text": "Missing",
                },
                {
                    "key": "holding",
                    "label": "Holding",
                    "ok": False,
                    "text": "Not on statement",
                },
            ]
        else:
            title = f"{n} name(s): {verdict}{date_bit}"
            badge = str(verdict)
            pipe = []

        groups.append(
            {
                "verdict": verdict,
                "badge": badge,
                "title": title,
                "pipeline": pipe,
                "items": items,
                "item_labels": _dedupe_keep_order([it["label"] for it in items]),
                "next_steps": _canonical_next_steps(
                    verdict=verdict, trade_details_has=has_td
                ),
                "evidence_note": g.get("evidence_note") if has_td else None,
                "count": n,
                "reco_dates": dates,
                "symbols": _dedupe_keep_order([it["symbol"] for it in items]),
            }
        )
    return groups


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


def remember_client_pdf_password(client_id: int, password: str) -> bool:
    """Append a working PDF password for this client (not displayed in UI).

    Skips empty values and passwords already stored. Also skips values that
    match KYC/agreement PAN (those are resolved separately).
    """
    pw = (password or "").strip()
    if not pw:
        return False
    try:
        from services.regulatory_identity_capture_service import (
            get_client_document_passwords,
        )

        if pw in get_client_document_passwords(client_id):
            return False
    except Exception:
        pass
    link = load_trade_drive_link(client_id)
    existing = [
        str(p).strip() for p in (link.get("pdf_passwords") or []) if str(p).strip()
    ]
    if pw in existing:
        return False
    existing.append(pw)
    save_trade_drive_link(client_id, pdf_passwords=existing)
    return True


def client_saved_pdf_passwords(client_id: int) -> List[str]:
    link = load_trade_drive_link(client_id)
    return [
        str(p).strip() for p in (link.get("pdf_passwords") or []) if str(p).strip()
    ]


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
    # Default: KYC / agreement PAN (server-side only; not stored back into links JSON)
    try:
        from services.regulatory_identity_capture_service import (
            get_client_document_passwords,
        )

        for pan in get_client_document_passwords(client_id):
            if pan not in passwords:
                passwords.append(pan)
    except Exception:
        pass
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

    from services.reconciliation.trade_parsers import detect_and_parse_trade

    format_counts: Dict[str, int] = {}
    parse_warnings: List[str] = []

    for f in files:
        name = f.get("name") or ""
        mime = (f.get("mimeType") or "").lower()
        fid = f.get("id")
        if not fid:
            continue
        is_image = mime.startswith("image/") or name.lower().endswith(
            (".jpeg", ".jpg", ".png", ".webp")
        )
        dl = download_file_bytes(fid)
        if not dl.get("ok"):
            continue
        data = dl.get("content") or b""
        scanned += 1
        parsed = detect_and_parse_trade(
            data, filename=name or "trade.bin", passwords=passwords
        )
        format_counts[parsed.format_id] = format_counts.get(parsed.format_id, 0) + 1
        for w in parsed.warnings or []:
            parse_warnings.append(f"{name}: {w}")
        if is_image:
            screenshots.append(
                {
                    "name": name,
                    "id": fid,
                    "mimeType": mime,
                    "format_id": parsed.format_id,
                    "fills_parsed": len(parsed.fills),
                    "note": (
                        f"Screenshot parsed ({len(parsed.fills)} fills)."
                        if parsed.fills
                        else (
                            parsed.warnings[0]
                            if parsed.warnings
                            else "Screenshot — OCR required to extract fills."
                        )
                    ),
                }
            )
        if parsed.format_id in ("pdf_unreadable",) or (
            not parsed.fills
            and parsed.format_id.startswith("zerodha_contract")
            and parsed.warnings
        ):
            decrypt_fail += 1
        elif parsed.fills or parsed.format_id in (
            "zerodha_contract_note",
            "zerodha_tradebook_xlsx",
        ):
            decrypt_ok += 1
        fills.extend(parsed.fills_as_dicts())

    # Positions screens are not executed trades — drop from recon evidence
    fills = [
        x
        for x in fills
        if (x.get("status") or "executed").lower() == "executed"
        and (x.get("format_id") or "") != "broker_positions_screenshot"
    ]

    # Same security_maps.json as holdings recon → canonical Security.symbol
    try:
        from services.reconciliation.security_resolve import map_trade_fills

        fills = map_trade_fills(fills)
    except Exception as exc:
        logger.warning("map_trade_fills skipped: %s", exc)

    if want:
        fills = [
            x
            for x in fills
            if _norm(x.get("symbol")) in want
            or _norm(x.get("broker_symbol")) in want
        ]

    return {
        "ok": True,
        "folder_id": folder_id,
        "files_scanned": scanned,
        "decrypt_ok": decrypt_ok,
        "decrypt_fail": decrypt_fail,
        "fills": fills,
        "screenshots": screenshots,
        "passwords_configured": bool(passwords),
        "format_counts": format_counts,
        "parse_warnings": parse_warnings[:40],
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
        status = (r.status or "").strip().lower()
        sess_name = names.get(int(r.session_id)) if r.session_id else None
        # Prefer a real send date; else keep full session name (not truncated later).
        when_label = sent.isoformat() if sent else (sess_name or None)
        out.setdefault(sym, []).append(
            {
                "sent_at": sent.isoformat() if sent else None,
                "when_label": when_label,
                "action": (r.action or "").lower(),
                "quantity": float(r.quantity) if r.quantity is not None else None,
                "status": status,
                "executed_at": (
                    r.executed_at.isoformat() if r.executed_at else None
                ),
                "session_id": r.session_id,
                "session_name": sess_name,
                "id": r.id,
                "source": "system_reco",
                # Draft/pending with no send date are not emailed advice yet.
                "emailed": bool(sent)
                or status in ("sent", "executed", "closed", "partial"),
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
        keys = {
            _norm(f.get("symbol")),
            _norm(f.get("broker_symbol")),
        }
        for s in keys:
            if s and s in broker_by_sym:
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

        def _side_ok_book(tt: str, act: str) -> bool:
            tt = (tt or "").lower()
            if act == "buy":
                return tt in ("buy", "b")
            if act == "sell":
                return tt in ("sell", "s")
            return False

        def _near(
            d_iso: Optional[str], center: Optional[date], days: int = 45
        ) -> bool:
            if not center or not d_iso:
                return True
            try:
                td = date.fromisoformat(str(d_iso)[:10])
                return abs((td - center).days) <= days
            except Exception:
                return True

        def _book_covers(act: str, qty: float, sent: Optional[date]) -> bool:
            for t in books:
                if not _side_ok_book(t.get("type") or "", act):
                    continue
                if not _near(t.get("date"), sent):
                    continue
                if _qty_close(float(t.get("quantity") or 0), float(qty)):
                    return True
            window = []
            for t in books:
                if not _side_ok_book(t.get("type") or "", act):
                    continue
                if not _near(t.get("date"), sent, days=45):
                    continue
                window.append(float(t.get("quantity") or 0))
            if window and _qty_close(sum(window), float(qty), tol=1.0):
                return True
            if sent:
                by_day: Dict[str, float] = {}
                for t in books:
                    if not _side_ok_book(t.get("type") or "", act):
                        continue
                    if not _near(t.get("date"), sent, days=3):
                        continue
                    day = str(t.get("date") or "")[:10]
                    by_day[day] = by_day.get(day, 0.0) + float(t.get("quantity") or 0)
                if any(_qty_close(v, float(qty), tol=1.0) for v in by_day.values()):
                    return True
            return False

        def _broker_covers(
            act: str, qty: float, sent: Optional[date]
        ) -> Tuple[bool, Optional[str]]:
            for f in bfs:
                side = (f.get("side") or "buy").lower()
                if act != side:
                    continue
                if not _near(f.get("trade_date"), sent):
                    continue
                if _qty_close(float(f.get("quantity") or 0), float(qty)):
                    return True, f.get("source_file") or f.get("name")
            if sent:
                by_day_b: Dict[str, float] = {}
                by_day_file: Dict[str, str] = {}
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
                    if day not in by_day_file:
                        by_day_file[day] = f.get("source_file") or f.get("name") or ""
                for day, v in by_day_b.items():
                    if _qty_close(v, float(qty), tol=1.0):
                        return True, by_day_file.get(day)
            return False, None

        # Positive gap on statement vs books (shares ahead).
        gap_pos = (
            float(delta_f)
            if delta_f is not None and delta_f > 0.01
            else (
                float(stmt_f) - float(sys_f)
                if stmt_f is not None
                and sys_f is not None
                and float(stmt_f) > float(sys_f) + 0.01
                else 0.0
            )
        )

        # Build open emailed/sent candidates only (skip drafts like Aug BUY 5).
        candidates: List[Dict[str, Any]] = []
        for r in rs:
            st = (r.get("status") or "").lower()
            if st in ("executed", "closed", "cancelled"):
                continue
            if r.get("executed_at"):
                continue
            if not r.get("emailed") and st in ("pending", "draft", ""):
                continue
            act = (r.get("action") or "").lower()
            qty = r.get("quantity")
            if act not in ("buy", "sell") or qty is None:
                continue
            sent = None
            try:
                sent = date.fromisoformat(str(r.get("sent_at") or "")[:10])
            except Exception:
                sent = None
            if _book_covers(act, float(qty), sent):
                continue
            matched_broker, broker_file = _broker_covers(act, float(qty), sent)
            # Also treat broker match on partial held qty
            if (
                not matched_broker
                and act == "buy"
                and gap_pos > 0.01
                and gap_pos + 0.51 < float(qty)
            ):
                matched_broker, broker_file = _broker_covers(act, gap_pos, sent)
            candidates.append(
                {
                    "r": r,
                    "act": act,
                    "qty": float(qty),
                    "sent": sent,
                    "matched_broker": matched_broker,
                    "broker_file": broker_file,
                }
            )

        missing_from_books = []
        attributed = False  # one gap → one BUY story

        # Prefer: full qty match to gap, else largest emailed BUY that covers gap as partial
        buy_cands = [c for c in candidates if c["act"] == "buy"]
        buy_cands.sort(
            key=lambda c: (
                0 if gap_pos > 0 and _qty_close(gap_pos, c["qty"]) else 1,
                0 if c["r"].get("emailed") else 1,
                -c["qty"],
            )
        )

        for c in buy_cands:
            if attributed and gap_pos > 0.01:
                # Gap already explained by a better reco — do not invent more stories
                continue
            r = c["r"]
            qty = c["qty"]
            when = (
                r.get("when_label")
                or r.get("sent_at")
                or r.get("session_name")
                or "reco"
            )
            full = gap_pos > 0.01 and _qty_close(gap_pos, qty)
            partial = bool(
                gap_pos > 0.01 and (not full) and gap_pos + 0.51 < qty
            )
            if gap_pos <= 0.01:
                # No holdings ahead — reco may simply be undone
                holdings_ok = False
                partial = False
                held = None
            elif full:
                holdings_ok = True
                held = gap_pos
            elif partial:
                # e.g. holdings +3 belong to June BUY 30 — not to a draft BUY 5
                holdings_ok = True
                held = gap_pos
            else:
                # Gap larger than this reco or unrelated — skip this reco for booking story
                continue

            story = build_booking_gap_story(
                symbol=sym,
                action="buy",
                quantity=qty,
                when=str(when),
                book_has_trade=False,
                trade_details_has=c["matched_broker"],
                trade_details_file=c["broker_file"],
                trade_folder_linked=bool(broker.get("ok")),
                has_screenshots=bool(broker.get("screenshots")),
                holdings_ok=holdings_ok,
                held_qty=held,
                partial=partial,
            )
            missing_from_books.append(
                {
                    **r,
                    "book_match": False,
                    "broker_match": c["matched_broker"],
                    "verdict": story["verdict"],
                    "detail": story["detail"],
                    "headline": story["headline"],
                    "next_steps": story["next_steps"],
                    "pipeline": story["pipeline"],
                    "evidence_note": story.get("evidence_note"),
                    "trade_details_file": c["broker_file"],
                    "partial": partial,
                    "held_qty": held,
                    "story": story,
                }
            )
            findings.append(story["detail"])
            if holdings_ok:
                attributed = True

        # SELL candidates (unchanged idea; one story each if relevant)
        for c in candidates:
            if c["act"] != "sell":
                continue
            r = c["r"]
            qty = c["qty"]
            when = (
                r.get("when_label")
                or r.get("sent_at")
                or r.get("session_name")
                or "reco"
            )
            holdings_ahead = gap_pos > 0.01
            if holdings_ahead:
                holdings_ok = False
            elif delta_f is not None and delta_f < -0.01:
                holdings_ok = True
            else:
                holdings_ok = False
            story = build_booking_gap_story(
                symbol=sym,
                action="sell",
                quantity=qty,
                when=str(when),
                book_has_trade=False,
                trade_details_has=c["matched_broker"],
                trade_details_file=c["broker_file"],
                trade_folder_linked=bool(broker.get("ok")),
                has_screenshots=bool(broker.get("screenshots")),
                holdings_ok=holdings_ok,
            )
            missing_from_books.append(
                {
                    **r,
                    "book_match": False,
                    "broker_match": c["matched_broker"],
                    "verdict": story["verdict"],
                    "detail": story["detail"],
                    "headline": story["headline"],
                    "next_steps": story["next_steps"],
                    "pipeline": story["pipeline"],
                    "evidence_note": story.get("evidence_note"),
                    "trade_details_file": c["broker_file"],
                    "story": story,
                }
            )
            findings.append(story["detail"])

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
        for p in per_symbol
        for u in (p.get("missing_from_books") or [])
        if (u.get("verdict") or "") == "EXECUTED_NOT_RECORDED"
    )
    n_not_exec = sum(
        1
        for p in per_symbol
        for u in (p.get("missing_from_books") or [])
        if (u.get("verdict") or "") == "RECO_NOT_EXECUTED"
    )
    flat_stories: List[Dict[str, Any]] = []
    for p in per_symbol:
        for u in p.get("missing_from_books") or []:
            st = dict(u.get("story") or u)
            st.setdefault("symbol", p.get("symbol"))
            flat_stories.append(st)
    story_groups = group_booking_gap_stories(flat_stories)
    trade_map = build_reco_trade_map_rows(per_symbol)

    # Prefer group titles for summary (one line per conclusion), not per-name findings
    if story_groups:
        summary = " · ".join(
            str(g.get("title") or "").rstrip(".") for g in story_groups if g.get("title")
        )
        if summary:
            summary = summary + "."
        else:
            summary = ""
    elif findings:
        parts = []
        if n_missed:
            parts.append(
                f"{n_missed} name(s): recommended + in holdings, but trade missing from our books"
            )
        if n_not_exec:
            parts.append(f"{n_not_exec} name(s): recommendation may not have been done")
        summary = "; ".join(parts) + "."
    else:
        summary = (
            "No gaps for these names (or Trade details folder not linked / empty)."
            if broker.get("ok")
            else (broker.get("message") or "Trade details folder not linked.")
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
        "trade_map": trade_map,
        "story_groups": story_groups,
        # Findings kept for non-UI / legacy; UI prefers story_groups
        "findings": _dedupe_keep_order(list(findings or []))[:40],
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
    # Attach short note onto each break diagnosis (no full headline dump —
    # overview story_groups already show the shared narrative).
    by_sym = {p["symbol"]: p for p in pack.get("per_symbol") or []}
    for b in breaks or []:
        sym = _norm(b.get("symbol"))
        info = by_sym.get(sym)
        if not info:
            continue
        d = dict(b.get("diagnosis") or {})
        notes = list(d.get("investigation_notes") or [])
        stories = []
        short_labels: List[str] = []
        for u in info.get("missing_from_books") or info.get("unexecuted_recos") or []:
            stories.append(u.get("story") or u)
            verdict = u.get("verdict") or "EXECUTED_NOT_RECORDED"
            if verdict == "EXECUTED_NOT_RECORDED":
                label = "Trade missing from books"
            elif verdict == "RECO_NOT_EXECUTED":
                label = "Recommendation may not be done"
            else:
                label = str(verdict)
            short_labels.append(label)
            hyp = str(d.get("top_hypothesis") or "")
            if (
                not hyp
                or hyp in ("UNKNOWN", "QTY_BREAK", "MISSING_IN_SYSTEM", "CHECK_SENT_EMAIL")
                or hyp.startswith("TIMING")
                or hyp.startswith("RECO_NOT_EXECUTED")
                or verdict == "EXECUTED_NOT_RECORDED"
                or "not recorded" in hyp.lower()
                or "EXECUTED_NOT_RECORDED" in hyp
            ):
                if hyp and not d.get("prior_hypothesis") and hyp != label:
                    d["prior_hypothesis"] = hyp
                d["top_hypothesis"] = label
        for lab in _dedupe_keep_order(short_labels):
            if lab not in notes:
                notes.append(lab)
        if info.get("broker_fills"):
            note = (
                f"Trade details: found {len(info['broker_fills'])} broker trade(s) for {sym}."
            )
            if note not in notes:
                notes.append(note)
        elif pack.get("broker", {}).get("ok") and stories:
            note = (
                f"No {sym} trade found in linked Trade details "
                f"(checked {pack['broker'].get('files_scanned')} file(s))."
            )
            if note not in notes:
                notes.append(note)
        d["broker_evidence"] = {
            "broker_fills": info.get("broker_fills") or [],
            "unexecuted_recos": info.get("unexecuted_recos") or [],
            "missing_from_books": info.get("missing_from_books") or [],
            "stories": stories,
            "system_trade_count": len(info.get("system_trades") or []),
        }
        if stories:
            d["booking_gap_stories"] = stories
        d["investigation_notes"] = _dedupe_keep_order(notes)
        b["diagnosis"] = d
    pack["symbols"] = ordered
    return pack


def symbols_covered_by_story_groups(
    story_groups: Optional[List[Dict[str, Any]]],
    *,
    verdicts: Optional[Set[str]] = None,
) -> Set[str]:
    """Symbols already explained by Recommended→Trade→Holding groups."""
    out: Set[str] = set()
    want = verdicts
    for g in story_groups or []:
        if want is not None and (g.get("verdict") or "") not in want:
            continue
        for s in g.get("symbols") or []:
            if s:
                out.add(str(s).upper().strip())
        for it in g.get("items") or []:
            sym = (it.get("symbol") or "").upper().strip()
            if sym:
                out.add(sym)
    return out


def filter_hvr_hits_against_broker(
    hvr_pack: Dict[str, Any],
    broker_pack: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Drop Confirm-with-client rows already covered by broker story groups
    (same EXECUTED_NOT_RECORDED / trade-missing conclusion).
    """
    if not isinstance(hvr_pack, dict):
        return hvr_pack
    covered = symbols_covered_by_story_groups(
        broker_pack.get("story_groups") if isinstance(broker_pack, dict) else None,
        verdicts={"EXECUTED_NOT_RECORDED"},
    )
    if not covered:
        return hvr_pack
    hits = list(hvr_pack.get("hits") or [])
    kept = []
    for h in hits:
        sym = str(h.get("symbol") or "").upper().strip()
        hyp = str(h.get("hypothesis") or "")
        # Same story as Recommended→Trade→Holding — hide duplicate card line
        if sym in covered and hyp in (
            "EXECUTED_NOT_RECORDED",
            "POSSIBLE_DUPLICATE_CLIENT_ORDER",
        ):
            continue
        kept.append(h)
    out = dict(hvr_pack)
    out["hits"] = kept
    if kept:
        out["summary"] = (
            f"{len(kept)} name(s) need advisor confirmation with client "
            f"(not already covered by trade-missing groups)."
        )
    else:
        out["summary"] = (
            "No extra Confirm-with-client cues "
            "(covered by Recommended → Trade → Holding)."
            if hits
            else hvr_pack.get("summary")
        )
    out["deduped_against_broker"] = len(hits) - len(kept)
    return out


def slim_remaining_notes_against_broker(
    remaining_notes: List[Dict[str, Any]],
    broker_pack: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Keep pending expanders for qty, but drop repeated “what to do” prose
    when Recommended→Trade→Holding already covers the name.
    """
    covered = symbols_covered_by_story_groups(
        broker_pack.get("story_groups") if isinstance(broker_pack, dict) else None,
        verdicts={"EXECUTED_NOT_RECORDED", "RECO_NOT_EXECUTED"},
    )
    if not covered or not remaining_notes:
        return remaining_notes
    out: List[Dict[str, Any]] = []
    for n in remaining_notes:
        row = dict(n)
        sym = str(row.get("symbol") or "").upper().strip()
        if sym in covered:
            row["summary"] = "Covered above (Recommended → Trade → Holding)."
            row["potential_issues"] = []
            row["checks"] = []
            # Keep delta / gap / suspects for qty drill-down
        out.append(row)
    return out
