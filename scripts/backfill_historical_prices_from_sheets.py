#!/usr/bin/env python3
"""
Backfill ``historical_price`` from Google Sheets (GOOGLEFINANCE on NSE symbols).

Flow:
1. Build a list of missing (security_id, symbol, date) — by default **weekdays**
   with no row in ``historical_price`` (see ``find_historical_price_gaps.find_gaps``).
2. Skip any security with **more than N missing weekdays** (default **30**) in scope — large holes should use **CSV upload** instead of per-day Sheets formulas.
3. Write formulas to the ``HistoricalPrices`` sheet in **chunks** (avoid timeouts; worksheet auto-expands rows when needed).
4. Wait for Sheets to calculate, read column C, upsert into PostgreSQL.

Prerequisites:
  - Run from project root: ``cd /path/to/app``
  - ``service_account.json`` at project root with access to the spreadsheet
  - ``gspread``, ``oauth2client`` (see requirements.txt)

Usage:
  # Dry-run: fetch first 10 gaps only, no DB
  python scripts/backfill_historical_prices_from_sheets.py --dry-run --limit 10

  # Last 120 days, stocks only, commits in chunks of 40
  python scripts/backfill_historical_prices_from_sheets.py --days 120 --chunk-size 40

  # REITs / ETFs / all types with transactions (NSE formula may fail for some instruments)
  python scripts/backfill_historical_prices_from_sheets.py --days 90 --all-security-types --symbol EMBASSY

  # From a JSON file produced by: python scripts/find_historical_price_gaps.py -o gaps.json
  python scripts/backfill_historical_prices_from_sheets.py --gaps gaps.json --chunk-size 50
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _is_valid_price(price) -> bool:
    try:
        p = float(price)
        return 0.01 <= p <= 1e8 and abs(p) == p
    except (TypeError, ValueError):
        return False


def _parse_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if hasattr(val, "date") and callable(getattr(val, "date", None)):
        return val.date()
    if isinstance(val, str):
        try:
            return date.fromisoformat(val[:10])
        except ValueError:
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical prices from Google Sheets")
    parser.add_argument(
        "--gaps",
        "-g",
        help="JSON path: object with \"gaps\" list or bare list [{security_id, symbol, date}, ...]",
    )
    parser.add_argument("--symbol", "-s", help="Only gaps for this symbol (exact uppercase match after load)")
    parser.add_argument("--days", "-d", type=int, help="Only weekdays missing in the last N days (when not using --gaps)")
    parser.add_argument("--limit", "-l", type=int, help="Cap number of (symbol, date) gaps to process")
    parser.add_argument("--chunk-size", type=int, default=40, metavar="N", help="Sheet batch size (default: 40)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch from Sheets only; do not write DB")
    parser.add_argument("--wait", type=float, default=4.0, help="Seconds to wait after each chunk write")
    parser.add_argument(
        "--all-security-types",
        action="store_true",
        help="Include all securities with transactions (when discovering gaps internally)",
    )
    parser.add_argument(
        "--max-missing-days-for-sheets",
        type=int,
        default=30,
        metavar="D",
        help=(
            "Per security: skip Google Sheets backfill if more than D missing weekdays in scope "
            "(use Historical Prices CSV upload instead). Default 30."
        ),
    )
    parser.add_argument(
        "--allow-large-sheet-backfill",
        action="store_true",
        help="Disable the per-security gap cap (not recommended for very large holes).",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    if not (root / "service_account.json").is_file():
        print("Error: service_account.json not found in project root.")
        return 1

    from main import create_app

    flask_app = None

    # --- load gaps ---
    if args.gaps:
        raw = Path(args.gaps).read_text(encoding="utf-8")
        data = json.loads(raw)
        gaps = data.get("gaps", data) if isinstance(data, dict) else data
        if not isinstance(gaps, list):
            print("Invalid gaps JSON: expected list or {gaps: [...]} ")
            return 1
        for i, g in enumerate(gaps):
            if not isinstance(g, dict) or "security_id" not in g or "symbol" not in g or "date" not in g:
                print(f"Invalid gap at index {i}: need security_id, symbol, date — {g!r}")
                return 1
    else:
        # One Flask app for gap scan + DB (no second create_app later).
        flask_app = create_app()
        from scripts.find_historical_price_gaps import find_gaps

        st_filter = None if args.all_security_types else "STOCK"
        gaps, _ = find_gaps(
            symbol_filter=args.symbol,
            days_back=args.days,
            security_type_filter=st_filter,
            flask_app=flask_app,
        )
        if not gaps:
            print("No gaps found (try --days, --symbol, or --all-security-types).")
            return 0

    if args.symbol:
        su = args.symbol.strip().upper()
        gaps = [g for g in gaps if str(g.get("symbol", "")).strip().upper() == su]

    # dedupe stable order
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for g in gaps:
        k = (str(g["symbol"]).strip().upper(), str(g["date"]).strip()[:10])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(g)
    gaps = deduped

    if not args.allow_large_sheet_backfill and gaps:
        thr = args.max_missing_days_for_sheets
        by_sec: defaultdict[int, list[dict]] = defaultdict(list)
        for g in gaps:
            by_sec[int(g["security_id"])].append(g)
        skipped_info: list[tuple[str, int]] = []
        gaps = []
        for sid, rows in sorted(by_sec.items(), key=lambda x: -len(x[1])):
            if len(rows) > thr:
                sym = str(rows[0].get("symbol", sid))
                skipped_info.append((sym, len(rows)))
            else:
                gaps.extend(rows)
        if skipped_info:
            print(
                f"Skipping {len(skipped_info)} securities with >{thr} weekday gaps each "
                f"(upload CSV instead). Examples:",
                flush=True,
            )
            for sym, n in skipped_info[:15]:
                print(f"    {sym}: {n} missing weekdays", flush=True)
            if len(skipped_info) > 15:
                print(f"    ... +{len(skipped_info) - 15} more", flush=True)
        print(
            f"Sheets-eligible weekday gaps remaining: {len(gaps)} (cap={thr}); "
            f"use --allow-large-sheet-backfill to override.",
            flush=True,
        )

    if args.limit:
        gaps = gaps[: args.limit]

    if not gaps:
        print(
            "No gaps to process after filters (either none in scope or all exceeded "
            "`--max-missing-days-for-sheets`; use upload).",
            flush=True,
        )
        return 0

    symbol_date_pairs = [(g["symbol"], g["date"]) for g in gaps]
    key_to_gap = {(str(g["symbol"]).strip().upper(), str(g["date"]).strip()[:10]): g for g in gaps}

    print(
        f"Processing {len(gaps)} weekday gaps in chunks of {args.chunk_size} "
        f"({'dry-run' if args.dry_run else 'writes DB'}, wait {args.wait}s/chunk)...",
        flush=True,
    )

    from services.google_sheets_historical_price import GoogleSheetsHistoricalPrice

    all_prices: dict = {}
    for chunk in _chunks(symbol_date_pairs, max(1, args.chunk_size)):
        part = GoogleSheetsHistoricalPrice.get_historical_price_batch(chunk, wait_seconds=args.wait)
        all_prices.update(part)
        print(
            f"  Sheets returned {len(part)} prices for chunk ({len(chunk)} requested)",
            flush=True,
        )

    if not all_prices:
        print("No usable prices returned from Google Sheets.")
        return 1

    if args.dry_run:
        for (sym, ds), pd in sorted(all_prices.items())[: min(40, len(all_prices))]:
            print(f"  {sym} {ds}: ₹{pd['price']}")
        if len(all_prices) > 40:
            print(f"  ... ({len(all_prices) - 40} more)")
        print(f"\nDry-run: got {len(all_prices)} prices for {len(gaps)} gaps; no DB changes.")
        return 0

    app = flask_app or create_app()

    from extensions import db
    from models import HistoricalPrice

    inserted = 0
    updated = 0
    skipped_invalid = 0
    unmatched = 0

    with app.app_context():
        for (symbol_key, date_str), pd in all_prices.items():
            g = key_to_gap.get((symbol_key, date_str[:10]))
            if not g:
                unmatched += 1
                continue
            security_id = int(g["security_id"])
            if not _is_valid_price(pd.get("price")):
                skipped_invalid += 1
                continue
            price = float(pd["price"])
            actual_date = _parse_date(pd.get("date"))
            if actual_date is None:
                skipped_invalid += 1
                continue

            existing = HistoricalPrice.query.filter_by(
                security_id=security_id,
                date=actual_date,
            ).first()

            if existing:
                existing.close_price = price
                existing.source = "googlefinance"
                existing.updated_at = datetime.utcnow()
                updated += 1
            else:
                db.session.add(
                    HistoricalPrice(
                        security_id=security_id,
                        date=actual_date,
                        close_price=price,
                        source="googlefinance",
                        confidence=float(pd.get("confidence", 0.98)),
                    )
                )
                inserted += 1

        db.session.commit()

    no_price = len(gaps) - len(all_prices)
    print(
        f"\nDone. Inserted: {inserted}, Updated: {updated}, "
        f"No price from Sheets (vs gaps): approx {max(0, no_price)}, "
        f"invalid (skipped): {skipped_invalid}, key unmatched: {unmatched}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
