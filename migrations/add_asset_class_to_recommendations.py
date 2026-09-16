#!/usr/bin/env python3
"""
Migration: Add asset_class_id to Recommendation and create AssetClassDistribution table
TEST ENVIRONMENT ONLY - DO NOT MIGRATE TO PROD
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extensions import db
from sqlalchemy import text

def migrate():
    """Add asset_class_id to Recommendation and create AssetClassDistribution table"""
    try:
        # 1. Add asset_class_id column to recommendation table
        print("Adding asset_class_id column to recommendation table...")
        try:
            db.session.execute(text("""
                ALTER TABLE recommendation 
                ADD COLUMN asset_class_id INT NULL AFTER session_id
            """))
            db.session.commit()
            print("✓ Added asset_class_id column")
        except Exception as e:
            db.session.rollback()
            if "Duplicate column name" in str(e) or "already exists" in str(e).lower():
                print("⚠ asset_class_id column already exists, skipping...")
            else:
                raise
        
        # 2. Add foreign key constraint
        print("Adding foreign key constraint...")
        try:
            db.session.execute(text("""
                ALTER TABLE recommendation 
                ADD CONSTRAINT fk_recommendation_asset_class 
                FOREIGN KEY (asset_class_id) REFERENCES asset_class(id)
            """))
            db.session.commit()
            print("✓ Added foreign key constraint")
        except Exception as e:
            db.session.rollback()
            if "Duplicate key name" in str(e) or "already exists" in str(e).lower():
                print("⚠ Foreign key constraint might already exist, skipping...")
            else:
                raise
        
        # 3. Populate asset_class_id from security relationship
        print("Populating asset_class_id from security relationship...")
        result = db.session.execute(text("""
            UPDATE recommendation r
            JOIN security s ON r.security_id = s.id
            SET r.asset_class_id = s.asset_class_id
            WHERE r.asset_class_id IS NULL
        """))
        db.session.commit()
        print(f"✓ Updated {result.rowcount} recommendations with asset_class_id")
        
        # 4. Create asset_class_distribution table
        print("Creating asset_class_distribution table...")
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS asset_class_distribution (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    session_id INT NOT NULL,
                    asset_class_id INT NOT NULL,
                    target_weight DECIMAL(5,2),
                    current_weight DECIMAL(5,2),
                    allocated_amount DECIMAL(15,2),
                    required_change DECIMAL(15,2),
                    security_count INT DEFAULT 0,
                    total_recommended_amount DECIMAL(15,2) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NULL ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES recommendation_session(id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_class_id) REFERENCES asset_class(id),
                    UNIQUE KEY uq_session_asset_class (session_id, asset_class_id)
                )
            """))
            db.session.commit()
            print("✓ Created asset_class_distribution table")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e).lower():
                print("⚠ asset_class_distribution table might already exist, skipping...")
            else:
                raise
        
        print("\n✅ Migration completed successfully!")
        
    except Exception as e:
        db.session.rollback()
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == '__main__':
    from main import create_app
    import os
    
    # Use TestConfig if FLASK_ENV is set to test
    if os.environ.get('FLASK_ENV') == 'test':
        from config import TestConfig
        app = create_app(TestConfig)
        print("Running migration on TEST database (inertia_app2025_test)")
    else:
        app = create_app()
        print("Running migration on default database")
    
    with app.app_context():
        from extensions import db
        from sqlalchemy import text
        # Verify which database we're using
        result = db.session.execute(text("SELECT DATABASE()"))
        db_name = result.scalar()
        print(f"Connected to database: {db_name}")
        migrate()

