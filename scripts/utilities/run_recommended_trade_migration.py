#!/usr/bin/env python3
"""
Script to run the recommended trade migration
"""
import os
import sys
from flask import Flask
from flask_migrate import upgrade

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set environment variables
os.environ['FLASK_APP'] = 'main.py'
os.environ['FLASK_ENV'] = 'production'

# Import the app
from app import create_app
from extensions import db

def run_migration():
    """Run the recommended trade migration"""
    app = create_app()
    
    with app.app_context():
        try:
            print("Running migration to add recommended_trade table...")
            upgrade()
            print("Migration completed successfully!")
            
            # Verify the table was created
            result = db.engine.execute("SHOW TABLES LIKE 'recommended_trade'")
            if result.fetchone():
                print("✓ recommended_trade table created successfully")
            else:
                print("✗ recommended_trade table not found")
                
        except Exception as e:
            print(f"Error running migration: {str(e)}")
            return False
    
    return True

if __name__ == '__main__':
    success = run_migration()
    sys.exit(0 if success else 1) 