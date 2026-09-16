#!/usr/bin/env python3
"""
Migration: Add other_notes field to client table
Note: background_notes already exists in the database, and planning_synopsis already exists.
This migration only adds the other_notes field.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from sqlalchemy import text

def upgrade():
    """Add other_notes column to client table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if column already exists before adding
            inspector = db.inspect(db.engine)
            existing_columns = [col['name'] for col in inspector.get_columns('client')]
            
            if 'other_notes' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN other_notes TEXT NULL
                """))
                print("✓ Added other_notes column")
            else:
                print("→ other_notes column already exists")
            
            # Verify background_notes exists (it should, but check anyway)
            if 'background_notes' in existing_columns:
                print("→ background_notes column already exists")
            else:
                print("⚠ Warning: background_notes column not found, but it should exist")
            
            db.session.commit()
            print("\n✅ Migration completed successfully!")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Migration failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

def downgrade():
    """Remove other_notes column from client table"""
    app = create_app()
    
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            existing_columns = [col['name'] for col in inspector.get_columns('client')]
            
            if 'other_notes' in existing_columns:
                db.session.execute(text("ALTER TABLE client DROP COLUMN other_notes"))
                print("✓ Removed other_notes column")
            else:
                print("→ other_notes column does not exist")
            
            db.session.commit()
            print("\n✅ Rollback completed successfully!")
            return True
            
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Rollback failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'downgrade':
        print("Rolling back client notes fields migration...")
        downgrade()
    else:
        print("Running client notes fields migration...")
        upgrade()

