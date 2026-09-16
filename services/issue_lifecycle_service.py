"""
Issue lifecycle service.

Centralizes cross-entity lifecycle commands so task/issue/alert state moves together.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from extensions import db
from models import DataIntegrityIssue, OpsTask
from services.alert_task_reconcile_service import (
    OPEN_ISSUE_STATUSES,
    OPEN_TASK_STATUSES,
    sync_alert_lifecycle_for_issue,
)


def _mark_task_completed(task: OpsTask, actor_user_id: Optional[int]) -> bool:
    """
    Mark one task completed if it is still open.
    Returns True if this call changed task state.
    """
    if not task or task.status not in OPEN_TASK_STATUSES:
        return False
    now = datetime.utcnow()
    task.status = "completed"
    task.completed_at = now
    task.completed_by = actor_user_id
    task.reminder_at = None
    task.reminder_sent = True
    task.updated_at = now
    return True


def close_linked_open_tasks(
    issue_id: int,
    actor_user_id: Optional[int] = None,
    *,
    exclude_task_id: Optional[int] = None,
) -> int:
    """
    Close all open OpsTasks linked to a DataIntegrityIssue.
    """
    if not issue_id:
        return 0
    q = OpsTask.query.filter(
        OpsTask.data_integrity_issue_id == issue_id,
        OpsTask.status.in_(list(OPEN_TASK_STATUSES)),
    )
    if exclude_task_id:
        q = q.filter(OpsTask.id != exclude_task_id)

    closed = 0
    for task in q.all():
        if _mark_task_completed(task, actor_user_id):
            closed += 1
    return closed


def resolve_issue_and_sync(
    issue: Optional[DataIntegrityIssue],
    *,
    resolution_type: str = "fixed",
    actor_user_id: Optional[int] = None,
    notes: str = "",
    source: str = "manual",
    commit: bool = True,
    exclude_task_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Resolve an issue (idempotent), close linked open tasks, and sync related alerts.
    """
    if not issue:
        return {
            "issue_resolved": 0,
            "linked_tasks_closed": 0,
            "linked_alerts_resolved": 0,
            "client_stale_alerts_resolved": 0,
        }

    issue_resolved = 0
    if issue.status in OPEN_ISSUE_STATUSES:
        issue.status = "resolved"
        issue.resolution_type = resolution_type
        issue.resolved_at = datetime.utcnow()
        issue.resolved_by = actor_user_id
        msg = notes.strip() if notes else ""
        source_note = f"Resolved via lifecycle source={source}"
        issue.resolution_notes = f"{msg} | {source_note}" if msg else source_note
        issue_resolved = 1

    linked_tasks_closed = close_linked_open_tasks(
        issue.id, actor_user_id=actor_user_id, exclude_task_id=exclude_task_id
    )
    alert_sync = sync_alert_lifecycle_for_issue(issue, resolver_user_id=actor_user_id, dry_run=False)

    if commit:
        db.session.commit()

    return {
        "issue_resolved": issue_resolved,
        "linked_tasks_closed": linked_tasks_closed,
        "linked_alerts_resolved": alert_sync.get("linked_alerts_resolved", 0),
        "client_stale_alerts_resolved": alert_sync.get("client_stale_alerts_resolved", 0),
    }


def complete_task_and_sync(
    task_id: int,
    actor_user_id: Optional[int],
    *,
    commit: bool = True,
) -> Dict[str, Any]:
    """
    Complete one OpsTask and, for issue-backed tasks, resolve/sync lifecycle.
    Idempotent for already-closed tasks.
    """
    task = OpsTask.query.get(task_id)
    if not task:
        raise ValueError(f"OpsTask not found: {task_id}")

    task_closed = 1 if _mark_task_completed(task, actor_user_id) else 0
    result: Dict[str, Any] = {
        "task_closed": task_closed,
        "issue_resolved": 0,
        "linked_tasks_closed": 0,
        "linked_alerts_resolved": 0,
        "client_stale_alerts_resolved": 0,
    }

    issue = task.data_integrity_issue if task.data_integrity_issue_id else None
    if issue:
        issue_result = resolve_issue_and_sync(
            issue,
            resolution_type="task_complete",
            actor_user_id=actor_user_id,
            notes=f"Completed from OpsTask #{task.id}",
            source="task_complete",
            commit=False,
            exclude_task_id=task.id,
        )
        for k, v in issue_result.items():
            result[k] = result.get(k, 0) + int(v or 0)

    if commit:
        db.session.commit()

    return result
