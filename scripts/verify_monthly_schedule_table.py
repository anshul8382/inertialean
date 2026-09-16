#!/usr/bin/env python3
"""Verify monthly_investment_schedule table exists and structure is correct"""

from sqlalchemy import create_engine, text, inspect

DB_URI = (
    f"mysql+pymysql://{os.environ.get('DB_USER', 'inertia_admin')}:"
    f"{os.environ.get('DB_PASSWORD', '')}@"
    f"{os.environ.get('DB_HOST', '127.0.0.1')}:{os.environ.get('DB_PORT', '3306')}/"
    f"{os.environ.get('DB_NAME', 'inertia_app2025')}"
)

engine = create_engine(DB_URI)
inspector = inspect(engine)

if 'monthly_investment_schedule' in inspector.get_table_names():
    print("✅ Table 'monthly_investment_schedule' exists")
    
    columns = inspector.get_columns('monthly_investment_schedule')
    print(f"\nTable has {len(columns)} columns:")
    for col in columns:
        print(f"  - {col['name']}: {col['type']}")
    
    print("\n✅ Table structure verified successfully!")
else:
    print("❌ Table 'monthly_investment_schedule' does not exist")

