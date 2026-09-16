#!/usr/bin/env python3
"""
Migration script to add notes_history field to alert table
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from main import create_app
from models import db
from sqlalchemy import text

def add_notes_history_column():
    """Add notes_history column to alert table"""
    app = create_app()
    with app.app_context():
        try:
            # Check if column already exists
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('alert')]
            
            if 'notes_history' in columns:
                print("Column 'notes_history' already exists. Skipping migration.")
                return
            
            # Add the column
            print("Adding notes_history column to alert table...")
            db.session.execute(text("""
                ALTER TABLE alert 
                ADD COLUMN notes_history TEXT NULL
            """))
            db.session.commit()
            print("✓ Successfully added notes_history column to alert table")
            
        except Exception as e:
            print(f"✗ Error adding notes_history column: {e}")
            db.session.rollback()
            raise

if __name__ == '__main__':
    add_notes_history_column()

