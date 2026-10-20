"""Zerodha Console tradebook Excel (Symbol / ISIN / Trade Date / Trade Type / Quantity)."""
from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from typing import Any, List, Optional

from services.reconciliation.trade_parsers.base import (
    TradeFill,
    TradeParseResult,
    is_plausible_symbol,
    normalize_trade_symbol,
    parse_money,
)

FORMAT_ID = "zerodha_tradebook_xlsx"

_HEADER_ALIASES = {
    "symbol": {"symbol", "tradingsymbol", "scrip", "stock name", "scrip name"},
    "isin": {"isin"},
    "trade_date": {"trade date", "tradedate", "date", "order date", "execution date"},
    "side": {
        "trade type",
        "tradetype",
        "type",
        "side",
        "transaction",
        "buy/sell",
        "buy sell",
        "action",
    },
    "quantity": {"quantity", "qty", "traded quantity", "filled qty", "trade quantity"},
    "price": {
        "price",
        "avg price",
        "average price",
        "trade price",
        "avg. price",
        "rate",
        "traded price",
    },
    "amount": {
        "amount",
        "value",
        "trade value",
        "traded value",
        "net amount",
        "total",
        "consideration",
    },
    "exchange": {"exchange"},
}


def _norm_header(cell: Any) -> str:
    return str(cell or "").strip().lower()


def _map_headers(header_row: List[Any]) -> dict:
    idx = {}
    for i, cell in enumerate(header_row or []):
        h = _norm_header(cell)
        if not h:
            continue
        for key, aliases in _HEADER_ALIASES.items():
            if h in aliases and key not in idx:
                idx[key] = i
    return idx


def looks_like_zerodha_tradebook_xlsx(rows: List[List[Any]]) -> bool:
    for row in rows[:40]:
        idx = _map_headers(list(row or []))
        if "symbol" in idx and "quantity" in idx and ("side" in idx or "isin" in idx):
            return True
    return False


def _cell_date(v: Any) -> Optional[str]:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str) and len(v) >= 10:
        return v[:10]
    return None


def parse_zerodha_tradebook_xlsx(
    data: bytes, *, source_name: str = ""
) -> TradeParseResult:
    try:
        import openpyxl
    except ImportError:
        return TradeParseResult(
            format_id=FORMAT_ID,
            warnings=["openpyxl not installed"],
        )
    try:
        wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
    except Exception as exc:
        return TradeParseResult(
            format_id=FORMAT_ID,
            warnings=[f"xlsx open failed: {exc}"],
        )

    fills: List[TradeFill] = []
    warnings: List[str] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        header_i = None
        col = {}
        for i, row in enumerate(rows[:50]):
            col = _map_headers(row)
            if "symbol" in col and "quantity" in col:
                header_i = i
                break
        if header_i is None:
            continue
        for row in rows[header_i + 1 :]:
            if not row or all(c is None for c in row):
                continue
            try:
                sym_raw = row[col["symbol"]]
                qty_raw = row[col["quantity"]]
            except Exception:
                continue
            sym = normalize_trade_symbol(str(sym_raw or ""))
            if not is_plausible_symbol(sym):
                continue
            try:
                qty = float(qty_raw)
            except Exception:
                continue
            if qty <= 0:
                continue
            side_raw = ""
            if "side" in col and col["side"] < len(row):
                side_raw = str(row[col["side"]] or "").strip().lower()
            side = "sell" if side_raw.startswith("s") else "buy"
            isin = None
            if "isin" in col and col["isin"] < len(row):
                isin = str(row[col["isin"]] or "").strip().upper() or None
            px = None
            if "price" in col and col["price"] < len(row):
                px = parse_money(row[col["price"]])
            amt = None
            if "amount" in col and col["amount"] < len(row):
                amt = parse_money(row[col["amount"]])
            td = None
            if "trade_date" in col and col["trade_date"] < len(row):
                td = _cell_date(row[col["trade_date"]])
            ex = None
            if "exchange" in col and col["exchange"] < len(row):
                ex = str(row[col["exchange"]] or "").strip().upper() or None
            fills.append(
                TradeFill(
                    symbol=sym,
                    isin=isin,
                    quantity=qty,
                    side=side,
                    price=px,
                    amount=amt,
                    trade_date=td,
                    source_file=source_name,
                    format_id=FORMAT_ID,
                    exchange=ex,
                    raw="xlsx",
                ).ensure_amount()
            )
    if not fills:
        warnings.append("zerodha_tradebook_xlsx: no rows parsed")
    return TradeParseResult(
        format_id=FORMAT_ID,
        fills=fills,
        warnings=warnings,
        meta={"row_count": len(fills)},
    )
