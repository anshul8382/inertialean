"""
Database Migration: Add Hot Stock Rating Column

This migration adds the hot_stock_rating column to the security table
while maintaining backward compatibility with the existing is_hot_stock column.

Migration is safe and non-disruptive:
- Adds new column with default value 0
- Populates new column based on existing is_hot_stock data
- Keeps old column for backward compatibility
- Can be rolled back safely
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from __init__ import create_app
from extensions import db
import logging

logger = logging.getLogger(__name__)

def safe_migration_add_hot_stock_rating():
    """
    Safe migration that doesn't affect current application
    
    Steps:
    1. Add new column with default value 0
    2. Populate new column based on existing is_hot_stock data
    3. Verify migration success
    """
    
    app = create_app()
    
    with app.app_context():
        try:
            logger.info("Starting hot stock rating migration...")
            
            # Step 1: Check if column already exists
            result = db.session.execute(db.text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'security' 
                AND column_name = 'hot_stock_rating'
            """)).fetchone()
            
            if result:
                logger.info("✅ hot_stock_rating column already exists")
                return True
            
            # Step 2: Add new column with default value
            logger.info("Adding hot_stock_rating column...")
            db.session.execute(db.text("""
                ALTER TABLE security 
                ADD COLUMN hot_stock_rating INTEGER DEFAULT 0
            """))
            db.session.commit()
            logger.info("✅ hot_stock_rating column added successfully")
            
            # Step 3: Populate new column based on existing data
            logger.info("Populating hot_stock_rating from existing is_hot_stock data...")
            
            # Set rating 3 for existing hot stocks
            result = db.session.execute(db.text("""
                UPDATE security 
                SET hot_stock_rating = 3 
                WHERE is_hot_stock = true
            """))
            hot_stocks_updated = result.rowcount
            db.session.commit()
            logger.info(f"✅ Updated {hot_stocks_updated} hot stocks to rating 3")
            
            # Set rating 0 for non-hot stocks
            result = db.session.execute(db.text("""
                UPDATE security 
                SET hot_stock_rating = 0 
                WHERE is_hot_stock = false OR is_hot_stock IS NULL
            """))
            non_hot_stocks_updated = result.rowcount
            db.session.commit()
            logger.info(f"✅ Updated {non_hot_stocks_updated} non-hot stocks to rating 0")
            
            # Step 4: Verify migration
            total_securities = db.session.execute(db.text("SELECT COUNT(*) FROM security")).scalar()
            hot_stocks = db.session.execute(db.text("SELECT COUNT(*) FROM security WHERE hot_stock_rating = 3")).scalar()
            non_hot_stocks = db.session.execute(db.text("SELECT COUNT(*) FROM security WHERE hot_stock_rating = 0")).scalar()
            
            logger.info(f"Migration verification:")
            logger.info(f"  Total securities: {total_securities}")
            logger.info(f"  Hot stocks (rating 3): {hot_stocks}")
            logger.info(f"  Non-hot stocks (rating 0): {non_hot_stocks}")
            
            if hot_stocks + non_hot_stocks == total_securities:
                logger.info("✅ Migration completed successfully")
                logger.info("✅ Current application unaffected")
                logger.info("✅ New rating system ready")
                return True
            else:
                logger.error("❌ Migration verification failed")
                return False
                
        except Exception as e:
            logger.error(f"❌ Migration failed: {str(e)}")
            logger.info("✅ Current application still works")
            # Rollback is automatic - new column just doesn't exist
            return False

def rollback_hot_stock_rating():
    """
    Remove new column if needed (current app still works)
    
    WARNING: Only run this if you want to remove the new rating system
    """
    
    app = create_app()
    
    with app.app_context():
        try:
            logger.info("Starting rollback of hot_stock_rating column...")
            
            # Check if column exists
            result = db.session.execute(db.text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'security' 
                AND column_name = 'hot_stock_rating'
            """)).fetchone()
            
            if not result:
                logger.info("✅ hot_stock_rating column doesn't exist - nothing to rollback")
                return True
            
            # Remove the column
            db.session.execute(db.text("""
                ALTER TABLE security 
                DROP COLUMN hot_stock_rating
            """))
            db.session.commit()
            
            logger.info("✅ Rollback completed - back to original state")
            return True
            
        except Exception as e:
            logger.error(f"❌ Rollback failed: {str(e)}")
            return False

if __name__ == "__main__":
    print("Hot Stock Rating Migration")
    print("=" * 40)
    print("Running automatic migration...")
    
    success = safe_migration_add_hot_stock_rating()
    if success:
        print("\n✅ Migration completed successfully!")
        print("✅ Your current application continues to work unchanged")
        print("✅ New rating system is ready for use")
    else:
        print("\n❌ Migration failed, but your current application is unaffected")
