"""
Agent System API Routes
=======================
Endpoints for:
- Viewing and resolving data integrity issues
- Managing client behavior patterns
- Triggering agent runs
- Bulk operations
"""

import hashlib
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from sqlalchemy import func, and_

from extensions import db
from models import (
    DataIntegrityIssue, DataIntegrityException, ClientBehaviorPattern,
    AgentRun, Client
)

logger = logging.getLogger(__name__)

agents_bp = Blueprint('agents', __name__, url_prefix='/api/agents')


@agents_bp.before_request
def _enforce_agents_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


# =============================================================================
# ISSUE LISTING & GROUPING
# =============================================================================

@agents_bp.route('/issues', methods=['GET'])
@login_required
def list_issues():
    """
    List data integrity issues with filtering and grouping.
    
    Query params:
    - client_id: Filter by client
    - status: Filter by status (open, resolved, etc.)
    - severity: Filter by severity
    - category: Filter by check category
    - group_by: Group issues (client, category, group_key)
    - page, per_page: Pagination
    """
    client_id = request.args.get('client_id', type=int)
    status = request.args.get('status', 'open')
    severity = request.args.get('severity')
    category = request.args.get('category')
    group_by = request.args.get('group_by')
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
    query = DataIntegrityIssue.query
    
    if client_id:
        query = query.filter_by(client_id=client_id)
    if status:
        query = query.filter_by(status=status)
    if severity:
        query = query.filter_by(severity=severity)
    if category:
        query = query.filter_by(check_category=category)
    
    # Grouping
    if group_by == 'client':
        # Return summary per client
        summary = db.session.query(
            DataIntegrityIssue.client_id,
            Client.name,
            func.count(DataIntegrityIssue.id).label('issue_count'),
            func.sum(func.IF(DataIntegrityIssue.severity == 'critical', 1, 0)).label('critical_count'),
            func.sum(func.IF(DataIntegrityIssue.severity == 'warning', 1, 0)).label('warning_count'),
            func.sum(func.IF(DataIntegrityIssue.severity == 'info', 1, 0)).label('info_count')
        ).join(Client).filter(
            DataIntegrityIssue.status == status if status else True
        ).group_by(DataIntegrityIssue.client_id).all()
        
        return jsonify({
            'grouped_by': 'client',
            'items': [
                {
                    'client_id': row[0],
                    'client_name': row[1],
                    'issue_count': row[2],
                    'critical_count': row[3] or 0,
                    'warning_count': row[4] or 0,
                    'info_count': row[5] or 0
                }
                for row in summary
            ]
        })
    
    elif group_by == 'group_key':
        # Return summary per group_key (for bulk resolution)
        summary = db.session.query(
            DataIntegrityIssue.group_key,
            DataIntegrityIssue.check_category,
            DataIntegrityIssue.check_name,
            func.count(DataIntegrityIssue.id).label('issue_count'),
            func.min(DataIntegrityIssue.detected_at).label('first_detected')
        ).filter(
            DataIntegrityIssue.status == status if status else True,
            DataIntegrityIssue.client_id == client_id if client_id else True
        ).group_by(
            DataIntegrityIssue.group_key,
            DataIntegrityIssue.check_category,
            DataIntegrityIssue.check_name
        ).all()
        
        return jsonify({
            'grouped_by': 'group_key',
            'items': [
                {
                    'group_key': row[0],
                    'check_category': row[1],
                    'check_name': row[2],
                    'issue_count': row[3],
                    'first_detected': row[4].isoformat() if row[4] else None
                }
                for row in summary
            ]
        })
    
    # Default: return individual issues
    query = query.order_by(
        DataIntegrityIssue.severity.desc(),  # critical first
        DataIntegrityIssue.detected_at.desc()
    )
    
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    return jsonify({
        'items': [_issue_to_dict(issue) for issue in pagination.items],
        'total': pagination.total,
        'page': pagination.page,
        'per_page': pagination.per_page,
        'pages': pagination.pages
    })


@agents_bp.route('/issues/<int:issue_id>', methods=['GET'])
@login_required
def get_issue(issue_id):
    """Get a single issue with full details."""
    issue = DataIntegrityIssue.query.get_or_404(issue_id)
    return jsonify(_issue_to_dict(issue, include_details=True))


# =============================================================================
# ISSUE RESOLUTION (FEEDBACK)
# =============================================================================

