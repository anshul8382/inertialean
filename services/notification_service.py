"""
Notification Service
Handles portfolio alerts, performance updates, and client notifications
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy import and_, or_
from extensions import db
from models.whatsapp_models import WhatsAppNotification, NotificationType, ClientCommunication
from services.whatsapp_service import WhatsAppService
from models import Client, Transaction, Holding

logger = logging.getLogger(__name__)

class NotificationService:
    """Service for managing notifications and alerts"""
    
    def __init__(self):
        self.whatsapp_service = WhatsAppService()
    
    def send_portfolio_alerts(self, client_id: int = None) -> Dict[str, Any]:
        """Send portfolio alerts to clients"""
        try:
            results = {"success": True, "sent": 0, "failed": 0, "errors": []}
            
            # Get clients to notify
            if client_id:
                clients = Client.query.filter_by(id=client_id).all()
            else:
                clients = Client.query.filter(Client.whatsapp_number.isnot(None)).all()
            
            for client in clients:
                try:
                    # Check if client has WhatsApp notifications enabled
                    notifications = WhatsAppNotification.query.filter_by(
                        client_id=client.id,
                        is_enabled=True
                    ).all()
                    
                    if not notifications:
                        continue
                    
                    # Get portfolio data
                    portfolio_data = self._get_portfolio_data(client.id)
                    
                    # Send different types of alerts
                    for notification in notifications:
                        if notification.notification_type == NotificationType.PORTFOLIO_ALERT:
                            self._send_portfolio_alert(client, portfolio_data)
                        elif notification.notification_type == NotificationType.PERFORMANCE_UPDATE:
                            self._send_performance_update(client, portfolio_data)
                        elif notification.notification_type == NotificationType.PRICE_ALERT:
                            self._send_price_alerts(client, portfolio_data, notification)
                    
                    results["sent"] += 1
                    
                except Exception as e:
                    logger.error(f"Error sending alerts to client {client.id}: {str(e)}")
                    results["failed"] += 1
                    results["errors"].append(f"Client {client.id}: {str(e)}")
            
            return results
            
        except Exception as e:
            logger.error(f"Error in send_portfolio_alerts: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def send_transaction_notifications(self, transaction_id: int) -> Dict[str, Any]:
        """Send transaction confirmation notifications"""
        try:
            transaction = Transaction.query.get(transaction_id)
            if not transaction:
                return {"success": False, "error": "Transaction not found"}
            
            client = Client.query.get(transaction.client_id)
            if not client or not client.whatsapp_number:
                return {"success": False, "error": "Client WhatsApp number not found"}
            
            # Check if client wants transaction notifications
            notification = WhatsAppNotification.query.filter_by(
                client_id=client.id,
                notification_type=NotificationType.TRANSACTION_CONFIRMATION,
                is_enabled=True
            ).first()
            
            if not notification:
                return {"success": False, "error": "Transaction notifications not enabled"}
            
            # Prepare transaction data
            transaction_data = {
                'type': transaction.type,
                'stock_symbol': transaction.security.symbol if transaction.security else 'N/A',
                'quantity': transaction.quantity,
                'price': transaction.price,
                'total_amount': transaction.quantity * transaction.price,
                'date': transaction.transaction_date.strftime('%Y-%m-%d'),
                'transaction_id': transaction.id
            }
            
            # Send WhatsApp message
            result = self.whatsapp_service.send_transaction_confirmation(
                client_id=client.id,
                transaction_data=transaction_data
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error sending transaction notification: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def send_daily_performance_updates(self) -> Dict[str, Any]:
        """Send daily performance updates to all clients"""
        try:
            results = {"success": True, "sent": 0, "failed": 0, "errors": []}
            
            # Get clients with performance notifications enabled
            clients = db.session.query(Client).join(WhatsAppNotification).filter(
                and_(
                    Client.whatsapp_number.isnot(None),
                    WhatsAppNotification.notification_type == NotificationType.PERFORMANCE_UPDATE,
                    WhatsAppNotification.is_enabled == True
                )
            ).all()
            
            for client in clients:
                try:
                    # Get performance data
                    performance_data = self._get_performance_data(client.id)
                    
                    # Send performance update
                    result = self.whatsapp_service.send_performance_update(
                        client_id=client.id,
                        performance_data=performance_data
                    )
                    
                    if result.get('success'):
                        results["sent"] += 1
                    else:
                        results["failed"] += 1
                        results["errors"].append(f"Client {client.id}: {result.get('error')}")
                    
                except Exception as e:
                    logger.error(f"Error sending performance update to client {client.id}: {str(e)}")
                    results["failed"] += 1
                    results["errors"].append(f"Client {client.id}: {str(e)}")
            
            return results
            
        except Exception as e:
            logger.error(f"Error in send_daily_performance_updates: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def check_price_alerts(self) -> Dict[str, Any]:
        """Check and send price alerts"""
        try:
            results = {"success": True, "alerts_sent": 0, "errors": []}
            
            # Get all price alert notifications
            price_alerts = WhatsAppNotification.query.filter(
                and_(
                    WhatsAppNotification.notification_type == NotificationType.PRICE_ALERT,
                    WhatsAppNotification.is_enabled == True
                )
            ).all()
            
            for alert in price_alerts:
                try:
                    client = Client.query.get(alert.client_id)
                    if not client or not client.whatsapp_number:
                        continue
                    
                    # Get client's holdings
                    holdings = Holding.query.filter_by(client_id=client.id).all()
                    
                    for holding in holdings:
                        if self._should_send_price_alert(holding, alert):
                            # Send price alert
                            alert_data = {
                                'stock_symbol': holding.security.symbol,
                                'current_price': holding.security.current_price,
                                'target_price': alert.threshold_value,
                                'change_percent': self._calculate_price_change(holding),
                                'portfolio_value': self._get_portfolio_value(client.id)
                            }
                            
                            result = self.whatsapp_service.send_portfolio_alert(
                                client_id=client.id,
                                alert_type="price_alert",
                                data=alert_data
                            )
                            
                            if result.get('success'):
                                results["alerts_sent"] += 1
                                # Update last sent time
                                alert.last_sent = datetime.utcnow()
                                db.session.commit()
                    
                except Exception as e:
                    logger.error(f"Error checking price alert for client {alert.client_id}: {str(e)}")
                    results["errors"].append(f"Client {alert.client_id}: {str(e)}")
            
            return results
            
        except Exception as e:
            logger.error(f"Error in check_price_alerts: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def create_notification_preference(self, client_id: int, notification_type: str, 
                                     phone_number: str, threshold_value: str = None) -> Dict[str, Any]:
        """Create notification preference for client"""
        try:
            # Check if preference already exists
            existing = WhatsAppNotification.query.filter_by(
                client_id=client_id,
                notification_type=NotificationType(notification_type)
            ).first()
            
            if existing:
                # Update existing preference
                existing.phone_number = phone_number
                existing.threshold_value = threshold_value
                existing.is_enabled = True
                existing.updated_at = datetime.utcnow()
            else:
                # Create new preference
                notification = WhatsAppNotification(
                    client_id=client_id,
                    notification_type=NotificationType(notification_type),
                    phone_number=phone_number,
                    threshold_value=threshold_value,
                    is_enabled=True
                )
                db.session.add(notification)
            
            db.session.commit()
            
            return {"success": True, "message": "Notification preference created/updated"}
            
        except Exception as e:
            logger.error(f"Error creating notification preference: {str(e)}")
            db.session.rollback()
            return {"success": False, "error": str(e)}
    
    def get_client_communications(self, client_id: int = None, status: str = None) -> List[Dict[str, Any]]:
        """Get client communications"""
        try:
            query = ClientCommunication.query
            
            if client_id:
                query = query.filter_by(client_id=client_id)
            
            if status:
                query = query.filter_by(status=status)
            
            communications = query.order_by(ClientCommunication.created_at.desc()).all()
            
            return [{
                'id': comm.id,
                'client_id': comm.client_id,
                'client_name': comm.client.name if comm.client else 'Unknown',
                'communication_type': comm.communication_type,
                'direction': comm.direction,
                'subject': comm.subject,
                'content': comm.content,
                'status': comm.status,
                'priority': comm.priority,
                'created_at': comm.created_at.isoformat(),
                'resolved_at': comm.resolved_at.isoformat() if comm.resolved_at else None
            } for comm in communications]
            
        except Exception as e:
            logger.error(f"Error getting client communications: {str(e)}")
            return []
    
    def update_communication_status(self, communication_id: int, status: str, 
                                  resolution_notes: str = None) -> Dict[str, Any]:
        """Update communication status"""
        try:
            communication = ClientCommunication.query.get(communication_id)
            if not communication:
                return {"success": False, "error": "Communication not found"}
            
            communication.status = status
            if resolution_notes:
                communication.resolution_notes = resolution_notes
            
            if status == "resolved":
                communication.resolved_at = datetime.utcnow()
            
            communication.updated_at = datetime.utcnow()
            db.session.commit()
            
            return {"success": True, "message": "Communication status updated"}
            
        except Exception as e:
            logger.error(f"Error updating communication status: {str(e)}")
            db.session.rollback()
            return {"success": False, "error": str(e)}
    
    def _get_portfolio_data(self, client_id: int) -> Dict[str, Any]:
        """Get portfolio data for client"""
        try:
            holdings = Holding.query.filter_by(client_id=client_id).all()
            
            total_value = sum(holding.quantity * holding.security.current_price for holding in holdings)
            total_cost = sum(holding.quantity * holding.average_buy_price for holding in holdings)
            gain_loss = total_value - total_cost
            gain_loss_percent = (gain_loss / total_cost * 100) if total_cost > 0 else 0
            
            # Get top performers
            top_performers = []
            for holding in holdings:
                if holding.security.current_price > 0 and holding.average_buy_price > 0:
                    change_percent = ((holding.security.current_price - holding.average_buy_price) / holding.average_buy_price) * 100
                    top_performers.append({
                        'symbol': holding.security.symbol,
                        'change_percent': round(change_percent, 2)
                    })
            
            top_performers.sort(key=lambda x: x['change_percent'], reverse=True)
            
            return {
                'total_value': round(total_value, 2),
                'total_cost': round(total_cost, 2),
                'gain_loss': round(gain_loss, 2),
                'gain_loss_percent': round(gain_loss_percent, 2),
                'top_performers': top_performers[:5],
                'client_id': client_id
            }
            
        except Exception as e:
            logger.error(f"Error getting portfolio data: {str(e)}")
            return {}
    
    def _get_performance_data(self, client_id: int) -> Dict[str, Any]:
        """Get performance data for client"""
        try:
            # Get current portfolio value
            portfolio_data = self._get_portfolio_data(client_id)
            
            # Get yesterday's value (simplified - in real implementation, store daily snapshots)
            # For now, we'll calculate a simple daily change
            daily_change = portfolio_data.get('gain_loss', 0) * 0.01  # Simplified calculation
            daily_change_percent = portfolio_data.get('gain_loss_percent', 0) * 0.01
            
            return {
                'total_value': portfolio_data.get('total_value', 0),
                'daily_change': round(daily_change, 2),
                'daily_change_percent': round(daily_change_percent, 2),
                'top_movers': portfolio_data.get('top_performers', [])[:3],
                'client_id': client_id
            }
            
        except Exception as e:
            logger.error(f"Error getting performance data: {str(e)}")
            return {}
    
    def _should_send_price_alert(self, holding: Holding, alert: WhatsAppNotification) -> bool:
        """Check if price alert should be sent"""
        try:
            if not alert.threshold_value or not holding.security.current_price:
                return False
            
            # Check if enough time has passed since last alert
            if alert.last_sent and (datetime.utcnow() - alert.last_sent).hours < 24:
                return False
            
            # Check if price change meets threshold
            if holding.average_buy_price > 0:
                change_percent = ((holding.security.current_price - holding.average_buy_price) / holding.average_buy_price) * 100
                threshold = float(alert.threshold_value.replace('%', ''))
                
                return abs(change_percent) >= threshold
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking price alert: {str(e)}")
            return False
    
    def _calculate_price_change(self, holding: Holding) -> float:
        """Calculate price change percentage"""
        try:
            if holding.average_buy_price > 0 and holding.security.current_price > 0:
                return round(((holding.security.current_price - holding.average_buy_price) / holding.average_buy_price) * 100, 2)
            return 0
        except:
            return 0
    
    def _get_portfolio_value(self, client_id: int) -> float:
        """Get total portfolio value"""
        try:
            holdings = Holding.query.filter_by(client_id=client_id).all()
            return round(sum(holding.quantity * holding.security.current_price for holding in holdings), 2)
        except:
            return 0
    
    def _send_portfolio_alert(self, client: Client, portfolio_data: Dict[str, Any]):
        """Send portfolio alert to client"""
        try:
            self.whatsapp_service.send_portfolio_alert(
                client_id=client.id,
                alert_type="performance_update",
                data=portfolio_data
            )
        except Exception as e:
            logger.error(f"Error sending portfolio alert: {str(e)}")
    
    def _send_performance_update(self, client: Client, portfolio_data: Dict[str, Any]):
        """Send performance update to client"""
        try:
            performance_data = self._get_performance_data(client.id)
            self.whatsapp_service.send_performance_update(
                client_id=client.id,
                performance_data=performance_data
            )
        except Exception as e:
            logger.error(f"Error sending performance update: {str(e)}")
    
    def _send_price_alerts(self, client: Client, portfolio_data: Dict[str, Any], alert: WhatsAppNotification):
        """Send price alerts to client"""
        try:
            # This will be handled by check_price_alerts method
            pass
        except Exception as e:
            logger.error(f"Error sending price alerts: {str(e)}")
