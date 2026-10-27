"""Demat holdings as TSV/CSV often misnamed ``.xls``.

Header: Stock Name | Stock | ISIN | Allocated Quantity | …
(e.g. ``7500013278_Demat.xls`` — ICICI Direct-style short Stock codes).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import List, Sequence, Union

from services.reconciliation.parsers.base import (
    ParseResult,
    StagingLine,
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


def can_parse_path(path: Path) -> bool:
    try:
        # Small peek — these exports are tiny text files
        raw = path.read_bytes()[:400]
        if raw[:2] == b"PK" or raw[:8] == b"\xd0\xcf\x11\xe0":
            return False
        text = raw.decode("utf-8", errors="replace")
        return looks_like_demat_allocated_text(text)
    except Exception:
        return False


def _parse_rows(rows: Sequence[Sequence[str]]) -> ParseResult:
    if not rows:
        raise ValueError("Empty demat allocated file")
    header = [norm_header(c) for c in rows[0]]
    mapping = {h: i for i, h in enumerate(header) if h}

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
        raise ValueError("Demat allocated TSV needs ISIN and Allocated Quantity")

    lines = []
    for row in rows[1:]:
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
        format_id="demat_allocated_tsv",
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
    text = _read_text(path)
    if not looks_like_demat_allocated_text(text):
        raise ValueError("Not a demat allocated-quantity TSV")
    # Prefer tab; fall back to csv sniff
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,;")
    except csv.Error:
        dialect = csv.excel_tab
    reader = csv.reader(io.StringIO(text), dialect)
    rows: List[List[str]] = [list(r) for r in reader]
    return _parse_rows(rows)
