"""
Hierarchy between workflow-level execution issues and trade-level RECOMMENDATION_MATCH issues.

When a monthly workflow is in "cycle-level non-execution" (same conditions as
RECOMMENDATION_EXECUTION / recos_not_executed), per-line unexecuted / mismatch
issues are redundant. This module detects those workflows and links recommendations
to workflows via RecommendationSession → WORKFLOW_ID notes (see
recommendation_workflow_link_service).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional, Set

from sqlalchemy import func

from models import (
    DataIntegrityIssue,
    MonthlyInvestment,
    OpsTask,
    Recommendation,
    Transaction,
    Workflow,
    WorkflowAction,
)

if TYPE_CHECKING:
    from models import Recommendation as RecommendationModel

logger = logging.getLogger(__name__)

# Trade-level checks superseded when cycle-level non-execution applies
TRADE_LEVEL_CHECK_NAMES = frozenset(
    {
        "unexecuted_recommendation",
        "unexecuted_recommendations_batch",
        "quantity_mismatch",
        "price_mismatch",
        "trade_execution_mismatch",
    }
)


def workflow_id_for_recommendation(rec: RecommendationModel) -> Optional[int]:
    """Resolve workflow id from session notes / active monthly investment; None if unknown."""
    if not rec or not rec.client_id:
        return None
    if not rec.session_id:
        return None
    from models import RecommendationSession

    session = RecommendationSession.query.get(rec.session_id)
    if not session:
        return None
    from services.recommendation_workflow_link_service import (
        resolve_workflow_for_recommendation_session,
    )

    wf = resolve_workflow_for_recommendation_session(session, rec.client_id)
    return wf.id if wf else None


def get_workflow_ids_with_cycle_level_non_execution(client_id: int) -> Set[int]:
    """
    Workflows where RECOMMENDATION_EXECUTION / recos_not_executed would be raised:
    stage at EXEC+, SLA passed since start action, no BUY/SELL on or after start day,
    and should_alert allows the issue (same as RecommendationExecutionMonitor).
    """
    from agents.recommendation_execution_monitor import RecommendationExecutionMonitor

    monitor = RecommendationExecutionMonitor()
    blocked: Set[int] = set()

    workflows = (
        Workflow.query.join(MonthlyInvestment)
        .filter(
            MonthlyInvestment.client_id == client_id,
            Workflow.is_archived == False,
            Workflow.current_stage != "COMPLETED",
        )
        .all()
    )

    for workflow in workflows:
        if workflow.current_stage not in ("EXEC", "UPDATE", "COMPLETED"):
            continue

        exec_sla = monitor.get_stage_sla_days(client_id, "EXEC")

        start_action = (
            WorkflowAction.query.filter(
                WorkflowAction.workflow_id == workflow.id,
                WorkflowAction.action_type == "RECOMMENDATIONS_SENT",
            )
            .order_by(WorkflowAction.action_date.desc())
            .first()
        )
        if not start_action:
            start_action = (
                WorkflowAction.query.filter(
                    WorkflowAction.workflow_id == workflow.id,
                    WorkflowAction.action_type.in_(
                        ["CLIENT_NOTIFIED", "RECOS_GENERATED", "FUNDS_RECEIVED"]
                    ),
                )
                .order_by(WorkflowAction.action_date.desc())
                .first()
            )

        if not start_action:
            continue

        start_dt = start_action.action_date
        days_since_start = (datetime.utcnow().date() - start_dt.date()).days
        if days_since_start < exec_sla:
            continue

        start_day = start_dt.date()
        end_day = start_day + timedelta(days=exec_sla)

        first_trade_day = (
            Transaction.query.filter(
                Transaction.client_id == client_id,
                func.date(Transaction.transaction_date) >= start_day,
                Transaction.type.in_(["BUY", "Buy", "buy", "SELL", "Sell", "sell"]),
            )
            .with_entities(func.min(func.date(Transaction.transaction_date)))
            .scalar()
        )

        if first_trade_day:
            continue

        trades_in_window = (
            Transaction.query.filter(
                Transaction.client_id == client_id,
                func.date(Transaction.transaction_date) >= start_day,
                func.date(Transaction.transaction_date) <= end_day,
                Transaction.type.in_(["BUY", "Buy", "buy", "SELL", "Sell", "sell"]),
            ).count()
        )

        if trades_in_window != 0:
            continue

        start_label = (
            "recommendation sent"
            if start_action.action_type == "RECOMMENDATIONS_SENT"
            else start_action.action_type
        )
        if monitor.should_alert(
            workflow,
            "recos_not_executed",
            f"No trades within {exec_sla} days after {start_label}",
        ):
            blocked.add(workflow.id)

    return blocked


def resolve_trade_level_issues_superseded_by_cycle(
    client_id: int, blocked_workflow_ids: Set[int]
) -> int:
    """
    Resolve open RECOMMENDATION_MATCH line-item issues when their recommendation's
    workflow is in cycle-level non-execution. Closes linked OpsTasks.

    Returns:
        Number of issues resolved.
    """
    if not blocked_workflow_ids:
        return 0

    now = datetime.utcnow()
    open_statuses = ["pending", "in_progress", "snoozed"]
    resolved = 0

    issues = (
        DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
            DataIntegrityIssue.check_name.in_(TRADE_LEVEL_CHECK_NAMES),
            DataIntegrityIssue.status.in_(["open", "baseline"]),
            DataIntegrityIssue.recommendation_id.isnot(None),
        )
        .all()
    )

    for issue in issues:
        rec = Recommendation.query.get(issue.recommendation_id)
        if not rec:
            continue
        wf_id = workflow_id_for_recommendation(rec)
        if wf_id is None or wf_id not in blocked_workflow_ids:
            continue

        det = dict(issue.details or {})
        det["superseded_by"] = "cycle_level_non_execution"
        det["superseded_workflow_id"] = wf_id
        issue.details = det
        issue.status = "resolved"
        issue.resolution_type = "superseded"
        issue.resolved_at = now
        issue.resolution_notes = (
            "Superseded: workflow-level recommendation not executed "
            f"(workflow_id={wf_id}); trade-level detail not applicable until execution starts."
        )
        resolved += 1

        for task in OpsTask.query.filter(
            OpsTask.data_integrity_issue_id == issue.id,
            OpsTask.status.in_(open_statuses),
        ).all():
            task.status = "completed"
            task.completed_at = now
            task.completed_by = None
            task.reminder_at = None
            task.reminder_sent = True
            task.updated_at = now

    # Batch issues tied to a blocked workflow (same cycle-level story)
    batch_issues = (
        DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == "RECOMMENDATION_MATCH",
            DataIntegrityIssue.check_name == "unexecuted_recommendations_batch",
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        )
        .all()
    )
    for issue in batch_issues:
        wid = (issue.details or {}).get("workflow_id")
        if wid is None:
            continue
        try:
            wid = int(wid)
        except (TypeError, ValueError):
            continue
        if wid not in blocked_workflow_ids:
            continue
        det = dict(issue.details or {})
        det["superseded_by"] = "cycle_level_non_execution"
        det["superseded_workflow_id"] = wid
        issue.details = det
        issue.status = "resolved"
        issue.resolution_type = "superseded"
        issue.resolved_at = now
        issue.resolution_notes = (
            "Superseded: workflow-level recommendation not executed "
            f"(workflow_id={wid}); batch detail not applicable until execution starts."
        )
        resolved += 1
        for task in OpsTask.query.filter(
            OpsTask.data_integrity_issue_id == issue.id,
            OpsTask.status.in_(open_statuses),
        ).all():
            task.status = "completed"
            task.completed_at = now
            task.completed_by = None
            task.reminder_at = None
            task.reminder_sent = True
            task.updated_at = now

    if resolved:
        logger.info(
            "Resolved %s superseded RECOMMENDATION_MATCH issues for client_id=%s",
            resolved,
            client_id,
        )

    return resolved
