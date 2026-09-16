"""
Data Integrity Dashboard Routes
================================
UI pages for viewing and managing data integrity issues.
"""

import logging
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.internal_next import safe_referrer_path
from utils.internal_next import safe_referrer_path
from sqlalchemy import func, desc
import hashlib

from extensions import db
from models import (
    DataIntegrityIssue, DataIntegrityException, ClientBehaviorPattern,
    AgentRun, AgentFeedback, Client, User
)

# Import Alert from alert_system_models
try:
    from alert_system_models import Alert
    ALERT_AVAILABLE = True
except ImportError:
    ALERT_AVAILABLE = False
    Alert = None

logger = logging.getLogger(__name__)

data_integrity_bp = Blueprint('data_integrity', __name__, url_prefix='/data-integrity')
data_integrity_bp.strict_slashes = False


@data_integrity_bp.before_request
def _data_integrity_enforce_client_access():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


def _generate_exception_hash(client_id: int, check_category: str, check_name: str, **identifiers) -> str:
    """
    Must match BaseAgent._generate_exception_hash behavior (sorted keys, sha256).
    Use this for UI-driven exceptions so agent skip checks work reliably.
    """
    parts = [str(client_id), str(check_category), str(check_name)]
    for key in sorted(identifiers.keys()):
        val = identifiers.get(key)
        if val is not None:
            parts.append(f"{key}:{val}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _pattern_type_for_issue(issue: DataIntegrityIssue) -> str:
    """
    Map issue.check_name to the pattern_type that the agent actually reads.
    Keep DIM overrides rare and explicit.
    """
    if issue.check_category == 'RECOMMENDATION_MATCH':
        if issue.check_name == 'trade_execution_mismatch':
            d = issue.details or {}
            if isinstance(d.get('quantity'), dict):
                return 'quantity_multiplier'
            if isinstance(d.get('price'), dict):
                return 'price_tolerance'
            return 'quantity_multiplier'
        mapping = {
            'quantity_mismatch': 'quantity_multiplier',
            'price_mismatch': 'price_tolerance',
            'unexecuted_recommendation': 'execution_delay',
            'unexecuted_recommendations_batch': 'execution_delay',
            'unrecorded_superseded_session': 'execution_delay',
        }
        return mapping.get(issue.check_name, issue.check_name)
    return issue.check_name


def _default_pattern_value(issue: DataIntegrityIssue, pattern_type: str) -> dict:
    """
    Best-effort structured defaults derived from issue.details.
    """
    details = dict(issue.details or {})
    if issue.check_name == 'trade_execution_mismatch':
        qd = details.get('quantity')
        pd = details.get('price')
        if pattern_type == 'quantity_multiplier' and isinstance(qd, dict):
            details = {**details, **qd}
        elif pattern_type == 'price_tolerance' and isinstance(pd, dict):
            details = {**details, **pd}
    if pattern_type == 'quantity_multiplier':
        expected = details.get('expected_qty') or details.get('expected_with_multiplier') or 1
        actual = details.get('actual_qty') or details.get('txn_qty') or expected
        try:
            expected = float(expected)
            actual = float(actual)
        except Exception:
            expected, actual = 1.0, 1.0
        multiplier = round(actual / expected, 2) if expected else 1.0
        return {'multiplier': multiplier, 'tolerance': 0.20}
    if pattern_type == 'price_tolerance':
        variance = details.get('variance_pct')
        try:
            variance = float(variance) if variance is not None else 5.0
        except Exception:
            variance = 5.0
        # Add a small buffer to reduce noise
        return {'tolerance_pct': round(max(variance, 5.0) + 2.0, 1)}
    if pattern_type == 'execution_delay':
        return {'typical_delay_days': 30}
    return {}


# =============================================================================
# DASHBOARD
# =============================================================================

@data_integrity_bp.route('/by-client')
@login_required
def data_price_by_client():
    """
    Client Health L7: data integrity + price issues grouped by client.
    Close within 2 weeks (guidelines). Prefer nightly observation pack.
    """
    from access_control import get_accessible_clients
    from services.client_health_dashboard_service import (
        CLOSE_WITHIN_DAYS,
        get_data_price_source_meta,
        group_data_price_by_client,
    )

    accessible = get_accessible_clients()
    ids = {c.id for c in accessible} if accessible is not None else None
    rows = group_data_price_by_client(accessible_client_ids=ids)
    source_meta = get_data_price_source_meta()
    return render_template(
        "data_integrity/by_client.html",
        rows=rows,
        close_within_days=CLOSE_WITHIN_DAYS,
        source_meta=source_meta,
    )


@data_integrity_bp.route('/')
@login_required
def dashboard():
    """Main data integrity dashboard."""
    
    # Get summary counts
    severity_counts = db.session.query(
        DataIntegrityIssue.severity,
        func.count(DataIntegrityIssue.id)
    ).filter(
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(DataIntegrityIssue.severity).all()
    
    severity_summary = {
        'critical': 0,
        'warning': 0,
        'info': 0
    }
    for severity, count in severity_counts:
        severity_summary[severity] = count
    
    total_open = sum(severity_summary.values())
    
    # Get resolved count (last 30 days)
    from datetime import timedelta
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    resolved_count = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.status == 'resolved',
        DataIntegrityIssue.resolved_at >= thirty_days_ago
    ).count()
    
    # Get issues grouped by category
    issue_count_col = func.count(DataIntegrityIssue.id).label('issue_count')
    category_summary = db.session.query(
        DataIntegrityIssue.check_category,
        DataIntegrityIssue.check_name,
        DataIntegrityIssue.severity,
        issue_count_col,
        func.count(func.distinct(DataIntegrityIssue.client_id)).label('client_count')
    ).filter(
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(
        DataIntegrityIssue.check_category,
        DataIntegrityIssue.check_name,
        DataIntegrityIssue.severity
    ).order_by(
        desc(issue_count_col)
    ).all()
    
    # Group by category for display
    categories = {}
    for row in category_summary:
        cat = row.check_category
        if cat not in categories:
            categories[cat] = {
                'name': cat,
                'checks': [],
                'total_issues': 0,
                'total_clients': 0,
                'max_severity': 'info'
            }
        categories[cat]['checks'].append({
            'name': row.check_name,
            'severity': row.severity,
            'issue_count': row.issue_count,
            'client_count': row.client_count
        })
        categories[cat]['total_issues'] += row.issue_count
        # Track max severity
        if row.severity == 'critical':
            categories[cat]['max_severity'] = 'critical'
        elif row.severity == 'warning' and categories[cat]['max_severity'] != 'critical':
            categories[cat]['max_severity'] = 'warning'
    
    # Calculate unique client count per category
    for cat_name in categories:
        client_count = db.session.query(
            func.count(func.distinct(DataIntegrityIssue.client_id))
        ).filter(
            DataIntegrityIssue.check_category == cat_name,
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ).scalar()
        categories[cat_name]['total_clients'] = client_count or 0
    
    # Get last run
    last_run = AgentRun.query.filter_by(
        agent_name='data_integrity_manager'
    ).order_by(desc(AgentRun.started_at)).first()
    
    # Get recent runs
    recent_runs = AgentRun.query.filter_by(
        agent_name='data_integrity_manager'
    ).order_by(desc(AgentRun.started_at)).limit(5).all()
    
    # Get recent feedback
    recent_feedback = AgentFeedback.query.order_by(
        desc(AgentFeedback.created_at)
    ).limit(5).all()
    
    return render_template('data_integrity/dashboard.html',
        severity_summary=severity_summary,
        total_open=total_open,
        resolved_count=resolved_count,
        categories=categories,
        last_run=last_run,
        recent_runs=recent_runs,
        recent_feedback=recent_feedback
    )


# =============================================================================
# CATEGORY DETAIL
# =============================================================================

@data_integrity_bp.route('/category/<category>')
@login_required
def category_detail(category):
    """View issues for a specific category."""
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    client_filter = request.args.get('client_id', type=int)
    
    # Get issues grouped by client
    issue_count = func.count(DataIntegrityIssue.id).label('issue_count')
    query = db.session.query(
        DataIntegrityIssue.client_id,
        Client.name.label('client_name'),
        issue_count,
        func.max(DataIntegrityIssue.severity).label('max_severity')
    ).join(Client).filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(
        DataIntegrityIssue.client_id,
        Client.name
    ).order_by(
        desc(issue_count),
        Client.name
    )
    
    if client_filter:
        query = query.filter(DataIntegrityIssue.client_id == client_filter)
    
    # Paginate
    total = query.count()
    clients_with_issues = query.offset((page - 1) * per_page).limit(per_page).all()
    
    # Get detailed issues for each client (first 5 per client for preview)
    client_issues = {}
    for row in clients_with_issues:
        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == row.client_id,
            DataIntegrityIssue.check_category == category,
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ).order_by(desc(DataIntegrityIssue.detected_at)).limit(5).all()
        client_issues[row.client_id] = issues
    
    # Get category summary
    total_issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).count()
    
    total_clients = db.session.query(
        func.count(func.distinct(DataIntegrityIssue.client_id))
    ).filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).scalar()
    
    # Get all clients for filter dropdown
    from access_control import get_accessible_clients_ordered
    all_clients = get_accessible_clients_ordered()
    
    return render_template('data_integrity/category_detail.html',
        category=category,
        clients_with_issues=clients_with_issues,
        client_issues=client_issues,
        total_issues=total_issues,
        total_clients=total_clients,
        all_clients=all_clients,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        client_filter=client_filter
    )


