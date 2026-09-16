#!/usr/bin/env python3
"""
Temporary script to populate client fields from cashflow data:
1. Starting AUA - from first cashflow month (convert negative investment amounts to positive)
2. Date of Joining - date of first cashflow entry

Usage:
    python scripts/populate_client_fields_from_cashflow.py [--dry-run] [--force]
    
Options:
    --dry-run: Show what would be updated without making changes
    --force: Update even if fields already have values
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import create_app
from models import db, Client, Cashflow
from decimal import Decimal
from sqlalchemy import func
from datetime import date
import argparse

def populate_client_fields(dry_run=False, force=False):
    """Populate Starting AUA and Date of Joining from cashflow data"""
    
    app = create_app()
    
    with app.app_context():
        print("=" * 60)
        print("Populating Client Fields from Cashflow Data")
        print("=" * 60)
        print(f"Mode: {'DRY RUN (no changes will be made)' if dry_run else 'LIVE UPDATE'}")
        print(f"Force update: {'Yes (will overwrite existing values)' if force else 'No (only update empty fields)'}")
        print("=" * 60)
        print()
        
        # Get all clients
        clients = Client.query.all()
        print(f"Found {len(clients)} clients to process\n")
        
        updated_count = 0
        skipped_count = 0
        no_cashflow_count = 0
        errors = []
        
        for client in clients:
            try:
                # Check if we should skip this client
                if not force:
                    # Skip if both fields already have values
                    if client.starting_aua and client.starting_aua > 0 and client.date_of_joining:
                        skipped_count += 1
                        continue
                
                # Get first cashflow for this client (ordered by date)
                first_cashflow = Cashflow.query.filter_by(
                    client_id=client.id
                ).order_by(Cashflow.date.asc()).first()
                
                if not first_cashflow:
                    no_cashflow_count += 1
                    if not force or (force and (not client.starting_aua or client.starting_aua == 0) and not client.date_of_joining):
                        continue
                
                # Determine Starting AUA
                new_starting_aua = None
                if first_cashflow:
                    amount = float(first_cashflow.amount)
                    # If amount is negative (investment), convert to positive for Starting AUA
                    if amount < 0:
                        new_starting_aua = abs(amount)
                    else:
                        # If positive (withdrawal), Starting AUA should be 0 or the amount?
                        # Based on user requirement, only negative amounts should be used
                        new_starting_aua = Decimal('0')
                else:
                    # No cashflow - set to 0 if forcing
                    if force:
                        new_starting_aua = Decimal('0')
                
                # Determine Date of Joining
                new_date_of_joining = None
                if first_cashflow:
                    # Extract date from datetime
                    if isinstance(first_cashflow.date, date):
                        new_date_of_joining = first_cashflow.date
                    else:
                        new_date_of_joining = first_cashflow.date.date()
                elif force:
                    # No cashflow - leave as None
                    new_date_of_joining = None
                
                # Check if update is needed
                needs_update = False
                update_fields = []
                
                if new_starting_aua is not None:
                    current_aua = float(client.starting_aua) if client.starting_aua else 0
                    if force or not client.starting_aua or client.starting_aua == 0:
                        if float(new_starting_aua) != current_aua:
                            needs_update = True
                            update_fields.append(f"Starting AUA: ₹{current_aua:,.2f} → ₹{float(new_starting_aua):,.2f}")
                
                if new_date_of_joining is not None:
                    if force or not client.date_of_joining:
                        current_doj = client.date_of_joining.strftime('%Y-%m-%d') if client.date_of_joining else 'None'
                        new_doj_str = new_date_of_joining.strftime('%Y-%m-%d') if new_date_of_joining else 'None'
                        if client.date_of_joining != new_date_of_joining:
                            needs_update = True
                            update_fields.append(f"Date of Joining: {current_doj} → {new_doj_str}")
                
                if needs_update:
                    print(f"Client: {client.name} (ID: {client.id}, Email: {client.email})")
                    for field_update in update_fields:
                        print(f"  - {field_update}")
                    
                    if not dry_run:
                        if new_starting_aua is not None and (force or not client.starting_aua or client.starting_aua == 0):
                            client.starting_aua = new_starting_aua
                        if new_date_of_joining is not None and (force or not client.date_of_joining):
                            client.date_of_joining = new_date_of_joining
                    
                    updated_count += 1
                    print()
                else:
                    skipped_count += 1
                    
            except Exception as e:
                error_msg = f"Error processing client {client.id} ({client.name}): {str(e)}"
                errors.append(error_msg)
                print(f"❌ {error_msg}")
                print()
        
        # Commit changes if not dry run
        if not dry_run and updated_count > 0:
            try:
                db.session.commit()
                print("=" * 60)
                print("✅ Changes committed successfully!")
            except Exception as e:
                db.session.rollback()
                print("=" * 60)
                print(f"❌ Error committing changes: {str(e)}")
                return False
        elif dry_run:
            print("=" * 60)
            print("ℹ️  DRY RUN - No changes were made")
        
        # Print summary
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"✅ Updated: {updated_count} clients")
        print(f"⏭️  Skipped: {skipped_count} clients")
        print(f"📭 No cashflow: {no_cashflow_count} clients")
        if errors:
            print(f"❌ Errors: {len(errors)} clients")
            print("\nErrors:")
            for error in errors[:10]:  # Show first 10 errors
                print(f"  - {error}")
            if len(errors) > 10:
                print(f"  ... and {len(errors) - 10} more errors")
        print("=" * 60)
        
        return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Populate client fields from cashflow data')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Show what would be updated without making changes')
    parser.add_argument('--force', action='store_true',
                       help='Update even if fields already have values')
    
    args = parser.parse_args()
    
    success = populate_client_fields(dry_run=args.dry_run, force=args.force)
    sys.exit(0 if success else 1)

