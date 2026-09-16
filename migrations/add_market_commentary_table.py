#!/usr/bin/env python3
"""
Migration: Add market_commentary table for Period Analysis V2

This migration creates the market_commentary table to store shared market commentary
that appears in all Period Analysis V2 emails.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text

def upgrade():
    """Create market_commentary table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if table already exists
            inspector = db.inspect(db.engine)
            existing_tables = inspector.get_table_names()
            
            if 'market_commentary' in existing_tables:
                print("→ market_commentary table already exists")
                return
            
            db.session.execute(text("""
                CREATE TABLE market_commentary (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    content TEXT NOT NULL,
                    is_ai_generated BOOLEAN DEFAULT TRUE,
                    period_start_date DATE NULL,
                    period_end_date DATE NULL,
                    version INT DEFAULT 1,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    created_by INT NOT NULL,
                    updated_by INT NULL,
                    FOREIGN KEY (created_by) REFERENCES user(id),
                    FOREIGN KEY (updated_by) REFERENCES user(id),
                    INDEX idx_is_active (is_active),
                    INDEX idx_updated_at (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """))
            db.session.commit()
            print("✓ Created market_commentary table")
        except Exception as e:
            db.session.rollback()
            print(f"✗ Error creating market_commentary table: {e}")
            raise

def downgrade():
    """Drop market_commentary table"""
    app = create_app()
    
    with app.app_context():
        try:
            db.session.execute(text("DROP TABLE IF EXISTS market_commentary;"))
            db.session.commit()
            print("✓ Dropped market_commentary table")
        except Exception as e:
            db.session.rollback()
            print(f"✗ Error dropping market_commentary table: {e}")
            raise

if __name__ == '__main__':
    upgrade()
    print("Migration completed successfully!")

