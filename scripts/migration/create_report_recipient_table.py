#!/usr/bin/env python3
"""
Create ReportRecipient table in the database
"""

import os
import sys
import pymysql
from datetime import datetime

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'inertia_admin',
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': 'inertia_app2025',
    'charset': 'utf8mb4'
}

def create_report_recipient_table():
    """Create the report_recipient table"""
    
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS `report_recipient` (
        `id` int(11) NOT NULL AUTO_INCREMENT,
        `job_id` varchar(100) NOT NULL,
        `email` varchar(120) NOT NULL,
        `name` varchar(100) NOT NULL,
        `is_active` tinyint(1) DEFAULT 1,
        `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
        `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        KEY `idx_job_id` (`job_id`),
        KEY `idx_email` (`email`),
        KEY `idx_is_active` (`is_active`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """
    
    try:
        connection = pymysql.connect(**DB_CONFIG)
        cursor = connection.cursor()
        
        print("Creating report_recipient table...")
        cursor.execute(create_table_sql)
        connection.commit()
        
        print("✅ report_recipient table created successfully!")
        
        # Verify table was created
        cursor.execute("SHOW TABLES LIKE 'report_recipient'")
        result = cursor.fetchone()
        if result:
            print("✅ Table verification successful")
        else:
            print("❌ Table verification failed")
            
    except Exception as e:
        print(f"❌ Error creating table: {str(e)}")
        return False
    finally:
        if 'connection' in locals():
            connection.close()
    
    return True

if __name__ == "__main__":
    print("🚀 Creating ReportRecipient table...")
    success = create_report_recipient_table()
    
    if success:
        print("🎉 ReportRecipient table creation completed successfully!")
        sys.exit(0)
    else:
        print("💥 ReportRecipient table creation failed!")
        sys.exit(1)
