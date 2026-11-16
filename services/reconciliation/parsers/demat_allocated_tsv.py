"""Demat holdings: Stock Name | Stock | ISIN | Allocated Quantity | …

Supports:
- TSV/CSV often misnamed ``.xls`` (e.g. ``7500013278_Demat.xls``)
- Real OOXML ``.xlsx`` with the same columns (e.g. ``8509253663_Demat.xlsx``)
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import List, Sequence, Union

from services.reconciliation.parsers.base import (
    ParseResult,
    StagingLine,
    find_header_row,
    iter_xlsx_rows,
    norm_header,
    normalize_isin,
    parse_qty,
)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] == b"PK":
        raise ValueError("File is OOXML xlsx, not demat TSV")
    if raw[:8] == b"\xd0\xcf\x11\xe0":
        raise ValueError("File is legacy OLE .xls, not demat TSV")
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def looks_like_demat_allocated_text(text: str) -> bool:
    head = "\n".join(text.splitlines()[:3]).lower()
    return "allocated quantity" in head and "isin" in head and "stock" in head


def can_parse_rows(rows) -> bool:
    """True for OOXML demat allocated sheets (header often on row 0)."""
    blob = " ".join(" ".join(str(c) for c in r) for r in rows[:8]).lower()
    return "allocated quantity" in blob and "isin" in blob and (
        "stock name" in blob or ("stock" in blob and "market value" in blob)
    )


def can_parse_path(path: Path) -> bool:
    try:
        raw = path.read_bytes()[:400]
        # Real xlsx — handled via can_parse_rows in detect.py
        if raw[:2] == b"PK" or raw[:8] == b"\xd0\xcf\x11\xe0":
            return False
        text = raw.decode("utf-8", errors="replace")
        return looks_like_demat_allocated_text(text)
    except Exception:
        return False


def _parse_rows(rows: Sequence[Sequence[str]], *, format_id: str = "demat_allocated_tsv") -> ParseResult:
    if not rows:
        raise ValueError("Empty demat allocated file")
    # Header may not be row 0 on some exports; prefer exact allocated-quantity header
    try:
        hdr_i, mapping = find_header_row(rows, ["isin", "allocated quantity"])
    except ValueError:
        header = [norm_header(c) for c in rows[0]]
        mapping = {h: i for i, h in enumerate(header) if h}
        hdr_i = 0
        if "isin" not in mapping or (
            "allocated quantity" not in mapping
            and "quantity" not in mapping
            and "qty" not in mapping
        ):
            raise ValueError("Demat allocated needs ISIN and Allocated Quantity")

    def col(*names):
        for n in names:
            if norm_header(n) in mapping:
                return mapping[norm_header(n)]
        return None

    c_name = col("stock name")
    c_code = col("stock")
    c_isin = col("isin")
    c_qty = col("allocated quantity", "quantity", "qty")
    if c_isin is None or c_qty is None:
        raise ValueError("Demat allocated needs ISIN and Allocated Quantity")

    lines = []
    for row in rows[hdr_i + 1 :]:
        if not row or not any(str(c).strip() for c in row):
            continue

        def cell(idx):
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx] or "").strip()

        qty = parse_qty(cell(c_qty))
        if qty is None or qty == 0:
            continue
        isin = normalize_isin(cell(c_isin))
        code = cell(c_code) if c_code is not None else ""
        name = cell(c_name) if c_name is not None else (code or isin)
        if not isin:
            continue
        lines.append(
            StagingLine(
                isin=isin,
                broker_code=(code or None),
                qty=qty,
                raw_name=name or isin,
                raw_row={
                    "isin": isin,
                    "broker_code": code,
                    "qty": qty,
                    "name": name,
                },
            )
        )
    if not lines:
        raise ValueError("No demat allocated holdings lines found")
    return ParseResult(
        format_id=format_id,
        lines=lines,
        as_of=None,
        as_of_source=None,
        as_of_confidence="none",
        meta={"row_count": len(lines)},
        warnings=[
            "Stock column is a truncated broker code — matching uses ISIN. "
            "As-of not in file — confirm from filename or statement date."
        ],
    )


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    raw = path.read_bytes()[:8]
    # Real Excel OOXML
    if raw[:2] == b"PK":
        rows = iter_xlsx_rows(path)
        if not can_parse_rows(rows[:8] if rows else []):
            raise ValueError("Not a demat allocated-quantity xlsx")
        return _parse_rows(rows, format_id="demat_allocated_xlsx")

    text = _read_text(path)
    if not looks_like_demat_allocated_text(text):
        raise ValueError("Not a demat allocated-quantity TSV")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,;")
    except csv.Error:
        dialect = csv.excel_tab
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [list(r) for r in reader]
    return _parse_rows(rows, format_id="demat_allocated_tsv")
