"""
Orchestrator Dashboard Routes
============================
UI for running the alert orchestrator and reviewing decisions.
Creates one consolidated alert per client based on all agent issues + client context.
"""

import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func, desc, case

from extensions import db
from models import DataIntegrityIssue, AgentRun, OrchestratorFeedback, Client

try:
    from alert_system_models import Alert
    ALERT_AVAILABLE = True
except Exception:
    Alert = None
    ALERT_AVAILABLE = False

logger = logging.getLogger(__name__)

orchestrator_bp = Blueprint("orchestrator", __name__, url_prefix="/orchestrator")
orchestrator_bp.strict_slashes = False


@orchestrator_bp.before_request
def _enforce_orchestrator_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


@orchestrator_bp.route("/")
@login_required
def dashboard():
    # Summary: clients with open issues
    issue_count_col = func.count(DataIntegrityIssue.id).label("issue_count")
    open_clients = db.session.query(
        DataIntegrityIssue.client_id,
        Client.name.label("client_name"),
        issue_count_col,
        func.sum(
            case((DataIntegrityIssue.severity == "critical", 1), else_=0)
        ).label("critical_count"),
        func.sum(
            case((DataIntegrityIssue.alert_id.is_(None), 1), else_=0)
        ).label("unlinked_count"),
    ).join(Client).filter(
        DataIntegrityIssue.status.in_(["open", "baseline"])
    ).group_by(
        DataIntegrityIssue.client_id, Client.name
    ).order_by(
        desc(issue_count_col)
    ).limit(100).all()

    # Latest orchestrator run
    last_run = AgentRun.query.filter_by(agent_name="alert_orchestrator").order_by(desc(AgentRun.started_at)).first()

    # Recent orchestrator alerts (last 7 days)
    recent_alerts = []
    if ALERT_AVAILABLE:
        recent_alerts = Alert.query.filter(
            Alert.alert_type == "agent_orchestrated",
            Alert.created_at >= datetime.utcnow() - timedelta(days=7),
        ).order_by(desc(Alert.created_at)).limit(20).all()

    # Open issues without an alert linkage (needs orchestration)
    unlinked_count = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.status.in_(["open", "baseline"]),
        DataIntegrityIssue.alert_id.is_(None),
    ).count()

    # Recent feedback
    recent_feedback = OrchestratorFeedback.query.order_by(desc(OrchestratorFeedback.created_at)).limit(10).all()

    return render_template(
        "orchestrator/dashboard.html",
        open_clients=open_clients,
        last_run=last_run,
        recent_alerts=recent_alerts,
        unlinked_count=unlinked_count,
        recent_feedback=recent_feedback,
        alerts_available=ALERT_AVAILABLE,
    )


@orchestrator_bp.route("/run", methods=["POST"])
@login_required
def run_orchestrator():
    if not ALERT_AVAILABLE:
        flash("Alert system not available in this environment.", "error")
        return redirect(url_for("orchestrator.dashboard"))

    client_id = request.form.get("client_id", type=int)
    try:
        from agents import AlertOrchestrator

        agent = AlertOrchestrator()
        run_id = agent.orchestrate_alerts(client_ids=[client_id] if client_id else None, user_id=current_user.id)
        flash(f"Orchestrator run completed: {run_id}", "success")
    except Exception as e:
        logger.error(f"Error running orchestrator: {e}", exc_info=True)
        flash(f"Error running orchestrator: {e}", "error")

    if client_id:
        return redirect(url_for("orchestrator.client_detail", client_id=client_id))
    return redirect(url_for("orchestrator.dashboard"))


@orchestrator_bp.route("/client/<int:client_id>")
@login_required
def client_detail(client_id: int):
    """
    Client drilldown for orchestrator:
    - unlinked issues (not yet orchestrated)
    - linked issues (already part of an orchestrated alert)
    - recent orchestrated alerts + feedback
    """
    client = Client.query.get_or_404(client_id)

    unlinked_issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.client_id == client_id,
        DataIntegrityIssue.status.in_(["open", "baseline"]),
        DataIntegrityIssue.alert_id.is_(None),
    ).order_by(desc(DataIntegrityIssue.severity), desc(DataIntegrityIssue.detected_at)).all()

    linked_issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.client_id == client_id,
        DataIntegrityIssue.status.in_(["open", "baseline"]),
        DataIntegrityIssue.alert_id.isnot(None),
    ).order_by(desc(DataIntegrityIssue.alert_id), desc(DataIntegrityIssue.detected_at)).all()

    alerts = []
    alerts_by_id = {}
    if ALERT_AVAILABLE:
        alert_ids = sorted({i.alert_id for i in linked_issues if i.alert_id})
        if alert_ids:
            alerts = Alert.query.filter(
                Alert.id.in_(alert_ids)
            ).order_by(desc(Alert.created_at)).all()
            alerts_by_id = {a.id: a for a in alerts}

    # Group linked issues by alert_id for display
    linked_by_alert = {}
    for issue in linked_issues:
        linked_by_alert.setdefault(issue.alert_id, []).append(issue)

    return render_template(
        "orchestrator/client_detail.html",
        client=client,
        unlinked_issues=unlinked_issues,
        linked_by_alert=linked_by_alert,
        alerts_by_id=alerts_by_id,
        alerts_available=ALERT_AVAILABLE,
    )


@orchestrator_bp.route("/feedback", methods=["POST"])
@login_required
def submit_feedback():
    """Submit feedback about an orchestrated alert decision."""
    alert_id = request.form.get("alert_id", type=int)
    client_id = request.form.get("client_id", type=int)
    feedback_type = request.form.get("feedback_type")
    feedback_text = request.form.get("feedback_text", "")
    suggested_improvement = request.form.get("suggested_improvement", "")
    logic_documentation = request.form.get("logic_documentation", "")

    if not alert_id or not client_id or not feedback_type:
        flash("Missing required feedback fields.", "error")
        return redirect(url_for("orchestrator.dashboard"))

    fb = OrchestratorFeedback(
        alert_id=alert_id,
        client_id=client_id,
        feedback_type=feedback_type,
        feedback_text=feedback_text,
        suggested_improvement=suggested_improvement,
        logic_documentation=logic_documentation,
        created_by=current_user.id,
    )
    db.session.add(fb)
    db.session.commit()

    flash("Feedback submitted. Thank you!", "success")
    return redirect(url_for("orchestrator.dashboard"))

