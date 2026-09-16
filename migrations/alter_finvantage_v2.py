#!/usr/bin/env python3
"""
Migration: Add health_insurance_coverage, waterfall_order to profile; create finvantage_savings_record.
Run: python migrations/alter_finvantage_v2.py
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text


def upgrade():
    app = create_app()
    with app.app_context():
        engine = db.get_engine(bind_key='finvantage')
        for stmt in [
            "ALTER TABLE finvantage_profile ADD COLUMN health_insurance_coverage DECIMAL(15,2) DEFAULT 0",
            "ALTER TABLE finvantage_profile ADD COLUMN waterfall_order JSON DEFAULT NULL",
            "ALTER TABLE finvantage_profile ADD COLUMN life_stage VARCHAR(30) DEFAULT 'married'",
            "ALTER TABLE finvantage_profile ADD COLUMN income_type VARCHAR(20) DEFAULT 'stable'",
        ]:
            try:
                with engine.connect() as conn:
                    conn.execute(text(stmt))
                    conn.commit()
                print(f"✓ Added column")
            except Exception as e:
                if "Duplicate column" in str(e) or "1060" in str(e):
                    print(f"  (skip, column exists)")
                else:
                    raise
        for stmt in [
            "ALTER TABLE finvantage_goal ADD COLUMN is_recurring TINYINT(1) DEFAULT 0",
            "ALTER TABLE finvantage_goal ADD COLUMN frequency VARCHAR(20) DEFAULT NULL",
        ]:
            try:
                with engine.connect() as conn:
                    conn.execute(text(stmt))
                    conn.commit()
                print(f"✓ Added goal column")
            except Exception as e:
                if "Duplicate column" in str(e) or "1060" in str(e):
                    print(f"  (skip, goal column exists)")
                else:
                    raise
        try:
            with engine.connect() as conn:
                conn.execute(text("""
                CREATE TABLE IF NOT EXISTS finvantage_savings_record (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    `year_month` VARCHAR(7) NOT NULL,
                    planned_savings DECIMAL(15,2) DEFAULT 0,
                    actual_savings DECIMAL(15,2) DEFAULT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    CONSTRAINT fk_savings_user FOREIGN KEY (user_id) REFERENCES finvantage_user(id) ON DELETE CASCADE,
                    CONSTRAINT uq_user_month UNIQUE (user_id, `year_month`)
                )
            """))
                conn.commit()
            print("✓ Created finvantage_savings_record")
        except Exception as e:
            if "1050" in str(e):
                print("  (finvantage_savings_record exists)")
            else:
                raise
        print("\n✅ FinVantage v2 migration complete!")


if __name__ == '__main__':
    upgrade()
