#!/usr/bin/env python3
"""
Migration script to add 2FA policy fields to User model
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import create_app
from extensions import db

def add_2fa_policy_fields():
    """Add 2FA policy fields to User table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Add 2FA policy columns to user table
            db.session.execute(db.text("""
                ALTER TABLE user 
                ADD COLUMN two_factor_required BOOLEAN DEFAULT FALSE,
                ADD COLUMN two_factor_setup_reminder DATETIME NULL
            """))
            
            db.session.commit()
            print("✅ 2FA policy fields added successfully to User table")
            
        except Exception as e:
            print(f"❌ Error adding 2FA policy fields: {str(e)}")
            db.session.rollback()
            return False
    
    return True

if __name__ == "__main__":
    print("Adding 2FA policy fields to User model...")
    success = add_2fa_policy_fields()
    if success:
        print("✅ Migration completed successfully!")
    else:
        print("❌ Migration failed!")
        sys.exit(1)
