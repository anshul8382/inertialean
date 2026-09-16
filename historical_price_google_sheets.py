"""
Backward-compatible import path for Google Sheets historical prices.

Prefer:
    from services.google_sheets_historical_price import GoogleSheetsHistoricalPrice

Spreadsheet: https://docs.google.com/spreadsheets/d/1Cd-TYldviG1HHMVo8coAjj-zzcu_nAr64slRZsFFmgM/
"""

from services.google_sheets_historical_price import GoogleSheetsHistoricalPrice

__all__ = ["GoogleSheetsHistoricalPrice"]
