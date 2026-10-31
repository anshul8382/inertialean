#!/usr/bin/env python3
"""Backfill distributor_invoice_attribution for paid invoices missing a row.

Usage:
  python3 scripts/backfill_distributor_attributions.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from services.distributor_service import backfill_missing_attributions


def main() -> int:
    app = create_app()
    with app.app_context():
        result = backfill_missing_attributions(commit=True)
        print(result)
        return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
