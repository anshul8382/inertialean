"""Task management routes for daily operations."""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from extensions import db
from models import OpsTask, User, Client
from routes.forms import OpsTaskForm, OpsTaskSnoozeForm, OpsTaskReminderForm
from access_control import require_advisor_or_manager, require_tasks_access, user_is_ops_manager
from datetime import datetime
from functools import wraps
import logging

logger = logging.getLogger(__name__)
tasks_bp = Blueprint('tasks', __name__)


def _sync_google_calendar_for_task(task_id):
    try:
        from services.google_calendar_service import sync_ops_task_google_calendar

        sync_ops_task_google_calendar(task_id)
    except Exception:
        logger.exception("Google Calendar sync failed for task %s", task_id)


def _sync_google_tasks_for_task(task_id):
    try:
        from services.google_tasks_service import sync_ops_task_google_tasks

        sync_ops_task_google_tasks(task_id)
    except Exception:
        logger.exception("Google Tasks sync failed for task %s", task_id)


def _sync_google_integrations_for_ops_task(task_id):
    _sync_google_calendar_for_task(task_id)
    _sync_google_tasks_for_task(task_id)


def _wake_expired_snoozes():
    """Wake tasks whose snooze time has passed."""
    now = datetime.utcnow()
    expired = OpsTask.query.filter(
        OpsTask.status == 'snoozed',
        OpsTask.snoozed_until.isnot(None),
        OpsTask.snoozed_until <= now
    ).all()
    if not expired:
        return 0
    for t in expired:
        t.status = t.pre_snooze_status or 'pending'
        t.pre_snooze_status = None
        t.snoozed_until = None
        t.snoozed_at = None
        t.snoozed_by = None
        t.snooze_reason = None
        t.updated_at = now
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to wake expired snoozed tasks")
        return 0
    return len(expired)


def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.error(f"Error in {f.__name__}: {str(e)}", exc_info=True)
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('tasks.list_tasks'))
    return decorated_function


def _populate_task_form(form, *, include_assignee_id=None):
    """Build assignee dropdown; User model uses username (no name field)."""
    assignee_choices = [(0, 'Unassigned')]
    seen_ids = {0}
    for u in User.query.filter_by(is_active=True).order_by(User.username).all():
        assignee_choices.append((u.id, u.username))
        seen_ids.add(u.id)
    if include_assignee_id and include_assignee_id not in seen_ids:
        extra = User.query.get(include_assignee_id)
        if extra:
            suffix = ' (inactive)' if not extra.is_active else ''
            assignee_choices.append((extra.id, f'{extra.username}{suffix}'))
    form.assigned_to.choices = assignee_choices
    from access_control import get_accessible_clients_ordered
    form.client_id.choices = [(0, 'None')] + [(c.id, c.name) for c in get_accessible_clients_ordered()]


def _can_manage_tasks():
    """True if user can create/edit/assign/cancel tasks (advisor/manager/ops manager)."""
    return current_user.is_advisor or current_user.is_manager or user_is_ops_manager(current_user)


def _can_access_task(task):
    """True if user can view/act on this task (assigned to, created by, or elevated ops roles)."""
    if _can_manage_tasks():
        return True
    return task.assigned_to == current_user.id or task.created_by == current_user.id