@data_integrity_bp.route('/client/<int:client_id>')
@login_required
def client_issues(client_id):
    """View all issues for a specific client."""
    
    client = Client.query.get_or_404(client_id)
    
    # Get issues grouped by category
    issues_by_category = {}
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.client_id == client_id,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).order_by(
        DataIntegrityIssue.check_category,
        desc(DataIntegrityIssue.detected_at)
    ).all()
    
    for issue in issues:
        cat = issue.check_category
        if cat not in issues_by_category:
            issues_by_category[cat] = []
        issues_by_category[cat].append(issue)
    
    # Get client patterns
    patterns = ClientBehaviorPattern.query.filter_by(client_id=client_id).all()

    # Overrides summary for quick visibility
    pattern_count = len(patterns)
    exception_count = DataIntegrityException.query.filter_by(client_id=client_id).count()
    has_overrides = (pattern_count > 0) or (exception_count > 0)
    
    from services.task_assignment_service import list_assignable_users

    assignee_users = list_assignable_users()
    return render_template('data_integrity/client_issues.html',
        client=client,
        issues_by_category=issues_by_category,
        patterns=patterns,
        total_issues=len(issues),
        pattern_count=pattern_count,
        exception_count=exception_count,
        has_overrides=has_overrides,
        assignee_users=assignee_users,
        assignee_user_ids={u.id for u in assignee_users},
    )


