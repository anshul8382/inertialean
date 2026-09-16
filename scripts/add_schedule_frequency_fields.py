#!/usr/bin/env python3
"""
Add frequency and flexible amount fields to monthly_investment_schedule table
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from extensions import db
from sqlalchemy import text
import config

def migrate():
    app = Flask(__name__)
    app.config.from_object(config.Config)
    db.init_app(app)
    with app.app_context():
        try:
            conn = db.engine.connect()
            trans = conn.begin()
            
            # Check if columns already exist
            result = conn.execute(text("SHOW COLUMNS FROM monthly_investment_schedule LIKE 'change_frequency_months'"))
            if result.fetchone():
                print("Columns already exist, skipping migration")
                trans.rollback()
                conn.close()
                return
            
            # Add new columns
            print("Adding change_frequency_months column...")
            conn.execute(text("ALTER TABLE monthly_investment_schedule ADD COLUMN change_frequency_months INT NOT NULL DEFAULT 1"))
            
            print("Adding withdrawal_mode column...")
            conn.execute(text("ALTER TABLE monthly_investment_schedule ADD COLUMN withdrawal_mode VARCHAR(20) NOT NULL DEFAULT 'AD_HOC'"))
            
            print("Adding withdrawal_frequency_months column...")
            conn.execute(text("ALTER TABLE monthly_investment_schedule ADD COLUMN withdrawal_frequency_months INT NOT NULL DEFAULT 0"))
            
            print("Adding allow_zero_amount column...")
            conn.execute(text("ALTER TABLE monthly_investment_schedule ADD COLUMN allow_zero_amount BOOLEAN NOT NULL DEFAULT TRUE"))
            
            print("Adding allow_negative_amount column...")
            conn.execute(text("ALTER TABLE monthly_investment_schedule ADD COLUMN allow_negative_amount BOOLEAN NOT NULL DEFAULT TRUE"))
            
            trans.commit()
            conn.close()
            print("Migration completed successfully!")
            
        except Exception as e:
            print(f"Error during migration: {str(e)}")
            trans.rollback()
            conn.close()
            raise

if __name__ == '__main__':
    migrate()

