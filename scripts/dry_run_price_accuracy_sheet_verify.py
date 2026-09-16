#!/usr/bin/env python3
"""
Dry-run spike verify vs Google Sheets (no DB writes).

Uses the Flask .env DB (often SSH tunnel 127.0.0.1:3307). Does not ack spikes.

  python3 scripts/dry_run_price_accuracy_sheet_verify.py
  python3 scripts/dry_run_price_accuracy_sheet_verify.py --commit   # writes acks — local/dev only
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
    ap.add_argument(
        "--commit",
        action="store_true",
        help="Write spike acks (default is dry-run). Refuses prod DB name unless --allow-prod-db.",
    )
    ap.add_argument(
        "--allow-prod-db",
        action="store_true",
        help="Permit --commit when DB_NAME is inertia_app2025.",
    )
    args = ap.parse_args()

    from main import create_app
    from services.price_accuracy_service import verify_spikes_from_google_sheets

    app = create_app()
    with app.app_context():
        db_name = (app.config.get("DB_NAME") or "").strip()
        db_host = (app.config.get("DB_HOST") or "").strip()
        print(
            json.dumps(
                {
                    "db_host": db_host,
                    "db_port": app.config.get("DB_PORT"),
                    "db_name": db_name,
                    "commit": bool(args.commit),
                }
            ),
            flush=True,
        )
        if args.commit and db_name == "inertia_app2025" and not args.allow_prod_db:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "refusing_prod_db_commit",
                        "hint": "This .env uses inertia_app2025 (prod name). Dry-run only, or pass --allow-prod-db.",
                    }
                )
            )
            return 2
        result = verify_spikes_from_google_sheets(
            all_spikes_for_latest_run=True,
            dry_run=not args.commit,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") is not False and "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
