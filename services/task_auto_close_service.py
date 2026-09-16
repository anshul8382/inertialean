"""
Task Auto-Close Service
=======================
Closes OpsTasks when the underlying work is already done.
Inverts agent creation logic: if the condition that created the task is no longer true, close it.

- funds_not_received → close when workflow past FUNDS (funds received)
- recos_not_sent → close when workflow at EXEC/UPDATE/COMPLETED (recos sent)
- recos_not_executed → close when workflow at UPDATE/COMPLETED (execution done)
- workflow_stalled → close when workflow progressed past that stage
- amount_mismatch → close when planned ≈ actual (user edited to fix mistake)
- ReviewWorkflow → close when review status is 'closed'
"""

import logging
from datetime import datetime, timedelta

from extensions import db
from models import OpsTask, DataIntegrityIssue, ReviewWorkflow, Workflow

logger = logging.getLogger(__name__)

# Same tolerance as RecommendationExecutionMonitor (variance within ±10% = OK)
AMOUNT_TOLERANCE = 0.10

# Max deadline: close tasks whose deadline is more than 1 week in the future
MAX_DEADLINE_DAYS = 7


def _should_close_task_for_issue(issue: DataIntegrityIssue) -> bool:
    """
    Invert agent logic: close when the condition that created the task is no longer true.
    """
    if issue.status in ('resolved', 'false_positive'):
        return True

    if issue.check_category != 'RECOMMENDATION_EXECUTION':
        return False

    details = issue.details or {}
    workflow_id = details.get('workflow_id')
    if workflow_id is None:
        return False

    workflow = Workflow.query.get(int(workflow_id))
    if not workflow:
        return False
    if workflow.current_stage == 'COMPLETED' or getattr(workflow, 'is_archived', False):
        return True

    check_name = (issue.check_name or '').lower()
    current_stage = workflow.current_stage or 'FUNDS'

    # funds_not_received: created when stage=FUNDS, no funds. Close when past FUNDS.
    if check_name == 'funds_not_received':
        return current_stage != 'FUNDS'

    # recos_not_sent: created when at RECOS/NOTIFY, no recos action. Close when at EXEC+.
    if check_name == 'recos_not_sent':
        return current_stage in ('EXEC', 'UPDATE', 'COMPLETED')

    # recos_not_executed: created when at EXEC, no trades. Close when at UPDATE/COMPLETED.
    if check_name == 'recos_not_executed':
        return current_stage in ('UPDATE', 'COMPLETED')

    # workflow_stalled: unified workflow SLA issue. Prefer subtype-specific closure.
    if check_name == 'workflow_stalled':
        sla_breach_type = (details.get('sla_breach_type') or '').lower()
        if sla_breach_type == 'funds_not_received':
            return current_stage != 'FUNDS'
        if sla_breach_type == 'recos_not_sent':
            return current_stage in ('EXEC', 'UPDATE', 'COMPLETED')
        if sla_breach_type == 'recos_not_executed':
            return current_stage in ('UPDATE', 'COMPLETED')
        # Generic workflow stalled: close when progressed past captured stage.
        issue_stage = details.get('current_stage', '')
        stage_order = {'FUNDS': 0, 'RECOS': 1, 'NOTIFY': 2, 'EXEC': 3, 'UPDATE': 4, 'COMPLETED': 5}
        curr_idx = stage_order.get(current_stage, -1)
        issue_idx = stage_order.get(issue_stage, 99)
        return curr_idx > issue_idx

    # amount_mismatch: created when planned != actual (variance > 10%). Close when user edited to fix.
    if check_name == 'amount_mismatch':
        if not workflow.planned_amount or not workflow.actual_amount:
            return False
        planned = float(workflow.planned_amount)
        actual = float(workflow.actual_amount)
        if planned == 0:
            return False
        variance = abs(actual - planned) / planned
        return variance <= AMOUNT_TOLERANCE

    return False


