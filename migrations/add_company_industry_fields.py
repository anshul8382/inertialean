#!/usr/bin/env python3
"""
Migration: Add company_name and industry fields to client table
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from utils.sql_ddl import alter_table_drop_column

def upgrade():
    """Add company_name and industry columns to client table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if columns already exist before adding
            inspector = db.inspect(db.engine)
            existing_columns = [col['name'] for col in inspector.get_columns('client')]
            
            if 'company_name' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN company_name VARCHAR(200) NULL
                """))
                print("✓ Added company_name column")
            else:
                print("→ company_name column already exists")
            
            if 'industry' not in existing_columns:
                db.session.execute(text("""
                    ALTER TABLE client 
                    ADD COLUMN industry VARCHAR(100) NULL
                """))
                print("✓ Added industry column")
            else:
                print("→ industry column already exists")
            
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
    """Remove company_name and industry columns from client table"""
    app = create_app()
    
    with app.app_context():
        try:
            inspector = db.inspect(db.engine)
            existing_columns = [col['name'] for col in inspector.get_columns('client')]
            
            columns_to_remove = ['company_name', 'industry']
            
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
        print("Rolling back company_name and industry fields migration...")
        downgrade()
    else:
        print("Running company_name and industry fields migration...")
        upgrade()

