#!/usr/bin/env python3
"""
Migration: Add finvantage_sync_block table (block re-sync after admin delete).
Run: python migrations/alter_finvantage_v7_sync_block.py
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
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS finvantage_sync_block (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        email VARCHAR(120) NOT NULL UNIQUE,
                        deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.commit()
            print("✓ Created finvantage_sync_block")
        except Exception as e:
            if "already exists" in str(e).lower() or "1050" in str(e):
                print("  (finvantage_sync_block already exists, skip)")
            else:
                raise
        print("\n✅ FinVantage v7 sync block migration complete!")


if __name__ == '__main__':
    upgrade()
