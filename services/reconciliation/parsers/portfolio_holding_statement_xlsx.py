"""PORTFOLIO HOLDING STATEMENT xlsx (Name + ISIN + Qty).

Broker report with Customer Information / Date of Download header
(e.g. ``Oct 2026 holding .xlsx``).
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

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
    blob = " ".join(" ".join(str(c) for c in r) for r in rows[:20]).lower()
    if "portfolio holding statement" in blob:
        return True
    return "date of download" in blob and "isin" in blob and (
        " qty" in f" {blob} " or "quantity" in blob
    )


def _as_of(rows) -> Optional[object]:
    for row in rows[:20]:
        if len(row) < 2:
            continue
        label = norm_header(row[0])
        raw = str(row[1] or "").strip()
        if not raw:
            continue
        if label in ("date of download", "report date", "as on date", "as of date"):
            # "11/10/2026, 08:28:15" or "11/10/2026"
            m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", raw)
            if m:
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                # Prefer DMY (Indian statements)
                try:
                    return datetime(y, mo, d).date()
                except ValueError:
                    try:
                        return datetime(y, d, mo).date()
                    except ValueError:
                        pass
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y"):
                try:
                    return datetime.strptime(raw.split(",")[0].strip(), fmt).date()
                except ValueError:
                    continue
    return None


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    rows = iter_xlsx_rows(path)
    if not rows:
        raise ValueError("Empty workbook")
    as_of = _as_of(rows)
    hdr_i, mapping = find_header_row(rows, ["isin", "qty", "name"])

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    # Prefer exact Qty over Short Term Qty etc.
    c_qty = mapping.get("qty")
    if c_qty is None:
        c_qty = col("quantity", "qty.")
    c_isin = col("isin")
    c_name = col("name", "stock name", "company name")
    if c_isin is None or c_qty is None:
        raise ValueError("Portfolio holding statement needs ISIN and Qty")

    customer = None
    for row in rows[: hdr_i + 1]:
        if norm_header(row[0] if row else "") == "customer name" and len(row) > 1:
            customer = str(row[1] or "").strip() or None

    lines = []
    for row in rows[hdr_i + 1 :]:
        qty = parse_qty(cell(row, c_qty))
        if qty is None or qty == 0:
            continue
        isin = normalize_isin(cell(row, c_isin))
        name = cell(row, c_name) if c_name is not None else isin
        if not isin:
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
        raise ValueError("No portfolio holding statement lines found")
    return ParseResult(
        format_id="portfolio_holding_statement_xlsx",
        lines=lines,
        as_of=as_of,
        as_of_source="header" if as_of else None,
        as_of_confidence="high" if as_of else "none",
        meta={"row_count": len(lines), "customer_name": customer},
    )
