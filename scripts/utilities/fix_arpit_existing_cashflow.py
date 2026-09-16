#!/usr/bin/env python3
"""
Script to fix Arpit's existing cashflow that shows as positive instead of negative
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from run import create_app
from models import db, Client, Cashflow
from decimal import Decimal

def fix_arpit_existing_cashflow():
    """Fix Arpit's existing cashflow that shows as positive instead of negative"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Fixing Arpit's Existing Cashflow ===")
        
        # Find Arpit Golash
        client = Client.query.filter(Client.name.ilike('%arpit%golash%')).first()
        
        if not client:
            print("❌ Arpit Golash not found")
            return
        
        print(f"✅ Found: {client.name} (ID: {client.id})")
        
        # Find the cashflow for June 4th that shows as positive
        cashflow = Cashflow.query.filter_by(
            client_id=client.id,
            date=db.func.date('2025-06-04')
        ).first()
        
        if not cashflow:
            print("❌ No cashflow found for June 4th, 2025")
            return
        
        print(f"\nCurrent cashflow:")
        print(f"  ID: {cashflow.id}")
        print(f"  Date: {cashflow.date.strftime('%Y-%m-%d')}")
        print(f"  Amount: ₹{float(cashflow.amount):,.2f}")
        print(f"  Type: {cashflow.type}")
        print(f"  Description: {cashflow.description}")
        
        # Fix the cashflow - make amount negative and type INFLOW
        current_amount = float(cashflow.amount)
        new_amount = -abs(current_amount)
        
        print(f"\nFixing cashflow:")
        print(f"  Amount: ₹{current_amount:,.2f} → ₹{new_amount:,.2f}")
        print(f"  Type: {cashflow.type} → INFLOW")
        
        cashflow.amount = Decimal(str(new_amount))
        cashflow.type = 'INFLOW'
        
        # Commit the changes
        try:
            db.session.commit()
            print(f"\n✅ Successfully fixed cashflow!")
            
            # Show updated cashflow
            print(f"\nUpdated cashflow:")
            print(f"  ID: {cashflow.id}")
            print(f"  Date: {cashflow.date.strftime('%Y-%m-%d')}")
            print(f"  Amount: ₹{float(cashflow.amount):,.2f}")
            print(f"  Type: {cashflow.type}")
            print(f"  Description: {cashflow.description}")
                
        except Exception as e:
            print(f"❌ Error fixing cashflow: {str(e)}")
            db.session.rollback()
            return
        
        print(f"\n🎉 Arpit's cashflow has been fixed!")
        print(f"   - Amount is now negative (investment)")
        print(f"   - Type is set to INFLOW")
        print(f"   - Ready for correct Nifty XIRR calculation")

if __name__ == "__main__":
    fix_arpit_existing_cashflow() 