"""Zerodha / Kite holdings CSV."""
from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from typing import Union

from services.reconciliation.parsers.base import (
    ParseResult,
    StagingLine,
    normalize_symbol,
    parse_qty,
)


def can_parse(text_head: str) -> bool:
    low = text_head.lower()
    return "instrument" in low and "qty" in low


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(StringIO(raw))
    if not reader.fieldnames:
        raise ValueError("Empty CSV")
    fields = { (f or "").strip(): f for f in reader.fieldnames }
    # normalize lookup
    lower_map = {k.lower(): v for k, v in fields.items()}
    inst_key = lower_map.get("instrument")
    qty_key = None
    for cand in ("qty.", "qty", "quantity"):
        if cand in lower_map:
            qty_key = lower_map[cand]
            break
    if not inst_key or not qty_key:
        raise ValueError("Zerodha CSV needs Instrument and Qty. columns")

    lines = []
    for row in reader:
        sym = normalize_symbol(row.get(inst_key) or "")
        qty = parse_qty(row.get(qty_key) or "")
        if not sym or qty is None:
            continue
        if qty == 0:
            continue
        lines.append(
            StagingLine(
                nse_symbol=sym,
                qty=qty,
                raw_name=sym,
                raw_row={"instrument": sym, "qty": qty},
            )
        )
    return ParseResult(
        format_id="zerodha_kite_csv",
        lines=lines,
        as_of=None,
        as_of_source=None,
        as_of_confidence="none",
        meta={"row_count": len(lines)},
        warnings=["As-of date not in Zerodha CSV — confirm from filename or enter manually."],
    )
