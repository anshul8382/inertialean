"""Angel One DP Holding Cum Transaction PDF — closing balance per ISIN."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import List, Union

from services.reconciliation.parsers.base import (
    ParseResult,
    StagingLine,
    normalize_isin,
    parse_qty,
)


def can_parse_text(text: str) -> bool:
    low = (text or "").lower()
    return (
        "statement of dp transaction cum holding" in low
        or ("closing balance" in low and "angelone" in low)
        or ("demat id" in low and "closing balance" in low and "isin" in low)
    )


def _extract_text(path: Path) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(str(path))
    parts: List[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def _as_of(text: str):
    # Period : Apr  1 2025 TO Oct 14 2026
    m = re.search(
        r"Period\s*:\s*.*?TO\s+(\w+\s+\d{1,2}\s+\d{4})",
        text,
        re.I | re.S,
    )
    if m:
        raw = re.sub(r"\s+", " ", m.group(1).strip())
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    m = re.search(r"Date\s*:\s*(\w+\s+\d{1,2}\s+\d{4})", text, re.I)
    if m:
        raw = re.sub(r"\s+", " ", m.group(1).strip())
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


def parse(path: Union[str, Path]) -> ParseResult:
    path = Path(path)
    text = _extract_text(path)
    if not text.strip():
        raise ValueError("No text extracted from PDF (scanned PDFs not supported)")
    as_of = _as_of(text)

    # Blocks: ISIN then name then ... CLOSING BALANCE ... qty
    # Example:
    # INE009A01021
    # INFOSYS LTD-EQ 5/-
    # ...
    # CLOSING BALANCE
    # 0
    # 0
    # 10
    # 10889.00
    lines = []
    # Split by ISIN headers
    parts = re.split(r"(?=(INE[A-Z0-9]{9}))", text)
    i = 0
    while i < len(parts):
        chunk = parts[i]
        if re.fullmatch(r"INE[A-Z0-9]{9}", chunk.strip()):
            isin = normalize_isin(chunk.strip())
            body = parts[i + 1] if i + 1 < len(parts) else ""
            i += 2
            name_m = re.search(r"([A-Z0-9][A-Z0-9 &./()-]{2,80})", body)
            name = name_m.group(1).strip() if name_m else isin
            # CLOSING BALANCE followed by numbers; balance qty is 3rd number typically
            cm = re.search(
                r"CLOSING\s+BALANCE\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)",
                body,
                re.I,
            )
            qty = None
            if cm:
                qty = parse_qty(cm.group(3))
            else:
                cm2 = re.search(
                    r"CLOSING\s+BALANCE[^\d]*([\d.,]+)[^\d]+([\d.,]+)[^\d]+([\d.,]+)",
                    body,
                    re.I,
                )
                if cm2:
                    qty = parse_qty(cm2.group(3))
            if isin and qty is not None and qty > 0:
                lines.append(
                    StagingLine(
                        isin=isin,
                        qty=qty,
                        raw_name=name,
                        raw_row={"isin": isin, "name": name, "qty": qty},
                    )
                )
        else:
            i += 1

    if not lines:
        raise ValueError("No CLOSING BALANCE lines found in Angel DP PDF")

    return ParseResult(
        format_id="angel_dp_pdf",
        lines=lines,
        as_of=as_of,
        as_of_source="header" if as_of else None,
        as_of_confidence="high" if as_of else "none",
        meta={"row_count": len(lines)},
    )
