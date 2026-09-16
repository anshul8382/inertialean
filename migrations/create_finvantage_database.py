#!/usr/bin/env python3
"""
Migration: Create FinVantage tables in the separate finvantage database.
Requires: finvantage database must exist (run create_finvantage_db.sql first as root).
Creates: finvantage_user, finvantage_profile, finvantage_asset, finvantage_goal,
         finvantage_earmarking, finvantage_persona.

FinVantage writes to finvantage DB; reads inertia_app2025 when syncing existing clients.
Run: python migrations/create_finvantage_database.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import drop_table_if_exists

# Ensure FinVantage models are registered (for create_all)
import models.finvantage_models  # noqa: F401


def upgrade():
    """Create FinVantage tables in the finvantage database."""
    app = create_app()
    with app.app_context():
        try:
            # Create only tables bound to 'finvantage'
            db.create_all(bind_key='finvantage')
            print("✓ Created FinVantage tables in finvantage database")
            print("  (finvantage_user, finvantage_profile, finvantage_asset,")
            print("   finvantage_goal, finvantage_earmarking, finvantage_persona)")
            print("\n✅ FinVantage database setup complete!")
        except Exception as e:
            if "Unknown database" in str(e) or "1049" in str(e):
                print("\n❌ Error: finvantage database does not exist.")
                print("   Run as MySQL root: mysql -u root -p < migrations/create_finvantage_db.sql")
            raise


def downgrade():
    """Drop FinVantage tables from finvantage database."""
    app = create_app()
    with app.app_context():
        try:
            db.drop_all(bind_key='finvantage')
            print("✓ Dropped all FinVantage tables from finvantage database")
        except Exception as e:
            raise


def remove_from_inertia():
    """Remove finvantage_* tables from inertia_app2025 (if migrated from shared DB)."""
    app = create_app()
    with app.app_context():
        from sqlalchemy import text
        tables = ['finvantage_persona', 'finvantage_earmarking', 'finvantage_goal',
                  'finvantage_asset', 'finvantage_profile', 'finvantage_user']
        for t in tables:
            try:
                drop_table_if_exists(db.session, t)
                print(f"✓ Dropped {t} from inertia_app2025")
            except Exception as ex:
                print(f"  (skip {t}: {ex})")
        db.session.commit()
        print("\n✓ Cleaned finvantage tables from inertia_app2025")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'upgrade'
    if cmd == 'downgrade':
        downgrade()
    elif cmd == 'remove-from-inertia':
        remove_from_inertia()
    else:
        upgrade()
