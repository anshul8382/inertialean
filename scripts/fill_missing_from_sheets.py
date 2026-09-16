#!/usr/bin/env python3
"""
Fill MISSING price-accuracy dates from GOOGLEFINANCE (HistoricalPrices sheet).

Same calendar date only. Skips weekends. Default is dry-run (no DB writes).

  python3 scripts/fill_missing_from_sheets.py
  python3 scripts/fill_missing_from_sheets.py --batch 300
  python3 scripts/fill_missing_from_sheets.py --commit --allow-prod-db
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=350)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--allow-prod-db", action="store_true")
    args = ap.parse_args()

    from main import create_app
    from services.price_accuracy_service import fill_missing_from_google_sheets

    app = create_app()
    with app.app_context():
        db_name = (app.config.get("DB_NAME") or "").strip()
        print(
            json.dumps(
                {
                    "db_host": app.config.get("DB_HOST"),
                    "db_port": app.config.get("DB_PORT"),
                    "db_name": db_name,
                    "commit": bool(args.commit),
                    "batch": int(args.batch),
                }
            ),
            flush=True,
        )
        if args.commit and db_name == "inertia_app2025" and not args.allow_prod_db:
            print(json.dumps({"ok": False, "error": "refusing_prod_db_commit"}))
            return 2

        after = None
        cycles = []
        while True:
            result = fill_missing_from_google_sheets(
                dry_run=not args.commit,
                max_unique_lookups=int(args.batch),
                after_finding_id=after,
            )
            cycles.append(result)
            print(json.dumps(result, indent=2, default=str), flush=True)
            if not result.get("ok"):
                return 1
            if result.get("batch_complete") or int(result.get("sheet_lookups") or 0) == 0:
                break
            nxt = int(result.get("last_finding_id") or 0)
            if after is not None and nxt <= after:
                break
            after = nxt

        would = sum(int(c.get("would_fill") or 0) for c in cycles)
        empty = sum(int(c.get("holiday_or_empty") or 0) for c in cycles)
        rejected = sum(int(c.get("rejected_adjusted") or 0) for c in cycles)
        msg = next((c.get("user_message") for c in cycles if c.get("user_message")), "")
        print(
            json.dumps(
                {
                    "ok": True,
                    "cycles": len(cycles),
                    "would_fill_total": would,
                    "holiday_or_empty_total": empty,
                    "rejected_adjusted_total": rejected,
                    "user_message": msg or None,
                }
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
