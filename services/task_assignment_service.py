"""
Task Assignment Service
=======================
Resolves assignee for workflow alerts and data integrity issues based on
configurable TaskAssignmentRule records. Falls back to client advisor / ops manager
when no rule matches.
Can create OpsTasks from assigned issues so they appear in /tasks.
"""

import logging
from datetime import datetime, timedelta, date, time
from typing import Any, Dict, List, Optional

from extensions import db
from models import Client, TaskAssignmentRule, Workflow, DataIntegrityIssue, OpsTask, User, ReviewWorkflow

logger = logging.getLogger(__name__)

# Max deadline: tasks should not be created with deadline more than 1 week from now
MAX_DEADLINE_DAYS = 7

# Module-level cache for rules (invalidated on rule changes; TTL could be added)
_rules_cache = None
_rules_cache_ts = 0
_ISSUE_CONTRIB_PREFIX = "Top active negative contributors"


def _base_issue_message_for_task(issue_message: Optional[str]) -> str:
    """
    Keep task header concise by removing contributor detail suffix from issue text.
    """
    msg = (issue_message or "").strip()
    if not msg:
        return ""
    marker = f" {_ISSUE_CONTRIB_PREFIX}:"
    if marker in msg:
        return msg.split(marker, 1)[0].strip()
    return msg


def _format_underperformers_for_notes(issue: DataIntegrityIssue, max_items: int = 5) -> str:
    """
    Build contributor summary for notes using percentage values.
    """
    details = issue.details if isinstance(issue.details, dict) else {}
    rows: List[Dict[str, Any]] = details.get("underperforming_securities") or []
    if not rows:
        return ""

    formatted = []
    for row in rows[:max_items]:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol") or row.get("name") or "N/A"
        pct = float(row.get("contribution_percent") or 0.0)
        formatted.append(f"{symbol} ({pct:+.2f}%)")
    if not formatted:
        return ""
    return f"{_ISSUE_CONTRIB_PREFIX} (%): {', '.join(formatted)}."


def _load_rules():
    """Load active rules from DB, ordered by priority desc."""
    global _rules_cache, _rules_cache_ts
    rules = TaskAssignmentRule.query.filter_by(is_active=True).order_by(
        TaskAssignmentRule.priority.desc()
    ).all()
    _rules_cache = rules
    return rules


def _get_rules():
    """Get rules (use cache)."""
    global _rules_cache
    if _rules_cache is None:
        _load_rules()
    return _rules_cache or []


def invalidate_rules_cache():
    """Call after creating/updating/deleting rules."""
    global _rules_cache
    _rules_cache = None


def get_assignee_for_workflow_stage(workflow) -> Optional[int]:
    """
    Get assignee user_id for a workflow based on its current_stage and rules.
    If no rule matches, falls back to ops_manager role (workflow operations).
    """
    stage = getattr(workflow, "current_stage", None) or "FUNDS"
    for rule in _get_rules():
        if rule.rule_type == "workflow_stage" and rule.match_value == stage:
            return rule.assigned_user_id

    try:
        from services.task_role_assignee import resolve_user_id_for_assignment_role

        uid = resolve_user_id_for_assignment_role("ops_manager")
        if uid:
            return uid
    except Exception as e:
        logger.warning("Task assignment workflow fallback failed: %s", e)
    return None


def get_assignee_for_review_status(review_workflow) -> Optional[int]:
    """
    Get assignee user_id for a ReviewWorkflow.

    Policy (client-wise):
    - Prefer the client's assigned advisor (Client.advisor_id).
    - Else review_status TaskAssignmentRule (legacy override when no advisor).
    - Else ops_manager role.
    """
    # Prefer the advisor for this client (book ownership)
    try:
        client = getattr(review_workflow, "client", None)
        client_id = getattr(review_workflow, "client_id", None)
        if client is None and client_id:
            client = Client.query.get(int(client_id))
        advisor_id = getattr(client, "advisor_id", None) if client else None
        if advisor_id:
            return int(advisor_id)
    except (TypeError, ValueError) as e:
        logger.warning("Client advisor lookup failed for review assignee: %s", e)

    status = getattr(review_workflow, "status", None) or "initiated"
    for rule in _get_rules():
        if rule.rule_type == "review_status" and rule.match_value == status:
            return rule.assigned_user_id

    try:
        from services.task_role_assignee import resolve_user_id_for_assignment_role

        uid = resolve_user_id_for_assignment_role("ops_manager")
        if uid:
            return uid
    except Exception as e:
        logger.warning("Review assignment fallback failed: %s", e)
    return None


