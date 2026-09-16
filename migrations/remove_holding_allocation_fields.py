"""
Remove allocation fields from Holding model - they belong in allocation models, not holdings
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from __init__ import create_app
from extensions import db

def remove_holding_allocation_fields():
    """Remove allocation_percentage, min_allocation, max_allocation from holding table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if columns exist and remove them
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('holding')]
            
            columns_to_remove = ['allocation_percentage', 'min_allocation', 'max_allocation']
            
            for col in columns_to_remove:
                if col in columns:
                    db.session.execute(db.text(f"ALTER TABLE holding DROP COLUMN {col}"))
                    print(f"✅ Removed column: {col}")
                else:
                    print(f"ℹ️  Column {col} does not exist, skipping")
            
            db.session.commit()
            print("✅ Successfully processed allocation fields removal from holding table")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error removing allocation fields: {str(e)}")
            raise

if __name__ == "__main__":
    remove_holding_allocation_fields()

