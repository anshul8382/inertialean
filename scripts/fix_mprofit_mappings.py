#!/usr/bin/env python3
"""
Script to fix incorrect MProfit symbol mappings in the database.
Run this script to correct mappings that were auto-matched incorrectly.
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import create_app
from models import db, MProfitSymbolMap, Security
from datetime import datetime

# Mappings to fix: (raw_name, correct_nse_symbol)
FIXES = [
    ("Bharat Bond ETF - April 2031", "EBBETF0431"),
    ("Bharat Bond ETF - April 2030", "EBBETF0430"),
    ("Shree Cements", "SHREECEM"),
    ("Container Corporation", "CONCOR"),
    ("Dr Lal Pathlabs", "LALPATHLAB"),
    ("Dr. Lal Pathlabs", "LALPATHLAB"),
    ("Dr Lal PathLabs", "LALPATHLAB"),
]

def fix_mappings():
    """Fix incorrect MProfit symbol mappings."""
    app = create_app()
    
    with app.app_context():
        print("Fixing MProfit symbol mappings...")
        
        for raw_name, correct_symbol in FIXES:
            # Find the mapping
            mapping = MProfitSymbolMap.query.filter_by(raw_name=raw_name).first()
            
            if not mapping:
                print(f"  ⚠️  Mapping not found for: {raw_name}")
                continue
            
            # Find the correct security
            security = Security.query.filter_by(symbol=correct_symbol).first()
            
            if not security:
                print(f"  ⚠️  Security not found with symbol: {correct_symbol}")
                continue
            
            # Update the mapping
            old_symbol = mapping.nse_symbol
            old_security_id = mapping.security_id
            
            mapping.security_id = security.id
            mapping.nse_symbol = security.symbol
            mapping.is_manual = True
            mapping.updated_at = datetime.utcnow()
            mapping.notes = f"Fixed: was {old_symbol} (security_id: {old_security_id})"
            
            print(f"  ✅ Fixed: {raw_name}")
            print(f"     Old: {old_symbol} (security_id: {old_security_id})")
            print(f"     New: {security.symbol} (security_id: {security.id})")
        
        # Commit changes
        db.session.commit()
        print("\n✅ All mappings fixed successfully!")

if __name__ == '__main__':
    fix_mappings()

