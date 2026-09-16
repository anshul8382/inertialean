#!/usr/bin/env python3
"""
Apply Kotak Bank corporate action retroactively to all client holdings
This script finds the Kotak Bank corporate action and updates all client holdings
"""
import sys
import os
import argparse

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run import create_app
from models import db, Security, CorporateAction, Holding, Client
from services.data.corporate_action_service import CorporateActionService
from datetime import date
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def apply_kotak_bank_corporate_action(auto_confirm=False):
    """Apply Kotak Bank corporate action to all client holdings"""
    app = create_app()
    with app.app_context():
        print("="*80)
        print("APPLYING KOTAK BANK CORPORATE ACTION RETROACTIVELY")
        print("="*80)
        
        # Find Kotak Bank security
        kotak_security = Security.query.filter(
            Security.symbol.like('%KOTAK%')
        ).first()
        
        if not kotak_security:
            print("❌ Kotak Bank security not found!")
            return
        
        print(f"\n✅ Found Kotak Bank security:")
        print(f"   ID: {kotak_security.id}")
        print(f"   Symbol: {kotak_security.symbol}")
        print(f"   Name: {kotak_security.name}")
        
        # Find Kotak Bank corporate actions
        kotak_actions = CorporateAction.query.filter_by(
            security_id=kotak_security.id
        ).order_by(CorporateAction.action_date.desc()).all()
        
        if not kotak_actions:
            print("\n❌ No corporate actions found for Kotak Bank!")
            return
        
        print(f"\n📋 Found {len(kotak_actions)} corporate action(s) for Kotak Bank:")
        for action in kotak_actions:
            print(f"   - {action.action_type} on {action.action_date} (Ratio: {action.ratio})")
        
        # Get the most recent action (or all if user wants to apply all)
        action_to_apply = kotak_actions[0]
        print(f"\n🎯 Applying most recent corporate action:")
        print(f"   Type: {action_to_apply.action_type}")
        print(f"   Date: {action_to_apply.action_date}")
        print(f"   Ratio: {action_to_apply.ratio}")
        
        # Count clients with holdings
        clients_with_holdings = db.session.query(Client.id, Client.name).join(
            Holding, Client.id == Holding.client_id
        ).filter(
            Holding.security_id == kotak_security.id,
            Holding.quantity > 0
        ).distinct().all()
        
        print(f"\n👥 Found {len(clients_with_holdings)} clients with Kotak Bank holdings")
        
        if not clients_with_holdings:
            print("⚠️  No clients have holdings in Kotak Bank. Nothing to update.")
            return
        
        # Show clients before update
        print("\n📊 Clients with Kotak Bank holdings (before update):")
        for client_id, client_name in clients_with_holdings:
            holding = Holding.query.filter_by(
                client_id=client_id,
                security_id=kotak_security.id
            ).first()
            if holding:
                print(f"   - {client_name} (ID: {client_id}): {holding.quantity} shares @ ₹{holding.average_price}")
        
        # Confirm before proceeding
        if not auto_confirm:
            print("\n" + "="*80)
            try:
                response = input("Do you want to proceed with updating holdings? (yes/no): ")
                if response.lower() not in ['yes', 'y']:
                    print("❌ Update cancelled by user")
                    return
            except EOFError:
                print("⚠️  No input available. Use --yes flag to auto-confirm.")
                return
        else:
            print("\n" + "="*80)
            print("✅ Auto-confirmation enabled. Proceeding with update...")
        
        # Update holdings using the service method
        print("\n🔄 Updating holdings...")
        try:
            update_result = CorporateActionService.update_holdings_for_security_after_corporate_action(
                kotak_security.id
            )
            
            print("\n✅ Holdings update completed!")
            print(f"   Clients updated: {update_result.get('clients_updated', 0)}")
            print(f"   Holdings updated: {update_result.get('holdings_updated', 0)}")
            if update_result.get('clients_failed', 0) > 0:
                print(f"   ⚠️  Clients failed: {update_result.get('clients_failed', 0)}")
            
            # Show clients after update
            print("\n📊 Clients with Kotak Bank holdings (after update):")
            for client_id, client_name in clients_with_holdings:
                holding = Holding.query.filter_by(
                    client_id=client_id,
                    security_id=kotak_security.id
                ).first()
                if holding and holding.quantity > 0:
                    print(f"   - {client_name} (ID: {client_id}): {holding.quantity} shares @ ₹{holding.average_price}")
                elif holding:
                    print(f"   - {client_name} (ID: {client_id}): Holding removed (zero quantity)")
                else:
                    print(f"   - {client_name} (ID: {client_id}): No holding found")
            
        except Exception as e:
            print(f"\n❌ Error updating holdings: {str(e)}")
            logger.error(f"Error in apply_kotak_bank_corporate_action: {str(e)}", exc_info=True)
            raise
        
        print("\n" + "="*80)
        print("✅ Script completed successfully!")
        print("="*80)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Apply Kotak Bank corporate action retroactively')
    parser.add_argument('--yes', '-y', action='store_true', 
                       help='Auto-confirm and proceed without prompting')
    args = parser.parse_args()
    
    apply_kotak_bank_corporate_action(auto_confirm=args.yes)

