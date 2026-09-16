#!/usr/bin/env python3
"""
Migration: Add budget_opt_in and insurance_opt_in to profile (setup preferences).
Run: python migrations/alter_finvantage_v5_preferences.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import alter_table_add_column


def upgrade():
    app = create_app()
    with app.app_context():
        engine = db.get_engine(bind_key='finvantage')
        from sqlalchemy import text
        for col in ['budget_opt_in', 'insurance_opt_in']:
            try:
                with engine.connect() as conn:
                    alter_table_add_column(
                        conn,
                        "finvantage_profile",
                        f"{col} TINYINT(1) NULL",
                    )
                    conn.commit()
                print(f"✓ Added {col}")
            except Exception as e:
                if "Duplicate column" in str(e) or "1060" in str(e):
                    print(f"  ({col} already exists, skip)")
                else:
                    raise
        print("\n✅ FinVantage v5 preferences migration complete!")


if __name__ == '__main__':
    upgrade()
