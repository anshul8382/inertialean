"""
Agents Dashboard Routes
=======================
UI pages for viewing and managing all agents.
"""

import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func, desc

from extensions import db
from models import AgentRun, DataIntegrityIssue, Client, AgentFeedback
from agents.registry import (
    AGENT_DISPLAY_NAMES,
    build_dashboard_agents_info,
    build_dashboard_agent_groups,
    get_agent_meta,
    get_issue_categories,
)

logger = logging.getLogger(__name__)

agents_dashboard_bp = Blueprint('agents_dashboard', __name__, url_prefix='/agents')


@agents_dashboard_bp.route('/')
@login_required
def dashboard():
    """Main agents dashboard showing all agents and their status."""
    try:
        return _render_agents_dashboard()
    except Exception as e:
        logger.warning(f"Agents dashboard DB error (tables may be missing): {e}", exc_info=True)
        return render_template('agents/dashboard.html',
            agents=[],
            agent_groups=[],
            recent_runs=[],
            total_runs=0,
            total_issues=0,
            issues_by_agent={},
            db_error=str(e)
        )


def _render_agents_dashboard():
    """Build agents dashboard data; raises if DB tables missing."""
    agents_info = build_dashboard_agents_info()

    # Get last run for each runtime agent
    for agent in agents_info:
        last_run = AgentRun.query.filter_by(
            agent_name=agent['name']
        ).order_by(desc(AgentRun.started_at)).first()

        agent['last_run'] = last_run
        agent['last_run_id'] = last_run.run_id if last_run else None
        agent['last_run_status'] = last_run.status if last_run else 'never'
        agent['last_run_issues'] = last_run.issues_found if last_run else 0
        agent['last_run_time'] = last_run.started_at if last_run else None

        cats = agent.get('issue_categories') or []
        if cats:
            open_issues = DataIntegrityIssue.query.filter(
                DataIntegrityIssue.check_category.in_(cats),
                DataIntegrityIssue.status.in_(['open', 'baseline'])
            ).count()
        else:
            open_issues = 0
        agent['open_issues'] = open_issues

    by_name = {a['name']: a for a in agents_info}
    agent_groups = build_dashboard_agent_groups()
    for group in agent_groups:
        for agent in group['agents']:
            if agent['name'] in by_name:
                agent.update(by_name[agent['name']])
            else:
                agent.setdefault('last_run_time', None)
                agent.setdefault('last_run_status', 'n/a')
                agent.setdefault('last_run_issues', 0)
                agent.setdefault('open_issues', 0)

    # Get recent runs across all agents
    recent_runs = AgentRun.query.order_by(
        desc(AgentRun.started_at)
    ).limit(10).all()

    # Get total stats
    total_runs = AgentRun.query.count()
    total_issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.status.in_(['open', 'baseline'])
    ).count()

    # Get issues by agent
    issues_by_agent = {}
    for agent in agents_info:
        cats = agent.get('issue_categories') or []
        issues = []
        if cats:
            issues = DataIntegrityIssue.query.filter(
                DataIntegrityIssue.check_category.in_(cats),
                DataIntegrityIssue.status.in_(['open', 'baseline'])
            ).all()
        issues_by_agent[agent['name']] = {
            'total': len(issues),
            'critical': len([i for i in issues if i.severity == 'critical']),
            'warning': len([i for i in issues if i.severity == 'warning']),
            'info': len([i for i in issues if i.severity == 'info'])
        }

    return render_template('agents/dashboard.html',
        agents=agents_info,
        agent_groups=agent_groups,
        recent_runs=recent_runs,
        total_runs=total_runs,
        total_issues=total_issues,
        issues_by_agent=issues_by_agent,
        db_error=None
    )


@agents_dashboard_bp.route('/<agent_name>')
@login_required
def agent_detail(agent_name):
    """View details for a specific agent."""
    
    meta = get_agent_meta(agent_name)
    agent_info = None
    if meta:
        agent_info = {
            'display_name': meta['display_name'],
            'description': meta['description'],
        }
        endpoint = meta.get('dashboard_endpoint')
        if endpoint:
            try:
                agent_info['dashboard_url'] = url_for(endpoint)
            except Exception:
                pass
    if not agent_info:
        flash('Agent not found', 'error')
        return redirect(url_for('agents_dashboard.dashboard'))
    
    # Get runs for this agent
    runs = AgentRun.query.filter_by(
        agent_name=agent_name
    ).order_by(desc(AgentRun.started_at)).limit(20).all()

    categories = get_issue_categories(agent_name)
    if categories:
        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.check_category.in_(categories),
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ).order_by(desc(DataIntegrityIssue.detected_at)).limit(50).all()
    else:
        issues = DataIntegrityIssue.query.filter(
            DataIntegrityIssue.check_category.like(f"{agent_name}%"),
            DataIntegrityIssue.status.in_(['open', 'baseline'])
        ).order_by(desc(DataIntegrityIssue.detected_at)).limit(50).all()
    
    return render_template('agents/agent_detail.html',
        agent_name=agent_name,
        agent_info=agent_info,
        runs=runs,
        issues=issues
    )


