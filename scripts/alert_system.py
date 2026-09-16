#!/usr/bin/env python3
"""
Real-time Alert System for Corporate Actions and Price Discrepancies
Implements immediate alerts for discrepancies and sharp price changes
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run import create_app
from models import db, Client, Security, Transaction, Holding, CorporateAction, HistoricalPrice
from datetime import datetime, date, timedelta
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from email.mime.base import MIMEBase
from email import encoders

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AlertSystem:
    """Real-time alert system for corporate action discrepancies"""
    
    def __init__(self):
        self.app = create_app()
        self.alert_thresholds = {
            'quantity_discrepancy_percent': 10.0,
            'price_discrepancy_percent': 20.0,
            'pnl_excessive_profit_percent': 200.0,
            'pnl_excessive_loss_percent': -50.0,
            'price_change_percent': 10.0,
            'max_split_ratio': 10.0,
            'max_total_split_ratio': 20.0,
            'min_days_between_actions': 30,
            'max_annual_return_percent': 25.0,  # Max acceptable annual return
            'min_holding_period_days': 365       # Minimum holding period for annual return calculation
        }
        
        # Email configuration: use email_config (which reads MAIL_* env) or fallback with env
        try:
            from email_config import EMAIL_CONFIG
            self.email_config = dict(EMAIL_CONFIG)
        except ImportError:
            # Fallback: same env vars as main app so one .env works
            pw = (os.environ.get('MAIL_PASSWORD') or os.environ.get('SMTP_PASSWORD') or '').strip().strip('"\'').replace(' ', '').replace('\r', '').replace('\n', '')
            self.email_config = {
                'smtp_server': os.environ.get('MAIL_SERVER') or os.environ.get('SMTP_SERVER') or 'smtp.gmail.com',
                'smtp_port': int(os.environ.get('MAIL_PORT') or os.environ.get('SMTP_PORT') or 587),
                'sender_email': (os.environ.get('MAIL_USERNAME') or os.environ.get('SMTP_USERNAME') or 'alerts@equities4wealth.com').strip(),
                'sender_password': pw,
                'recipients': ['anshul@equities4wealth.com', 'service@equities4wealth.com'],
                'send_email_alerts': True
            }
        
    def send_alert(self, alert_type, severity, message, data=None):
        """Send alert via email and log"""
        timestamp = datetime.now().isoformat()
        
        # Log alert
        logger.warning(f"ALERT [{severity}] {alert_type}: {message}")
        
        # Email alert (only if enabled)
        if self.email_config.get('send_email_alerts', True):
            try:
                self._send_email_alert(alert_type, severity, message, data, timestamp)
            except Exception as e:
                logger.error(f"Failed to send email alert: {e}")
        else:
            logger.info("Email alerts disabled - alert logged only")
        
        # Store alert in database (optional)
        self._store_alert(alert_type, severity, message, data, timestamp)
    
    def _send_email_alert(self, alert_type, severity, message, data, timestamp):
        """Send email alert via SMTP"""
        try:
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.email_config['sender_email']
            msg['To'] = ', '.join(self.email_config['recipients'])
            msg['Subject'] = f"[{severity}] Portfolio Alert: {alert_type}"
            
            # Create email body (replace ₹ with INR to avoid encoding issues)
            message_clean = message.replace('₹', 'INR ')
            body = f"""
Portfolio Management System Alert

Alert Type: {alert_type}
Severity: {severity}
Time: {timestamp}
Message: {message_clean}

"""
            
            if data:
                body += "Details:\n"
                for key, value in data.items():
                    if isinstance(value, (list, dict)):
                        value_str = json.dumps(value, indent=2)
                        value_str = value_str.replace('₹', 'INR ')
                        body += f"{key}: {value_str}\n"
                    else:
                        value_str = str(value).replace('₹', 'INR ')
                        body += f"{key}: {value_str}\n"
            
            body += f"""
This is an automated alert from the Portfolio Management System.
Please review the data and take appropriate action if necessary.

