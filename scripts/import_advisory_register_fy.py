#!/usr/bin/env python3
"""Import Advisory Register for an Indian FY from Gmail Sent + reliable DB.

Usage:
  python3 scripts/import_advisory_register_fy.py
  python3 scripts/import_advisory_register_fy.py --fy 2025-26 --dry-run
  python3 scripts/import_advisory_register_fy.py --no-gmail   # DB backfill only
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description="Import advisory register FY")
    parser.add_argument("--fy", default="2025-26", help="Indian FY label e.g. 2025-26")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-gmail",
        action="store_true",
        help="Skip IMAP; only backfill from reliable DB sessions",
    )
    args = parser.parse_args()

    from main import create_app

    app = create_app()
    with app.app_context():
        from services.advisory_register_service import run_fy_import
        from services.db_cutover import advisory_register_enabled

        if not advisory_register_enabled():
            print("ERROR: advisory_register_entry missing or deferred.")
            print("Run: python3 migrations/add_advisory_register_entry.py")
            sys.exit(1)

        result = run_fy_import(
            fy_label=args.fy,
            user_id=None,
            dry_run=args.dry_run,
            fetch_gmail=not args.no_gmail,
        )
        print(json.dumps(result, indent=2, default=str))
        if not result.get("ok"):
            sys.exit(1)


if __name__ == "__main__":
    main()
