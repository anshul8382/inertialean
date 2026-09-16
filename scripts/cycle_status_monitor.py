#!/usr/bin/env python3
"""
Monitor cycle status and generate reports
"""
import os
import sys
from datetime import datetime, date
from collections import defaultdict

sys.path.insert(0, '/home/inertia/app')

from run import create_app
from extensions import db
from models import Client
from sqlalchemy import text

def generate_cycle_report():
    """Generate a report of the current cycle status"""
    app = create_app()
    
    with app.app_context():
        try:
            # Get current cycle
            current_month = datetime.now().strftime("%Y-%m")
            
            with db.engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT status, mismatches_found, client_id
                    FROM monthly_holdings_cycle 
                    WHERE cycle_id = :cycle_id
                """), {"cycle_id": current_month})
                cycle_entries = result.fetchall()
            
            if not cycle_entries:
                print(f"No cycle found for {current_month}")
                return
            
            # Calculate statistics
            stats = defaultdict(int)
            mismatch_clients = []
            
            for entry in cycle_entries:
                status = entry[0]
                mismatches = entry[1]
                client_id = entry[2]
                
                stats[status] += 1
                
                if mismatches and mismatches > 0:
                    mismatch_clients.append((client_id, mismatches))
            
            total_clients = len(cycle_entries)
            completed = stats['completed']
            pending = stats['pending']
            failed = stats['failed']
            
            print(f"\n📊 Monthly Cycle Report - {current_month}")
            print("=" * 50)
            print(f"Total Clients: {total_clients}")
            print(f"Completed: {completed} ({completed/total_clients*100:.1f}%)")
            print(f"Pending: {pending} ({pending/total_clients*100:.1f}%)")
            print(f"Failed: {failed} ({failed/total_clients*100:.1f}%)")
            
            # Show clients with mismatches
            if mismatch_clients:
                print(f"\n⚠️  Clients with Mismatches: {len(mismatch_clients)}")
                for client_id, mismatches in mismatch_clients[:10]:  # Show first 10
                    client = Client.query.get(client_id)
                    client_name = client.name if client else f"Client {client_id}"
                    print(f"  - {client_name}: {mismatches} mismatches")
                
                if len(mismatch_clients) > 10:
                    print(f"  ... and {len(mismatch_clients) - 10} more")
            
            # Check if cycle is complete
            if pending == 0:
                print(f"\n🎉 Cycle {current_month} is complete!")
                print(f"Next cycle will start on the 1st of next month")
            else:
                print(f"\n⏳ Cycle {current_month} is in progress")
                print(f"Remaining clients: {pending}")
            
        except Exception as e:
            print(f"Error generating report: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    generate_cycle_report()

