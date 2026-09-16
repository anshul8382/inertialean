#!/usr/bin/env python3
"""
Generate detailed report with recommendations for corporate action duplicates
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from extensions import db
from sqlalchemy import text, func
from models import CorporateAction, Security
import config
from datetime import date

def analyze_duplicates():
    app = Flask(__name__)
    app.config.from_object(config.Config)
    db.init_app(app)
    
    with app.app_context():
        # Find groups with same security_id and action_date
        query = db.session.query(
            CorporateAction.security_id,
            CorporateAction.action_date,
            func.count(CorporateAction.id).label('count')
        ).group_by(
            CorporateAction.security_id,
            CorporateAction.action_date
        ).having(
            func.count(CorporateAction.id) > 1
        ).order_by(
            CorporateAction.action_date.desc()
        )
        
        duplicates = query.all()
        
        if not duplicates:
            print("No duplicates found!")
            return
        
        report = []
        report.append("=" * 100)
        report.append("CORPORATE ACTION DUPLICATE ANALYSIS REPORT")
        report.append("=" * 100)
        report.append(f"\nGenerated: {date.today()}")
        report.append(f"Total duplicate groups: {len(duplicates)}")
        report.append("=" * 100)
        report.append("")
        
        for dup in duplicates:
            security_id, action_date, count = dup
            security = Security.query.get(security_id)
            symbol = security.symbol if security else f"ID:{security_id}"
            name = security.name if security else "Unknown"
            
            # Get all entries for this security/date combination
            entries = CorporateAction.query.filter_by(
                security_id=security_id,
                action_date=action_date
            ).order_by(CorporateAction.id).all()
            
            report.append("\n" + "=" * 100)
            report.append(f"GROUP: {symbol} - {name}")
            report.append(f"Date: {action_date}")
            report.append(f"Number of entries: {count}")
            report.append("-" * 100)
            
            # Analyze entries
            entry_details = []
            for i, entry in enumerate(entries, 1):
                entry_details.append({
                    'id': entry.id,
                    'type': entry.action_type,
                    'ratio': float(entry.ratio),
                    'description': entry.description or 'N/A',
                    'source': entry.source,
                    'active': entry.is_active,
                    'created': entry.created_at.strftime('%Y-%m-%d %H:%M') if entry.created_at else 'N/A'
                })
                report.append(f"\n  Entry {i}:")
                report.append(f"    ID: {entry.id}")
                report.append(f"    Type: {entry.action_type}")
                report.append(f"    Ratio: {entry.ratio}")
                report.append(f"    Description: {entry.description or 'N/A'}")
                report.append(f"    Source: {entry.source}")
                report.append(f"    Active: {entry.is_active}")
                report.append(f"    Created: {entry.created_at.strftime('%Y-%m-%d %H:%M') if entry.created_at else 'N/A'}")
            
            # Generate recommendations
            report.append("\n" + "-" * 100)
            report.append("ANALYSIS & RECOMMENDATIONS:")
            report.append("-" * 100)
            
            recommendations = analyze_group(entry_details, symbol, action_date)
            report.extend(recommendations)
            
            report.append("")
        
        # Summary section
        report.append("\n" + "=" * 100)
        report.append("SUMMARY & ACTION ITEMS")
        report.append("=" * 100)
        report.append("\n1. Review all groups marked as 'REVIEW REQUIRED'")
        report.append("2. Deactivate or delete entries marked as 'LIKELY DUPLICATE'")
        report.append("3. Correct action types for entries marked as 'WRONG TYPE'")
        report.append("4. Verify legitimate multiple actions are correctly configured")
        report.append("=" * 100)
        
        # Write report to file
        report_text = "\n".join(report)
        print(report_text)
        
        # Save to file
        report_file = f"/home/inertia/app/corporate_action_duplicate_report_{date.today()}.txt"
        with open(report_file, 'w') as f:
            f.write(report_text)
        
        print(f"\n\nReport saved to: {report_file}")

def analyze_group(entries, symbol, action_date):
    """Analyze a group of duplicate entries and provide recommendations"""
    recommendations = []
    
    # Check for same action type (definite duplicates)
    types = [e['type'] for e in entries]
    if len(types) != len(set(types)):
        # Same type appears multiple times
        type_counts = {}
        for t in types:
            type_counts[t] = type_counts.get(t, 0) + 1
        
        for action_type, count in type_counts.items():
            if count > 1:
                same_type_entries = [e for e in entries if e['type'] == action_type]
                recommendations.append(f"⚠️  DUPLICATE DETECTED: {count} entries with type '{action_type}'")
                recommendations.append(f"   → Keep: Entry ID {same_type_entries[0]['id']} (most recent or active)")
                for dup in same_type_entries[1:]:
                    recommendations.append(f"   → DELETE/DEACTIVATE: Entry ID {dup['id']} (duplicate)")
                recommendations.append("")
    
    # Check for inactive entries that might be duplicates
    inactive_entries = [e for e in entries if not e['active']]
    active_entries = [e for e in entries if e['active']]
    
    if inactive_entries and active_entries:
        recommendations.append(f"⚠️  INACTIVE ENTRIES FOUND: {len(inactive_entries)} inactive entry/entries")
        for inactive in inactive_entries:
            # Check if there's an active entry of same type
            matching_active = [e for e in active_entries if e['type'] == inactive['type']]
            if matching_active:
                recommendations.append(f"   → DELETE: Entry ID {inactive['id']} (inactive duplicate of active Entry ID {matching_active[0]['id']})")
            else:
                recommendations.append(f"   → REVIEW: Entry ID {inactive['id']} (inactive but no active match found)")
        recommendations.append("")
    
    # Check for DIVIDEND entries with "Face Value Split" or "Split" in description
    for entry in entries:
        desc_lower = entry['description'].lower() if entry['description'] else ''
        if entry['type'] == 'DIVIDEND' and ('split' in desc_lower or 'sub-division' in desc_lower):
            recommendations.append(f"⚠️  WRONG ACTION TYPE: Entry ID {entry['id']}")
            recommendations.append(f"   → Current: DIVIDEND, Should be: SPLIT")
            recommendations.append(f"   → Description indicates face value split, not dividend")
            recommendations.append(f"   → ACTION: Update action_type from DIVIDEND to SPLIT")
            recommendations.append("")
    
    # Check for legitimate multiple actions (Bonus + Dividend, Split + Dividend, etc.)
    unique_types = set(types)
    if len(unique_types) == len(entries):
        # All different types - likely legitimate
        if 'BONUS' in unique_types and 'DIVIDEND' in unique_types:
            recommendations.append("✓ LEGITIMATE: Bonus and Dividend on same date (common corporate action)")
            recommendations.append("   → Keep all entries - these are separate actions")
        elif 'SPLIT' in unique_types and 'DIVIDEND' in unique_types:
            # Need to check if DIVIDEND is actually a split
            dividend_entries = [e for e in entries if e['type'] == 'DIVIDEND']
            for div_entry in dividend_entries:
                desc_lower = div_entry['description'].lower() if div_entry['description'] else ''
                if 'split' in desc_lower or 'sub-division' in desc_lower:
                    recommendations.append(f"⚠️  POTENTIAL ISSUE: Entry ID {div_entry['id']} marked as DIVIDEND but description suggests SPLIT")
                    recommendations.append("   → Verify if this should be SPLIT type instead")
                else:
                    recommendations.append("✓ LEGITIMATE: Split and Dividend on same date (possible)")
                    recommendations.append("   → Keep all entries if both actions occurred")
        elif 'SPLIT' in unique_types and 'BONUS' in unique_types:
            recommendations.append("⚠️  REVIEW REQUIRED: Split and Bonus on same date (unusual)")
            recommendations.append("   → Verify if both actions actually occurred on this date")
            recommendations.append("   → Check if one entry is incorrect")
        else:
            recommendations.append("✓ LEGITIMATE: Multiple different action types on same date")
            recommendations.append("   → Keep all entries if all actions are valid")
    
    # Check source differences (MIGRATED vs FILE_UPLOAD vs MANUAL)
    sources = [e['source'] for e in entries]
    if len(set(sources)) > 1:
        recommendations.append(f"ℹ️  MIXED SOURCES: Entries from different sources ({', '.join(set(sources))})")
        file_upload_entries = [e for e in entries if e['source'] == 'FILE_UPLOAD']
        migrated_entries = [e for e in entries if e['source'] == 'MIGRATED']
        if file_upload_entries and migrated_entries:
            recommendations.append("   → FILE_UPLOAD entries are likely more recent/accurate")
            recommendations.append("   → Consider deactivating MIGRATED entries if they duplicate FILE_UPLOAD")
    
    # If no specific recommendations, mark for review
    if not recommendations:
        recommendations.append("⚠️  REVIEW REQUIRED: Multiple entries need manual verification")
        recommendations.append("   → Check if all entries are legitimate or if duplicates exist")
    
    return recommendations

if __name__ == '__main__':
    analyze_duplicates()

