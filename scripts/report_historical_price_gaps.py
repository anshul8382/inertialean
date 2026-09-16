#!/usr/bin/env python3
"""
Summarise missing historical price data (calendar gaps between saved closes).

Uses the same bracketing rules as the historical-prices upload page: span from
each security's first transaction (after 2000-01-01) through today.

Usage (from project root):
  python scripts/report_historical_price_gaps.py
  python scripts/report_historical_price_gaps.py --symbol EMBASSY
  python scripts/report_historical_price_gaps.py --only-flagged --json
  python scripts/report_historical_price_gaps.py --min-block 7
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _parse_cutoff(s: str) -> date:
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", help="Only this NSE symbol (case-insensitive)")
    p.add_argument(
        "--through",
        metavar="YYYY-MM-DD",
        help="Span end date (default: today UTC date)",
    )
    p.add_argument(
        "--min-block",
        type=int,
        default=None,
        metavar="N",
        help="Only show rows whose largest gap is >= N calendar days (default: report all)",
    )
    p.add_argument(
        "--only-flagged",
        action="store_true",
        help=(
            f"Only securities that would show on upload (max gap >= "
            f"upload threshold; uses same default as app unless --flag-threshold set)"
        ),
    )
    p.add_argument(
        "--flag-threshold",
        type=int,
        default=None,
        help="Upload-style alert threshold (calendar days in max block); app default if omitted",
    )
    p.add_argument("--limit", type=int, default=None, help="Max securities to print")
    p.add_argument("--json", action="store_true", help="Print JSON array to stdout")
    args = p.parse_args()

    from main import create_app
    from sqlalchemy import func

    from extensions import db
    from models import Security, Transaction

    from routes.historical_prices import (
        MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG,
        historical_price_calendar_gap_report,
    )

    span_end = _parse_cutoff(args.through) if args.through else date.today()
    flag_thresh = (
        args.flag_threshold
        if args.flag_threshold is not None
        else MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG
    )
    min_block_filter = args.min_block

    app = create_app()
    rows_out: list[dict] = []
    tx_epoch = date(2000, 1, 1)

    with app.app_context():
        q = (
            db.session.query(
                Security.id,
                Security.symbol,
                Security.name,
                func.min(Transaction.transaction_date).label("first_tx"),
            )
            .join(Transaction, Security.id == Transaction.security_id)
            .filter(Transaction.transaction_date > tx_epoch)
            .group_by(Security.id, Security.symbol, Security.name)
            .order_by(Security.symbol.asc())
        )

        if args.symbol:
            q = q.filter(
                Security.symbol == args.symbol.strip().upper(),
            )

        securities = q.all()
        total_sec = len(securities)

        sum_total_missing = 0

        for idx, sec in enumerate(securities):
            if args.limit is not None and idx >= args.limit:
                break

            first_tx = sec.first_tx
            if isinstance(first_tx, datetime):
                first_tx = first_tx.date()

            span_start = first_tx

            report = historical_price_calendar_gap_report(
                sec.id, span_start, span_end, flag_min_interior=flag_thresh
            )
            max_blk = report["max_block_missing_calendar_days"]
            if min_block_filter is not None and max_blk < min_block_filter:
                continue
            if args.only_flagged and not report["flags_upload_alert"]:
                continue

            row = {
                "security_id": sec.id,
                "symbol": sec.symbol,
                "name": sec.name,
                "first_transaction": span_start.isoformat(),
                "through": span_end.isoformat(),
                "quoted_distinct_days": report["quoted_distinct_days"],
                "missing_segment_count": report["missing_segment_count"],
                "total_missing_calendar_days": report["total_missing_calendar_days"],
                "max_block_missing_calendar_days": max_blk,
                "max_block_left": report["max_block_left"],
                "max_block_right": report["max_block_right"],
                "flags_upload_alert": report["flags_upload_alert"],
            }
            rows_out.append(row)
            sum_total_missing += report["total_missing_calendar_days"]

    if args.json:
        print(json.dumps(rows_out, indent=2))
        return 0

    print(
        f"Historical price gaps | span end {span_end.isoformat()} | "
        f"upload threshold (max block) ≥ {flag_thresh} calendar days\n"
        f"Securities in transaction universe: {total_sec}"
        + (f" (processing limit first {args.limit})" if args.limit else "")
    )

    rows_out.sort(key=lambda x: (-x["max_block_missing_calendar_days"], x["symbol"]))

    for r in rows_out:
        alert = "ALERT" if r["flags_upload_alert"] else " ok "
        print(
            f"[{alert}] {r['symbol']:16} "
            f"max_blk={r['max_block_missing_calendar_days']:5} "
            f"total_missing_days={r['total_missing_calendar_days']:5} "
            f"gaps={r['missing_segment_count']:4} quotes={r['quoted_distinct_days']:5} "
            f"{r['max_block_left'] or '?'} … {r['max_block_right'] or '?'}"
        )

    alerts = sum(1 for r in rows_out if r["flags_upload_alert"])
    print("---")
    print(f"Rows printed: {len(rows_out)}  (upload-style alert on {alerts})")
    print(f"Sum of total_missing_calendar_days across printed rows: {sum_total_missing}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
