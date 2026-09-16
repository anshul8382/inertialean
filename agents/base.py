"""
Base Agent Class
================
Abstract base class for all agents in the system.
Provides common functionality for:
- Run management and logging
- Exception/pattern checking
- Issue creation
- Feedback handling
"""

import uuid
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from extensions import db
from models import (
    AgentRun, DataIntegrityIssue, DataIntegrityException, 
    ClientBehaviorPattern, Client
)

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base class for all agents.
    
    Subclasses must implement:
    - agent_name: str property
    - agent_version: str property  
    - run_checks(client_id, **kwargs): Generator yielding issues
    """
    
    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Unique identifier for this agent"""
        pass
    
    @property
    def agent_version(self) -> str:
        """Version string for this agent"""
        return "1.0.0"
    
    def __init__(self):
        self.current_run: Optional[AgentRun] = None
        self.issues_found: List[DataIntegrityIssue] = []
        self._exceptions_cache: Dict[str, DataIntegrityException] = {}
        self._patterns_cache: Dict[int, List[ClientBehaviorPattern]] = {}
    
    # =========================================================================
    # RUN MANAGEMENT
    # =========================================================================
    
    def start_run(self, mode: str, scope: Dict = None, 
                  triggered_by: str = 'manual', user_id: int = None) -> str:
        """
        Start a new agent run. Returns run_id.
        
        Args:
            mode: 'full_audit', 'incremental', 'on_demand', 'baseline'
            scope: Optional dict specifying clients, date range, etc.
            triggered_by: 'scheduler', 'manual', 'api'
            user_id: User who triggered the run (if manual/api)
        """
        # run_id must fit in agent_run.run_id VARCHAR(50); longest agent_name is ~27 chars
        run_id = f"{self.agent_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        
        self.current_run = AgentRun(
            run_id=run_id,
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            run_mode=mode,
            scope=scope or {},
            triggered_by=triggered_by,
            triggered_by_user=user_id,
            status='running'
        )
        db.session.add(self.current_run)
        db.session.commit()
        
        self.issues_found = []
        self._exceptions_cache = {}
        self._patterns_cache = {}
        
        logger.info(f"Started {self.agent_name} run: {run_id} (mode={mode})")
        return run_id
    
    def complete_run(self, error: Exception = None):
        """Complete the current run and save statistics."""
        if not self.current_run:
            return
        
        self.current_run.completed_at = datetime.utcnow()
        started = self.current_run.started_at or self.current_run.completed_at
        self.current_run.duration_seconds = max(
            0.0,
            (self.current_run.completed_at - started).total_seconds(),
        )
        
        if error:
            self.current_run.status = 'failed'
            self.current_run.error_message = str(error)
            import traceback
            self.current_run.error_traceback = traceback.format_exc()
        else:
            self.current_run.status = 'completed'
        
        # Calculate statistics
        self.current_run.issues_found = len(self.issues_found)
        
        severity_counts = {}
        category_counts = {}
        for issue in self.issues_found:
            sev = str(issue.severity or "unknown")
            cat = str(issue.check_category or "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
            category_counts[cat] = category_counts.get(cat, 0) + 1

        self.current_run.issues_by_severity = severity_counts
        self.current_run.issues_by_category = category_counts
        
        db.session.commit()
        
        logger.info(
            f"Completed {self.agent_name} run: {self.current_run.run_id} "
            f"({self.current_run.clients_processed} clients, "
            f"{self.current_run.issues_found} issues, "
            f"{self.current_run.duration_seconds:.1f}s)"
        )
    
    # =========================================================================
    # EXCEPTION & PATTERN CHECKING
    # =========================================================================
    
    def _load_exceptions(self, client_id: int):
        """Load exceptions for a client into cache."""
        if client_id not in self._exceptions_cache:
            exceptions = DataIntegrityException.query.filter_by(
                client_id=client_id
            ).all()
            for exc in exceptions:
                self._exceptions_cache[exc.exception_hash] = exc
    
    def _load_patterns(self, client_id: int):
        """Load behavior patterns for a client into cache."""
        if client_id not in self._patterns_cache:
            patterns = ClientBehaviorPattern.query.filter_by(
                client_id=client_id
            ).all()
            self._patterns_cache[client_id] = patterns
    
    def _generate_exception_hash(self, client_id: int, check_category: str, 
                                  check_name: str, **identifiers) -> str:
        """Generate a unique hash for an exception."""
        import hashlib
        parts = [str(client_id), check_category, check_name]
        for key in sorted(identifiers.keys()):
            if identifiers[key] is not None:
                parts.append(f"{key}:{identifiers[key]}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()
    
    def should_skip_issue(self, client_id: int, check_category: str, 
                          check_name: str, **identifiers) -> Tuple[bool, Optional[str]]:
        """
        Check if this specific issue should be skipped due to exception.
        
        Args:
            client_id: Client ID
            check_category: Category of the check
            check_name: Name of the specific check
            **identifiers: transaction_id, recommendation_id, reference_date, security_id, etc.
        
        Returns:
            (should_skip: bool, reason: Optional[str])
        """
        self._load_exceptions(client_id)
        
        exc_hash = self._generate_exception_hash(
            client_id, check_category, check_name, **identifiers
        )
        
        if exc_hash in self._exceptions_cache:
            exc = self._exceptions_cache[exc_hash]
            return True, f"Exception: {exc.reason}"
        
        return False, None
    
    def get_client_pattern(self, client_id: int, check_category: str, 
                           pattern_type: str, security_id: int = None) -> Optional[ClientBehaviorPattern]:
        """
        Get a client's behavior pattern for a specific check.
        
        Returns pattern if found and confidence >= 0.7, else None.
        """
        self._load_patterns(client_id)
        
        patterns = self._patterns_cache.get(client_id, [])
        for pattern in patterns:
            if pattern.check_category != check_category:
                continue
            if pattern.pattern_type != pattern_type:
                continue
            if pattern.confidence < 0.7:
                continue
            
            # Check scope
            if pattern.applies_to == 'all':
                return pattern
            elif pattern.applies_to == 'security_specific' and pattern.security_id == security_id:
                return pattern
        
        return None
    
    # =========================================================================
    # ISSUE CREATION
    # =========================================================================
    
    def create_issue(self, client_id: int, check_category: str, check_name: str,
                     severity: str, message: str, details: Dict = None,
                     suggested_action: str = None, group_key: str = None,
                     security_id: int = None, transaction_id: int = None,
                     recommendation_id: int = None, cashflow_id: int = None,
                     reference_date = None, date_range_start = None, 
                     date_range_end = None, **skip_check_kwargs) -> Optional[DataIntegrityIssue]:
        """
        Create a new issue if it shouldn't be skipped.
        
        Returns the created issue, or None if skipped.
        """
        # Check for exceptions
        skip_kwargs = {
            'transaction_id': transaction_id,
            'recommendation_id': recommendation_id,
            'security_id': security_id,
            'reference_date': str(reference_date) if reference_date else None,
            **skip_check_kwargs
        }
        
        should_skip, skip_reason = self.should_skip_issue(
            client_id, check_category, check_name, **skip_kwargs
        )
        
        if should_skip:
            logger.debug(f"Skipping issue for client {client_id}: {skip_reason}")
            return None
        
        # Check for existing open issue with same identifiers
        existing_q = DataIntegrityIssue.query.filter_by(
            client_id=client_id,
            check_category=check_category,
            check_name=check_name,
            status='open',
            transaction_id=transaction_id,
            recommendation_id=recommendation_id,
            reference_date=reference_date
        )
        if group_key is not None:
            existing_q = existing_q.filter_by(group_key=group_key)
        existing = existing_q.first()

        if existing:
            logger.debug(f"Issue already exists: {existing.id}")
            return existing
        
        issue = DataIntegrityIssue(
            client_id=client_id,
            check_category=check_category,
            check_name=check_name,
            severity=severity,
            message=message,
            details=details or {},
            suggested_action=suggested_action,
            group_key=group_key or f"{check_category}_{check_name}_{datetime.utcnow().strftime('%Y_%m')}",
            security_id=security_id,
            transaction_id=transaction_id,
            recommendation_id=recommendation_id,
            cashflow_id=cashflow_id,
            reference_date=reference_date,
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            run_id=self.current_run.run_id if self.current_run else None
        )
        
        db.session.add(issue)
        self.issues_found.append(issue)

        # Auto-assign based on TaskAssignmentRule and create OpsTask for /tasks
        try:
            from services.task_assignment_service import get_assignee_for_issue, create_ops_task_from_issue
            assignee_id = get_assignee_for_issue(issue)
            if assignee_id:
                issue.assigned_to = assignee_id
                db.session.flush()  # ensure issue has id
                create_ops_task_from_issue(issue, assignee_id=assignee_id)
        except Exception as e:
            logger.debug(f"Task assignment for issue skipped: {e}")

        return issue
    
    # =========================================================================
    # ABSTRACT METHODS FOR SUBCLASSES
    # =========================================================================
    
    @abstractmethod
    def run_checks(self, client_id: int, **kwargs) -> List[DataIntegrityIssue]:
        """
        Run all checks for a specific client.
        
        Args:
            client_id: Client to check
            **kwargs: Additional parameters (date_range, etc.)
        
        Returns:
            List of issues found
        """
        pass
    
    # =========================================================================
    # HIGH-LEVEL RUN METHODS
    # =========================================================================
    
    def run_full_audit(self, client_ids: List[int] = None, user_id: int = None):
        """
        Run full audit on all (or specified) clients.
        Checks all historical data.
        """
        run_id = self.start_run(
            mode='full_audit',
            scope={'client_ids': client_ids},
            triggered_by='manual' if user_id else 'scheduler',
            user_id=user_id
        )
        
        try:
            if client_ids is None:
                clients = Client.query.all()
                client_ids = [c.id for c in clients]
            
            for client_id in client_ids:
                try:
                    self.run_checks(client_id)
                    self.current_run.clients_processed += 1
                    db.session.commit()
                except Exception as e:
                    logger.error(f"Error checking client {client_id}: {e}", exc_info=True)
                    db.session.rollback()
            
            self.complete_run()
            
        except Exception as e:
            self.complete_run(error=e)
            raise
        
        return run_id
    
    def run_incremental(self, days: int = 7, user_id: int = None):
        """
        Run incremental check on recent changes.
        Only checks data modified in the last N days.
        """
        from datetime import timedelta
        
        run_id = self.start_run(
            mode='incremental',
            scope={'days': days},
            triggered_by='scheduler',
            user_id=user_id
        )
        
        try:
            # Clients with recent activity: trades (created_at often null — use coalesce)
            # plus clients with non-completed workflows touched in the window.
            cutoff = datetime.utcnow() - timedelta(days=days)
            from sqlalchemy import func, or_
            from models import Transaction, Workflow, MonthlyInvestment

            tx_clients = (
                db.session.query(Transaction.client_id)
                .filter(
                    Transaction.client_id.isnot(None),
                    func.coalesce(
                        Transaction.created_at, Transaction.transaction_date
                    )
                    >= cutoff,
                )
                .distinct()
                .all()
            )
            tx_ids = {c[0] for c in tx_clients if c[0] is not None}

            wf_clients = (
                db.session.query(MonthlyInvestment.client_id)
                .join(
                    Workflow,
                    Workflow.monthly_investment_id == MonthlyInvestment.id,
                )
                .filter(
                    Workflow.is_archived == False,
                    Workflow.current_stage != "COMPLETED",
                    or_(
                        Workflow.updated_at >= cutoff,
                        Workflow.created_at >= cutoff,
                    ),
                )
                .distinct()
                .all()
            )
            wf_ids = {c[0] for c in wf_clients if c[0] is not None}

            client_ids = sorted(tx_ids | wf_ids)
            
            for client_id in client_ids:
                try:
                    self.run_checks(client_id, since=cutoff)
                    self.current_run.clients_processed += 1
                    db.session.commit()
                except Exception as e:
                    logger.error(f"Error checking client {client_id}: {e}", exc_info=True)
                    db.session.rollback()
            
            self.complete_run()
            
        except Exception as e:
            self.complete_run(error=e)
            raise
        
        return run_id
    
    def run_baseline(self, client_ids: List[int] = None, user_id: int = None):
        """
        Run in baseline mode - generates report without creating alerts.
        Used for initial setup to establish what's "normal".
        """
        run_id = self.start_run(
            mode='baseline',
            scope={'client_ids': client_ids},
            triggered_by='manual',
            user_id=user_id
        )
        
        try:
            if client_ids is None:
                clients = Client.query.all()
                client_ids = [c.id for c in clients]
            
            for client_id in client_ids:
                try:
                    issues = self.run_checks(client_id)
                    # In baseline mode, mark issues as 'baseline' status
                    for issue in issues:
                        issue.status = 'baseline'
                    self.current_run.clients_processed += 1
                    db.session.commit()
                except Exception as e:
                    logger.error(f"Error checking client {client_id}: {e}", exc_info=True)
                    db.session.rollback()
            
            self.complete_run()
            
        except Exception as e:
            self.complete_run(error=e)
            raise
        
        return run_id
