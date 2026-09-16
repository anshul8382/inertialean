"""
Alert Orchestrator Agent
========================
The most intelligent agent that:
- Reads ALL client context (meetings, synopsis, notes, communications)
- Validates individual agent decisions
- Makes final alert decision with documented logic
- Creates ONE consolidated alert per client if needed

This orchestrator ensures alerts are contextually appropriate and reduces noise.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy import func, and_, or_, desc

from extensions import db
from models import (
    Client, DataIntegrityIssue, AgentRun, Meeting, ReviewWorkflow,
    MonthlyInvestmentSchedule, Workflow, ClientBehaviorPattern, MonthlyInvestment
)
from agents.base import BaseAgent

# Import alert system
try:
    from alert_system_models import Alert
    ALERT_AVAILABLE = True
except ImportError:
    ALERT_AVAILABLE = False
    Alert = None

logger = logging.getLogger(__name__)


class AlertOrchestrator(BaseAgent):
    """
    Alert Orchestrator - The intelligent decision maker.
    
    Responsibilities:
    1. Collect all issues from all agents for a client
    2. Read comprehensive client context
    3. Validate each agent's should_alert() decision
    4. Make final alert decision with documented logic
    5. Create ONE consolidated alert per client
    """
    
    @property
    def agent_name(self) -> str:
        return "alert_orchestrator"
    
    @property
    def agent_version(self) -> str:
        return "1.0.0"
    
    # =========================================================================
    # CONTEXT COLLECTION
    # =========================================================================
    
    def collect_client_context(self, client_id: int) -> Dict[str, Any]:
        """
        Collect ALL client context from multiple sources.
        
        Returns comprehensive context dictionary.
        """
        client = Client.query.get(client_id)
        if not client:
            return {}
        
        context = {
            'client_id': client_id,
            'client_name': client.name,
            'sources': {}
        }
        
        # 1. Planning synopsis
        if client.planning_synopsis:
            context['sources']['planning_synopsis'] = {
                'text': client.planning_synopsis,
                'source': 'Client.planning_synopsis',
                'relevance': 'high'
            }
        
        # 2. Background notes
        if client.background_notes:
            context['sources']['background_notes'] = {
                'text': client.background_notes,
                'source': 'Client.background_notes',
                'relevance': 'medium'
            }
        
        # 3. Meeting notes (recent meetings, last 90 days)
        cutoff_date = datetime.utcnow() - timedelta(days=90)
        meetings = Meeting.query.filter(
            Meeting.client_id == client_id,
            Meeting.meeting_date >= cutoff_date
        ).order_by(desc(Meeting.meeting_date)).limit(10).all()
        
        if meetings:
            meeting_notes = []
            for meeting in meetings:
                if meeting.notes:
                    meeting_notes.append({
                        'date': meeting.meeting_date.isoformat() if meeting.meeting_date else None,
                        'title': meeting.title,
                        'notes': meeting.notes
                    })
            if meeting_notes:
                context['sources']['meeting_notes'] = {
                    'meetings': meeting_notes,
                    'count': len(meeting_notes),
                    'source': 'Meeting.notes',
                    'relevance': 'high'
                }
        
        # 4. Review meeting notes (from review workflows)
        reviews = ReviewWorkflow.query.filter(
            ReviewWorkflow.client_id == client_id,
            ReviewWorkflow.meeting_date >= cutoff_date
        ).order_by(desc(ReviewWorkflow.meeting_date)).limit(10).all()
        
        if reviews:
            review_notes = []
            for review in reviews:
                if review.meeting_notes:
                    review_notes.append({
                        'date': review.meeting_date.isoformat() if review.meeting_date else None,
                        'notes': review.meeting_notes
                    })
            if review_notes:
                context['sources']['review_notes'] = {
                    'reviews': review_notes,
                    'count': len(review_notes),
                    'source': 'ReviewWorkflow.meeting_notes',
                    'relevance': 'high'
                }
        
        # 5. Monthly investment schedule notes
        schedule = MonthlyInvestmentSchedule.query.filter_by(client_id=client_id).first()
        if schedule and schedule.notes:
            context['sources']['monthly_schedule_notes'] = {
                'text': schedule.notes,
                'source': 'MonthlyInvestmentSchedule.notes',
                'relevance': 'high'
            }
        
        # 6. Active workflow notes
        active_workflows = Workflow.query.join(MonthlyInvestment).filter(
            MonthlyInvestment.client_id == client_id,
            Workflow.is_archived == False
        ).all()
        
        workflow_notes = []
        for workflow in active_workflows:
            if workflow.schedule_notes:
                workflow_notes.append({
                    'workflow_id': workflow.id,
                    'schedule_notes': workflow.schedule_notes,
                    'notes': workflow.notes or ''
                })
            elif workflow.notes:
                workflow_notes.append({
                    'workflow_id': workflow.id,
                    'notes': workflow.notes
                })
        
        if workflow_notes:
            context['sources']['workflow_notes'] = {
                'workflows': workflow_notes,
                'count': len(workflow_notes),
                'source': 'Workflow.notes + schedule_notes',
                'relevance': 'high'
            }
        
        # 7. Client behavior patterns (learned)
        patterns = ClientBehaviorPattern.query.filter_by(client_id=client_id).all()
        if patterns:
            context['sources']['learned_patterns'] = {
                'patterns': [
                    {
                        'category': p.check_category,
                        'type': p.pattern_type,
                        'value': p.pattern_value,
                        'confidence': p.confidence,
                        'notes': p.notes
                    } for p in patterns
                ],
                'count': len(patterns),
                'source': 'ClientBehaviorPattern',
                'relevance': 'high'
            }
        
        # 8. Recent alerts (last 30 days) - to avoid duplicate alerts
        if ALERT_AVAILABLE:
            recent_alerts = Alert.query.filter(
                Alert.client_id == client_id,
                Alert.created_at >= datetime.utcnow() - timedelta(days=30)
            ).order_by(desc(Alert.created_at)).limit(5).all()
            
            if recent_alerts:
                context['sources']['recent_alerts'] = {
                    'alerts': [
                        {
                            'id': a.id,
                            'type': a.alert_type,
                            'subtype': a.alert_subtype,
                            'status': a.status,
                            'created_at': a.created_at.isoformat() if a.created_at else None
                        } for a in recent_alerts
                    ],
                    'count': len(recent_alerts),
                    'source': 'Alert',
                    'relevance': 'medium'
                }
        
        return context
    
    def analyze_context(self, context: Dict[str, Any], agent_issues: List[DataIntegrityIssue]) -> Dict[str, Any]:
        """
        Analyze collected context to make alert decision.
        
        Returns decision with documented logic.
        """
        decision = {
            'should_alert': False,
            'reasoning': [],
            'confidence': 0.0,
            'context_summary': {}
        }
        
        # Combine all text sources
        all_text = ""
        for source_name, source_data in context.get('sources', {}).items():
            if 'text' in source_data:
                all_text += source_data['text'].lower() + " "
            elif 'meetings' in source_data:
                for meeting in source_data['meetings']:
                    if 'notes' in meeting:
                        all_text += meeting['notes'].lower() + " "
            elif 'reviews' in source_data:
                for review in source_data['reviews']:
                    if 'notes' in review:
                        all_text += review['notes'].lower() + " "
            elif 'workflows' in source_data:
                for workflow in source_data['workflows']:
                    if 'schedule_notes' in workflow:
                        all_text += workflow['schedule_notes'].lower() + " "
                    if 'notes' in workflow:
                        all_text += workflow['notes'].lower() + " "
        
        all_text = all_text.strip()
        
        # Count issues by severity
        critical_issues = [i for i in agent_issues if i.severity == 'critical']
        warning_issues = [i for i in agent_issues if i.severity == 'warning']
        info_issues = [i for i in agent_issues if i.severity == 'info']
        
        # Decision logic
        decision['context_summary'] = {
            'total_issues': len(agent_issues),
            'critical': len(critical_issues),
            'warning': len(warning_issues),
            'info': len(info_issues),
            'context_sources_count': len(context.get('sources', {})),
            'has_context': len(all_text) > 0
        }
        
        # Rule 1: Critical issues always alert (unless strong context says otherwise)
        if critical_issues:
            if all_text:
                # Check for strong delay/skip context
                delay_keywords = ['delay', 'wait', 'hold', 'skip', 'postpone', 'later', 'next month']
                has_delay_context = any(keyword in all_text for keyword in delay_keywords)
                
                if has_delay_context:
                    decision['reasoning'].append(
                        f"Found {len(critical_issues)} critical issues, but client context indicates delay/skip. "
                        f"Context: Found delay keywords in notes."
                    )
                    decision['should_alert'] = False
                    decision['confidence'] = 0.7
                else:
                    decision['reasoning'].append(
                        f"Found {len(critical_issues)} critical issues with no delay context. Alert needed."
                    )
                    decision['should_alert'] = True
                    decision['confidence'] = 0.9
            else:
                decision['reasoning'].append(
                    f"Found {len(critical_issues)} critical issues with no client context. Alert needed."
                )
                decision['should_alert'] = True
                decision['confidence'] = 0.95
        
        # Rule 2: Multiple warning issues
        elif len(warning_issues) >= 3:
            if all_text:
                delay_keywords = ['delay', 'wait', 'hold', 'skip', 'postpone']
                has_delay_context = any(keyword in all_text for keyword in delay_keywords)
                
                if has_delay_context:
                    decision['reasoning'].append(
                        f"Found {len(warning_issues)} warning issues, but context explains delay. No alert."
                    )
                    decision['should_alert'] = False
                    decision['confidence'] = 0.6
                else:
                    decision['reasoning'].append(
                        f"Found {len(warning_issues)} warning issues with no delay context. Alert needed."
                    )
                    decision['should_alert'] = True
                    decision['confidence'] = 0.7
            else:
                decision['reasoning'].append(
                    f"Found {len(warning_issues)} warning issues with no context. Alert needed."
                )
                decision['should_alert'] = True
                decision['confidence'] = 0.8
        
        # Rule 3: Learned patterns indicate expected behavior
        if context.get('sources', {}).get('learned_patterns'):
            patterns = context['sources']['learned_patterns']['patterns']
            high_confidence_patterns = [p for p in patterns if p.get('confidence', 0) > 0.7]
            
            if high_confidence_patterns:
                decision['reasoning'].append(
                    f"Found {len(high_confidence_patterns)} high-confidence learned patterns. "
                    f"Client behavior is known. Reducing alert priority."
                )
                if decision['should_alert']:
                    decision['confidence'] = max(0.3, decision['confidence'] - 0.2)
        
        # Rule 4: Recent alerts for same client (avoid duplicates)
        if context.get('sources', {}).get('recent_alerts'):
            recent_count = context['sources']['recent_alerts']['count']
            if recent_count >= 3:
                decision['reasoning'].append(
                    f"Client has {recent_count} recent alerts. Consolidating into one alert."
                )
                decision['should_alert'] = True
                decision['confidence'] = min(0.9, decision['confidence'] + 0.1)
        
        # Rule 5: No context and issues = alert
        if not all_text and agent_issues:
            if not decision['reasoning']:
                decision['reasoning'].append(
                    f"Found {len(agent_issues)} issues with no client context. Alert needed."
                )
                decision['should_alert'] = True
                decision['confidence'] = 0.85
        
        # Rule 6: Strong delay context = no alert
        if all_text and not decision['should_alert']:
            strong_delay_phrases = [
                'client said', 'client mentioned', 'client requested',
                'will do later', 'next month', 'skip this',
                'not urgent', 'no rush', 'can wait'
            ]
            has_strong_delay = any(phrase in all_text for phrase in strong_delay_phrases)
            
            if has_strong_delay and agent_issues:
                decision['reasoning'].append(
                    "Strong delay context found in client notes. No alert needed."
                )
                decision['should_alert'] = False
                decision['confidence'] = 0.8
        
        return decision
    
    # =========================================================================
    # MAIN RUN METHOD
    # =========================================================================
    
    def run_checks(self, client_id: int, since: datetime = None, **kwargs) -> List[DataIntegrityIssue]:
        """
        Orchestrate alert creation for a client.
        
        This doesn't create issues, but creates alerts based on agent issues.
        """
        # This agent doesn't create issues, it creates alerts
        # So we return empty list but create alerts
        return []
    
    def orchestrate_alerts(self, client_ids: List[int] = None, user_id: int = None):
        """
        Main orchestration method - runs after all agents.
        
        For each client:
        1. Collect all open issues from all agents
        2. Collect client context
        3. Analyze and decide
        4. Create ONE alert if needed
        """
        if not ALERT_AVAILABLE:
            logger.warning("Alert system not available - skipping orchestration")
            return
        
        run_id = self.start_run(
            mode='orchestration',
            scope={'client_ids': client_ids},
            triggered_by='scheduler',
            user_id=user_id
        )
        
        try:
            if client_ids is None:
                # Get all clients with open issues
                client_ids = db.session.query(
                    DataIntegrityIssue.client_id
                ).filter(
                    DataIntegrityIssue.status.in_(['open', 'baseline'])
                ).distinct().all()
                client_ids = [c[0] for c in client_ids]
            
            alerts_created = 0
            alerts_skipped = 0
            
            for client_id in client_ids:
                try:
                    # Get all open issues for this client
                    issues = DataIntegrityIssue.query.filter(
                        DataIntegrityIssue.client_id == client_id,
                        DataIntegrityIssue.status.in_(['open', 'baseline'])
                    ).all()
                    
                    if not issues:
                        continue
                    
                    # Collect context
                    context = self.collect_client_context(client_id)
                    
                    # Analyze and decide
                    decision = self.analyze_context(context, issues)
                    
                    # Check if alert already exists for this client (recent)
                    existing_alert = None
                    if ALERT_AVAILABLE:
                        existing_alert = Alert.query.filter(
                            Alert.client_id == client_id,
                            Alert.status == 'active',
                            Alert.created_at >= datetime.utcnow() - timedelta(days=7)
                        ).first()
                    
                    if decision['should_alert'] and not existing_alert:
                        # Create consolidated alert
                        alert = self._create_consolidated_alert(
                            client_id, issues, decision, context
                        )
                        if alert:
                            alerts_created += 1
                            # Link issues to alert
                            for issue in issues:
                                issue.alert_id = alert.id
                            db.session.commit()
                    else:
                        if existing_alert:
                            logger.info(f"Alert already exists for client {client_id}, skipping")
                        alerts_skipped += 1
                    
                    self.current_run.clients_processed += 1
                    
                except Exception as e:
                    logger.error(f"Error orchestrating alerts for client {client_id}: {e}", exc_info=True)
            
            self.complete_run()
            logger.info(f"Orchestration completed: {alerts_created} alerts created, {alerts_skipped} skipped")
            
        except Exception as e:
            self.complete_run(error=e)
            raise
        
        return run_id
    
    def _create_consolidated_alert(
        self, 
        client_id: int, 
        issues: List[DataIntegrityIssue],
        decision: Dict[str, Any],
        context: Dict[str, Any]
    ) -> Optional[Alert]:
        """Create ONE consolidated alert for a client with all issues."""
        
        client = Client.query.get(client_id)
        if not client:
            return None
        
        # Group issues by agent/category
        issues_by_agent = {}
        for issue in issues:
            # Extract agent name from check_category
            # Format: AGENT_NAME_CHECK_NAME or just CATEGORY
            agent_name = issue.check_category.split('_')[0].lower() if '_' in issue.check_category else issue.check_category.lower()
            
            if agent_name not in issues_by_agent:
                issues_by_agent[agent_name] = {
                    'issues': [],
                    'critical': 0,
                    'warning': 0,
                    'info': 0
                }
            
            issues_by_agent[agent_name]['issues'].append(issue)
            if issue.severity == 'critical':
                issues_by_agent[agent_name]['critical'] += 1
            elif issue.severity == 'warning':
                issues_by_agent[agent_name]['warning'] += 1
            else:
                issues_by_agent[agent_name]['info'] += 1
        
        # Build alert title
        total_critical = sum(a['critical'] for a in issues_by_agent.values())
        total_warning = sum(a['warning'] for a in issues_by_agent.values())
        
        if total_critical > 0:
            severity = 'critical'
            title = f"Critical Issues Detected for {client.name}"
        elif total_warning >= 3:
            severity = 'warning'
            title = f"Multiple Issues Detected for {client.name}"
        else:
            severity = 'info'
            title = f"Issues Detected for {client.name}"
        
        # Build alert description
        description_parts = [
            f"Orchestrator detected {len(issues)} issues across {len(issues_by_agent)} agent(s).\n",
            f"Severity: {total_critical} critical, {total_warning} warning, {len(issues) - total_critical - total_warning} info\n",
            "\nIssues by Agent:"
        ]
        
        for agent_name, agent_data in issues_by_agent.items():
            description_parts.append(f"\n{agent_name.upper()}:")
            description_parts.append(f"  • {agent_data['critical']} critical, {agent_data['warning']} warning, {agent_data['info']} info")
            
            # Add sample issues
            for issue in agent_data['issues'][:3]:
                description_parts.append(f"    - {issue.message}")
            if len(agent_data['issues']) > 3:
                description_parts.append(f"    ... and {len(agent_data['issues']) - 3} more")
        
        # Add orchestrator logic documentation
        description_parts.append("\n\nOrchestrator Logic:")
        description_parts.append(f"Decision: {'ALERT' if decision['should_alert'] else 'NO ALERT'}")
        description_parts.append(f"Confidence: {decision['confidence']:.1%}")
        description_parts.append("\nReasoning:")
        for reason in decision['reasoning']:
            description_parts.append(f"  • {reason}")
        
        description_parts.append(f"\nContext Sources: {decision['context_summary'].get('context_sources_count', 0)}")
        if context.get('sources'):
            description_parts.append("Sources used:")
            for source_name in context['sources'].keys():
                description_parts.append(f"  • {source_name}")
        
        description = "\n".join(description_parts)

        assignee_user_id = client.advisor_id
        try:
            from services.task_assignment_service import get_assignee_for_alert
            resolved = get_assignee_for_alert(
                "agent_orchestrated",
                "consolidated",
                workflow=None,
                issues=issues,
                client_id=client_id,
            )
            if resolved:
                assignee_user_id = resolved
        except Exception as e:
            logger.warning(
                "Orchestrator alert assignee resolution failed, using client.advisor_id: %s", e
            )

        # Create alert
        try:
            alert = Alert(
                title=title,
                description=description,
                alert_type='agent_orchestrated',
                alert_subtype='consolidated',
                severity=severity,
                status='active',
                client_id=client_id,
                user_id=assignee_user_id,
                created_at=datetime.utcnow()
            )
            db.session.add(alert)
            db.session.commit()
            
            logger.info(f"Created consolidated alert {alert.id} for client {client_id}")
            return alert
            
        except Exception as e:
            logger.error(f"Error creating alert: {e}", exc_info=True)
            db.session.rollback()
            return None
