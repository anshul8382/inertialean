"""Axis Direct / similar Portfolio Holdings Statement Excel (Stock Name + ISIN + Open Qty)."""
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
    if "stock name" in blob and "open qty" in blob and "isin" in blob:
        return True
    if "portfolio holdings statement" in blob and "open qty" in blob:
        return True
    if "as on date" in blob and "open qty" in blob and "isin" in blob:
        return True
    return False


def _parse_as_of_from_rows(rows) -> Optional[object]:
    # Prefer explicit "As on Date" cell value in next column / same row
    for row in rows[:20]:
        cells = [str(c or "").strip() for c in row]
        for i, s in enumerate(cells):
            if norm_header(s) != "as on date":
                continue
            # value often in next non-empty cell
            for j in range(i + 1, min(i + 3, len(cells))):
                raw = cells[j]
                if not raw:
                    continue
                d = _parse_date_token(raw)
                if d:
                    return d
    # Title: "Portfolio Holdings Statement as on  01 Sep 2026"
    for row in rows[:12]:
        for c in row:
            s = str(c or "")
            m = re.search(
                r"as on\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{4}|\d{2}[-/]\d{2}[-/]\d{4})",
                s,
                re.I,
            )
            if m:
                d = _parse_date_token(m.group(1))
                if d:
                    return d
    return None


def _parse_date_token(raw: str):
    s = (raw or "").strip()
    for fmt in (
        "%d/%b/%Y",
        "%d-%b-%Y",
        "%d %b %Y",
        "%d/%B/%Y",
        "%d %B %Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # "01/Sep/2026" already covered; try replace spaces
    s2 = re.sub(r"\s+", " ", s)
    if s2 != s:
        return _parse_date_token(s2)
    return None


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    rows = iter_xlsx_rows(path)
    if not rows:
        raise ValueError("Empty workbook")
    as_of = _parse_as_of_from_rows(rows)
    try:
        hdr_i, mapping = find_header_row(
            rows, ["stock name", "open qty", "isin"]
        )
    except ValueError as exc:
        raise ValueError("Axis Direct holdings header not found") from exc

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    c_name = col("stock name", "scheme name")
    c_isin = col("isin")
    c_qty = col("open qty", "quantity", "qty")
    if c_qty is None:
        raise ValueError("Open Qty column not found")
    if c_name is None and c_isin is None:
        raise ValueError("Stock Name / ISIN columns not found")

    # Require Open Qty specifically when present so we don't pick wrong sheet
    if "open qty" not in mapping and "stock name" not in mapping:
        raise ValueError("Not an Axis Direct open-holdings sheet")

    lines = []
    for row in rows[hdr_i + 1 :]:
        qty = parse_qty(cell(row, c_qty))
        if qty is None or qty == 0:
            continue
        isin = normalize_isin(cell(row, c_isin)) if c_isin is not None else None
        name = cell(row, c_name) if c_name is not None else ""
        if not isin and not name:
            continue
        if name and norm_header(name) in ("stock name", "scheme name"):
            continue
        lines.append(
            StagingLine(
                nse_symbol=None,
                isin=isin,
                qty=qty,
                raw_name=name or isin,
                raw_row={"stock_name": name, "isin": isin, "qty": qty},
            )
        )
    if not lines:
        raise ValueError("No Axis Direct holdings lines found")
    return ParseResult(
        format_id="axis_direct_holdings_xlsx",
        lines=lines,
        as_of=as_of,
        as_of_source="header" if as_of else None,
        as_of_confidence="high" if as_of else "none",
        meta={"row_count": len(lines), "broker": "axis_direct"},
    )
