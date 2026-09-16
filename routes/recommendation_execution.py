"""
Recommendation Execution Monitor Dashboard Routes
=================================================
UI pages for viewing and managing recommendation execution issues.
"""

import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.internal_next import safe_referrer_path
from sqlalchemy import case, desc, func
import hashlib

from extensions import db
from models import (
    DataIntegrityIssue, DataIntegrityException, ClientBehaviorPattern,
    AgentRun, AgentFeedback, Client, Workflow, MonthlyInvestment
)

logger = logging.getLogger(__name__)

rec_exec_bp = Blueprint('rec_execution', __name__, url_prefix='/recommendation-execution')
rec_exec_bp.strict_slashes = False


@rec_exec_bp.before_request
def _enforce_rec_exec_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


# Import Alert from alert_system_models (same model used by the alert dashboard)
try:
    from alert_system_models import Alert
    ALERT_AVAILABLE = True
except Exception:
    Alert = None
    ALERT_AVAILABLE = False


# =============================================================================
# CLIENT SLA SETTINGS
# =============================================================================

@rec_exec_bp.route('/sla')
@login_required
def sla_settings():
    """
    Client-level SLA configuration for Recommendation Execution Monitor.
    Uses ClientBehaviorPattern(check_category='RECOMMENDATION_EXECUTION', pattern_type='stage_sla_days').
    """
    client_id = request.args.get('client_id', type=int)
    from access_control import can_access_client, get_accessible_clients_ordered
    clients = get_accessible_clients_ordered()

    client = None
    stage_sla = {}
    exception_count = 0
    if client_id:
        if not can_access_client(client_id):
            flash("Access denied. You can only access your assigned clients.", "error")
            return redirect(url_for("rec_exec.sla_settings"))
        client = Client.query.get_or_404(client_id)
        stage_sla_pattern = ClientBehaviorPattern.query.filter_by(
            client_id=client_id,
            check_category='RECOMMENDATION_EXECUTION',
            pattern_type='stage_sla_days',
            security_id=None
        ).first()
        stage_sla = stage_sla_pattern.pattern_value if stage_sla_pattern and isinstance(stage_sla_pattern.pattern_value, dict) else {}
        exception_count = DataIntegrityException.query.filter_by(
            client_id=client_id,
            check_category='RECOMMENDATION_EXECUTION'
        ).count()

    return render_template(
        'rec_execution/sla_settings.html',
        clients=clients,
        client=client,
        stage_sla=stage_sla,
        exception_count=exception_count
    )


# =============================================================================
# DASHBOARD
# =============================================================================

