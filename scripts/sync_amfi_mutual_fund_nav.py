#!/usr/bin/env python3
"""
Fetch AMFI NAVAll.txt and upsert daily NAV for Mutual Fund securities
that have meta_data.amfi_scheme_code set.

Usage (from project root):
  python scripts/sync_amfi_mutual_fund_nav.py
  python scripts/sync_amfi_mutual_fund_nav.py --dry-run

Requires network access to https://www.amfiindia.com/spages/NAVAll.txt
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync AMFI NAV for linked mutual fund securities")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only download and parse NAVAll.txt; print counts; do not write DB",
    )
    args = parser.parse_args()

    os.environ.setdefault("FLASK_ENV", "development")

    from main import create_app
    from services.amfi_nav_service import (
        clear_nav_all_cache,
        fetch_nav_all_text,
        parse_nav_all_text,
        sync_daily_nav_for_linked_securities,
    )

    app = create_app()
    with app.app_context():
        from extensions import db
        from models import HistoricalPrice, Security

        clear_nav_all_cache()
        if args.dry_run:
            text = fetch_nav_all_text()
            rows = parse_nav_all_text(text)
            codes = {r.scheme_code for r in rows}
            print(f"Parsed {len(rows)} NAV rows, {len(codes)} distinct scheme codes (dry run, no DB write)")
            return

        stats = sync_daily_nav_for_linked_securities(
            db_session=db,
            Security=Security,
            HistoricalPrice=HistoricalPrice,
            fetch_fresh=True,
        )
        db.session.commit()
        print(stats)


if __name__ == "__main__":
    main()
