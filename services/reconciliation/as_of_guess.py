"""Guess statement as-of from filename / mtime (headers handled in parsers)."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple

from services.reconciliation.rules_loader import load_rules

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def guess_from_filename(filename: str) -> Tuple[Optional[date], str]:
    name = filename or ""
    # DD-MM-YYYY or DD/MM/YYYY
    m = re.search(r"(?<!\d)(\d{2})[-/.](\d{2})[-/.](\d{4})(?!\d)", name)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d), "filename_day"
        except ValueError:
            pass
    # YYYY-MM-DD
    m = re.search(r"(?<!\d)(\d{4})[-/.](\d{2})[-/.](\d{2})(?!\d)", name)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d), "filename_day"
        except ValueError:
            pass
    # Month YYYY
    m = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\s+(\d{4})\b",
        name,
        re.I,
    )
    if m:
        mo = _MONTHS[m.group(1).lower()]
        y = int(m.group(2))
        use = (load_rules().get("as_of_guess") or {}).get("month_only_use") or "month_end"
        if use == "month_start":
            return date(y, mo, 1), "filename_month"
        return _month_end(y, mo), "filename_month"
    return None, "none"


def guess_from_mtime(path: Path) -> Tuple[Optional[date], str]:
    allow = (load_rules().get("as_of_guess") or {}).get("allow_file_mtime", True)
    if not allow or not path.is_file():
        return None, "none"
    try:
        ts = path.stat().st_mtime
        return datetime.utcfromtimestamp(ts).date(), "mtime"
    except OSError:
        return None, "none"


def merge_as_of(
    parser_as_of: Optional[date],
    parser_source: Optional[str],
    parser_confidence: str,
    filename: str,
    path: Optional[Path] = None,
) -> Tuple[Optional[date], str, str]:
    """Prefer parser header, then filename, then mtime."""
    if parser_as_of:
        return parser_as_of, parser_source or "header", parser_confidence or "high"
    d, src = guess_from_filename(filename)
    if d:
        conf = "medium" if src == "filename_day" else "low"
        return d, src, conf
    if path is not None:
        d, src = guess_from_mtime(path)
        if d:
            return d, src, "low"
    return None, "none", "none"
