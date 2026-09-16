"""
Real-time Alert API for Corporate Actions and Price Discrepancies
Provides endpoints for alert management and real-time monitoring
"""

import logging
from datetime import datetime, date, timedelta
from flask import Blueprint, request, jsonify
from sqlalchemy import func, desc, and_

from api.core.response import APIResponse
from api.core.exceptions import ValidationError, NotFoundError
from models import db, Client, Security, Transaction, HistoricalPrice, Holding, CorporateAction

logger = logging.getLogger(__name__)
alerts_bp = Blueprint('alerts', __name__)

def _calculate_holding_period(client_id, security_id):
    """Calculate holding period in days based on first buy transaction"""
    # Get first BUY transaction for this client and security
    first_buy = Transaction.query.filter(
        Transaction.client_id == client_id,
        Transaction.security_id == security_id,
        Transaction.type == 'BUY'
    ).order_by(Transaction.transaction_date.asc()).first()
    
    if first_buy:
        # Calculate days from first buy to today
        holding_period = (date.today() - first_buy.transaction_date.date()).days
        return max(0, holding_period)
    
    return 0

@alerts_bp.route('/discrepancies', methods=['GET'])
def get_discrepancies():
    """Get current discrepancies that need attention"""
    try:
        client_id = request.args.get('client_id', type=int)
        security_id = request.args.get('security_id', type=int)
        alert_type = request.args.get('type', 'all')
        
        discrepancies = []
        
        # Get all clients or specific client
        if client_id:
            clients = [Client.query.get_or_404(client_id)]
        else:
            clients = Client.query.all()
        
        for client in clients:
            holdings = Holding.query.filter_by(client_id=client.id).all()
            
            for holding in holdings:
                security = Security.query.get(holding.security_id)
                if not security:
                    continue
                
                # Skip if specific security requested and doesn't match
                if security_id and security.id != security_id:
                    continue
                
                # Get BUY transactions
                buy_transactions = Transaction.query.filter(
                    Transaction.client_id == client.id,
                    Transaction.security_id == holding.security_id,
                    Transaction.type == 'BUY'
                ).all()
                
                if not buy_transactions:
                    continue
                
                # Calculate expected quantity
                transaction_quantity = sum(float(tx.quantity) for tx in buy_transactions)
                transaction_cost = sum(float(tx.quantity) * float(tx.price) for tx in buy_transactions)
                
                # Apply corporate actions
                corporate_actions = CorporateAction.query.filter_by(security_id=holding.security_id).all()
                expected_quantity = transaction_quantity
                for action in corporate_actions:
                    if action.action_type == 'SPLIT':
                        expected_quantity *= float(action.ratio)
                    elif action.action_type == 'BONUS':
                        expected_quantity *= (1.0 + float(action.ratio))
                
                # Check quantity discrepancy
                actual_quantity = float(holding.quantity)
                quantity_discrepancy = abs(actual_quantity - expected_quantity)
                quantity_discrepancy_percent = (quantity_discrepancy / expected_quantity * 100) if expected_quantity > 0 else 0
                
                # Check price discrepancy
                if transaction_quantity > 0:
                    expected_avg_price = transaction_cost / transaction_quantity
                    actual_avg_price = float(holding.average_price)
                    price_discrepancy = abs(actual_avg_price - expected_avg_price)
                    price_discrepancy_percent = (price_discrepancy / expected_avg_price * 100) if expected_avg_price > 0 else 0
                else:
                    price_discrepancy_percent = 0
                
                # Calculate P&L
                if security.current_price:
                    current_value = actual_quantity * float(security.current_price)
                    cost_basis = actual_quantity * actual_avg_price
                    pnl = current_value - cost_basis
                    pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0
                    
                    # Calculate holding period and annualized return
                    holding_period_days = _calculate_holding_period(client.id, security.id)
                    holding_period_years = holding_period_days / 365.25 if holding_period_days > 0 else 0
                    
                    annualized_return = 0
                    if holding_period_years > 0 and cost_basis > 0:
                        annualized_return = ((current_value / cost_basis) ** (1 / holding_period_years) - 1) * 100
                else:
                    pnl_percent = 0
                    holding_period_days = 0
                    holding_period_years = 0
                    annualized_return = 0
                
                # Add discrepancy if it meets criteria
                discrepancy = {
                    'client_id': client.id,
                    'client_name': client.name,
                    'security_id': security.id,
                    'security_symbol': security.symbol,
                    'security_name': security.name,
                    'quantity_discrepancy_percent': round(quantity_discrepancy_percent, 2),
                    'price_discrepancy_percent': round(price_discrepancy_percent, 2),
                    'pnl_percent': round(pnl_percent, 2),
                    'expected_quantity': round(expected_quantity, 2),
                    'actual_quantity': round(actual_quantity, 2),
                    'expected_avg_price': round(expected_avg_price, 2) if transaction_quantity > 0 else 0,
                    'actual_avg_price': round(actual_avg_price, 2),
                    'current_price': round(float(security.current_price), 2) if security.current_price else 0,
                    'holding_period_days': holding_period_days,
                    'holding_period_years': round(holding_period_years, 2),
                    'annualized_return': round(annualized_return, 2),
                    'corporate_actions': [
                        {
                            'date': action.action_date.isoformat(),
                            'type': action.action_type,
                            'ratio': float(action.ratio)
                        } for action in corporate_actions
                    ],
                    'alerts': []
                }
                
                # Add specific alerts
                if quantity_discrepancy_percent > 10:
                    discrepancy['alerts'].append({
                        'type': 'quantity_discrepancy',
                        'severity': 'HIGH',
                        'message': f"Quantity discrepancy: {quantity_discrepancy_percent:.1f}%"
                    })
                
                if price_discrepancy_percent > 20:
                    discrepancy['alerts'].append({
                        'type': 'price_discrepancy',
                        'severity': 'HIGH',
                        'message': f"Price discrepancy: {price_discrepancy_percent:.1f}%"
                    })
                
                # Enhanced P&L anomaly detection with holding period
                if pnl_percent > 200:
                    if holding_period_days < 365:
                        discrepancy['alerts'].append({
                            'type': 'excessive_profit',
                            'severity': 'HIGH',
                            'message': f"Excessive profit with short holding period: {pnl_percent:.1f}% ({holding_period_days} days)"
                        })
                    elif annualized_return > 25:
                        discrepancy['alerts'].append({
                            'type': 'excessive_profit',
                            'severity': 'MEDIUM',
                            'message': f"Excessive annual return: {annualized_return:.1f}% (holding {holding_period_days} days)"
                        })
                    # Large profit with reasonable annual return is OK - no alert
                
                if pnl_percent < -50:
                    discrepancy['alerts'].append({
                        'type': 'excessive_loss',
                        'severity': 'HIGH',
                        'message': f"Excessive loss: {pnl_percent:.1f}%"
                    })
                
                # Filter by alert type
                if alert_type != 'all':
                    if not any(alert['type'] == alert_type for alert in discrepancy['alerts']):
                        continue
                
                # Only include if there are alerts
                if discrepancy['alerts']:
                    discrepancies.append(discrepancy)
        
        # Sort by severity and discrepancy percentage
        discrepancies.sort(key=lambda x: (
            max([0 if alert['severity'] == 'LOW' else 1 if alert['severity'] == 'MEDIUM' else 2 for alert in x['alerts']]),
            max([discrepancy['quantity_discrepancy_percent'], discrepancy['price_discrepancy_percent']])
        ), reverse=True)
        
        return APIResponse(
            message="Discrepancies retrieved successfully",
            data={
                "discrepancies": discrepancies,
                "total_count": len(discrepancies),
                "filters": {
                    "client_id": client_id,
                    "security_id": security_id,
                    "type": alert_type
                }
            }
        ).to_response()
        
    except Exception as e:
        logger.error(f"Error getting discrepancies: {e}")
        return APIResponse(
            message="Error retrieving discrepancies",
            success=False,
            error=str(e)
        ).to_response()

