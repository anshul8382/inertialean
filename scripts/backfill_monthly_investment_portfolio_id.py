#!/usr/bin/env python3
"""
Backfill ``monthly_investment.portfolio_id`` where it is NULL.

Uses the same resolution rules as the monthly investment UI (active portfolio,
else first portfolio, else create a default portfolio).

Usage (from repo root, with app env / .env loaded as usual):

    python scripts/backfill_monthly_investment_portfolio_id.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import create_app
from models import User
from services.monthly_investment_portfolio_service import (
    backfill_null_monthly_investment_portfolio_ids,
)


def main() -> None:
    app = create_app()
    with app.app_context():
        admin = User.query.first()
        user_id = admin.id if admin else 1
        result = backfill_null_monthly_investment_portfolio_ids(user_id)
        print("Backfill monthly_investment.portfolio_id")
        print(f"  scanned: {result['scanned']}")
        print(f"  updated: {result['updated']}")
        if result.get("errors"):
            print(f"  errors ({len(result['errors'])}):")
            for err in result["errors"][:25]:
                print(f"    - {err}")
            if len(result["errors"]) > 25:
                print("    ...")


if __name__ == "__main__":
    main()
