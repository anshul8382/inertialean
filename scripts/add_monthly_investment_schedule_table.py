#!/usr/bin/env python3
"""
Safe script to add monthly_investment_schedule table to database
This script only adds the new table and does not modify any existing tables
"""

import sys
import os
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import OperationalError

# Database connection
DB_URI = (
    f"mysql+pymysql://{os.environ.get('DB_USER', 'inertia_admin')}:"
    f"{os.environ.get('DB_PASSWORD', '')}@"
    f"{os.environ.get('DB_HOST', '127.0.0.1')}:{os.environ.get('DB_PORT', '3306')}/"
    f"{os.environ.get('DB_NAME', 'inertia_app2025')}"
)

def table_exists(engine, table_name):
    """Check if a table exists in the database"""
    inspector = inspect(engine)
    return table_name in inspector.get_table_names()

def create_monthly_investment_schedule_table():
    """Create the monthly_investment_schedule table"""
    
    print("=" * 70)
    print("Adding Monthly Investment Schedule Table")
    print("=" * 70)
    
    try:
        engine = create_engine(DB_URI)
        
        # Check if table already exists
        if table_exists(engine, 'monthly_investment_schedule'):
            print("⚠️  Table 'monthly_investment_schedule' already exists!")
            print("   Skipping table creation to avoid conflicts.")
            return True
        
        print("✅ Table does not exist. Proceeding with creation...")
        
        with engine.connect() as conn:
            # Create the table
            create_table_sql = """
            CREATE TABLE monthly_investment_schedule (
                id INT AUTO_INCREMENT PRIMARY KEY,
                client_id INT NOT NULL,
                planned_amount DECIMAL(15, 2) NOT NULL,
                day_of_month INT NOT NULL DEFAULT 1,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                start_date DATE NOT NULL,
                end_date DATE NULL,
                notes TEXT NULL,
                created_at DATETIME NULL,
                updated_at DATETIME NULL,
                created_by INT NOT NULL,
                UNIQUE KEY unique_client_schedule (client_id),
                FOREIGN KEY (client_id) REFERENCES client(id) ON DELETE CASCADE,
                FOREIGN KEY (created_by) REFERENCES user(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """
            
            print("\nCreating table...")
            conn.execute(text(create_table_sql))
            conn.commit()
            
            print("✅ Table 'monthly_investment_schedule' created successfully!")
            print("\nTable structure:")
            print("  - id: Primary key")
            print("  - client_id: Foreign key to client (unique, one schedule per client)")
            print("  - planned_amount: Monthly investment amount")
            print("  - day_of_month: Day of month (1-31)")
            print("  - is_active: Whether schedule is active")
            print("  - start_date: When schedule starts")
            print("  - end_date: Optional end date")
            print("  - notes: Optional notes")
            print("  - created_at, updated_at: Timestamps")
            print("  - created_by: User who created the schedule")
            
            return True
            
    except OperationalError as e:
        print(f"❌ Database error: {str(e)}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    print("\n🔒 SAFE DATABASE UPDATE SCRIPT")
    print("This script will ONLY add the new 'monthly_investment_schedule' table")
    print("It will NOT modify any existing tables or data\n")
    
    success = create_monthly_investment_schedule_table()
    
    if success:
        print("\n" + "=" * 70)
        print("✅ Database update completed successfully!")
        print("=" * 70)
        sys.exit(0)
    else:
        print("\n" + "=" * 70)
        print("❌ Database update failed!")
        print("=" * 70)
        sys.exit(1)