@agents_bp.route('/issues/<int:issue_id>/resolve', methods=['POST'])
@login_required
def resolve_issue(issue_id):
    """
    Resolve an issue with feedback.
    
    Body:
    {
        "resolution": "fixed" | "exception" | "relax_rule" | "false_positive",
        "note": "optional note",
        "pattern_value": {...}  // only for relax_rule
    }
    """
    issue = DataIntegrityIssue.query.get_or_404(issue_id)
    data = request.get_json() or {}
    
    resolution = data.get('resolution')
    if resolution not in ('fixed', 'exception', 'relax_rule', 'false_positive'):
        return jsonify({'error': 'Invalid resolution type'}), 400
    
    note = data.get('note', '')
    
    # Handle each resolution type
    if resolution == 'fixed':
        issue.status = 'resolved'
        issue.resolution_type = 'fixed'
        
    elif resolution == 'exception':
        issue.status = 'resolved'
        issue.resolution_type = 'exception'
        _create_exception(issue, note, current_user.id)
        
    elif resolution == 'relax_rule':
        issue.status = 'resolved'
        issue.resolution_type = 'relax_rule'
        pattern_value = data.get('pattern_value', {})
        _create_or_update_pattern(issue, pattern_value, note, current_user.id)
        
    elif resolution == 'false_positive':
        issue.status = 'false_positive'
        issue.resolution_type = 'false_positive'
    
    issue.resolved_at = datetime.utcnow()
    issue.resolved_by = current_user.id
    issue.resolution_notes = note
    
    db.session.commit()
    
    logger.info(f"Issue {issue_id} resolved by user {current_user.id} as {resolution}")
    
    return jsonify({
        'success': True,
        'issue_id': issue_id,
        'resolution': resolution,
        'message': f'Issue resolved as {resolution}'
    })


@agents_bp.route('/issues/bulk-resolve', methods=['POST'])
@login_required
def bulk_resolve_issues():
    """
    Resolve multiple issues at once.
    
    Body:
    {
        "issue_ids": [1, 2, 3],  // OR
        "group_key": "RECOMMENDATION_MATCH_qty_mismatch_2026_01",
        "resolution": "fixed" | "exception" | "relax_rule" | "false_positive",
        "note": "optional note",
        "pattern_value": {...}  // only for relax_rule
    }
    """
    data = request.get_json() or {}
    
    resolution = data.get('resolution')
    if resolution not in ('fixed', 'exception', 'relax_rule', 'false_positive'):
        return jsonify({'error': 'Invalid resolution type'}), 400
    
    # Get issues to resolve
    issue_ids = data.get('issue_ids')
    group_key = data.get('group_key')
    client_id = data.get('client_id')
    
    if issue_ids:
        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.id.in_(issue_ids),
            DataIntegrityIssue.status == 'open'
        ).all()
    elif group_key:
        query = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.group_key == group_key,
            DataIntegrityIssue.status == 'open'
        )
        if client_id:
            query = query.filter_by(client_id=client_id)
        issues = query.all()
    else:
        return jsonify({'error': 'Either issue_ids or group_key required'}), 400
    
    note = data.get('note', '')
    pattern_value = data.get('pattern_value', {})
    resolved_count = 0
    
    for issue in issues:
        if resolution == 'fixed':
            issue.status = 'resolved'
            issue.resolution_type = 'fixed'
        elif resolution == 'exception':
            issue.status = 'resolved'
            issue.resolution_type = 'exception'
            _create_exception(issue, note, current_user.id)
        elif resolution == 'relax_rule':
            issue.status = 'resolved'
            issue.resolution_type = 'relax_rule'
            _create_or_update_pattern(issue, pattern_value, note, current_user.id)
        elif resolution == 'false_positive':
            issue.status = 'false_positive'
            issue.resolution_type = 'false_positive'
        
        issue.resolved_at = datetime.utcnow()
        issue.resolved_by = current_user.id
        issue.resolution_notes = note
        resolved_count += 1
    
    db.session.commit()
    
    logger.info(f"Bulk resolved {resolved_count} issues by user {current_user.id} as {resolution}")
    
    return jsonify({
        'success': True,
        'resolved_count': resolved_count,
        'resolution': resolution,
        'message': f'Resolved {resolved_count} issues as {resolution}'
    })


# =============================================================================
# PATTERN SUGGESTIONS
# =============================================================================