# =============================================================================
# ACTIONS
# =============================================================================

@data_integrity_bp.route('/reassign', methods=['POST'])
@login_required
def reassign_issues():
    """Reassign one or more open issues to another user (or unassign)."""
    from services.task_assignment_service import reassign_issues as do_reassign

    issue_ids = request.form.getlist('issue_ids[]') or request.form.getlist('issue_ids')
    raw_assignee = (request.form.get('assigned_to') or '').strip()
    next_url = safe_referrer_path(request.referrer) or url_for('data_integrity.dashboard')

    if not issue_ids:
        flash('No issues selected', 'error')
        return redirect(next_url)

    assignee_user_id = None
    if raw_assignee and raw_assignee not in ('0', 'unassigned'):
        try:
            assignee_user_id = int(raw_assignee)
        except (TypeError, ValueError):
            flash('Invalid assignee', 'error')
            return redirect(next_url)

    try:
        count = do_reassign(
            [int(i) for i in issue_ids],
            assignee_user_id,
            by_user_id=current_user.id,
        )
        db.session.commit()
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'error')
        return redirect(next_url)
    except Exception as e:
        db.session.rollback()
        logger.exception('Issue reassignment failed: %s', e)
        flash('Could not reassign issues', 'error')
        return redirect(next_url)

    if assignee_user_id:
        user = User.query.get(assignee_user_id)
        label = user.username if user else f'user #{assignee_user_id}'
        flash(f'Reassigned {count} issue(s) to {label}', 'success')
    else:
        flash(f'Unassigned {count} issue(s)', 'success')
    return redirect(next_url)


