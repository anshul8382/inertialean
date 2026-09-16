#!/usr/bin/env python3
"""
Migration: Add secondary_emails column to client table.

Stores comma-separated CC addresses (spouse, assistant, etc.) for recommendation emails.

Run on prod (before or immediately after deploying code that uses Client.secondary_emails):
    cd /opt/Inertia2026v1
    sudo -u inertia ./venv/bin/python3 migrations/add_client_secondary_emails.py

Verify:
    sudo -u inertia ./venv/bin/python3 migrations/add_client_secondary_emails.py --verify

Manual SQL (if Python script unavailable):
    ALTER TABLE client ADD COLUMN secondary_emails TEXT NULL;
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, text

from extensions import db
from main import create_app

COLUMN = "secondary_emails"
TABLE = "client"


def column_exists() -> bool:
    app = create_app()
    with app.app_context():
        cols = [c["name"] for c in inspect(db.engine).get_columns(TABLE)]
        return COLUMN in cols


def verify() -> bool:
    if not column_exists():
        print(f"❌ Missing column {TABLE}.{COLUMN}")
        return False
    print(f"✓ {TABLE}.{COLUMN} exists")
    return True


def upgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            existing_columns = [col["name"] for col in inspect(db.engine).get_columns(TABLE)]
            if COLUMN not in existing_columns:
                db.session.execute(
                    text(
                        f"""
                        ALTER TABLE {TABLE}
                        ADD COLUMN {COLUMN} TEXT NULL
                        """
                    )
                )
                db.session.commit()
                print(f"✓ Added {TABLE}.{COLUMN}")
            else:
                print(f"→ {TABLE}.{COLUMN} already exists")
            return verify()
        except Exception as exc:
            db.session.rollback()
            print(f"\n❌ Migration failed: {exc}")
            import traceback

            traceback.print_exc()
            return False


def downgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            from utils.sql_ddl import alter_table_drop_column

            existing_columns = [col["name"] for col in inspect(db.engine).get_columns(TABLE)]
            if COLUMN in existing_columns:
                alter_table_drop_column(db.session, TABLE, COLUMN)
                db.session.commit()
                print(f"✓ Removed {TABLE}.{COLUMN}")
            else:
                print(f"→ {TABLE}.{COLUMN} does not exist")
            return True
        except Exception as exc:
            db.session.rollback()
            print(f"\n❌ Rollback failed: {exc}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "upgrade").strip().lower()
    if cmd in ("verify", "check"):
        ok = verify()
        sys.exit(0 if ok else 1)
    if cmd == "downgrade":
        print(f"Rolling back {TABLE}.{COLUMN}...")
        sys.exit(0 if downgrade() else 1)
    print(f"Running {TABLE}.{COLUMN} migration...")
    sys.exit(0 if upgrade() else 1)
