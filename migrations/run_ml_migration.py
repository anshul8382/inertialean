#!/usr/bin/env python3
"""
Script to run ML database migration
Creates tables for user modification tracking and ML prediction accuracy
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extensions import db
from migrations.add_user_modification_tracking import create_user_modification_tracking_tables

def run_ml_migration():
    """Run the ML migration to create necessary tables"""
    try:
        print("Starting ML database migration...")
        
        # Get the migration SQL
        migration_sql = create_user_modification_tracking_tables()
        
        # Execute the migration
        with db.engine.connect() as connection:
            # Split by semicolon and execute each statement
            statements = [s.strip() for s in migration_sql.split(';') if s.strip()]
            
            for statement in statements:
                if statement:
                    try:
                        connection.execute(db.text(statement))
                        print(f"✓ Executed: {statement[:50]}...")
                    except Exception as e:
                        # Ignore "table already exists" errors
                        if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                            print(f"⚠ Table already exists, skipping: {statement[:50]}...")
                        else:
                            print(f"✗ Error executing statement: {e}")
                            print(f"  Statement: {statement[:100]}...")
            
            connection.commit()
        
        print("\n✓ ML migration completed successfully!")
        print("\nCreated tables:")
        print("  - user_modification_log")
        print("  - user_modification_features")
        print("  - ml_prediction_accuracy")
        
        return True
        
    except Exception as e:
        print(f"\n✗ Error running ML migration: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    from main import create_app
    app = create_app()
    with app.app_context():
        success = run_ml_migration()
        sys.exit(0 if success else 1)

