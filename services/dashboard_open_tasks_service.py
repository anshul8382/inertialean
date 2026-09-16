"""
Open OpsTasks for dashboard widgets (assignee-scoped).
"""

from __future__ import annotations

from typing import List

from sqlalchemy.orm import joinedload


def fetch_open_ops_tasks_for_user(user_id: int, limit: int = 25) -> List:
    """
    Return OpsTask rows assigned to user_id with status pending/in_progress/snoozed,
    ordered by deadline ascending. Eager-loads client for display.
    """
    if not user_id:
        return []
    try:
        from models import OpsTask
    except Exception:
        return []

    return (
        OpsTask.query.options(joinedload(OpsTask.client))
        .filter(
            OpsTask.assigned_to == user_id,
            OpsTask.status.in_(["pending", "in_progress", "snoozed"]),
        )
        .order_by(OpsTask.deadline.asc())
        .limit(limit)
        .all()
    )


def count_open_ops_tasks_for_user(user_id: int) -> int:
    if not user_id:
        return 0
    try:
        from models import OpsTask
    except Exception:
        return 0
    return (
        OpsTask.query.filter(
            OpsTask.assigned_to == user_id,
            OpsTask.status.in_(["pending", "in_progress", "snoozed"]),
        ).count()
    )
