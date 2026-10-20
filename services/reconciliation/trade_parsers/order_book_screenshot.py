"""Mobile order-book screenshots (Angel One / ICICI Direct / similar).

Layout (Angel EXECUTED tab)::

    SUPREMEIND                    4,215.25   ← trade price (top-right)
    Qty 3/3 | 03-Oct-2025  Executed          ← filled/ordered qty + status
    BUY  DELIVERY  MARKET

``Qty 3/3`` = 3 filled of 3 ordered (use filled). ``Executed`` = trade done;
``Ordered`` / pending = order placed, not a fill for recon.

Requires OCR (pytesseract + tesseract). Without OCR we classify only.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from services.reconciliation.trade_parsers.base import (
    TradeFill,
    TradeParseResult,
    is_plausible_symbol,
    normalize_trade_symbol,
    parse_money,
)

FORMAT_ANGEL = "angel_order_book_screenshot"
FORMAT_ICICI = "icici_order_book_screenshot"
FORMAT_GROWW = "groww_order_report_screenshot"
FORMAT_POSITIONS = "broker_positions_screenshot"
FORMAT_GENERIC = "order_book_screenshot"

# NSE / broker renames + common OCR misreads
_SYMBOL_ALIASES = {
    "LTIM": "LTM",  # LTIM → LTM
    "LY": "LT",  # OCR of LT
    "LP": "LT",
    "L7": "LT",
    "LIA": "LT",  # OCR "Lia)" / "LIA"
    "LI4": "LT",
    "L1A": "LT",
}

# OCR often turns leading qty digit into a letter: 4Shares → AShares
_OCR_DIGIT = {
    "A": "4",
    "O": "0",
    "Q": "0",
    "I": "1",
    "L": "1",
    "S": "5",
    "B": "8",
    "G": "6",
    "Z": "2",
    "T": "7",
}

# Qty OCR often drops the leading Q: "Yty 3/3", "ty 11/11", "y 2/2"
_QTY_DATE_RE = re.compile(
    r"(?:Qty|Yty|Qtv|[Qq]?[Tt]y|[Yy])\s*"
    r"(?P<filled>\d+(?:\.\d+)?)\s*/\s*(?P<total>\d+(?:\.\d+)?)"
    r".{0,40}?"
    r"(?P<d>\d{1,2})[-/| ]+(?P<mon>[A-Za-z]{3}|\d{1,2})[-/| ]+(?P<y>20\d{2})",
    re.I | re.S,
)
# Symbol + price on one line (common after upscale OCR).
# Prefer full digit runs (2411.70) — do NOT stop at 3 digits (old bug → 241).
_SYM_PRICE_RE = re.compile(
    r"^(?P<sym>[A-Za-z][A-Za-z0-9]{1,15})"
    r"\s+(?P<px>[\d,]+(?:\.\d+)?)\s*$"
)
_PRICE_LINE_RE = re.compile(
    r"^(?:₹|Rs\.?\s*)?(?P<px>[\d,]+(?:\.\d+)?)\s*$"
)
# Plausible equity/ETF trade price (reject OCR junk like 90773)
_MAX_PLAUSIBLE_PRICE = 200_000.0
_MIN_PLAUSIBLE_PRICE = 0.05
_SYM_ONLY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,15}$")
_SIDE_RE = re.compile(r"\b(BUY|SELL)\b", re.I)
_STATUS_RE = re.compile(r"\b(Executed|Ordered|Pending|Open|Cancelled|Rejected)\b", re.I)
_MON = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def classify_order_book_text(text: str) -> str:
    u = (text or "").upper()
    if "EATM" in u or "ICICI" in u or "IDIRECT" in u:
        return FORMAT_ICICI
    # Open Positions (Avg / LTP) — not executed trade fills
    if "POSITIONS" in u and (
        "AVG" in u or "LTP" in u or "EXIT ALL" in u or "SECURE EXIT" in u or "SECUREIEX" in u
    ):
        return FORMAT_POSITIONS
    # Groww Holdings (portfolio) — ATP avg, not order fills
    if "HOLDINGS" in u and "ATP" in u and "ORDER REPORT" not in u:
        return FORMAT_POSITIONS
    # Groww Order Report: "ORDER AGAIN" / "Order Report" / "Share ORDER"
    if (
        "ORDER REPORT" in u
        or "ORDER AGAIN" in u
        or ("GROWW" in u)
        or re.search(r"\d+\s*/\s*\d+\s+SHARES?\b", u)
    ):
        if "ORDER BOOK" not in u and "GTT" not in u:
            return FORMAT_GROWW
    if "ANGEL" in u or "ORDER BOOK" in u or "GTT" in u:
        return FORMAT_ANGEL
    return FORMAT_GENERIC


def _plausible_price(px: Optional[float]) -> Optional[float]:
    if px is None:
        return None
    try:
        v = float(px)
    except Exception:
        return None
    if v < _MIN_PLAUSIBLE_PRICE or v > _MAX_PLAUSIBLE_PRICE:
        return None
    # Whole-rupee smash only when clearly not a stock price (90773 for 90.75)
    if abs(v - round(v)) < 1e-9:
        iv = int(round(v))
        digits = str(abs(iv))
        if len(digits) >= 5 and iv > 50000:
            try:
                fixed = float(f"{digits[:2]}.{digits[2:]}")
                if _MIN_PLAUSIBLE_PRICE <= fixed <= 5000:
                    return round(fixed, 4)
            except Exception:
                pass
            return None
    return v


def _accept_symbol(sym: str) -> bool:
    s = (sym or "").upper().strip()
    if not s or not is_plausible_symbol(s):
        return False
    if len(s) == 2 and s not in _TWO_LETTER_OK and s not in _SYMBOL_ALIASES:
        return False
    return True


_POS_SHARES_RE = re.compile(
    r"(?:(?P<qty>-?\d+)|(?P<ocr>[A-Za-z]))\s*Shares?\b",
    re.I,
)
# Pending/history rows: "2/2 Shares LTP …" (no Avg)
_POS_SLASH_SHARES_RE = re.compile(
    r"(?P<filled>\d+(?:\.\d+)?)\s*/\s*(?P<total>\d+(?:\.\d+)?)\s*Shares?\b",
    re.I,
)
_POS_AVG_RE = re.compile(
    r"Avg\.?\s*[^\d\s,]*\s*(?P<avg>[\d,]+(?:\.\d+)?)",
    re.I,
)
# Groww Holdings: "20 X ATP 1,242.17 LTP 242.00"
_HOLDINGS_ATP_RE = re.compile(
    r"(?P<qty>\d+)\s*[xX×]\s*ATP\s*[^\d\s,]*\s*(?P<atp>[\d,]+(?:\.\d+)?)",
    re.I,
)
_POS_LTP_RE = re.compile(
    r"LTP\s*[^\d\s,]*\s*(?P<ltp>[\d,]+(?:\.\d+)?)",
    re.I,
)
# Two-letter tickers we accept (NSE). Others (IN/UN/IL) are OCR junk.
_TWO_LETTER_OK = frozenset({"LT", "ITC"})


def _price_candidates(raw: Optional[float]) -> List[float]:
    """OCR often glues ₹→'2' onto the price — offer stripped alternative."""
    out: List[float] = []
    if raw is None:
        return out
    try:
        v = float(raw)
    except Exception:
        return out
    seen = set()

    def _add(x: Optional[float]) -> None:
        px = _plausible_price(x)
        if px is None:
            return
        key = round(px, 4)
        if key in seen:
            return
        seen.add(key)
        out.append(px)

    _add(v)
    s = ("%f" % v).rstrip("0").rstrip(".")
    # ₹ misread as leading '2' only when glued into a larger number
    # (2242.17→242.17, 2411.70→411.70). Do NOT strip real prices like 237.79.
    if v >= 1000 and s.startswith("2") and len(s) > 1 and s[1].isdigit():
        try:
            _add(float(s[1:]))
        except Exception:
            pass
    return out


def _ltp_anchors(ltp: Optional[float]) -> List[float]:
    """LTP anchors for ATP disambiguation.

    Trust high LTPs (SHREECEM ~25k). Only ₹-strip when LTP is retail-scale
    or looks like glued ``21xxx`` (₹ + 1xxx).
    """
    v = _plausible_price(ltp)
    if v is None:
        return []
    s = ("%f" % v).rstrip("0").rstrip(".")
    if v < 15000 or s.startswith("21"):
        return _price_candidates(v)
    return [v]


def _pick_price_near_ltp(
    raw: Optional[float],
    ltp: Optional[float],
    *,
    ref_price: Optional[float] = None,
) -> Optional[float]:
    cands = _price_candidates(raw)
    if not cands:
        return None
    # Prefer DB/current market price when available (catches digit drops)
    if ref_price is not None:
        refined = refine_ocr_price(raw, ref_price)
        if refined is not None:
            return refined
    if ltp is None:
        # Prefer ₹-stripped form when original looks glued (2xxxx vs xxxx)
        if len(cands) >= 2 and cands[0] > 10000:
            ratio = cands[0] / max(cands[1], 1e-9)
            if 1.5 < ratio < 25:
                return cands[1]
        return cands[0]
    anchors = _ltp_anchors(ltp)
    if not anchors:
        return cands[0]
    # Minimize |ATP−LTP|; on a tie prefer the smaller cand (₹→2 glue)
    best_c = cands[0]
    best_score: Optional[float] = None
    for c in cands:
        score = min(abs(c - a) for a in anchors)
        if best_score is None or score < best_score - 1e-6 or (
            abs(score - best_score) <= 1e-6 and c < best_c
        ):
            best_score = score
            best_c = c
    return best_c


def refine_ocr_price(
    ocr_price: Optional[float], ref_price: Optional[float]
) -> Optional[float]:
    """Pick OCR price candidate closest to a trusted ref (e.g. Security.current_price).

    Recovers dropped leading digits (37.79 vs 237.79) and ₹→2 glue
    (2411.70 vs 411.70). No-op when OCR is already near the ref.
    """
    ocr = _plausible_price(ocr_price)
    if ocr is None:
        return None
    ref = _plausible_price(ref_price)
    if ref is None or ref <= 0:
        return ocr

    cands: List[float] = list(_price_candidates(ocr))
    ocr_s = ("%f" % ocr).rstrip("0").rstrip(".")
    ref_int = str(int(round(abs(ref))))
    # Prepend 1–2 digits from the ref magnitude (digit dropped by OCR / over-strip)
    for n in (1, 2):
        if len(ref_int) >= n and ocr_s:
            try:
                pref = _plausible_price(float(ref_int[:n] + ocr_s))
                if pref is not None:
                    cands.append(pref)
            except Exception:
                pass

    seen = set()
    uniq: List[float] = []
    for c in cands:
        if c is None:
            continue
        key = round(float(c), 4)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(float(c))
    if not uniq:
        return ocr

    best = min(uniq, key=lambda c: abs(c - ref))
    ocr_err = abs(ocr - ref) / ref
    best_err = abs(best - ref) / ref
    if best_err < ocr_err and (ocr_err >= 0.12 or best_err <= 0.05):
        return round(best, 4)
    return ocr


def _pos_qty_from_match(m: re.Match) -> Optional[float]:
    if m.groupdict().get("qty"):
        try:
            return abs(float(m.group("qty")))
        except Exception:
            return None
    ocr = (m.groupdict().get("ocr") or "").upper()
    digit = _OCR_DIGIT.get(ocr)
    if digit is None:
        return None
    try:
        return abs(float(digit))
    except Exception:
        return None


# Groww: AUROPHARMA 1,658.00 / BUY DEL NSE EXECUTED at 3:29PM / 5/5 Shares
_GROWW_QTY_RE = re.compile(
    r"(?P<filled>\d+(?:\.\d+)?)\s*/\s*(?P<total>\d+(?:\.\d+)?)\s*Shares?\b",
    re.I,
)
_GROWW_DATE_RE = re.compile(
    r"^(?P<d>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\s+(?P<y>20\d{2})\s*$",
    re.I,
)


def apply_broker_symbol_alias(sym: str) -> str:
    s = (sym or "").upper().strip()
    return _SYMBOL_ALIASES.get(s, s)


def _ocr_image(data: bytes) -> Tuple[str, Optional[str]]:
    try:
        from io import BytesIO

        from PIL import Image, ImageFilter, ImageOps
        import pytesseract
    except ImportError as exc:
        return "", f"OCR deps missing: {exc}"
    from services.reconciliation.trade_parsers.ocr_env import configure_pytesseract

    cfg_err = configure_pytesseract()
    if cfg_err:
        return "", cfg_err
    try:
        img = Image.open(BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # 2× upscale + grayscale — recovers "Qty" and same-line prices on phone shots
        w, h = img.size
        img = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
        img = ImageOps.grayscale(img)
        img = img.filter(ImageFilter.SHARPEN)
        texts = []
        for cfg in ("--psm 6", "--psm 4"):
            t = pytesseract.image_to_string(img, config=cfg) or ""
            texts.append(t)

        def _score(t: str) -> int:
            u = t.upper()
            return (
                u.count("QTY")
                + u.count("EXECUTED")
                + u.count("ORDER AGAIN")
                + len(re.findall(r"\b[A-Z]{3,12}\s+\d", t))
            )

        text = max(texts, key=_score) if texts else ""
        return text, None
    except Exception as exc:
        return "", f"OCR failed: {exc}"


def _parse_trade_date(mon: str, d: str, y: str) -> Optional[str]:
    try:
        if mon.isdigit():
            month = int(mon)
        else:
            month = _MON.get(mon[:3].lower(), 0)
        if month == 0:
            return None
        return f"{int(y):04d}-{month:02d}-{int(d):02d}"
    except Exception:
        return None


def parse_groww_order_report_text(
    text: str, *, source_name: str = "", executed_only: bool = True
) -> TradeParseResult:
    """
    Groww Order Report rows::

        AUROPHARMA 1,658.00
        BUY DEL NSE © EXECUTED at 3:29PM
        5/5 Shares ORDER AGAIN
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    fills: List[TradeFill] = []
    skipped = 0
    section_date: Optional[str] = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        dm = _GROWW_DATE_RE.match(ln)
        if dm:
            section_date = _parse_trade_date(dm.group("mon"), dm.group("d"), dm.group("y"))
            i += 1
            continue
        sp = _SYM_PRICE_RE.match(ln)
        if not sp:
            i += 1
            continue
        sym = normalize_trade_symbol(sp.group("sym"))
        if len(sym) < 3 or not is_plausible_symbol(sym):
            i += 1
            continue
        if sym in {"QIY", "QTY", "YTY", "QTV", "TY", "ALL", "NSE", "BSE", "DEL"}:
            i += 1
            continue
        price = _plausible_price(parse_money(sp.group("px")))
        if not _accept_symbol(sym):
            i += 1
            continue
        # Look ahead a few lines for side/status + qty Shares (stop at next symbol)
        end = i + 1
        while end < len(lines) and end < i + 5:
            if _SYM_PRICE_RE.match(lines[end]) or _GROWW_DATE_RE.match(lines[end]):
                break
            end += 1
        block = " | ".join(lines[i:end])
        st = _STATUS_RE.search(block)
        status = st.group(1).lower() if st else ""
        if executed_only and status != "executed":
            if "EXECUTED" not in block.upper():
                skipped += 1
                i += 1
                continue
            status = "executed"
        sm = _SIDE_RE.search(block)
        side = "sell" if sm and sm.group(1).upper() == "SELL" else "buy"
        qm = _GROWW_QTY_RE.search(block)
        qty: Optional[float] = None
        qty_missing = False
        if qm:
            try:
                qty = float(qm.group("filled"))
            except Exception:
                qty = None
        elif (status == "executed" or "EXECUTED" in block.upper()) and price is not None:
            # Last row often truncated — still emit so mapping can resolve the symbol
            qty = 0.0
            qty_missing = True
            status = status or "executed"
        else:
            i += 1
            continue
        if qty is None or qty < 0 or (qty == 0 and not qty_missing):
            i += 1
            continue
        amount = (
            round(qty * price, 2) if price is not None and qty > 0 else None
        )
        broker_sym = sym
        canon = apply_broker_symbol_alias(broker_sym)
        key = (canon, qty, price, section_date, side)
        if key in {(f.symbol, f.quantity, f.price, f.trade_date, f.side) for f in fills}:
            i = end if end > i + 1 else i + 1
            continue
        fills.append(
            TradeFill(
                symbol=canon,
                quantity=qty,
                side=side,
                price=price,
                amount=amount,
                trade_date=section_date,
                source_file=source_name,
                format_id=FORMAT_GROWW,
                raw=block[:240],
                broker_symbol=broker_sym,
                status=status or "executed",
            ).ensure_amount()
        )
        if qty_missing:
            fills[-1].raw = ((fills[-1].raw or "") + " | qty_missing").strip(" |")
        i = end if end > i + 1 else i + 1

    warnings = []
    if not fills:
        warnings.append(f"{FORMAT_GROWW}: no executed order rows parsed")
    if skipped:
        warnings.append(f"skipped {skipped} non-executed Groww order(s)")
    missing_qty = sum(1 for f in fills if f.quantity == 0)
    if missing_qty:
        warnings.append(
            f"{missing_qty} executed row(s) missing qty (screenshot truncated?)"
        )
    return TradeParseResult(
        format_id=FORMAT_GROWW,
        fills=fills,
        warnings=warnings,
        meta={"ocr_lines": len(lines), "skipped_non_executed": skipped},
    )


