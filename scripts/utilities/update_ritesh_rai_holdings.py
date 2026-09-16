#!/usr/bin/env python3
"""
Script to automatically update Ritesh Rai's holdings to match transactions
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import create_app
from models import db, Client, Transaction, Holding, Security
from routes.main import update_holdings_from_transactions
from datetime import datetime
from decimal import Decimal

def update_ritesh_rai_holdings():
    """Update Ritesh Rai's holdings to match transactions"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Updating Ritesh Rai's Holdings ===")
        
        # Find Ritesh Rai
        client = Client.query.filter(Client.name.ilike('%ritesh%rai%')).first()
        
        if not client:
            print("❌ Ritesh Rai not found")
            return
        
        print(f"✅ Found: {client.name} (ID: {client.id})")
        
        # Get current holdings count
        current_holdings = Holding.query.filter_by(client_id=client.id).all()
        print(f"Current holdings before update: {len(current_holdings)}")
        
        # Update holdings
        print("Updating holdings to match transactions...")
        try:
            update_holdings_from_transactions(client.id)
            print("✅ Holdings updated successfully!")
            
            # Get updated holdings
            updated_holdings = Holding.query.filter_by(client_id=client.id).all()
            print(f"Holdings after update: {len(updated_holdings)}")
            
            # Show summary of updated holdings
            print(f"\nUpdated Holdings Summary:")
            print("Security\tQuantity\tAvg Price\tCurrent Value")
            print("-" * 60)
            
            total_value = 0
            for h in updated_holdings:
                security_name = h.security.name if h.security else "Unknown"
                current_value = h.quantity * h.average_price if h.quantity and h.average_price else 0
                total_value += current_value
                print(f"{security_name}\t{h.quantity}\t₹{h.average_price:.2f}\t₹{current_value:.2f}")
            
            print("-" * 60)
            print(f"Total Portfolio Value: ₹{total_value:,.2f}")
            
        except Exception as e:
            print(f"❌ Error updating holdings: {str(e)}")
            import traceback
            traceback.print_exc()
        
        print(f"\n=== Update Complete ===")

if __name__ == "__main__":
    update_ritesh_rai_holdings()











