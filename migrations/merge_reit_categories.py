"""
Migration script to merge duplicate REIT asset classes into a single "REITs" category.

Merges: "REIT/InvIT", "REIT/INVIT", "REITS", "REITs", "REIT", "Invits", etc. -> "REITs"

This migration:
1. Finds all AssetClass records with REIT/InvIT-related names
2. Creates or finds the canonical "REITs" AssetClass
3. Updates all tables that reference asset_class_id: Security, Recommendation,
   AssetClassDistribution, AssetAllocation, SecurityAllocationModel,
   ModelAssignmentSecurityModel, AssetClassCustomization, BillingRateStructure,
   InvoiceLineItem (asset_class_id and asset_class_name)
4. Handles unique constraints (e.g. merge duplicate distributions / model assignments)
5. Deletes the old AssetClass records
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from extensions import db
from models import (
    AssetClass,
    Security,
    AssetClassDistribution,
    Recommendation,
    AssetAllocation,
    SecurityAllocationModel,
    ModelAssignmentSecurityModel,
    AssetClassCustomization,
    BillingRateStructure,
    InvoiceLineItem,
)

# Canonical name for merged REIT asset class
REITS_CANONICAL_NAME = "REITs"


def merge_reit_categories():
    """Merge REIT/InvIT, REITS, and all REIT-related asset classes into REITs."""
    app = create_app()

    with app.app_context():
        try:
            print("Starting REIT category merge migration...")

            # Step 1: Find all REIT-related asset classes (REITs, REITS, REIT, REIT/InvIT, Invits, etc.)
            reit_classes = AssetClass.query.filter(
                db.or_(
                    AssetClass.name.like("%REIT%"),
                    AssetClass.name.like("%INVIT%"),
                    AssetClass.name.like("%Invits%"),
                )
            ).all()

            print(f"Found {len(reit_classes)} REIT-related asset classes:")
            for ac in reit_classes:
                print(f"  - {ac.name} (ID: {ac.id})")

            if not reit_classes:
                print("No REIT asset classes found. Creating 'REITs' asset class...")
                reits_class = AssetClass(
                    name=REITS_CANONICAL_NAME,
                    description="Real Estate Investment Trusts and Infrastructure Investment Trusts",
                    created_by=1,
                )
                db.session.add(reits_class)
                db.session.flush()
                print(f"Created 'REITs' asset class (ID: {reits_class.id})")
            else:
                reits_class = AssetClass.query.filter_by(name=REITS_CANONICAL_NAME).first()
                if not reits_class:
                    reits_class = reit_classes[0]
                    reits_class.name = REITS_CANONICAL_NAME
                    reits_class.description = (
                        "Real Estate Investment Trusts and Infrastructure Investment Trusts"
                    )
                    db.session.flush()
                    print(f"Renamed to 'REITs' (ID: {reits_class.id})")
                else:
                    print(f"Found existing 'REITs' asset class (ID: {reits_class.id})")

            old_reit_ids = [ac.id for ac in reit_classes if ac.id != reits_class.id]
            if not old_reit_ids:
                print("No duplicate REIT asset classes to merge.")
                db.session.commit()
                return

            # Step 2: Update Security
            security_count = Security.query.filter(Security.asset_class_id.in_(old_reit_ids)).update(
                {Security.asset_class_id: reits_class.id}, synchronize_session=False
            )
            print(f"\nUpdated {security_count} Security records to REITs")

            # Step 3: AssetClassDistribution – merge by session, then update
            for old_id in old_reit_ids:
                old_dists = AssetClassDistribution.query.filter_by(asset_class_id=old_id).all()
                for old_dist in old_dists:
                    existing = AssetClassDistribution.query.filter_by(
                        session_id=old_dist.session_id,
                        asset_class_id=reits_class.id,
                    ).first()
                    if existing:
                        if old_dist.current_weight is not None:
                            existing.current_weight = (existing.current_weight or 0) + (
                                old_dist.current_weight or 0
                            )
                        if old_dist.target_weight is not None:
                            existing.target_weight = (existing.target_weight or 0) + (
                                old_dist.target_weight or 0
                            )
                        if old_dist.allocated_amount is not None:
                            existing.allocated_amount = (existing.allocated_amount or 0) + (
                                old_dist.allocated_amount or 0
                            )
                        if old_dist.required_change is not None:
                            existing.required_change = (existing.required_change or 0) + (
                                old_dist.required_change or 0
                            )
                        db.session.delete(old_dist)
                    else:
                        old_dist.asset_class_id = reits_class.id
            print("Updated AssetClassDistribution records")

            # Step 4: Recommendation
            rec_count = Recommendation.query.filter(
                Recommendation.asset_class_id.in_(old_reit_ids)
            ).update(
                {Recommendation.asset_class_id: reits_class.id}, synchronize_session=False
            )
            print(f"Updated {rec_count} Recommendation records to REITs")

            # Step 5: AssetAllocation
            aa_count = AssetAllocation.query.filter(
                AssetAllocation.asset_class_id.in_(old_reit_ids)
            ).update(
                {AssetAllocation.asset_class_id: reits_class.id}, synchronize_session=False
            )
            print(f"Updated {aa_count} AssetAllocation records to REITs")

            # Step 6: SecurityAllocationModel
            sam_count = SecurityAllocationModel.query.filter(
                SecurityAllocationModel.asset_class_id.in_(old_reit_ids)
            ).update(
                {SecurityAllocationModel.asset_class_id: reits_class.id},
                synchronize_session=False,
            )
            print(f"Updated {sam_count} SecurityAllocationModel records to REITs")

            # Step 7: ModelAssignmentSecurityModel – update then dedupe by (model_assignment_id, asset_class_id)
            masm_rows = ModelAssignmentSecurityModel.query.filter(
                ModelAssignmentSecurityModel.asset_class_id.in_(old_reit_ids)
            ).all()
            for row in masm_rows:
                existing = ModelAssignmentSecurityModel.query.filter_by(
                    model_assignment_id=row.model_assignment_id,
                    asset_class_id=reits_class.id,
                ).first()
                if existing:
                    # Prefer keeping the one with a security_model_id if any
                    if row.security_model_id and not existing.security_model_id:
                        existing.security_model_id = row.security_model_id
                    db.session.delete(row)
                else:
                    row.asset_class_id = reits_class.id
            print(f"Updated ModelAssignmentSecurityModel records to REITs")

            # Step 8: AssetClassCustomization
            acc_count = AssetClassCustomization.query.filter(
                AssetClassCustomization.asset_class_id.in_(old_reit_ids)
            ).update(
                {AssetClassCustomization.asset_class_id: reits_class.id},
                synchronize_session=False,
            )
            print(f"Updated {acc_count} AssetClassCustomization records to REITs")

            # Step 9: BillingRateStructure
            brs_count = BillingRateStructure.query.filter(
                BillingRateStructure.asset_class_id.in_(old_reit_ids)
            ).update(
                {BillingRateStructure.asset_class_id: reits_class.id},
                synchronize_session=False,
            )
            print(f"Updated {brs_count} BillingRateStructure records to REITs")

            # Step 10: InvoiceLineItem (asset_class_id and asset_class_name)
            line_items = InvoiceLineItem.query.filter(
                InvoiceLineItem.asset_class_id.in_(old_reit_ids)
            ).all()
            for item in line_items:
                item.asset_class_id = reits_class.id
                item.asset_class_name = REITS_CANONICAL_NAME
            print(f"Updated {len(line_items)} InvoiceLineItem records to REITs")

            # Step 11: Delete old AssetClass records
            delete_count = AssetClass.query.filter(AssetClass.id.in_(old_reit_ids)).delete(
                synchronize_session=False
            )
            print(f"\nDeleted {delete_count} old AssetClass records")

            db.session.commit()
            print("\n✓ Migration completed successfully!")
            print(f"All REIT-related data is now consolidated under '{REITS_CANONICAL_NAME}' (ID: {reits_class.id})")

        except Exception as e:
            db.session.rollback()
            print(f"\n✗ Error during migration: {str(e)}")
            import traceback
            traceback.print_exc()
            raise


if __name__ == "__main__":
    merge_reit_categories()

