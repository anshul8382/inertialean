"""Groww / equity holdings statement xlsx (Symbol + ISIN)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Union

from services.reconciliation.parsers.base import (
    ParseResult,
    StagingLine,
    cell,
    find_header_row,
    iter_xlsx_rows,
    norm_header,
    normalize_isin,
    normalize_symbol,
    parse_qty,
)


def can_parse_rows(rows) -> bool:
    blob = " ".join(" ".join(str(c) for c in r) for r in rows[:15]).lower()
    return (
        "equity holdings statement as on" in blob
        or "holdings statement for stocks as on" in blob
        or ("unique client code" in blob and "isin" in blob)
        or ("symbol" in blob and "quantity available" in blob)
    )


def _parse_as_of_from_rows(rows):
    for row in rows[:12]:
        for c in row:
            s = str(c or "")
            m = re.search(
                r"(?:as on|as of)\s*(\d{4}-\d{2}-\d{2}|\d{2}[-/]\d{2}[-/]\d{4})",
                s,
                re.I,
            )
            if not m:
                continue
            raw = m.group(1)
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(raw, fmt).date()
                except ValueError:
                    continue
    return None


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    rows = iter_xlsx_rows(path)
    if not rows:
        raise ValueError("Empty workbook")
    as_of = _parse_as_of_from_rows(rows)
    # header variants
    try:
        hdr_i, mapping = find_header_row(
            rows, ["symbol", "stock name", "isin", "quantity available", "quantity"]
        )
    except ValueError as exc:
        raise ValueError("Groww-style holdings header not found") from exc

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    c_sym = col("symbol", "stock name")
    c_isin = col("isin")
    c_qty = col("quantity available", "quantity")
    if c_qty is None:
        raise ValueError("Quantity column not found")

    lines = []
    for row in rows[hdr_i + 1 :]:
        qty = parse_qty(cell(row, c_qty))
        if qty is None or qty == 0:
            continue
        isin = normalize_isin(cell(row, c_isin)) if c_isin is not None else None
        sym_raw = cell(row, c_sym) if c_sym is not None else ""
        # Prefer NSE-like symbol; stock name rows still keep raw_name
        sym = normalize_symbol(sym_raw) if sym_raw and " " not in sym_raw.strip() else None
        if not sym and not isin:
            # name-only with spaces — keep as raw_name
            if not sym_raw:
                continue
        lines.append(
            StagingLine(
                nse_symbol=sym,
                isin=isin,
                qty=qty,
                raw_name=sym_raw or isin,
                raw_row={"symbol": sym_raw, "isin": isin, "qty": qty},
            )
        )
    return ParseResult(
        format_id="groww_holdings_xlsx",
        lines=lines,
        as_of=as_of,
        as_of_source="header" if as_of else None,
        as_of_confidence="high" if as_of else "none",
        meta={"row_count": len(lines)},
    )
