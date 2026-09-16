#!/usr/bin/env python3
"""
Script to delete all holdings and transactions for Ritesh Rai and recreate them
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import create_app
from models import db, Client, Transaction, Holding, Cashflow
from routes.main import update_holdings_from_transactions, update_cashflows_from_transactions
from datetime import datetime
from decimal import Decimal

def reset_ritesh_rai_data():
    """Delete all holdings and transactions for Ritesh Rai and recreate them"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Resetting Ritesh Rai's Data ===")
        
        # Find Ritesh Rai
        client = Client.query.filter(Client.name.ilike('%ritesh%rai%')).first()
        
        if not client:
            print("❌ Ritesh Rai not found")
            return
        
        print(f"✅ Found: {client.name} (ID: {client.id})")
        
        # Get current counts
        current_transactions = Transaction.query.filter_by(client_id=client.id).all()
        current_holdings = Holding.query.filter_by(client_id=client.id).all()
        current_cashflows = Cashflow.query.filter_by(client_id=client.id).all()
        
        print(f"Current data:")
        print(f"  Transactions: {len(current_transactions)}")
        print(f"  Holdings: {len(current_holdings)}")
        print(f"  Cashflows: {len(current_cashflows)}")
        
        # Confirm deletion
        print(f"\n⚠️  WARNING: This will delete ALL data for Ritesh Rai!")
        print(f"This action cannot be undone.")
        response = input("Are you sure you want to proceed? (yes/no): ").strip().lower()
        
        if response != 'yes':
            print("Operation cancelled.")
            return
        
        try:
            print("\nDeleting existing data...")
            
            # Delete cashflows first (they reference transactions)
            for cashflow in current_cashflows:
                db.session.delete(cashflow)
            print(f"  Deleted {len(current_cashflows)} cashflows")
            
            # Delete holdings
            for holding in current_holdings:
                db.session.delete(holding)
            print(f"  Deleted {len(current_holdings)} holdings")
            
            # Delete transactions
            for transaction in current_transactions:
                db.session.delete(transaction)
            print(f"  Deleted {len(current_transactions)} transactions")
            
            # Commit the deletions
            db.session.commit()
            print("✅ All data deleted successfully!")
            
            print("\nData has been reset. You can now upload the transactions again.")
            print("After uploading transactions, run the update_holdings_from_transactions function")
            print("to recreate the holdings and cashflows.")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error deleting data: {str(e)}")
            import traceback
            traceback.print_exc()
        
        print(f"\n=== Reset Complete ===")

if __name__ == "__main__":
    reset_ritesh_rai_data()