def parse_positions_ocr_text(
    text: str, *, source_name: str = ""
) -> TradeParseResult:
    """
    Broker Positions tab (open qty + avg) — useful for lab preview, **not**
    executed trade fills for recon.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    fills: List[TradeFill] = []
    i = 0
    seen = set()
    while i < len(lines):
        ln = lines[i]
        # UI chrome / qty rows — never symbols
        if ln[:1].islower() or re.search(
            r"(?i)shares?\b|\bavg\.?\b|\batp\b|\bltp\b", ln
        ):
            i += 1
            continue
        first = ln.split()[0] if ln.split() else ""
        sym = normalize_trade_symbol(first)
        if (
            not _accept_symbol(sym)
            or sym.endswith("SHARES")
            or sym
            in {
                "QIY",
                "QTY",
                "ORDERS",
                "POSITIONS",
                "CASH",
                "HOME",
                "TOTAL",
                "NIFTY",
                "SENSEX",
                "SOLD",
                "WATCHLIST",
                "PORTFOLIO",
                "ACCOUNT",
                "BUY",
                "EXIT",
                "HOLDINGS",
                "OVERVIEW",
                "EQUITY",
                "MUTUAL",
                "FUNDS",
                "SEARCH",
                "OH",
                "GB",
                "AA",
                "IN",
                "UN",
                "IL",
                "RL",
            }
        ):
            i += 1
            continue
        # Stop before next symbol so ATP/Shares of the following row are not borrowed.
        # Keep UI rows (SOLD FROM / BUY DEL / EXIT) inside the window for side.
        _pos_ui = re.compile(
            r"(?i)^(?:SOLD|BUY|EXIT|SECURE|CASH|TOTAL|HOME|WATCHLIST|"
            r"PORTFOLIO|ORDERS|ACCOUNT|AGAIN)\b"
        )
        end = i + 1
        while end < len(lines) and end < i + 5:
            nxt = lines[end]
            if (
                nxt[:1].islower()
                or re.search(r"(?i)shares?\b|\bavg\.?\b|\batp\b|\bltp\b", nxt)
                or _pos_ui.match(nxt)
            ):
                end += 1
                continue
            nf = nxt.split()[0] if nxt.split() else ""
            ns = normalize_trade_symbol(nf)
            if ns != sym and _accept_symbol(ns) and not ns.endswith("SHARES"):
                break
            end += 1
        window = " | ".join(lines[i:end])
        qm = _POS_SHARES_RE.search(window)
        sm = _POS_SLASH_SHARES_RE.search(window)
        am = _POS_AVG_RE.search(window)
        hm = _HOLDINGS_ATP_RE.search(window)
        lm = _POS_LTP_RE.search(window)
        ltp = parse_money(lm.group("ltp")) if lm else None
        qty = None
        price = None
        neg_shares = False

        def _sym_line_price(line: str) -> Optional[float]:
            sp = _SYM_PRICE_RE.match(line)
            if sp:
                return parse_money(sp.group("px"))
            # "PIIND %3,059.40" / "SHREECEM ~25,410.00"
            pm = re.search(
                r"^[A-Za-z][A-Za-z0-9]{1,15}\s*[^\d\s,]*\s*([\d,]+(?:\.\d+)?)",
                line,
            )
            return parse_money(pm.group(1)) if pm else None

        if hm:
            try:
                qty = float(hm.group("qty"))
            except Exception:
                qty = None
            price = _pick_price_near_ltp(parse_money(hm.group("atp")), ltp)
        elif sm:
            # Prefer N/N Shares over bare "2 Shares" (avoids matching denom of 2/2)
            try:
                qty = float(sm.group("filled"))
            except Exception:
                qty = None
            price = _pick_price_near_ltp(_sym_line_price(ln), ltp)
        elif qm:
            raw_q = qm.groupdict().get("qty")
            if raw_q is not None:
                try:
                    neg_shares = float(raw_q) < 0
                except Exception:
                    neg_shares = False
            qty = _pos_qty_from_match(qm)
            raw_avg = parse_money(am.group("avg")) if am else None
            if raw_avg is None:
                raw_avg = _sym_line_price(ln)
            price = _pick_price_near_ltp(raw_avg, ltp)
        if qty is None or qty <= 0:
            i += 1
            continue
        side = (
            "sell"
            if neg_shares or "SOLD FROM" in window.upper()
            else "buy"
        )
        amount = round(qty * price, 2) if price is not None else None
        broker_sym = sym
        canon = apply_broker_symbol_alias(broker_sym)
        key = (canon, qty, price)
        if key in seen:
            i += 1
            continue
        seen.add(key)
        fills.append(
            TradeFill(
                symbol=canon,
                quantity=qty,
                side=side,
                price=price,
                amount=amount,
                source_file=source_name,
                format_id=FORMAT_POSITIONS,
                raw=window[:240],
                broker_symbol=broker_sym,
                status="position",
            ).ensure_amount()
        )
        i += 1
    return TradeParseResult(
        format_id=FORMAT_POSITIONS,
        fills=fills,
        warnings=[
            "Positions/Holdings screen — open qty + avg, not executed trades. "
            "Use Order Report / EXECUTED order book / contract note for fills."
        ],
        meta={"ocr_lines": len(lines), "is_positions": True},
    )


def parse_order_book_ocr_text(
    text: str,
    *,
    source_name: str = "",
    format_id: Optional[str] = None,
    executed_only: bool = True,
) -> TradeParseResult:
    """
    Parse OCR text into fills.

    ``executed_only`` (default True): skip Ordered / Pending rows — those are
    not executed trades.
    """
    fmt = format_id or classify_order_book_text(text)
    if fmt == FORMAT_POSITIONS or (
        (
            "POSITIONS" in (text or "").upper()
            and ("AVG" in (text or "").upper() or "LTP" in (text or "").upper())
        )
        or ("HOLDINGS" in (text or "").upper() and "ATP" in (text or "").upper())
    ):
        return parse_positions_ocr_text(text, source_name=source_name)
    if fmt == FORMAT_GROWW or (
        "ORDER AGAIN" in (text or "").upper() or "ORDER REPORT" in (text or "").upper()
    ):
        return parse_groww_order_report_text(
            text, source_name=source_name, executed_only=executed_only
        )

    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    fills: List[TradeFill] = []
    skipped_status = 0
    i = 0
    while i < len(lines):
        ln = lines[i]
        # Qty lines (OCR: Qty / Qiy / Yty / ty / y 3/3 …) are never symbols
        if re.match(r"(?i)^(?:q(?:ty|iy|tv)|yty|[ty])\b", ln) or re.search(
            r"(?i)^(?:q(?:ty|iy|tv)|yty|[ty])\s*\d+\s*/\s*\d+", ln
        ):
            i += 1
            continue
        if re.match(r"(?i)^(buy|sell|executed|ordered|pending)\b", ln) and len(ln) < 40:
            i += 1
            continue

        sym_cand = None
        price = None
        sp = _SYM_PRICE_RE.match(ln)
        if sp:
            sym_cand = normalize_trade_symbol(sp.group("sym"))
            price = parse_money(sp.group("px"))
        elif _SYM_ONLY_RE.match(ln.split()[0] if ln.split() else ""):
            sym_cand = normalize_trade_symbol(ln.split()[0])
            if i + 1 < len(lines):
                pm = _PRICE_LINE_RE.match(lines[i + 1])
                if pm:
                    price = parse_money(pm.group("px"))

        if not sym_cand or not _accept_symbol(sym_cand):
            i += 1
            continue
        # OCR misreads of "Qty" as a ticker
        if sym_cand in {"QIY", "QTY", "YTY", "QTV", "TY"}:
            i += 1
            continue

        # Only lines until the *next* symbol — never borrow NAUKRI's qty for INFY
        end = i + 1
        while end < len(lines) and end < i + 6:
            nxt = lines[end]
            spn = _SYM_PRICE_RE.match(nxt)
            if spn:
                ns = normalize_trade_symbol(spn.group("sym"))
                if ns != sym_cand and _accept_symbol(ns) and ns not in {
                    "QIY",
                    "QTY",
                    "YTY",
                    "QTV",
                    "TY",
                }:
                    break
            elif _SYM_ONLY_RE.match(nxt.split()[0] if nxt.split() else ""):
                ns = normalize_trade_symbol(nxt.split()[0])
                if (
                    ns != sym_cand
                    and _accept_symbol(ns)
                    and ns not in {"QIY", "QTY", "YTY", "QTV", "TY"}
                    and not re.match(r"(?i)^(?:q(?:ty|iy|tv)|yty|[ty])\b", nxt)
                ):
                    break
            end += 1
        block_lines = lines[i:end]
        window = " | ".join(block_lines)
        qm = _QTY_DATE_RE.search(window)
        if not qm:
            i += 1
            continue
        try:
            qty = float(qm.group("filled"))  # filled side of Qty filled/ordered
        except Exception:
            i += 1
            continue
        if qty <= 0:
            i += 1
            continue

        sm = _SIDE_RE.search(window)
        side = "sell" if sm and sm.group(1).upper() == "SELL" else "buy"
        st = _STATUS_RE.search(window)
        status = st.group(1).lower() if st else "executed"
        if executed_only and status != "executed":
            skipped_status += 1
            i += 1
            continue

        td = _parse_trade_date(qm.group("mon"), qm.group("d"), qm.group("y"))

        if price is None:
            for bl in block_lines:
                sp2 = _SYM_PRICE_RE.match(bl)
                if sp2 and normalize_trade_symbol(sp2.group("sym")) == sym_cand:
                    price = _plausible_price(parse_money(sp2.group("px")))
                    break
                pm = _PRICE_LINE_RE.match(bl)
                if pm:
                    price = _plausible_price(parse_money(pm.group("px")))
                    break
        else:
            price = _plausible_price(price)

        amount = round(qty * price, 2) if price is not None else None
        broker_sym = sym_cand
        canon = apply_broker_symbol_alias(broker_sym)

        fills.append(
            TradeFill(
                symbol=canon,
                quantity=qty,
                side=side,
                price=price,
                amount=amount,
                trade_date=td,
                source_file=source_name,
                format_id=fmt,
                raw=window[:240],
                broker_symbol=broker_sym,
                status=status,
            ).ensure_amount()
        )
        i += 1

    warnings = []
    if not fills:
        warnings.append(f"{fmt}: no executed order rows parsed from OCR text")
    if skipped_status:
        warnings.append(f"skipped {skipped_status} non-executed order(s)")
    return TradeParseResult(
        format_id=fmt,
        fills=fills,
        warnings=warnings,
        meta={"ocr_lines": len(lines), "skipped_non_executed": skipped_status},
    )


def parse_order_book_screenshot(
    data: bytes, *, source_name: str = ""
) -> TradeParseResult:
    text, err = _ocr_image(data)
    fmt = classify_order_book_text(text) if text else FORMAT_GENERIC
    if err or not text.strip():
        return TradeParseResult(
            format_id=fmt,
            fills=[],
            warnings=[
                err
                or "screenshot: no OCR text (install tesseract + pytesseract to parse)"
            ],
            meta={"ocr_available": False, "ocr_text": text or ""},
        )
    result = parse_order_book_ocr_text(text, source_name=source_name, format_id=fmt)
    result.meta = {**(result.meta or {}), "ocr_available": True, "ocr_text": text}
    return result
