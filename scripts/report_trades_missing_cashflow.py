#!/usr/bin/env python3
"""
List BUY/SELL trades whose matching cashflow row is missing.

Cashflow has no transaction_id. Matching rules (same client):
  auto-generated description, signed amount (INFLOW + or −) within the amount
  band, leftover day-net, then ±3 settlement days. Dummy opening-book dates
  are skipped.

Read-only. Default window is the last 30 calendar days (trade date or created_at).

Usage (from project root):
  python3 scripts/report_trades_missing_cashflow.py
  python3 scripts/report_trades_missing_cashflow.py --days 30 --csv docs/
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _write_csv(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = dict(row)
            for key in ("expected_cf_amount", "trade_amount", "price"):
                if isinstance(out.get(key), Decimal):
                    out[key] = f"{out[key]:.2f}"
            if isinstance(out.get("quantity"), Decimal):
                out["quantity"] = str(out["quantity"])
            writer.writerow(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=30, help="Lookback in calendar days (default 30)")
    p.add_argument("--start", metavar="YYYY-MM-DD", help="Inclusive start date (overrides --days)")
    p.add_argument("--end", metavar="YYYY-MM-DD", help="Inclusive end date (default: today)")
    p.add_argument("--include-inactive", action="store_true", help="Include inactive clients")
    p.add_argument(
        "--csv",
        metavar="DIR",
        default="docs",
        help="Directory for CSV output (default: docs/)",
    )
    args = p.parse_args()

    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    start = (
        datetime.strptime(args.start, "%Y-%m-%d").date()
        if args.start
        else end - timedelta(days=max(args.days, 1) - 1)
    )

    from main import create_app
    from services.trades_missing_cashflow_report_service import find_trades_missing_cashflow

    app = create_app()
    with app.app_context():
        missing, trade_count = find_trades_missing_cashflow(
            start,
            end,
            active_only=not args.include_inactive,
        )

    missing.sort(
        key=lambda r: (r.get("transaction_date") or "", r.get("client_id") or 0, r.get("transaction_id") or 0)
    )

    print("Trades missing corresponding cashflow (read-only)")
    print(f"  Window:        {start.isoformat()} .. {end.isoformat()}")
    print(f"  BUY/SELL trades in window: {trade_count}")
    print(f"  Missing cashflow:          {len(missing)}")
    print()

    if not missing:
        print("(none — every trade in the window has a same-day cashflow match or day-net cover)")
    else:
        header = (
            f"{'Txn':>6}  {'Date':<10}  {'Client':<22}  {'Type':<4}  {'Symbol':<12}  "
            f"{'Expected CF':>14}  {'Reason'}"
        )
        print(header)
        print("-" * len(header))
        for r in missing:
            name = (r.get("client_name") or "")[:22]
            print(
                f"{r['transaction_id']:>6}  {r.get('transaction_date') or '':<10}  "
                f"{name:<22}  {r.get('type') or '':<4}  {(r.get('symbol') or ''):<12}  "
                f"{float(r['expected_cf_amount']):>14,.2f}  {r.get('reason')}"
            )

    out_dir = Path(args.csv)
    stamp = date.today().isoformat()
    path = out_dir / f"trades_missing_cashflow_{stamp}.csv"
    fields = [
        "transaction_id",
        "transaction_date",
        "created_at",
        "client_id",
        "client_name",
        "client_active",
        "type",
        "symbol",
        "security_id",
        "quantity",
        "price",
        "trade_amount",
        "expected_cf_amount",
        "reason",
        "same_day_cashflow_count",
        "same_day_cashflow_net",
        "same_day_trade_net",
        "day_gap",
        "same_day_cashflow_ids",
        "trade_edit_url",
        "cashflows_url",
        "notes",
    ]
    _write_csv(path, missing, fields)
    print()
    print(f"Wrote {path} ({len(missing)} rows)")
    print("No database changes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
