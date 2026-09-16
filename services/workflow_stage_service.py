"""
Workflow Stage Service
======================
Central place for "workflow stage changed" side effects. Call this whenever
workflow.current_stage is updated so that alerts and tasks stay in sync.

- Closes previous-stage workflow_sla alerts
- Closes obsolete OpsTasks and resolves obsolete DataIntegrityIssues for that workflow
"""

import logging

logger = logging.getLogger(__name__)


def on_workflow_stage_changed(workflow_id: int, old_stage: str, new_stage: str, user_id=None) -> dict:
    """
    Call whenever a workflow's stage is updated. Ensures alerts and tasks
    for the previous stage are closed immediately.

    Args:
        workflow_id: Workflow.id
        old_stage: Stage before the update (e.g. 'FUNDS', 'RECOS')
        new_stage: Stage after the update (e.g. 'RECOS', 'NOTIFY', 'COMPLETED')
        user_id: User who triggered the change (for resolve audit)

    Returns:
        dict with keys: alerts_closed (int), tasks_closed (int), issues_resolved (int)
    """
    result = {'alerts_closed': 0, 'tasks_closed': 0, 'issues_resolved': 0}

    if not workflow_id or old_stage == new_stage:
        return result

    # 1. Close previous-stage workflow_sla alerts
    try:
        from alert_service import AlertService
        n = AlertService.close_previous_stage_alerts(workflow_id, old_stage, user_id)
        result['alerts_closed'] = n or 0
        if n:
            logger.info(f"Workflow {workflow_id}: closed {n} alert(s) for previous stage {old_stage}")
    except Exception as e:
        logger.warning(f"Workflow {workflow_id}: failed to close previous stage alerts: {e}")

    # 2. Close obsolete OpsTasks and resolve obsolete RECOMMENDATION_EXECUTION issues for this workflow
    try:
        from services.task_auto_close_service import close_ops_tasks_and_resolve_issues_for_workflow
        tasks_closed, issues_resolved = close_ops_tasks_and_resolve_issues_for_workflow(workflow_id, user_id)
        result['tasks_closed'] = tasks_closed
        result['issues_resolved'] = issues_resolved
        if tasks_closed or issues_resolved:
            logger.info(f"Workflow {workflow_id}: closed {tasks_closed} task(s), resolved {issues_resolved} issue(s)")
    except Exception as e:
        logger.warning(f"Workflow {workflow_id}: failed to close tasks/resolve issues: {e}")

    return result
