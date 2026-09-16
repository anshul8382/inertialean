"""
WhatsApp Business API Integration Models
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON, Enum
from sqlalchemy.orm import relationship
from extensions import db
import enum

class MessageStatus(enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"

class MessageType(enum.Enum):
    TEXT = "text"
    TEMPLATE = "template"
    MEDIA = "media"
    INTERACTIVE = "interactive"

class NotificationType(enum.Enum):
    PORTFOLIO_ALERT = "portfolio_alert"
    TRANSACTION_CONFIRMATION = "transaction_confirmation"
    PRICE_ALERT = "price_alert"
    PERFORMANCE_UPDATE = "performance_update"
    SYSTEM_NOTIFICATION = "system_notification"
    CLIENT_QUERY = "client_query"

class WhatsAppGroup(db.Model):
    """Meta WhatsApp group synced from Cloud API; optional 1:1 link to a client."""
    __tablename__ = 'whatsapp_groups'

    id = Column(Integer, primary_key=True)
    group_id = Column(String(255), unique=True, nullable=False, index=True)
    subject = Column(String(255), nullable=True)
    meta_created_at = Column(String(64), nullable=True)
    client_id = Column(Integer, ForeignKey('client.id'), nullable=True, unique=True)
    is_active = Column(Boolean, default=True)
    participant_count = Column(Integer, nullable=True)
    last_message_at = Column(DateTime, nullable=True)
    synced_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    client = relationship(
        "Client",
        foreign_keys=[client_id],
        primaryjoin="WhatsAppGroup.client_id == Client.id",
        backref=db.backref("whatsapp_group_link", uselist=False),
    )


class WhatsAppMessage(db.Model):
    """Store all WhatsApp messages sent and received"""
    __tablename__ = 'whatsapp_messages'
    
    id = Column(Integer, primary_key=True)
    message_id = Column(String(255), unique=True, nullable=False)  # WhatsApp message ID
    client_id = Column(Integer, ForeignKey('client.id'), nullable=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=True)
    group_id = Column(String(255), nullable=True, index=True)

    # Message details
    phone_number = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=True)  # inbound | outbound
    sender_wa_id = Column(String(32), nullable=True)
    message_type = Column(Enum(MessageType), nullable=False)
    content = Column(Text, nullable=True)
    template_name = Column(String(100), nullable=True)
    template_params = Column(JSON, nullable=True)
    
    # Status tracking
    status = Column(Enum(MessageStatus), default=MessageStatus.PENDING)
    whatsapp_timestamp = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)
    
    # Error handling
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    client = relationship("Client", foreign_keys=[client_id], primaryjoin="WhatsAppMessage.client_id == Client.id", backref="whatsapp_messages")
    user = relationship("User", foreign_keys=[user_id], primaryjoin="WhatsAppMessage.user_id == User.id", backref="whatsapp_messages")

class WhatsAppNotification(db.Model):
    """Store notification preferences and history"""
    __tablename__ = 'whatsapp_notifications'
    
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('client.id'), nullable=False)
    notification_type = Column(Enum(NotificationType), nullable=False)
    
    # Notification settings
    is_enabled = Column(Boolean, default=True)
    phone_number = Column(String(20), nullable=False)
    
    # Alert thresholds (for price alerts, etc.)
    threshold_value = Column(String(50), nullable=True)  # e.g., "5%" for 5% change
    threshold_type = Column(String(20), nullable=True)   # e.g., "percentage", "absolute"
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_sent = Column(DateTime, nullable=True)
    
    # Relationships
    client = relationship("Client", foreign_keys=[client_id], primaryjoin="WhatsAppNotification.client_id == Client.id", backref="whatsapp_notifications")

class ClientCommunication(db.Model):
    """Track all client communications and queries"""
    __tablename__ = 'client_communications'
    
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey('client.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=True)
    
    # Communication details
    communication_type = Column(String(50), nullable=False)  # whatsapp, email, phone, etc.
    direction = Column(String(20), nullable=False)  # inbound, outbound
    subject = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    
    # WhatsApp specific
    whatsapp_message_id = Column(String(255), nullable=True)
    phone_number = Column(String(20), nullable=True)
    
    # Status and resolution
    status = Column(String(20), default="open")  # open, in_progress, resolved, closed
    priority = Column(String(20), default="medium")  # low, medium, high, urgent
    resolution_notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    
    # Relationships
    client = relationship("Client", foreign_keys=[client_id], primaryjoin="ClientCommunication.client_id == Client.id", backref="communications")
    user = relationship("User", foreign_keys=[user_id], primaryjoin="ClientCommunication.user_id == User.id", backref="communications")

class WhatsAppTemplate(db.Model):
    """Store WhatsApp message templates"""
    __tablename__ = 'whatsapp_templates'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    category = Column(String(50), nullable=False)  # AUTHENTICATION, MARKETING, UTILITY
    language = Column(String(10), default="en")
    
    # Template content
    header_type = Column(String(20), nullable=True)  # text, image, document, video
    header_content = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    footer = Column(String(60), nullable=True)
    
    # Template status
    status = Column(String(20), default="draft")  # draft, pending, approved, rejected
    whatsapp_template_id = Column(String(100), nullable=True)  # ID from WhatsApp
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)

class WhatsAppWebhookLog(db.Model):
    """Log all webhook events from WhatsApp"""
    __tablename__ = 'whatsapp_webhook_logs'
    
    id = Column(Integer, primary_key=True)
    event_type = Column(String(50), nullable=False)  # message, status, error
    webhook_data = Column(JSON, nullable=False)
    processed = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
