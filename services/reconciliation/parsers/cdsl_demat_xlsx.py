"""CDSL / demat holdings xlsx (Script Name + ISIN Number + Total Qty)."""
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
    parse_qty,
)


def can_parse_rows(rows) -> bool:
    blob = " ".join(" ".join(str(c) for c in r) for r in rows[:20]).lower()
    return "demat holdings as on" in blob or (
        "isin number" in blob and "script name" in blob
    )


def _as_of(rows):
    for row in rows[:15]:
        joined = " ".join(str(c) for c in row if c)
        m = re.search(
            r"demat holdings as on\s+(\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4})",
            joined,
            re.I,
        )
        if not m:
            # Holding Date cell nearby
            m = re.search(
                r"holding date\s+(\d{1,2}[-/]\w{3}[-/]\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{4})",
                joined,
                re.I,
            )
        if not m:
            continue
        raw = m.group(1)
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%d/%b/%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    # scan cells for date next to Holding Date label
    for i, row in enumerate(rows[:15]):
        for j, c in enumerate(row):
            if norm_header(c) == "holding date" and j + 1 < len(row):
                raw = str(row[j + 1]).strip()
                for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
                    try:
                        return datetime.strptime(raw, fmt).date()
                    except ValueError:
                        continue
    return None


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    rows = iter_xlsx_rows(path)
    as_of = _as_of(rows)
    hdr_i, mapping = find_header_row(rows, ["isin number", "script name", "total qty"])

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    c_isin = col("isin number", "isin")
    c_name = col("script name")
    c_qty = col("total qty", "free")
    if c_isin is None or c_qty is None:
        raise ValueError("CDSL demat table needs ISIN Number and Total Qty")

    lines = []
    for row in rows[hdr_i + 1 :]:
        isin = normalize_isin(cell(row, c_isin))
        qty = parse_qty(cell(row, c_qty))
        if not isin or qty is None or qty == 0:
            continue
        name = cell(row, c_name) if c_name is not None else isin
        lines.append(
            StagingLine(
                isin=isin,
                qty=qty,
                raw_name=name,
                raw_row={"isin": isin, "name": name, "qty": qty},
            )
        )
    return ParseResult(
        format_id="cdsl_demat_xlsx",
        lines=lines,
        as_of=as_of,
        as_of_source="header" if as_of else None,
        as_of_confidence="high" if as_of else "none",
        meta={"row_count": len(lines)},
    )
