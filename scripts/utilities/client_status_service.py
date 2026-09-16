"""
Client Status Service
Calculates client status based on SLA violations and alert data
"""

from datetime import datetime, timedelta
from sqlalchemy import func, and_, or_
from flask import current_app
from models import db, Client
from alert_system_models import Alert
import logging

logger = logging.getLogger(__name__)

class ClientStatusService:
    """Service to calculate client status based on SLA violations"""
    
    # Status thresholds (configurable)
    STATUS_THRESHOLDS = {
        'excellent': {'max_critical': 0, 'max_warning': 1, 'max_total': 1},
        'good': {'max_critical': 0, 'max_warning': 3, 'max_total': 3},
        'warning': {'max_critical': 2, 'max_warning': 5, 'max_total': 5},
        'critical': {'max_critical': 3, 'max_warning': 10, 'max_total': 10},
        'inactive': {'max_critical': 0, 'max_warning': 0, 'max_total': 0, 'days_inactive': 30}
    }
    
    # Scoring weights
    SCORING_WEIGHTS = {
        'critical_alert': 3,
        'warning_alert': 1,
        'info_alert': 0.5,
        'age_penalty_per_day': 0.1,
        'resolution_bonus': 0.5
    }
    
    @classmethod
    def calculate_client_status(cls, client_id):
        """
        Calculate comprehensive client status based on SLA violations
        
        Args:
            client_id (int): Client ID to analyze
            
        Returns:
            dict: Status information with details and violations
        """
        try:
            # Ensure we're in application context
            if not current_app:
                return {'error': 'Application context not available'}
                
            # Get client
            client = Client.query.get(client_id)
            if not client:
                return {'error': 'Client not found'}
            
            # Get all alerts for this client
            alerts = Alert.query.filter_by(client_id=client_id).all()
            
            # Separate active and resolved alerts
            active_alerts = [a for a in alerts if a.status == 'active']
            resolved_alerts = [a for a in alerts if a.status in ['resolved', 'acknowledged']]
            
            # Calculate metrics
            metrics = cls._calculate_metrics(active_alerts, resolved_alerts)
            
            # Determine status
            status = cls._determine_status(metrics, active_alerts)
            
            # Get detailed violations
            violations = cls._get_violation_details(active_alerts)
            
            # Generate recommendations
            recommendations = cls._generate_recommendations(metrics, violations)
            
            # Calculate score
            score = cls._calculate_score(metrics, active_alerts)
            
            return {
                'client_id': client_id,
                'client_name': client.name,
                'status': status,
                'score': score,
                'details': metrics,
                'violations': violations,
                'recommendations': recommendations,
                'last_updated': datetime.utcnow().isoformat() + 'Z'
            }
            
        except Exception as e:
            logger.error(f"Error calculating client status for {client_id}: {str(e)}")
            return {'error': f'Failed to calculate status: {str(e)}'}
    
    @classmethod
    def _calculate_metrics(cls, active_alerts, resolved_alerts):
        """Calculate key metrics from alerts"""
        now = datetime.utcnow()
        
        # Count alerts by severity
        critical_active = len([a for a in active_alerts if a.severity == 'critical'])
        warning_active = len([a for a in active_alerts if a.severity == 'warning'])
        info_active = len([a for a in active_alerts if a.severity == 'info'])
        
        # Calculate resolution rate (last 30 days)
        thirty_days_ago = now - timedelta(days=30)
        recent_resolved = len([a for a in resolved_alerts if a.resolved_at and a.resolved_at >= thirty_days_ago])
        recent_total = len([a for a in active_alerts + resolved_alerts if a.created_at >= thirty_days_ago])
        resolution_rate = recent_resolved / recent_total if recent_total > 0 else 1.0
        
        # Calculate average age of active alerts
        if active_alerts:
            total_age_hours = sum([(now - a.created_at).total_seconds() / 3600 for a in active_alerts])
            avg_age_hours = total_age_hours / len(active_alerts)
        else:
            avg_age_hours = 0
        
        return {
            'critical_alerts': critical_active,
            'warning_alerts': warning_active,
            'info_alerts': info_active,
            'total_active_alerts': len(active_alerts),
            'total_resolved_alerts': len(resolved_alerts),
            'resolution_rate': round(resolution_rate, 2),
            'avg_alert_age_hours': round(avg_age_hours, 1),
            'oldest_alert_hours': max([(now - a.created_at).total_seconds() / 3600 for a in active_alerts], default=0)
        }
    
    @classmethod
    def _determine_status(cls, metrics, active_alerts):
        """Determine client status based on metrics"""
        critical = metrics['critical_alerts']
        warning = metrics['warning_alerts']
        total = metrics['total_active_alerts']
        
        # Check for inactive status (no alerts but no recent activity)
        if total == 0:
            # Check if client has been inactive for too long
            # This would require additional data like last transaction, last login, etc.
            # For now, we'll consider clients with no alerts as 'good'
            return 'good'
        
        # Check status thresholds
        for status, thresholds in cls.STATUS_THRESHOLDS.items():
            if status == 'inactive':
                continue  # Skip inactive check for now
                
            if (critical <= thresholds['max_critical'] and 
                warning <= thresholds['max_warning'] and 
                total <= thresholds['max_total']):
                return status
        
        # If no threshold matches, return critical
        return 'critical'
    
    @classmethod
    def _get_violation_details(cls, active_alerts):
        """Get detailed information about violations"""
        violations = []
        now = datetime.utcnow()
        
        for alert in active_alerts:
            age_hours = (now - alert.created_at).total_seconds() / 3600
            
            # Determine impact level
            if alert.severity == 'critical':
                impact = 'high'
            elif alert.severity == 'warning':
                impact = 'medium'
            else:
                impact = 'low'
            
            # Add age penalty
            if age_hours > 72:  # More than 3 days
                impact = 'high'
            elif age_hours > 24:  # More than 1 day
                impact = 'medium' if impact == 'low' else impact
            
            # Generate specific improvement action
            improvement_action = cls._get_improvement_action(alert, age_hours)
            
            violations.append({
                'alert_id': alert.id,
                'type': alert.alert_type,
                'subtype': alert.alert_subtype,
                'severity': alert.severity,
                'title': alert.title,
                'description': alert.description,
                'age_hours': round(age_hours, 1),
                'impact': impact,
                'sla_timeline': alert.sla_timeline,
                'current_delay': alert.current_delay,
                'created_at': alert.created_at.isoformat(),
                'improvement_action': improvement_action,
                'priority': 'HIGH' if impact == 'high' else 'MEDIUM' if impact == 'medium' else 'LOW'
            })
        
        # Sort by impact and age
        violations.sort(key=lambda x: (
            {'high': 0, 'medium': 1, 'low': 2}[x['impact']],
            -x['age_hours']
        ))
        
        return violations
    
    @classmethod
    def _get_improvement_action(cls, alert, age_hours):
        """Generate specific improvement action for an alert"""
        if alert.alert_type == 'workflow_sla':
            if 'FUNDS' in alert.title:
                return "Contact client to resolve funding issues and update workflow status"
            elif 'EXEC' in alert.title:
                return "Execute pending transactions and update execution status"
            elif 'NOTIFY' in alert.title:
                return "Send required notifications to client and update communication log"
            else:
                return "Review workflow stage and take appropriate action to resolve SLA breach"
        elif alert.alert_type == 'lead_followup':
            return "Contact lead immediately and update lead status in CRM"
        elif alert.alert_type == 'recommendation':
            return "Review and send pending recommendations to client"
        else:
            if age_hours > 72:
                return "URGENT: Escalate to management and take immediate corrective action"
            elif age_hours > 24:
                return "High priority: Address within next business day"
            else:
                return "Standard priority: Address within SLA timeline"
    
    @classmethod
    def _generate_recommendations(cls, metrics, violations):
        """Generate actionable recommendations"""
        recommendations = []
        
        # Critical alerts recommendations
        if metrics['critical_alerts'] > 0:
            recommendations.append("🚨 CRITICAL: Address critical alerts immediately - these are blocking client operations and impacting service quality")
        
        # Warning alerts recommendations
        if metrics['warning_alerts'] > 2:
            recommendations.append("⚠️ WARNING: Multiple warning alerts detected - review and resolve pending issues to prevent escalation")
        
        # Age-based recommendations
        if metrics['avg_alert_age_hours'] > 48:
            recommendations.append("⏰ URGENT: Several alerts are aging (avg {:.1f}h) - prioritize resolution to maintain SLA compliance".format(metrics['avg_alert_age_hours']))
        
        # Resolution rate recommendations
        if metrics['resolution_rate'] < 0.7:
            recommendations.append("📈 IMPROVEMENT NEEDED: Alert resolution rate is {:.1f}% - consider process optimization and resource allocation".format(metrics['resolution_rate'] * 100))
        
        # Specific violation recommendations
        violation_types = set([v['type'] for v in violations])
        if 'workflow_sla' in violation_types:
            workflow_violations = [v for v in violations if v['type'] == 'workflow_sla']
            recommendations.append("🔄 WORKFLOW ISSUES: {} workflow SLA violations detected - review process efficiency and resource allocation".format(len(workflow_violations)))
        
        # Detailed violation analysis
        if violations:
            critical_violations = [v for v in violations if v['severity'] == 'critical']
            if critical_violations:
                recommendations.append("🔍 DETAILED ANALYSIS: {} critical violations need immediate attention - check alert details for specific actions".format(len(critical_violations)))
            
            old_violations = [v for v in violations if v['age_hours'] > 72]
            if old_violations:
                recommendations.append("📅 OVERDUE ITEMS: {} violations are over 3 days old - these require immediate escalation and resolution".format(len(old_violations)))
        
        # Performance recommendations
        if metrics['total_active_alerts'] > 5:
            recommendations.append("📊 HIGH VOLUME: {} active alerts indicate potential systemic issues - consider process review and capacity planning".format(metrics['total_active_alerts']))
        
        # General recommendations
        if not recommendations:
            recommendations.append("✅ EXCELLENT: Client status is healthy - maintain current service levels and continue monitoring")
        
        return recommendations
    
    @classmethod
    def _calculate_score(cls, metrics, active_alerts):
        """Calculate numerical score (0-100, higher is better)"""
        base_score = 100
        
        # Deduct points for active alerts
        score_deduction = 0
        score_deduction += metrics['critical_alerts'] * cls.SCORING_WEIGHTS['critical_alert'] * 10
        score_deduction += metrics['warning_alerts'] * cls.SCORING_WEIGHTS['warning_alert'] * 5
        score_deduction += metrics['info_alerts'] * cls.SCORING_WEIGHTS['info_alert'] * 2
        
        # Age penalty
        if active_alerts:
            avg_age_days = metrics['avg_alert_age_hours'] / 24
            score_deduction += avg_age_days * cls.SCORING_WEIGHTS['age_penalty_per_day'] * 10
        
        # Resolution bonus
        resolution_bonus = metrics['resolution_rate'] * cls.SCORING_WEIGHTS['resolution_bonus'] * 10
        
        final_score = max(0, min(100, base_score - score_deduction + resolution_bonus))
        return round(final_score, 1)
    
    @classmethod
    def get_all_clients_status(cls):
        """Get status for all clients"""
        try:
            # Ensure we're in application context
            if not current_app:
                return []
                
            clients = Client.query.all()
            results = []
            
            for client in clients:
                status_data = cls.calculate_client_status(client.id)
                if 'error' not in status_data:
                    results.append(status_data)
            
            # Sort by score (descending) and status priority
            status_priority = {'critical': 0, 'warning': 1, 'good': 2, 'excellent': 3}
            results.sort(key=lambda x: (status_priority.get(x['status'], 4), -x['score']))
            
            return results
        except Exception as e:
            logger.error(f"Error getting all clients status: {str(e)}")
            return []
