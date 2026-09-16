"""
Script to recalculate all holdings from transaction history
This will fix data integrity issues where holdings don't match transactions
"""

from models import db, Transaction, Holding, Security
from collections import defaultdict
from run import create_app
from decimal import Decimal

def recalculate_holdings_for_client(client_id):
    """Recalculate holdings for a specific client from transactions"""
    
    # Calculate expected holdings from transactions
    holdings_from_txns = defaultdict(lambda: {'quantity': 0.0, 'total_cost': 0.0})
    
    all_txns = Transaction.query.filter_by(client_id=client_id).order_by(Transaction.transaction_date).all()
    
    for txn in all_txns:
        sec_id = txn.security_id
        qty = float(txn.quantity)
        price = float(txn.price)
        
        if txn.type == 'BUY':
            holdings_from_txns[sec_id]['quantity'] += qty
            holdings_from_txns[sec_id]['total_cost'] += qty * price
        elif txn.type == 'SELL':
            holdings_from_txns[sec_id]['quantity'] -= qty
            # Reduce proportional cost
            if holdings_from_txns[sec_id]['quantity'] > 0:
                holdings_from_txns[sec_id]['total_cost'] *= (holdings_from_txns[sec_id]['quantity'] + qty - qty) / (holdings_from_txns[sec_id]['quantity'] + qty)
    
    # Update holdings table
    print(f"\nRecalculating holdings for client {client_id}...")
    print("="*80)
    
    updated = 0
    created = 0
    deleted = 0
    
    # Get existing holdings
    existing_holdings = {h.security_id: h for h in Holding.query.filter_by(client_id=client_id).all()}
    
    # Update or create holdings from transactions
    for sec_id, data in holdings_from_txns.items():
        qty = data['quantity']
        
        if qty <= 0.01:  # Effectively zero
            # Delete if exists
            if sec_id in existing_holdings:
                security = Security.query.get(sec_id)
                symbol = security.symbol if security else f"ID:{sec_id}"
                print(f"  Deleting: {symbol:15s} (quantity: {qty:.2f})")
                db.session.delete(existing_holdings[sec_id])
                deleted += 1
            continue
        
        avg_price = data['total_cost'] / qty if qty > 0 else 0
        
        if sec_id in existing_holdings:
            # Update existing
            holding = existing_holdings[sec_id]
            old_qty = float(holding.quantity)
            old_price = float(holding.average_price)
            
            if abs(old_qty - qty) > 0.01 or abs(old_price - avg_price) > 0.01:
                security = Security.query.get(sec_id)
                symbol = security.symbol if security else f"ID:{sec_id}"
                print(f"  Updating: {symbol:15s} Qty: {old_qty:8.2f} → {qty:8.2f}, Price: ₹{old_price:8.2f} → ₹{avg_price:8.2f}")
                
                holding.quantity = Decimal(str(qty))
                holding.average_price = Decimal(str(avg_price))
                updated += 1
        else:
            # Create new
            security = Security.query.get(sec_id)
            symbol = security.symbol if security else f"ID:{sec_id}"
            print(f"  Creating: {symbol:15s} Qty: {qty:8.2f}, Price: ₹{avg_price:8.2f}")
            
            new_holding = Holding(
                client_id=client_id,
                security_id=sec_id,
                quantity=Decimal(str(qty)),
                average_price=Decimal(str(avg_price))
            )
            db.session.add(new_holding)
            created += 1
    
    # Delete holdings that don't exist in transactions
    for sec_id, holding in existing_holdings.items():
        if sec_id not in holdings_from_txns or holdings_from_txns[sec_id]['quantity'] <= 0.01:
            security = Security.query.get(sec_id)
            symbol = security.symbol if security else f"ID:{sec_id}"
            print(f"  Deleting: {symbol:15s} (no transactions or zero quantity)")
            db.session.delete(holding)
            deleted += 1
    
    print(f"\nSummary:")
    print(f"  Updated: {updated}")
    print(f"  Created: {created}")
    print(f"  Deleted: {deleted}")
    print(f"  Total changes: {updated + created + deleted}")
    
    return updated + created + deleted > 0


if __name__ == '__main__':
    app = create_app()
    
    with app.app_context():
        print("\n" + "="*80)
        print("RECALCULATING HOLDINGS FROM TRANSACTIONS")
        print("="*80)
        
        client_id = 26
        
        has_changes = recalculate_holdings_for_client(client_id)
        
        if has_changes:
            # Auto-confirm for script execution
            print("\n✅ Auto-confirming changes...")
            db.session.commit()
            print("✅ Changes committed successfully!")
        else:
            print("\n✓ No changes needed. Holdings already match transactions.")
        
        print("="*80)
