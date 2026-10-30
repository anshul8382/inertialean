#!/usr/bin/env python3
"""
Run daily client returns digest against the configured DB (typically local/dev).

Usage:
  ./venv/bin/python scripts/run_daily_client_returns_digest.py
  ./venv/bin/python scripts/run_daily_client_returns_digest.py --limit 5
  ./venv/bin/python scripts/run_daily_client_returns_digest.py --send   # email Anshul
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily client returns digest")
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually email anshul@equities4wealth.com (default: dry-run artifacts only)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit client count")
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Restrict to is_active clients",
    )
    parser.add_argument(
        "--client-id",
        type=int,
        action="append",
        dest="client_ids",
        help="Restrict to one or more client IDs (repeatable)",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="As-of date YYYY-MM-DD (default: today)",
    )
    args = parser.parse_args()

    as_of = None
    if args.as_of:
        from datetime import date as date_cls

        as_of = date_cls.fromisoformat(args.as_of)

    from main import create_app
    from services.daily_client_returns_digest_service import (
        send_daily_client_returns_digest,
    )

    app = create_app()
    with app.app_context():
        result = send_daily_client_returns_digest(
            dry_run=not args.send,
            as_of=as_of,
            active_only=args.active_only,
            client_ids=args.client_ids,
            limit=args.limit,
        )
    print(result)
    arts = result.get("artifacts") or {}
    for k, v in arts.items():
        print(f"  {k}: {v}")
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
