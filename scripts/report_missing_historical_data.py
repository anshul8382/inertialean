#!/usr/bin/env python3
"""
Readable summary before fixing historical prices:

- Weekday misses: counts where ``historical_price`` has **no row** Mon–Fri in the window.
- Optional: calendar “holes” between saved dates (portfolio-style), same idea as upload alerts.

Examples:
  python scripts/report_missing_historical_data.py
  python scripts/report_missing_historical_data.py --days 60 --all-security-types
  python scripts/report_missing_historical_data.py --days 365 --calendar-holes
  python scripts/report_missing_historical_data.py --json > missing_summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_UPLOAD_CAP_WEEKDAYS = 30


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--days",
        "-d",
        type=int,
        default=365,
        help="Look back this many calendar days from today (default 365)",
    )
    ap.add_argument(
        "--all-security-types",
        action="store_true",
        help="All transaction-linked symbols (default: STOCK securities only)",
    )
    ap.add_argument(
        "--calendar-holes",
        action="store_true",
        help="Add per-symbol calendar gap stats (more DB work)",
    )
    ap.add_argument(
        "--sheet-cap",
        type=int,
        default=DEFAULT_UPLOAD_CAP_WEEKDAYS,
        metavar="N",
        help="Treat >N weekday misses/symbol as 'prefer CSV upload' (default 30)",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON only")
    args = ap.parse_args()

    from main import create_app
    from scripts.find_historical_price_gaps import find_gaps

    app = create_app()
    st = None if args.all_security_types else "STOCK"
    gaps, _summ = find_gaps(
        days_back=args.days,
        security_type_filter=st,
        flask_app=app,
    )

    per_symbol: Counter[str] = Counter(str(g["symbol"]) for g in gaps)
    sheet_ok_symbols = [s for s, n in per_symbol.items() if n <= args.sheet_cap]
    csv_symbols = [s for s, n in per_symbol.items() if n > args.sheet_cap]
    gaps_for_sheet_symbols = sum(per_symbol[s] for s in sheet_ok_symbols)
    gaps_for_csv_symbols = sum(per_symbol[s] for s in csv_symbols)

    out: dict = {
        "as_of": date.today().isoformat(),
        "lookback_calendar_days": args.days,
        "security_types": "all_with_transactions" if st is None else "STOCK",
        "missing_weekday_datapoints": len(gaps),
        "securities_with_any_weekday_miss": len(per_symbol),
        "sheets_backfill_cap_weekdays_per_symbol": args.sheet_cap,
        "symbols_sheet_backfill_band": sorted(sheet_ok_symbols),
        "symbols_prefer_csv_upload": sorted(csv_symbols),
        "gap_rows_for_sheet_band": gaps_for_sheet_symbols,
        "gap_rows_for_csv_band": gaps_for_csv_symbols,
        "top_weekday_misses_by_symbol": per_symbol.most_common(50),
    }

    if args.calendar_holes:
        from extensions import db
        from models import Security, Transaction
        from sqlalchemy import func

        from routes.historical_prices import (
            MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG,
            historical_price_calendar_gap_report,
        )

        holes: list[dict] = []
        with app.app_context():
            q = (
                db.session.query(
                    Security.id.label("sid"),
                    Security.symbol,
                    func.min(Transaction.transaction_date).label("first_tx"),
                )
                .join(Transaction, Transaction.security_id == Security.id)
                .filter(Transaction.transaction_date > date(2000, 1, 1))
                .group_by(Security.id, Security.symbol)
                .order_by(Security.symbol)
            )
            if st == "STOCK":
                q = q.filter(Security.security_type == "STOCK")

            span_end = date.today()
            for row in q.all():
                first_tx = row.first_tx
                if hasattr(first_tx, "date"):
                    first_tx = first_tx.date()
                first_tx_d = first_tx if isinstance(first_tx, date) else span_end
                rpt = historical_price_calendar_gap_report(
                    int(row.sid),
                    first_tx_d,
                    span_end,
                    flag_min_interior=None,
                )
                holes.append(
                    {
                        "symbol": row.symbol,
                        "security_id": int(row.sid),
                        "first_transaction": first_tx_d.isoformat(),
                        "quoted_distinct_days": rpt["quoted_distinct_days"],
                        "total_missing_calendar_days_sum_of_holes": rpt[
                            "total_missing_calendar_days"
                        ],
                        "max_calendar_hole_between_closes_days": rpt[
                            "max_block_missing_calendar_days"
                        ],
                        "upload_page_style_alert_missing_block_ge_days": MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG,
                        "flags_calendar_hole_ge_threshold": bool(
                            rpt["max_block_missing_calendar_days"]
                            >= MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG
                        ),
                        "max_block_brackets": (
                            rpt["max_block_left"],
                            rpt["max_block_right"],
                        ),
                    }
                )

        holes_sorted = sorted(
            holes,
            key=lambda x: (
                -x["total_missing_calendar_days_sum_of_holes"],
                x["symbol"],
            ),
        )
        flagged_holes = sum(
            1 for h in holes if h["flags_calendar_hole_ge_threshold"]
        )
        out["calendar_hole_summaries"] = holes_sorted[:200]
        out["calendar_symbols_flagged_ge_threshold_count"] = flagged_holes
        out[
            "note_calendar_hole"
        ] = "max_block_* counts calendar dates strictly between two saved closes; upload page uses threshold on max block."

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print(f"\nMissing historical price report   as-of {out['as_of']}   lookback={args.days}d", end="")
    print(f"   universe={out['security_types']}\n")
    print("--- Weekdays with no closing price row ---")
    print(f"  Missing datapoints (each = one symbol×date weekday): {len(gaps):,}")
    print(f"  Securities with ≥1 miss:                         {len(per_symbol)}")
    print(
        f"  Rows fixable via small-batch Sheets script (≤{args.sheet_cap} misses/symbol): "
        f"{len(sheet_ok_symbols)} symbols → {gaps_for_sheet_symbols:,} gap-rows"
    )
    print(
        f"  Prefer CSV upload route (>{args.sheet_cap} misses/symbol):        "
        f"{len(csv_symbols)} symbols → {gaps_for_csv_symbols:,} gap-rows"
    )
    print("\n  Top weekday misses:")
    for sym, n in per_symbol.most_common(25):
        tag = "→ CSV upload" if n > args.sheet_cap else "→ Sheets OK"
        print(f"    {sym:16} {n:5}  {tag}")
    if len(per_symbol) > 25:
        print(f"    … +{len(per_symbol) - 25} more symbols")

    if args.calendar_holes:
        fh = out.get("calendar_symbols_flagged_ge_threshold_count", 0)
        print("\n--- Calendar holes (first_trade → today) ---")
        print(
            f"  Symbols where largest hole ≥ {MISSING_CLOSE_BLOCK_MIN_CALENDAR_DAYS_TO_FLAG} consecutive "
            f"calendar days without quotes: {fh}"
        )
        print(
            "  Top 15 by summed missing calendar days across holes:",
        )
        for h in holes_sorted[:15]:
            fl = "ALERT" if h["flags_calendar_hole_ge_threshold"] else ""
            print(
                f"    {h['symbol']:14} sum_missing={h['total_missing_calendar_days_sum_of_holes']:5} "
                f"max_block={h['max_calendar_hole_between_closes_days']:4} "
                f"quotes={h['quoted_distinct_days']:5} {fl}"
            )

    print(
        "\nNext steps: small holes → ",
        "`python scripts/backfill_historical_prices_from_sheets.py --days …`;",
        " large per-symbol misses → Maintenance → Historical Prices upload (CSV).\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
