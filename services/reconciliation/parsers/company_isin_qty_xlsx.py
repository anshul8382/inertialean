"""Company Name + ISIN Code + Qty portfolio export.

Flat broker / PMS-style holdings sheet (e.g. ``Sumit Jain Portfolio.xlsx``):

``Company Name | ISIN Code | Qty | Average Cost Price | Current Market Price | …``

No NSE symbol column — match by ISIN (and name fallback).
"""
from __future__ import annotations

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
    parse_qty,
)


def can_parse_rows(rows) -> bool:
    blob = " ".join(" ".join(str(c) for c in r) for r in rows[:8]).lower()
    # Distinct from Client Portfolio Details (Symbol + Holdings)
    if "symbol" in blob and "holdings" in blob:
        return False
    # Distinct from PORTFOLIO HOLDING STATEMENT branded report
    if "portfolio holding statement" in blob or "date of download" in blob:
        return False
    # Distinct from broker short-code (Stock Symbol + ISIN Code)
    if "stock symbol" in blob:
        return False
    return (
        "company name" in blob
        and ("isin code" in blob or "isin" in blob)
        and "qty" in blob
    )


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    rows = iter_xlsx_rows(path)
    if not rows:
        raise ValueError("Empty workbook")
    hdr_i, mapping = find_header_row(
        rows, ["isin code", "company name", "qty"]
    )

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    c_isin = col("isin code", "isin")
    c_qty = col("qty", "quantity", "qty.")
    c_name = col("company name", "name", "stock name")
    if c_isin is None or c_qty is None:
        raise ValueError("Company/ISIN holdings table needs ISIN Code and Qty")

    lines = []
    for row in rows[hdr_i + 1 :]:
        qty = parse_qty(cell(row, c_qty))
        if qty is None or qty == 0:
            continue
        isin = normalize_isin(cell(row, c_isin))
        name = cell(row, c_name) if c_name is not None else (isin or "")
        # Skip totals / blank name rows without ISIN
        if not isin:
            continue
        low_name = (name or "").strip().lower()
        if low_name in ("total", "grand total", "subtotal"):
            continue
        lines.append(
            StagingLine(
                isin=isin,
                qty=qty,
                raw_name=name or isin,
                raw_row={"isin": isin, "qty": qty, "name": name},
            )
        )
    if not lines:
        raise ValueError("No Company Name / ISIN Code holdings lines found")
    return ParseResult(
        format_id="company_isin_qty_xlsx",
        lines=lines,
        as_of=None,
        as_of_source=None,
        as_of_confidence="none",
        meta={"row_count": len(lines)},
        warnings=[
            "As-of date not in Company/ISIN sheet — confirm from filename or upload."
        ],
    )