def close_completed_ops_tasks() -> int:
    """
    Close OpsTasks whose underlying work is done.
    - Deadline > 1 week out: close (shouldn't have such far deadlines)
    - DataIntegrityIssue: close when issue resolved, or RECOMMENDATION_EXECUTION and workflow progressed
    - ReviewWorkflow: close when review workflow status is 'closed'

    Returns:
        Number of tasks closed
    """
    closed_count = 0
    open_statuses = ['pending', 'in_progress', 'snoozed']
    now = datetime.utcnow()
    max_deadline = now + timedelta(days=MAX_DEADLINE_DAYS)

    # 0. Close tasks whose deadline is more than 1 week in the future
    for task in OpsTask.query.filter(OpsTask.status.in_(open_statuses)).all():
        if task.deadline and task.deadline > max_deadline:
            try:
                task.status = 'completed'
                task.completed_at = now
                task.completed_by = None
                task.reminder_at = None
                task.reminder_sent = True
                task.updated_at = now
                prefix = f"[Auto-closed: deadline {task.deadline.strftime('%Y-%m-%d')} > {MAX_DEADLINE_DAYS}d out] "
                task.notes = (prefix + (task.notes or ""))[:500]
                closed_count += 1
                logger.info(f"Auto-closed OpsTask #{task.id} - deadline {task.deadline} > {MAX_DEADLINE_DAYS} days out")
            except Exception as e:
                logger.warning(f"Failed to auto-close OpsTask #{task.id}: {e}")

    # 1. OpsTasks linked to DataIntegrityIssue
    for task in OpsTask.query.filter(
        OpsTask.data_integrity_issue_id.isnot(None),
        OpsTask.status.in_(open_statuses)
    ).all():
        issue = task.data_integrity_issue
        if not issue:
            continue
        if _should_close_task_for_issue(issue):
            try:
                task.status = 'completed'
                task.completed_at = datetime.utcnow()
                task.completed_by = None  # System auto-close
                task.reminder_at = None
                task.reminder_sent = True
                task.updated_at = datetime.utcnow()
                closed_count += 1
                logger.info(f"Auto-closed OpsTask #{task.id} ({task.name[:50]}...) - issue/workflow progressed")
            except Exception as e:
                logger.warning(f"Failed to auto-close OpsTask #{task.id}: {e}")

    # 2. OpsTasks linked to ReviewWorkflow
    for task in OpsTask.query.filter(
        OpsTask.review_workflow_id.isnot(None),
        OpsTask.status.in_(open_statuses)
    ).all():
        rw = task.review_workflow
        if rw and rw.status == 'closed':
            try:
                task.status = 'completed'
                task.completed_at = datetime.utcnow()
                task.completed_by = None
                task.reminder_at = None
                task.reminder_sent = True
                task.updated_at = datetime.utcnow()
                closed_count += 1
                logger.info(f"Auto-closed OpsTask #{task.id} (ReviewWorkflow #{rw.id} closed)")
            except Exception as e:
                logger.warning(f"Failed to auto-close OpsTask #{task.id}: {e}")

    if closed_count:
        db.session.commit()

    return closed_count


def close_ops_tasks_and_resolve_issues_for_workflow(workflow_id: int, user_id=None) -> tuple:
    """
    When a workflow has moved to a new stage, close OpsTasks and resolve DataIntegrityIssues
    that are now obsolete (same logic as _should_close_task_for_issue), scoped to this workflow.

    Returns:
        (tasks_closed_count, issues_resolved_count)
    """
    from sqlalchemy import cast, String

    workflow = Workflow.query.get(workflow_id)
    if not workflow:
        return 0, 0

    client_id = None
    if getattr(workflow, 'monthly_investment', None):
        client_id = getattr(workflow.monthly_investment, 'client_id', None)
    if client_id is None:
        # Fallback: Workflow may have monthly_investment_id
        from models import MonthlyInvestment
        mi = MonthlyInvestment.query.get(getattr(workflow, 'monthly_investment_id', None))
        client_id = mi.client_id if mi else None
    if client_id is None:
        logger.warning(f"close_ops_tasks_and_resolve_issues_for_workflow: no client_id for workflow {workflow_id}")
        return 0, 0

    now = datetime.utcnow()
    open_statuses = ['pending', 'in_progress', 'snoozed']
    tasks_closed = 0
    issues_resolved = 0
    resolution_notes = f"Automatically resolved: Workflow {workflow_id} progressed past this stage"

    # Find RECOMMENDATION_EXECUTION issues for this workflow (open/baseline) with details.workflow_id
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
        DataIntegrityIssue.status.in_(['open', 'baseline']),
        DataIntegrityIssue.client_id == client_id,
        cast(DataIntegrityIssue.details['workflow_id'], String) == str(workflow_id),
    ).all()

    for issue in issues:
        if not _should_close_task_for_issue(issue):
            continue
        try:
            issue.status = 'resolved'
            issue.resolved_at = now
            issue.resolved_by = user_id
            issue.resolution_notes = resolution_notes
            issues_resolved += 1
        except Exception as e:
            logger.warning(f"Failed to resolve DataIntegrityIssue #{issue.id} for workflow {workflow_id}: {e}")

        # Close any OpsTask linked to this issue
        for task in OpsTask.query.filter(
            OpsTask.data_integrity_issue_id == issue.id,
            OpsTask.status.in_(open_statuses),
        ).all():
            try:
                task.status = 'completed'
                task.completed_at = now
                task.completed_by = user_id
                task.reminder_at = None
                task.reminder_sent = True
                task.updated_at = now
                tasks_closed += 1
                logger.info(f"Auto-closed OpsTask #{task.id} (workflow {workflow_id} progressed)")
            except Exception as e:
                logger.warning(f"Failed to auto-close OpsTask #{task.id}: {e}")

    if tasks_closed or issues_resolved:
        try:
            db.session.commit()
        except Exception as e:
            logger.warning(f"Failed to commit workflow {workflow_id} task/issue closure: {e}")
            db.session.rollback()

    return tasks_closed, issues_resolved
