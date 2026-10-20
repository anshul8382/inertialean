"""Zerodha / Kite holdings Excel export (Instrument + Qty.)."""
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
    normalize_symbol,
    parse_qty,
)


def can_parse_rows(rows) -> bool:
    blob = " ".join(" ".join(str(c) for c in r) for r in rows[:8]).lower()
    if "instrument" not in blob:
        return False
    # Kite uses Qty. / Qty; avoid Axis "Open Qty" / Groww "Quantity available"
    return (
        "qty." in blob
        or (" qty" in f" {blob} " and "open qty" not in blob and "quantity" not in blob)
        or "avg. cost" in blob
        or "avg cost" in blob
    )


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    rows = iter_xlsx_rows(path)
    if not rows:
        raise ValueError("Empty workbook")
    try:
        hdr_i, mapping = find_header_row(rows, ["instrument"])
    except ValueError as exc:
        raise ValueError("Zerodha Kite holdings header not found") from exc

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    c_inst = col("instrument")
    c_qty = col("qty.", "qty", "quantity")
    if c_inst is None or c_qty is None:
        raise ValueError("Zerodha Kite xlsx needs Instrument and Qty. columns")

    lines = []
    for row in rows[hdr_i + 1 :]:
        sym = normalize_symbol(cell(row, c_inst))
        qty = parse_qty(cell(row, c_qty))
        if not sym or qty is None or qty == 0:
            continue
        # Skip duplicate block headers if a second Instrument column exists
        if sym.lower() == "instrument":
            continue
        lines.append(
            StagingLine(
                nse_symbol=sym,
                qty=qty,
                raw_name=sym,
                raw_row={"instrument": sym, "qty": qty},
            )
        )
    if not lines:
        raise ValueError("No Zerodha Kite holdings lines found")
    return ParseResult(
        format_id="zerodha_kite_xlsx",
        lines=lines,
        as_of=None,
        as_of_source=None,
        as_of_confidence="none",
        meta={"row_count": len(lines)},
        warnings=[
            "As-of date not in Zerodha Kite Excel — confirm from filename or enter manually."
        ],
    )
