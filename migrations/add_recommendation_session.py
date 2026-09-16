#!/usr/bin/env python3
"""
Migration: Add Recommendation Session table and session_id to recommendations
This allows grouping related recommendations generated together for a client
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

def create_recommendation_session_table():
    """Create the recommendation_session table"""
    from extensions import db
    
    # Create recommendation_session table
    db.engine.execute("""
        CREATE TABLE IF NOT EXISTS recommendation_session (
            id INT AUTO_INCREMENT PRIMARY KEY,
            client_id INT NOT NULL,
            session_name VARCHAR(100),
            session_type VARCHAR(50) DEFAULT 'rebalancing',
            investment_amount DECIMAL(15,2),
            status VARCHAR(20) DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by INT NOT NULL,
            completed_at TIMESTAMP NULL,
            notes TEXT,
            FOREIGN KEY (client_id) REFERENCES client(id),
            FOREIGN KEY (created_by) REFERENCES user(id)
        )
    """)
    
    # Add session_id to recommendation table
    try:
        db.engine.execute("ALTER TABLE recommendation ADD COLUMN session_id INT")
        db.engine.execute("ALTER TABLE recommendation ADD COLUMN batch_created_at TIMESTAMP")
    except Exception as e:
        print(f"Columns might already exist: {e}")
    
    # Add foreign key constraint
    try:
        db.engine.execute("""
            ALTER TABLE recommendation 
            ADD CONSTRAINT fk_recommendation_session 
            FOREIGN KEY (session_id) REFERENCES recommendation_session(id)
        """)
    except Exception as e:
        print(f"Foreign key might already exist: {e}")

def rollback_recommendation_session():
    """Rollback the changes"""
    from extensions import db
    try:
        db.engine.execute("ALTER TABLE recommendation DROP COLUMN session_id")
        db.engine.execute("ALTER TABLE recommendation DROP COLUMN batch_created_at")
        db.engine.execute("DROP TABLE IF EXISTS recommendation_session")
    except Exception as e:
        print(f"Rollback error: {e}")

if __name__ == "__main__":
    from main import create_app
    app = create_app()
    
    with app.app_context():
        print("Creating recommendation session table...")
        create_recommendation_session_table()
        print("Migration completed successfully!")
