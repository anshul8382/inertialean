#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from extensions import db
from models import Transaction, Cashflow, User
from datetime import datetime

def create_cashflows_for_existing_transactions():
    """Create cashflow records for existing transactions"""
    app = create_app()
    
    with app.app_context():
        try:
            # Get all transactions
            transactions = Transaction.query.all()
            print(f"Found {len(transactions)} transactions")
            
            # Group transactions by client and date
            client_date_transactions = {}
            for transaction in transactions:
                key = (transaction.client_id, transaction.transaction_date.date())
                if key not in client_date_transactions:
                    client_date_transactions[key] = []
                client_date_transactions[key].append(transaction)
            
            print(f"Grouped into {len(client_date_transactions)} client-date combinations")
            
            # Create cashflow records
            cashflows_created = 0
            for (client_id, date), day_transactions in client_date_transactions.items():
                total_cashflow = 0.0
                
                for transaction in day_transactions:
                    amount = float(transaction.amount) if transaction.amount else 0.0
                    # Negative for BUY (money going out), positive for SELL (money coming in)
                    cashflow_amount = -amount if transaction.type == 'BUY' else amount
                    total_cashflow += cashflow_amount
                
                # Check if cashflow already exists for this client and date
                existing_cashflow = Cashflow.query.filter_by(
                    client_id=client_id,
                    date=datetime.combine(date, datetime.min.time())
                ).first()
                
                if not existing_cashflow and total_cashflow != 0:
                    # Create cashflow record
                    cashflow = Cashflow(
                        client_id=client_id,
                        date=datetime.combine(date, datetime.min.time()),
                        amount=total_cashflow,
                        type='WITHDRAWAL' if total_cashflow > 0 else 'INVESTMENT',
                        description=f"Net cashflow for {date.strftime('%Y-%m-%d')}",
                        created_by=1  # Default to user ID 1
                    )
                    db.session.add(cashflow)
                    cashflows_created += 1
                    print(f"Created cashflow for client {client_id} on {date}: ₹{total_cashflow:.2f}")
            
            db.session.commit()
            print(f"Successfully created {cashflows_created} cashflow records")
            
        except Exception as e:
            db.session.rollback()
            print(f"Error: {e}")
            return False
    
    return True

if __name__ == "__main__":
    print("Creating cashflow records for existing transactions...")
    success = create_cashflows_for_existing_transactions()
    if success:
        print("✅ Cashflow creation completed successfully!")
    else:
        print("❌ Cashflow creation failed!") 