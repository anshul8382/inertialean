#!/usr/bin/env python3
"""
Start a new monthly holdings processing cycle
Runs on the 1st of every month at 1 AM
"""
import os
import sys
from datetime import datetime, date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from main import create_app
from extensions import db
from models import Client
from sqlalchemy import text


def first_sunday_on_or_after(d: date) -> date:
    """First Sunday on or after d (weekday: Mon=0 … Sun=6)."""
    days_until_sunday = (6 - d.weekday()) % 7
    return d + timedelta(days=days_until_sunday)


def start_monthly_cycle():
    """Start a new monthly cycle for all clients"""
    app = create_app()
    
    with app.app_context():
        try:
            # Generate cycle ID (YYYY-MM format)
            cycle_id = datetime.now().strftime("%Y-%m")
            
            # Check if cycle already exists
            with db.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT COUNT(*) as count FROM monthly_holdings_cycle 
                    WHERE cycle_id = :cycle_id
                """), {"cycle_id": cycle_id})
                existing_count = result.fetchone()[0]
            
            if existing_count > 0:
                print(f"Cycle {cycle_id} already exists with {existing_count} entries. Skipping.")
                return
            
            # Get all active clients
            clients = Client.query.all()
            print(f"Starting cycle {cycle_id} for {len(clients)} clients")
            
            # All clients are processed together on the first Sunday of the month
            first_of_month = date.today().replace(day=1)
            processing_date = first_sunday_on_or_after(first_of_month)

            cycle_entries = []
            for client in clients:
                cycle_entries.append({
                    'cycle_id': cycle_id,
                    'client_id': client.id,
                    'processing_date': processing_date,
                    'status': 'pending'
                })
            
            # Bulk insert
            if cycle_entries:
                with db.engine.connect() as conn:
                    conn.execute(text("""
                        INSERT INTO monthly_holdings_cycle 
                        (cycle_id, client_id, processing_date, status)
                        VALUES (:cycle_id, :client_id, :processing_date, :status)
                    """), cycle_entries)
                    conn.commit()
            
            print(f"✅ Created cycle {cycle_id} with {len(cycle_entries)} client entries")
            
        except Exception as e:
            print(f"❌ Error starting cycle: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            raise

if __name__ == "__main__":
    start_monthly_cycle()

