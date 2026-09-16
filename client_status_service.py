"""
Client Status Service
Calculates client status based on SLA violations and alert data
"""

from collections import defaultdict
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
        'resolution_bonus': 0.5,
        # Resolved-alert penalty: penalize SLA breaches on resolution time
        # (breach_hours * severity_weight * resolved_breach_penalty_per_hour), capped per alert.
        'resolved_breach_penalty_per_hour': 0.05,
        'resolved_breach_penalty_cap_per_alert': 15.0,
        # Kept for backwards compatibility; rolling windows are domain-based below.
        'resolved_window_days': 30
    }

    # Rolling windows (monthly recompute) for historical signals
    # - Active alerts ALWAYS count (current breach), regardless of when created.
    # - Resolved/acknowledged signals are windowed by domain.
    ALERT_DOMAIN_WINDOWS_DAYS = {
        'investment_ops': 183,  # ~6 months
        'communication': 183,   # ~6 months
        'reviews': 730,         # ~2 years
        'billing': 730,         # ~2 years
        'other': 183            # default to 6-month window
    }
    
    @classmethod
    def calculate_client_status(cls, client_id, as_of=None):
        """
        Calculate comprehensive client status based on SLA violations
        
        Args:
            client_id (int): Client ID to analyze
            
        Returns:
            dict: Status information with details and violations
        """
        try:
            now = as_of or datetime.utcnow()

            # Ensure we're in application context
            if not current_app:
                return {'error': 'Application context not available'}
                
            client = Client.query.get(client_id)
            if not client:
                return {'error': 'Client not found'}

            alerts = Alert.query.filter_by(client_id=client_id).all()
            return cls._build_status_payload(client, alerts, as_of=now)

        except Exception as e:
            logger.error(f"Error calculating client status for {client_id}: {str(e)}")
            return {'error': f'Failed to calculate status: {str(e)}'}

    @classmethod
    def _build_status_payload(cls, client, alerts, as_of=None):
        """Build status dict from a client row and pre-loaded alerts (no extra queries)."""
        now = as_of or datetime.utcnow()
        active_alerts = [a for a in alerts if a.status == 'active']
        resolved_alerts = [a for a in alerts if a.status in ['resolved', 'acknowledged']]

        metrics = cls._calculate_metrics(active_alerts, resolved_alerts, now=now)
        status = cls._determine_status(metrics, active_alerts)
        violations = cls._get_violation_details(active_alerts, now=now)
        # Prefer nightly observation→LLM interpretation pack when present (fact bus).
        # Fall back to live alert ranking + optional interactive phrasing.
        copy_source = "alerts"
        recommendations = None
        try:
            from services.client_health_observations import get_client_interpretation

            nightly = get_client_interpretation(client.id)
            if nightly and (nightly.get("violations") or nightly.get("recommendations")):
                violations = nightly.get("violations") or []
                recommendations = nightly.get("recommendations") or []
                copy_source = f"nightly:{nightly.get('copy_source') or 'pack'}"
        except Exception as exc:
            logger.warning("Nightly client health pack read failed: %s", exc)

        if recommendations is None:
            try:
                from services.client_health_phrase_service import phrase_health_copy

                phrased = phrase_health_copy(violations)
                violations = phrased.get("violations") or violations
                recommendations = phrased.get("recommendations") or []
                copy_source = phrased.get("copy_source") or "deterministic"
            except Exception as exc:
                logger.warning("Client health focus phrasing failed: %s", exc)
                recommendations = cls._generate_recommendations(metrics, violations)
                copy_source = "legacy"
        score = cls._calculate_score(metrics, active_alerts, resolved_alerts, now=now)

        return {
            'client_id': client.id,
            'client_name': client.name,
            'status': status,
            'score': score,
            'details': metrics,
            'violations': violations,
            'recommendations': recommendations,
            'copy_source': copy_source,
            'last_updated': now.isoformat() + 'Z',
        }
    
    @classmethod
    def _calculate_metrics(cls, active_alerts, resolved_alerts, now=None):
        """Calculate key metrics from alerts"""
        now = now or datetime.utcnow()
        
        # Count alerts by severity
        critical_active = len([a for a in active_alerts if a.severity == 'critical'])
        warning_active = len([a for a in active_alerts if a.severity == 'warning'])
        info_active = len([a for a in active_alerts if a.severity == 'info'])

        # Rolling-window resolution rate (per-domain windows)
        domain_starts = {
            domain: now - timedelta(days=days)
            for domain, days in cls.ALERT_DOMAIN_WINDOWS_DAYS.items()
        }

        created_by_domain = {k: 0 for k in domain_starts.keys()}
        resolved_by_domain = {k: 0 for k in domain_starts.keys()}

        all_alerts = list(active_alerts) + list(resolved_alerts)
        for alert in all_alerts:
            domain = cls._get_alert_domain(alert)
            start = domain_starts.get(domain, domain_starts['other'])

            if getattr(alert, 'created_at', None) and alert.created_at >= start:
                created_by_domain[domain] = created_by_domain.get(domain, 0) + 1

            if alert.status in ['resolved', 'acknowledged']:
                resolved_ts = cls._get_alert_resolution_timestamp(alert)
                if resolved_ts and resolved_ts >= start:
                    resolved_by_domain[domain] = resolved_by_domain.get(domain, 0) + 1

        total_created = sum(created_by_domain.values())
        if total_created > 0:
            combined_resolution_rate = 0.0
            for domain, created_count in created_by_domain.items():
                if created_count <= 0:
                    continue
                domain_resolved = resolved_by_domain.get(domain, 0)
                domain_rate = domain_resolved / created_count if created_count > 0 else 1.0
                combined_resolution_rate += domain_rate * created_count
            combined_resolution_rate = combined_resolution_rate / total_created
        else:
            combined_resolution_rate = 1.0

        # Calculate average age of active alerts (cap by domain window to prevent runaway penalties)
        capped_age_hours_list = []
        raw_age_hours_list = []
        for alert in active_alerts:
            if not getattr(alert, 'created_at', None):
                continue
            age_hours = (now - alert.created_at).total_seconds() / 3600
            raw_age_hours_list.append(age_hours)
            domain = cls._get_alert_domain(alert)
            cap_hours = float(cls.ALERT_DOMAIN_WINDOWS_DAYS.get(domain, cls.ALERT_DOMAIN_WINDOWS_DAYS['other'])) * 24.0
            capped_age_hours_list.append(min(age_hours, cap_hours))

        avg_age_hours = (sum(capped_age_hours_list) / len(capped_age_hours_list)) if capped_age_hours_list else 0.0
        oldest_capped = max(capped_age_hours_list, default=0.0)
        oldest_raw = max(raw_age_hours_list, default=0.0)
        
        return {
            'critical_alerts': critical_active,
            'warning_alerts': warning_active,
            'info_alerts': info_active,
            'total_active_alerts': len(active_alerts),
            'total_resolved_alerts': len(resolved_alerts),
            'resolution_rate': round(combined_resolution_rate, 2),
            'avg_alert_age_hours': round(avg_age_hours, 1),
            'oldest_alert_hours': round(oldest_capped, 1),
            'oldest_alert_hours_raw': round(oldest_raw, 1),
            'resolution_rate_by_domain': {
                domain: round((resolved_by_domain.get(domain, 0) / created_by_domain.get(domain, 1)) if created_by_domain.get(domain, 0) > 0 else 1.0, 2)
                for domain in created_by_domain.keys()
            },
            'domain_windows_days': dict(cls.ALERT_DOMAIN_WINDOWS_DAYS)
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
            # For now, we'll consider clients with no alerts as 'excellent'
            return 'excellent'
        
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
    def _get_violation_details(cls, active_alerts, now=None):
        """Get detailed information about violations"""
        violations = []
        now = now or datetime.utcnow()
        
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
                'domain': cls._get_alert_domain(alert),
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
    def _calculate_score(cls, metrics, active_alerts, resolved_alerts, now=None):
        """Calculate numerical score (0-100, higher is better)"""
        now = now or datetime.utcnow()
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

        # Penalize slow/late resolutions (last N days), weighted by severity and SLA breach hours
        resolution_breach_penalty = 0.0
        try:
            # Fallback SLA hours when alert.sla_timeline is missing
            def _effective_sla_hours(alert):
                if getattr(alert, 'sla_timeline', None):
                    return float(alert.sla_timeline)
                # Mirror Alert.is_overdue thresholds as default expectations
                if alert.severity == 'critical':
                    return 4.0
                if alert.severity == 'warning':
                    return 24.0
                return 72.0

            penalty_per_hour = float(cls.SCORING_WEIGHTS.get('resolved_breach_penalty_per_hour', 0.05))
            cap_per_alert = float(cls.SCORING_WEIGHTS.get('resolved_breach_penalty_cap_per_alert', 15.0))

            for alert in resolved_alerts:
                # Window this resolved/acknowledged signal by its domain window
                resolved_ts = cls._get_alert_resolution_timestamp(alert)
                created_ts = getattr(alert, 'created_at', None)
                if not resolved_ts or not created_ts:
                    continue

                domain = cls._get_alert_domain(alert)
                window_days = cls.ALERT_DOMAIN_WINDOWS_DAYS.get(domain, cls.ALERT_DOMAIN_WINDOWS_DAYS['other'])
                window_start = now - timedelta(days=window_days)
                if resolved_ts < window_start:
                    continue

                resolution_hours = (resolved_ts - created_ts).total_seconds() / 3600
                breach_hours = max(0.0, resolution_hours - _effective_sla_hours(alert))

                severity_weight = {
                    'critical': cls.SCORING_WEIGHTS['critical_alert'],
                    'warning': cls.SCORING_WEIGHTS['warning_alert'],
                    'info': cls.SCORING_WEIGHTS['info_alert']
                }.get(alert.severity, 1.0)

                per_alert_penalty = min(cap_per_alert, breach_hours * severity_weight * penalty_per_hour)
                resolution_breach_penalty += per_alert_penalty
        except Exception:
            # Don't let scoring fail due to any unexpected alert data edge case
            resolution_breach_penalty = 0.0
        
        final_score = max(0, min(100, base_score - score_deduction - resolution_breach_penalty + resolution_bonus))
        return round(final_score, 1)

    @classmethod
    def _get_alert_domain(cls, alert):
        """
        Map an Alert to a scoring domain for rolling-window logic.
        Defaults to 'other' (6-month window).
        """
        alert_type = (getattr(alert, 'alert_type', None) or '').strip()
        alert_subtype = (getattr(alert, 'alert_subtype', None) or '').strip()

        if alert_type in ('workflow_sla', 'recommendation'):
            return 'investment_ops'

        if alert_type == 'communication':
            return 'communication'

        if alert_type in ('review_overdue', 'review_upcoming', 'review_schedule'):
            return 'reviews'

        if alert_type == 'billing':
            return 'billing'

        # Some legacy/other SLA-driven alerts should still be in the fast window
        if alert_type in ('service_ticket', 'lead_sla', 'lead_followup'):
            return 'communication'

        # Default
        return 'other'

    @classmethod
    def _get_alert_resolution_timestamp(cls, alert):
        """Best-effort timestamp for when an alert was addressed."""
        # Only use resolved_at or acknowledged_at - never use created_at as fallback
        # as it would make resolution time appear as zero
        return getattr(alert, 'resolved_at', None) or getattr(alert, 'acknowledged_at', None)
    
    @classmethod
    def get_clients_status(cls, client_ids, as_of=None):
        """
        Status for a specific set of clients (batched alerts — fast for list pages).

        Uses two queries (clients + alerts) instead of N+1 per client.
        """
        try:
            if not current_app:
                return []
            if not client_ids:
                return []

            now = as_of or datetime.utcnow()
            ids = list({int(i) for i in client_ids if i is not None})
            if not ids:
                return []

            clients = Client.query.filter(Client.id.in_(ids)).all()
            alerts = Alert.query.filter(Alert.client_id.in_(ids)).all()
            alerts_by_client = defaultdict(list)
            for alert in alerts:
                alerts_by_client[alert.client_id].append(alert)

            results = []
            for client in clients:
                payload = cls._build_status_payload(
                    client, alerts_by_client.get(client.id, []), as_of=now
                )
                if 'error' not in payload:
                    results.append(payload)

            status_priority = {'critical': 0, 'warning': 1, 'good': 2, 'excellent': 3}
            results.sort(key=lambda x: (status_priority.get(x['status'], 4), -x['score']))
            return results
        except Exception as e:
            logger.error(f"Error getting clients status: {str(e)}")
            return []

    @classmethod
    def get_all_clients_status(cls, as_of=None):
        """Get status for all clients (prefer get_clients_status(ids) on list pages)."""
        try:
            if not current_app:
                return []
            client_ids = [row[0] for row in db.session.query(Client.id).all()]
            return cls.get_clients_status(client_ids, as_of=as_of)
        except Exception as e:
            logger.error(f"Error getting all clients status: {str(e)}")
            return []
