#!/usr/bin/env python3
"""
Migration: Add current_allocation JSON column to finvantage_profile (Inertia allocation data).
Run: python migrations/alter_finvantage_v8_current_allocation.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db


def upgrade():
    app = create_app()
    with app.app_context():
        engine = db.get_engine(bind_key='finvantage')
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE finvantage_profile ADD COLUMN current_allocation JSON NULL"
                ))
                conn.commit()
            print("✓ Added current_allocation")
        except Exception as e:
            if "Duplicate column" in str(e) or "1060" in str(e):
                print("  (current_allocation already exists, skip)")
            else:
                raise
        print("\n✅ FinVantage v8 current_allocation migration complete!")


if __name__ == '__main__':
    upgrade()
