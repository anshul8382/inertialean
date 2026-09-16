#!/usr/bin/env python3
"""
Utility script to clean up orphaned cashflows for a client
Orphaned cashflows are cashflows that exist but have no corresponding transactions
"""
import sys
from main import create_app
from extensions import db
from models import Client, Cashflow, Transaction
from datetime import datetime

def cleanup_orphaned_cashflows(client_id=None, client_name=None, dry_run=True):
    """
    Clean up cashflows that don't have corresponding transactions
    
    Args:
        client_id: Specific client ID to clean up
        client_name: Client name to search for (partial match)
        dry_run: If True, only show what would be deleted without actually deleting
    """
    app = create_app()
    
    with app.app_context():
        # Find client
        if client_id:
            client = Client.query.get(client_id)
        elif client_name:
            client = Client.query.filter(Client.name.ilike(f'%{client_name}%')).first()
        else:
            print("Error: Must provide either client_id or client_name")
            return
        
        if not client:
            print(f"Client not found")
            return
        
        print(f"\n=== Cleaning up cashflows for: {client.name} (ID: {client.id}) ===\n")
        
        # Get all transactions for this client
        transactions = Transaction.query.filter_by(client_id=client.id).all()
        transaction_dates = {t.transaction_date.date() for t in transactions}
        
        print(f"Found {len(transactions)} transactions")
        print(f"Transaction dates: {sorted(transaction_dates)[:10]}..." if len(transaction_dates) > 10 else f"Transaction dates: {sorted(transaction_dates)}")
        
        # Get all cashflows for this client
        all_cashflows = Cashflow.query.filter_by(client_id=client.id).all()
        print(f"\nFound {len(all_cashflows)} total cashflows")
        
        # Find cashflows that don't correspond to any transaction date
        orphaned_cashflows = []
        for cf in all_cashflows:
            cf_date = cf.date.date() if hasattr(cf.date, 'date') else cf.date
            if cf_date not in transaction_dates:
                orphaned_cashflows.append(cf)
        
        print(f"\nFound {len(orphaned_cashflows)} orphaned cashflows (no matching transaction dates)")
        
        if orphaned_cashflows:
            print("\nOrphaned cashflow details:")
            total_amount = 0
            for cf in orphaned_cashflows[:20]:  # Show first 20
                cf_date = cf.date.date() if hasattr(cf.date, 'date') else cf.date
                print(f"  ID: {cf.id}, Date: {cf_date}, Amount: {cf.amount}, Type: {cf.type}")
                total_amount += float(cf.amount)
            if len(orphaned_cashflows) > 20:
                print(f"  ... and {len(orphaned_cashflows) - 20} more")
            print(f"\nTotal orphaned amount: {total_amount:,.2f}")
            
            if not dry_run:
                # Delete orphaned cashflows
                for cf in orphaned_cashflows:
                    db.session.delete(cf)
                
                db.session.commit()
                print(f"\n✓ Deleted {len(orphaned_cashflows)} orphaned cashflows")
            else:
                print(f"\n[DRY RUN] Would delete {len(orphaned_cashflows)} cashflows")
                print("Run with dry_run=False to actually delete")
        else:
            print("\n✓ No orphaned cashflows found")
        
        # Show summary
        remaining_cashflows = Cashflow.query.filter_by(client_id=client.id).count()
        print(f"\nRemaining cashflows after cleanup: {remaining_cashflows}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Clean up orphaned cashflows for a client')
    parser.add_argument('--client-id', type=int, help='Client ID')
    parser.add_argument('--client-name', type=str, help='Client name (partial match)')
    parser.add_argument('--execute', action='store_true', help='Actually delete (default is dry run)')
    
    args = parser.parse_args()
    
    if not args.client_id and not args.client_name:
        # Default to Soumya Chatterjee if no args
        print("No client specified, defaulting to Soumya Chatterjee")
        args.client_name = 'soumya'
    
    cleanup_orphaned_cashflows(
        client_id=args.client_id,
        client_name=args.client_name,
        dry_run=not args.execute
    )

