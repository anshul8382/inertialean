#!/usr/bin/env python3
"""
Migration script to add 2FA fields to User model
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import create_app
from extensions import db

def add_2fa_fields():
    """Add 2FA fields to User table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Add 2FA columns to user table
            db.session.execute(db.text("""
                ALTER TABLE user 
                ADD COLUMN two_factor_enabled BOOLEAN DEFAULT FALSE,
                ADD COLUMN two_factor_secret VARCHAR(32) NULL,
                ADD COLUMN two_factor_backup_codes TEXT NULL
            """))
            
            db.session.commit()
            print("✅ 2FA fields added successfully to User table")
            
        except Exception as e:
            print(f"❌ Error adding 2FA fields: {str(e)}")
            db.session.rollback()
            return False
    
    return True

if __name__ == "__main__":
    print("Adding 2FA fields to User model...")
    success = add_2fa_fields()
    if success:
        print("✅ Migration completed successfully!")
    else:
        print("❌ Migration failed!")
        sys.exit(1)
