"""
Sync OpsTask rows to the assignee's default Google Tasks list (@default).

Uses the same OAuth refresh token as Google Calendar (see routes/google_calendar.py).
Requires Tasks API enabled in Google Cloud and scope https://www.googleapis.com/auth/tasks
(included in GOOGLE_USER_OAUTH_SCOPES). Users who connected before Tasks was added must
reconnect once to grant the new scope.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from flask import has_request_context, url_for

from extensions import db
from models import OpsTask, User

from services.google_calendar_service import (
    _naive_as_app_local_to_utc,
    get_google_user_oauth_access_token,
    is_oauth_configured,
)

logger = logging.getLogger(__name__)

TASKS_LIST_ID = "@default"
TASKS_API_BASE = f"https://tasks.googleapis.com/tasks/v1/lists/{TASKS_LIST_ID}/tasks"


def clear_ops_task_google_tasks_fields(task: OpsTask) -> None:
    task.google_tasks_task_id = None
    task.google_tasks_sync_user_id = None


def _task_view_url(task_id: int) -> str:
    try:
        return url_for("tasks.view_task", task_id=task_id, _external=True)
    except Exception:
        return ""


def _due_rfc3339(deadline: datetime) -> str:
    dt = _naive_as_app_local_to_utc(deadline)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _payload(task: OpsTask) -> Dict[str, Any]:
    details = _task_view_url(task.id)
    notes_parts = []
    if (task.notes or "").strip():
        notes_parts.append((task.notes or "").strip())
    if details:
        notes_parts.append(details)
    notes = "\n\n".join(notes_parts)[:8000]
    title = (task.name or "Ops task").strip()[:1024]
    if not title.lower().startswith("inertia"):
        title = f"Inertia · {title}"[:1024]
    body: Dict[str, Any] = {
        "title": title,
        "notes": notes,
        "status": "needsAction",
    }
    if task.deadline:
        body["due"] = _due_rfc3339(task.deadline)
    return body


def delete_remote_google_task(user: User, remote_task_id: Optional[str]) -> None:
    if not user or not remote_task_id:
        return
    token = get_google_user_oauth_access_token(user)
    if not token:
        return
    r = requests.delete(
        f"{TASKS_API_BASE}/{remote_task_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if r.status_code not in (200, 204, 404):
        logger.warning(
            "Google Tasks DELETE failed remote_id=%s status=%s body=%s",
            remote_task_id,
            r.status_code,
            r.text[:500],
        )


def sync_ops_task_google_tasks(task_id: int) -> None:
    """
    Create/update/delete a Google Task on the assignee's default list.
    Call after commit; logs and returns on misconfiguration or API errors.
    """
    if not has_request_context() or not is_oauth_configured():
        return

    task = OpsTask.query.get(task_id)
    if not task:
        return

    if task.status in ("completed", "cancelled"):
        if task.google_tasks_sync_user_id and task.google_tasks_task_id:
            owner = User.query.get(task.google_tasks_sync_user_id)
            if owner:
                try:
                    delete_remote_google_task(owner, task.google_tasks_task_id)
                except Exception:
                    logger.exception("Google Tasks delete failed for closed task %s", task_id)
        clear_ops_task_google_tasks_fields(task)
        db.session.commit()
        return

    if not task.assigned_to:
        if task.google_tasks_sync_user_id and task.google_tasks_task_id:
            owner = User.query.get(task.google_tasks_sync_user_id)
            if owner:
                try:
                    delete_remote_google_task(owner, task.google_tasks_task_id)
                except Exception:
                    logger.exception(
                        "Google Tasks delete failed for unassigned task %s", task_id
                    )
        clear_ops_task_google_tasks_fields(task)
        db.session.commit()
        return

    assignee = User.query.get(task.assigned_to)
    if not assignee:
        return

    assignee_token = None
    if assignee.google_calendar_refresh_token:
        try:
            assignee_token = get_google_user_oauth_access_token(assignee)
        except Exception:
            logger.exception(
                "Google Tasks OAuth refresh failed for user %s", assignee.id
            )
            return

    if (
        task.google_tasks_task_id
        and task.google_tasks_sync_user_id
        and task.google_tasks_sync_user_id != assignee.id
    ):
        prev = User.query.get(task.google_tasks_sync_user_id)
        if prev:
            try:
                delete_remote_google_task(prev, task.google_tasks_task_id)
            except Exception:
                logger.exception("Google Tasks delete failed when reassigning task %s", task_id)
        clear_ops_task_google_tasks_fields(task)
        db.session.commit()
        task = OpsTask.query.get(task_id)

    if not assignee_token:
        if task.google_tasks_task_id and task.google_tasks_sync_user_id == assignee.id:
            clear_ops_task_google_tasks_fields(task)
            db.session.commit()
        return

    payload = _payload(task)
    headers = {
        "Authorization": f"Bearer {assignee_token}",
        "Content-Type": "application/json",
    }

    try:
        if task.google_tasks_task_id and task.google_tasks_sync_user_id == assignee.id:
            r = requests.patch(
                f"{TASKS_API_BASE}/{task.google_tasks_task_id}",
                json=payload,
                headers=headers,
                timeout=30,
            )
            if r.status_code == 404:
                clear_ops_task_google_tasks_fields(task)
                db.session.commit()
                sync_ops_task_google_tasks(task_id)
                return
            if r.status_code not in (200,):
                logger.warning(
                    "Google Tasks PATCH failed task=%s status=%s body=%s",
                    task_id,
                    r.status_code,
                    r.text[:500],
                )
                return
        else:
            r = requests.post(
                TASKS_API_BASE,
                json=payload,
                headers=headers,
                timeout=30,
            )
            if r.status_code not in (200, 201):
                logger.warning(
                    "Google Tasks POST failed task=%s status=%s body=%s",
                    task_id,
                    r.status_code,
                    r.text[:500],
                )
                return
            data = r.json()
            tid = data.get("id")
            if not tid:
                logger.warning("Google Tasks POST missing id for task=%s", task_id)
                return
            task.google_tasks_task_id = tid
            task.google_tasks_sync_user_id = assignee.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Google Tasks sync failed for task %s", task_id)
