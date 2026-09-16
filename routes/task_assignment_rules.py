"""
Task Assignment Rules - Admin UI
================================
CRUD for TaskAssignmentRule. Admin only.
"""

import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from functools import wraps

from extensions import db
from models import TaskAssignmentRule, User
from services.task_assignment_service import invalidate_rules_cache

logger = logging.getLogger(__name__)

task_assignment_rules_bp = Blueprint('task_assignment_rules', __name__, url_prefix='/admin/task-assignment-rules')


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


def handle_errors(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}")
            flash(f'An error occurred: {str(e)}', 'error')
            return redirect(url_for('task_assignment_rules.list_rules'))
    return decorated


# Rule type options and match values for dropdowns
WORKFLOW_STAGES = ['FUNDS', 'RECOS', 'NOTIFY', 'EXEC', 'UPDATE', 'COMPLETED']
REVIEW_STATUSES = ['initiated', 'sent', 'meeting', 'closed']
ISSUE_CATEGORIES = [
    'RECOMMENDATION_EXECUTION',
    'RECOMMENDATION_MATCH',
    'PORTFOLIO_PERFORMANCE',
    'CASHFLOW',
    'HOLDINGS',
    'DUPLICATES',
    'DATE_INTEGRITY',
    'NEGATIVE_VALUES',
    'CORPORATE_ACTION',
]


@task_assignment_rules_bp.route('/')
@login_required
@admin_required
@handle_errors
def list_rules():
    """List all task assignment rules."""
    rules = TaskAssignmentRule.query.order_by(
        TaskAssignmentRule.priority.desc(),
        TaskAssignmentRule.rule_type,
        TaskAssignmentRule.match_value
    ).all()
    users = User.query.filter_by(is_active=True).order_by(User.username).all()
    return render_template(
        'admin/task_assignment_rules.html',
        rules=rules,
        users=users,
        workflow_stages=WORKFLOW_STAGES,
        review_statuses=REVIEW_STATUSES,
        issue_categories=ISSUE_CATEGORIES,
    )


@task_assignment_rules_bp.route('/create', methods=['POST'])
@login_required
@admin_required
@handle_errors
def create_rule():
    """Create a new rule."""
    rule_type = request.form.get('rule_type', '').strip()
    match_value = request.form.get('match_value', '').strip()
    assigned_user_id = request.form.get('assigned_user_id', type=int)
    priority = request.form.get('priority', type=int, default=0)

    if not rule_type or not match_value:
        flash('Rule type and match value are required.', 'error')
        return redirect(url_for('task_assignment_rules.list_rules'))

    if not assigned_user_id:
        flash('Please select a user to assign.', 'error')
        return redirect(url_for('task_assignment_rules.list_rules'))

    user = User.query.get(assigned_user_id)
    if not user:
        flash('Selected user not found.', 'error')
        return redirect(url_for('task_assignment_rules.list_rules'))

    # Check for duplicate
    existing = TaskAssignmentRule.query.filter_by(
        rule_type=rule_type,
        match_value=match_value,
        is_active=True
    ).first()
    if existing:
        flash(f'A rule for {rule_type}={match_value} already exists.', 'warning')
        return redirect(url_for('task_assignment_rules.list_rules'))

    rule = TaskAssignmentRule(
        rule_type=rule_type,
        match_value=match_value,
        assigned_user_id=assigned_user_id,
        priority=priority,
        is_active=True,
    )
    db.session.add(rule)
    db.session.commit()
    invalidate_rules_cache()

    flash(f'Rule created: {rule_type}={match_value} → {user.username}', 'success')
    return redirect(url_for('task_assignment_rules.list_rules'))


@task_assignment_rules_bp.route('/<int:rule_id>/edit', methods=['POST'])
@login_required
@admin_required
@handle_errors
def edit_rule(rule_id):
    """Edit an existing rule."""
    rule = TaskAssignmentRule.query.get_or_404(rule_id)
    assigned_user_id = request.form.get('assigned_user_id', type=int)
    priority = request.form.get('priority', type=int, default=0)
    is_active = request.form.get('is_active') == 'on'  # checkbox: present when checked

    if assigned_user_id:
        user = User.query.get(assigned_user_id)
        if user:
            rule.assigned_user_id = assigned_user_id
    rule.priority = priority
    rule.is_active = is_active

    db.session.commit()
    invalidate_rules_cache()

    flash('Rule updated.', 'success')
    return redirect(url_for('task_assignment_rules.list_rules'))


@task_assignment_rules_bp.route('/<int:rule_id>/delete', methods=['POST'])
@login_required
@admin_required
@handle_errors
def delete_rule(rule_id):
    """Delete a rule."""
    rule = TaskAssignmentRule.query.get_or_404(rule_id)
    db.session.delete(rule)
    db.session.commit()
    invalidate_rules_cache()

    flash('Rule deleted.', 'success')
    return redirect(url_for('task_assignment_rules.list_rules'))


@task_assignment_rules_bp.route('/run-backfill', methods=['POST'])
@login_required
@admin_required
@handle_errors
def run_backfill():
    """Run TaskAssignmentAgent backfill to assign unassigned issues and review workflows."""
    from agents.task_assignment_agent import TaskAssignmentAgent
    agent = TaskAssignmentAgent()
    updated, tasks_created = agent.run_backfill()
    parts = []
    if updated > 0:
        parts.append(f'{updated} issues assigned')
    if tasks_created > 0:
        parts.append(f'{tasks_created} tasks created in Task Management (/tasks)')
    if parts:
        flash(f'Backfill completed: {"; ".join(parts)}.', 'success')
    else:
        flash('Backfill completed: All open issues and review workflows already have assignees.', 'info')
    return redirect(url_for('task_assignment_rules.list_rules'))
