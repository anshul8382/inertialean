#!/usr/bin/env python3
"""
Force update holdings table directly with correct values
"""
import sys
sys.path.insert(0, '/home/inertia/app')
from run import create_app
from services.forward_holding_calculation_service import get_client_portfolio_by_date
from models import db, Client, Holding
from datetime import date

def force_update_holdings():
    app = create_app()
    with app.app_context():
        print("="*80)
        print("FORCE UPDATING HOLDINGS TABLE with Forward Calculation Results")
        print("="*80)
        
        client_id = 46
        test_date = date.today()
        
        # Get client info
        client = Client.query.get(client_id)
        if not client:
            print(f"❌ Client {client_id} not found!")
            return
        
        print(f"Client: {client.name} (ID: {client_id})")
        
        # Get forward calculation results
        print(f"\n🔄 Getting forward calculation results...")
        portfolio = get_client_portfolio_by_date(client_id, test_date)
        
        if 'error' in portfolio:
            print(f"❌ Forward calculation error: {portfolio['error']}")
            return
        
        print(f"✅ Forward calculation complete: {len(portfolio['holdings'])} holdings")
        
        # Show before/after comparison for key securities
        print(f"\n📊 BEFORE/AFTER COMPARISON:")
        print(f"{'Symbol':<15} {'Before Qty':<12} {'Before Price':<15} {'After Qty':<12} {'After Price':<15}")
        print("="*80)
        
        # Get current holdings for comparison
        current_holdings = {h.security.symbol: h for h in Holding.query.filter_by(client_id=client_id).all() if h.security}
        
        # Update holdings table
        print(f"\n🔄 Updating holdings table...")
        
        # Clear existing holdings
        Holding.query.filter_by(client_id=client_id).delete()
        db.session.flush()
        
        # Create new holdings from forward calculation
        updated_count = 0
        for holding_data in portfolio['holdings']:
            # Find security symbol for display
            from models import Security
            security = Security.query.get(holding_data['security_id'])
            symbol = security.symbol if security else f"ID{holding_data['security_id']}"
            
            # Show comparison
            before_holding = current_holdings.get(symbol)
            if before_holding:
                print(f"{symbol:<15} {before_holding.quantity:<12.2f} ₹{before_holding.average_price:<14.2f} {holding_data['quantity']:<12.2f} ₹{holding_data['average_price']:<14.2f}")
            else:
                print(f"{symbol:<15} {'NEW':<12} {'NEW':<15} {holding_data['quantity']:<12.2f} ₹{holding_data['average_price']:<14.2f}")
            
            # Create new holding
            holding = Holding(
                client_id=client_id,
                security_id=holding_data['security_id'],
                quantity=holding_data['quantity'],
                average_price=holding_data['average_price']
            )
            db.session.add(holding)
            updated_count += 1
        
        # Commit changes
        db.session.commit()
        
        print(f"\n✅ Successfully updated {updated_count} holdings!")
        print(f"🎉 Holdings table now reflects correct forward calculation values!")

if __name__ == "__main__":
    force_update_holdings()



