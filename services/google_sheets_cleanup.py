"""
Trim Google Sheets tabs that accumulate API-written rows (e.g. HistoricalPrices formulas).

Uses the inertia price workbook and service_account.json — same spreadsheet id as cron / historical price.

Environment:
  GOOGLE_SHEETS_CLEANUP_WORKSHEETS — comma-separated tab names Hub will clean when no override is passed.
  Default (see config.py) is HistoricalPrices only.

Never cleans master data tabs named Prices or Nifty (case-insensitive).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import gspread
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger(__name__)

_SPREADSHEET_ID = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
_SCOPES = (
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
)

# Tabs that hold live reference data pulled by cron — do not wipe via this tool.
_FORBIDDEN_TAB_NAMES_LOWER = frozenset({"prices", "nifty"})


def _app_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _sa_path() -> str:
    return os.path.join(_app_root(), "service_account.json")


def _client():
    creds = ServiceAccountCredentials.from_json_keyfile_name(_sa_path(), list(_SCOPES))
    return gspread.authorize(creds)


def cleanup_sheet_tabs_allowlist(tab_name: str) -> bool:
    return (tab_name or "").strip().lower() not in _FORBIDDEN_TAB_NAMES_LOWER


def default_cleanup_worksheet_names() -> List[str]:
    raw = (os.environ.get("GOOGLE_SHEETS_CLEANUP_WORKSHEETS") or "HistoricalPrices").strip()
    names = [x.strip() for x in raw.split(",") if x.strip()]
    return names if names else ["HistoricalPrices"]


def trim_worksheet_keep_header_rows(
    worksheet: Any,
    *,
    header_rows: int = 1,
    chunk: int = 600,
    max_iterations: int = 10000,
) -> int:
    """
    Deletes data rows below the header using chunked row deletes (avoids oversized single deletes).
    Returns number of data rows removed.
    """
    if header_rows < 1:
        raise ValueError("header_rows must be >= 1")
    deleted = 0
    iteration = 0
    while iteration < max_iterations:
        vals = worksheet.get_all_values()
        n = len(vals)
        if n <= header_rows:
            break
        end_del = min(n, header_rows + chunk)
        worksheet.delete_rows(header_rows + 1, end_del)
        deleted += end_del - header_rows
        iteration += 1
    return deleted


def cleanup_workbook_tabs(
    tab_names: List[str],
    *,
    header_rows: int = 1,
    chunk_size: int = 600,
) -> Dict[str, Any]:
    """
    For each worksheet name: open tab, strip all rows after ``header_rows`` (default: keep row 1 only).
    """
    if not tab_names:
        return {"ok": False, "error": "no_tab_names", "tabs": {}, "skipped_forbidden": []}

    path = _sa_path()
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Google service account file not found: {path}")

    gc = _client()
    sh = gc.open_by_key(_SPREADSHEET_ID)
    results: Dict[str, int] = {}
    skipped: List[str] = []
    forbidden: List[str] = []

    for raw in tab_names:
        name = raw.strip()
        if not name:
            continue
        if not cleanup_sheet_tabs_allowlist(name):
            forbidden.append(name)
            continue
        try:
            ws = sh.worksheet(name)
        except Exception as exc:
            logger.warning("cleanup: worksheet %r missing: %s", name, exc)
            skipped.append(name)
            continue
        n = trim_worksheet_keep_header_rows(ws, header_rows=header_rows, chunk=chunk_size)
        results[name] = n
        logger.info("google_sheets_cleanup: removed %s data rows from %r", n, name)

    return {
        "ok": True,
        "tabs": results,
        "skipped_missing": skipped,
        "skipped_forbidden": forbidden,
        "spreadsheet_id": _SPREADSHEET_ID,
    }