@rec_exec_bp.route('/')
@login_required
def dashboard():
    """Main recommendation execution dashboard."""
    
    # Get summary counts
    severity_counts = db.session.query(
        DataIntegrityIssue.severity,
        func.count(DataIntegrityIssue.id)
    ).filter(
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
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
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    resolved_count = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
        DataIntegrityIssue.status == 'resolved',
        DataIntegrityIssue.resolved_at >= thirty_days_ago
    ).count()
    
    # Get issues grouped by check type
    issue_count_col = func.count(DataIntegrityIssue.id).label('issue_count')
    check_summary = db.session.query(
        DataIntegrityIssue.check_name,
        DataIntegrityIssue.severity,
        issue_count_col,
        func.count(func.distinct(DataIntegrityIssue.client_id)).label('client_count')
    ).filter(
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(
        DataIntegrityIssue.check_name,
        DataIntegrityIssue.severity
    ).order_by(
        desc(issue_count_col)
    ).all()
    
    # Group by check name for display
    checks = {}
    for row in check_summary:
        check = row.check_name
        if check not in checks:
            checks[check] = {
                'name': check.replace('_', ' ').title(),
                'total_issues': 0,
                'total_clients': 0,
                'max_severity': 'info',
                'by_severity': {'critical': 0, 'warning': 0, 'info': 0}
            }
        checks[check]['total_issues'] += row.issue_count
        checks[check]['by_severity'][row.severity] = row.issue_count
        # Track max severity
        if row.severity == 'critical':
            checks[check]['max_severity'] = 'critical'
        elif row.severity == 'warning' and checks[check]['max_severity'] != 'critical':
            checks[check]['max_severity'] = 'warning'
    
    # Calculate unique client count per check
    for check_name in checks:
        client_count = db.session.query(
            func.count(func.distinct(DataIntegrityIssue.client_id))
        ).filter(
            DataIntegrityIssue.check_name == check_name,
            DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ).scalar()
        checks[check_name]['total_clients'] = client_count or 0
    
    # Get last run
    last_run = AgentRun.query.filter_by(
        agent_name='rec_exec_monitor'
    ).order_by(desc(AgentRun.started_at)).first()
    
    # Get recent runs
    recent_runs = AgentRun.query.filter_by(
        agent_name='rec_exec_monitor'
    ).order_by(desc(AgentRun.started_at)).limit(5).all()
    
    return render_template('rec_execution/dashboard.html',
        severity_summary=severity_summary,
        total_open=total_open,
        resolved_count=resolved_count,
        checks=checks,
        last_run=last_run,
        recent_runs=recent_runs
    )


# =============================================================================
# BY CLIENT (merged view)
# =============================================================================

@rec_exec_bp.route('/by-client')
@login_required
def by_client():
    """List all clients that have at least one open Recommendation Execution issue (merged client-wise)."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    # Clients with open RECOMMENDATION_EXECUTION issues, with issue count and max severity
    subq = db.session.query(
        DataIntegrityIssue.client_id,
        func.count(DataIntegrityIssue.id).label('issue_count'),
        func.max(
            case(
                (DataIntegrityIssue.severity == 'critical', 3),
                (DataIntegrityIssue.severity == 'warning', 2),
                else_=1
            )
        ).label('severity_rank')
    ).filter(
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(DataIntegrityIssue.client_id).subquery()

    query = db.session.query(
        Client.id.label('client_id'),
        Client.name.label('client_name'),
        subq.c.issue_count,
        subq.c.severity_rank
    ).join(subq, Client.id == subq.c.client_id).order_by(
        desc(subq.c.severity_rank),
        desc(subq.c.issue_count),
        Client.name
    )
    total = query.count()
    clients = query.offset((page - 1) * per_page).limit(per_page).all()

    # Map severity_rank back to label
    def severity_label(rank):
        return 'critical' if rank == 3 else ('warning' if rank == 2 else 'info')

    client_rows = []
    for row in clients:
        client_rows.append({
            'client_id': row.client_id,
            'client_name': row.client_name,
            'issue_count': row.issue_count,
            'max_severity': severity_label(row.severity_rank) if row.severity_rank else 'info',
        })

    total_pages = max(1, (total + per_page - 1) // per_page)
    return render_template('rec_execution/by_client.html',
        clients=client_rows,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages
    )


@rec_exec_bp.route('/client/<int:client_id>')
@login_required
def client_issues(client_id):
    """View all Recommendation Execution issues for one client (all check types merged)."""
    client = Client.query.get_or_404(client_id)
    severity_order = case(
        (DataIntegrityIssue.severity == 'critical', 0),
        (DataIntegrityIssue.severity == 'warning', 1),
        else_=2
    )
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.client_id == client_id,
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).order_by(
        severity_order,
        desc(DataIntegrityIssue.detected_at)
    ).all()

    patterns = ClientBehaviorPattern.query.filter_by(
        client_id=client_id
    ).all()
    stage_sla_pattern = ClientBehaviorPattern.query.filter_by(
        client_id=client_id,
        check_category='RECOMMENDATION_EXECUTION',
        pattern_type='stage_sla_days',
        security_id=None
    ).first()
    stage_sla = stage_sla_pattern.pattern_value if stage_sla_pattern and isinstance(stage_sla_pattern.pattern_value, dict) else {}
    exception_count = DataIntegrityException.query.filter_by(
        client_id=client_id,
        check_category='RECOMMENDATION_EXECUTION'
    ).count()

    from services.task_assignment_service import list_assignable_users
    assignee_users = list_assignable_users()

    return render_template('rec_execution/client_issues.html',
        client=client,
        issues=issues,
        total_issues=len(issues),
        patterns=patterns,
        stage_sla=stage_sla,
        exception_count=exception_count,
        assignee_users=assignee_users,
        assignee_user_ids={u.id for u in assignee_users},
    )


# =============================================================================
# CHECK DETAIL
# =============================================================================

@rec_exec_bp.route('/check/<check_name>')
@login_required
def check_detail(check_name):
    """View issues for a specific check type."""
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    client_filter = request.args.get('client_id', type=int)
    
    # Get issues grouped by client
    issue_count_col = func.count(DataIntegrityIssue.id).label('issue_count')
    query = db.session.query(
        DataIntegrityIssue.client_id,
        Client.name.label('client_name'),
        issue_count_col,
        func.max(DataIntegrityIssue.severity).label('max_severity')
    ).join(Client).filter(
        DataIntegrityIssue.check_name == check_name,
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).group_by(
        DataIntegrityIssue.client_id,
        Client.name
    ).order_by(
        desc(issue_count_col),
        Client.name
    )
    from access_control import can_access_client, scope_query_to_accessible_clients
    query = scope_query_to_accessible_clients(query, DataIntegrityIssue.client_id)
    
    if client_filter:
        if not can_access_client(client_filter):
            flash("Access denied. You can only access your assigned clients.", "error")
            return redirect(url_for("rec_exec.check_detail", check_name=check_name))
        query = query.filter(DataIntegrityIssue.client_id == client_filter)
    
    # Paginate
    total = query.count()
    clients_with_issues = query.offset((page - 1) * per_page).limit(per_page).all()
    
    # Get detailed issues for each client (first 5 per client for preview)
    client_issues = {}
    for row in clients_with_issues:
        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == row.client_id,
            DataIntegrityIssue.check_name == check_name,
            DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ).order_by(desc(DataIntegrityIssue.detected_at)).limit(5).all()
        client_issues[row.client_id] = issues

    # SLA suggestion banner: if a client has repeated delays for the same stage/check,
    # show avg days vs SLA and link to SLA settings.
    client_ids = [row.client_id for row in clients_with_issues]
    sla_suggestions = {}
    if client_ids:
        # Get client SLA overrides in one query
        patterns = ClientBehaviorPattern.query.filter(
            ClientBehaviorPattern.client_id.in_(client_ids),
            ClientBehaviorPattern.check_category == 'RECOMMENDATION_EXECUTION',
            ClientBehaviorPattern.pattern_type == 'stage_sla_days',
            ClientBehaviorPattern.security_id.is_(None),
        ).all()
        sla_by_client = {
            p.client_id: (p.pattern_value if isinstance(p.pattern_value, dict) else {})
            for p in patterns
        }

        stage_map = {
            'funds_not_received': 'FUNDS',
            'recos_not_sent': 'RECOS',
            'recos_not_executed': 'EXEC',
            'workflow_stalled': 'STALL_ANY',
        }
        stage = stage_map.get(check_name)
        default_sla = {'FUNDS': 3, 'RECOS': 2, 'EXEC': 5, 'STALL_ANY': 7}

        # Look at last 180 days (open+baseline+resolved) for this check to compute average
        cutoff = datetime.utcnow() - timedelta(days=180)
        history = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
            DataIntegrityIssue.check_name == check_name,
            DataIntegrityIssue.client_id.in_(client_ids),
            DataIntegrityIssue.detected_at >= cutoff
        ).all()

        by_client = {}
        for issue in history:
            d = issue.details or {}
            days = d.get('days_overdue') or d.get('days_stalled')
            try:
                days = float(days)
            except Exception:
                continue
            by_client.setdefault(issue.client_id, []).append(days)

        if stage:
            for cid, days_list in by_client.items():
                if len(days_list) < 2:
                    continue  # only suggest SLA update when repeated
                avg_days = sum(days_list) / len(days_list)
                sla_val = default_sla.get(stage, 7)
                pv = sla_by_client.get(cid, {}) or {}
                try:
                    sla_val = int(pv.get(stage, pv.get('STALL_ANY', sla_val)))
                except Exception:
                    pass
                sla_suggestions[cid] = {
                    'stage': stage,
                    'sla_days': sla_val,
                    'avg_days': round(avg_days, 1),
                    'count': len(days_list),
                }
    
    # Get clients for filter dropdown (assigned book only for advisors)
    from access_control import get_accessible_clients_ordered, scope_query_to_accessible_clients
    all_clients = get_accessible_clients_ordered()

    total_issues = scope_query_to_accessible_clients(
        DataIntegrityIssue.query.filter(
            DataIntegrityIssue.check_name == check_name,
            DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ),
        DataIntegrityIssue.client_id,
    ).count()
    
    total_clients = scope_query_to_accessible_clients(
        db.session.query(func.count(func.distinct(DataIntegrityIssue.client_id))).filter(
            DataIntegrityIssue.check_name == check_name,
            DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ),
        DataIntegrityIssue.client_id,
    ).scalar()
    
    return render_template('rec_execution/check_detail.html',
        check_name=check_name,
        check_display=check_name.replace('_', ' ').title(),
        clients_with_issues=clients_with_issues,
        client_issues=client_issues,
        sla_suggestions=sla_suggestions,
        total_issues=total_issues,
        total_clients=total_clients,
        all_clients=all_clients,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        client_filter=client_filter
    )


@rec_exec_bp.route('/workflow/<int:id>')
@login_required
def workflow_issues(id):
    """
    View all issues for a specific workflow.
    Accepts MonthlyInvestment.id (preferred) or Workflow.id for backwards compatibility.
    Using MI id aligns with /monthly-investments/<id> and avoids ID namespace confusion.
    """
    # Resolve workflow: prefer MonthlyInvestment.id so /workflow/617 = workflow of MI 617
    mi = MonthlyInvestment.query.get(id)
    if mi and mi.workflow:
        workflow = mi.workflow
    else:
        workflow = Workflow.query.get_or_404(id)
    
    client_id = workflow.monthly_investment.client_id
    
    # Get issues for this workflow (check details JSON for workflow_id, with client_id safety)
    from sqlalchemy import cast, String
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
        DataIntegrityIssue.status.in_(['open', 'baseline']),
        DataIntegrityIssue.client_id == client_id,
        cast(DataIntegrityIssue.details['workflow_id'], String) == str(workflow.id)
    ).all()
    
    # If no issues found via details, try to find by client and date
    if not issues:
        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.client_id == client_id,
            DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
            DataIntegrityIssue.status.in_(['open', 'baseline']),
            DataIntegrityIssue.reference_date == workflow.investment_date
        ).all()
    
    # Get client patterns
    patterns = ClientBehaviorPattern.query.filter_by(
        client_id=workflow.monthly_investment.client_id
    ).all()

    # Stage SLA (client-specific override if present)
    stage_sla_pattern = ClientBehaviorPattern.query.filter_by(
        client_id=workflow.monthly_investment.client_id,
        check_category='RECOMMENDATION_EXECUTION',
        pattern_type='stage_sla_days',
        security_id=None
    ).first()
    stage_sla = stage_sla_pattern.pattern_value if stage_sla_pattern and isinstance(stage_sla_pattern.pattern_value, dict) else {}

    exception_count = DataIntegrityException.query.filter_by(
        client_id=workflow.monthly_investment.client_id,
        check_category='RECOMMENDATION_EXECUTION'
    ).count()

    from services.task_assignment_service import list_assignable_users
    assignee_users = list_assignable_users()

    return render_template('rec_execution/workflow_issues.html',
        workflow=workflow,
        issues=issues,
        patterns=patterns,
        total_issues=len(issues),
        stage_sla=stage_sla,
        exception_count=exception_count,
        assignee_users=assignee_users,
        assignee_user_ids={u.id for u in assignee_users},
    )


@rec_exec_bp.route('/client/<int:client_id>/sla', methods=['POST'])
@login_required
def set_client_sla(client_id: int):
    """
    Save per-client stage SLA days for Recommendation Execution Monitor.
    Stored in ClientBehaviorPattern(stage_sla_days).
    """
    def _to_int(name: str, default: int) -> int:
        v = request.form.get(name, type=int)
        return int(v) if v is not None else int(default)

    stage_sla = {
        'FUNDS': _to_int('sla_FUNDS', 3),
        'RECOS': _to_int('sla_RECOS', 2),
        'EXEC': _to_int('sla_EXEC', 5),
        'STALL_ANY': _to_int('sla_STALL_ANY', 7),
    }

    existing = ClientBehaviorPattern.query.filter_by(
        client_id=client_id,
        check_category='RECOMMENDATION_EXECUTION',
        pattern_type='stage_sla_days',
        security_id=None
    ).first()

    if existing:
        existing.pattern_value = stage_sla
        existing.confidence = max(existing.confidence or 0.0, 0.9)
        existing.last_used_at = datetime.utcnow()
        existing.updated_at = datetime.utcnow()
        existing.notes = (request.form.get('notes') or existing.notes)
    else:
        p = ClientBehaviorPattern(
            client_id=client_id,
            check_category='RECOMMENDATION_EXECUTION',
            pattern_type='stage_sla_days',
            applies_to='all',
            security_id=None,
            pattern_value=stage_sla,
            occurrences=1,
            confidence=0.9,
            last_used_at=datetime.utcnow(),
            notes=request.form.get('notes') or 'Per-client stage SLA configured from REM UI',
            created_by=current_user.id
        )
        db.session.add(p)

    db.session.commit()
    flash('Saved client SLA overrides for Recommendation Execution.', 'success')
    return redirect(safe_referrer_path(request.referrer) or url_for('rec_execution.sla_settings', client_id=client_id))


@rec_exec_bp.route('/create-alert', methods=['POST'])
@login_required
def create_alert_from_issues():
    """
    Create alert(s) from selected Recommendation Execution issues.
    Creates one alert per client and links selected issues via issue.alert_id.
    """
    if not ALERT_AVAILABLE:
        flash('Alert system not available in this environment.', 'warning')
        return redirect(safe_referrer_path(request.referrer) or url_for('rec_execution.dashboard'))

    issue_ids = request.form.getlist('issue_ids[]') or request.form.getlist('issue_ids')
    if not issue_ids:
        flash('No issues selected to create an alert.', 'error')
        return redirect(safe_referrer_path(request.referrer) or url_for('rec_execution.dashboard'))

    # Load issues and group by client
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.id.in_([int(x) for x in issue_ids if str(x).isdigit()]),
        DataIntegrityIssue.check_category == 'RECOMMENDATION_EXECUTION',
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).all()

    if not issues:
        flash('No valid open issues found for alert creation.', 'warning')
        return redirect(safe_referrer_path(request.referrer) or url_for('rec_execution.dashboard'))

    by_client = {}
    for issue in issues:
        by_client.setdefault(issue.client_id, []).append(issue)

    def _severity_rank(s: str) -> int:
        return {'critical': 3, 'warning': 2, 'info': 1}.get((s or 'info').lower(), 1)

    created = 0
    for client_id, client_issues in by_client.items():
        client = Client.query.get(client_id)
        client_name = client.name if client else f'Client {client_id}'

        # Determine severity (max)
        severity = max(((i.severity or 'info') for i in client_issues), key=_severity_rank)

        # Try to infer a single workflow_id if all issues refer to same workflow
        workflow_ids = set()
        for i in client_issues:
            try:
                wid = (i.details or {}).get('workflow_id')
                if wid:
                    workflow_ids.add(int(wid))
            except Exception:
                continue
        workflow_id = list(workflow_ids)[0] if len(workflow_ids) == 1 else None
        workflow = None
        if workflow_id:
            workflow = Workflow.query.get(workflow_id)

        # Assignee from TaskAssignmentService (rule-based); fallback to current_user
        try:
            from services.task_assignment_service import get_assignee_for_alert
            assigned_user_id = get_assignee_for_alert(
                alert_type='recommendation_execution',
                alert_subtype='',
                workflow=workflow,
                issues=client_issues,
                client_id=client_id,
            )
        except Exception:
            assigned_user_id = None
        if not assigned_user_id:
            assigned_user_id = getattr(current_user, 'id', None)

        # SLA / delay (best-effort; hours)
        max_days = 0
        for i in client_issues:
            d = i.details or {}
            for key in ('days_overdue', 'days_stalled'):
                if key in d:
                    try:
                        max_days = max(max_days, int(float(d.get(key) or 0)))
                    except Exception:
                        pass
        current_delay_hours = max_days * 24 if max_days else None

        # Alert subtype: single check_name or "multiple"
        check_names = {i.check_name for i in client_issues if i.check_name}
        alert_subtype = list(check_names)[0] if len(check_names) == 1 else 'multiple'

        title = f"Recommendation Execution: {client_name}"
        description_lines = [
            f"Client: {client_name} (id={client_id})",
            f"Open flags: {len(client_issues)}",
            "",
            "Issues:",
        ]
        for i in sorted(client_issues, key=lambda x: _severity_rank(x.severity), reverse=True):
            description_lines.append(f"- (issue #{i.id}) [{i.severity}] {i.message}")
        description = "\n".join(description_lines)

        alert = Alert(
            title=title,
            description=description,
            alert_type='recommendation_execution',
            alert_subtype=str(alert_subtype),
            severity=severity if severity in ['critical', 'warning', 'info'] else 'warning',
            status='active',
            client_id=client_id,
            workflow_id=workflow_id,
            user_id=assigned_user_id,
            current_delay=current_delay_hours,
            created_at=datetime.utcnow()
        )

        db.session.add(alert)
        db.session.flush()  # get alert.id

        for i in client_issues:
            i.alert_id = alert.id

        created += 1

    db.session.commit()
    flash(f'Created {created} alert(s) from selected issues.', 'success')
    return redirect(safe_referrer_path(request.referrer) or url_for('rec_execution.dashboard'))


# =============================================================================
# ACTIONS
# =============================================================================

@rec_exec_bp.route('/resolve', methods=['POST'])
@login_required
def resolve_issues():
    """Resolve one or more issues."""
    
    issue_ids = request.form.getlist('issue_ids[]') or request.form.getlist('issue_ids')
    resolution = request.form.get('resolution', 'fixed')
    note = request.form.get('note', '')
    
    if not issue_ids:
        flash('No issues selected', 'error')
        return redirect(safe_referrer_path(request.referrer) or url_for('rec_execution.dashboard'))
    
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

                # One-time exception: create DataIntegrityException keyed to this workflow/check
                if resolution in ['exception', 'snooze']:
                    workflow_id = None
                    try:
                        if issue.details and issue.details.get('workflow_id'):
                            workflow_id = str(issue.details.get('workflow_id'))
                    except Exception:
                        workflow_id = None

                    # Build exception hash (same idea as BaseAgent._generate_exception_hash)
                    parts = [
                        str(issue.client_id),
                        str(issue.check_category or ''),
                        str(issue.check_name or ''),
                    ]
                    identifiers = {
                        'workflow_id': workflow_id,
                        'recommendation_id': issue.recommendation_id,
                        'reference_date': str(issue.reference_date) if issue.reference_date else None,
                    }
                    for k in sorted(identifiers.keys()):
                        if identifiers[k] is not None:
                            parts.append(f"{k}:{identifiers[k]}")
                    exc_hash = hashlib.sha256("|".join(parts).encode()).hexdigest()

                    existing_exc = DataIntegrityException.query.filter_by(exception_hash=exc_hash).first()
                    if not existing_exc:
                        exc = DataIntegrityException(
                            client_id=issue.client_id,
                            check_category=issue.check_category,
                            check_name=issue.check_name,
                            recommendation_id=issue.recommendation_id,
                            security_id=issue.security_id,
                            reference_date=issue.reference_date,
                            exception_hash=exc_hash,
                            reason=note or f"One-time exception from REM UI ({resolution})",
                            original_issue_id=issue.id,
                            created_by=current_user.id
                        )
                        db.session.add(exc)
        except (ValueError, TypeError):
            continue
    
    db.session.commit()
    flash(f'Resolved {resolved_count} issues', 'success')
    
    return redirect(safe_referrer_path(request.referrer) or url_for('rec_execution.dashboard'))


@rec_exec_bp.route('/rerun', methods=['POST'])
@login_required
def rerun_agent():
    """Unified integrity refresh (same backend as Data Integrity / PPM reruns)."""
    from access_control import get_accessible_clients
    from services.client_integrity_refresh_service import (
        format_refresh_flash,
        refresh_integrity_scope,
    )

    mode = (request.form.get('mode') or 'incremental').strip().lower()
    if mode == 'baseline':
        try:
            from agents import RecommendationExecutionMonitor
            run_id = RecommendationExecutionMonitor().run_baseline(user_id=current_user.id)
            flash(f'Baseline agent run started: {run_id}', 'success')
        except Exception as e:
            logger.error(f"Error running baseline: {e}", exc_info=True)
            flash(f'Error running agent: {str(e)}', 'error')
        return redirect(url_for('rec_execution.dashboard'))

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

    return redirect(url_for('rec_execution.dashboard'))