Best regards,
Portfolio Management System
"""
            
            msg.attach(MIMEText(body, 'plain'))
            
            # Send email
            server = smtplib.SMTP(self.email_config['smtp_server'], self.email_config['smtp_port'])
            server.starttls()
            
            # Only login if password is configured
            if self.email_config.get('sender_password'):
                server.login(self.email_config['sender_email'], self.email_config['sender_password'])
            
            text = msg.as_string()
            server.sendmail(self.email_config['sender_email'], self.email_config['recipients'], text)
            server.quit()
            
            logger.info(f"Email alert sent successfully to {self.email_config['recipients']}")
            
        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            # Don't raise exception to avoid breaking the alert system
    
    def _store_alert(self, alert_type, severity, message, data, timestamp):
        """Store alert in database for tracking"""
        # This is a placeholder - implement if you want to store alerts
        pass
    
    def check_corporate_action_discrepancies(self):
        """Check for corporate action discrepancies and send alerts"""
        with self.app.app_context():
            logger.info("🔍 Checking Corporate Action Discrepancies...")
            
            alerts_sent = 0
            
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
                    
                    # Check for multiple actions within threshold days
                    for i in range(len(actions) - 1):
                        days_diff = (actions[i+1].action_date - actions[i].action_date).days
                        if days_diff <= self.alert_thresholds['min_days_between_actions']:
                            message = f"Duplicate corporate actions detected for {symbol}: {actions[i].action_date} and {actions[i+1].action_date} ({days_diff} days apart)"
                            self.send_alert(
                                'duplicate_corporate_actions',
                                'HIGH',
                                message,
                                {
                                    'security': symbol,
                                    'action1': f"{actions[i].action_date}: {actions[i].action_type} {actions[i].ratio}",
                                    'action2': f"{actions[i+1].action_date}: {actions[i+1].action_type} {actions[i+1].ratio}",
                                    'days_diff': days_diff
                                }
                            )
                            alerts_sent += 1
                    
                    # Check for excessive total split ratio
                    total_split_ratio = 1.0
                    for action in actions:
                        if action.action_type == 'SPLIT':
                            total_split_ratio *= float(action.ratio)
                    
                    if total_split_ratio > self.alert_thresholds['max_total_split_ratio']:
                        message = f"Excessive total split ratio for {symbol}: {total_split_ratio}:1 (max: {self.alert_thresholds['max_total_split_ratio']}:1)"
                        self.send_alert(
                            'excessive_split_ratio',
                            'CRITICAL',
                            message,
                            {
                                'security': symbol,
                                'total_ratio': total_split_ratio,
                                'actions': [f"{a.action_date}: {a.action_type} {a.ratio}" for a in actions]
                            }
                        )
                        alerts_sent += 1
            
            # Check for non-standard split ratios
            split_actions = CorporateAction.query.filter(CorporateAction.action_type == 'SPLIT').all()
            standard_ratios = [1.5, 2.0, 3.0, 5.0, 10.0]
            
            for action in split_actions:
                if float(action.ratio) not in standard_ratios:
                    security = Security.query.get(action.security_id)
                    message = f"Non-standard split ratio for {security.symbol if security else 'Unknown'}: {action.ratio} (standard: {standard_ratios})"
                    self.send_alert(
                        'non_standard_split_ratio',
                        'MEDIUM',
                        message,
                        {
                            'security': security.symbol if security else 'Unknown',
                            'date': action.action_date.isoformat(),
                            'ratio': float(action.ratio),
                            'standard_ratios': standard_ratios
                        }
                    )
                    alerts_sent += 1
            
            return alerts_sent
    
    def check_holding_discrepancies(self):
        """Check for holding discrepancies and send alerts"""
        with self.app.app_context():
            logger.info("🔍 Checking Holding Discrepancies...")
            
            alerts_sent = 0
            
            # Get all clients with holdings
            clients_with_holdings = db.session.query(Client.id, Client.name).join(Holding).distinct().all()
            
            for client_id, client_name in clients_with_holdings:
                holdings = Holding.query.filter_by(client_id=client_id).all()
                
                for holding in holdings:
                    security = Security.query.get(holding.security_id)
                    if not security:
                        continue
                    
                    # Get BUY transactions
                    buy_transactions = Transaction.query.filter(
                        Transaction.client_id == client_id,
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
                    
                    if quantity_discrepancy_percent > self.alert_thresholds['quantity_discrepancy_percent']:
                        message = f"Quantity discrepancy for {client_name} - {security.symbol}: {quantity_discrepancy_percent:.1f}% (expected: {expected_quantity:.2f}, actual: {actual_quantity:.2f})"
                        self.send_alert(
                            'quantity_discrepancy',
                            'HIGH',
                            message,
                            {
                                'client': client_name,
                                'security': security.symbol,
                                'expected_quantity': expected_quantity,
                                'actual_quantity': actual_quantity,
                                'discrepancy_percent': quantity_discrepancy_percent,
                                'corporate_actions': [f"{a.action_date}: {a.action_type} {a.ratio}" for a in corporate_actions]
                            }
                        )
                        alerts_sent += 1
                    
                    # Check price discrepancy
                    if transaction_quantity > 0:
                        expected_avg_price = transaction_cost / transaction_quantity
                        actual_avg_price = float(holding.average_price)
                        price_discrepancy = abs(actual_avg_price - expected_avg_price)
                        price_discrepancy_percent = (price_discrepancy / expected_avg_price * 100) if expected_avg_price > 0 else 0
                        
                        if price_discrepancy_percent > self.alert_thresholds['price_discrepancy_percent']:
                            message = f"Price discrepancy for {client_name} - {security.symbol}: {price_discrepancy_percent:.1f}% (expected: ₹{expected_avg_price:.2f}, actual: ₹{actual_avg_price:.2f})"
                            self.send_alert(
                                'price_discrepancy',
                                'HIGH',
                                message,
                                {
                                    'client': client_name,
                                    'security': security.symbol,
                                    'expected_avg_price': expected_avg_price,
                                    'actual_avg_price': actual_avg_price,
                                    'discrepancy_percent': price_discrepancy_percent
                                }
                            )
                            alerts_sent += 1
            
            return alerts_sent
    
    def check_pnl_anomalies(self):
        """Check for P&L anomalies considering holding period and annual returns"""
        with self.app.app_context():
            logger.info("🔍 Checking P&L Anomalies...")
            
            alerts_sent = 0
            
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
                
                # Calculate holding period
                holding_period_days = self._calculate_holding_period(client.id, security.id)
                holding_period_years = holding_period_days / 365.25 if holding_period_days > 0 else 0
                
                # Calculate annualized return
                annualized_return = 0
                if holding_period_years > 0 and cost_basis > 0:
                    annualized_return = ((current_value / cost_basis) ** (1 / holding_period_years) - 1) * 100
                
                # Determine if this is an anomaly based on holding period and annual return
                is_anomaly = False
                anomaly_reason = ""
                
                # Check for excessive loss (always flag)
                if pnl_percent < self.alert_thresholds['pnl_excessive_loss_percent']:
                    is_anomaly = True
                    anomaly_reason = f"Excessive loss: {pnl_percent:.1f}%"
                
                # Check for excessive profit based on holding period and annual return
                elif pnl_percent > self.alert_thresholds['pnl_excessive_profit_percent']:
                    if holding_period_days < self.alert_thresholds['min_holding_period_days']:
                        # Short holding period - flag any excessive profit
                        is_anomaly = True
                        anomaly_reason = f"Excessive profit with short holding period: {pnl_percent:.1f}% ({holding_period_days} days)"
                    elif annualized_return > self.alert_thresholds['max_annual_return_percent']:
                        # Long holding period but excessive annual return
                        is_anomaly = True
                        anomaly_reason = f"Excessive annual return: {annualized_return:.1f}% (holding {holding_period_days} days)"
                    else:
                        # Long holding period with reasonable annual return - not an anomaly
                        logger.info(f"Large profit but reasonable annual return: {client.name} - {security.symbol}: {pnl_percent:.1f}% profit, {annualized_return:.1f}% annual return over {holding_period_days} days")
                
                if is_anomaly:
                    severity = 'HIGH' if pnl_percent < 0 else 'MEDIUM'
                    alert_type = 'excessive_loss' if pnl_percent < 0 else 'excessive_profit'
                    
                    message = f"{anomaly_reason} for {client.name} - {security.symbol}: {pnl_percent:.1f}% (₹{pnl:,.0f})"
                    self.send_alert(
                        alert_type,
                        severity,
                        message,
                        {
                            'client': client.name,
                            'security': security.symbol,
                            'pnl_percent': pnl_percent,
                            'pnl_amount': pnl,
                            'quantity': float(holding.quantity),
                            'avg_price': float(holding.average_price),
                            'current_price': float(security.current_price),
                            'holding_period_days': holding_period_days,
                            'holding_period_years': round(holding_period_years, 2),
                            'annualized_return': round(annualized_return, 2),
                            'anomaly_reason': anomaly_reason
                        }
                    )
                    alerts_sent += 1
            
            return alerts_sent
    
    def _calculate_holding_period(self, client_id, security_id):
        """Calculate holding period in days based on first buy transaction"""
        with self.app.app_context():
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
    
    def check_price_changes(self):
        """Check for sharp price changes that might indicate missing corporate actions"""
        with self.app.app_context():
            logger.info("🔍 Checking Sharp Price Changes...")
            
            alerts_sent = 0
            
            # Get all securities with current prices
            securities = Security.query.filter(Security.current_price.isnot(None)).all()
            
            for security in securities:
                # Get historical prices for last 2 days
                today = date.today()
                yesterday = today - timedelta(days=1)
                day_before = today - timedelta(days=2)
                
                # Get prices for these dates
                today_price = None
                yesterday_price = None
                day_before_price = None
                
                # Try to get exact date prices first
                today_price_data = HistoricalPrice.query.filter_by(
                    security_id=security.id, date=today
                ).first()
                if today_price_data:
                    today_price = float(today_price_data.close_price)
                
                yesterday_price_data = HistoricalPrice.query.filter_by(
                    security_id=security.id, date=yesterday
                ).first()
                if yesterday_price_data:
                    yesterday_price = float(yesterday_price_data.close_price)
                
                day_before_price_data = HistoricalPrice.query.filter_by(
                    security_id=security.id, date=day_before
                ).first()
                if day_before_price_data:
                    day_before_price = float(day_before_price_data.close_price)
                
                # If no exact date prices, get closest previous prices
                if not today_price:
                    today_price_data = HistoricalPrice.query.filter(
                        HistoricalPrice.security_id == security.id,
                        HistoricalPrice.date <= today
                    ).order_by(HistoricalPrice.date.desc()).first()
                    if today_price_data:
                        today_price = float(today_price_data.close_price)
                
                if not yesterday_price:
                    yesterday_price_data = HistoricalPrice.query.filter(
                        HistoricalPrice.security_id == security.id,
                        HistoricalPrice.date <= yesterday
                    ).order_by(HistoricalPrice.date.desc()).first()
                    if yesterday_price_data:
                        yesterday_price = float(yesterday_price_data.close_price)
                
                if not day_before_price:
                    day_before_price_data = HistoricalPrice.query.filter(
                        HistoricalPrice.security_id == security.id,
                        HistoricalPrice.date <= day_before
                    ).order_by(HistoricalPrice.date.desc()).first()
                    if day_before_price_data:
                        day_before_price = float(day_before_price_data.close_price)
                
                # Use current price as fallback
                if not today_price:
                    today_price = float(security.current_price)
                
                # Check for sharp price changes
                if yesterday_price and today_price:
                    price_change_percent = abs((today_price - yesterday_price) / yesterday_price * 100)
                    
                    if price_change_percent > self.alert_thresholds['price_change_percent']:
                        # Check if this might be due to a corporate action
                        recent_corporate_actions = CorporateAction.query.filter(
                            CorporateAction.security_id == security.id,
                            CorporateAction.action_date >= yesterday,
                            CorporateAction.action_date <= today
                        ).all()
                        
                        if not recent_corporate_actions:
                            message = f"Sharp price change for {security.symbol}: {price_change_percent:.1f}% ({yesterday_price:.2f} → {today_price:.2f}) - Possible missing corporate action"
                            self.send_alert(
                                'sharp_price_change',
                                'HIGH',
                                message,
                                {
                                    'security': security.symbol,
                                    'price_change_percent': price_change_percent,
                                    'yesterday_price': yesterday_price,
                                    'today_price': today_price,
                                    'date': today.isoformat(),
                                    'possible_corporate_action': True
                                }
                            )
                            alerts_sent += 1
                        else:
                            # Log that corporate action exists but price change is still significant
                            logger.info(f"Sharp price change for {security.symbol}: {price_change_percent:.1f}% with corporate action on {recent_corporate_actions[0].action_date}")
                
                # Check for consistent price drops that might indicate split
                if day_before_price and yesterday_price and today_price:
                    # Check if price consistently dropped (might indicate split)
                    if (day_before_price > yesterday_price > today_price and 
                        abs((yesterday_price - day_before_price) / day_before_price * 100) > 5 and
                        abs((today_price - yesterday_price) / yesterday_price * 100) > 5):
                        
                        # Check if there's a corporate action
                        recent_corporate_actions = CorporateAction.query.filter(
                            CorporateAction.security_id == security.id,
                            CorporateAction.action_date >= day_before,
                            CorporateAction.action_date <= today
                        ).all()
                        
                        if not recent_corporate_actions:
                            message = f"Consistent price drop for {security.symbol}: {day_before_price:.2f} → {yesterday_price:.2f} → {today_price:.2f} - Possible split action"
                            self.send_alert(
                                'consistent_price_drop',
                                'MEDIUM',
                                message,
                                {
                                    'security': security.symbol,
                                    'day_before_price': day_before_price,
                                    'yesterday_price': yesterday_price,
                                    'today_price': today_price,
                                    'date': today.isoformat(),
                                    'possible_split': True
                                }
                            )
                            alerts_sent += 1
            
            return alerts_sent
    
    def run_all_checks(self):
        """Run all alert checks"""
        logger.info("🚨 Starting Alert System Checks...")
        
        total_alerts = 0
        
        # Run all checks
        total_alerts += self.check_corporate_action_discrepancies()
        total_alerts += self.check_holding_discrepancies()
        total_alerts += self.check_pnl_anomalies()
        total_alerts += self.check_price_changes()
        
        logger.info(f"✅ Alert System Check Complete - {total_alerts} alerts sent")
        
        return total_alerts

def main():
    """Main function to run alert system"""
    print("🚨 Corporate Action Alert System")
    print("=" * 50)
    
    try:
        alert_system = AlertSystem()
        total_alerts = alert_system.run_all_checks()
        
        print(f"\n📊 Alert System Report:")
        print(f"Total Alerts Sent: {total_alerts}")
        
        if total_alerts > 0:
            print(f"\n🚨 Alerts Generated:")
            print(f"Check logs for detailed alert information")
            print(f"Configure email settings to receive alerts")
        else:
            print(f"\n✅ No alerts generated - All checks passed!")
        
        print(f"\n🎯 Alert Types:")
        print(f"1. Duplicate Corporate Actions")
        print(f"2. Excessive Split Ratios")
        print(f"3. Non-Standard Split Ratios")
        print(f"4. Quantity Discrepancies")
        print(f"5. Price Discrepancies")
        print(f"6. P&L Anomalies")
        print(f"7. Sharp Price Changes")
        print(f"8. Consistent Price Drops")
        
        print(f"\n⚙️ Configuration:")
        print(f"- Quantity Discrepancy Threshold: {alert_system.alert_thresholds['quantity_discrepancy_percent']}%")
        print(f"- Price Discrepancy Threshold: {alert_system.alert_thresholds['price_discrepancy_percent']}%")
        print(f"- P&L Profit Threshold: {alert_system.alert_thresholds['pnl_excessive_profit_percent']}%")
        print(f"- P&L Loss Threshold: {alert_system.alert_thresholds['pnl_excessive_loss_percent']}%")
        print(f"- Price Change Threshold: {alert_system.alert_thresholds['price_change_percent']}%")
        print(f"- Max Split Ratio: {alert_system.alert_thresholds['max_split_ratio']}:1")
        print(f"- Max Total Split Ratio: {alert_system.alert_thresholds['max_total_split_ratio']}:1")
        print(f"- Min Days Between Actions: {alert_system.alert_thresholds['min_days_between_actions']}")
        
    except Exception as e:
        logger.error(f"Error running alert system: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
