#!/usr/bin/env python3
"""Add portfolio_discussion_state for Weekly Portfolio Discussion snooze/reviewed tracking.

Run: python3 migrations/add_portfolio_discussion_state.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from main import create_app
from extensions import db


def upgrade() -> bool:
    app = create_app()
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            if inspector.has_table("portfolio_discussion_state"):
                print("→ portfolio_discussion_state already exists")
            else:
                db.session.execute(
                    text(
                        """
                    CREATE TABLE portfolio_discussion_state (
                        client_id INT NOT NULL,
                        last_reviewed_at DATETIME NULL,
                        hide_until DATE NULL,
                        reviewed_by INT NULL,
                        last_review_note TEXT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        PRIMARY KEY (client_id),
                        CONSTRAINT fk_pds_client FOREIGN KEY (client_id) REFERENCES client(id),
                        CONSTRAINT fk_pds_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES user(id),
                        INDEX ix_pds_hide_until (hide_until)
                    )
                    """
                    )
                )
                print("✓ Created portfolio_discussion_state")
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
    print("Running portfolio_discussion_state migration...")
    ok = upgrade()
    sys.exit(0 if ok else 1)
