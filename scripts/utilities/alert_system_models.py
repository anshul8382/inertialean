from datetime import datetime, timedelta
from sqlalchemy import and_, or_, func
from models import db

class Alert(db.Model):
    """Alert system for SLA monitoring and process delays"""
    __tablename__ = 'alert'
    
    id = db.Column(db.Integer, primary_key=True)
    alert_type = db.Column(db.String(50), nullable=False)  # workflow_sla, recommendation, communication, portfolio, system
    alert_subtype = db.Column(db.String(50), nullable=False)  # funds_delay, recos_delay, etc.
    severity = db.Column(db.String(20), nullable=False)  # critical, warning, info
    status = db.Column(db.String(20), default='active')  # active, acknowledged, resolved, snoozed
    
    # Related entities
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflow.id'), nullable=True)
    recommendation_id = db.Column(db.Integer, db.ForeignKey('recommendation.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # assigned user
    
    # Alert details
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    sla_timeline = db.Column(db.Integer)  # SLA in hours
    current_delay = db.Column(db.Integer)  # Current delay in hours
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    snoozed_until = db.Column(db.DateTime, nullable=True)
    
    # Escalation
    escalated_at = db.Column(db.DateTime, nullable=True)
    escalated_to = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    
    # Resolution
    resolved_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    resolution_notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    client = db.relationship('Client', backref='alerts')
    lead = db.relationship('Lead', backref='alerts')
    workflow = db.relationship('Workflow', backref='alerts')
    recommendation = db.relationship('Recommendation', backref='alerts')
    assigned_user = db.relationship('User', foreign_keys=[user_id], backref='assigned_alerts')
    escalated_user = db.relationship('User', foreign_keys=[escalated_to], backref='escalated_alerts')
    resolver = db.relationship('User', foreign_keys=[resolved_by], backref='resolved_alerts')
    
    @property
    def is_overdue(self):
        """Check if alert is overdue for escalation"""
        if self.severity == 'critical' and self.created_at < datetime.utcnow() - timedelta(hours=4):
            return True
        elif self.severity == 'warning' and self.created_at < datetime.utcnow() - timedelta(hours=24):
            return True
        return False
    
    @property
    def age_hours(self):
        """Get alert age in hours"""
        return int((datetime.utcnow() - self.created_at).total_seconds() / 3600)
    
    def acknowledge(self, user_id):
        """Acknowledge the alert"""
        self.status = 'acknowledged'
        self.acknowledged_at = datetime.utcnow()
        self.user_id = user_id
    
    def resolve(self, user_id, notes=None):
        """Resolve the alert"""
        self.status = 'resolved'
        self.resolved_at = datetime.utcnow()
        self.resolved_by = user_id
        self.resolution_notes = notes
    
    def escalate(self, escalated_to_user_id):
        """Escalate the alert"""
        self.escalated_at = datetime.utcnow()
        self.escalated_to = escalated_to_user_id
        self.severity = 'critical'
    
    def snooze(self, hours=24):
        """Snooze the alert for specified hours"""
        self.status = 'snoozed'
        self.snoozed_until = datetime.utcnow() + timedelta(hours=hours)

class SLAConfiguration(db.Model):
    """SLA configuration for different processes"""
    __tablename__ = 'sla_configuration'
    
    id = db.Column(db.Integer, primary_key=True)
    process_name = db.Column(db.String(100), nullable=False)
    process_stage = db.Column(db.String(100), nullable=False)
    sla_hours = db.Column(db.Integer, nullable=False)  # SLA in hours
    alert_trigger_percentage = db.Column(db.Integer, default=80)  # Alert at 80% of SLA
    escalation_hours = db.Column(db.Integer, nullable=True)  # Escalation after X hours
    
    # Notifications
    email_notification = db.Column(db.Boolean, default=True)
    sms_notification = db.Column(db.Boolean, default=False)
    in_app_notification = db.Column(db.Boolean, default=True)
    
    # Assignment
    default_assignee_role = db.Column(db.String(50), nullable=True)  # advisor, manager, admin
    escalation_role = db.Column(db.String(50), nullable=True)
    
    # Customization
    client_specific = db.Column(db.Boolean, default=False)  # Different SLA for VIP clients
    active = db.Column(db.Boolean, default=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def alert_trigger_hours(self):
        """Calculate when alert should trigger"""
        return int(self.sla_hours * (self.alert_trigger_percentage / 100))

class AlertNotification(db.Model):
    """Track alert notifications sent"""
    __tablename__ = 'alert_notification'
    
    id = db.Column(db.Integer, primary_key=True)
    alert_id = db.Column(db.Integer, db.ForeignKey('alert.id'), nullable=False)
    notification_type = db.Column(db.String(20), nullable=False)  # email, sms, in_app
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='sent')  # sent, delivered, failed
    
    # Relationships
    alert = db.relationship('Alert', backref='notifications')
    recipient = db.relationship('User', backref='received_notifications')

class AlertReport(db.Model):
    """Daily/weekly/monthly alert reports"""
    __tablename__ = 'alert_report'
    
    id = db.Column(db.Integer, primary_key=True)
    report_date = db.Column(db.Date, nullable=False)
    report_type = db.Column(db.String(20), nullable=False)  # daily, weekly, monthly
    
    # Metrics
    total_alerts = db.Column(db.Integer, default=0)
    new_alerts = db.Column(db.Integer, default=0)
    resolved_alerts = db.Column(db.Integer, default=0)
    escalated_alerts = db.Column(db.Integer, default=0)
    sla_compliance_rate = db.Column(db.Float, default=0.0)  # Percentage
    
    # Breakdown by type
    workflow_alerts = db.Column(db.Integer, default=0)
    recommendation_alerts = db.Column(db.Integer, default=0)
    communication_alerts = db.Column(db.Integer, default=0)
    portfolio_alerts = db.Column(db.Integer, default=0)
    system_alerts = db.Column(db.Integer, default=0)
    
    # Breakdown by severity
    critical_alerts = db.Column(db.Integer, default=0)
    warning_alerts = db.Column(db.Integer, default=0)
    info_alerts = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
