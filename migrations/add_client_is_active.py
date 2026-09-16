#!/usr/bin/env python3
"""
Migration: Add is_active flag to client table for marking clients active/inactive.
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text


def upgrade():
    """Add is_active column to client table (default True for existing rows)."""
    app = create_app()

    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            existing_columns = [col["name"] for col in inspector.get_columns("client")]

            if "is_active" not in existing_columns:
                db.session.execute(
                    text(
                        """
                        ALTER TABLE client
                        ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE
                        """
                    )
                )
                print("✓ Added is_active column to client")
            else:
                print("→ is_active column already exists on client")

            db.session.commit()
            print("\n✅ Migration completed successfully!")
            return True

        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Migration failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return False


def downgrade():
    """Remove is_active column from client table."""
    app = create_app()

    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            existing_columns = [col["name"] for col in inspector.get_columns("client")]

            if "is_active" in existing_columns:
                db.session.execute(text("ALTER TABLE client DROP COLUMN is_active"))
                print("✓ Removed is_active column from client")
            else:
                print("→ is_active column does not exist on client")

            db.session.commit()
            print("\n✅ Rollback completed successfully!")
            return True

        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Rollback failed: {str(e)}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "downgrade":
        print("Rolling back client is_active migration...")
        downgrade()
    else:
        print("Running client is_active migration...")
        upgrade()
