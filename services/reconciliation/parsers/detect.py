"""Detect format and dispatch parsers."""
from __future__ import annotations

from pathlib import Path
from typing import Union

from services.reconciliation.as_of_guess import merge_as_of
from services.reconciliation.parsers import (
    angel_dp_pdf,
    broker_shortcode_xlsx,
    cdsl_demat_xlsx,
    groww_xlsx,
    zerodha_csv,
)
from services.reconciliation.parsers.base import ParseResult, iter_xlsx_rows


def detect_and_parse(path: Union[str, Path], filename: str = "") -> ParseResult:
    path = Path(path)
    fname = filename or path.name
    ext = path.suffix.lower()

    if ext == ".csv":
        head = path.read_text(encoding="utf-8-sig", errors="replace")[:2000]
        if zerodha_csv.can_parse(head):
            result = zerodha_csv.parse(path)
        else:
            raise ValueError("Unrecognized CSV holdings format (expected Zerodha Kite)")
    elif ext in (".xlsx", ".xls"):
        rows = iter_xlsx_rows(path, max_rows=25)
        if cdsl_demat_xlsx.can_parse_rows(rows):
            result = cdsl_demat_xlsx.parse(path)
        elif broker_shortcode_xlsx.can_parse_rows(rows):
            result = broker_shortcode_xlsx.parse(path)
        elif groww_xlsx.can_parse_rows(rows):
            result = groww_xlsx.parse(path)
        else:
            # try full parse groww/cdsl as fallback order
            try:
                result = groww_xlsx.parse(path)
            except Exception:
                try:
                    result = broker_shortcode_xlsx.parse(path)
                except Exception as exc:
                    raise ValueError("Unrecognized Excel holdings format") from exc
    elif ext == ".pdf":
        # peek text
        from PyPDF2 import PdfReader

        reader = PdfReader(str(path))
        head = ""
        for page in reader.pages[:2]:
            head += page.extract_text() or ""
        if angel_dp_pdf.can_parse_text(head):
            result = angel_dp_pdf.parse(path)
        else:
            raise ValueError(
                "Unrecognized PDF (Phase 1 supports Angel One DP Holding Cum statements)"
            )
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    as_of, src, conf = merge_as_of(
        result.as_of,
        result.as_of_source,
        result.as_of_confidence,
        fname,
        path,
    )
    result.as_of = as_of
    result.as_of_source = src
    result.as_of_confidence = conf
    return result
