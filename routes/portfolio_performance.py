"""
Portfolio Performance Dashboard Routes
=======================================
UI for viewing and managing portfolio-vs-benchmark underperformance issues.
Same pattern as data integrity: dashboard, category detail, client issues, resolve.
"""

import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.internal_next import safe_referrer_path
from sqlalchemy import func, desc

from extensions import db
from models import DataIntegrityIssue, AgentRun, AgentFeedback, Client

logger = logging.getLogger(__name__)

portfolio_performance_bp = Blueprint(
    'portfolio_performance',
    __name__,
    url_prefix='/portfolio-performance'
)
portfolio_performance_bp.strict_slashes = False


@portfolio_performance_bp.before_request
def _enforce_portfolio_performance_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


CATEGORY = 'PORTFOLIO_PERFORMANCE'


@portfolio_performance_bp.route('/')
@login_required
def dashboard():
    """Main portfolio performance dashboard (underperformance vs benchmark)."""

    severity_counts = db.session.query(
        DataIntegrityIssue.severity,
        func.count(DataIntegrityIssue.id)
    ).filter(
        DataIntegrityIssue.check_category == CATEGORY,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(DataIntegrityIssue.severity).all()

    severity_summary = {'critical': 0, 'warning': 0, 'info': 0}
    for severity, count in severity_counts:
        severity_summary[severity] = count

    total_open = sum(severity_summary.values())

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    resolved_count = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == CATEGORY,
        DataIntegrityIssue.status == 'resolved',
        DataIntegrityIssue.resolved_at >= thirty_days_ago
    ).count()

    issue_count_col = func.count(DataIntegrityIssue.id).label('issue_count')
    category_summary = db.session.query(
        DataIntegrityIssue.check_category,
        DataIntegrityIssue.check_name,
        DataIntegrityIssue.severity,
        issue_count_col,
        func.count(func.distinct(DataIntegrityIssue.client_id)).label('client_count')
    ).filter(
        DataIntegrityIssue.check_category == CATEGORY,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(
        DataIntegrityIssue.check_category,
        DataIntegrityIssue.check_name,
        DataIntegrityIssue.severity
    ).order_by(desc(issue_count_col)).all()

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
        if row.severity == 'critical':
            categories[cat]['max_severity'] = 'critical'
        elif row.severity == 'warning' and categories[cat]['max_severity'] != 'critical':
            categories[cat]['max_severity'] = 'warning'

    for cat_name in categories:
        client_count = db.session.query(
            func.count(func.distinct(DataIntegrityIssue.client_id))
        ).filter(
            DataIntegrityIssue.check_category == cat_name,
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ).scalar()
        categories[cat_name]['total_clients'] = client_count or 0

    last_run = AgentRun.query.filter_by(
        agent_name='portfolio_performance_monitor'
    ).order_by(desc(AgentRun.started_at)).first()

    recent_runs = AgentRun.query.filter_by(
        agent_name='portfolio_performance_monitor'
    ).order_by(desc(AgentRun.started_at)).limit(5).all()

    return render_template(
        'portfolio_performance/dashboard.html',
        severity_summary=severity_summary,
        total_open=total_open,
        resolved_count=resolved_count,
        categories=categories,
        last_run=last_run,
        recent_runs=recent_runs,
    )


@portfolio_performance_bp.route('/category/<category>')
@login_required
def category_detail(category):
    """Issues for a given category (e.g. PORTFOLIO_PERFORMANCE)."""
    if category != CATEGORY:
        flash('Invalid category', 'error')
        return redirect(url_for('portfolio_performance.dashboard'))

    client_filter = request.args.get('client_id', type=int)

    issue_count = func.count(DataIntegrityIssue.id).label('issue_count')
    query = db.session.query(
        DataIntegrityIssue.client_id,
        Client.name.label('client_name'),
        issue_count,
        func.max(DataIntegrityIssue.severity).label('max_severity')
    ).join(Client, DataIntegrityIssue.client_id == Client.id).filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(
        DataIntegrityIssue.client_id,
        Client.name
    )

    if client_filter:
        query = query.filter(DataIntegrityIssue.client_id == client_filter)

    clients_with_issues = query.order_by(desc(issue_count)).all()

    client_issues = {}
    for row in clients_with_issues:
        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == row.client_id,
            DataIntegrityIssue.check_category == category,
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ).order_by(desc(DataIntegrityIssue.detected_at)).limit(5).all()
        client_issues[row.client_id] = issues

    total_issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).count()

    total_clients = db.session.query(
        func.count(func.distinct(DataIntegrityIssue.client_id))
    ).filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).scalar() or 0

    from access_control import get_accessible_clients_ordered
    all_clients = get_accessible_clients_ordered()

    return render_template(
        'portfolio_performance/category_detail.html',
        category=category,
        clients_with_issues=clients_with_issues,
        client_issues=client_issues,
        total_issues=total_issues,
        total_clients=total_clients,
        all_clients=all_clients,
        client_filter=client_filter,
    )


