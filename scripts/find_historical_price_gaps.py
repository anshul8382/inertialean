#!/usr/bin/env python3
"""
Find gaps in historical price data - securities and dates for which data is missing.

Usage:
  python scripts/find_historical_price_gaps.py
  python scripts/find_historical_price_gaps.py --output gaps.json
  python scripts/find_historical_price_gaps.py --symbol IEX
  python scripts/find_historical_price_gaps.py --days 90  # Only check last 90 days
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _trading_days(start: date, end: date):
    """Yield trading days (weekdays) from start to end inclusive."""
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon=0 .. Fri=4
            yield current
        current += timedelta(days=1)


def find_gaps(
    symbol_filter=None,
    end_date=None,
    days_back=None,
    security_type_filter="STOCK",
    flask_app=None,
):
    """
    Find (security_id, symbol, date) tuples for which historical_price has no data.

    Args:
        symbol_filter: If set, only check this symbol (e.g. 'IEX')
        end_date: End of date range (default: today)
        days_back: If set, only check this many days back from end_date; if omitted, default window is last 365 days
        security_type_filter: e.g. 'STOCK', or ``None`` to include all securities with transactions
            (still uses NSE GOOGLEFINANCE in the backfill script—may fail for non-NSE instruments)
        flask_app: optional existing Flask app (avoids duplicate ``create_app()`` in same process)
    """
    from main import create_app
    from models import Security, Transaction, HistoricalPrice
    from sqlalchemy import func
    from config import config
    import os

    os.environ.setdefault("FLASK_ENV", "production")
    app = flask_app or create_app(config.get("production", config["default"]))

    with app.app_context():
        end = end_date or date.today()
        start = end - timedelta(days=365) if days_back is None else end - timedelta(days=days_back)

        # Get securities with transactions (exclude dummy dates)
        q = (
            Security.query.join(Transaction, Transaction.security_id == Security.id)
            .filter(Transaction.transaction_date > date(2000, 1, 1))
            .with_entities(
                Security.id,
                Security.symbol,
                func.min(Transaction.transaction_date).label("first_txn"),
            )
            .group_by(Security.id, Security.symbol)
        )
        if symbol_filter:
            q = q.filter(Security.symbol.ilike(symbol_filter))
        if security_type_filter is not None:
            q = q.filter(Security.security_type == security_type_filter)

        securities = q.all()
        gaps = []
        summary = []

        for security_id, symbol, first_txn in securities:
            first_txn = first_txn.date() if hasattr(first_txn, "date") else first_txn
            range_start = max(start, first_txn)
            range_end = min(end, date.today())

            if range_start > range_end:
                continue

            # Get existing dates for this security
            rows = HistoricalPrice.query.filter_by(security_id=security_id).filter(
                HistoricalPrice.date >= range_start, HistoricalPrice.date <= range_end
            ).with_entities(HistoricalPrice.date).all()
            existing_dates = {r[0] if isinstance(r[0], date) else r[0].date() for r in rows}

            missing_dates = []
            for d in _trading_days(range_start, range_end):
                if d not in existing_dates:
                    ds = d.isoformat()
                    missing_dates.append(ds)
                    gaps.append({"security_id": security_id, "symbol": symbol, "date": ds})

            if missing_dates:
                summary.append(
                    {
                        "security_id": security_id,
                        "symbol": symbol,
                        "missing_count": len(missing_dates),
                        "date_range": f"{range_start} to {range_end}",
                        "missing_dates": sorted(missing_dates),
                    }
                )

        return gaps, summary


def main():
    parser = argparse.ArgumentParser(description="Find historical price gaps")
    parser.add_argument("--symbol", "-s", help="Filter by symbol (e.g. IEX)")
    parser.add_argument("--output", "-o", help="Output file (JSON)")
    parser.add_argument("--days", "-d", type=int, help="Only check last N days")
    parser.add_argument("--end-date", help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    parser.add_argument(
        "--all-security-types",
        action="store_true",
        help="Include all transaction-linked securities (default: STOCK only)",
    )
    args = parser.parse_args()

    end_date = None
    if args.end_date:
        end_date = date.fromisoformat(args.end_date)

    gaps, summary = find_gaps(
        symbol_filter=args.symbol,
        end_date=end_date,
        days_back=args.days,
        security_type_filter=None if args.all_security_types else "STOCK",
    )

    total_gaps = len(gaps)
    total_securities = len(summary)
    print(f"Found {total_gaps} missing price records across {total_securities} securities")

    if args.format == "csv":
        lines = ["security_id,symbol,date"]
        for g in gaps:
            lines.append(f"{g['security_id']},{g['symbol']},{g['date']}")
        out = "\n".join(lines)
    else:
        out = json.dumps(
            {"gaps": gaps, "summary": summary, "total_gaps": total_gaps, "total_securities": total_securities},
            indent=2,
        )

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Written to {args.output}")
    else:
        print(out)


if __name__ == "__main__":
    main()
