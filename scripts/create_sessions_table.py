#!/usr/bin/env python3
"""
Create the sessions table for Flask-Session if it doesn't exist
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from __init__ import create_app
from extensions import db
from flask_session import Session

def create_sessions_table():
    """Create sessions table for Flask-Session"""
    app = create_app()
    
    with app.app_context():
        # Check if sessions table exists
        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()
        
        if 'sessions' in tables:
            print("✓ Sessions table already exists")
            return
        
        # Create sessions table
        print("Creating sessions table...")
        
        # Flask-Session creates the table automatically when Session(app) is called
        # But we can also create it manually using SQL
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS `sessions` (
            `id` VARCHAR(255) NOT NULL PRIMARY KEY,
            `data` BLOB NOT NULL,
            `expiry` DATETIME NOT NULL,
            INDEX `expiry_idx` (`expiry`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        
        try:
            db.session.execute(db.text(create_table_sql))
            db.session.commit()
            print("✓ Sessions table created successfully")
        except Exception as e:
            print(f"✗ Error creating sessions table: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    create_sessions_table()


