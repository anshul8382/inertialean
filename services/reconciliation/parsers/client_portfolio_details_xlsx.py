"""Client Portfolio Details xlsx (Symbol + Comp. Name + ISIN + Holdings).

Seen as Angel One / similar broker portfolio exports
(e.g. ``VS HS sep 21 2026.xlsx``).
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
    normalize_symbol,
    parse_qty,
)


def can_parse_rows(rows) -> bool:
    blob = " ".join(" ".join(str(c) for c in r) for r in rows[:8]).lower()
    has_table = "symbol" in blob and "isin" in blob and "holdings" in blob
    if not has_table:
        return False
    return (
        "client portfolio details" in blob
        or "client code" in blob
        or "comp. name" in blob
        or "dpaccount holdings" in blob
        or "pool holdings" in blob
    )


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    rows = iter_xlsx_rows(path)
    if not rows:
        raise ValueError("Empty workbook")
    hdr_i, mapping = find_header_row(rows, ["symbol", "isin", "holdings"])

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    c_sym = col("symbol")
    c_name = col("comp. name", "comp name", "company name")
    c_isin = col("isin")
    c_qty = col("holdings", "dpaccount holdings", "quantity")
    if c_qty is None:
        raise ValueError("Client portfolio table needs Holdings quantity")

    client_code = None
    client_name = None
    for row in rows[: hdr_i + 1]:
        joined = " ".join(str(c) for c in row if c)
        low = joined.lower()
        if "client code" in low and ":" in joined:
            client_code = joined.split(":", 1)[-1].strip() or None
        if "client name" in low and ":" in joined:
            client_name = joined.split(":", 1)[-1].strip() or None

    lines = []
    for row in rows[hdr_i + 1 :]:
        qty = parse_qty(cell(row, c_qty))
        if qty is None or qty == 0:
            continue
        sym = normalize_symbol(cell(row, c_sym)) if c_sym is not None else None
        isin = normalize_isin(cell(row, c_isin)) if c_isin is not None else None
        name = cell(row, c_name) if c_name is not None else (sym or isin or "")
        if not sym and not isin:
            continue
        lines.append(
            StagingLine(
                nse_symbol=sym,
                isin=isin,
                qty=qty,
                raw_name=name or sym,
                raw_row={"symbol": sym, "isin": isin, "qty": qty, "name": name},
            )
        )
    if not lines:
        raise ValueError("No client portfolio holdings lines found")
    return ParseResult(
        format_id="client_portfolio_details_xlsx",
        lines=lines,
        as_of=None,
        as_of_source=None,
        as_of_confidence="none",
        meta={
            "row_count": len(lines),
            "client_code": client_code,
            "client_name": client_name,
        },
        warnings=[
            "As-of date not in Client Portfolio Details sheet — confirm from filename."
        ],
    )