def apply_review_assignment(review_workflow) -> bool:
    """
    Set assigned_to on a ReviewWorkflow based on its status and rules.
    Call after creating or updating a ReviewWorkflow.
    Returns True if assignment was applied.

    Does not auto-create OpsTasks (REVIEW_CREATE_OPS_TASKS defaults false). The open review
    is the work item; create_ops_task_from_review_workflow remains for rare explicit enable.
    """
    try:
        assignee_id = get_assignee_for_review_status(review_workflow)
        if assignee_id:
            review_workflow.assigned_to = assignee_id
            if review_workflow.status in ('initiated', 'sent', 'meeting'):
                create_ops_task_from_review_workflow(review_workflow, assignee_id=assignee_id)
            return True
    except Exception as e:
        logger.warning(f"Review assignment failed: {e}")
    return False


def get_assignee_for_issue(issue) -> Optional[int]:
    """
    Get assignee user_id for a DataIntegrityIssue.

    Policy:
    - RECOMMENDATION_EXECUTION with a workflow → workflow_stage rules (ops).
    - All other open integrity issues → client's assigned advisor (Client.advisor_id).
    - Fall back to issue_category TaskAssignmentRule, then ops_manager / admin.
    """
    category = getattr(issue, "check_category", None) or ""
    details = getattr(issue, "details", None) or {}

    # Special case: RECOMMENDATION_EXECUTION - derive from workflow stage
    if category == "RECOMMENDATION_EXECUTION":
        workflow_id = details.get("workflow_id")
        if workflow_id is not None:
            try:
                w = Workflow.query.get(int(workflow_id))
                if w:
                    uid = get_assignee_for_workflow_stage(w)
                    if uid:
                        return uid
            except (ValueError, TypeError):
                pass

    # Prefer the advisor assigned to this client (book ownership)
    client_id = getattr(issue, "client_id", None)
    if client_id:
        try:
            client = getattr(issue, "client", None)
            if client is None or getattr(client, "id", None) != int(client_id):
                client = Client.query.get(int(client_id))
            advisor_id = getattr(client, "advisor_id", None) if client else None
            if advisor_id:
                return int(advisor_id)
        except (TypeError, ValueError) as e:
            logger.warning("Client advisor lookup failed for issue assignee: %s", e)

    # Match by issue_category rule (legacy / override when client has no advisor)
    for rule in _get_rules():
        if rule.rule_type == "issue_category" and rule.match_value == category:
            return rule.assigned_user_id

    # Default policy when no DB rule: portfolio performance → admin role
    if category == "PORTFOLIO_PERFORMANCE":
        try:
            from services.task_role_assignee import resolve_user_id_for_assignment_role

            uid = resolve_user_id_for_assignment_role("admin")
            if uid:
                return uid
        except Exception as e:
            logger.warning("Portfolio performance assignee fallback failed: %s", e)

    try:
        from services.task_role_assignee import resolve_user_id_for_assignment_role

        return resolve_user_id_for_assignment_role("ops_manager")
    except Exception:
        return None


_OPEN_TASK_STATUSES = ("pending", "in_progress", "snoozed")


def issue_has_manual_assignment(issue) -> bool:
    """True when a user explicitly reassigned the issue (backfill must not overwrite)."""
    details = getattr(issue, "details", None) or {}
    if not isinstance(details, dict):
        return False
    return bool(details.get("manual_assignment"))


