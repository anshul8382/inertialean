#!/usr/bin/env python3
"""
Migration: Add product_url column for direct purchase links.
Run: python migrations/alter_finvantage_v4_product_url.py
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
                    "ALTER TABLE finvantage_product ADD COLUMN product_url VARCHAR(500) NULL AFTER max_coverage"
                ))
                conn.commit()
            print("✓ Added product_url column")
        except Exception as e:
            if "Duplicate column" in str(e) or "1060" in str(e):
                print("  (product_url already exists, skip)")
            else:
                raise
        print("\n✅ FinVantage v4 product_url migration complete!")


if __name__ == '__main__':
    upgrade()
