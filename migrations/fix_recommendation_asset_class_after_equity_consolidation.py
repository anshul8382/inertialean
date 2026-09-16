#!/usr/bin/env python3
"""
Post-Migration Fix: Update Recommendation asset_class_id after Equity Consolidation

This script ensures that recommendations with asset_class_id pointing to old equity
classes (Large Cap, Mid Cap, Small Cap) are updated to point to the consolidated
Equity asset class.

Run this AFTER migrate_asset_classes_to_equity.py if recommendations already have
asset_class_id set.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extensions import db
from sqlalchemy import text
from models import Recommendation, Security, AssetClass

def fix_recommendation_asset_classes():
    """Update recommendation asset_class_id to match consolidated equity classes"""
    
    print("=" * 60)
    print("Fixing Recommendation asset_class_id after Equity Consolidation")
    print("=" * 60)
    
    try:
        # Find the Equity asset class
        equity_class = AssetClass.query.filter_by(name='Equity').first()
        if not equity_class:
            print("⚠️  WARNING: Equity asset class not found. Skipping fix.")
            print("   This is okay if equity consolidation hasn't been run yet.")
            return
        
        equity_id = equity_class.id
        print(f"✅ Found Equity asset class (ID: {equity_id})")
        
        # Find old equity class IDs (Large Cap, Mid Cap, Small Cap)
        old_equity_classes = AssetClass.query.filter(
            AssetClass.name.in_(['Large Cap', 'Mid Cap', 'Small Cap'])
        ).all()
        
        old_equity_ids = [ac.id for ac in old_equity_classes]
        
        if not old_equity_ids:
            print("✅ No old equity classes found. Equity consolidation may already be complete.")
        else:
            print(f"📋 Found old equity classes: {[ac.name for ac in old_equity_classes]}")
            print(f"   IDs: {old_equity_ids}")
        
        # Strategy 1: Update recommendations with old equity class IDs to use Equity
        if old_equity_ids:
            old_recs = Recommendation.query.filter(
                Recommendation.asset_class_id.in_(old_equity_ids)
            ).count()
            
            if old_recs > 0:
                print(f"\n🔄 Found {old_recs} recommendations with old equity class IDs")
                print("   Updating to use consolidated Equity class...")
                
                result = db.session.execute(
                    text("""
                        UPDATE recommendation 
                        SET asset_class_id = :equity_id 
                        WHERE asset_class_id IN :old_ids
                    """),
                    {'equity_id': equity_id, 'old_ids': tuple(old_equity_ids)}
                )
                db.session.commit()
                print(f"✅ Updated {result.rowcount} recommendations to use Equity (ID: {equity_id})")
            else:
                print("✅ No recommendations found with old equity class IDs")
        
        # Strategy 2: Populate asset_class_id from Security for recommendations where it's NULL
        null_asset_class_recs = Recommendation.query.filter(
            Recommendation.asset_class_id.is_(None)
        ).count()
        
        if null_asset_class_recs > 0:
            print(f"\n🔄 Found {null_asset_class_recs} recommendations with NULL asset_class_id")
            print("   Populating from Security relationship...")
            
            result = db.session.execute(
                text("""
                    UPDATE recommendation r
                    JOIN security s ON r.security_id = s.id
                    SET r.asset_class_id = s.asset_class_id
                    WHERE r.asset_class_id IS NULL
                """)
            )
            db.session.commit()
            print(f"✅ Populated asset_class_id for {result.rowcount} recommendations from Security table")
        else:
            print("✅ All recommendations already have asset_class_id set")
        
        # Strategy 3: Verify and fix any mismatches between recommendation and security
        print("\n🔍 Checking for mismatches between recommendation and security asset_class_id...")
        
        mismatch_count = db.session.execute(
            text("""
                SELECT COUNT(*) 
                FROM recommendation r
                JOIN security s ON r.security_id = s.id
                WHERE r.asset_class_id IS NOT NULL 
                  AND s.asset_class_id IS NOT NULL
                  AND r.asset_class_id != s.asset_class_id
            """)
        ).scalar()
        
        if mismatch_count > 0:
            print(f"⚠️  Found {mismatch_count} recommendations where asset_class_id doesn't match security")
            print("   Options:")
            print("   1. Keep recommendation asset_class_id (user may have modified)")
            print("   2. Update to match security asset_class_id (run with --sync flag)")
            # For now, we'll log but not auto-fix (user decision)
        else:
            print("✅ No mismatches found - all recommendations match their securities")
        
        print("\n" + "=" * 60)
        print("✅ Fix completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        db.session.rollback()
        print(f"\n❌ Error during fix: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == '__main__':
    from main import create_app
    import os
    
    # Use TestConfig if FLASK_ENV is set to test
    if os.environ.get('FLASK_ENV') == 'test':
        from config import TestConfig
        app = create_app(TestConfig)
        print("Running fix on TEST database")
    else:
        app = create_app()
        print("Running fix on default database")
    
    with app.app_context():
        from extensions import db
        from sqlalchemy import text
        # Verify which database we're using
        result = db.session.execute(text("SELECT DATABASE()"))
        db_name = result.scalar()
        print(f"Connected to database: {db_name}\n")
        
        fix_recommendation_asset_classes()

