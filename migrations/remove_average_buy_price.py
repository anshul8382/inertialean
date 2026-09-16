"""
Remove redundant average_buy_price field from Holding model
Both average_buy_price and average_price were storing the same data
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from __init__ import create_app
from extensions import db

def remove_average_buy_price():
    """Remove average_buy_price column from holding table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if column exists and remove it
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('holding')]
            
            if 'average_buy_price' in columns:
                db.session.execute(db.text("ALTER TABLE holding DROP COLUMN average_buy_price"))
                print("✅ Removed column: average_buy_price")
            else:
                print("ℹ️  Column average_buy_price does not exist, skipping")
            
            db.session.commit()
            print("✅ Successfully processed average_buy_price removal from holding table")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error removing average_buy_price: {str(e)}")
            raise

if __name__ == "__main__":
    remove_average_buy_price()

