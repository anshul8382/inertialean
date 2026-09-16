#!/usr/bin/env python3
"""
Script to fix Arpit's cashflow amounts - make them negative (investments)
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from run import create_app
from models import db, Client, Cashflow
from decimal import Decimal

def fix_arpit_cashflows():
    """Fix Arpit's cashflow amounts to be negative (investments)"""
    
    app = create_app()
    
    with app.app_context():
        print("=== Fixing Arpit Golash Cashflows ===")
        
        # Find Arpit Golash
        client = Client.query.filter(Client.name.ilike('%arpit%golash%')).first()
        
        if not client:
            print("❌ Arpit Golash not found")
            return
        
        print(f"✅ Found: {client.name} (ID: {client.id})")
        
        # Get all cashflows for Arpit
        cashflows = Cashflow.query.filter_by(client_id=client.id).order_by(Cashflow.date).all()
        
        if not cashflows:
            print("❌ No cashflows found for Arpit")
            return
        
        print(f"\nCurrent cashflows:")
        for cf in cashflows:
            print(f"  ID: {cf.id}, Date: {cf.date.strftime('%Y-%m-%d')}, Amount: ₹{float(cf.amount):,.2f}, Type: {cf.type}")
        
        # Fix the cashflows - make amounts negative
        print(f"\nFixing cashflow amounts...")
        
        for cf in cashflows:
            current_amount = float(cf.amount)
            new_amount = -abs(current_amount)  # Make negative
            
            print(f"  Changing ID {cf.id}: ₹{current_amount:,.2f} → ₹{new_amount:,.2f}")
            
            cf.amount = Decimal(str(new_amount))
            cf.type = 'INFLOW'  # Set type to INFLOW (investment)
        
        # Commit the changes
        try:
            db.session.commit()
            print(f"\n✅ Successfully fixed {len(cashflows)} cashflows!")
            
            # Show updated cashflows
            print(f"\nUpdated cashflows:")
            for cf in cashflows:
                print(f"  ID: {cf.id}, Date: {cf.date.strftime('%Y-%m-%d')}, Amount: ₹{float(cf.amount):,.2f}, Type: {cf.type}")
                
        except Exception as e:
            print(f"❌ Error fixing cashflows: {str(e)}")
            db.session.rollback()
            return
        
        print(f"\n🎉 Arpit's cashflows have been fixed!")
        print(f"   - Amounts are now negative (investments)")
        print(f"   - Types are set to INFLOW")
        print(f"   - Ready for correct Nifty XIRR calculation")

if __name__ == "__main__":
    fix_arpit_cashflows() 