@tasks_bp.route('/')
@login_required
@require_tasks_access
@handle_errors
def list_tasks():
    _wake_expired_snoozes()
    status = request.args.get('status', 'active')  # default: active only (exclude completed/cancelled)
    view_filter = request.args.get('view', 'all')  # all, my_tasks
    priority = request.args.get('priority', 'all')
    group_by_client = request.args.get('group_by', 'client') == 'client'
    assigned_to_filter = request.args.get('assigned_to', 'all')  # all, unassigned, or user id
    task_type_filter = request.args.get('task_type', 'all')  # all, data_integrity, review_workflow, manual
    can_manage = _can_manage_tasks()

    query = OpsTask.query
    if status == 'active':
        query = query.filter(OpsTask.status.in_(['pending', 'in_progress', 'snoozed']))
    elif status != 'all':
        query = query.filter(OpsTask.status == status)
    if priority != 'all':
        query = query.filter(OpsTask.priority == priority)
    if view_filter == 'my_tasks' or not can_manage:
        query = query.filter(OpsTask.assigned_to == current_user.id)
    if can_manage and assigned_to_filter == 'unassigned':
        query = query.filter(OpsTask.assigned_to.is_(None))
    elif can_manage and assigned_to_filter != 'all':
        try:
            uid = int(assigned_to_filter)
            query = query.filter(OpsTask.assigned_to == uid)
        except (TypeError, ValueError):
            pass
    if task_type_filter == 'data_integrity':
        query = query.filter(OpsTask.data_integrity_issue_id.isnot(None))
    elif task_type_filter == 'review_workflow':
        query = query.filter(OpsTask.review_workflow_id.isnot(None))
    elif task_type_filter == 'manual':
        query = query.filter(
            OpsTask.data_integrity_issue_id.is_(None),
            OpsTask.review_workflow_id.is_(None)
        )

    tasks = query.order_by(OpsTask.deadline.asc()).all()

    # Group tasks by client for "one entry per client" view
    from collections import defaultdict
    tasks_by_client = defaultdict(list)
    for t in tasks:
        cid = t.client_id if t.client_id else 0
        tasks_by_client[cid].append(t)

    clients_with_tasks = []
    for cid, task_list in tasks_by_client.items():
        client = Client.query.get(cid) if cid else None
        open_tasks = [t for t in task_list if t.status in ('pending', 'in_progress', 'snoozed')]
        earliest = min(task_list, key=lambda x: x.deadline).deadline if task_list else None
        has_high_priority_open = any(
            t.priority == 'high' and t.status in ('pending', 'in_progress', 'snoozed')
            for t in task_list
        )
        clients_with_tasks.append({
            'client_id': cid,
            'client_name': client.name if client else 'Other / No client',
            'client': client,
            'tasks': sorted(task_list, key=lambda x: x.deadline),
            'open_count': len(open_tasks),
            'total_count': len(task_list),
            'earliest_deadline': earliest,
            'has_high_priority_open': has_high_priority_open,
        })
    clients_with_tasks.sort(key=lambda x: x['client_name'].lower())

    now = datetime.utcnow()
    stats_query = OpsTask.query
    if not can_manage:
        stats_query = stats_query.filter(OpsTask.assigned_to == current_user.id)
    total = stats_query.filter(OpsTask.status.in_(['pending', 'in_progress', 'snoozed'])).count()
    pending = stats_query.filter(OpsTask.status.in_(['pending', 'in_progress'])).count()
    overdue = stats_query.filter(
        OpsTask.status.in_(['pending', 'in_progress']),
        OpsTask.deadline < now
    ).count()
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    due_today = stats_query.filter(
        OpsTask.status.in_(['pending', 'in_progress']),
        OpsTask.deadline >= now,
        OpsTask.deadline <= today_end
    ).count()

    # Users for Assigned To filter (and for form dropdowns); show active users, fallback to all if none
    assignable_users = []
    if can_manage:
        assignable_users = User.query.filter_by(is_active=True).order_by(User.username).all()
        if not assignable_users:
            assignable_users = User.query.order_by(User.username).all()

    return render_template('tasks/list.html',
                         tasks=tasks,
                         clients_with_tasks=clients_with_tasks,
                         group_by_client=group_by_client,
                         status=status,
                         view_filter=view_filter,
                         priority=priority,
                         assigned_to_filter=assigned_to_filter,
                         task_type_filter=task_type_filter,
                         assignable_users=assignable_users,
                         total_tasks=total,
                         pending_tasks=pending,
                         overdue_tasks=overdue,
                         due_today_tasks=due_today,
                         can_manage_tasks=can_manage)


@tasks_bp.route('/new', methods=['GET', 'POST'])
@login_required
@require_advisor_or_manager
@handle_errors
def new_task():
    form = OpsTaskForm()
    _populate_task_form(form)
    if form.validate_on_submit():
        task = OpsTask(
            name=form.name.data,
            deadline=form.deadline.data,
            assigned_to=form.assigned_to.data if form.assigned_to.data else None,
            notes=form.notes.data,
            priority=form.priority.data,
            client_id=form.client_id.data if form.client_id.data else None,
            created_by=current_user.id,
            status='pending'
        )
        db.session.add(task)
        db.session.commit()
        _sync_google_integrations_for_ops_task(task.id)
        flash(f'Task "{task.name}" created.', 'success')
        return redirect(url_for('tasks.view_task', task_id=task.id))
    return render_template('tasks/form.html', form=form, task=None)


