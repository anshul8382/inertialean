"""
Migration: Add monthly_holdings_cycle table
Purpose: Track monthly holdings processing cycles for all clients
Created: 2024-10-27
"""

import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extensions import db
from run import create_app

def upgrade():
    """Add monthly_holdings_cycle table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Create the table using raw SQL for better control
            with db.engine.connect() as conn:
                conn.execute(db.text("""
                    CREATE TABLE IF NOT EXISTS monthly_holdings_cycle (
                        id INT PRIMARY KEY AUTO_INCREMENT,
                        cycle_id VARCHAR(50) NOT NULL,
                        client_id INT NOT NULL,
                        processing_date DATE,
                        status ENUM('pending', 'processing', 'completed', 'failed') DEFAULT 'pending',
                        processed_at DATETIME NULL,
                        error_message TEXT NULL,
                        mismatches_found INT DEFAULT 0,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        
                        FOREIGN KEY (client_id) REFERENCES client(id) ON DELETE CASCADE,
                        UNIQUE KEY unique_cycle_client (cycle_id, client_id),
                        INDEX idx_cycle_status (cycle_id, status),
                        INDEX idx_processing_date (processing_date)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """))
                conn.commit()
            
            print("✅ Created monthly_holdings_cycle table successfully")
            
        except Exception as e:
            print(f"❌ Error creating table: {e}")
            # Check if table already exists
            try:
                with db.engine.connect() as conn:
                    result = conn.execute(db.text("SHOW TABLES LIKE 'monthly_holdings_cycle'"))
                    if result.fetchone():
                        print("⚠️  Table already exists, skipping creation")
                    else:
                        raise
            except Exception as e2:
                print(f"❌ Critical error: {e2}")
                raise

def downgrade():
    """Remove monthly_holdings_cycle table"""
    app = create_app()
    
    with app.app_context():
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text("DROP TABLE IF EXISTS monthly_holdings_cycle"))
                conn.commit()
            print("✅ Dropped monthly_holdings_cycle table successfully")
        except Exception as e:
            print(f"❌ Error dropping table: {e}")
            raise

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "downgrade":
        downgrade()
    else:
        upgrade()

