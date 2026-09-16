#!/usr/bin/env python3
"""
Local / pre-prod smoke for cashflow ↔ trade nightly badge scan.

Prereq: MySQL reachable per .env (often SSH tunnel 127.0.0.1:3307).

Examples:
  python3 scripts/run_cashflow_trade_nightly.py
  python3 scripts/run_cashflow_trade_nightly.py --client-id 155 --print-summary
  python3 scripts/run_cashflow_trade_nightly.py --print-badge 155
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
    ap.add_argument("--client-id", type=int, action="append", dest="client_ids")
    ap.add_argument("--include-inactive", action="store_true")
    ap.add_argument("--print-summary", action="store_true")
    ap.add_argument("--print-badge", type=int, metavar="CLIENT_ID")
    args = ap.parse_args()

    from main import create_app
    from services.cashflow_trade_integrity_case_service import get_client_cashflow_trade_badge
    from services.cashflow_trade_nightly_service import run_cashflow_trade_nightly

    app = create_app()
    with app.app_context():
        summary = run_cashflow_trade_nightly(
            active_only=not args.include_inactive,
            client_ids=args.client_ids,
        )
        if args.print_summary or not args.print_badge:
            print(json.dumps(summary, indent=2, default=str))
        if args.print_badge:
            badge = get_client_cashflow_trade_badge(args.print_badge)
            print(json.dumps(badge, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
