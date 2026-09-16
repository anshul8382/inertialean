"""
Migration Script: Fix Cashflow Types Consistency

This script standardizes all cashflow types to INFLOW/OUTFLOW and fixes any incorrect type assignments.

Rules:
- Negative amount = Investment (money in) = INFLOW
- Positive amount = Withdrawal (money out) = OUTFLOW

The script:
1. Finds all cashflows with INVESTMENT/WITHDRAWAL types and converts them to INFLOW/OUTFLOW
2. Fixes any cashflows with incorrect INFLOW/OUTFLOW assignments based on amount sign
3. Logs all changes to a file for verification

Usage:
    python scripts/migration/fix_cashflow_types_consistency.py [--dry-run] [--log-file <path>]
"""

#!/usr/bin/env python3

import sys
import os
from datetime import datetime
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from main import create_app
from models import db, Cashflow, Client

def fix_cashflow_types(dry_run=False, log_file_path=None):
    """
    Fix cashflow types to ensure consistency.
    
    Args:
        dry_run: If True, don't commit changes, just report what would be changed
        log_file_path: Path to log file. If None, uses default path with timestamp.
    """
    app = create_app()
    
    if log_file_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file_path = f'cashflow_type_fix_{timestamp}.log'
    
    log_entries = []
    log_entries.append("=" * 80)
    log_entries.append(f"Cashflow Type Consistency Fix - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_entries.append("=" * 80)
    log_entries.append(f"Mode: {'DRY RUN (no changes will be committed)' if dry_run else 'LIVE (changes will be committed)'}")
    log_entries.append("")
    log_entries.append("Rules:")
    log_entries.append("  - Negative amount = Investment (money in) = INFLOW")
    log_entries.append("  - Positive amount = Withdrawal (money out) = OUTFLOW")
    log_entries.append("")
    log_entries.append("-" * 80)
    log_entries.append("")
    
    with app.app_context():
        # Get all cashflows
        all_cashflows = Cashflow.query.all()
        
        changes = []
        stats = {
            'total_cashflows': len(all_cashflows),
            'investment_to_inflow': 0,
            'withdrawal_to_outflow': 0,
            'inflow_fixed': 0,
            'outflow_fixed': 0,
            'no_change_needed': 0
        }
        
        for cashflow in all_cashflows:
            amount = float(cashflow.amount)
            old_type = cashflow.type
            client = Client.query.get(cashflow.client_id)
            client_name = client.name if client else f"Client ID {cashflow.client_id}"
            
            # Determine correct type based on amount sign
            if amount < 0:
                correct_type = 'INFLOW'
            elif amount > 0:
                correct_type = 'OUTFLOW'
            else:
                # Zero amount - keep existing type or default to OUTFLOW
                correct_type = old_type if old_type in ['INFLOW', 'OUTFLOW'] else 'OUTFLOW'
            
            # Check if change is needed
            if old_type != correct_type:
                # Handle legacy types
                if old_type == 'INVESTMENT':
                    new_type = 'INFLOW'
                    stats['investment_to_inflow'] += 1
                elif old_type == 'WITHDRAWAL':
                    new_type = 'OUTFLOW'
                    stats['withdrawal_to_outflow'] += 1
                elif old_type == 'INFLOW' and correct_type == 'OUTFLOW':
                    new_type = 'OUTFLOW'
                    stats['inflow_fixed'] += 1
                elif old_type == 'OUTFLOW' and correct_type == 'INFLOW':
                    new_type = 'INFLOW'
                    stats['outflow_fixed'] += 1
                else:
                    new_type = correct_type
                
                changes.append({
                    'cashflow_id': cashflow.id,
                    'client_id': cashflow.client_id,
                    'client_name': client_name,
                    'date': cashflow.date.strftime('%Y-%m-%d'),
                    'amount': amount,
                    'old_type': old_type,
                    'new_type': new_type,
                    'description': cashflow.description or ''
                })
                
                if not dry_run:
                    cashflow.type = new_type
            else:
                stats['no_change_needed'] += 1
        
        # Log all changes
        log_entries.append(f"Summary:")
        log_entries.append(f"  Total cashflows processed: {stats['total_cashflows']}")
        log_entries.append(f"  INVESTMENT → INFLOW: {stats['investment_to_inflow']}")
        log_entries.append(f"  WITHDRAWAL → OUTFLOW: {stats['withdrawal_to_outflow']}")
        log_entries.append(f"  INFLOW → OUTFLOW (fixed): {stats['inflow_fixed']}")
        log_entries.append(f"  OUTFLOW → INFLOW (fixed): {stats['outflow_fixed']}")
        log_entries.append(f"  No change needed: {stats['no_change_needed']}")
        log_entries.append(f"  Total changes: {len(changes)}")
        log_entries.append("")
        log_entries.append("-" * 80)
        log_entries.append("")
        
        if changes:
            log_entries.append("Detailed Changes:")
            log_entries.append("")
            log_entries.append(f"{'ID':<8} {'Client ID':<10} {'Client Name':<30} {'Date':<12} {'Amount':<15} {'Old Type':<15} {'New Type':<15}")
            log_entries.append("-" * 120)
            
            for change in changes:
                log_entries.append(
                    f"{change['cashflow_id']:<8} "
                    f"{change['client_id']:<10} "
                    f"{change['client_name'][:28]:<30} "
                    f"{change['date']:<12} "
                    f"{change['amount']:>14.2f} "
                    f"{change['old_type']:<15} "
                    f"{change['new_type']:<15}"
                )
            
            log_entries.append("")
            log_entries.append("-" * 80)
            log_entries.append("")
            log_entries.append("Change Details (CSV format for easy verification):")
            log_entries.append("")
            log_entries.append("Cashflow ID,Client ID,Client Name,Date,Amount,Old Type,New Type,Description")
            for change in changes:
                description = (change['description'] or '').replace(',', ';').replace('\n', ' ').replace('\r', ' ')
                log_entries.append(
                    f"{change['cashflow_id']},"
                    f"{change['client_id']},"
                    f'"{change["client_name"]}",'
                    f"{change['date']},"
                    f"{change['amount']},"
                    f"{change['old_type']},"
                    f"{change['new_type']},"
                    f'"{description}"'
                )
        else:
            log_entries.append("No changes needed - all cashflow types are already consistent!")
        
        log_entries.append("")
        log_entries.append("=" * 80)
        
        # Write log file
        log_content = "\n".join(log_entries)
        with open(log_file_path, 'w') as f:
            f.write(log_content)
        
        print("\n".join(log_entries))
        print(f"\nLog file saved to: {log_file_path}")
        
        if not dry_run and changes:
            try:
                db.session.commit()
                print(f"\n✓ Successfully updated {len(changes)} cashflow records in database.")
            except Exception as e:
                db.session.rollback()
                print(f"\n✗ Error committing changes: {e}")
                raise
        elif dry_run:
            print(f"\n✓ Dry run completed. {len(changes)} changes would be made.")
            print("   Run without --dry-run to apply changes.")
        
        return {
            'changes': changes,
            'stats': stats,
            'log_file': log_file_path
        }

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Fix cashflow types consistency')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Run in dry-run mode (no changes will be committed)')
    parser.add_argument('--log-file', type=str, default=None,
                       help='Path to log file (default: cashflow_type_fix_TIMESTAMP.log)')
    
    args = parser.parse_args()
    
    try:
        fix_cashflow_types(dry_run=args.dry_run, log_file_path=args.log_file)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