@agents_bp.route('/issues/<int:issue_id>/suggest-pattern', methods=['GET'])
@login_required
def suggest_pattern(issue_id):
    """
    Suggest a pattern based on the issue details.
    Auto-calculates multiplier, tolerance, etc.
    """
    issue = DataIntegrityIssue.query.get_or_404(issue_id)
    
    suggestion = {}
    
    if issue.check_name == 'quantity_mismatch':
        details = issue.details or {}
        expected = details.get('expected_qty', 1)
        actual = details.get('actual_qty', 1)
        
        if expected > 0:
            multiplier = round(actual / expected, 2)
            suggestion = {
                'pattern_type': 'quantity_multiplier',
                'pattern_value': {
                    'multiplier': multiplier,
                    'tolerance': 0.20  # Default 20%
                },
                'description': f"Client typically buys {multiplier}x the recommended quantity"
            }
    
    elif issue.check_name == 'price_mismatch':
        details = issue.details or {}
        variance = details.get('variance_pct', 5)
        
        suggestion = {
            'pattern_type': 'price_tolerance',
            'pattern_value': {
                'tolerance_pct': round(variance + 2, 1)  # Add buffer
            },
            'description': f"Allow price variance up to {round(variance + 2, 1)}%"
        }

    elif issue.check_name == 'trade_execution_mismatch':
        details = issue.details or {}
        qd = details.get('quantity') if isinstance(details.get('quantity'), dict) else None
        pd = details.get('price') if isinstance(details.get('price'), dict) else None
        if qd:
            expected = qd.get('expected_qty', 1)
            actual = qd.get('actual_qty', 1)
            try:
                expected = float(expected)
                actual = float(actual)
            except (TypeError, ValueError):
                expected, actual = 1.0, 1.0
            multiplier = round(actual / expected, 2) if expected else 1.0
            suggestion = {
                'pattern_type': 'quantity_multiplier',
                'pattern_value': {'multiplier': multiplier, 'tolerance': 0.20},
                'description': f"Client typically executes ~{multiplier}x recommended quantity",
            }
        elif pd:
            variance = pd.get('variance_pct', 5)
            try:
                variance = float(variance)
            except (TypeError, ValueError):
                variance = 5.0
            suggestion = {
                'pattern_type': 'price_tolerance',
                'pattern_value': {'tolerance_pct': round(variance + 2, 1)},
                'description': f"Allow price variance up to {round(variance + 2, 1)}%",
            }
    
    elif issue.check_name == 'unexecuted_recommendation':
        suggestion = {
            'pattern_type': 'execution_delay',
            'pattern_value': {
                'typical_delay_days': 30  # Extend window
            },
            'description': "Client typically executes recommendations with longer delay"
        }

    elif issue.check_name == 'unexecuted_recommendations_batch':
        n = (issue.details or {}).get('count') or len((issue.details or {}).get('related_items') or [])
        suggestion = {
            'pattern_type': 'execution_delay',
            'pattern_value': {'typical_delay_days': max(30, 21)},
            'description': f"Batch of {n} line(s): extend execution window if client consistently trades later",
        }

    elif issue.check_name == 'unrecorded_superseded_session':
        suggestion = {
            'pattern_type': 'execution_delay',
            'pattern_value': {'typical_delay_days': 21},
            'description': (
                "This session looks unused because another session in the same month "
                "was recorded. Confirm unused advice and close — do not treat as a delay."
            ),
        }
    
    return jsonify({
        'issue_id': issue_id,
        'check_category': issue.check_category,
        'check_name': issue.check_name,
        'suggestion': suggestion
    })


# =============================================================================
# CLIENT PATTERNS
# =============================================================================

@agents_bp.route('/clients/<int:client_id>/patterns', methods=['GET'])
@login_required
def list_client_patterns(client_id):
    """List all behavior patterns for a client."""
    patterns = ClientBehaviorPattern.query.filter_by(client_id=client_id).all()
    
    return jsonify({
        'client_id': client_id,
        'patterns': [
            {
                'id': p.id,
                'check_category': p.check_category,
                'pattern_type': p.pattern_type,
                'applies_to': p.applies_to,
                'security_id': p.security_id,
                'pattern_value': p.pattern_value,
                'occurrences': p.occurrences,
                'confidence': p.confidence,
                'notes': p.notes,
                'created_at': p.created_at.isoformat() if p.created_at else None,
                'last_used_at': p.last_used_at.isoformat() if p.last_used_at else None
            }
            for p in patterns
        ]
    })


@agents_bp.route('/clients/<int:client_id>/patterns/<int:pattern_id>', methods=['DELETE'])
@login_required
def delete_pattern(client_id, pattern_id):
    """Delete a client behavior pattern."""
    pattern = ClientBehaviorPattern.query.filter_by(
        id=pattern_id,
        client_id=client_id
    ).first_or_404()
    
    db.session.delete(pattern)
    db.session.commit()
    
    logger.info(f"Pattern {pattern_id} deleted by user {current_user.id}")
    
    return jsonify({
        'success': True,
        'message': 'Pattern deleted'
    })


