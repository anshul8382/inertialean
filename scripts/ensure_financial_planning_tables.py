#!/usr/bin/env python3
"""
Check database for financial planning tables and create any that are missing.
Run from project root: python scripts/ensure_financial_planning_tables.py
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from main import create_app
    from extensions import db
    from sqlalchemy import inspect

    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())

        required_tables = [
            'expense_category',
            'personal_cashflow_category',
            'financial_goal',
            'financial_plan',
            'budget',
            'personal_cashflow',
            'goal_funding_tag',
            'financial_scenario_run',
        ]

        missing = [t for t in required_tables if t not in tables]

        print("Financial Planning Tables Check")
        print("=" * 50)
        for t in required_tables:
            status = "OK" if t in tables else "MISSING"
            print(f"  {t}: {status}")

        if not missing:
            print()
            print("All tables exist. No action needed.")
            return 0

        print()
        print(f"Missing tables: {', '.join(missing)}")
        print()
        print("Running migration to create missing tables...")
        print()

        # Run the migration
        from migrations.add_financial_planning_tables import upgrade

        if upgrade():
            print()
            print("Verifying...")
            inspector = inspect(db.engine)
            tables = set(inspector.get_table_names())
            still_missing = [t for t in required_tables if t not in tables]
            if still_missing:
                print(f"WARNING: Some tables still missing: {still_missing}")
                return 1
            print("All tables created successfully.")
            return 0
        else:
            return 1


if __name__ == '__main__':
    sys.exit(main())
