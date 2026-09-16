#!/usr/bin/env python3
"""
Migration: Rename account head types from income/expense to credit/debit.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text


def upgrade():
    """Update head_type values: income -> credit, expense -> debit."""
    app = create_app()
    with app.app_context():
        try:
            db.session.execute(text("UPDATE account_head SET head_type = 'credit' WHERE head_type = 'income'"))
            db.session.execute(text("UPDATE account_head SET head_type = 'debit' WHERE head_type = 'expense'"))
            db.session.commit()
            print("✓ Renamed income -> credit, expense -> debit")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"Migration failed: {e}")
            return False


def downgrade():
    """Revert: credit -> income, debit -> expense."""
    app = create_app()
    with app.app_context():
        try:
            db.session.execute(text("UPDATE account_head SET head_type = 'income' WHERE head_type = 'credit'"))
            db.session.execute(text("UPDATE account_head SET head_type = 'expense' WHERE head_type = 'debit'"))
            db.session.commit()
            print("✓ Reverted to income/expense")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"Rollback failed: {e}")
            return False


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'downgrade':
        downgrade()
    else:
        upgrade()
