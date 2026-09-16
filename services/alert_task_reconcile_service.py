"""
Reconcile alerts and OpsTasks with current DataIntegrityIssue state.

- Close issue-linked OpsTasks when the issue is resolved/false_positive, missing, or
  no longer open/baseline (supplements task_auto_close RECOMMENDATION_EXECUTION logic).
- Resolve alerts when every issue pointing at that alert is non-open.
- Resolve stale agent/data/recommendation alerts when the client has no open issues.
- Clear alert_id on non-open issues so orchestrator can attach fresh consolidated alerts.

All resolution uses a system user (first active admin, else ops manager).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from extensions import db
from models import DataIntegrityIssue, OpsTask, Workflow
from alert_system_models import Alert

logger = logging.getLogger(__name__)

OPEN_ISSUE_STATUSES = ("open", "baseline")
OPEN_ALERT_STATUSES = ("active", "acknowledged", "snoozed")
OPEN_TASK_STATUSES = ("pending", "in_progress", "snoozed")

STALE_ALERT_TYPES_FOR_EMPTY_CLIENT = (
    "agent_orchestrated",
    "data_integrity",
    "recommendation_execution",
)

STAGE_ORDER = {"FUNDS": 0, "RECOS": 1, "NOTIFY": 2, "EXEC": 3, "UPDATE": 4, "COMPLETED": 5}


def get_system_resolver_user_id() -> int:
    from models import User

    u = User.query.filter_by(is_admin=True, is_active=True).first()
    if u:
        return u.id
    try:
        from alert_service import AlertService

        oid = AlertService._get_ops_manager_user_id()
        if oid:
            return oid
    except Exception as e:
        logger.warning("Could not get ops manager for resolver: %s", e)
    u2 = User.query.filter_by(is_active=True).first()
    if u2:
        return u2.id
    return 1


def _system_resolver_user_id() -> int:
    """Backward-compatible alias for internal callers."""
    return get_system_resolver_user_id()


def resolve_alert_if_all_linked_issues_closed(
    alert_id: Optional[int],
    *,
    resolver_user_id: Optional[int] = None,
    dry_run: bool = False,
) -> bool:
    """
    Resolve one alert only when all linked issues are non-open.
    Returns True when the alert is (or would be) resolved in this call.
    """
    if not alert_id:
        return False
    alert = Alert.query.get(alert_id)
    if not alert or alert.status not in OPEN_ALERT_STATUSES:
        return False

    linked = DataIntegrityIssue.query.filter_by(alert_id=alert_id).all()
    if not linked:
        return False
    if any(i.status in OPEN_ISSUE_STATUSES for i in linked):
        return False

    if dry_run:
        return True

    resolver = resolver_user_id or get_system_resolver_user_id()
    alert.resolve(
        resolver,
        "Reconciled: all linked DataIntegrityIssues are closed or resolved.",
    )
    logger.info("Reconcile resolved Alert #%s (all linked issues non-open)", alert_id)
    return True


def resolve_stale_client_wide_alerts_for_client(
    client_id: Optional[int],
    *,
    resolver_user_id: Optional[int] = None,
    dry_run: bool = False,
) -> int:
    """
    Resolve stale client-wide alerts for one client when no open issues remain.
    """
    if not client_id:
        return 0
    open_n = (
        DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.status.in_(list(OPEN_ISSUE_STATUSES)),
        ).count()
    )
    if open_n > 0:
        return 0

    n = 0
    alerts = Alert.query.filter(
        Alert.alert_type.in_(STALE_ALERT_TYPES_FOR_EMPTY_CLIENT),
        Alert.status.in_(list(OPEN_ALERT_STATUSES)),
        Alert.client_id == client_id,
    ).all()
    for alert in alerts:
        n += 1
        if dry_run:
            continue
        resolver = resolver_user_id or get_system_resolver_user_id()
        alert.resolve(
            resolver,
            "Reconciled: client has no open/baseline data integrity issues.",
        )
        logger.info(
            "Reconcile resolved stale Alert #%s type=%s client_id=%s",
            alert.id,
            alert.alert_type,
            client_id,
        )
    return n


def sync_alert_lifecycle_for_issue(
    issue: Optional[DataIntegrityIssue],
    *,
    resolver_user_id: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """
    Targeted alert sync for one issue context.
    - Resolve linked alert if all linked issues are closed.
    - Resolve stale client-wide alerts when client has no open issues.
    """
    if not issue:
        return {"linked_alerts_resolved": 0, "client_stale_alerts_resolved": 0}

    linked_resolved = 1 if resolve_alert_if_all_linked_issues_closed(
        getattr(issue, "alert_id", None),
        resolver_user_id=resolver_user_id,
        dry_run=dry_run,
    ) else 0
    stale_resolved = resolve_stale_client_wide_alerts_for_client(
        getattr(issue, "client_id", None),
        resolver_user_id=resolver_user_id,
        dry_run=dry_run,
    )
    return {
        "linked_alerts_resolved": linked_resolved,
        "client_stale_alerts_resolved": stale_resolved,
    }


def _issue_task_should_close(issue: Optional[DataIntegrityIssue]) -> bool:
    if issue is None:
        return True
    if issue.status not in OPEN_ISSUE_STATUSES:
        return True
    from services.task_auto_close_service import _should_close_task_for_issue

    return _should_close_task_for_issue(issue)


def close_ops_tasks_for_stale_issues(dry_run: bool = False) -> int:
    """
    Close open OpsTasks tied to a DataIntegrityIssue when the issue is gone or not open,
    or when task_auto_close would close for workflow/resolution.
    """
    n = 0
    now = datetime.utcnow()
    tasks = (
        OpsTask.query.filter(
            OpsTask.data_integrity_issue_id.isnot(None),
            OpsTask.status.in_(list(OPEN_TASK_STATUSES)),
        )
        .all()
    )
    for task in tasks:
        issue = task.data_integrity_issue
        if not _issue_task_should_close(issue):
            continue
        n += 1
        if dry_run:
            continue
        reason = (
            "underlying issue resolved or not open"
            if issue and issue.status not in OPEN_ISSUE_STATUSES
            else "issue missing or workflow/obsolescence"
        )
        prefix = f"[Reconciled: {reason}] "
        task.status = "completed"
        task.completed_at = now
        task.completed_by = None
        task.reminder_at = None
        task.reminder_sent = True
        task.updated_at = now
        task.notes = (prefix + (task.notes or ""))[:500]
        logger.info("Reconcile closed OpsTask #%s (issue=%s)", task.id, task.data_integrity_issue_id)
    if n and not dry_run:
        db.session.commit()
    return n


def resolve_alerts_all_linked_issues_closed(dry_run: bool = False) -> int:
    """Resolve active alerts that have linked issues and none are open/baseline."""
    n = 0
    alert_ids = (
        db.session.query(DataIntegrityIssue.alert_id)
        .filter(DataIntegrityIssue.alert_id.isnot(None))
        .distinct()
        .all()
    )
    for (aid,) in alert_ids:
        if not aid:
            continue
        alert = Alert.query.get(aid)
        if not alert or alert.status not in OPEN_ALERT_STATUSES:
            continue
        if resolve_alert_if_all_linked_issues_closed(aid, dry_run=dry_run):
            n += 1
    if n and not dry_run:
        db.session.commit()
    return n


def resolve_stale_client_wide_alerts(dry_run: bool = False) -> int:
    """
    Resolve alerts tied to a client when that client has no open/baseline issues.
    Types: agent_orchestrated, data_integrity, recommendation_execution.
    """
    n = 0
    alerts = Alert.query.filter(
        Alert.alert_type.in_(STALE_ALERT_TYPES_FOR_EMPTY_CLIENT),
        Alert.status.in_(list(OPEN_ALERT_STATUSES)),
        Alert.client_id.isnot(None),
    ).all()
    seen_clients = set()
    for alert in alerts:
        cid = alert.client_id
        if not cid or cid in seen_clients:
            continue
        seen_clients.add(cid)
        n += resolve_stale_client_wide_alerts_for_client(cid, dry_run=dry_run)
    if n and not dry_run:
        db.session.commit()
    return n


def clear_alert_id_on_non_open_issues(dry_run: bool = False) -> int:
    """Remove alert_id from issues that are not open/baseline (hygiene for orchestrator)."""
    q = DataIntegrityIssue.query.filter(
        ~DataIntegrityIssue.status.in_(list(OPEN_ISSUE_STATUSES)),
        DataIntegrityIssue.alert_id.isnot(None),
    )
    if dry_run:
        return q.count()
    updated = q.update({DataIntegrityIssue.alert_id: None}, synchronize_session=False)
    if updated:
        db.session.commit()
    return updated


def run_workflow_sla_stale_backfill(dry_run: bool = False) -> Dict[str, int]:
    """
    Same behaviour as scripts/backfill_workflow_stage_alerts_and_tasks.py (workflow SLA +
    RECOMMENDATION_EXECUTION task/issue closure per workflow).
    """
    from alert_service import AlertService
    from services.task_auto_close_service import close_ops_tasks_and_resolve_issues_for_workflow

    total_alerts = 0
    total_tasks = 0
    total_issues = 0
    workflows = Workflow.query.filter(Workflow.current_stage.isnot(None)).all()
    for w in workflows:
        current = (w.current_stage or "FUNDS").strip().upper()
        current_idx = STAGE_ORDER.get(current, -1)
        if current_idx <= 0:
            continue
        for stage, idx in STAGE_ORDER.items():
            if stage == "COMPLETED":
                continue
            if idx >= current_idx:
                break
            if dry_run:
                cnt = Alert.query.filter(
                    Alert.workflow_id == w.id,
                    Alert.alert_type == "workflow_sla",
                    Alert.alert_subtype == f"{stage}_delay",
                    Alert.status.in_(list(OPEN_ALERT_STATUSES)),
                ).count()
                total_alerts += cnt
            else:
                n = AlertService.close_previous_stage_alerts(w.id, stage, user_id=None)
                total_alerts += n
        if not dry_run:
            tc, ir = close_ops_tasks_and_resolve_issues_for_workflow(w.id, None)
            total_tasks += tc
            total_issues += ir
        else:
            # Dry-run: do not resolve issues; task counts would need duplicate logic — omit
            pass
    return {
        "workflow_sla_alerts": total_alerts,
        "workflow_tasks_closed": total_tasks,
        "workflow_issues_resolved": total_issues,
    }


def run_full_reconcile(
    dry_run: bool = False,
    *,
    workflow_backfill: bool = True,
    run_task_auto_close: bool = True,
    refresh_assignment_and_alerts: bool = False,
) -> Dict[str, Any]:
    """
    Ordered reconcile pass. When dry_run=True, no commits except where sub-calls commit
    (workflow backfill uses AlertService which commits per stage batch — pass dry_run there).
    """
    stats: Dict[str, Any] = {}
    stats["ops_tasks_closed_stale_issue"] = close_ops_tasks_for_stale_issues(dry_run=dry_run)
    stats["alerts_resolved_all_linked_closed"] = resolve_alerts_all_linked_issues_closed(
        dry_run=dry_run
    )
    stats["alerts_resolved_no_open_client_issues"] = resolve_stale_client_wide_alerts(
        dry_run=dry_run
    )
    stats["issues_cleared_alert_id"] = clear_alert_id_on_non_open_issues(dry_run=dry_run)
    if workflow_backfill:
        stats["workflow_backfill"] = run_workflow_sla_stale_backfill(dry_run=dry_run)
    if run_task_auto_close and not dry_run:
        from services.task_auto_close_service import close_completed_ops_tasks

        stats["task_auto_close_extra"] = close_completed_ops_tasks()
    elif run_task_auto_close:
        stats["task_auto_close_extra"] = "skipped_in_dry_run"

    if refresh_assignment_and_alerts and not dry_run:
        from agents.task_assignment_agent import TaskAssignmentAgent
        from agents.orchestrator import AlertOrchestrator, ALERT_AVAILABLE

        ta = TaskAssignmentAgent()
        u, t = ta.run_backfill()
        stats["task_assignment_backfill"] = {"issues_assigned": u, "ops_tasks_created": t}
        if ALERT_AVAILABLE:
            orch = AlertOrchestrator()
            run_id = orch.orchestrate_alerts(client_ids=None, user_id=None)
            stats["orchestrator_run_id"] = str(run_id) if run_id else None
        else:
            stats["orchestrator_run_id"] = None
    elif refresh_assignment_and_alerts:
        stats["task_assignment_backfill"] = "skipped_in_dry_run"
        stats["orchestrator_run_id"] = "skipped_in_dry_run"

    return stats
