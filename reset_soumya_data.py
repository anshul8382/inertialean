#!/usr/bin/env python3
"""
Script to delete all holdings, transactions, and cashflows for Soumya Chatterjee
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import create_app
from models import db, Client, Transaction, Holding, Cashflow
from datetime import datetime
from decimal import Decimal

def reset_soumya_data():
    """Delete all holdings, transactions, and cashflows for Soumya Chatterjee"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Resetting Soumya Chatterjee's Data ===")
        
        # Find Soumya Chatterjee
        client = Client.query.filter(Client.name.ilike('%soumya%chatterjee%')).first()
        
        if not client:
            print("❌ Soumya Chatterjee not found")
            return
        
        print(f"✅ Found: {client.name} (ID: {client.id})")
        
        # Get current counts
        current_transactions = Transaction.query.filter_by(client_id=client.id).all()
        current_holdings = Holding.query.filter_by(client_id=client.id).all()
        current_cashflows = Cashflow.query.filter_by(client_id=client.id).all()
        
        print(f"\nCurrent data:")
        print(f"  Transactions: {len(current_transactions)}")
        print(f"  Holdings: {len(current_holdings)}")
        print(f"  Cashflows: {len(current_cashflows)}")
        
        # Confirm deletion
        print(f"\n⚠️  WARNING: This will delete ALL data for {client.name}!")
        print(f"This action cannot be undone.")
        response = input("Are you sure you want to proceed? (yes/no): ").strip().lower()
        
        if response != 'yes':
            print("Operation cancelled.")
            return
        
        try:
            print("\nDeleting existing data...")
            
            # Delete cashflows first (they reference transactions)
            deleted_cashflows = 0
            for cashflow in current_cashflows:
                db.session.delete(cashflow)
                deleted_cashflows += 1
            print(f"  ✓ Deleted {deleted_cashflows} cashflows")
            
            # Delete holdings
            deleted_holdings = 0
            for holding in current_holdings:
                db.session.delete(holding)
                deleted_holdings += 1
            print(f"  ✓ Deleted {deleted_holdings} holdings")
            
            # Delete transactions
            deleted_transactions = 0
            for transaction in current_transactions:
                db.session.delete(transaction)
                deleted_transactions += 1
            print(f"  ✓ Deleted {deleted_transactions} transactions")
            
            # Commit the deletions
            db.session.commit()
            print("\n✅ All data deleted successfully!")
            
            print("\n📋 Summary:")
            print(f"  - Transactions deleted: {deleted_transactions}")
            print(f"  - Holdings deleted: {deleted_holdings}")
            print(f"  - Cashflows deleted: {deleted_cashflows}")
            
            print("\n✅ Data has been reset. You can now upload fresh transactions.")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error deleting data: {str(e)}")
            import traceback
            traceback.print_exc()
        
        print(f"\n=== Reset Complete ===")

if __name__ == "__main__":
    reset_soumya_data()

