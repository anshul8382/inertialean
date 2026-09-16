#!/usr/bin/env python3
"""
Refresh holdings for all clients using forward calculation
This script recalculates holdings from transactions and corporate actions
and updates the holdings table for all clients.
"""
import os
import sys
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
from main import create_app
from services.forward_holding_calculation_service import get_client_portfolio_by_date
from models import db, Client, Holding, Security
from datetime import date
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def refresh_all_holdings():
    """Refresh holdings for all clients using forward calculation"""
    app = create_app()
    with app.app_context():
        print("="*80)
        print("REFRESHING HOLDINGS FOR ALL CLIENTS")
        print("Using Forward Calculation from Transactions + Corporate Actions")
        print("="*80)
        
        # Get all clients
        clients = Client.query.order_by(Client.name).all()
        total_clients = len(clients)
        
        if total_clients == 0:
            print("❌ No clients found!")
            return
        
        print(f"\n📊 Found {total_clients} clients")
        print(f"📅 Calculating holdings as of: {date.today()}")
        print("\n" + "="*80)
        
        # Statistics
        clients_processed = 0
        clients_failed = 0
        total_holdings_updated = 0
        total_holdings_created = 0
        total_holdings_deleted = 0
        
        # Process each client
        for idx, client in enumerate(clients, 1):
            try:
                print(f"\n[{idx}/{total_clients}] Processing: {client.name} (ID: {client.id})")
                
                # Get forward calculation results
                portfolio = get_client_portfolio_by_date(client.id, date.today())
                
                if 'error' in portfolio:
                    print(f"  ❌ Error: {portfolio['error']}")
                    clients_failed += 1
                    continue
                
                # Get current holdings count for this client
                current_holdings_count = Holding.query.filter_by(client_id=client.id).count()
                
                # Clear existing holdings for this client
                deleted_count = Holding.query.filter_by(client_id=client.id).delete()
                db.session.flush()
                total_holdings_deleted += deleted_count
                
                # Create new holdings from forward calculation
                holdings_created = 0
                holdings_updated = 0
                
                for holding_data in portfolio['holdings']:
                    # Only create holdings with positive quantity
                    if holding_data['quantity'] > 0:
                        security = Security.query.get(holding_data['security_id'])
                        symbol = security.symbol if security else f"ID{holding_data['security_id']}"
                        
                        # Create new holding
                        holding = Holding(
                            client_id=client.id,
                            security_id=holding_data['security_id'],
                            quantity=holding_data['quantity'],
                            average_price=holding_data['average_price']
                        )
                        db.session.add(holding)
                        holdings_created += 1
                
                # Commit for this client
                db.session.commit()
                
                total_holdings_created += holdings_created
                clients_processed += 1
                
                print(f"  ✅ Updated: {holdings_created} holdings (deleted {deleted_count} old, created {holdings_created} new)")
                print(f"     Portfolio Value: ₹{portfolio.get('total_value', 0):,.2f}")
                
            except Exception as e:
                logger.error(f"Error processing client {client.id} ({client.name}): {str(e)}", exc_info=True)
                print(f"  ❌ Error: {str(e)}")
                db.session.rollback()
                clients_failed += 1
                continue
        
        # Final summary
        print("\n" + "="*80)
        print("REFRESH COMPLETE - SUMMARY")
        print("="*80)
        print(f"✅ Clients processed successfully: {clients_processed}")
        print(f"❌ Clients failed: {clients_failed}")
        print(f"📊 Total holdings deleted: {total_holdings_deleted}")
        print(f"📊 Total holdings created: {total_holdings_created}")
        print(f"📈 Success rate: {(clients_processed/total_clients*100):.1f}%")
        print("="*80)

if __name__ == "__main__":
    refresh_all_holdings()