# =============================================================================
# AGENT RUNS
# =============================================================================

@agents_bp.route('/runs', methods=['GET'])
@login_required
def list_runs():
    """List recent agent runs."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    agent_name = request.args.get('agent_name')
    
    query = AgentRun.query
    if agent_name:
        query = query.filter_by(agent_name=agent_name)
    
    query = query.order_by(AgentRun.started_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    return jsonify({
        'items': [
            {
                'run_id': run.run_id,
                'agent_name': run.agent_name,
                'run_mode': run.run_mode,
                'status': run.status,
                'started_at': run.started_at.isoformat() if run.started_at else None,
                'completed_at': run.completed_at.isoformat() if run.completed_at else None,
                'duration_seconds': run.duration_seconds,
                'clients_processed': run.clients_processed,
                'issues_found': run.issues_found,
                'issues_by_severity': run.issues_by_severity,
                'triggered_by': run.triggered_by
            }
            for run in pagination.items
        ],
        'total': pagination.total,
        'page': pagination.page,
        'per_page': pagination.per_page
    })


@agents_bp.route('/runs/trigger', methods=['POST'])
@login_required
def trigger_run():
    """
    Manually trigger an agent run.
    
    Body:
    {
        "agent_name": "data_integrity_manager",
        "mode": "full_audit" | "incremental" | "baseline",
        "client_ids": [1, 2, 3]  // optional, for specific clients
    }
    """
    data = request.get_json() or {}
    
    agent_name = data.get('agent_name', 'data_integrity_manager')
    mode = data.get('mode', 'incremental')
    client_ids = data.get('client_ids')
    
    if agent_name == 'data_integrity_manager':
        from agents import DataIntegrityManager
        agent = DataIntegrityManager()
        
        if mode == 'full_audit':
            run_id = agent.run_full_audit(client_ids=client_ids, user_id=current_user.id)
        elif mode == 'baseline':
            run_id = agent.run_baseline(client_ids=client_ids, user_id=current_user.id)
        else:
            run_id = agent.run_incremental(user_id=current_user.id)
        
        return jsonify({
            'success': True,
            'run_id': run_id,
            'message': f'Agent run started: {run_id}'
        })
    
    return jsonify({'error': f'Unknown agent: {agent_name}'}), 400


# =============================================================================
# DASHBOARD SUMMARY
# =============================================================================

@agents_bp.route('/dashboard', methods=['GET'])
@login_required
def dashboard():
    """Get summary for agents dashboard."""
    
    # Open issues by severity
    severity_counts = db.session.query(
        DataIntegrityIssue.severity,
        func.count(DataIntegrityIssue.id)
    ).filter_by(status='open').group_by(DataIntegrityIssue.severity).all()
    
    # Open issues by category
    category_counts = db.session.query(
        DataIntegrityIssue.check_category,
        func.count(DataIntegrityIssue.id)
    ).filter_by(status='open').group_by(DataIntegrityIssue.check_category).all()
    
    # Recent runs
    recent_runs = AgentRun.query.order_by(AgentRun.started_at.desc()).limit(5).all()
    
    # Clients with most issues
    top_clients = db.session.query(
        DataIntegrityIssue.client_id,
        Client.name,
        func.count(DataIntegrityIssue.id).label('issue_count')
    ).join(Client).filter(
        DataIntegrityIssue.status == 'open'
    ).group_by(
        DataIntegrityIssue.client_id
    ).order_by(
        func.count(DataIntegrityIssue.id).desc()
    ).limit(10).all()
    
    return jsonify({
        'open_issues': {
            'total': sum(c[1] for c in severity_counts),
            'by_severity': {s: c for s, c in severity_counts},
            'by_category': {c: cnt for c, cnt in category_counts}
        },
        'recent_runs': [
            {
                'run_id': run.run_id,
                'agent_name': run.agent_name,
                'status': run.status,
                'started_at': run.started_at.isoformat() if run.started_at else None,
                'issues_found': run.issues_found
            }
            for run in recent_runs
        ],
        'top_clients': [
            {
                'client_id': row[0],
                'client_name': row[1],
                'issue_count': row[2]
            }
            for row in top_clients
        ]
    })


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _issue_to_dict(issue: DataIntegrityIssue, include_details: bool = False) -> dict:
    """Convert issue to dictionary."""
    result = {
        'id': issue.id,
        'client_id': issue.client_id,
        'client_name': issue.client.name if issue.client else None,
        'check_category': issue.check_category,
        'check_name': issue.check_name,
        'severity': issue.severity,
        'message': issue.message,
        'status': issue.status,
        'suggested_action': issue.suggested_action,
        'group_key': issue.group_key,
        'reference_date': issue.reference_date.isoformat() if issue.reference_date else None,
        'detected_at': issue.detected_at.isoformat() if issue.detected_at else None,
        'resolved_at': issue.resolved_at.isoformat() if issue.resolved_at else None,
        'resolution_type': issue.resolution_type
    }
    
    if include_details:
        result['details'] = issue.details
        result['security_id'] = issue.security_id
        result['security_symbol'] = issue.security.symbol if issue.security else None
        result['transaction_id'] = issue.transaction_id
        result['recommendation_id'] = issue.recommendation_id
        result['resolution_notes'] = issue.resolution_notes
        result['run_id'] = issue.run_id
    
    return result


def _create_exception(issue: DataIntegrityIssue, reason: str, user_id: int):
    """Create a one-time exception for this issue."""
    # Generate hash for quick lookup
    parts = [
        str(issue.client_id),
        issue.check_category,
        issue.check_name,
        str(issue.transaction_id) if issue.transaction_id else '',
        str(issue.recommendation_id) if issue.recommendation_id else '',
        str(issue.reference_date) if issue.reference_date else ''
    ]
    exc_hash = hashlib.sha256("|".join(parts).encode()).hexdigest()
    
    # Check if exception already exists
    existing = DataIntegrityException.query.filter_by(exception_hash=exc_hash).first()
    if existing:
        return existing
    
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
        reason=reason,
        original_issue_id=issue.id,
        created_by=user_id
    )
    
    db.session.add(exception)
    return exception


def _create_or_update_pattern(issue: DataIntegrityIssue, pattern_value: dict, 
                               note: str, user_id: int):
    """Create or update a client behavior pattern."""
    
    # Determine pattern type from check name
    pattern_type_map = {
        'quantity_mismatch': 'quantity_multiplier',
        'price_mismatch': 'price_tolerance',
        'unexecuted_recommendation': 'execution_delay',
        'orphan_cashflow': 'partial_investment'
    }

    if issue.check_category == 'RECOMMENDATION_MATCH' and issue.check_name in (
        'trade_execution_mismatch',
        'unexecuted_recommendations_batch',
        'unrecorded_superseded_session',
    ):
        from routes.data_integrity import _pattern_type_for_issue

        pattern_type = _pattern_type_for_issue(issue)
    else:
        pattern_type = pattern_type_map.get(issue.check_name, issue.check_name)
    
    # Check for existing pattern
    existing = ClientBehaviorPattern.query.filter_by(
        client_id=issue.client_id,
        check_category=issue.check_category,
        pattern_type=pattern_type,
        security_id=issue.security_id
    ).first()
    
    if existing:
        # Update existing pattern
        existing.pattern_value = pattern_value or existing.pattern_value
        existing.occurrences += 1
        existing.confidence = min(0.95, existing.confidence + 0.1)
        existing.last_used_at = datetime.utcnow()
        if note:
            existing.notes = note
        return existing
    
    # Create new pattern
    # Auto-calculate pattern value if not provided
    if not pattern_value:
        if pattern_type == 'quantity_multiplier':
            details = issue.details or {}
            expected = details.get('expected_qty', 1)
            actual = details.get('actual_qty', 1)
            multiplier = actual / expected if expected > 0 else 1.0
            pattern_value = {
                'multiplier': round(multiplier, 2),
                'tolerance': 0.20
            }
        elif pattern_type == 'price_tolerance':
            details = issue.details or {}
            variance = details.get('variance_pct', 5)
            pattern_value = {
                'tolerance_pct': round(variance + 2, 1)
            }
        else:
            pattern_value = {}
    
    pattern = ClientBehaviorPattern(
        client_id=issue.client_id,
        check_category=issue.check_category,
        pattern_type=pattern_type,
        applies_to='security_specific' if issue.security_id else 'all',
        security_id=issue.security_id,
        pattern_value=pattern_value,
        occurrences=1,
        confidence=0.5,
        last_used_at=datetime.utcnow(),
        learned_from_issue_id=issue.id,
        notes=note,
        created_by=user_id
    )
    
    db.session.add(pattern)
    return pattern