@portfolio_performance_bp.route('/client/<int:client_id>')
@login_required
def client_issues(client_id):
    """All portfolio performance issues for one client."""
    client = Client.query.get_or_404(client_id)
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.client_id == client_id,
        DataIntegrityIssue.check_category == CATEGORY,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).order_by(
        DataIntegrityIssue.check_category,
        desc(DataIntegrityIssue.detected_at)
    ).all()

    issues_by_category = {}
    for issue in issues:
        key = issue.check_category
        if key not in issues_by_category:
            issues_by_category[key] = []
        issues_by_category[key].append(issue)

    total_issues = len(issues)

    return render_template(
        'portfolio_performance/client_issues.html',
        client=client,
        issues=issues,
        issues_by_category=issues_by_category,
        total_issues=total_issues,
    )


@portfolio_performance_bp.route('/resolve', methods=['POST'])
@login_required
def resolve_issues():
    """Resolve selected issues."""
    issue_ids = request.form.getlist('issue_id')
    resolution = request.form.get('resolution', 'fixed')

    if not issue_ids:
        flash('No issues selected', 'warning')
        return redirect(safe_referrer_path(request.referrer) or url_for('portfolio_performance.dashboard'))

    for iid in issue_ids:
        if not str(iid).isdigit():
            continue
        issue = DataIntegrityIssue.query.get(int(iid))
        if issue and issue.check_category == CATEGORY and issue.status in ('open', 'baseline'):
            issue.status = 'resolved'
            issue.resolution_type = resolution
            issue.resolved_at = datetime.utcnow()
            issue.resolved_by = current_user.id

    db.session.commit()
    flash(f'Resolved {len(issue_ids)} issue(s)', 'success')
    return redirect(safe_referrer_path(request.referrer) or url_for('portfolio_performance.dashboard'))


@portfolio_performance_bp.route('/bulk-resolve/<category>', methods=['POST'])
@login_required
def bulk_resolve(category):
    """Resolve all open issues in a category."""
    if category != CATEGORY:
        flash('Invalid category', 'error')
        return redirect(url_for('portfolio_performance.dashboard'))

    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == category,
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).all()

    for issue in issues:
        issue.status = 'resolved'
        issue.resolution_type = 'fixed'
        issue.resolved_at = datetime.utcnow()
        issue.resolved_by = current_user.id

    db.session.commit()
    flash(f'Resolved {len(issues)} issue(s)', 'success')
    return redirect(url_for('portfolio_performance.dashboard'))


@portfolio_performance_bp.route('/rerun', methods=['POST'])
@login_required
def rerun_agent():
    """Unified integrity refresh (same backend as Data Integrity / Rec Exec reruns)."""
    from access_control import get_accessible_clients
    from services.client_integrity_refresh_service import (
        format_refresh_flash,
        refresh_integrity_scope,
    )

    mode = (request.form.get('mode') or 'incremental').strip().lower()
    if mode == 'baseline':
        try:
            from agents import PortfolioPerformanceMonitor
            run_id = PortfolioPerformanceMonitor().run_baseline(user_id=current_user.id)
            flash(f'Baseline agent run started: {run_id}', 'success')
        except Exception as e:
            logger.error(f"Error running baseline: {e}", exc_info=True)
            flash(f'Error running agent: {str(e)}', 'error')
        return redirect(url_for('portfolio_performance.dashboard'))

    # PPM historically treated "incremental" as full audit; keep full for that button
    # when labelled Full Audit; Incremental uses recent-activity scope.
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

    return redirect(url_for('portfolio_performance.dashboard'))
