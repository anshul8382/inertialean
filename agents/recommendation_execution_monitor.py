"""
Recommendation Execution Monitor Agent
======================================
Monitors monthly investment workflows and recommendation execution:
- Workflow stage progression and SLA compliance
- Funds received tracking
- Recommendation sending and execution
- Amount mismatches
- Context-aware alerting based on client notes

This agent uses client context (monthly notes) to intelligently decide
when to create alerts vs. when to skip (e.g., client mentioned delay).
"""

import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional
from sqlalchemy import func, and_, or_

from extensions import db
from models import (
    Client, Workflow, MonthlyInvestment, MonthlyInvestmentSchedule,
    Recommendation, RecommendationSession, Transaction,
    DataIntegrityIssue, ClientBehaviorPattern
)
from agents.base import BaseAgent

logger = logging.getLogger(__name__)


class RecommendationExecutionMonitor(BaseAgent):
    """
    Recommendation Execution Monitor Agent
    
    Monitors:
    - Workflow stage progression (FUNDS → RECOS → NOTIFY → EXEC → UPDATE → COMPLETED)
    - Recommendation sending and execution
    - Amount mismatches
    - Uses client notes for context-aware alerting
    """
    
    @property
    def agent_name(self) -> str:
        return "rec_exec_monitor"
    
    @property
    def agent_version(self) -> str:
        return "1.0.0"
    
    # =========================================================================
    # CONFIGURATION
    # =========================================================================
    
    # SLA thresholds (in days) - defaults.
    # Can be overridden per-client via:
    # ClientBehaviorPattern(check_category='RECOMMENDATION_EXECUTION', pattern_type='stage_sla_days')
    # pattern_value example: {"FUNDS": 3, "RECOS": 2, "EXEC": 5, "STALL_ANY": 7}
    DEFAULT_STAGE_SLA_DAYS = {
        'FUNDS': 3,      # Funds should be received within 3 days of investment_date
        'RECOS': 2,      # Recommendations should be sent within 2 days of funds
        'EXEC': 5,       # Recommendations should be executed within 5 days of sending
        'STALL_ANY': 7,  # Alert if stuck in any stage for 7+ days (fallback)
    }
    
    # Amount tolerance
    AMOUNT_TOLERANCE = 0.10  # ±10% difference is acceptable
    
    # Context keywords that indicate expected delays (case-insensitive)
    # Enhanced with variations and natural language patterns
    DELAY_KEYWORDS = [
        # Direct delay terms
        'delay', 'delayed', 'delays', 'delaying',
        'wait', 'waiting', 'waited',
        'hold', 'hold off', 'holding', 'on hold',
        'postpone', 'postponed', 'postponing',
        'defer', 'deferred', 'deferring',
        'reschedule', 'rescheduled', 'rescheduling',
        # Skip terms
        'skip', 'skipping', 'skipped', 'skip this',
        'not this month', 'not this cycle',
        'next month', 'next cycle', 'next time',
        # Time-based
        'later', 'later this month', 'later in month',
        'end of month', 'eom',
        'will do later', 'do later',
        # Client communication
        'client said', 'client mentioned', 'client requested',
        'client asked', 'client wants',
        'waiting for client', 'awaiting client',
        # Intent patterns
        'no rush', 'not urgent', 'not a priority',
        'can wait', 'will wait',
        'take time', 'taking time',
        # Natural language variations
        'will send', 'will do', 'will process',
        'planning to', 'intend to',
        'might be', 'may be delayed',
        'could be', 'possibly delayed'
    ]
    
    # Regex patterns for better natural language understanding
    DELAY_PATTERNS = [
        r'\b(not|won\'t|will not)\s+(do|send|process|execute|complete)',
        r'\b(wait|hold|delay|skip|postpone)\s+(for|until|till)',
        r'\b(client|they|he|she)\s+(said|mentioned|asked|requested|wants)',
        r'\b(no|not)\s+(rush|urgent|priority|hurry)',
        r'\b(later|next)\s+(month|cycle|time|week)',
        r'\b(will|would|can|may)\s+(do|send|process)\s+(later|next)',
    ]
    
    # =========================================================================
    # MAIN RUN METHOD
    # =========================================================================
    
    def run_checks(self, client_id: int, since: datetime = None, **kwargs) -> List[DataIntegrityIssue]:
        """
        Run all monitoring checks for a client.
        
        Args:
            client_id: Client to check
            since: Only check workflows created after this date
        """
        issues = []
        
        # Resolve any open RECOMMENDATION_EXECUTION issues for workflows that are now completed or archived
        completed_or_archived_ids = [
            w.id for w in Workflow.query.join(MonthlyInvestment).filter(
                MonthlyInvestment.client_id == client_id
            ).filter(
                or_(Workflow.current_stage == 'COMPLETED', Workflow.is_archived == True)
            ).all()
        ]
        if completed_or_archived_ids:
            open_issues = DataIntegrityIssue.query.filter(
                DataIntegrityIssue.client_id == client_id,
                DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
                DataIntegrityIssue.status == 'open'
            ).all()
            for issue in open_issues:
                wid = (issue.details or {}).get('workflow_id')
                if wid is not None and int(wid) in completed_or_archived_ids:
                    issue.status = 'resolved'
                    issue.resolution_type = 'workflow_completed'
                    issue.resolved_at = datetime.utcnow()
                    issue.resolution_notes = 'Workflow completed or archived; auto-resolved by agent.'
                    logger.info(f"Auto-resolved RECOMMENDATION_EXECUTION issue {issue.id} (workflow {wid} completed/archived)")
        
        # Get active workflows for this client (exclude closed/completed)
        query = Workflow.query.join(MonthlyInvestment).filter(
            MonthlyInvestment.client_id == client_id,
            Workflow.is_archived == False,
            Workflow.current_stage != 'COMPLETED'
        )
        
        if since:
            query = query.filter(Workflow.created_at >= since)
        
        workflows = query.all()
        
        for workflow in workflows:
            # Check workflow progression
            issues.extend(self.check_workflow_stalled(workflow))
            issues.extend(self.check_funds_received(workflow))
            issues.extend(self.check_recommendations_sent(workflow))
            issues.extend(self.check_recommendations_executed(workflow))
            issues.extend(self.check_amount_mismatch(workflow))
        
        db.session.commit()
        return issues
    
    # =========================================================================
    # CONTEXT-AWARE DECISION LOGIC
    # =========================================================================

    def get_stage_sla_days(self, client_id: int, stage: str) -> int:
        """
        Resolve SLA days for a client+stage using:
        defaults -> ClientBehaviorPattern(stage_sla_days).
        """
        stage = (stage or '').upper()
        defaults = self.DEFAULT_STAGE_SLA_DAYS
        days = defaults.get(stage) or defaults.get('STALL_ANY', 7)

        pattern = self.get_client_pattern(
            client_id,
            'RECOMMENDATION_EXECUTION',
            'stage_sla_days'
        )
        if pattern and isinstance(pattern.pattern_value, dict):
            # Accept either "STALL_ANY" or "STAGE_STALL_DAYS"
            pv = {str(k).upper(): v for k, v in pattern.pattern_value.items()}
            if stage in pv and isinstance(pv[stage], (int, float)):
                return int(pv[stage])
            if stage not in defaults and 'STALL_ANY' in pv and isinstance(pv['STALL_ANY'], (int, float)):
                return int(pv['STALL_ANY'])
            if stage not in defaults and 'STAGE_STALL_DAYS' in pv and isinstance(pv['STAGE_STALL_DAYS'], (int, float)):
                return int(pv['STAGE_STALL_DAYS'])

        return int(days)
    
    def should_alert(self, workflow: Workflow, check_type: str, message: str) -> bool:
        """
        Decide whether to create an alert based on client context.
        
        Uses natural language understanding to detect:
        - Delay intentions (wait, hold, postpone)
        - Skip intentions (skip this month, not this cycle)
        - Client communication (client said, client requested)
        - Time-based patterns (later, next month)
        
        Returns True if alert should be created, False if context explains the issue.
        """
        # Get all notes (workflow notes; schedule snapshot stored in workflow.notes at creation)
        notes_text = ""
        if workflow.notes:
            notes_text += workflow.notes.lower() + " "
        
        # Check schedule notes
        schedule = MonthlyInvestmentSchedule.query.filter_by(
            client_id=workflow.monthly_investment.client_id
        ).first()
        if schedule and schedule.notes:
            notes_text += schedule.notes.lower() + " "
        
        if not notes_text.strip():
            # No notes - should alert
            return True
        
        # Method 1: Keyword matching (exact and fuzzy)
        for keyword in self.DELAY_KEYWORDS:
            # Check for word boundaries to avoid false positives
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, notes_text, re.IGNORECASE):
                logger.info(f"Context explains delay for workflow {workflow.id}: found keyword '{keyword}' in notes")
                return False
        
        # Method 2: Regex pattern matching for natural language
        for pattern in self.DELAY_PATTERNS:
            if re.search(pattern, notes_text, re.IGNORECASE):
                logger.info(f"Context explains delay for workflow {workflow.id}: matched pattern '{pattern}' in notes")
                return False
        
        # Method 3: Intent detection (simple heuristics)
        # Check for negative intent phrases
        negative_intents = [
            r'\b(not|no|won\'t|will not)\s+(doing|sending|processing|executing)',
            r'\b(not|no)\s+(this|current)\s+(month|cycle)',
            r'\b(skip|hold|delay|wait)\s+(this|current|now)',
        ]
        for intent_pattern in negative_intents:
            if re.search(intent_pattern, notes_text, re.IGNORECASE):
                logger.info(f"Context explains delay for workflow {workflow.id}: detected negative intent")
                return False
        
        # Method 4: Check client behavior patterns (learned from feedback)
        pattern = self.get_client_pattern(
            workflow.monthly_investment.client_id,
            'RECOMMENDATION_EXECUTION',
            f'{check_type}_expected_delay'
        )
        if pattern:
            logger.info(f"Client pattern indicates expected delay for {check_type}")
            return False
        
        # No context found - should alert
        return True
    
    # =========================================================================
    # CHECK 1: WORKFLOW STALLED
    # =========================================================================
    
    def check_workflow_stalled(self, workflow: Workflow) -> List[DataIntegrityIssue]:
        """Check if workflow is stuck in a stage beyond SLA."""
        issues = []
        
        stages = ['FUNDS', 'RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'COMPLETED']
        current_stage = workflow.current_stage
        
        if current_stage == 'COMPLETED':
            return issues

        # Do not treat future workflows as "stalled".
        # Workflows are often created for the next month; a stage is only "due"
        # on/after the investment_date.
        if workflow.investment_date and datetime.utcnow().date() < workflow.investment_date:
            return issues
        
        # Calculate days since last update
        last_update = workflow.updated_at or workflow.created_at
        days_stalled = (datetime.utcnow() - last_update).days

        client_id = workflow.monthly_investment.client_id
        stall_days = self.get_stage_sla_days(client_id, 'STALL_ANY')

        if days_stalled >= stall_days:
            # Check context before alerting
            if self.should_alert(workflow, 'workflow_stalled', 
                                f"Workflow stuck in {current_stage} for {days_stalled} days"):
                issue = self.create_issue(
                    client_id=client_id,
                    check_category='RECOMMENDATION_EXECUTION',
                    check_name='workflow_stalled',
                    severity='warning' if days_stalled < max(stall_days * 2, 14) else 'critical',
                    message=(
                        f"Workflow stuck in {current_stage} stage for {days_stalled} days. "
                        "Related SLA alerts: workflow_stalled."
                    ),
                    details={
                        'workflow_id': workflow.id,
                        'monthly_investment_id': workflow.monthly_investment_id,
                        'current_stage': current_stage,
                        'sla_breach_type': 'workflow_stalled',
                        'related_alerts': ['workflow_stalled'],
                        'days_stalled': days_stalled,
                        'investment_date': workflow.investment_date.isoformat() if workflow.investment_date else None,
                        'target_completion_date': workflow.target_completion_date.isoformat() if workflow.target_completion_date else None
                    },
                    suggested_action=f"Review workflow {workflow.id} and move to next stage",
                    reference_date=workflow.investment_date,
                    workflow_id=workflow.id
                )
                if issue:
                    issues.append(issue)
        
        return issues
    
    # =========================================================================
    # CHECK 2: FUNDS NOT RECEIVED
    # =========================================================================
    
    def check_funds_received(self, workflow: Workflow) -> List[DataIntegrityIssue]:
        """Check if funds were received within SLA."""
        issues = []
        
        if workflow.current_stage != 'FUNDS':
            return issues  # Already past funds stage
        
        if not workflow.investment_date:
            return issues
        
        days_since_investment = (datetime.utcnow().date() - workflow.investment_date).days

        client_id = workflow.monthly_investment.client_id
        funds_sla = self.get_stage_sla_days(client_id, 'FUNDS')

        if days_since_investment >= funds_sla:
            # Check if funds action exists
            from models import WorkflowAction
            funds_action = WorkflowAction.query.filter_by(
                workflow_id=workflow.id,
                action_type='FUNDS_RECEIVED'
            ).first()
            
            if not funds_action:
                # Funds not received - check context
                if self.should_alert(workflow, 'funds_not_received',
                                    f"Funds not received {days_since_investment} days after investment date"):
                    issue = self.create_issue(
                        client_id=client_id,
                        check_category='RECOMMENDATION_EXECUTION',
                        check_name='workflow_stalled',
                        severity='warning' if days_since_investment < 7 else 'critical',
                        message=(
                            f"Workflow SLA breach (FUNDS): funds not received {days_since_investment} days after "
                            f"investment date {workflow.investment_date}. Related SLA alerts: workflow_stalled, "
                            "funds_not_received."
                        ),
                        details={
                            'workflow_id': workflow.id,
                            'monthly_investment_id': workflow.monthly_investment_id,
                            'current_stage': workflow.current_stage,
                            'sla_breach_type': 'funds_not_received',
                            'related_alerts': ['workflow_stalled', 'funds_not_received'],
                            'investment_date': workflow.investment_date.isoformat(),
                            'days_overdue': days_since_investment,
                            'planned_amount': float(workflow.planned_amount) if workflow.planned_amount else None
                        },
                        suggested_action="Confirm funds receipt or update workflow notes if delayed",
                        reference_date=workflow.investment_date,
                        workflow_id=workflow.id
                    )
                    if issue:
                        issues.append(issue)
        
        return issues
    
    # =========================================================================
    # CHECK 3: RECOMMENDATIONS NOT SENT
    # =========================================================================
    
    def check_recommendations_sent(self, workflow: Workflow) -> List[DataIntegrityIssue]:
        """Check if recommendations were sent after funds received."""
        issues = []
        
        if workflow.current_stage not in ['RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'COMPLETED']:
            return issues  # Not yet at RECOS stage

        # If the workflow has already progressed beyond NOTIFY, recommendations were
        # generated/handled as part of stage progression (even if Recommendation
        # rows are not stored). Avoid false positives for completed workflows.
        if workflow.current_stage in ['EXEC', 'UPDATE', 'COMPLETED']:
            return issues
        
        # Check if funds were received
        from models import WorkflowAction
        funds_action = WorkflowAction.query.filter_by(
            workflow_id=workflow.id,
            action_type='FUNDS_RECEIVED'
        ).order_by(WorkflowAction.action_date.desc()).first()
        
        if not funds_action:
            return issues  # Can't check if funds not received yet

        # If we have an explicit workflow action indicating recommendations were generated
        # (or the client was notified), do not flag "recos not sent".
        recos_generated = WorkflowAction.query.filter(
            WorkflowAction.workflow_id == workflow.id,
            WorkflowAction.action_type.in_(['RECOS_GENERATED', 'CLIENT_NOTIFIED'])
        ).first()
        if recos_generated:
            return issues
        
        funds_date = funds_action.action_date.date()
        days_since_funds = (datetime.utcnow().date() - funds_date).days

        client_id = workflow.monthly_investment.client_id
        recos_sla = self.get_stage_sla_days(client_id, 'RECOS')

        if days_since_funds >= recos_sla:
            # No RECOS_GENERATED / CLIENT_NOTIFIED action found within SLA window.
            # This indicates recommendations weren't generated (or stage wasn't advanced).
            if self.should_alert(workflow, 'recos_not_sent',
                                f"No recommendations generated {days_since_funds} days after funds received"):
                issue = self.create_issue(
                    client_id=client_id,
                    check_category='RECOMMENDATION_EXECUTION',
                    check_name='workflow_stalled',
                    severity='warning' if days_since_funds < 5 else 'critical',
                    message=(
                        f"Workflow SLA breach (RECOS): no recommendations generated {days_since_funds} days "
                        f"after funds received on {funds_date}. Related SLA alerts: workflow_stalled, "
                        "recos_not_sent."
                    ),
                    details={
                        'workflow_id': workflow.id,
                        'monthly_investment_id': workflow.monthly_investment_id,
                        'current_stage': workflow.current_stage,
                        'sla_breach_type': 'recos_not_sent',
                        'related_alerts': ['workflow_stalled', 'recos_not_sent'],
                        'funds_received_date': funds_date.isoformat(),
                        'days_overdue': days_since_funds,
                        'planned_amount': float(workflow.planned_amount) if workflow.planned_amount else None
                    },
                    suggested_action="Generate recommendations (RECOS stage) and notify client",
                    reference_date=funds_date,
                    workflow_id=workflow.id
                )
                if issue:
                    issues.append(issue)
        
        return issues
    
    # =========================================================================
    # CHECK 4: RECOMMENDATIONS NOT EXECUTED
    # =========================================================================
    
    def check_recommendations_executed(self, workflow: Workflow) -> List[DataIntegrityIssue]:
        """Check if workflow reached execution but no trades happened within SLA."""
        issues = []
        
        if workflow.current_stage not in ['EXEC', 'UPDATE', 'COMPLETED']:
            return issues  # Not yet at execution stage

        client_id = workflow.monthly_investment.client_id
        exec_sla = self.get_stage_sla_days(client_id, 'EXEC')

        from models import WorkflowAction
        # Prefer recommendation sent date (when recos were actually sent), then notify/generate/funds
        start_action = WorkflowAction.query.filter(
            WorkflowAction.workflow_id == workflow.id,
            WorkflowAction.action_type == 'RECOMMENDATIONS_SENT'
        ).order_by(WorkflowAction.action_date.desc()).first()
        if not start_action:
            start_action = WorkflowAction.query.filter(
                WorkflowAction.workflow_id == workflow.id,
                WorkflowAction.action_type.in_(['CLIENT_NOTIFIED', 'RECOS_GENERATED', 'FUNDS_RECEIVED'])
            ).order_by(WorkflowAction.action_date.desc()).first()

        if not start_action:
            return issues

        start_dt = start_action.action_date
        days_since_start = (datetime.utcnow().date() - start_dt.date()).days
        if days_since_start < exec_sla:
            return issues

        # If any trades happened on/after the notify/generate date, consider executed.
        # In this system, workflows can be completed even if Recommendation rows are not stored,
        # and trades may occur later than the SLA. We still want to alert only when there are
        # no trades at all (true execution miss), not when execution happened (even late).
        start_day = start_dt.date()
        end_day = start_day + timedelta(days=exec_sla)
        # Use date-based filtering to avoid false negatives due to time-of-day ordering
        first_trade_day = Transaction.query.filter(
            Transaction.client_id == client_id,
            func.date(Transaction.transaction_date) >= start_day,
            Transaction.type.in_(['BUY', 'Buy', 'buy', 'SELL', 'Sell', 'sell'])
        ).with_entities(func.min(func.date(Transaction.transaction_date))).scalar()

        if first_trade_day:
            return issues

        trades_in_window = Transaction.query.filter(
            Transaction.client_id == client_id,
            func.date(Transaction.transaction_date) >= start_day,
            func.date(Transaction.transaction_date) <= end_day,
            Transaction.type.in_(['BUY', 'Buy', 'buy', 'SELL', 'Sell', 'sell'])
        ).count()

        if trades_in_window == 0:
            start_label = 'recommendation sent' if start_action.action_type == 'RECOMMENDATIONS_SENT' else start_action.action_type
            if self.should_alert(workflow, 'recos_not_executed',
                                f"No trades within {exec_sla} days after {start_label}"):
                issue = self.create_issue(
                    client_id=client_id,
                    check_category='RECOMMENDATION_EXECUTION',
                    check_name='workflow_stalled',
                    severity='warning' if days_since_start < max(exec_sla * 2, 10) else 'critical',
                    message=(
                        f"Workflow SLA breach (EXEC): no trades within {exec_sla} days after {start_label} "
                        f"on {start_dt.date()}. Related SLA alerts: workflow_stalled, recos_not_executed."
                    ),
                    details={
                        'workflow_id': workflow.id,
                        'monthly_investment_id': workflow.monthly_investment_id,
                        'current_stage': workflow.current_stage,
                        'sla_breach_type': 'recos_not_executed',
                        'related_alerts': ['workflow_stalled', 'recos_not_executed'],
                        'start_action_type': start_action.action_type,
                        'start_action_date': start_dt.isoformat(),
                        'exec_sla_days': exec_sla,
                        'days_overdue': days_since_start,
                        'planned_amount': float(workflow.planned_amount) if workflow.planned_amount else None,
                        # Hierarchy: trade-level RECOMMENDATION_MATCH issues for recs linked to this
                        # workflow (session WORKFLOW_ID) are suppressed while this condition holds.
                        'issue_hierarchy': 'cycle_level_execution',
                    },
                    suggested_action="Check execution status; update workflow notes or execute trades",
                    reference_date=start_dt.date(),
                    workflow_id=workflow.id
                )
                if issue:
                    issues.append(issue)
        
        return issues
    
    # =========================================================================
    # CHECK 5: AMOUNT MISMATCH
    # =========================================================================
    
    def check_amount_mismatch(self, workflow: Workflow) -> List[DataIntegrityIssue]:
        """Check if actual amount differs significantly from planned."""
        issues = []
        
        if not workflow.planned_amount or not workflow.actual_amount:
            return issues
        
        planned = float(workflow.planned_amount)
        actual = float(workflow.actual_amount)
        
        if planned == 0:
            return issues
        
        variance = abs(actual - planned) / planned
        
        if variance > self.AMOUNT_TOLERANCE:
            # Check context - notes might explain the change
            if self.should_alert(workflow, 'amount_mismatch',
                                f"Actual amount {actual} differs from planned {planned} by {variance*100:.1f}%"):
                issue = self.create_issue(
                    client_id=workflow.monthly_investment.client_id,
                    check_category='RECOMMENDATION_EXECUTION',
                    check_name='amount_mismatch',
                    severity='info' if variance < 0.20 else 'warning',
                    message=f"Amount mismatch: planned ₹{planned:,.0f}, actual ₹{actual:,.0f} ({variance*100:.1f}% difference)",
                    details={
                        'workflow_id': workflow.id,
                        'monthly_investment_id': workflow.monthly_investment_id,
                        'planned_amount': planned,
                        'actual_amount': actual,
                        'variance_pct': variance * 100
                    },
                    suggested_action="Verify if amount change was intentional",
                    reference_date=workflow.investment_date
                )
                if issue:
                    issues.append(issue)
        
        return issues
