#!/usr/bin/env python3
"""
Apply schedule_notes and model_assignment_security_model migrations
Can be run directly or via deployment script
"""

import sys
import os
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from main import create_app
from extensions import db
from sqlalchemy import text

def apply_migrations():
    """Apply the schedule_notes and model_assignment_security_model migrations"""
    app = create_app()
    
    with app.app_context():
        print("=" * 60)
        print("Applying Database Migrations")
        print("=" * 60)
        
        migrations_applied = []
        
        # Migration 1: Add schedule_notes column
        try:
            result = db.session.execute(text("SHOW COLUMNS FROM workflow LIKE 'schedule_notes'"))
            if not result.fetchone():
                print("\n[1/2] Adding schedule_notes column to workflow table...")
                db.session.execute(text("ALTER TABLE workflow ADD COLUMN schedule_notes TEXT NULL"))
                db.session.commit()
                migrations_applied.append("add_schedule_notes_to_workflow")
                print("✅ schedule_notes column added successfully")
            else:
                print("✅ schedule_notes column already exists")
        except Exception as e:
            error_str = str(e)
            if 'Duplicate column name' in error_str or 'already exists' in error_str:
                print("✅ schedule_notes column already exists")
            else:
                print(f"❌ Error adding schedule_notes: {e}")
                db.session.rollback()
                return False
        
        # Migration 2: Create model_assignment_security_model table
        try:
            result = db.session.execute(text("SHOW TABLES LIKE 'model_assignment_security_model'"))
            if not result.fetchone():
                print("\n[2/2] Creating model_assignment_security_model table...")
                create_table_sql = text("""
                    CREATE TABLE model_assignment_security_model (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        model_assignment_id INT NOT NULL,
                        asset_class_id INT NOT NULL,
                        security_model_id INT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_model_assignment_asset_class (model_assignment_id, asset_class_id),
                        KEY idx_masm_model_assignment_id (model_assignment_id),
                        KEY idx_masm_asset_class_id (asset_class_id),
                        FOREIGN KEY (model_assignment_id) REFERENCES model_assignment(id) ON DELETE CASCADE,
                        FOREIGN KEY (asset_class_id) REFERENCES asset_class(id),
                        FOREIGN KEY (security_model_id) REFERENCES security_allocation_model(id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                db.session.execute(create_table_sql)
                db.session.commit()
                migrations_applied.append("add_model_assignment_security_model_table")
                print("✅ model_assignment_security_model table created successfully")
            else:
                print("✅ model_assignment_security_model table already exists")
        except Exception as e:
            error_str = str(e)
            if 'already exists' in error_str or 'Duplicate table' in error_str:
                print("✅ model_assignment_security_model table already exists")
            else:
                print(f"❌ Error creating table: {e}")
                db.session.rollback()
                return False
        
        print("\n" + "=" * 60)
        if migrations_applied:
            print(f"✅ Successfully applied {len(migrations_applied)} migration(s)")
            for m in migrations_applied:
                print(f"   - {m}")
        else:
            print("✅ All migrations already applied")
        print("=" * 60)
        return True

if __name__ == '__main__':
    success = apply_migrations()
    sys.exit(0 if success else 1)

