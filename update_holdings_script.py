#!/usr/bin/env python3
"""
Script to get current holdings for Manish Dwivedi and update his holdings table.
This script will:
1. Get current holdings from the portfolio reconstruction service
2. Update the holdings table with current data
3. Provide audit trail of changes
"""

import sys
import os
sys.path.append('/home/inertia/app')

from datetime import date, datetime
from flask import Flask
from models import db, Holding, Client
from services.forward_holding_calculation_service import get_holding_quantity_by_date
from sqlalchemy import text
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_current_holdings_from_reconstruction(client_id, as_of_date=None):
    """Get current holdings using portfolio reconstruction"""
    if as_of_date is None:
        as_of_date = date.today()
    
    logger.info(f"Getting holdings for client {client_id} as of {as_of_date}")
    
    # Get holdings using forward calculation service
    holdings_data = []
    
    # Query to get all securities that have transactions for this client
    query = text("""
        SELECT DISTINCT s.symbol as security_symbol, s.name as security_name
        FROM transaction t
        JOIN security s ON t.security_id = s.id
        WHERE t.client_id = :client_id
        AND t.transaction_date <= :as_of_date
    """)
    
    result = db.session.execute(query, {
        'client_id': client_id,
        'as_of_date': as_of_date
    })
    
    securities = result.fetchall()
    logger.info(f"Found {len(securities)} securities with transactions")
    
    for security_symbol, security_name in securities:
        try:
            quantity = get_holding_quantity_by_date(
                client_id=client_id,
                security_symbol=security_symbol,
                as_of_date=as_of_date
            )
            
            if quantity > 0:
                holdings_data.append({
                    'security_symbol': security_symbol,
                    'security_name': security_name,
                    'quantity': quantity,
                    'as_of_date': as_of_date
                })
                logger.info(f"  {security_symbol}: {quantity} units")
                
        except Exception as e:
            logger.error(f"Error getting holdings for {security_symbol}: {e}")
    
    return holdings_data

def update_holdings_table(client_id, new_holdings_data):
    """Update the holdings table with new data"""
    logger.info(f"Updating holdings table for client {client_id}")
    
    # Get existing holdings
    existing_holdings = Holding.query.filter_by(client_id=client_id).all()
    existing_dict = {h.security_symbol: h for h in existing_holdings}
    
    # Track changes
    added = []
    updated = []
    removed = []
    
    # Update or add new holdings
    for holding_data in new_holdings_data:
        security_symbol = holding_data['security_symbol']
        quantity = holding_data['quantity']
        
        if security_symbol in existing_dict:
            # Update existing holding
            existing_holding = existing_dict[security_symbol]
            old_quantity = existing_holding.quantity
            if old_quantity != quantity:
                existing_holding.quantity = quantity
                existing_holding.updated_at = datetime.now()
                updated.append({
                    'security': security_symbol,
                    'old_quantity': old_quantity,
                    'new_quantity': quantity
                })
                logger.info(f"Updated {security_symbol}: {old_quantity} -> {quantity}")
        else:
            # Add new holding
            new_holding = Holding(
                client_id=client_id,
                security_symbol=security_symbol,
                security_name=holding_data['security_name'],
                quantity=quantity,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            db.session.add(new_holding)
            added.append({
                'security': security_symbol,
                'quantity': quantity
            })
            logger.info(f"Added {security_symbol}: {quantity}")
    
    # Remove holdings that are no longer present
    new_symbols = {h['security_symbol'] for h in new_holdings_data}
    for existing_holding in existing_holdings:
        if existing_holding.security_symbol not in new_symbols:
            removed.append({
                'security': existing_holding.security_symbol,
                'quantity': existing_holding.quantity
            })
            logger.info(f"Removing {existing_holding.security_symbol}: {existing_holding.quantity}")
            db.session.delete(existing_holding)
    
    return added, updated, removed

def main():
    """Main function to update holdings for Manish Dwivedi"""
    # Create Flask app context
    app = Flask(__name__)
    app.config.from_object('config.Config')
    
    with app.app_context():
        # Initialize database
        db.init_app(app)
        
        # Get Manish Dwivedi's client ID
        client = Client.query.filter_by(name='Manish Dwivedi').first()
        if not client:
            logger.error("Client 'Manish Dwivedi' not found")
            return
        
        client_id = client.id
        logger.info(f"Found client: {client.name} (ID: {client_id})")
        
        try:
            # Get current holdings using portfolio reconstruction
            logger.info("Step 1: Getting current holdings from portfolio reconstruction...")
            current_holdings = get_current_holdings_from_reconstruction(client_id)
            logger.info(f"Found {len(current_holdings)} current holdings")
            
            # Update holdings table
            logger.info("Step 2: Updating holdings table...")
            added, updated, removed = update_holdings_table(client_id, current_holdings)
            
            # Commit changes
            db.session.commit()
            logger.info("Changes committed to database")
            
            # Print summary
            print("\n" + "="*60)
            print("HOLDINGS UPDATE SUMMARY")
            print("="*60)
            print(f"Client: {client.name} (ID: {client_id})")
            print(f"Date: {date.today()}")
            print(f"Total current holdings: {len(current_holdings)}")
            print(f"Added: {len(added)}")
            print(f"Updated: {len(updated)}")
            print(f"Removed: {len(removed)}")
            
            if added:
                print("\nADDED HOLDINGS:")
                for item in added:
                    print(f"  + {item['security']}: {item['quantity']}")
            
            if updated:
                print("\nUPDATED HOLDINGS:")
                for item in updated:
                    print(f"  ~ {item['security']}: {item['old_quantity']} -> {item['new_quantity']}")
            
            if removed:
                print("\nREMOVED HOLDINGS:")
                for item in removed:
                    print(f"  - {item['security']}: {item['quantity']}")
            
            print("\nCURRENT HOLDINGS:")
            for holding in current_holdings:
                print(f"  {holding['security_symbol']}: {holding['quantity']}")
            
            print("="*60)
            
        except Exception as e:
            logger.error(f"Error updating holdings: {e}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    main()