@alerts_bp.route('/price-changes', methods=['GET'])
def get_price_changes():
    """Get sharp price changes that might indicate missing corporate actions"""
    try:
        days = request.args.get('days', 7, type=int)
        threshold = request.args.get('threshold', 10.0, type=float)
        
        price_changes = []
        
        # Get all securities with current prices
        securities = Security.query.filter(Security.current_price.isnot(None)).all()
        
        for security in securities:
            # Get historical prices for the specified period
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            
            # Get prices for start and end dates
            start_price_data = HistoricalPrice.query.filter(
                HistoricalPrice.security_id == security.id,
                HistoricalPrice.date >= start_date,
                HistoricalPrice.date <= end_date
            ).order_by(HistoricalPrice.date.asc()).first()
            
            end_price_data = HistoricalPrice.query.filter(
                HistoricalPrice.security_id == security.id,
                HistoricalPrice.date >= start_date,
                HistoricalPrice.date <= end_date
            ).order_by(HistoricalPrice.date.desc()).first()
            
            if not start_price_data or not end_price_data:
                continue
            
            start_price = float(start_price_data.close_price)
            end_price = float(end_price_data.close_price)
            
            # Calculate price change
            price_change_percent = abs((end_price - start_price) / start_price * 100)
            
            if price_change_percent > threshold:
                # Check for corporate actions in this period
                corporate_actions = CorporateAction.query.filter(
                    CorporateAction.security_id == security.id,
                    CorporateAction.action_date >= start_date,
                    CorporateAction.action_date <= end_date
                ).all()
                
                # Check for consistent drops (might indicate split)
                recent_prices = HistoricalPrice.query.filter(
                    HistoricalPrice.security_id == security.id,
                    HistoricalPrice.date >= start_date,
                    HistoricalPrice.date <= end_date
                ).order_by(HistoricalPrice.date.desc()).limit(3).all()
                
                consistent_drop = False
                if len(recent_prices) >= 3:
                    prices = [float(p.close_price) for p in recent_prices]
                    if prices[0] > prices[1] > prices[2]:
                        consistent_drop = True
                
                price_change = {
                    'security_id': security.id,
                    'security_symbol': security.symbol,
                    'security_name': security.name,
                    'start_date': start_price_data.date.isoformat(),
                    'end_date': end_price_data.date.isoformat(),
                    'start_price': round(start_price, 2),
                    'end_price': round(end_price, 2),
                    'price_change_percent': round(price_change_percent, 2),
                    'has_corporate_actions': len(corporate_actions) > 0,
                    'consistent_drop': consistent_drop,
                    'corporate_actions': [
                        {
                            'date': action.action_date.isoformat(),
                            'type': action.action_type,
                            'ratio': float(action.ratio)
                        } for action in corporate_actions
                    ],
                    'alerts': []
                }
                
                # Add specific alerts
                if not corporate_actions and price_change_percent > threshold:
                    price_change['alerts'].append({
                        'type': 'sharp_price_change',
                        'severity': 'HIGH',
                        'message': f"Sharp price change: {price_change_percent:.1f}% - Possible missing corporate action"
                    })
                
                if consistent_drop and not corporate_actions:
                    price_change['alerts'].append({
                        'type': 'consistent_price_drop',
                        'severity': 'MEDIUM',
                        'message': f"Consistent price drop: {price_change_percent:.1f}% - Possible split action"
                    })
                
                # Only include if there are alerts
                if price_change['alerts']:
                    price_changes.append(price_change)
        
        # Sort by price change percentage
        price_changes.sort(key=lambda x: x['price_change_percent'], reverse=True)
        
        return APIResponse(
            message="Price changes retrieved successfully",
            data={
                "price_changes": price_changes,
                "total_count": len(price_changes),
                "filters": {
                    "days": days,
                    "threshold": threshold
                }
            }
        ).to_response()
        
    except Exception as e:
        logger.error(f"Error getting price changes: {e}")
        return APIResponse(
            message="Error retrieving price changes",
            success=False,
            error=str(e)
        ).to_response()

