"""Broker short-code holdings xlsx (Stock Symbol + ISIN Code + Qty)."""
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
    blob = " ".join(" ".join(str(c) for c in r[:8]) for r in rows[:5]).lower()
    return "stock symbol" in blob and "isin code" in blob


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    rows = iter_xlsx_rows(path)
    hdr_i, mapping = find_header_row(rows, ["stock symbol", "isin code", "qty"])

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    c_code = col("stock symbol")
    c_isin = col("isin code", "isin")
    c_name = col("company name")
    c_qty = col("qty", "quantity")
    if c_qty is None:
        raise ValueError("Qty column not found")

    lines = []
    for row in rows[hdr_i + 1 :]:
        qty = parse_qty(cell(row, c_qty))
        if qty is None or qty == 0:
            continue
        code = cell(row, c_code) if c_code is not None else ""
        isin = normalize_isin(cell(row, c_isin)) if c_isin is not None else None
        name = cell(row, c_name) if c_name is not None else code
        if not code and not isin:
            continue
        lines.append(
            StagingLine(
                broker_code=(code or None),
                isin=isin,
                qty=qty,
                raw_name=name or code,
                raw_row={"broker_code": code, "isin": isin, "qty": qty},
            )
        )
    return ParseResult(
        format_id="broker_shortcode_xlsx",
        lines=lines,
        as_of=None,
        as_of_source=None,
        as_of_confidence="none",
        meta={"row_count": len(lines)},
        warnings=[
            "Broker short codes need confirm-once mapping (ISIN used when present)."
        ],
    )
