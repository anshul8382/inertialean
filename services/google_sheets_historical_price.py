"""
Google Sheets–backed historical NSE closes (GOOGLEFINANCE via live formulas).

Spreadsheet (current prices + historical formula workflow):
  https://docs.google.com/spreadsheets/d/1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM/

Worksheets:
  - Default tab for API writes/reads: ``HistoricalPrices`` (column A = ``NSE:SYMBOL``, Date, QUERY formula → close).
  - ``HistoricalPriceTest`` and others are used manually to validate formulas; this module
    targets the configured worksheet name (default ``HistoricalPrices``).

Credentials:
  ``<repo>/service_account.json`` (same convention as cron_jobs / scripts).

Environment:
  ``GOOGLE_SHEETS_HISTORICAL_WORKSHEET`` — optional worksheet name override (default ``HistoricalPrices``).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import gspread
from oauth2client.service_account import ServiceAccountCredentials

logger = logging.getLogger(__name__)

_SPREADSHEET_ID = "1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM"
_DEFAULT_WORKSHEET = "HistoricalPrices"
_SCOPES = (
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _service_account_path() -> Path:
    return _repo_root() / "service_account.json"


def _worksheet_name() -> str:
    return os.environ.get("GOOGLE_SHEETS_HISTORICAL_WORKSHEET", _DEFAULT_WORKSHEET).strip() or _DEFAULT_WORKSHEET


def _sanitize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper().replace(".NS", "").replace(".BO", "")
    if s.startswith("NSE:"):
        s = s[4:].strip()
    return "".join(c for c in s if c.isalnum() or c in ("&", "-", "."))[:20] or "INVALID"


def _nse_ticker_cell(plain_symbol: str) -> str:
    """Plain NSE equity code (after sanitise) as Google Finance expects in the sheet: ``NSE:CODE``."""
    if not plain_symbol or plain_symbol == "INVALID":
        return plain_symbol
    return f"NSE:{plain_symbol}"


def _google_client():
    path = _service_account_path()
    if not path.is_file():
        raise FileNotFoundError(f"Google service account file not found: {path}")
    creds = ServiceAccountCredentials.from_json_keyfile_name(str(path), list(_SCOPES))
    return gspread.authorize(creds)


class GoogleSheetsHistoricalPrice:
    """
    Historical (and as-of-date) closes via NSE: tickers in Google Sheets GOOGLEFINANCE.

    Same public API as the legacy ``historical_price_google_sheets`` module.
    """

    SPREADSHEET_KEY = _SPREADSHEET_ID
    SHEET_NAME = _DEFAULT_WORKSHEET

    @staticmethod
    def spreadsheet_url() -> str:
        return f"https://docs.google.com/spreadsheets/d/{_SPREADSHEET_ID}/"

    @staticmethod
    def get_historical_price(
        symbol: str,
        target_date: Union[date, str],
        try_nearby_days: bool = True,
        max_days_back: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """
        Write a QUERY(GOOGLEFINANCE(...)) formula to the sheet, wait, read the calculated close.

        Returns dict with ``price``, ``source``, ``date`` (resolved row date), etc., or None.
        """
        symbol = _sanitize_symbol(symbol)
        if symbol == "INVALID":
            return None
        try:
            gs_client = _google_client()
            spreadsheet = gs_client.open_by_key(_SPREADSHEET_ID)
            worksheet = spreadsheet.worksheet(_worksheet_name())

            if isinstance(target_date, str):
                target_date_obj = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
            else:
                target_date_obj = target_date

            days_to_try = [0]
            if try_nearby_days:
                days_to_try.extend(range(1, max_days_back + 1))

            for days_back in days_to_try:
                try_date = target_date_obj - timedelta(days=days_back)
                date_str = try_date.strftime("%Y-%m-%d")
                year, month, day = try_date.year, try_date.month, try_date.day

                all_values = worksheet.get_all_values()
                next_row = len(all_values) + 1

                nse_label = _nse_ticker_cell(symbol)
                formula = (
                    f'=Query(GOOGLEFINANCE(A{next_row},"Close",DATE({year},{month},{day}),1),'
                    f'"Select Col2 limit 1 offset 1",0)'
                )

                worksheet.update(f"A{next_row}", [[nse_label]], raw=False)
                worksheet.update(f"B{next_row}", [[date_str]], raw=False)
                worksheet.update(f"C{next_row}", [[formula]], raw=False)

                logger.info("Google Sheets historical price: row %s %s %s", next_row, nse_label, date_str)
                time.sleep(4)

                result = worksheet.acell(f"C{next_row}", value_render_option="UNFORMATTED_VALUE").value
                price_value: Optional[float] = None
                if result is not None and isinstance(result, (int, float)):
                    price_value = float(result)
                elif result:
                    try:
                        price_value = float(result)
                    except (ValueError, TypeError):
                        if days_back < max_days_back and try_nearby_days:
                            continue
                        logger.warning("Google Sheets non-numeric result for %s %s: %s", symbol, date_str, result)
                        return None

                if price_value and 0.01 <= price_value <= 1e8:
                    accuracy = "exact" if days_back == 0 else "nearby"
                    note = "Real historical price from GOOGLEFINANCE (via Google Sheets)"
                    if days_back > 0:
                        note += f" — used {days_back} calendar day(s) before requested date"
                    return {
                        "price": price_value,
                        "source": "google_sheets_googlefinance",
                        "date": try_date,
                        "requested_date": target_date_obj,
                        "days_offset": days_back,
                        "accuracy": accuracy,
                        "confidence": 0.98 if days_back == 0 else max(0.5, 0.95 - days_back * 0.01),
                        "note": note,
                    }
                if days_back < max_days_back and try_nearby_days:
                    continue
                return None

            return None
        except gspread.WorksheetNotFound:
            logger.warning("Worksheet %r not found in spreadsheet %s", _worksheet_name(), _SPREADSHEET_ID)
            return None
        except FileNotFoundError as e:
            logger.error("%s", e)
            return None
        except Exception as e:
            logger.error("Google Sheets get_historical_price: %s", e, exc_info=True)
            return None

    @staticmethod
    def get_historical_price_batch(
        symbol_date_pairs: List[Tuple[str, Union[date, str]]],
        wait_seconds: float = 4,
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Write many formulas in one range, single wait, read column C."""
        if not symbol_date_pairs:
            return {}
        try:
            gs_client = _google_client()
            spreadsheet = gs_client.open_by_key(_SPREADSHEET_ID)
            worksheet = spreadsheet.worksheet(_worksheet_name())

            all_values = worksheet.get_all_values()
            next_row = len(all_values) + 1

            batch_data: List[List[str]] = []
            row_mapping: List[Tuple[str, str]] = []

            for sym_raw, target_date in symbol_date_pairs:
                sym = _sanitize_symbol(sym_raw)
                if sym == "INVALID":
                    continue
                if isinstance(target_date, str):
                    date_str = target_date[:10]
                else:
                    date_str = target_date.strftime("%Y-%m-%d")
                y, m, d = int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10])
                row_num = next_row + len(batch_data)
                formula = (
                    f'=Query(GOOGLEFINANCE(A{row_num},"Close",DATE({y},{m},{d}),1),'
                    f'"Select Col2 limit 1 offset 1",0)'
                )
                batch_data.append([_nse_ticker_cell(sym), date_str, formula])
                row_mapping.append((sym, date_str))

            if not batch_data:
                return {}

            last_needed = next_row + len(batch_data) - 1
            row_cap = getattr(worksheet, "row_count", 1000) or 1000
            if row_cap < last_needed:
                # Append-on-demand: large backfills exceeded fixed grid rows (historical quota ~11k rows).
                new_rows = max(last_needed + 5000, int(row_cap * 1.2))
                worksheet.resize(rows=new_rows, cols=max(getattr(worksheet, "col_count", 3) or 3, 3))
                logger.info(
                    "Resized worksheet %r to %s rows (needed append through row %s)",
                    _worksheet_name(),
                    new_rows,
                    last_needed,
                )

            range_to_update = f"A{next_row}:C{next_row + len(batch_data) - 1}"
            worksheet.update(range_to_update, batch_data, raw=False)
            logger.info(
                "Google Sheets batch: wrote %s rows starting at %s",
                len(batch_data),
                next_row,
            )
            time.sleep(wait_seconds)

            results_range = f"C{next_row}:C{next_row + len(batch_data) - 1}"
            results = worksheet.get(results_range, value_render_option="UNFORMATTED_VALUE")

            price_data: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for i in range(min(len(results), len(row_mapping))):
                row = results[i]
                sym, date_str = row_mapping[i]
                result_value = row[0] if row and len(row) > 0 else None
                price_value = None
                if result_value is not None:
                    if isinstance(result_value, (int, float)):
                        price_value = float(result_value)
                    else:
                        try:
                            price_value = float(result_value)
                        except (ValueError, TypeError):
                            pass
                if price_value is not None and 0.01 <= price_value <= 1e8:
                    try:
                        asked = datetime.strptime(date_str, "%Y-%m-%d").date()
                        excel_serial = float((asked - date(1899, 12, 30)).days)
                        if abs(price_value - excel_serial) < 0.6:
                            continue
                    except Exception:
                        pass
                    price_data[(sym, date_str)] = {
                        "price": price_value,
                        "source": "google_sheets_googlefinance",
                        "date": datetime.strptime(date_str, "%Y-%m-%d").date(),
                        "accuracy": "exact",
                        "confidence": 0.98,
                    }
            return price_data
        except FileNotFoundError as e:
            logger.error("%s", e)
            return {}
        except Exception as e:
            logger.error("Google Sheets batch: %s", e, exc_info=True)
            return {}

    @staticmethod
    def read_all_prices_from_sheet() -> List[Dict[str, Any]]:
        """Read existing A:B/C rows (numeric C only)."""
        try:
            gs_client = _google_client()
            spreadsheet = gs_client.open_by_key(_SPREADSHEET_ID)
            worksheet = spreadsheet.worksheet(_worksheet_name())

            all_values = worksheet.get_all_values()
            if len(all_values) < 2:
                return []

            data_rows = len(all_values) - 1
            prices_range = f"C2:C{1 + data_rows}"
            prices_raw = worksheet.get(prices_range, value_render_option="UNFORMATTED_VALUE")

            result: List[Dict[str, Any]] = []
            for i, row in enumerate(all_values[1:], start=2):
                if len(row) < 2:
                    continue
                sym_cell = (row[0] or "").strip()
                plain = _sanitize_symbol(sym_cell)
                date_str = (row[1] or "").strip()
                if not plain or plain == "INVALID" or not date_str:
                    continue
                price_value = None
                if i - 2 < len(prices_raw) and prices_raw[i - 2] and len(prices_raw[i - 2]) > 0:
                    val = prices_raw[i - 2][0]
                    if isinstance(val, (int, float)):
                        price_value = float(val)
                    elif val:
                        try:
                            price_value = float(val)
                        except (ValueError, TypeError):
                            pass
                if price_value is not None and 0.01 <= price_value <= 1e8:
                    result.append({"symbol": plain, "date_str": date_str, "price": price_value})
            return result
        except Exception as e:
            logger.error("read_all_prices_from_sheet: %s", e, exc_info=True)
            return []