@alerts_bp.route('/corporate-actions', methods=['GET'])
def get_corporate_action_alerts():
    """Get corporate action alerts (duplicates, excessive ratios, etc.)"""
    try:
        alerts = []
        
        # Check for duplicate corporate actions
        securities_with_actions = db.session.query(
            Security.symbol, 
            Security.name,
            Security.id,
            db.func.count(CorporateAction.id).label('action_count')
        ).join(
            CorporateAction, Security.id == CorporateAction.security_id
        ).group_by(Security.id).all()
        
        for symbol, name, security_id, count in securities_with_actions:
            if count > 1:
                actions = CorporateAction.query.filter_by(security_id=security_id).order_by(CorporateAction.action_date).all()
                
                # Check for multiple actions within 30 days
                for i in range(len(actions) - 1):
                    days_diff = (actions[i+1].action_date - actions[i].action_date).days
                    if days_diff <= 30:
                        alerts.append({
                            'type': 'duplicate_corporate_actions',
                            'severity': 'HIGH',
                            'security_symbol': symbol,
                            'security_name': name,
                            'message': f"Duplicate corporate actions: {actions[i].action_date} and {actions[i+1].action_date} ({days_diff} days apart)",
                            'action1': {
                                'date': actions[i].action_date.isoformat(),
                                'type': actions[i].action_type,
                                'ratio': float(actions[i].ratio)
                            },
                            'action2': {
                                'date': actions[i+1].action_date.isoformat(),
                                'type': actions[i+1].action_type,
                                'ratio': float(actions[i+1].ratio)
                            },
                            'days_diff': days_diff
                        })
                
                # Check for excessive total split ratio
                total_split_ratio = 1.0
                for action in actions:
                    if action.action_type == 'SPLIT':
                        total_split_ratio *= float(action.ratio)
                
                if total_split_ratio > 20.0:
                    alerts.append({
                        'type': 'excessive_split_ratio',
                        'severity': 'CRITICAL',
                        'security_symbol': symbol,
                        'security_name': name,
                        'message': f"Excessive total split ratio: {total_split_ratio}:1 (max: 20:1)",
                        'total_ratio': total_split_ratio,
                        'actions': [
                            {
                                'date': action.action_date.isoformat(),
                                'type': action.action_type,
                                'ratio': float(action.ratio)
                            } for action in actions
                        ]
                    })
        
        # Check for non-standard split ratios
        split_actions = CorporateAction.query.filter(CorporateAction.action_type == 'SPLIT').all()
        standard_ratios = [1.5, 2.0, 3.0, 5.0, 10.0]
        
        for action in split_actions:
            if float(action.ratio) not in standard_ratios:
                security = Security.query.get(action.security_id)
                alerts.append({
                    'type': 'non_standard_split_ratio',
                    'severity': 'MEDIUM',
                    'security_symbol': security.symbol if security else 'Unknown',
                    'security_name': security.name if security else 'Unknown',
                    'message': f"Non-standard split ratio: {action.ratio} (standard: {standard_ratios})",
                    'date': action.action_date.isoformat(),
                    'ratio': float(action.ratio),
                    'standard_ratios': standard_ratios
                })
        
        # Sort by severity
        severity_order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
        alerts.sort(key=lambda x: severity_order.get(x['severity'], 4))
        
        return APIResponse(
            message="Corporate action alerts retrieved successfully",
            data={
                "alerts": alerts,
                "total_count": len(alerts)
            }
        ).to_response()
        
    except Exception as e:
        logger.error(f"Error getting corporate action alerts: {e}")
        return APIResponse(
            message="Error retrieving corporate action alerts",
            success=False,
            error=str(e)
        ).to_response()

