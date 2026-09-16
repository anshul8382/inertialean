#!/usr/bin/env python3
"""
Alert Service for SLA Monitoring and Process Delay Management
"""
from datetime import datetime, timedelta
from sqlalchemy import and_, or_, func
from models import db, Client, Workflow, Recommendation, User, Meeting
from alert_system_models import Alert, SLAConfiguration, AlertNotification
import logging

logger = logging.getLogger(__name__)

class AlertService:
    """Service class for alert management and SLA monitoring"""
    
    @staticmethod
    def check_workflow_sla():
        """Check workflow SLA compliance and generate alerts"""
        try:
            active_workflows = Workflow.query.filter(
            Workflow.current_stage != 'COMPLETED',
            Workflow.is_archived == False
        ).all()
            for workflow in active_workflows:
                AlertService._check_workflow_stage_sla(workflow)
        except Exception as e:
            logger.error(f"Error checking workflow SLA: {str(e)}")
    
    @staticmethod
    def _check_workflow_stage_sla(workflow):
        """Check SLA for specific workflow stage"""
        current_stage = workflow.current_stage
        stage_updated_at = workflow.updated_at
        
        sla_config = SLAConfiguration.query.filter_by(
            process_name='monthly_investment',
            process_stage=current_stage,
            active=True
        ).first()
        
        if not sla_config:
            return
        
        hours_since_update = (datetime.utcnow() - stage_updated_at).total_seconds() / 3600
        
        if hours_since_update >= sla_config.alert_trigger_hours:
            existing_alert = Alert.query.filter_by(
                workflow_id=workflow.id,
                alert_type='workflow_sla',
                alert_subtype=f'{current_stage}_delay',
                status='active'
            ).first()
            
            if not existing_alert:
                AlertService._create_workflow_alert(workflow, current_stage, sla_config, hours_since_update)
    
    @staticmethod
    def _create_workflow_alert(workflow, stage, sla_config, delay_hours):
        """Create workflow SLA alert"""
        try:
            severity = 'warning'
            if delay_hours >= sla_config.sla_hours:
                severity = 'critical'
            
            alert = Alert(
                alert_type='workflow_sla',
                alert_subtype=f'{stage}_delay',
                severity=severity,
                workflow_id=workflow.id,
                client_id=workflow.monthly_investment.client_id,
                title=f'Workflow {stage} Stage Delay',
                description=f'Workflow {workflow.id} has been in {stage} stage for {int(delay_hours)} hours.',
                sla_timeline=sla_config.sla_hours,
                current_delay=int(delay_hours),
                user_id=workflow.monthly_investment.client.advisor_id
            )
            
            db.session.add(alert)
            db.session.commit()
            
            logger.info(f"Created workflow alert for workflow {workflow.id}, stage {stage}")
            
        except Exception as e:
            logger.error(f"Error creating workflow alert: {str(e)}")
            db.session.rollback()
    
    @staticmethod
    def check_recommendation_sla():
        """Check recommendation SLA compliance"""
        try:
            pending_recommendations = Recommendation.query.filter_by(status='pending').all()
            for rec in pending_recommendations:
                AlertService._check_recommendation_alert(rec)
        except Exception as e:
            logger.error(f"Error checking recommendation SLA: {str(e)}")
    
    @staticmethod
    def _check_recommendation_alert(recommendation):
        """Check if recommendation needs alert"""
        sla_config = SLAConfiguration.query.filter_by(
            process_name='recommendation',
            process_stage='creation',
            active=True
        ).first()
        
        if not sla_config:
            return
        
        # Handle created_at field which might be None or a string
        if not recommendation.created_at:
            return
        
        created_at = recommendation.created_at
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        
        hours_since_creation = (datetime.utcnow() - created_at).total_seconds() / 3600
        
        if hours_since_creation >= sla_config.alert_trigger_hours:
            existing_alert = Alert.query.filter_by(
                recommendation_id=recommendation.id,
                alert_type='recommendation',
                alert_subtype='creation_delay',
                status='active'
            ).first()
            
            if not existing_alert:
                AlertService._create_recommendation_alert(recommendation, sla_config, hours_since_creation)
    
    @staticmethod
    def _create_recommendation_alert(recommendation, sla_config, delay_hours):
        """Create recommendation alert"""
        try:
            severity = 'warning'
            if delay_hours >= sla_config.sla_hours:
                severity = 'critical'
            
            alert = Alert(
                alert_type='recommendation',
                alert_subtype='creation_delay',
                severity=severity,
                recommendation_id=recommendation.id,
                client_id=recommendation.client_id,
                title=f'Recommendation Creation Delay',
                description=f'Recommendation {recommendation.id} has been pending for {int(delay_hours)} hours.',
                sla_timeline=sla_config.sla_hours,
                current_delay=int(delay_hours),
                user_id=recommendation.created_by
            )
            
            db.session.add(alert)
            db.session.commit()
            
        except Exception as e:
            logger.error(f"Error creating recommendation alert: {str(e)}")
            db.session.rollback()
    
    @staticmethod
    def get_user_alerts(user_id, status='active'):
        """Get alerts for a specific user"""
        return Alert.query.filter_by(
            user_id=user_id,
            status=status
        ).order_by(Alert.created_at.desc()).all()
    
    @staticmethod
    def get_all_active_alerts():
        """Get all active alerts"""
        return Alert.query.filter_by(status='active').order_by(Alert.created_at.desc()).all()
    
    @staticmethod
    def acknowledge_alert(alert_id, user_id):
        """Acknowledge an alert"""
        try:
            alert = Alert.query.get(alert_id)
            if alert:
                alert.acknowledge(user_id)
                db.session.commit()
                return True
        except Exception as e:
            logger.error(f"Error acknowledging alert: {str(e)}")
            db.session.rollback()
        return False
    
    @staticmethod
    def resolve_alert(alert_id, user_id, notes=None):
        """Resolve an alert"""
        try:
            alert = Alert.query.get(alert_id)
            if alert:
                alert.resolve(user_id, notes)
                db.session.commit()
                return True
        except Exception as e:
            logger.error(f"Error resolving alert: {str(e)}")
            db.session.rollback()
        return False
    
    @staticmethod
    def escalate_alert(alert_id, escalated_to_user_id):
        """Escalate an alert"""
        try:
            alert = Alert.query.get(alert_id)
            if alert:
                alert.escalate(escalated_to_user_id)
                db.session.commit()
                return True
        except Exception as e:
            logger.error(f"Error escalating alert: {str(e)}")
            db.session.rollback()
        return False
    
    @staticmethod
    def snooze_alert(alert_id, hours=24):
        """Snooze an alert"""
        try:
            alert = Alert.query.get(alert_id)
            if alert:
                alert.snooze(hours)
                db.session.commit()
                return True
        except Exception as e:
            logger.error(f"Error snoozing alert: {str(e)}")
            db.session.rollback()
        return False
    
    @staticmethod
    def initialize_default_sla_configurations():
        """Initialize default SLA configurations"""
        try:
            if SLAConfiguration.query.count() > 0:
                return
            
            default_configs = [
                {
                    'process_name': 'monthly_investment',
                    'process_stage': 'FUNDS',
                    'sla_hours': 48,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'monthly_investment',
                    'process_stage': 'RECOS',
                    'sla_hours': 72,
                    'alert_trigger_percentage': 67,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'monthly_investment',
                    'process_stage': 'NOTIFY',
                    'sla_hours': 48,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'monthly_investment',
                    'process_stage': 'EXEC',
                    'sla_hours': 72,
                    'alert_trigger_percentage': 67,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'monthly_investment',
                    'process_stage': 'UPDATE',
                    'sla_hours': 24,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'monthly_investment',
                    'process_stage': 'COMPLETED',
                    'sla_hours': 24,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'recommendation',
                    'process_stage': 'creation',
                    'sla_hours': 24,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                }
            ]
            
            for config_data in default_configs:
                config = SLAConfiguration(**config_data)
                db.session.add(config)
            
            db.session.commit()
            logger.info("Default SLA configurations initialized")
            
        except Exception as e:
            logger.error(f"Error initializing SLA configurations: {str(e)}")
            db.session.rollback()
    
    @staticmethod
    def check_meeting_reminders():
        """Check for meetings due in next 7 days and create alerts"""
        try:
            today = datetime.utcnow()
            seven_days_later = today + timedelta(days=7)
            
            # Get all meetings scheduled in the next 7 days
            upcoming_meetings = Meeting.query.filter(
                Meeting.meeting_date >= today,
                Meeting.meeting_date <= seven_days_later
            ).all()
            
            alerts_created = 0
            
            for meeting in upcoming_meetings:
                # Check if alert already exists for this meeting
                # Use a more specific identifier in the description to avoid duplicates
                meeting_id_str = f'meeting_id_{meeting.id}'
                existing_alert = Alert.query.filter(
                    Alert.alert_type == 'communication',
                    Alert.alert_subtype == 'meeting_reminder',
                    Alert.status == 'active',
                    Alert.description.like(f'%{meeting_id_str}%')
                ).first()
                
                if not existing_alert:
                    # Calculate days until meeting
                    days_until = (meeting.meeting_date.date() - today.date()).days
                    
                    # Create alert
                    client_name = meeting.client.name if meeting.client else (meeting.lead.name if meeting.lead else 'Unknown')
                    alert = Alert(
                        alert_type='communication',
                        alert_subtype='meeting_reminder',
                        severity='info' if days_until > 3 else 'warning',
                        client_id=meeting.client_id,
                        lead_id=meeting.lead_id,
                        title=f'Meeting Due: {meeting.title}',
                        description=f'[meeting_id_{meeting.id}] Meeting with {client_name} scheduled for {meeting.meeting_date.strftime("%Y-%m-%d %H:%M")} ({days_until} day{"s" if days_until != 1 else ""} away).',
                        user_id=meeting.client.advisor_id if meeting.client and meeting.client.advisor_id else None
                    )
                    
                    db.session.add(alert)
                    alerts_created += 1
                    logger.info(f"Created meeting reminder alert for meeting {meeting.id}")
            
            db.session.commit()
            logger.info(f"Created {alerts_created} meeting reminder alerts")
            
        except Exception as e:
            logger.error(f"Error checking meeting reminders: {str(e)}")
            db.session.rollback()
