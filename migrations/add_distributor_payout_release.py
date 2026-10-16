#!/usr/bin/env python3
"""
Additive: payout release tracking on distributor_invoice_attribution
+ distributor_payout_release batch table.

  python3 migrations/add_distributor_payout_release.py
  python3 migrations/add_distributor_payout_release.py --verify
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from extensions import db
from main import create_app

ATTR_COLS = {
    "payout_status": "VARCHAR(20) NOT NULL DEFAULT 'pending'",
    "released_at": "DATETIME NULL",
    "released_by_id": "INT NULL",
    "clawback_amount": "NUMERIC(12, 2) NOT NULL DEFAULT 0",
}


def _table_names() -> set[str]:
    return set(inspect(db.engine).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _table_names():
        return set()
    return {c["name"] for c in inspect(db.engine).get_columns(table)}


def upgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            if "distributor_invoice_attribution" not in _table_names():
                print("❌ Run add_distributor_support.py first")
                return False

            cols = _columns("distributor_invoice_attribution")
            for name, ddl in ATTR_COLS.items():
                if name not in cols:
                    db.session.execute(
                        text(
                            f"ALTER TABLE `distributor_invoice_attribution` "
                            f"ADD COLUMN `{name}` {ddl}"
                        )
                    )
                    print(f"✓ Added distributor_invoice_attribution.{name}")
                else:
                    print(f"✓ distributor_invoice_attribution.{name} exists")

            # Existing active rows without payout_status stay pending via DEFAULT;
            # normalize any NULL if column was added differently
            try:
                db.session.execute(
                    text(
                        "UPDATE distributor_invoice_attribution "
                        "SET payout_status = 'pending' "
                        "WHERE payout_status IS NULL OR payout_status = ''"
                    )
                )
            except Exception:
                pass

            if "distributor_payout_release" not in _table_names():
                db.session.execute(
                    text(
                        """
                        CREATE TABLE distributor_payout_release (
                            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                            distributor_id INT NOT NULL,
                            year INT NOT NULL,
                            month INT NOT NULL,
                            row_count INT NOT NULL DEFAULT 0,
                            total_payout NUMERIC(12, 2) NOT NULL DEFAULT 0,
                            released_at DATETIME NULL,
                            released_by_id INT NULL,
                            notes TEXT NULL,
                            created_at DATETIME NULL,
                            UNIQUE KEY uq_dist_payout_release_period (distributor_id, year, month),
                            KEY idx_dist_payout_release_dist (distributor_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                )
                print("✓ Created distributor_payout_release")
            else:
                print("✓ distributor_payout_release exists")

            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Migration failed: {e}")
            return False


def verify() -> bool:
    app = create_app()
    with app.app_context():
        ok = True
        cols = _columns("distributor_invoice_attribution")
        for name in ATTR_COLS:
            if name not in cols:
                print(f"❌ Missing distributor_invoice_attribution.{name}")
                ok = False
            else:
                print(f"✓ distributor_invoice_attribution.{name}")
        if "distributor_payout_release" not in _table_names():
            print("❌ Missing distributor_payout_release")
            ok = False
        else:
            print("✓ distributor_payout_release")
        return ok


if __name__ == "__main__":
    if "--verify" in sys.argv:
        raise SystemExit(0 if verify() else 1)
    raise SystemExit(0 if upgrade() else 1)
