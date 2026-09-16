#!/usr/bin/env python3
"""
Unpause the price_updates DAG so daily closing prices are updated.

The price_updates DAG updates Security.current_price and saves to historical_price
(at 4 PM IST market close). If the DAG is paused, no closing prices are saved.

Run this script to unpause: python scripts/unpause_price_updates_dag.py

Or use the Airflow UI: Workflows → Airflow → find price_updates → click Unpause
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    try:
        from services.airflow_service import AirflowService
        from main import create_app

        app = create_app()
        with app.app_context():
            result = AirflowService().unpause_dag('price_updates')
            if result.get('success'):
                print("✅ price_updates DAG unpaused successfully.")
                print("   Daily closing prices will now be saved at 4 PM IST.")
            else:
                print(f"❌ Failed: {result.get('message', 'Unknown error')}")
                sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\nAlternative: Unpause via Airflow UI at /airflow or use:")
        print("  airflow dags unpause price_updates")
        sys.exit(1)


if __name__ == '__main__':
    main()
