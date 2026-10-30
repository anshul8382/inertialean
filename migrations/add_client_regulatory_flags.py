#!/usr/bin/env python3
"""Add SEBI IA classification flags on client.

Run: python3 migrations/add_client_regulatory_flags.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import alter_table_add_column


COLUMNS = [
    ("is_nri", "TINYINT(1) NOT NULL DEFAULT 0"),
    ("is_fpi", "TINYINT(1) NOT NULL DEFAULT 0"),
    ("client_entity_type", "VARCHAR(32) NOT NULL DEFAULT 'individual'"),
    ("is_accredited_investor", "TINYINT(1) NOT NULL DEFAULT 0"),
    ("inactive_since", "DATE NULL"),
]


def upgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            existing = {col["name"] for col in inspector.get_columns("client")}
            for name, definition in COLUMNS:
                if name in existing:
                    print(f"→ {name} already exists on client")
                    continue
                alter_table_add_column(db.session, "client", f"{name} {definition}")
                print(f"✓ Added client.{name}")
            db.session.commit()
            print("\n✅ Migration completed successfully!")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Migration failed: {e}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    print("Running client regulatory flags migration...")
    ok = upgrade()
    sys.exit(0 if ok else 1)
