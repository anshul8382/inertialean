#!/usr/bin/env python3
"""
Script to show current holdings for Manish Dwivedi.
This script will display all current holdings with their details.
"""

import sys
import os
sys.path.append('/home/inertia/app')

from datetime import date, datetime
from flask import Flask
from models import db, Holding, Client, Security
from sqlalchemy import text
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def show_current_holdings(client_id):
    """Show current holdings for a client"""
    logger.info(f"Getting current holdings for client {client_id}")
    
    # Get holdings with security details
    holdings = db.session.query(Holding, Security).join(
        Security, Holding.security_id == Security.id
    ).filter(Holding.client_id == client_id).all()
    
    logger.info(f"Found {len(holdings)} current holdings")
    
    holdings_data = []
    for holding, security in holdings:
        holdings_data.append({
            'id': holding.id,
            'security_symbol': security.symbol,
            'security_name': security.name,
            'quantity': float(holding.quantity),
            'average_price': float(holding.average_price),
            'updated_at': holding.updated_at
        })
    
    return holdings_data

def main():
    """Main function to show holdings for Manish Dwivedi"""
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
            # Get current holdings
            holdings_data = show_current_holdings(client_id)
            
            # Print summary
            print("\n" + "="*80)
            print("CURRENT HOLDINGS FOR MANISH DWIVEDI")
            print("="*80)
            print(f"Client: {client.name} (ID: {client_id})")
            print(f"Date: {date.today()}")
            print(f"Total holdings: {len(holdings_data)}")
            print("="*80)
            
            # Sort by quantity descending
            holdings_data.sort(key=lambda x: x['quantity'], reverse=True)
            
            print(f"{'Symbol':<12} {'Name':<25} {'Quantity':<12} {'Avg Price':<12} {'Updated'}")
            print("-" * 70)
            
            total_value = 0
            for holding in holdings_data:
                value = holding['quantity'] * holding['average_price']
                total_value += value
                
                updated_str = holding['updated_at'].strftime('%Y-%m-%d') if holding['updated_at'] else "N/A"
                
                print(f"{holding['security_symbol']:<12} {holding['security_name']:<25} "
                      f"{holding['quantity']:<12.2f} {holding['average_price']:<12.2f} "
                      f"{updated_str}")
            
            print("-" * 70)
            print(f"Total Portfolio Value: ₹{total_value:,.2f}")
            print("="*80)
            
            # Show top 10 holdings by value
            print("\nTOP 10 HOLDINGS BY VALUE:")
            print("-" * 50)
            for i, holding in enumerate(holdings_data[:10], 1):
                value = holding['quantity'] * holding['average_price']
                print(f"{i:2d}. {holding['security_symbol']:<12} ₹{value:,.2f} ({holding['quantity']:.2f} units)")
            
        except Exception as e:
            logger.error(f"Error getting holdings: {e}")
            raise

if __name__ == "__main__":
    main()

