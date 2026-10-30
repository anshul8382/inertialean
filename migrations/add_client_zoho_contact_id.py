#!/usr/bin/env python3
"""Add client.zoho_contact_id for durable Zoho Books customer mapping.

Run: python3 migrations/add_client_zoho_contact_id.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import alter_table_add_column


def upgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            existing = {col["name"] for col in inspector.get_columns("client")}
            if "zoho_contact_id" in existing:
                print("→ zoho_contact_id already exists on client")
            else:
                alter_table_add_column(
                    db.session,
                    "client",
                    "zoho_contact_id VARCHAR(64) NULL",
                )
                print("✓ Added client.zoho_contact_id")
            # Optional index (idempotent-ish)
            try:
                db.session.execute(
                    db.text(
                        "CREATE INDEX ix_client_zoho_contact_id ON client (zoho_contact_id)"
                    )
                )
                print("✓ Index ix_client_zoho_contact_id")
            except Exception as idx_exc:
                print(f"→ index skipped/exists: {idx_exc}")
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
    print("Running client.zoho_contact_id migration...")
    ok = upgrade()
    sys.exit(0 if ok else 1)