def _get_recommendation_id_for_task(task):
    """Get recommendation_id from task's linked DataIntegrityIssue (for RECOMMENDATION_MATCH etc.)."""
    if not task.data_integrity_issue_id:
        return None
    from models import DataIntegrityIssue
    issue = DataIntegrityIssue.query.get(task.data_integrity_issue_id)
    if not issue:
        return None
    rec_id = getattr(issue, 'recommendation_id', None)
    if rec_id is not None:
        return int(rec_id) if rec_id else None
    details = issue.details or {}
    rec_id = details.get('recommendation_id')
    if rec_id is not None:
        try:
            return int(rec_id)
        except (TypeError, ValueError):
            pass
    # Batch unexecuted issue: link to first line in checklist (full list in issue.details.related_items)
    items = details.get("related_items")
    if isinstance(items, list) and items:
        rid = items[0].get("recommendation_id")
        if rid is not None:
            try:
                return int(rid)
            except (TypeError, ValueError):
                pass
    unused_lines = (details.get("unused_session") or {}).get("lines")
    if isinstance(unused_lines, list) and unused_lines:
        rid = unused_lines[0].get("recommendation_id")
        if rid is not None:
            try:
                return int(rid)
            except (TypeError, ValueError):
                pass
    return None


@tasks_bp.route('/<int:task_id>')
@login_required
@require_tasks_access
@handle_errors
def view_task(task_id):
    _wake_expired_snoozes()
    task = OpsTask.query.get_or_404(task_id)
    if not _can_access_task(task):
        flash('Access denied. You can only view tasks assigned to you.', 'error')
        return redirect(url_for('tasks.list_tasks'))
    snooze_form = OpsTaskSnoozeForm()
    reminder_form = OpsTaskReminderForm()
    recommendation_id = _get_recommendation_id_for_task(task)
    from services.google_calendar_service import is_oauth_configured, quick_add_google_calendar_url

    task_public = url_for("tasks.view_task", task_id=task.id, _external=True)
    gcal_template_url = quick_add_google_calendar_url(task, task_public)
    assignee = task.assignee
    calendar_connected_for_assignee = bool(
        assignee and getattr(assignee, "google_calendar_refresh_token", None)
    )
    return render_template(
        "tasks/view.html",
        task=task,
        snooze_form=snooze_form,
        reminder_form=reminder_form,
        can_manage_tasks=_can_manage_tasks(),
        recommendation_id=recommendation_id,
        gcal_template_url=gcal_template_url,
        gcal_oauth_configured=is_oauth_configured(),
        calendar_connected_for_assignee=calendar_connected_for_assignee,
    )


