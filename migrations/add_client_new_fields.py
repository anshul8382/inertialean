#!/usr/bin/env python3
"""
Migration: Add new fields to client table
- starting_aua
- designation
- linkedin_profile_url
- date_of_joining
- type_of_engagement
- portfolio_inherited
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import alter_table_drop_column

def upgrade():
    """Add new columns to client table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if columns already exist before adding
            inspector = db.inspect(db.engine)
            existing_columns = [col['name'] for col in inspector.get_columns('client')]
            
            if 'starting_aua' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN starting_aua DECIMAL(15, 2) DEFAULT 0
                """))
                print("✓ Added starting_aua column")
            else:
                print("→ starting_aua column already exists")
            
            if 'designation' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN designation VARCHAR(100) NULL
                """))
                print("✓ Added designation column")
            else:
                print("→ designation column already exists")
            
            if 'linkedin_profile_url' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN linkedin_profile_url VARCHAR(500) NULL
                """))
                print("✓ Added linkedin_profile_url column")
            else:
                print("→ linkedin_profile_url column already exists")
            
            if 'date_of_joining' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN date_of_joining DATE NULL
                """))
                print("✓ Added date_of_joining column")
            else:
                print("→ date_of_joining column already exists")
            
            if 'type_of_engagement' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN type_of_engagement VARCHAR(200) NULL
                """))
                print("✓ Added type_of_engagement column")
            else:
                print("→ type_of_engagement column already exists")
            
            if 'portfolio_inherited' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN portfolio_inherited BOOLEAN DEFAULT FALSE
                """))
                print("✓ Added portfolio_inherited column")
            else:
                print("→ portfolio_inherited column already exists")
            
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
    """Remove new columns from client table"""
    app = create_app()
    
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            existing_columns = [col['name'] for col in inspector.get_columns('client')]
            
            columns_to_remove = [
                'starting_aua',
                'designation',
                'linkedin_profile_url',
                'date_of_joining',
                'type_of_engagement',
                'portfolio_inherited'
            ]
            
            for column in columns_to_remove:
                if column in existing_columns:
                    alter_table_drop_column(db.session, "client", column)
                    print(f"✓ Removed {column} column")
                else:
                    print(f"→ {column} column does not exist")
            
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
        print("Rolling back client new fields migration...")
        downgrade()
    else:
        print("Running client new fields migration...")
        upgrade()
























