#!/usr/bin/env python3
"""
Corporate Action Monitoring Script
Detects manual entry problems and data integrity issues
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import create_app
from models import db, Client, Security, Transaction, Holding, CorporateAction
from datetime import datetime, date, timedelta
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def validate_corporate_actions():
    """Validate corporate actions for duplicates and anomalies"""
    app = create_app()
    with app.app_context():
        logger.info("🔍 Validating Corporate Actions...")
        
        issues = []
        
        # Check for duplicate corporate actions
        securities_with_actions = db.session.query(
            Security.symbol, 
            db.func.count(CorporateAction.id).label('action_count')
        ).join(
            CorporateAction, Security.id == CorporateAction.security_id
        ).group_by(Security.id).all()
        
        for symbol, count in securities_with_actions:
            if count > 1:
                security = Security.query.filter_by(symbol=symbol).first()
                actions = CorporateAction.query.filter_by(security_id=security.id).order_by(CorporateAction.action_date).all()
                
                # Check for multiple actions within 30 days
                for i in range(len(actions) - 1):
                    if (actions[i+1].action_date - actions[i].action_date).days <= 30:
                        issues.append({
                            'type': 'duplicate_actions',
                            'security': symbol,
                            'date1': actions[i].action_date,
                            'date2': actions[i+1].action_date,
                            'days_diff': (actions[i+1].action_date - actions[i].action_date).days,
                            'action1': f"{actions[i].action_type} {actions[i].ratio}",
                            'action2': f"{actions[i+1].action_type} {actions[i+1].ratio}"
                        })
                
                # Check for excessive split ratios
                total_split_ratio = 1.0
                for action in actions:
                    if action.action_type == 'SPLIT':
                        total_split_ratio *= float(action.ratio)
                
                if total_split_ratio > 10.0:
                    issues.append({
                        'type': 'excessive_split',
                        'security': symbol,
                        'total_ratio': total_split_ratio,
                        'actions': [f"{a.action_date}: {a.action_type} {a.ratio}" for a in actions]
                    })
        
        # Check for non-standard split ratios
        split_actions = CorporateAction.query.filter(CorporateAction.action_type == 'SPLIT').all()
        standard_ratios = [1.5, 2.0, 3.0, 5.0, 10.0]
        
        for action in split_actions:
            if float(action.ratio) not in standard_ratios:
                security = Security.query.get(action.security_id)
                issues.append({
                    'type': 'non_standard_ratio',
                    'security': security.symbol if security else 'Unknown',
                    'date': action.action_date,
                    'ratio': float(action.ratio),
                    'standard_ratios': standard_ratios
                })
        
        return issues

def reconcile_holdings():
    """Reconcile holdings with transactions and corporate actions"""
    app = create_app()
    with app.app_context():
        logger.info("🔍 Reconciling Holdings with Transactions...")
        
        issues = []
        
        # Get all clients with holdings
        clients_with_holdings = db.session.query(Client.id, Client.name).join(Holding).distinct().all()
        
        for client_id, client_name in clients_with_holdings:
            holdings = Holding.query.filter_by(client_id=client_id).all()
            
            for holding in holdings:
                security = Security.query.get(holding.security_id)
                if not security:
                    continue
                
                # Get all BUY transactions for this client and security
                buy_transactions = Transaction.query.filter(
                    Transaction.client_id == client_id,
                    Transaction.security_id == holding.security_id,
                    Transaction.type == 'BUY'
                ).all()
                
                # Calculate expected quantity from transactions
                transaction_quantity = sum(float(tx.quantity) for tx in buy_transactions)
                transaction_cost = sum(float(tx.quantity) * float(tx.price) for tx in buy_transactions)
                
                # Get corporate actions for this security
                corporate_actions = CorporateAction.query.filter_by(security_id=holding.security_id).all()
                
                # Apply corporate actions to get expected post-split quantity
                expected_quantity = transaction_quantity
                for action in corporate_actions:
                    if action.action_type == 'SPLIT':
                        expected_quantity *= float(action.ratio)
                    elif action.action_type == 'BONUS':
                        expected_quantity *= (1.0 + float(action.ratio))
                
                # Compare with actual holding
                actual_quantity = float(holding.quantity)
                discrepancy = abs(actual_quantity - expected_quantity)
                discrepancy_percent = (discrepancy / expected_quantity * 100) if expected_quantity > 0 else 0
                
                if discrepancy_percent > 10:  # Flag if discrepancy > 10%
                    issues.append({
                        'type': 'quantity_discrepancy',
                        'client': client_name,
                        'security': security.symbol,
                        'actual_quantity': actual_quantity,
                        'expected_quantity': expected_quantity,
                        'discrepancy': discrepancy,
                        'discrepancy_percent': discrepancy_percent,
                        'corporate_actions': [f"{a.action_date}: {a.action_type} {a.ratio}" for a in corporate_actions]
                    })
                
                # Check average price consistency
                if transaction_quantity > 0:
                    expected_avg_price = transaction_cost / transaction_quantity
                    actual_avg_price = float(holding.average_price)
                    price_discrepancy = abs(actual_avg_price - expected_avg_price)
                    price_discrepancy_percent = (price_discrepancy / expected_avg_price * 100) if expected_avg_price > 0 else 0
                    
                    if price_discrepancy_percent > 20:  # Flag if price discrepancy > 20%
                        issues.append({
                            'type': 'price_discrepancy',
                            'client': client_name,
                            'security': security.symbol,
                            'actual_avg_price': actual_avg_price,
                            'expected_avg_price': expected_avg_price,
                            'discrepancy': price_discrepancy,
                            'discrepancy_percent': price_discrepancy_percent
                        })
        
        return issues

def detect_pnl_anomalies():
    """Detect P&L anomalies that might indicate corporate action issues"""
    app = create_app()
    with app.app_context():
        logger.info("🔍 Detecting P&L Anomalies...")
        
        issues = []
        
        # Get all holdings with current prices
        holdings = db.session.query(Holding).join(Security).filter(
            Security.current_price.isnot(None)
        ).all()
        
        for holding in holdings:
            security = Security.query.get(holding.security_id)
            client = Client.query.get(holding.client_id)
            
            if not security or not client:
                continue
            
            # Calculate P&L
            current_value = float(holding.quantity) * float(security.current_price)
            cost_basis = float(holding.quantity) * float(holding.average_price)
            pnl = current_value - cost_basis
            pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0
            
            # Flag unusual P&L percentages
            if pnl_percent > 200:  # Flag if P&L > 200%
                issues.append({
                    'type': 'excessive_profit',
                    'client': client.name,
                    'security': security.symbol,
                    'pnl_percent': pnl_percent,
                    'pnl_amount': pnl,
                    'quantity': float(holding.quantity),
                    'avg_price': float(holding.average_price),
                    'current_price': float(security.current_price)
                })
            
            if pnl_percent < -50:  # Flag if P&L < -50%
                issues.append({
                    'type': 'excessive_loss',
                    'client': client.name,
                    'security': security.symbol,
                    'pnl_percent': pnl_percent,
                    'pnl_amount': pnl,
                    'quantity': float(holding.quantity),
                    'avg_price': float(holding.average_price),
                    'current_price': float(security.current_price)
                })
        
        return issues

def generate_monitoring_report():
    """Generate comprehensive monitoring report"""
    app = create_app()
    with app.app_context():
        logger.info("📊 Generating Corporate Action Monitoring Report...")
        
        # Run all validations
        corporate_action_issues = validate_corporate_actions()
        holding_issues = reconcile_holdings()
        pnl_issues = detect_pnl_anomalies()
        
        # Generate report
        report = {
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'corporate_action_issues': len(corporate_action_issues),
                'holding_issues': len(holding_issues),
                'pnl_issues': len(pnl_issues),
                'total_issues': len(corporate_action_issues) + len(holding_issues) + len(pnl_issues)
            },
            'issues': {
                'corporate_actions': corporate_action_issues,
                'holdings': holding_issues,
                'pnl': pnl_issues
            }
        }
        
        return report

def main():
    """Main function to run monitoring"""
    print("🔍 Corporate Action Monitoring Script")
    print("=" * 50)
    
    try:
        report = generate_monitoring_report()
        
        print(f"\n📊 Monitoring Report - {report['timestamp']}")
        print(f"Total Issues Found: {report['summary']['total_issues']}")
        print(f"  - Corporate Action Issues: {report['summary']['corporate_action_issues']}")
        print(f"  - Holding Issues: {report['summary']['holding_issues']}")
        print(f"  - P&L Issues: {report['summary']['pnl_issues']}")
        
        if report['summary']['total_issues'] > 0:
            print(f"\n🚨 Issues Detected:")
            
            # Corporate Action Issues
            if report['issues']['corporate_actions']:
                print(f"\n1. Corporate Action Issues:")
                for issue in report['issues']['corporate_actions']:
                    print(f"   - {issue['type']}: {issue.get('security', 'N/A')}")
                    if issue['type'] == 'duplicate_actions':
                        print(f"     Multiple actions within {issue['days_diff']} days")
                    elif issue['type'] == 'excessive_split':
                        print(f"     Total split ratio: {issue['total_ratio']}")
                    elif issue['type'] == 'non_standard_ratio':
                        print(f"     Non-standard ratio: {issue['ratio']}")
            
            # Holding Issues
            if report['issues']['holdings']:
                print(f"\n2. Holding Reconciliation Issues:")
                for issue in report['issues']['holdings']:
                    print(f"   - {issue['type']}: {issue['client']} - {issue['security']}")
                    if issue['type'] == 'quantity_discrepancy':
                        print(f"     Quantity discrepancy: {issue['discrepancy_percent']:.1f}%")
                    elif issue['type'] == 'price_discrepancy':
                        print(f"     Price discrepancy: {issue['discrepancy_percent']:.1f}%")
            
            # P&L Issues
            if report['issues']['pnl']:
                print(f"\n3. P&L Anomaly Issues:")
                for issue in report['issues']['pnl']:
                    print(f"   - {issue['type']}: {issue['client']} - {issue['security']}")
                    print(f"     P&L: {issue['pnl_percent']:.1f}% (₹{issue['pnl_amount']:,.0f})")
        else:
            print(f"\n✅ No issues detected - All validations passed!")
        
        print(f"\n🎯 Recommendations:")
        print(f"1. Review and fix any issues identified above")
        print(f"2. Implement automated monitoring for continuous validation")
        print(f"3. Set up alerts for critical issues")
        print(f"4. Regular reconciliation of holdings with transactions")
        print(f"5. Validate corporate actions against external sources")
        
    except Exception as e:
        logger.error(f"Error running monitoring script: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()





