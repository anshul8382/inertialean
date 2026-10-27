#!/usr/bin/env python3
"""
Manual / ops runner for portfolio ↔ Prices sheet audit.

  python3 scripts/run_prices_sheet_portfolio_audit.py
  python3 scripts/run_prices_sheet_portfolio_audit.py --force-email
  python3 scripts/run_prices_sheet_portfolio_audit.py --no-email
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit held stocks vs Prices sheet")
    ap.add_argument("--force-email", action="store_true", help="Send digests even if already emailed today / not close")
    ap.add_argument("--no-email", action="store_true", help="Update streaks only; never send mail")
    args = ap.parse_args()

    from main import create_app
    from services.price_accuracy_prices_sheet_audit_service import (
        run_prices_sheet_portfolio_audit,
    )

    app = create_app()
    with app.app_context():
        send = False if args.no_email else True
        summary = run_prices_sheet_portfolio_audit(
            send_email=send,
            force_email=bool(args.force_email) and not args.no_email,
        )
        # Drop bulky findings list for CLI readability unless empty/small
        printable = dict(summary)
        findings = printable.get("findings") or []
        if len(findings) > 40:
            printable["findings"] = findings[:40]
            printable["findings_truncated"] = True
        print(json.dumps(printable, indent=2, default=str))
        return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
