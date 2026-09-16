#!/usr/bin/env python3
"""
Migration: Add monthly_planned_investment to profile (Inertia SIP for planning).
Run: python migrations/alter_finvantage_v6_inertia_planning.py
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
                    "ALTER TABLE finvantage_profile ADD COLUMN monthly_planned_investment DECIMAL(15,2) NULL"
                ))
                conn.commit()
            print("✓ Added monthly_planned_investment")
        except Exception as e:
            if "Duplicate column" in str(e) or "1060" in str(e):
                print("  (monthly_planned_investment already exists, skip)")
            else:
                raise
        print("\n✅ FinVantage v6 Inertia planning migration complete!")


if __name__ == '__main__':
    upgrade()
