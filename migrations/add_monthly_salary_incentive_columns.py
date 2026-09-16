#!/usr/bin/env python3
"""
Migration: Add sales_incentive and internal_incentive to monthly_salary.

Additive, idempotent. Required before admin salary detail (base + incentives) on prod.

Run on prod (same deploy window as attendance salary detail code):
    cd /opt/Inertia2026v1
    bash scripts/deployment/run_monthly_salary_incentive_migration.sh
    # or:
    sudo -u inertia ./venv/bin/python3 migrations/add_monthly_salary_incentive_columns.py
    sudo -u inertia ./venv/bin/python3 migrations/add_monthly_salary_incentive_columns.py --verify

Manual SQL:
    ALTER TABLE monthly_salary ADD COLUMN sales_incentive NUMERIC(10, 2) DEFAULT 0.00;
    ALTER TABLE monthly_salary ADD COLUMN internal_incentive NUMERIC(10, 2) DEFAULT 0.00;
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from extensions import db
from main import create_app

TABLE = "monthly_salary"
COLUMNS = ("sales_incentive", "internal_incentive")


def _columns() -> set[str]:
    inspector = inspect(db.engine)
    if TABLE not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(TABLE)}


def _verify_in_context() -> bool:
    existing = _columns()
    if TABLE not in inspect(db.engine).get_table_names():
        print(f"❌ Missing table {TABLE}")
        return False
    ok = True
    for col in COLUMNS:
        if col in existing:
            print(f"✓ {TABLE}.{col} exists")
        else:
            print(f"❌ Missing column {TABLE}.{col}")
            ok = False
    return ok


def verify() -> bool:
    app = create_app()
    with app.app_context():
        return _verify_in_context()


def upgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            if TABLE not in inspect(db.engine).get_table_names():
                print(f"❌ Missing table {TABLE} — cannot add incentive columns")
                return False

            existing = _columns()
            for col in COLUMNS:
                if col not in existing:
                    db.session.execute(
                        text(
                            f"ALTER TABLE {TABLE} "
                            f"ADD COLUMN {col} NUMERIC(10, 2) DEFAULT 0.00"
                        )
                    )
                    print(f"✓ Added {TABLE}.{col}")
                else:
                    print(f"→ {TABLE}.{col} already exists")

            db.session.commit()
            return _verify_in_context()
        except Exception as exc:
            db.session.rollback()
            print(f"\n❌ Migration failed: {exc}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    raw = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").strip().lower()
    cmd = raw.lstrip("-")
    if cmd in ("verify", "check"):
        sys.exit(0 if verify() else 1)
    print(f"Running {TABLE} incentive columns migration...")
    sys.exit(0 if upgrade() else 1)
