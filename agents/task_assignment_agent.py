"""
Task Assignment Agent
=====================
Backfills assigned_to on DataIntegrityIssue and ReviewWorkflow records.
Run after rule changes or migration to apply new assignment rules to existing records.
Integrity issues and open reviews are assigned to the client's advisor
(see get_assignee_for_issue / get_assignee_for_review_status).
"""

import logging

from extensions import db
from models import DataIntegrityIssue, OpsTask, ReviewWorkflow
from services.task_assignment_service import (
    get_assignee_for_issue,
    get_assignee_for_review_status,
    create_ops_task_from_issue,
    create_ops_task_from_review_workflow,
    issue_has_manual_assignment,
)

logger = logging.getLogger(__name__)

_OPEN_TASK_STATUSES = ["pending", "in_progress", "snoozed"]


class TaskAssignmentAgent:
    """
    Assigns open DataIntegrityIssues and ReviewWorkflows based on TaskAssignmentService
    policy (client advisor for book ownership; workflow stage for rec-exec).
    """

    @property
    def agent_name(self) -> str:
        return "task_assignment_agent"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    def run_backfill(self) -> tuple:
        """
        Assign / reassign open issues to current policy.
        Creates OpsTasks for assigned issues so they appear in /tasks.
        Returns (issues_assigned_or_reassigned, ops_tasks_created).
        """
        open_issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.status.in_(["open", "baseline"]),
        ).all()

        updated = 0
        tasks_created = 0
        for issue in open_issues:
            try:
                # Respect explicit user reassignment (do not overwrite)
                if issue_has_manual_assignment(issue):
                    if issue.assigned_to:
                        task = create_ops_task_from_issue(
                            issue, assignee_id=issue.assigned_to
                        )
                        if task:
                            tasks_created += 1
                    continue

                assignee_id = get_assignee_for_issue(issue)
                if not assignee_id:
                    continue
                if issue.assigned_to != assignee_id:
                    issue.assigned_to = assignee_id
                    updated += 1
                    for task in OpsTask.query.filter(
                        OpsTask.data_integrity_issue_id == issue.id,
                        OpsTask.status.in_(_OPEN_TASK_STATUSES),
                    ).all():
                        if task.assigned_to != assignee_id:
                            task.assigned_to = assignee_id
                task = create_ops_task_from_issue(issue, assignee_id=assignee_id)
                if task:
                    tasks_created += 1
            except Exception as e:
                logger.warning("Task assignment failed for issue %s: %s", issue.id, e)

        # Ensure OpsTasks exist for already-correct assignees (idempotent)
        assigned_issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.status.in_(["open", "baseline"]),
            DataIntegrityIssue.assigned_to.isnot(None),
        ).all()
        for issue in assigned_issues:
            try:
                task = create_ops_task_from_issue(issue, assignee_id=issue.assigned_to)
                if task:
                    tasks_created += 1
            except Exception as e:
                logger.warning("OpsTask creation failed for issue %s: %s", issue.id, e)

        review_assigned, review_tasks = self._backfill_review_workflows()
        tasks_created += review_tasks

        if updated or tasks_created or review_assigned:
            try:
                db.session.commit()
            except Exception as e:
                logger.exception("TaskAssignmentAgent backfill commit failed: %s", e)
                db.session.rollback()
                raise
            logger.info(
                "TaskAssignmentAgent backfill: assigned/reassigned %s issues, "
                "created %s OpsTasks, assigned %s review workflows",
                updated,
                tasks_created,
                review_assigned,
            )

        try:
            from services.task_auto_close_service import close_completed_ops_tasks

            closed = close_completed_ops_tasks()
            if closed:
                logger.info("TaskAssignmentAgent: auto-closed %s OpsTasks", closed)
        except Exception as e:
            logger.warning(
                "TaskAssignmentAgent: close_completed_ops_tasks skipped: %s", e
            )

        return updated, tasks_created

    def _backfill_review_workflows(self) -> tuple:
        """Assign open ReviewWorkflows to each client's advisor; create OpsTasks.
        Returns (workflows_assigned_or_reassigned, ops_tasks_created)."""
        assigned_count = 0
        tasks_count = 0

        open_workflows = ReviewWorkflow.query.filter(
            ReviewWorkflow.status.in_(["initiated", "sent", "meeting"]),
        ).all()
        for wf in open_workflows:
            try:
                assignee_id = get_assignee_for_review_status(wf)
                if not assignee_id:
                    continue
                if wf.assigned_to != assignee_id:
                    wf.assigned_to = assignee_id
                    assigned_count += 1
                    for task in OpsTask.query.filter(
                        OpsTask.review_workflow_id == wf.id,
                        OpsTask.status.in_(_OPEN_TASK_STATUSES),
                    ).all():
                        if task.assigned_to != assignee_id:
                            task.assigned_to = assignee_id
                task = create_ops_task_from_review_workflow(
                    wf, assignee_id=assignee_id
                )
                if task:
                    tasks_count += 1
            except Exception as e:
                logger.warning(
                    "Review assignment failed for workflow %s: %s", wf.id, e
                )

        return assigned_count, tasks_count