@alerts_bp.route('/summary', methods=['GET'])
def get_alert_summary():
    """Get summary of all alerts"""
    try:
        # Get discrepancies
        discrepancies_response = get_discrepancies()
        discrepancies_data = discrepancies_response.get_json()
        discrepancies = discrepancies_data.get('data', {}).get('discrepancies', [])
        
        # Get price changes
        price_changes_response = get_price_changes()
        price_changes_data = price_changes_response.get_json()
        price_changes = price_changes_data.get('data', {}).get('price_changes', [])
        
        # Get corporate action alerts
        corporate_actions_response = get_corporate_action_alerts()
        corporate_actions_data = corporate_actions_response.get_json()
        corporate_actions = corporate_actions_data.get('data', {}).get('alerts', [])
        
        # Count alerts by severity
        severity_counts = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
        
        for discrepancy in discrepancies:
            for alert in discrepancy.get('alerts', []):
                severity = alert.get('severity', 'LOW')
                severity_counts[severity] += 1
        
        for price_change in price_changes:
            for alert in price_change.get('alerts', []):
                severity = alert.get('severity', 'LOW')
                severity_counts[severity] += 1
        
        for alert in corporate_actions:
            severity = alert.get('severity', 'LOW')
            severity_counts[severity] += 1
        
        total_alerts = sum(severity_counts.values())
        
        return APIResponse(
            message="Alert summary retrieved successfully",
            data={
                "summary": {
                    "total_alerts": total_alerts,
                    "severity_counts": severity_counts,
                    "discrepancies_count": len(discrepancies),
                    "price_changes_count": len(price_changes),
                    "corporate_actions_count": len(corporate_actions)
                },
                "discrepancies": discrepancies[:5],  # Top 5
                "price_changes": price_changes[:5],   # Top 5
                "corporate_actions": corporate_actions[:5]  # Top 5
            }
        ).to_response()
        
    except Exception as e:
        logger.error(f"Error getting alert summary: {e}")
        return APIResponse(
            message="Error retrieving alert summary",
            success=False,
            error=str(e)
        ).to_response()