@tasks_bp.route('/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
@require_advisor_or_manager
@handle_errors
def edit_task(task_id):
    _wake_expired_snoozes()
    task = OpsTask.query.get_or_404(task_id)
    if task.status in ['completed', 'cancelled']:
        flash('Cannot edit a completed or cancelled task.', 'warning')
        return redirect(url_for('tasks.view_task', task_id=task.id))
    form = OpsTaskForm()
    _populate_task_form(form, include_assignee_id=task.assigned_to)
    if request.method == 'GET':
        form.name.data = task.name
        form.deadline.data = task.deadline
        form.assigned_to.data = task.assigned_to or 0
        form.notes.data = task.notes
        form.priority.data = task.priority
        form.status.data = task.status if task.status in ['pending', 'in_progress', 'completed'] else 'pending'
        form.client_id.data = task.client_id or 0
    if form.validate_on_submit():
        task.name = form.name.data
        task.deadline = form.deadline.data
        task.assigned_to = form.assigned_to.data if form.assigned_to.data else None
        task.notes = form.notes.data
        task.priority = form.priority.data
        if form.status.data in ['pending', 'in_progress', 'completed']:
            if form.status.data == 'completed' and task.status != 'completed':
                from services.issue_lifecycle_service import complete_task_and_sync

                complete_task_and_sync(task.id, current_user.id, commit=False)
            elif form.status.data in ['pending', 'in_progress']:
                task.status = form.status.data
        task.client_id = form.client_id.data if form.client_id.data else None
        task.updated_at = datetime.utcnow()
        db.session.commit()
        _sync_google_integrations_for_ops_task(task.id)
        flash('Task updated.', 'success')
        return redirect(url_for('tasks.view_task', task_id=task.id))
    return render_template('tasks/form.html', form=form, task=task)


@tasks_bp.route('/<int:task_id>/snooze', methods=['POST'])
@login_required
@require_tasks_access
@handle_errors
def snooze_task(task_id):
    task = OpsTask.query.get_or_404(task_id)
    if not _can_access_task(task):
        flash('Access denied.', 'error')
        return redirect(url_for('tasks.list_tasks'))
    form = OpsTaskSnoozeForm()
    if form.validate_on_submit():
        if task.status in ['completed', 'cancelled']:
            flash('Cannot snooze a completed or cancelled task.', 'error')
            return redirect(url_for('tasks.view_task', task_id=task_id))
        snoozed_until = form.snoozed_until.data
        if snoozed_until <= datetime.utcnow():
            flash('Snooze until must be in the future.', 'error')
            return redirect(url_for('tasks.view_task', task_id=task_id))
        task.pre_snooze_status = task.status if task.status != 'snoozed' else task.pre_snooze_status or 'pending'
        task.status = 'snoozed'
        task.snoozed_until = snoozed_until
        task.snoozed_at = datetime.utcnow()
        task.snoozed_by = current_user.id
        task.snooze_reason = form.snooze_reason.data
        task.updated_at = datetime.utcnow()
        db.session.commit()
        flash('Task snoozed.', 'success')
    else:
        flash('Invalid snooze request.', 'error')
    return redirect(url_for('tasks.view_task', task_id=task_id))


@tasks_bp.route('/<int:task_id>/unsnooze', methods=['POST'])
@login_required
@require_tasks_access
@handle_errors
def unsnooze_task(task_id):
    task = OpsTask.query.get_or_404(task_id)
    if not _can_access_task(task):
        flash('Access denied.', 'error')
        return redirect(url_for('tasks.list_tasks'))
    if task.status != 'snoozed':
        flash('Task is not snoozed.', 'info')
        return redirect(url_for('tasks.view_task', task_id=task_id))
    task.status = task.pre_snooze_status or 'pending'
    task.pre_snooze_status = None
    task.snoozed_until = None
    task.snoozed_at = None
    task.snoozed_by = None
    task.snooze_reason = None
    task.updated_at = datetime.utcnow()
    db.session.commit()
    flash('Task unsnoozed.', 'success')
    return redirect(url_for('tasks.view_task', task_id=task_id))


@tasks_bp.route('/<int:task_id>/reminder', methods=['POST'])
@login_required
@require_tasks_access
@handle_errors
def set_reminder(task_id):
    task = OpsTask.query.get_or_404(task_id)
    if not _can_access_task(task):
        flash('Access denied.', 'error')
        return redirect(url_for('tasks.list_tasks'))
    form = OpsTaskReminderForm()
    if form.validate_on_submit():
        reminder_at = form.reminder_at.data
        if reminder_at <= datetime.utcnow():
            flash('Reminder must be in the future.', 'error')
            return redirect(url_for('tasks.view_task', task_id=task_id))
        if task.status in ['completed', 'cancelled']:
            flash('Cannot set reminder for completed or cancelled task.', 'error')
            return redirect(url_for('tasks.view_task', task_id=task_id))
        task.reminder_at = reminder_at
        task.reminder_sent = False
        task.updated_at = datetime.utcnow()
        db.session.commit()
        _sync_google_integrations_for_ops_task(task_id)
        flash('Reminder set.', 'success')
    else:
        flash('Invalid reminder request.', 'error')
    return redirect(url_for('tasks.view_task', task_id=task_id))


@tasks_bp.route('/<int:task_id>/complete', methods=['POST'])
@login_required
@require_tasks_access
@handle_errors
def complete_task(task_id):
    task = OpsTask.query.get_or_404(task_id)
    if not _can_access_task(task):
        flash('Access denied.', 'error')
        return redirect(url_for('tasks.list_tasks'))
    if task.status in ['completed', 'cancelled']:
        flash('Task is already closed.', 'info')
        return redirect(url_for('tasks.view_task', task_id=task_id))
    from services.issue_lifecycle_service import complete_task_and_sync

    outcome = complete_task_and_sync(task.id, current_user.id, commit=True)
    issue_resolved = int(outcome.get('issue_resolved', 0) or 0)
    linked_alerts_resolved = int(outcome.get('linked_alerts_resolved', 0) or 0)
    client_stale_resolved = int(outcome.get('client_stale_alerts_resolved', 0) or 0)
    if issue_resolved or linked_alerts_resolved or client_stale_resolved:
        flash(
            f"Task marked complete. Issue resolved={issue_resolved}, "
            f"alerts resolved={linked_alerts_resolved + client_stale_resolved}.",
            'success',
        )
    else:
        flash('Task marked complete.', 'success')
    _sync_google_integrations_for_ops_task(task_id)
    return redirect(url_for('tasks.view_task', task_id=task_id))


@tasks_bp.route('/<int:task_id>/cancel', methods=['POST'])
@login_required
@require_advisor_or_manager
@handle_errors
def cancel_task(task_id):
    task = OpsTask.query.get_or_404(task_id)
    if task.status in ['completed', 'cancelled']:
        flash('Task is already closed.', 'info')
        return redirect(url_for('tasks.view_task', task_id=task_id))
    task.status = 'cancelled'
    task.reminder_at = None
    task.reminder_sent = True
    task.updated_at = datetime.utcnow()
    db.session.commit()
    _sync_google_integrations_for_ops_task(task_id)
    flash('Task cancelled.', 'success')
    return redirect(url_for('tasks.view_task', task_id=task_id))