@data_integrity_bp.route('/resolve', methods=['POST'])
@login_required
def resolve_issues():
    """Resolve one or more issues."""
    
    issue_ids = request.form.getlist('issue_ids[]') or request.form.getlist('issue_ids')
    resolution = request.form.get('resolution', 'fixed')
    note = request.form.get('note', '')
    
    if not issue_ids:
        flash('No issues selected', 'error')
        return redirect(safe_referrer_path(request.referrer) or url_for('data_integrity.dashboard'))
    
    resolved_count = 0
    for issue_id in issue_ids:
        try:
            issue = DataIntegrityIssue.query.get(int(issue_id))
            if issue and issue.status in ['open', 'baseline']:
                issue.status = 'resolved'
                issue.resolution_type = resolution
                issue.resolved_at = datetime.utcnow()
                issue.resolved_by = current_user.id
                issue.resolution_notes = note
                resolved_count += 1
        except (ValueError, TypeError):
            continue
    
    db.session.commit()
    flash(f'Resolved {resolved_count} issues', 'success')
    
    return redirect(safe_referrer_path(request.referrer) or url_for('data_integrity.dashboard'))


@data_integrity_bp.route('/bulk-resolve/<category>', methods=['POST'])
@login_required
def bulk_resolve_category(category):
    """Resolve all issues in a category."""
    
    resolution = request.form.get('resolution', 'fixed')
    note = request.form.get('note', '')
    
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).all()
    
    for issue in issues:
        issue.status = 'resolved'
        issue.resolution_type = resolution
        issue.resolved_at = datetime.utcnow()
        issue.resolved_by = current_user.id
        issue.resolution_notes = note
    
    db.session.commit()
    flash(f'Resolved {len(issues)} issues in {category}', 'success')
    
    return redirect(url_for('data_integrity.dashboard'))


@data_integrity_bp.route('/create-alert/<category>', methods=['POST'])
@login_required
def create_alert_from_category(category):
    """Create an alert from a category of issues."""
    
    title = request.form.get('title', f'Data Integrity: {category}')
    assign_to = request.form.get('assign_to', type=int)
    priority = request.form.get('priority', 'high')
    
    # Get summary of issues
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).all()
    
    # Group by client
    clients_affected = {}
    for issue in issues:
        if issue.client_id not in clients_affected:
            clients_affected[issue.client_id] = {
                'name': issue.client.name if issue.client else f'Client {issue.client_id}',
                'count': 0,
                'issues': []
            }
        clients_affected[issue.client_id]['count'] += 1
        if len(clients_affected[issue.client_id]['issues']) < 3:
            clients_affected[issue.client_id]['issues'].append(issue.message[:50])
    
    # Build alert message
    message_parts = [f"Data integrity issues detected in category: {category}\n"]
    message_parts.append(f"Total issues: {len(issues)}")
    message_parts.append(f"Clients affected: {len(clients_affected)}\n")
    message_parts.append("Affected clients:")
    
    for client_id, info in sorted(clients_affected.items(), key=lambda x: -x[1]['count'])[:10]:
        message_parts.append(f"  • {info['name']}: {info['count']} issues")
    
    if len(clients_affected) > 10:
        message_parts.append(f"  ... and {len(clients_affected) - 10} more clients")
    
    message = "\n".join(message_parts)
    
    # Create alert
    if not ALERT_AVAILABLE:
        # Alert model not available - just show a message
        flash(f'Alert system not configured. Summary: {len(issues)} issues affecting {len(clients_affected)} clients in {category}', 'warning')
        return redirect(url_for('data_integrity.dashboard'))
    
    try:
        from services.alert_creation_policy import guard_alert_creation
        if not guard_alert_creation("routes.data_integrity.create_alert"):
            flash('Alert creation is frozen while Client Health scenarios are defined.', 'info')
            return redirect(url_for('data_integrity.dashboard'))

        alert = Alert(
            title=title,
            description=message,
            alert_type='data_integrity',
            alert_subtype=category.lower(),
            severity=priority if priority in ['critical', 'warning', 'info'] else 'warning',
            status='active',
            user_id=assign_to or current_user.id,
            created_at=datetime.utcnow()
        )
        db.session.add(alert)
        db.session.commit()
        
        flash(f'Alert created: {title}', 'success')
    except Exception as e:
        logger.error(f"Error creating alert: {e}")
        flash(f'Error creating alert: {str(e)}', 'error')
    
    return redirect(url_for('data_integrity.dashboard'))


