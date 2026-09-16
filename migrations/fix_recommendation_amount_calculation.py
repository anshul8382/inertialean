#!/usr/bin/env python3
"""
Migration: Fix recommendation amount calculation to ensure amount = quantity * current_price

This script recalculates the amount field for all recommendations in the database
to ensure it matches quantity * current_price from the security.

Run this script to fix existing recommendations:
    python migrations/fix_recommendation_amount_calculation.py
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from models import Recommendation, Security, db
from extensions import db as db_ext

def fix_recommendation_amounts():
    """Fix amount calculation for all recommendations"""
    app = create_app()
    
    with app.app_context():
        # Get all recommendations with quantity and security
        recommendations = Recommendation.query.filter(
            Recommendation.quantity.isnot(None),
            Recommendation.quantity != 0,
            Recommendation.security_id.isnot(None)
        ).all()
        
        fixed_count = 0
        error_count = 0
        skipped_count = 0
        
        print(f"Found {len(recommendations)} recommendations to check...")
        
        for rec in recommendations:
            try:
                # Get security
                security = Security.query.get(rec.security_id)
                if not security:
                    skipped_count += 1
                    print(f"  Skipping recommendation {rec.id}: Security {rec.security_id} not found")
                    continue
                
                # Get current price
                current_price = float(security.current_price) if security.current_price else 0.0
                if current_price <= 0:
                    skipped_count += 1
                    print(f"  Skipping recommendation {rec.id}: Security {security.symbol} has no current_price")
                    continue
                
                # Calculate correct amount
                quantity = float(rec.quantity) if rec.quantity else 0.0
                if quantity == 0:
                    skipped_count += 1
                    continue
                
                correct_amount = quantity * current_price
                current_amount = float(rec.amount) if rec.amount else 0.0
                
                # Check if amount needs fixing (allow small rounding differences)
                amount_diff = abs(correct_amount - current_amount)
                if amount_diff > 0.01:  # More than 1 paisa difference
                    # Update amount
                    rec.amount = correct_amount
                    fixed_count += 1
                    print(f"  Fixed recommendation {rec.id} (Security: {security.symbol}): "
                          f"Amount {current_amount:.2f} -> {correct_amount:.2f} "
                          f"(Qty: {quantity}, Price: {current_price:.2f})")
                else:
                    skipped_count += 1
                    
            except Exception as e:
                error_count += 1
                print(f"  Error fixing recommendation {rec.id}: {e}")
                continue
        
        # Commit all changes
        if fixed_count > 0:
            try:
                db.session.commit()
                print(f"\n✅ Successfully fixed {fixed_count} recommendations")
            except Exception as e:
                db.session.rollback()
                print(f"\n❌ Error committing changes: {e}")
                return False
        else:
            print(f"\n✅ No recommendations needed fixing")
        
        print(f"\nSummary:")
        print(f"  Fixed: {fixed_count}")
        print(f"  Skipped: {skipped_count}")
        print(f"  Errors: {error_count}")
        
        return True

if __name__ == '__main__':
    print("=" * 60)
    print("Fix Recommendation Amount Calculation")
    print("=" * 60)
    print("\nThis script will recalculate amount = quantity * current_price")
    print("for all recommendations in the database.\n")
    
    response = input("Do you want to continue? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("Aborted.")
        sys.exit(0)
    
    success = fix_recommendation_amounts()
    
    if success:
        print("\n✅ Migration completed successfully!")
    else:
        print("\n❌ Migration failed. Please check errors above.")
        sys.exit(1)

