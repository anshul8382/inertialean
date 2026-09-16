#!/usr/bin/env python3
"""
Script to add performance tracking tables to the database
"""
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

def add_performance_tables():
    """Add performance tracking tables to the database"""
    try:
        # Connect to database
        connection = pymysql.connect(**DB_CONFIG)
        cursor = connection.cursor()
        
        print("Connected to database successfully!")
        
        # Create portfolio_snapshot table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshot (
                id INT AUTO_INCREMENT PRIMARY KEY,
                client_id INT NOT NULL,
                date DATE NOT NULL,
                total_value DECIMAL(15,2) NOT NULL,
                total_invested DECIMAL(15,2) NOT NULL,
                total_withdrawn DECIMAL(15,2) NOT NULL,
                net_investment DECIMAL(15,2) NOT NULL,
                absolute_return DECIMAL(15,2) NOT NULL,
                absolute_return_percent DECIMAL(10,4) NOT NULL,
                xirr DECIMAL(10,4) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (client_id) REFERENCES client(id) ON DELETE CASCADE,
                INDEX idx_client_date (client_id, date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        print("✓ Created portfolio_snapshot table")
        
        # Create holding_snapshot table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS holding_snapshot (
                id INT AUTO_INCREMENT PRIMARY KEY,
                portfolio_snapshot_id INT NOT NULL,
                security_id INT NOT NULL,
                quantity DECIMAL(15,4) NOT NULL,
                average_price DECIMAL(15,4) NOT NULL,
                current_price DECIMAL(15,4) NOT NULL,
                current_value DECIMAL(15,2) NOT NULL,
                unrealized_pnl DECIMAL(15,2) NOT NULL,
                unrealized_pnl_percent DECIMAL(10,4) NOT NULL,
                allocation_percent DECIMAL(10,4) NOT NULL,
                FOREIGN KEY (portfolio_snapshot_id) REFERENCES portfolio_snapshot(id) ON DELETE CASCADE,
                FOREIGN KEY (security_id) REFERENCES security(id) ON DELETE CASCADE,
                INDEX idx_portfolio_snapshot (portfolio_snapshot_id),
                INDEX idx_security (security_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        print("✓ Created holding_snapshot table")
        
        # Create benchmark table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS benchmark (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                symbol VARCHAR(20) NOT NULL,
                description TEXT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_symbol (symbol)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        print("✓ Created benchmark table")
        
        # Create benchmark_data table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_data (
                id INT AUTO_INCREMENT PRIMARY KEY,
                benchmark_id INT NOT NULL,
                date DATE NOT NULL,
                price DECIMAL(15,4) NOT NULL,
                return_percent DECIMAL(10,4) NULL,
                cumulative_return DECIMAL(10,4) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (benchmark_id) REFERENCES benchmark(id) ON DELETE CASCADE,
                UNIQUE KEY unique_benchmark_date (benchmark_id, date),
                INDEX idx_benchmark_date (benchmark_id, date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        print("✓ Created benchmark_data table")
        
        # Create model_performance table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_performance (
                id INT AUTO_INCREMENT PRIMARY KEY,
                model_id INT NOT NULL,
                model_type VARCHAR(20) NOT NULL,
                date DATE NOT NULL,
                total_value DECIMAL(15,2) NOT NULL,
                return_percent DECIMAL(10,4) NULL,
                cumulative_return DECIMAL(10,4) NULL,
                benchmark_id INT NULL,
                benchmark_return DECIMAL(10,4) NULL,
                excess_return DECIMAL(10,4) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (benchmark_id) REFERENCES benchmark(id) ON DELETE SET NULL,
                UNIQUE KEY unique_model_date (model_id, model_type, date),
                INDEX idx_model_date (model_id, model_type, date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        print("✓ Created model_performance table")
        
        # Add some default benchmarks
        benchmarks = [
            ('NIFTY 50', '^NSEI', 'NIFTY 50 Index - Large Cap Indian Stocks'),
            ('SENSEX', '^BSESN', 'BSE SENSEX - Bombay Stock Exchange Index'),
            ('NIFTY BANK', '^NSEBANK', 'NIFTY Bank Index - Banking Sector'),
            ('NIFTY IT', '^CNXIT', 'NIFTY IT Index - Information Technology'),
            ('GOLD ETF', 'GOLDBEES.NS', 'Gold ETF - Commodity Benchmark')
        ]
        
        for name, symbol, description in benchmarks:
            cursor.execute("""
                INSERT IGNORE INTO benchmark (name, symbol, description) 
                VALUES (%s, %s, %s)
            """, (name, symbol, description))
        
        print("✓ Added default benchmarks")
        
        connection.commit()
        print("\n🎉 All performance tracking tables created successfully!")
        
        # Show table structure
        tables = ['portfolio_snapshot', 'holding_snapshot', 'benchmark', 'benchmark_data', 'model_performance']
        import re
        _ident = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,127}$')
        for table in tables:
            if not _ident.match(table):
                continue
            print(f"\n{table} table structure:")
            cursor.execute("DESCRIBE `" + table + "`")
            columns = cursor.fetchall()
            for column in columns:
                print(f"  {column[0]}: {column[1]} {column[2]} {column[3]} {column[4]} {column[5]}")
        
    except Exception as e:
        print(f"Error: {str(e)}")
        if 'connection' in locals():
            connection.rollback()
    finally:
        if 'connection' in locals():
            connection.close()
            print("\nDatabase connection closed.")

if __name__ == "__main__":
    add_performance_tables() 