@data_integrity_bp.route('/rerun', methods=['POST'])
@login_required
def rerun_agent():
    """Unified integrity refresh (incremental / full) — all checks for each client in scope."""
    from access_control import get_accessible_clients
    from services.client_integrity_refresh_service import (
        format_refresh_flash,
        refresh_integrity_scope,
    )

    mode = (request.form.get('mode') or 'incremental').strip().lower()
    if mode == 'baseline':
        # Baseline remains agent-only (no alerts/heal) for setup; not the unified path
        try:
            from agents import DataIntegrityManager
            run_id = DataIntegrityManager().run_baseline(user_id=current_user.id)
            flash(f'Baseline agent run started: {run_id}', 'success')
        except Exception as e:
            logger.error(f"Error running baseline: {e}", exc_info=True)
            flash(f'Error running agent: {str(e)}', 'error')
        return redirect(url_for('data_integrity.dashboard'))

    scope_mode = 'full' if mode in ('full', 'full_audit') else 'incremental'
    accessible = get_accessible_clients()
    accessible_ids = None if accessible is None else {c.id for c in accessible}
    try:
        result = refresh_integrity_scope(
            mode=scope_mode,
            accessible_client_ids=accessible_ids,
            actor_user_id=current_user.id,
        )
        flash(format_refresh_flash(result), 'success' if result.get('ok') else 'warning')
    except Exception as e:
        logger.error(f"Error running unified integrity refresh: {e}", exc_info=True)
        flash(f'Error refreshing integrity: {str(e)}', 'error')

    return redirect(url_for('data_integrity.dashboard'))


@data_integrity_bp.route('/refresh-book', methods=['POST'])
@login_required
def refresh_book():
    """Refresh integrity for the current user's accessible client book."""
    from access_control import get_accessible_clients
    from services.client_integrity_refresh_service import (
        format_refresh_flash,
        refresh_integrity_scope,
    )

    accessible = get_accessible_clients()
    accessible_ids = set() if accessible is None else {c.id for c in accessible}
    # None from get_accessible_clients means all clients — use full mode
    mode = 'full' if accessible is None else 'book'
    try:
        result = refresh_integrity_scope(
            mode=mode,
            accessible_client_ids=None if accessible is None else accessible_ids,
            actor_user_id=current_user.id,
        )
        flash(format_refresh_flash(result), 'success' if result.get('ok') else 'warning')
    except Exception as e:
        logger.error(f"Error refreshing integrity book: {e}", exc_info=True)
        flash(f'Error refreshing integrity: {str(e)}', 'error')

    nxt = request.form.get('next') or request.args.get('next')
    if nxt and str(nxt).startswith('/'):
        return redirect(nxt)
    return redirect(url_for('data_integrity.data_price_by_client'))


@data_integrity_bp.route('/client/<int:client_id>/refresh', methods=['POST'])
@login_required
def refresh_client(client_id):
    """Per-client unified integrity refresh (issues, healer, alerts, snapshots)."""
    from access_control import can_access_client
    from services.client_integrity_refresh_service import (
        format_refresh_flash,
        refresh_integrity_scope,
    )

    if not can_access_client(client_id):
        flash('Access denied.', 'error')
        return redirect(url_for('data_integrity.dashboard'))

    try:
        result = refresh_integrity_scope(
            mode='client',
            client_id=client_id,
            actor_user_id=current_user.id,
        )
        flash(format_refresh_flash(result), 'success' if result.get('ok') else 'warning')
    except Exception as e:
        logger.error(f"Error refreshing client {client_id}: {e}", exc_info=True)
        flash(f'Error refreshing integrity: {str(e)}', 'error')

    nxt = request.form.get('next') or request.args.get('next')
    if nxt and str(nxt).startswith('/'):
        return redirect(nxt)
    return redirect(url_for('data_integrity.client_issues', client_id=client_id))