def reassign_issue(
    issue: DataIntegrityIssue,
    assignee_user_id: Optional[int],
    *,
    by_user_id: Optional[int] = None,
) -> bool:
    """
    Manually set assigned_to on an open issue and sync linked open OpsTasks.
    Marks details.manual_assignment so TaskAssignmentAgent backfill skips overwrite.
    Returns True if the issue was updated.
    """
    if not issue or getattr(issue, "status", None) not in ("open", "baseline"):
        return False

    new_uid = int(assignee_user_id) if assignee_user_id else None
    if new_uid is not None:
        user = User.query.get(new_uid)
        if not user or not getattr(user, "is_active", True):
            raise ValueError(f"Invalid or inactive assignee user_id={new_uid}")

    changed = issue.assigned_to != new_uid
    issue.assigned_to = new_uid

    details = dict(issue.details) if isinstance(issue.details, dict) else {}
    details["manual_assignment"] = True
    if by_user_id is not None:
        details["assigned_by"] = int(by_user_id)
    details["assigned_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if new_uid is None:
        details["unassigned"] = True
    else:
        details.pop("unassigned", None)
    issue.details = details

    for task in OpsTask.query.filter(
        OpsTask.data_integrity_issue_id == issue.id,
        OpsTask.status.in_(_OPEN_TASK_STATUSES),
    ).all():
        if task.assigned_to != new_uid:
            task.assigned_to = new_uid
            changed = True

    if new_uid and not OpsTask.query.filter(
        OpsTask.data_integrity_issue_id == issue.id,
        OpsTask.status.in_(_OPEN_TASK_STATUSES),
    ).first():
        create_ops_task_from_issue(issue, assignee_id=new_uid)
        changed = True

    return True  # open issue accepted (assignee and/or lock applied)


def reassign_issues(
    issue_ids: List[int],
    assignee_user_id: Optional[int],
    *,
    by_user_id: Optional[int] = None,
) -> int:
    """Reassign multiple open issues. Returns count of issues touched."""
    updated = 0
    for raw_id in issue_ids:
        try:
            issue = DataIntegrityIssue.query.get(int(raw_id))
        except (TypeError, ValueError):
            continue
        if not issue:
            continue
        if reassign_issue(issue, assignee_user_id, by_user_id=by_user_id):
            updated += 1
    return updated


def list_assignable_users() -> List[User]:
    """Active users for issue/task assignee dropdowns."""
    return User.query.filter_by(is_active=True).order_by(User.username).all()


def get_assignee_for_alert(
    alert_type: str,
    alert_subtype: str,
    workflow=None,
    issues=None,
    client_id=None,
) -> Optional[int]:
    """
    Unified assignee lookup for alerts.
    Used by create_alert_from_issues and similar flows.
    """
    # Workflow SLA alerts: use workflow stage
    if alert_type == "workflow_sla" and workflow:
        return get_assignee_for_workflow_stage(workflow)

    # Consolidated orchestrator alerts: align with TaskAssignmentRule / OpsTask routing.
    if alert_type == "agent_orchestrated" and issues:
        workflow_ids = set()
        for i in issues:
            wid = (i.details or {}).get("workflow_id")
            if wid is not None:
                try:
                    workflow_ids.add(int(wid))
                except (TypeError, ValueError):
                    pass
        if len(workflow_ids) == 1:
            w = Workflow.query.get(list(workflow_ids)[0])
            if w:
                uid = get_assignee_for_workflow_stage(w)
                if uid:
                    return uid
        sev_rank = {"critical": 3, "warning": 2, "info": 1}
        for issue in sorted(
            issues,
            key=lambda i: sev_rank.get((i.severity or "info").lower(), 0),
            reverse=True,
        ):
            uid = get_assignee_for_issue(issue)
            if uid:
                return uid
        if client_id:
            c = Client.query.get(client_id)
            if c and getattr(c, "advisor_id", None):
                return c.advisor_id
        try:
            from services.task_role_assignee import resolve_user_id_for_assignment_role

            return resolve_user_id_for_assignment_role("ops_manager")
        except Exception:
            return None

    # Recommendation execution alerts from issues: prefer workflow-based, else category
    if alert_type == "recommendation_execution" and issues:
        # If all issues share same workflow, use workflow stage
        workflow_ids = set()
        for i in issues:
            wid = (i.details or {}).get("workflow_id")
            if wid is not None:
                workflow_ids.add(int(wid))
        if len(workflow_ids) == 1:
            w = Workflow.query.get(list(workflow_ids)[0])
            if w:
                return get_assignee_for_workflow_stage(w)
        # Else use first issue's category
        if issues:
            return get_assignee_for_issue(issues[0])

    # Data integrity alerts: use first issue's category
    if alert_type in ("data_integrity", "recommendation_execution") and issues:
        return get_assignee_for_issue(issues[0])

    try:
        from services.task_role_assignee import resolve_user_id_for_assignment_role

        return resolve_user_id_for_assignment_role("ops_manager")
    except Exception:
        return None


def create_ops_task_from_issue(issue: DataIntegrityIssue, assignee_id: Optional[int] = None) -> Optional[OpsTask]:
    """
    Create an OpsTask from a DataIntegrityIssue so it appears in /tasks.
    Skips if an OpsTask already exists for this issue.
    When DI_CREATE_OPS_TASKS is false (default), skips creation — use advisor digests instead.
    Returns the created OpsTask or None.
    """
    if not issue or not issue.id:
        return None
    try:
        from services.ops_digest_email_service import di_create_ops_tasks_enabled

        if not di_create_ops_tasks_enabled():
            logger.debug(
                "Skipping OpsTask for DataIntegrityIssue %s (DI_CREATE_OPS_TASKS off)",
                issue.id,
            )
            return None
    except Exception as e:
        logger.debug("DI_CREATE_OPS_TASKS check failed, skipping OpsTask: %s", e)
        return None
    # Skip if we already have an OpsTask for this issue (pending/in_progress/snoozed)
    existing = OpsTask.query.filter_by(data_integrity_issue_id=issue.id).filter(
        OpsTask.status.in_(['pending', 'in_progress', 'snoozed'])
    ).first()
    if existing:
        return None

    assignee_id = assignee_id or getattr(issue, 'assigned_to', None)
    if not assignee_id:
        return None

    # created_by: use assignee for system-created tasks, or first admin
    created_by = assignee_id
    creator = User.query.get(created_by)
    if not creator:
        creator = User.query.filter_by(is_admin=True, is_active=True).first()
        created_by = creator.id if creator else None
    if not created_by:
        return None

    # Build task name (truncate to 200 chars)
    client_name = getattr(issue.client, 'name', None) or f'Client {issue.client_id}'
    base_message = _base_issue_message_for_task(issue.message)
    msg_preview = base_message[:150]
    name = f"[{issue.check_category or 'issue'}] {msg_preview}"
    if len(name) > 200:
        name = name[:197] + '...'

    # Deadline: 3 days from now (or from detected_at), capped at 1 week max
    base_dt = issue.detected_at if issue.detected_at else datetime.utcnow()
    if isinstance(base_dt, datetime):
        deadline = base_dt + timedelta(days=3)
    else:
        deadline = datetime.utcnow() + timedelta(days=3)
    max_deadline = datetime.utcnow() + timedelta(days=MAX_DEADLINE_DAYS)
    if deadline > max_deadline:
        deadline = max_deadline

    # Priority from severity
    severity = (issue.severity or 'warning').lower()
    priority = 'high' if severity == 'critical' else 'medium' if severity == 'warning' else 'low'

    # Notes with link to issue
    notes = f"Auto-created from DataIntegrityIssue #{issue.id}. {issue.suggested_action or ''}".strip()
    if issue.check_category == "PORTFOLIO_PERFORMANCE":
        contrib_line = _format_underperformers_for_notes(issue)
        if contrib_line:
            notes = f"{notes}\n{contrib_line}".strip()

    task = OpsTask(
        name=name,
        deadline=deadline,
        assigned_to=assignee_id,
        created_by=created_by,
        client_id=issue.client_id,
        data_integrity_issue_id=issue.id,
        notes=notes,
        priority=priority,
        status='pending',
    )
    try:
        # Savepoint so a failed flush does not rollback issue assignments in the same backfill.
        with db.session.begin_nested():
            db.session.add(task)
            db.session.flush()
        return task
    except Exception as e:
        logger.warning(f"Failed to create OpsTask for issue {issue.id}: {e}")
        return None


def create_ops_task_from_review_workflow(
    workflow: ReviewWorkflow, assignee_id: Optional[int] = None
) -> Optional[OpsTask]:
    """
    Create an OpsTask from an open ReviewWorkflow so it appears in /tasks.
    Only for status in (initiated, sent, meeting). Skips closed workflows.
    Skips if an OpsTask already exists for this workflow.

    When REVIEW_CREATE_OPS_TASKS is false (default), skips creation — the open review is the
    work item; users may still create a task manually for tracking.
    Returns the created OpsTask or None.
    """
    if not workflow or not workflow.id:
        return None
    try:
        from services.ops_digest_email_service import review_create_ops_tasks_enabled

        if not review_create_ops_tasks_enabled():
            logger.debug(
                "Skipping OpsTask for ReviewWorkflow %s (REVIEW_CREATE_OPS_TASKS off)",
                workflow.id,
            )
            return None
    except Exception as e:
        logger.debug("REVIEW_CREATE_OPS_TASKS check failed, skipping OpsTask: %s", e)
        return None
    if workflow.status == 'closed':
        return None

    existing = OpsTask.query.filter_by(review_workflow_id=workflow.id).filter(
        OpsTask.status.in_(['pending', 'in_progress', 'snoozed'])
    ).first()
    if existing:
        return None

    # Don't create tasks for reviews more than 1 week out - they clutter the list
    rd = workflow.review_date
    if isinstance(rd, date):
        cutoff = (datetime.utcnow() + timedelta(days=MAX_DEADLINE_DAYS)).date()
        if rd > cutoff:
            return None

    assignee_id = assignee_id or getattr(workflow, 'assigned_to', None)
    if not assignee_id:
        return None

    created_by = assignee_id
    creator = User.query.get(created_by)
    if not creator:
        creator = User.query.filter_by(is_admin=True, is_active=True).first()
        created_by = creator.id if creator else None
    if not created_by:
        return None

    client_name = getattr(workflow.client, 'name', None) or f'Client {workflow.client_id}'
    name = f"[Review] {client_name} - {workflow.status} ({workflow.review_date})"
    if len(name) > 200:
        name = name[:197] + '...'

    # Deadline: review_date end of day, capped at 1 week max (no tasks > 1 week out)
    rd = workflow.review_date
    if isinstance(rd, date):
        deadline = datetime.combine(rd, time(23, 59, 59))
    else:
        deadline = datetime.utcnow() + timedelta(days=MAX_DEADLINE_DAYS)
    max_deadline = datetime.utcnow() + timedelta(days=MAX_DEADLINE_DAYS)
    if deadline > max_deadline:
        deadline = max_deadline

    notes = f"Auto-created from ReviewWorkflow #{workflow.id}. Status: {workflow.status}."

    task = OpsTask(
        name=name,
        deadline=deadline,
        assigned_to=assignee_id,
        created_by=created_by,
        client_id=workflow.client_id,
        review_workflow_id=workflow.id,
        notes=notes,
        priority='medium',
        status='pending',
    )
    try:
        with db.session.begin_nested():
            db.session.add(task)
            db.session.flush()
        return task
    except Exception as e:
        logger.warning(f"Failed to create OpsTask for review workflow {workflow.id}: {e}")
        return None
