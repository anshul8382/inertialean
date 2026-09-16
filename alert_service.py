#!/usr/bin/env python3
"""
Alert Service for SLA Monitoring and Process Delay Management
"""
from datetime import datetime, timedelta
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import joinedload
from models import db, Client, Workflow, Recommendation, User, Meeting, Invoice, ReviewWorkflow
from alert_system_models import Alert, SLAConfiguration, AlertNotification
import logging

logger = logging.getLogger(__name__)

class AlertService:
    """Service class for alert management and SLA monitoring"""
    
    @staticmethod
    def _get_ops_manager_user_id():
        """
        Get the operations manager user ID as fallback for unassigned alerts.
        Returns the first active user with 'manager' role, or None if not found.
        """
        try:
            from models import Role
            manager_role = Role.query.filter(
                Role.name.ilike('%manager%')
            ).first()
            
            if manager_role:
                manager_user = User.query.filter_by(
                    role_id=manager_role.id,
                    is_active=True
                ).first()
                if manager_user:
                    return manager_user.id
            
            # Fallback: look for admin users
            admin_user = User.query.filter_by(
                is_admin=True,
                is_active=True
            ).first()
            if admin_user:
                return admin_user.id
            
            return None
        except Exception as e:
            logger.warning(f"Error finding ops manager: {str(e)}")
            return None
    
    @staticmethod
    def _get_workflow_alert_assignee_user_id(workflow):
        """
        Best-effort assignee for workflow SLA alerts.

        Priority:
        1) Client.advisor_id (direct assignment)
        2) Most-recent active ClientAdvisorAssignment
        3) Client.user_id (creator/owner in legacy flows)
        """
        try:
            investment = getattr(workflow, "monthly_investment", None)
            client = getattr(investment, "client", None) if investment else None
            if not client:
                return None

            if getattr(client, "advisor_id", None):
                return client.advisor_id

            # Newer assignment model (history + active flag)
            from models import ClientAdvisorAssignment
            assignment = ClientAdvisorAssignment.query.filter_by(
                client_id=client.id,
                is_active=True
            ).order_by(ClientAdvisorAssignment.assigned_at.desc()).first()
            if assignment and getattr(assignment, "advisor_id", None):
                return assignment.advisor_id

            return getattr(client, "user_id", None)
        except Exception:
            # Never fail alert creation due to assignment lookup
            return None
    
    @staticmethod
    def _format_time_duration(hours):
        """Format hours as days if > 72 hours, otherwise as hours"""
        if hours > 72:
            days = round(hours / 24)
            return f"{days} days"
        else:
            return f"{int(hours)} hours"
    
    @staticmethod
    def check_workflow_sla():
        """Check workflow SLA compliance and generate alerts"""
        try:
            # EAGER LOAD monthly_investment relationship to prevent stale data
            # This ensures we always get fresh client_id when creating alerts
            active_workflows = Workflow.query.options(
                joinedload(Workflow.monthly_investment)
            ).filter(
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
        
        # Check if workflow's investment_date is in the future
        # Alerts should only be created when approaching or past the due date
        today = datetime.utcnow().date()
        investment_date = workflow.investment_date
        
        # If investment_date is in the future, don't create alerts yet
        # Only create alerts when we're approaching or past the due date
        if investment_date and investment_date > today:
            # Calculate days until due date
            days_until_due = (investment_date - today).days
            # Only create alerts if we're within 2 days of the due date
            # This gives a buffer for early alerts but prevents alerts for far-future workflows
            if days_until_due > 2:
                return  # Too far in the future, don't create alerts
        
        # Calculate hours since update, but only if we're at or past the due date
        # For future dates, calculate from the investment_date instead
        if investment_date and investment_date > today:
            # We're approaching the due date (within 2 days)
            # Calculate delay from investment_date, not from updated_at
            hours_since_due = ((datetime.utcnow() - datetime.combine(investment_date, datetime.min.time())).total_seconds() / 3600)
            # If still in future, use 0 delay (we're just approaching)
            if hours_since_due < 0:
                hours_since_due = 0
        else:
            # Past due date or today - use normal logic based on stage_updated_at
            hours_since_update = (datetime.utcnow() - stage_updated_at).total_seconds() / 3600
            hours_since_due = hours_since_update
        
        # Only create alert if we've exceeded the alert trigger threshold
        if hours_since_due >= sla_config.alert_trigger_hours:
            # IMPORTANT:
            # We dedupe workflow SLA alerts by (client_id + stage), not by workflow_id.
            # Reason: clients can have multiple concurrent workflows in the same stage,
            # which would otherwise create duplicated "same client, same stage" alerts
            # in the alerts dashboard.
            from models import MonthlyInvestment
            monthly_investment = MonthlyInvestment.query.get(workflow.monthly_investment_id)
            client_id = monthly_investment.client_id if monthly_investment else None

            existing_alert_query = Alert.query.filter(
                Alert.alert_type == 'workflow_sla',
                Alert.alert_subtype == f'{current_stage}_delay',
                Alert.status.in_(['active', 'acknowledged', 'snoozed'])
            )
            if client_id:
                existing_alert_query = existing_alert_query.filter(Alert.client_id == client_id)
            else:
                # If client_id can't be determined, fall back to workflow-level dedupe
                existing_alert_query = existing_alert_query.filter(Alert.workflow_id == workflow.id)

            existing_alert = existing_alert_query.first()
            
            if not existing_alert:
                AlertService._create_workflow_alert(workflow, current_stage, sla_config, hours_since_due)
    
    @staticmethod
    def _create_workflow_alert(workflow, stage, sla_config, delay_hours):
        """Create workflow SLA alert"""
        try:
            severity = 'warning'
            if delay_hours >= sla_config.sla_hours:
                severity = 'critical'
            
            time_duration = AlertService._format_time_duration(delay_hours)

            # Use TaskAssignmentService for rule-based assignment; fallback to client advisor / ops manager
            try:
                from services.task_assignment_service import get_assignee_for_workflow_stage
                assignee_user_id = get_assignee_for_workflow_stage(workflow)
            except Exception:
                assignee_user_id = None
            if not assignee_user_id:
                assignee_user_id = AlertService._get_workflow_alert_assignee_user_id(workflow)
            if not assignee_user_id:
                assignee_user_id = AlertService._get_ops_manager_user_id()
            
            # CRITICAL: Reload monthly_investment to ensure we have fresh data
            # Even with eager loading, we reload here as a safety measure to prevent stale data
            from models import MonthlyInvestment
            monthly_investment = MonthlyInvestment.query.get(workflow.monthly_investment_id)
            
            if not monthly_investment:
                logger.error(f"Cannot create alert for workflow {workflow.id}: monthly_investment {workflow.monthly_investment_id} not found")
                return
            
            client_id = monthly_investment.client_id
            if not client_id:
                logger.error(f"Cannot create alert for workflow {workflow.id}: monthly_investment {monthly_investment.id} has no client_id")
                return
            
            # Log for debugging and audit trail
            logger.info(f"Creating alert for workflow {workflow.id} (MI {monthly_investment.id}, client_id {client_id}), stage {stage}")
            
            alert = Alert(
                alert_type='workflow_sla',
                alert_subtype=f'{stage}_delay',
                severity=severity,
                workflow_id=workflow.id,
                client_id=client_id,  # Use reloaded client_id to prevent stale data
                title=f'Workflow {stage} Stage Delay',
                description=f'Workflow {workflow.id} has been in {stage} stage for {time_duration}.',
                sla_timeline=sla_config.sla_hours,
                current_delay=int(delay_hours),
                user_id=assignee_user_id
            )
            
            db.session.add(alert)
            db.session.commit()
            
            logger.info(f"Created workflow alert {alert.id} for workflow {workflow.id}, stage {stage}, client_id {client_id}")
            
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
                alert_subtype='creation_delay'
            ).filter(
                Alert.status.in_(['active', 'acknowledged', 'snoozed'])
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
            
            time_duration = AlertService._format_time_duration(delay_hours)
            
            # Use recommendation creator, fallback to ops manager
            assignee_user_id = recommendation.created_by if recommendation.created_by else AlertService._get_ops_manager_user_id()
            
            alert = Alert(
                alert_type='recommendation',
                alert_subtype='creation_delay',
                severity=severity,
                recommendation_id=recommendation.id,
                client_id=recommendation.client_id,
                title=f'Recommendation Creation Delay',
                description=f'Recommendation {recommendation.id} has been pending for {time_duration}.',
                sla_timeline=sla_config.sla_hours,
                current_delay=int(delay_hours),
                user_id=assignee_user_id
            )
            
            db.session.add(alert)
            db.session.commit()
            
        except Exception as e:
            logger.error(f"Error creating recommendation alert: {str(e)}")
            db.session.rollback()
    
    @staticmethod
    def get_user_alerts(user_id, status='active'):
        """Get alerts for a specific user"""
        if status == 'active':
            # Include both active and acknowledged alerts
            return Alert.query.filter_by(
                user_id=user_id
            ).filter(
                Alert.status.in_(['active', 'acknowledged'])
            ).order_by(Alert.created_at.desc()).all()
        else:
            return Alert.query.filter_by(
                user_id=user_id,
                status=status
            ).order_by(Alert.created_at.desc()).all()
    
    @staticmethod
    def get_all_active_alerts():
        """Get all active alerts (includes acknowledged)"""
        return Alert.query.filter(
            Alert.status.in_(['active', 'acknowledged'])
        ).order_by(Alert.created_at.desc()).all()
    
    @staticmethod
    def _client_name_for_alert_row(alert):
        """Resolve display client for an alert (direct client_id or via workflow → monthly_investment)."""
        try:
            if alert.client:
                return alert.client.name
            wf = alert.workflow
            if wf and wf.monthly_investment and wf.monthly_investment.client:
                return wf.monthly_investment.client.name
        except Exception as ex:
            logger.debug("client name for alert %s: %s", getattr(alert, "id", None), ex)
        return '—'

    @staticmethod
    def generate_daily_alert_report():
        """
        Generate and email daily alert summary (active/acknowledged alerts by type and severity).
        Used by the daily_alert_report Airflow DAG. Recipients from ReportRecipient (job_id='daily_alert_report').

        If the caller already pushed an app context (e.g. Airflow task), that app is reused — do not call
        create_app() again here or Flask-Session will try to re-register the ``sessions`` table and crash.
        """
        try:
            from flask import has_app_context
            from flask_mail import Message
            from main import create_app
            from extensions import mail
            from models import MonthlyInvestment, ReportRecipient
            from sqlalchemy.orm import joinedload

            def run():
                alerts = (
                    Alert.query.options(
                        joinedload(Alert.client),
                        joinedload(Alert.workflow)
                        .joinedload(Workflow.monthly_investment)
                        .joinedload(MonthlyInvestment.client),
                    )
                    .filter(Alert.status.in_(['active', 'acknowledged']))
                    .order_by(Alert.created_at.desc())
                    .all()
                )
                report_date = datetime.utcnow()

                by_type = {}
                by_severity = {'critical': 0, 'warning': 0, 'info': 0}
                for a in alerts:
                    by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1
                    by_severity[a.severity] = by_severity.get(a.severity, 0) + 1

                rows = []
                for a in alerts[:100]:
                    try:
                        client_name = AlertService._client_name_for_alert_row(a)
                        title_short = (a.title[:60] + '…') if len((a.title or '')) > 60 else (a.title or '—')
                        rows.append(
                            f'<tr><td>{a.alert_type}</td><td>{a.alert_subtype}</td><td>{a.severity}</td>'
                            f'<td>{a.status}</td><td>{title_short}</td><td>{client_name}</td></tr>'
                        )
                    except Exception as row_ex:
                        logger.warning("Skipping alert %s in daily report row: %s", getattr(a, "id", None), row_ex)

                table_rows = '\n'.join(rows) if rows else '<tr><td colspan="6">No active alerts</td></tr>'

                html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Daily Alert Report</title></head>
<body style="font-family: Arial, sans-serif; padding: 20px;">
<h2>Daily Alert Report</h2>
<p>Generated: {report_date.strftime('%Y-%m-%d %H:%M')} UTC</p>
<p><strong>Summary:</strong> {len(alerts)} active/acknowledged alert(s) | Critical: {by_severity["critical"]} | Warning: {by_severity["warning"]} | Info: {by_severity["info"]}</p>
<p>By type: {", ".join(f"{k}: {v}" for k, v in sorted(by_type.items())) or "—"}</p>
<table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
<thead><tr style="background: #eee;"><th>Type</th><th>Subtype</th><th>Severity</th><th>Status</th><th>Title</th><th>Client</th></tr></thead>
<tbody>{table_rows}</tbody>
</table>
<p style="color:#666;font-size:12px;">View and manage alerts in the Alerts dashboard.</p>
</body>
</html>
"""
                recipients = ReportRecipient.query.filter_by(job_id='daily_alert_report', is_active=True).all()
                recipient_emails = [r.email for r in recipients]
                if not recipient_emails:
                    recipient_emails = ['anshul@equities4wealth.com']
                msg = Message(
                    subject=f'Daily Alert Report – {report_date.strftime("%Y-%m-%d")}',
                    recipients=recipient_emails,
                    html=html,
                )
                try:
                    mail.send(msg)
                    logger.info(
                        "Daily alert report sent to %s recipient(s)",
                        len(recipient_emails),
                    )
                except Exception as mail_ex:
                    # Do not fail the Airflow task: report HTML was built; fix SMTP separately.
                    logger.exception(
                        "Daily alert report email failed (report generated): %s", mail_ex
                    )

            if has_app_context():
                run()
            else:
                app = create_app()
                with app.app_context():
                    run()
        except Exception as e:
            logger.error(f"Error generating daily alert report: {str(e)}", exc_info=True)
            raise

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
            if not alert:
                logger.error(f"Cannot resolve alert {alert_id}: Alert not found")
                return False
            
            # Validate user_id exists
            from models import User
            user = User.query.get(user_id)
            if not user:
                logger.error(f"Cannot resolve alert {alert_id}: User {user_id} not found")
                return False
            
            # Check if alert is already resolved
            if alert.status == 'resolved':
                logger.warning(f"Alert {alert_id} is already resolved")
                return True
            
            # Resolve the alert
            alert.resolve(user_id, notes)
            db.session.commit()
            logger.info(f"Successfully resolved alert {alert_id} by user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error resolving alert {alert_id}: {str(e)}", exc_info=True)
            db.session.rollback()
            return False
    
    @staticmethod
    def close_previous_stage_alerts(workflow_id, previous_stage, user_id=None):
        """Close/resolve alerts for a previous workflow stage when moving to next stage"""
        try:
            # Find all alerts for this workflow and previous stage (active, acknowledged, or snoozed)
            previous_stage_alerts = Alert.query.filter(
                Alert.workflow_id == workflow_id,
                Alert.alert_type == 'workflow_sla',
                Alert.alert_subtype == f'{previous_stage}_delay',
                Alert.status.in_(['active', 'acknowledged', 'snoozed'])
            ).all()
            
            if previous_stage_alerts:
                notes = f'Automatically resolved: Workflow moved from {previous_stage} stage to next stage'
                for alert in previous_stage_alerts:
                    if user_id:
                        alert.resolve(user_id, notes)
                    else:
                        # If no user_id provided, use system user (ID 1) or alert's user_id
                        resolve_user_id = alert.user_id if alert.user_id else 1
                        alert.resolve(resolve_user_id, notes)
                
                db.session.commit()
                logger.info(f"Closed {len(previous_stage_alerts)} alert(s) for workflow {workflow_id}, previous stage: {previous_stage}")
                return len(previous_stage_alerts)
            
            return 0
            
        except Exception as e:
            logger.error(f"Error closing previous stage alerts: {str(e)}")
            db.session.rollback()
            return 0
    
    @staticmethod
    def cleanup_old_resolved_alerts(days_old=90):
        """
        Clean up old resolved alerts to prevent database bloat.
        
        Args:
            days_old (int): Number of days after which resolved alerts should be deleted (default: 90)
        
        Returns:
            int: Number of alerts deleted
        """
        try:
            from datetime import datetime, timedelta
            
            cutoff_date = datetime.utcnow() - timedelta(days=days_old)
            
            # Find resolved alerts older than cutoff date
            old_resolved_alerts = Alert.query.filter(
                Alert.status == 'resolved',
                Alert.resolved_at.isnot(None),
                Alert.resolved_at < cutoff_date
            ).all()
            
            deleted_count = len(old_resolved_alerts)
            
            if deleted_count > 0:
                for alert in old_resolved_alerts:
                    db.session.delete(alert)
                
                db.session.commit()
                logger.info(f"Cleaned up {deleted_count} old resolved alerts (older than {days_old} days)")
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error cleaning up old resolved alerts: {str(e)}")
            db.session.rollback()
            return 0
    
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
            # Do not early-return if configurations already exist.
            # We want to be able to add *new* default configurations over time without wiping existing ones.
            
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
                },
                {
                    'process_name': 'service_ticket',
                    'process_stage': 'critical',
                    'sla_hours': 4,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'service_ticket',
                    'process_stage': 'high',
                    'sla_hours': 24,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'service_ticket',
                    'process_stage': 'medium',
                    'sla_hours': 72,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'service_ticket',
                    'process_stage': 'low',
                    'sla_hours': 168,
                    'alert_trigger_percentage': 75,
                    'default_assignee_role': 'advisor'
                },
                # Billing / payments
                {
                    'process_name': 'billing',
                    'process_stage': 'invoice_payment',
                    'sla_hours': 72,
                    'alert_trigger_percentage': 0,  # Create alert immediately after due date passes
                    'default_assignee_role': 'advisor'
                },
                # Communication / meetings
                {
                    'process_name': 'communication',
                    'process_stage': 'meeting_reminder',
                    'sla_hours': 24,
                    'alert_trigger_percentage': 0,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'communication',
                    'process_stage': 'meeting_cadence',
                    'sla_hours': 2160,  # 90 days
                    'alert_trigger_percentage': 100,
                    'default_assignee_role': 'advisor'
                },
                # Reviews
                {
                    'process_name': 'review',
                    'process_stage': 'overdue',
                    'sla_hours': 24,
                    'alert_trigger_percentage': 0,
                    'default_assignee_role': 'advisor'
                },
                {
                    'process_name': 'review',
                    'process_stage': 'upcoming',
                    'sla_hours': 48,
                    'alert_trigger_percentage': 0,
                    'default_assignee_role': 'advisor'
                }
            ]
            
            for config_data in default_configs:
                # Check if config already exists
                existing = SLAConfiguration.query.filter_by(
                    process_name=config_data['process_name'],
                    process_stage=config_data['process_stage']
                ).first()
                if not existing:
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
            sla_config = SLAConfiguration.query.filter_by(
                process_name='communication',
                process_stage='meeting_reminder',
                active=True
            ).first()
            sla_hours = sla_config.sla_hours if sla_config else 24
            
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
                    Alert.status.in_(['active', 'acknowledged', 'snoozed']),
                    Alert.description.like(f'%{meeting_id_str}%')
                ).first()
                
                if not existing_alert:
                    # Calculate days until meeting
                    days_until = (meeting.meeting_date.date() - today.date()).days
                    
                    # Create alert
                    client_name = meeting.client.name if meeting.client else (meeting.lead.name if meeting.lead else 'Unknown')
                    assignee_user_id = None
                    if meeting.client and meeting.client.advisor_id:
                        assignee_user_id = meeting.client.advisor_id
                    elif meeting.lead and getattr(meeting.lead, 'user_id', None):
                        assignee_user_id = meeting.lead.user_id
                    if not assignee_user_id:
                        assignee_user_id = AlertService._get_ops_manager_user_id()
                    
                    alert = Alert(
                        alert_type='communication',
                        alert_subtype='meeting_reminder',
                        severity='info' if days_until > 3 else 'warning',
                        client_id=meeting.client_id,
                        lead_id=meeting.lead_id,
                        title=f'Meeting Due: {meeting.title}',
                        description=f'[meeting_id_{meeting.id}] Meeting with {client_name} scheduled for {meeting.meeting_date.strftime("%Y-%m-%d %H:%M")} ({days_until} day{"s" if days_until != 1 else ""} away).',
                        sla_timeline=int(sla_hours) if sla_hours else None,
                        current_delay=0,
                        user_id=assignee_user_id
                    )
                    
                    db.session.add(alert)
                    alerts_created += 1
                    logger.info(f"Created meeting reminder alert for meeting {meeting.id}")
            
            db.session.commit()
            logger.info(f"Created {alerts_created} meeting reminder alerts")
            
        except Exception as e:
            logger.error(f"Error checking meeting reminders: {str(e)}")
            db.session.rollback()

    @staticmethod
    def check_invoice_payment_sla():
        """Check invoice due dates and create payment delay alerts"""
        try:
            now = datetime.utcnow()
            today = now.date()

            sla_config = SLAConfiguration.query.filter_by(
                process_name='billing',
                process_stage='invoice_payment',
                active=True
            ).first()
            if not sla_config:
                return

            # Invoices that are past due and not paid/cancelled
            overdue_invoices = Invoice.query.filter(
                Invoice.due_date.isnot(None),
                Invoice.due_date < today,
                Invoice.status.in_(['sent', 'overdue', 'draft'])
            ).all()

            for inv in overdue_invoices:
                # Calculate hours since due date
                hours_since_due = (now - datetime.combine(inv.due_date, datetime.min.time())).total_seconds() / 3600
                if hours_since_due < sla_config.alert_trigger_hours:
                    continue

                existing_alert = Alert.query.filter_by(
                    client_id=inv.client_id,
                    alert_type='billing',
                    alert_subtype='invoice_payment_delay'
                ).filter(
                    Alert.status.in_(['active', 'acknowledged', 'snoozed']),
                    Alert.description.like(f'%invoice_{inv.id}%')
                ).first()

                if existing_alert:
                    continue

                days_overdue = (today - inv.due_date).days
                severity = 'warning'
                if hours_since_due >= sla_config.sla_hours:
                    severity = 'critical'

                assignee_user_id = inv.client.advisor_id if inv.client and inv.client.advisor_id else None
                # Fallback to ops manager if no assignee found
                if not assignee_user_id:
                    assignee_user_id = AlertService._get_ops_manager_user_id()

                alert = Alert(
                    alert_type='billing',
                    alert_subtype='invoice_payment_delay',
                    severity=severity,
                    client_id=inv.client_id,
                    title=f'Payment Delay: Invoice {inv.invoice_number}',
                    description=f'[invoice_{inv.id}] Invoice {inv.invoice_number} is {days_overdue} day{"s" if days_overdue != 1 else ""} overdue (due: {inv.due_date.isoformat()}). Status: {inv.status}. Amount: {inv.total_amount}.',
                    sla_timeline=sla_config.sla_hours,
                    current_delay=int(hours_since_due),
                    user_id=assignee_user_id
                )
                db.session.add(alert)

            db.session.commit()

        except Exception as e:
            logger.error(f"Error checking invoice payment SLA: {str(e)}")
            db.session.rollback()

    @staticmethod
    def check_meeting_cadence_sla():
        """
        Create alerts when a client has no upcoming meeting and hasn't had one in the configured SLA window.
        This helps catch "missing meeting schedules".
        """
        try:
            now = datetime.utcnow()

            sla_config = SLAConfiguration.query.filter_by(
                process_name='communication',
                process_stage='meeting_cadence',
                active=True
            ).first()
            if not sla_config:
                return

            clients = Client.query.all()

            for client in clients:
                # Skip if there is an upcoming meeting scheduled
                upcoming = Meeting.query.filter(
                    Meeting.client_id == client.id,
                    Meeting.meeting_date >= now
                ).first()
                if upcoming:
                    continue

                last_meeting = Meeting.query.filter(
                    Meeting.client_id == client.id,
                    Meeting.meeting_date < now
                ).order_by(Meeting.meeting_date.desc()).first()

                # Use last meeting date if exists; otherwise use client created_at as a proxy
                reference_dt = last_meeting.meeting_date if last_meeting else client.created_at
                if not reference_dt:
                    continue

                hours_since = (now - reference_dt).total_seconds() / 3600
                if hours_since < sla_config.alert_trigger_hours:
                    continue

                existing_alert = Alert.query.filter_by(
                    client_id=client.id,
                    alert_type='communication',
                    alert_subtype='meeting_cadence'
                ).filter(
                    Alert.status.in_(['active', 'acknowledged', 'snoozed'])
                ).first()
                if existing_alert:
                    continue

                severity = 'warning'
                if hours_since >= sla_config.sla_hours:
                    severity = 'critical'

                days_since = int(hours_since // 24)
                assignee_user_id = client.advisor_id if client.advisor_id else None
                # Fallback to ops manager if no assignee found
                if not assignee_user_id:
                    assignee_user_id = AlertService._get_ops_manager_user_id()
                
                alert = Alert(
                    alert_type='communication',
                    alert_subtype='meeting_cadence',
                    severity=severity,
                    client_id=client.id,
                    title='Meeting Not Scheduled',
                    description=f'No upcoming meeting is scheduled. Last meeting/reference is {days_since} day{"s" if days_since != 1 else ""} ago. Please schedule a meeting to maintain service cadence.',
                    sla_timeline=sla_config.sla_hours,
                    current_delay=int(hours_since),
                    user_id=assignee_user_id
                )
                db.session.add(alert)

            db.session.commit()

        except Exception as e:
            logger.error(f"Error checking meeting cadence SLA: {str(e)}")
            db.session.rollback()

    @staticmethod
    def check_review_sla():
        """Create alerts for upcoming and overdue review workflows"""
        try:
            now = datetime.utcnow()
            today = now.date()

            overdue_cfg = SLAConfiguration.query.filter_by(
                process_name='review',
                process_stage='overdue',
                active=True
            ).first()
            upcoming_cfg = SLAConfiguration.query.filter_by(
                process_name='review',
                process_stage='upcoming',
                active=True
            ).first()

            # Overdue workflows (initiated/sent) where review_date < today
            overdue_workflows = ReviewWorkflow.query.filter(
                ReviewWorkflow.status.in_(['initiated', 'sent']),
                ReviewWorkflow.review_date < today
            ).all()

            for workflow in overdue_workflows:
                existing = Alert.query.filter_by(
                    client_id=workflow.client_id,
                    alert_type='review_overdue'
                ).filter(
                    Alert.status.in_(['active', 'acknowledged', 'snoozed'])
                ).first()
                if existing:
                    continue

                days_overdue = (today - workflow.review_date).days
                if days_overdue > 30:
                    severity = 'critical'
                elif days_overdue > 7:
                    severity = 'warning'
                else:
                    severity = 'info'

                sla_hours = overdue_cfg.sla_hours if overdue_cfg else 24
                assignee_user_id = workflow.created_by if workflow.created_by else None
                # Fallback to ops manager if no assignee found
                if not assignee_user_id:
                    assignee_user_id = AlertService._get_ops_manager_user_id()
                
                alert = Alert(
                    title=f"Review Overdue: {workflow.client.name}",
                    description=f"Client review is {days_overdue} days overdue. Review date: {workflow.review_date.strftime('%Y-%m-%d')}. Current status: {workflow.status}",
                    alert_type='review_overdue',
                    alert_subtype='overdue',
                    severity=severity,
                    status='active',
                    client_id=workflow.client_id,
                    user_id=assignee_user_id,
                    sla_timeline=sla_hours,
                    current_delay=int(days_overdue * 24)
                )
                db.session.add(alert)

            # Upcoming reviews due within 7 days
            upcoming_workflows = ReviewWorkflow.query.filter(
                ReviewWorkflow.status.in_(['initiated', 'sent']),
                ReviewWorkflow.review_date >= today,
                ReviewWorkflow.review_date <= today + timedelta(days=7)
            ).all()

            for workflow in upcoming_workflows:
                existing = Alert.query.filter_by(
                    client_id=workflow.client_id,
                    alert_type='review_upcoming'
                ).filter(
                    Alert.status.in_(['active', 'acknowledged', 'snoozed'])
                ).first()
                if existing:
                    continue

                days_until_due = (workflow.review_date - today).days
                sla_hours = upcoming_cfg.sla_hours if upcoming_cfg else 48
                assignee_user_id = workflow.created_by if workflow.created_by else None
                # Fallback to ops manager if no assignee found
                if not assignee_user_id:
                    assignee_user_id = AlertService._get_ops_manager_user_id()
                
                alert = Alert(
                    title=f"Review Due Soon: {workflow.client.name}",
                    description=f"Client review is due in {days_until_due} days. Review date: {workflow.review_date.strftime('%Y-%m-%d')}. Current status: {workflow.status}",
                    alert_type='review_upcoming',
                    alert_subtype='upcoming',
                    severity='warning',
                    status='active',
                    client_id=workflow.client_id,
                    user_id=assignee_user_id,
                    sla_timeline=sla_hours,
                    current_delay=0
                )
                db.session.add(alert)

            db.session.commit()

        except Exception as e:
            logger.error(f"Error checking review SLA: {str(e)}")
            db.session.rollback()
    
    @staticmethod
    def check_ticket_sla():
        """Check service ticket SLA compliance and generate alerts"""
        try:
            from models import ServiceTicket
            
            open_tickets = ServiceTicket.query.filter(
                ServiceTicket.status.in_(['open', 'in_progress'])
            ).all()
            
            for ticket in open_tickets:
                # Check if ticket is overdue or approaching deadline
                if ticket.sla_deadline:
                    hours_until_deadline = (ticket.sla_deadline - datetime.utcnow()).total_seconds() / 3600
                    
                    # Mark as breached if overdue
                    if hours_until_deadline < 0 and not ticket.sla_breached:
                        ticket.sla_breached = True
                        db.session.commit()
                        
                        # Create or update alert
                        existing_alert = Alert.query.filter_by(
                            alert_type='service_ticket',
                            client_id=ticket.client_id
                        ).filter(
                            Alert.description.like(f'%{ticket.ticket_number}%'),
                            Alert.status == 'active'
                        ).first()
                        
                        if not existing_alert:
                            assignee_user_id = ticket.assigned_to if ticket.assigned_to else None
                            # Fallback to ops manager if no assignee found
                            if not assignee_user_id:
                                assignee_user_id = AlertService._get_ops_manager_user_id()
                            
                            alert = Alert(
                                alert_type='service_ticket',
                                alert_subtype=ticket.alert_type,
                                severity='critical' if ticket.priority == 'critical' else 'warning',
                                client_id=ticket.client_id,
                                title=f'Ticket SLA Breached: {ticket.ticket_number}',
                                description=f'Service ticket {ticket.ticket_number} has breached its SLA deadline. Ticket: {ticket.title}',
                                sla_timeline=ticket.sla_hours,
                                current_delay=int(abs(hours_until_deadline)),
                                user_id=assignee_user_id
                            )
                            db.session.add(alert)
                            db.session.commit()
                            logger.info(f"Created SLA breach alert for ticket {ticket.ticket_number}")
                    
                    # Create warning alert if approaching deadline (within 25% of SLA)
                    elif hours_until_deadline > 0 and hours_until_deadline <= (ticket.sla_hours * 0.25):
                        existing_alert = Alert.query.filter_by(
                            alert_type='service_ticket',
                            client_id=ticket.client_id
                        ).filter(
                            Alert.description.like(f'%{ticket.ticket_number}%'),
                            Alert.status.in_(['active', 'acknowledged', 'snoozed'])
                        ).first()
                        
                        if not existing_alert:
                            assignee_user_id = ticket.assigned_to if ticket.assigned_to else None
                            # Fallback to ops manager if no assignee found
                            if not assignee_user_id:
                                assignee_user_id = AlertService._get_ops_manager_user_id()
                            
                            alert = Alert(
                                alert_type='service_ticket',
                                alert_subtype=ticket.alert_type,
                                severity='warning' if ticket.priority != 'critical' else 'critical',
                                client_id=ticket.client_id,
                                title=f'Ticket Approaching SLA: {ticket.ticket_number}',
                                description=f'Service ticket {ticket.ticket_number} is approaching its SLA deadline ({hours_until_deadline:.1f}h remaining). Ticket: {ticket.title}',
                                sla_timeline=ticket.sla_hours,
                                user_id=assignee_user_id
                            )
                            db.session.add(alert)
                            db.session.commit()
                            logger.info(f"Created SLA warning alert for ticket {ticket.ticket_number}")
                            
        except Exception as e:
            logger.error(f"Error checking ticket SLA: {str(e)}")
            db.session.rollback()
    
    @staticmethod
    def cleanup_premature_workflow_alerts():
        """
        Clean up alerts for workflows with future investment dates.
        Alerts should only be active when approaching or past the due date.
        This resolves alerts that were created too early for next month cycles.
        """
        try:
            today = datetime.utcnow().date()
            resolved_count = 0
            
            # Get all active workflow SLA alerts
            active_workflow_alerts = Alert.query.filter(
                Alert.alert_type == 'workflow_sla',
                Alert.status.in_(['active', 'acknowledged', 'snoozed'])
            ).all()
            
            for alert in active_workflow_alerts:
                if not alert.workflow_id:
                    continue
                
                workflow = Workflow.query.get(alert.workflow_id)
                if not workflow:
                    continue
                
                investment_date = workflow.investment_date
                if not investment_date:
                    continue
                
                # If investment_date is more than 2 days in the future, resolve the alert
                if investment_date > today:
                    days_until_due = (investment_date - today).days
                    if days_until_due > 2:
                        # Resolve the alert - it was created too early
                        system_user_id = AlertService._get_ops_manager_user_id() or 1
                        notes = f'Automatically resolved: Workflow investment date ({investment_date}) is {days_until_due} days in the future. Alert will be recreated when approaching due date (within 2 days).'
                        alert.resolve(system_user_id, notes)
                        resolved_count += 1
                        logger.info(f"Resolved premature alert {alert.id} for workflow {workflow.id} (investment_date: {investment_date}, days until due: {days_until_due})")
            
            if resolved_count > 0:
                db.session.commit()
                logger.info(f"Cleaned up {resolved_count} premature workflow alerts")
            else:
                logger.info("No premature workflow alerts found to clean up")
            
            return resolved_count
            
        except Exception as e:
            logger.error(f"Error cleaning up premature workflow alerts: {str(e)}")
            db.session.rollback()
            return 0