@data_integrity_bp.route('/locate-recommendation/<int:recommendation_id>')
@login_required
def locate_recommendation(recommendation_id):
    """Redirect to the recommendation so users can find it by ID (e.g. from RECOMMENDATION_MATCH alerts)."""
    return redirect(url_for('unified_recommendations.edit_recommendation', recommendation_id=recommendation_id))


# =============================================================================
# FEEDBACK
# =============================================================================

@data_integrity_bp.route('/feedback')
@login_required
def feedback_list():
    """View all feedback."""
    
    status_filter = request.args.get('status', 'all')
    
    query = AgentFeedback.query.order_by(desc(AgentFeedback.created_at))
    
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    feedback_items = query.all()
    
    return render_template('data_integrity/feedback.html',
        feedback_items=feedback_items,
        status_filter=status_filter
    )


@data_integrity_bp.route('/feedback/add', methods=['GET', 'POST'])
@login_required
def add_feedback():
    """Add new feedback."""
    
    if request.method == 'POST':
        feedback = AgentFeedback(
            category=request.form.get('category', 'other'),
            related_check=request.form.get('related_check'),
            title=request.form.get('title'),
            description=request.form.get('description'),
            created_by=current_user.id
        )
        db.session.add(feedback)
        db.session.commit()
        
        flash('Feedback submitted successfully', 'success')
        return redirect(url_for('data_integrity.dashboard'))
    
    # Get categories for dropdown
    categories = db.session.query(
        DataIntegrityIssue.check_category
    ).distinct().all()
    
    return render_template('data_integrity/feedback_form.html',
        categories=[c[0] for c in categories]
    )


@data_integrity_bp.route('/feedback/<int:feedback_id>/respond', methods=['POST'])
@login_required
def respond_to_feedback(feedback_id):
    """Respond to feedback."""
    
    feedback = AgentFeedback.query.get_or_404(feedback_id)
    
    feedback.status = request.form.get('status', 'addressed')
    feedback.response = request.form.get('response')
    feedback.addressed_at = datetime.utcnow()
    feedback.addressed_by = current_user.id
    
    db.session.commit()
    
    flash('Feedback updated', 'success')
    return redirect(url_for('data_integrity.feedback_list'))


