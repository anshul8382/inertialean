"""
Push outstanding price-accuracy findings to a Google Sheet tab for Apps Script workflows.
Uses the same service account / spreadsheet id as cron price updates.

Env:
  GOOGLE_SHEETS_PRICE_ACCURACY_WORKSHEET — worksheet name (default: PriceAccuracyQueue)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import gspread
from gspread.utils import rowcol_to_a1
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger(__name__)

_SPREADSHEET_ID = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
_DEFAULT_WS = "PriceAccuracyQueue"


def _app_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _sa_path() -> str:
    return os.path.join(_app_root(), "service_account.json")


def _worksheet_name() -> str:
    return (os.environ.get("GOOGLE_SHEETS_PRICE_ACCURACY_WORKSHEET") or _DEFAULT_WS).strip() or _DEFAULT_WS


def _client():
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(_sa_path(), scopes)
    return gspread.authorize(creds)


_HEADERS = [
    "security_id",
    "symbol",
    "finding_id",
    "kind",
    "date_prev",
    "date_next",
    "close_prev",
    "close_next",
    "pct_change",
    "missing_date",
    "new_close_price",
    "verified_ok_Y_or_blank",
]


def push_queue_from_findings(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Rewrite tab body (preserves assumed row 1 header). Rows are actionable queue for Sheets scripting.
    """
    gc = _client()
    sh = gc.open_by_key(_SPREADSHEET_ID)
    try:
        ws = sh.worksheet(_worksheet_name())
    except Exception:
        ws = sh.add_worksheet(title=_worksheet_name(), rows=500, cols=len(_HEADERS))

    values: List[List[str]] = [_HEADERS]
    for f in findings:
        values.append(
            [
                str(f.get("security_id", "")),
                str(f.get("symbol", "")),
                str(f.get("id", "")),
                str(f.get("kind", "")),
                str(f.get("date_prev") or ""),
                str(f.get("date_next") or ""),
                "" if f.get("close_prev") is None else str(f.get("close_prev")),
                "" if f.get("close_next") is None else str(f.get("close_next")),
                "" if f.get("pct_change") is None else str(f.get("pct_change")),
                str(f.get("missing_date") or ""),
                "",
                "",
            ]
        )

    ws.clear()
    rng = f"A1:{rowcol_to_a1(len(values), len(_HEADERS))}"
    ws.update(values, range_name=rng, value_input_option="USER_ENTERED")

    sheet_url = f"https://docs.google.com/spreadsheets/d/{_SPREADSHEET_ID}/edit#gid={ws.id}"
    return {"worksheet": _worksheet_name(), "rows": len(values) - 1, "url": sheet_url}