@agents_dashboard_bp.route('/<agent_name>/trigger', methods=['POST'])
@login_required
def trigger_agent(agent_name):
    """
    Trigger agent run. Integrity-related agents share one unified refresh backend
    (DIM + rec-exec + PPM + healer + alerts + snapshots per client in scope).
    """
    mode = request.form.get('mode', 'incremental')
    integrity_agents = {
        'data_integrity_manager',
        'rec_exec_monitor',
        'portfolio_performance_monitor',
        'alert_orchestrator',
    }

    try:
        if agent_name in integrity_agents and mode != 'baseline':
            from access_control import get_accessible_clients
            from services.client_integrity_refresh_service import (
                format_refresh_flash,
                refresh_integrity_scope,
            )

            scope_mode = 'full' if mode in ('full', 'full_audit') else 'incremental'
            accessible = get_accessible_clients()
            accessible_ids = None if accessible is None else {c.id for c in accessible}
            result = refresh_integrity_scope(
                mode=scope_mode,
                accessible_client_ids=accessible_ids,
                actor_user_id=current_user.id,
            )
            flash(format_refresh_flash(result), 'success' if result.get('ok') else 'warning')
            return redirect(url_for('agents_dashboard.dashboard'))

        if agent_name == 'data_integrity_manager':
            from agents import DataIntegrityManager
            agent = DataIntegrityManager()
        elif agent_name == 'rec_exec_monitor':
            from agents import RecommendationExecutionMonitor
            agent = RecommendationExecutionMonitor()
        elif agent_name == 'portfolio_performance_monitor':
            from agents import PortfolioPerformanceMonitor
            agent = PortfolioPerformanceMonitor()
        elif agent_name == 'alert_orchestrator':
            from agents import AlertOrchestrator
            agent = AlertOrchestrator()
        elif agent_name == 'task_assignment_agent':
            from agents import TaskAssignmentAgent
            updated, tasks_created = TaskAssignmentAgent().run_backfill()
            flash(
                f'Task assignment backfill complete: {updated} issues assigned, '
                f'{tasks_created} OpsTasks created/linked',
                'success',
            )
            return redirect(url_for('agents_dashboard.dashboard'))
        else:
            flash(f'Unknown agent: {agent_name}', 'error')
            return redirect(url_for('agents_dashboard.dashboard'))

        if mode == 'baseline':
            run_id = agent.run_baseline(user_id=current_user.id)
        elif agent_name == 'alert_orchestrator':
            run_id = agent.orchestrate_alerts(user_id=current_user.id)
        elif mode == 'full':
            run_id = agent.run_full_audit(user_id=current_user.id)
        else:
            run_id = agent.run_incremental(days=7, user_id=current_user.id)

        flash(f'Agent run started: {run_id}', 'success')
    except Exception as e:
        logger.error(f"Error running agent {agent_name}: {e}", exc_info=True)
        flash(f'Error running agent: {str(e)}', 'error')

    return redirect(url_for('agents_dashboard.dashboard'))


@agents_dashboard_bp.route('/<agent_name>/feedback', methods=['GET', 'POST'])
@login_required
def agent_feedback(agent_name):
    """
    Agent-specific feedback form.
    Stores feedback in AgentFeedback with related_check=agent_name.
    """
    agent_display = AGENT_DISPLAY_NAMES.get(agent_name, agent_name)

    if request.method == 'POST':
        category = request.form.get('category', 'other')
        title = request.form.get('title') or f'Feedback: {agent_display}'
        description = request.form.get('description')

        if not description:
            flash('Please enter feedback details.', 'error')
            return redirect(url_for('agents_dashboard.agent_feedback', agent_name=agent_name))

        fb = AgentFeedback(
            category=category,
            related_check=agent_name,
            title=title,
            description=description,
            created_by=current_user.id
        )
        db.session.add(fb)
        db.session.commit()

        flash('Feedback submitted. Thank you!', 'success')
        from utils.internal_next import normalize_internal_next
        next_url = normalize_internal_next(request.form.get('next'))
        return redirect(next_url or url_for('agents_dashboard.dashboard'))

    return render_template(
        'agents/feedback_form.html',
        agent_name=agent_name,
        agent_display=agent_display,
        next_url=request.args.get('next') or request.referrer or url_for('agents_dashboard.dashboard')
    )