@data_integrity_bp.route('/issue-feedback', methods=['POST'])
@login_required
def handle_issue_feedback():
    """Handle user feedback on a specific issue - exception or pattern."""
    
    try:
        issue_id = request.form.get('issue_id', type=int)
        action = request.form.get('action')
        notes = request.form.get('notes', '')
        
        issue = DataIntegrityIssue.query.get_or_404(issue_id)
        
        if action == 'fixed':
            # Mark as fixed
            issue.status = 'resolved'
            issue.resolution_type = 'fixed'
            issue.resolved_at = datetime.utcnow()
            issue.resolved_by = current_user.id
            issue.resolution_notes = notes
            
        elif action == 'exception':
            # Create one-time exception
            exc_hash = _generate_exception_hash(
                issue.client_id,
                issue.check_category,
                issue.check_name,
                transaction_id=issue.transaction_id,
                recommendation_id=issue.recommendation_id,
                cashflow_id=issue.cashflow_id,
                security_id=issue.security_id,
                reference_date=str(issue.reference_date) if issue.reference_date else None,
            )
            existing_exc = DataIntegrityException.query.filter_by(exception_hash=exc_hash).first()
            if not existing_exc:
                exception = DataIntegrityException(
                    client_id=issue.client_id,
                    check_category=issue.check_category,
                    check_name=issue.check_name,
                    transaction_id=issue.transaction_id,
                    recommendation_id=issue.recommendation_id,
                    cashflow_id=issue.cashflow_id,
                    security_id=issue.security_id,
                    reference_date=issue.reference_date,
                    exception_hash=exc_hash,
                    reason=notes or 'One-time exception',
                    original_issue_id=issue.id,
                    created_by=current_user.id
                )
                db.session.add(exception)
            
            issue.status = 'resolved'
            issue.resolution_type = 'exception'
            issue.resolved_at = datetime.utcnow()
            issue.resolved_by = current_user.id
            issue.resolution_notes = notes
            
        elif action == 'pattern':
            # Create or update client behavior pattern
            pattern_desc = request.form.get('pattern_description', '')
            pattern_type = _pattern_type_for_issue(issue)
            pattern_value = _default_pattern_value(issue, pattern_type)
            if pattern_desc:
                # Store human explanation in the pattern itself as well
                pattern_value = {**pattern_value, 'description': pattern_desc}
            
            # Check if pattern already exists (client+category+pattern_type+security scope)
            existing = ClientBehaviorPattern.query.filter_by(
                client_id=issue.client_id,
                check_category=issue.check_category,
                pattern_type=pattern_type,
                security_id=issue.security_id
            ).first()
            
            if existing:
                existing.occurrences += 1
                existing.pattern_value = pattern_value or existing.pattern_value
                existing.notes = notes or pattern_desc or existing.notes
                existing.last_used_at = datetime.utcnow()
                existing.confidence = max(existing.confidence or 0.0, 0.9)
            else:
                pattern = ClientBehaviorPattern(
                    client_id=issue.client_id,
                    check_category=issue.check_category,
                    pattern_type=pattern_type,
                    applies_to='security_specific' if issue.security_id else 'all',
                    security_id=issue.security_id,
                    pattern_value=pattern_value or {'description': pattern_desc, 'learned_from': issue.message},
                    notes=notes or pattern_desc,
                    learned_from_issue_id=issue.id,
                    confidence=0.9,
                    last_used_at=datetime.utcnow(),
                    created_by=current_user.id
                )
                db.session.add(pattern)
            
            issue.status = 'resolved'
            issue.resolution_type = 'pattern'
            issue.resolved_at = datetime.utcnow()
            issue.resolved_by = current_user.id
            issue.resolution_notes = f"Pattern created: {pattern_desc}"
            
        elif action == 'false_positive':
            # Mark as false positive
            issue.status = 'resolved'
            issue.resolution_type = 'false_positive'
            issue.resolved_at = datetime.utcnow()
            issue.resolved_by = current_user.id
            issue.resolution_notes = notes
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Issue resolved as {action}'})
        
    except Exception as e:
        logger.error(f"Error handling issue feedback: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@data_integrity_bp.route('/client/<int:client_id>/cashflow/ignore-dummy-dates', methods=['POST'])
@login_required
def ignore_cashflow_dummy_trade_dates(client_id: int):
    """
    Create/update a client-level override for legacy dummy trade dates (e.g. 2000-01-01)
    so month-wise CASHFLOW reconciliation doesn't generate noise.
    """
    client = Client.query.get_or_404(client_id)
    dummy_date = request.form.get('dummy_date') or '2000-01-01'
    note = request.form.get('note') or f'Legacy dummy trade dates present; suppress month-wise cashflow reconciliation. ({dummy_date})'

    existing = ClientBehaviorPattern.query.filter_by(
        client_id=client_id,
        check_category='CASHFLOW',
        pattern_type='ignore_dummy_trade_dates',
        security_id=None
    ).first()

    pattern_value = {
        'dummy_dates': [dummy_date],
        'mode': 'suppress_monthly_recon'
    }

    if existing:
        pv = existing.pattern_value if isinstance(existing.pattern_value, dict) else {}
        dates = set(str(d) for d in (pv.get('dummy_dates') or []))
        dates.add(dummy_date)
        pv.update({'dummy_dates': sorted(dates), 'mode': 'suppress_monthly_recon'})
        existing.pattern_value = pv
        existing.confidence = max(existing.confidence or 0.0, 0.9)
        existing.last_used_at = datetime.utcnow()
        existing.notes = note
    else:
        p = ClientBehaviorPattern(
            client_id=client_id,
            check_category='CASHFLOW',
            pattern_type='ignore_dummy_trade_dates',
            applies_to='all',
            security_id=None,
            pattern_value=pattern_value,
            occurrences=1,
            confidence=0.9,
            last_used_at=datetime.utcnow(),
            notes=note,
            created_by=current_user.id
        )
        db.session.add(p)

    db.session.commit()
    flash(f'Enabled dummy trade-date suppression for CASHFLOW checks for {client.name}.', 'success')
    return redirect(safe_referrer_path(request.referrer) or url_for('data_integrity.client_issues', client_id=client_id))
