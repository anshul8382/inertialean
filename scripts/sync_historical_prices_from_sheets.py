#!/usr/bin/env python3
"""
Sync historical_price table from existing data on the Google Sheet.

Reads all rows from the HistoricalPrices sheet (column A ``NSE:SYMBOL`` or plain symbol, Date, formula result)
and inserts only MISSING prices into the database. Never updates existing records.
Does NOT write any formulas - only reads what's already on the sheet.

Prerequisites:
  - Run from project root (cd /path/to/app)
  - service_account.json in project root
  - Sheet shared with service account

Usage:
  python scripts/sync_historical_prices_from_sheets.py
  python scripts/sync_historical_prices_from_sheets.py --dry-run
"""
import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _parse_date(val):
    """Parse date from string; return None if invalid."""
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _is_valid_price(price):
    """Validate price: must be positive finite number in reasonable range."""
    try:
        p = float(price)
        return 0.01 <= p <= 1e8 and abs(p) == p
    except (TypeError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Sync DB from HistoricalPrices sheet")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    args = parser.parse_args()

    service_account = Path(__file__).resolve().parent.parent / "service_account.json"
    if not service_account.exists():
        print("Error: service_account.json not found in project root")
        sys.exit(1)

    from services.google_sheets_historical_price import GoogleSheetsHistoricalPrice

    print("Reading data from HistoricalPrices sheet...")
    rows = GoogleSheetsHistoricalPrice.read_all_prices_from_sheet()
    if not rows:
        print("No valid price data found on sheet.")
        return

    print(f"Found {len(rows)} valid rows on sheet.")

    if args.dry_run:
        for r in rows[:10]:
            print(f"  {r['symbol']} {r['date_str']} ₹{r['price']}")
        if len(rows) > 10:
            print(f"  ... and {len(rows) - 10} more")
        print(f"\nDry run: would sync {len(rows)} records")
        return

    from main import create_app
    from models import db, Security, HistoricalPrice
    from config import config
    import os

    os.environ.setdefault("FLASK_ENV", "production")
    app = create_app(config.get("production", config["default"]))

    inserted = 0
    skipped_existing = 0
    skipped_no_security = 0
    skipped_invalid = 0

    with app.app_context():
        symbol_to_security = {s.symbol.upper(): s for s in Security.query.all()}
        for r in rows:
            symbol = (r.get('symbol') or '').strip().upper()
            date_str = (r.get('date_str') or '').strip()
            price_val = r.get('price')

            if not _is_valid_price(price_val):
                skipped_invalid += 1
                continue
            price = float(price_val)

            actual_date = _parse_date(date_str)
            if actual_date is None:
                skipped_invalid += 1
                continue

            security = symbol_to_security.get(symbol)
            if not security:
                skipped_no_security += 1
                continue

            existing = HistoricalPrice.query.filter_by(
                security_id=security.id, date=actual_date
            ).first()

            if existing:
                skipped_existing += 1
                continue
            db.session.add(HistoricalPrice(
                security_id=security.id,
                date=actual_date,
                close_price=price,
                source="googlefinance",
                confidence=0.98,
            ))
            inserted += 1

        db.session.commit()

    print(f"\nDone. Inserted: {inserted}, Already existed (skipped): {skipped_existing}, No security: {skipped_no_security}, Invalid: {skipped_invalid}")


if __name__ == "__main__":
    main()
