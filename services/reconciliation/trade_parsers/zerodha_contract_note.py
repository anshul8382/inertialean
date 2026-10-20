"""Zerodha contract note PDF — parse order fill lines (not obligation junk)."""
from __future__ import annotations

import re
from datetime import date
from typing import List, Optional, Tuple

from services.reconciliation.trade_parsers.base import (
    TradeFill,
    TradeParseResult,
    is_plausible_symbol,
    normalize_trade_symbol,
    parse_money,
)

FORMAT_ID = "zerodha_contract_note"

# ORDER line: SYMBOL-EQ/INE... B NSE 10 0.0000 706.6
# Also: BAJAJCON-B/INE... S BSE 70 ; P A Y TM-EQ/INE... ; DI VISLAB-EQ/INE...
# Do not allow digits to start the symbol (avoids "11:21:37 MARICO" → 37MARICO).
_ORDER_RE = re.compile(
    r"(?<![A-Z0-9])(?P<sym>[A-Z](?:[A-Z]|[ .]){1,30}?)"
    r"\s*-\s*(?:EQ|BE|BZ|IF|B|SM|RE)?"
    r"\s*/\s*(?P<isin>INE[0-9A-Z]{9,12})"
    r"\s+(?P<side>[BS])\s+(?P<ex>NSE|BSE)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)"
    r"(?:\s+(?P<br>\d+(?:\.\d+)?)\s+(?P<price>\d+(?:\.\d+)?))?",
    re.I,
)

_TRADE_DATE_RE = re.compile(
    r"Trade\s*Date\s*:\s*(\d{1,2})/(\d{1,2})/(20\d{2})",
    re.I,
)

_CONTRACT_MARKERS = (
    "ZERODHA",
    "CONTRACT NOTE",
    "CONTRACT NOTE CUM",
    "ZER O DHA",  # spaced PDF extract
)


def looks_like_zerodha_contract(text: str, filename: str = "") -> bool:
    u = (text or "").upper()
    fn = (filename or "").upper()
    if "CONTRACT" in fn and ("UDZ" in fn or "TV" in fn or "ZERODHA" in u):
        return True
    hits = sum(1 for m in _CONTRACT_MARKERS if m in u)
    return hits >= 2 or ("ZERODHA" in u and "CONTRACT" in u)


def _trade_date_from_text(text: str) -> Optional[date]:
    m = _TRADE_DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except Exception:
        return None


def parse_zerodha_contract_text(
    text: str,
    *,
    source_name: str = "",
    trade_date: Optional[date] = None,
) -> TradeParseResult:
    warnings: List[str] = []
    td = trade_date or _trade_date_from_text(text)
    fills: List[TradeFill] = []
    seen = set()

    # Normalize common spaced extract quirks for matching
    flat = text or ""
    for ln in flat.splitlines():
        raw = ln.strip()
        if not raw or "INE" not in raw.upper():
            continue
        # Skip obligation summary table header noise
        if "OBLIGATION FOR ISIN" in raw.upper() and "BUY" in raw.upper():
            continue
        m = _ORDER_RE.search(raw.upper().replace("  ", " "))
        if not m:
            # try original line (case)
            m = _ORDER_RE.search(raw)
        if not m:
            continue
        sym = normalize_trade_symbol(m.group("sym"))
        sym = re.sub(r"^\d+", "", sym)  # belt-and-braces vs timestamp glue
        if not is_plausible_symbol(sym):
            continue
        if not re.match(r"^[A-Z]", sym):
            continue
        isin = m.group("isin").upper()
        side = "buy" if m.group("side").upper() == "B" else "sell"
        try:
            qty = float(m.group("qty"))
        except Exception:
            continue
        if qty <= 0:
            continue
        price = parse_money(m.groupdict().get("price"))
        # Fallback: last money-like token on the line (often trade price)
        if price is None:
            nums = re.findall(r"\d+(?:\.\d+)?", raw.replace(",", ""))
            # pattern: qty, maybe brokerage, price, (amount)
            if len(nums) >= 3:
                try:
                    # after qty index — prefer a value that looks like a price
                    cand = float(nums[-2]) if len(nums) >= 4 else float(nums[-1])
                    if cand > 0:
                        price = cand
                except Exception:
                    price = None
        amount = round(qty * price, 2) if price is not None else None
        key = (sym, isin, side, qty, raw[:80])
        if key in seen:
            continue
        seen.add(key)
        fills.append(
            TradeFill(
                symbol=sym,
                isin=isin,
                quantity=qty,
                side=side,
                price=price,
                amount=amount,
                trade_date=td.isoformat() if td else None,
                source_file=source_name,
                format_id=FORMAT_ID,
                exchange=m.group("ex").upper(),
                raw=raw[:240],
            ).ensure_amount()
        )

    # Aggregate same symbol/side/isin/date (split fills); qty-weighted avg price
    agg: dict = {}
    for f in fills:
        k = (f.symbol, f.isin, f.side, f.trade_date)
        if k not in agg:
            agg[k] = f
        else:
            prev = agg[k]
            q1, q2 = float(prev.quantity), float(f.quantity)
            if prev.price is not None and f.price is not None and (q1 + q2) > 0:
                prev.price = round((prev.price * q1 + f.price * q2) / (q1 + q2), 4)
            elif f.price is not None and prev.price is None:
                prev.price = f.price
            prev.quantity = q1 + q2
            if prev.amount is not None and f.amount is not None:
                prev.amount = round(float(prev.amount) + float(f.amount), 2)
            else:
                prev.ensure_amount()

    out = [f.ensure_amount() for f in agg.values()]
    if not out and text:
        warnings.append("zerodha_contract_note: no order lines matched")
    return TradeParseResult(
        format_id=FORMAT_ID,
        fills=out,
        trade_date=td,
        warnings=warnings,
        meta={"order_line_count": len(fills), "aggregated": len(out)},
    )
