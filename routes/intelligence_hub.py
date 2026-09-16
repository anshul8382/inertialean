"""
Intelligence Hub Routes.
Unified management surface for agent issues, alerts, and AI-assisted actions.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from statistics import mean
from typing import Any, Dict

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for, send_file
from flask_login import current_user, login_required
from utils.internal_next import safe_referrer_path

from extensions import db
from models import (
    AgentFeedback,
    AgentRun,
    Client,
    ClientBehaviorPattern,
    DataIntegrityIssue,
    PriceAccuracyFinding,
    User,
)

logger = logging.getLogger(__name__)
hub_bp = Blueprint("hub", __name__, url_prefix="/hub")


@hub_bp.before_request
def _enforce_hub_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()


from agents.registry import AGENT_CATEGORIES, AGENT_DISPLAY_NAMES

PPM_CATEGORY = "PORTFOLIO_PERFORMANCE"


def handle_errors(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as exc:
            logger.error("Hub error in %s: %s", f.__name__, str(exc), exc_info=True)
            flash(f"An error occurred: {str(exc)}", "error")
            return redirect(url_for("hub.dashboard"))

    return decorated


def _severity_rank(sev):
    return {"critical": 3, "warning": 2, "info": 1}.get(sev, 0)


def _count_active_alerts_for_user(user) -> int:
    """Match visibility rules used on the main alerts list (managers/ops/admin see all)."""
    try:
        from alert_system_models import Alert
    except Exception:
        return 0
    q = Alert.query.filter_by(status="active")
    if getattr(user, "is_manager", False) or getattr(user, "is_ops_manager", False) or getattr(user, "is_admin", False):
        return q.count()
    return q.filter_by(user_id=user.id).count()


def _get_agent_stats():
    agents = []
    for agent_name, categories in AGENT_CATEGORIES.items():
        last_run = AgentRun.query.filter_by(agent_name=agent_name).order_by(AgentRun.started_at.desc()).first()

        if agent_name == "alert_orchestrator":
            primary_count = 0
            critical_count = 0
            try:
                from alert_system_models import Alert

                aq = Alert.query.filter_by(status="active")
                primary_count = aq.count()
                critical_count = aq.filter(Alert.severity == "critical").count()
            except Exception:
                pass
            agents.append(
                {
                    "name": agent_name,
                    "display": AGENT_DISPLAY_NAMES.get(agent_name, agent_name),
                    "last_run": last_run,
                    "primary_count": primary_count,
                    "critical_count": critical_count,
                    "metric_kind": "alerts",
                }
            )
            continue

        query = DataIntegrityIssue.query.filter(DataIntegrityIssue.status.in_(["open", "baseline"]))
        if categories:
            query = query.filter(DataIntegrityIssue.check_category.in_(categories))
        issue_count = query.count()
        critical_count = query.filter(DataIntegrityIssue.severity == "critical").count()
        agents.append(
            {
                "name": agent_name,
                "display": AGENT_DISPLAY_NAMES.get(agent_name, agent_name),
                "last_run": last_run,
                "primary_count": issue_count,
                "critical_count": critical_count,
                "metric_kind": "issues",
            }
        )
        if agent_name == "task_assignment_agent":
            from models import ReviewWorkflow

            unassigned_issues = DataIntegrityIssue.query.filter(
                DataIntegrityIssue.status.in_(["open", "baseline"]),
                DataIntegrityIssue.assigned_to.is_(None),
            ).count()
            unassigned_reviews = ReviewWorkflow.query.filter(
                ReviewWorkflow.assigned_to.is_(None),
                ReviewWorkflow.status.in_(["initiated", "sent", "meeting"]),
            ).count()
            agents[-1]["primary_count"] = unassigned_issues + unassigned_reviews
            agents[-1]["metric_kind"] = "unassigned"
    return agents


def _attach_agent_detail_urls(agents):
    """Deep links for each monitoring card (existing UIs)."""
    endpoints = {
        "data_integrity_manager": "data_integrity.dashboard",
        "rec_exec_monitor": "rec_execution.dashboard",
        "portfolio_performance_monitor": "hub.performance_view",
        "alert_orchestrator": "alerts.alert_dashboard",
        "task_assignment_agent": "task_assignment_rules.list_rules",
    }
    for agent in agents:
        ep = endpoints.get(agent["name"], "hub.issue_queue")
        try:
            agent["detail_url"] = url_for(ep)
        except Exception:
            agent["detail_url"] = url_for("hub.issue_queue")
    return agents


def _get_alerts():
    try:
        from alert_system_models import Alert

        return Alert.query.filter_by(status="active").order_by(Alert.created_at.desc()).limit(10).all(), Alert
    except Exception:
        return [], None


@hub_bp.route("/")
@login_required
@handle_errors
def dashboard():
    """Hub branch selector page."""
    open_issues_count = DataIntegrityIssue.query.filter(DataIntegrityIssue.status.in_(["open", "baseline"])).count()
    active_alerts, _ = _get_alerts()
    return render_template(
        "hub/dashboard.html",
        open_issues_count=open_issues_count,
        active_alerts_count=len(active_alerts),
    )


@hub_bp.route("/airflow-runs", methods=["GET", "POST"])
@login_required
@handle_errors
def airflow_runs_dashboard():
    """Scheduled jobs (Airflow) — last run status, failure hints, alert sync."""
    from services.airflow_run_insights_service import (
        build_airflow_runs_template_context,
        sync_airflow_failure_alerts,
    )

    path = current_app.config.get("AIRFLOW_METADATA_DB_PATH")
    airflow_home = current_app.config.get("AIRFLOW_HOME")
    if request.method == "POST" and request.form.get("action") == "sync_alerts":
        try:
            sync_result = sync_airflow_failure_alerts(
                db.session, current_user.id, path, airflow_home=airflow_home
            )
            flash(
                "Alerts synced: "
                f"{sync_result.get('created', 0)} created, "
                f"{sync_result.get('updated', 0)} updated, "
                f"{sync_result.get('resolved', 0)} auto-resolved after success.",
                "success",
            )
        except Exception as exc:
            logger.error("Airflow alert sync failed: %s", exc, exc_info=True)
            flash(f"Could not sync alerts: {exc}", "error")
        return redirect(url_for("hub.airflow_runs_dashboard"))

    ctx = build_airflow_runs_template_context(current_app.config)
    ctx["runs_url"] = url_for("hub.airflow_runs_dashboard")
    return render_template("hub/airflow_runs.html", **ctx)


@hub_bp.route("/operational")
@login_required
@handle_errors
def operational_dashboard():
    """Operational Intelligence dashboard."""
    agents = _attach_agent_detail_urls(_get_agent_stats())

    my_tasks_count = 0
    tasks_url = None
    if current_app.config.get("NAV_TASKS_ENABLED", False):
        try:
            from services.dashboard_open_tasks_service import count_open_ops_tasks_for_user

            tasks_url = url_for("tasks.list_tasks")
            my_tasks_count = count_open_ops_tasks_for_user(current_user.id)
        except Exception:
            tasks_url = None
            my_tasks_count = 0

    alerts_list_url = None
    alerts_dash_url = None
    try:
        alerts_list_url = url_for("alerts.list_alerts")
    except Exception:
        pass
    try:
        alerts_dash_url = url_for("alerts.alert_dashboard")
    except Exception:
        pass
    my_active_alerts_count = _count_active_alerts_for_user(current_user)

    priority_issues = (
        DataIntegrityIssue.query.filter(DataIntegrityIssue.status.in_(["open", "baseline"]))
        .order_by(
            db.case({"critical": 3, "warning": 2, "info": 1}, value=DataIntegrityIssue.severity).desc(),
            DataIntegrityIssue.detected_at.asc(),
        )
        .limit(20)
        .all()
    )

    from sqlalchemy import func

    hotlist = (
        db.session.query(
            DataIntegrityIssue.client_id,
            func.count(DataIntegrityIssue.id).label("issue_count"),
            func.sum(db.case({"critical": 3, "warning": 2, "info": 1}, value=DataIntegrityIssue.severity)).label(
                "severity_score"
            ),
        )
        .filter(DataIntegrityIssue.status.in_(["open", "baseline"]))
        .group_by(DataIntegrityIssue.client_id)
        .order_by(db.text("severity_score DESC"))
        .limit(10)
        .all()
    )
    client_map = {c.id: c for c in Client.query.filter(Client.id.in_([h.client_id for h in hotlist])).all()}
    hotlist_data = [
        {"client": client_map.get(h.client_id), "client_id": h.client_id, "issue_count": h.issue_count}
        for h in hotlist
        if h.client_id
    ]

    active_alerts, _ = _get_alerts()
    ppm_issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == PPM_CATEGORY, DataIntegrityIssue.status.in_(["open", "baseline"])
    ).all()
    ppm_systemic = 0
    ppm_client_specific = 0
    if ppm_issues:
        avg_under = mean((i.details or {}).get("underperformance_pct", 0) for i in ppm_issues)
        for issue in ppm_issues:
            excess = (issue.details or {}).get("underperformance_pct", 0) - avg_under
            if excess > 3.0:
                ppm_client_specific += 1
            else:
                ppm_systemic += 1

    from services.codebase_backup_service import list_recent_backups, read_backup_status

    codebase_backup_status = read_backup_status(current_app.config)
    codebase_recent = list_recent_backups(current_app.config, limit=10)

    return render_template(
        "hub/operational_dashboard.html",
        agents=agents,
        my_tasks_count=my_tasks_count,
        tasks_url=tasks_url,
        alerts_list_url=alerts_list_url,
        alerts_dash_url=alerts_dash_url,
        my_active_alerts_count=my_active_alerts_count,
        priority_issues=priority_issues,
        hotlist_data=hotlist_data,
        active_alerts=active_alerts,
        ppm_systemic=ppm_systemic,
        ppm_client_specific=ppm_client_specific,
        ai_suggestions=session.get("hub_ai_suggestions", []),
        codebase_backup_status=codebase_backup_status,
        codebase_recent=codebase_recent,
    )


def execute_operational_pipeline(refresh: bool) -> Dict[str, Any]:
    """
    Run alert/task reconcile + workflow SLA backfill + task auto-close;
    when refresh=True, also run unified integrity refresh (incremental scope).
    """
    from services.alert_task_reconcile_service import run_full_reconcile

    stats = run_full_reconcile(
        dry_run=False,
        workflow_backfill=True,
        run_task_auto_close=True,
        refresh_assignment_and_alerts=False,
    )
    if refresh:
        from services.client_integrity_refresh_service import refresh_integrity_scope

        stats["integrity_refresh"] = refresh_integrity_scope(
            mode="incremental",
            actor_user_id=None,
        )
    return stats


def format_operational_pipeline_flash_message(stats: Dict[str, Any], refresh: bool) -> str:
    wf = stats.get("workflow_backfill") or {}
    ir = stats.get("integrity_refresh")
    msg = (
        f"Stale issue tasks closed: {stats.get('ops_tasks_closed_stale_issue', 0)}. "
        f"Alerts resolved (linked / no open client issues): "
        f"{stats.get('alerts_resolved_all_linked_closed', 0)} / "
        f"{stats.get('alerts_resolved_no_open_client_issues', 0)}. "
        f"Issue alert_id cleared: {stats.get('issues_cleared_alert_id', 0)}. "
        f"Workflow backfill — SLA alerts: {wf.get('workflow_sla_alerts', 0)}, "
        f"tasks closed: {wf.get('workflow_tasks_closed', 0)}, "
        f"issues resolved: {wf.get('workflow_issues_resolved', 0)}. "
        f"Task auto-close pass: {stats.get('task_auto_close_extra', 0)}."
    )
    if isinstance(ir, dict):
        msg += (
            f" Integrity refresh: {ir.get('clients_ok', 0)}/"
            f"{ir.get('clients_requested', 0)} clients."
        )
    elif refresh:
        msg += " Integrity refresh: requested."
    return msg


@hub_bp.route("/investment")
@login_required
@handle_errors
def investment_dashboard():
    """Tax, capital gains, pseudo trades, exports, and performance timeline (same client access as review tools)."""
    from access_control import get_accessible_clients

    clients = get_accessible_clients()
    return render_template("hub/investment_dashboard.html", clients=clients)


@hub_bp.route("/system")
@login_required
@handle_errors
def system_dashboard():
    """System Intelligence — links to governance, roles, SLA, Airflow, rec SLA."""
    return render_template("hub/system_dashboard.html")


def _hub_price_accuracy_access_ok():
    user = current_user
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(
        getattr(user, "is_admin", False)
        or getattr(user, "is_manager", False)
        or getattr(user, "is_ops_manager", False)
    )


def _price_accuracy_verify_flash_bits(res: Dict[str, Any]) -> str:
    bits = [f"{res.get('verified', 0)} auto-verified"]
    if res.get("no_sheet_data"):
        bits.append(f"{res['no_sheet_data']} no GOOGLEFINANCE data")
    if res.get("mismatch"):
        bits.append(f"{res['mismatch']} mismatch (DB jump ≠ sheet jump)")
    if res.get("skipped_already_acked"):
        bits.append(f"{res['skipped_already_acked']} skipped (already verified)")
    if res.get("message"):
        bits.append(str(res["message"]))
    samples = res.get("mismatch_samples") or []
    if samples:
        ex = []
        for s in samples[:3]:
            ex.append(
                f"{s.get('symbol')} DB {s.get('db_pct')}% vs sheet {s.get('sheet_pct')}%"
            )
        bits.append("e.g. " + "; ".join(ex))
    return ", ".join(bits)


@hub_bp.route("/system/price-accuracy", methods=["GET"])
@login_required
@handle_errors
def price_accuracy_dashboard():
    from services.google_sheets_cleanup import default_cleanup_worksheet_names
    from services.price_accuracy_service import group_findings_by_security_for_hub, latest_run_snapshot

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    snapshot = latest_run_snapshot()
    spike_groups = []
    missing_groups = []
    possible_holidays = []
    if snapshot:
        from services.price_accuracy_holiday_service import load_sparse_candidates

        findings = snapshot.get("findings") or []
        spikes_list = [f for f in findings if f.get("kind") == "SPIKE"]
        missing_list = [f for f in findings if f.get("kind") == "MISSING"]
        spike_groups = group_findings_by_security_for_hub(spikes_list)
        missing_groups = group_findings_by_security_for_hub(missing_list)
        possible_holidays = load_sparse_candidates()
    token_hint = ""
    cfg = getattr(current_app, "config", None) or {}
    if (cfg.get("PRICE_ACCURACY_API_TOKEN") or "").strip():
        token_hint = "Configured — use the same token in Apps Script headers as X-Price-Accuracy-Token."
    else:
        token_hint = "Set PRICE_ACCURACY_API_TOKEN to enable webhook calls from Apps Script."
    webhook_base = ""
    try:
        webhook_base = request.url_root.rstrip("/") + "/api/v1/price-accuracy/webhook/"
    except Exception:
        webhook_base = "/api/v1/price-accuracy/webhook/"
    sheet_tol = float(current_app.config.get("PRICE_ACCURACY_SHEETS_MATCH_TOLERANCE_PCT", 1.0))
    sheet_cleanup_tabs_label = ", ".join(default_cleanup_worksheet_names())
    return render_template(
        "hub/price_accuracy_dashboard.html",
        snapshot=snapshot,
        spike_groups=spike_groups,
        missing_groups=missing_groups,
        token_hint=token_hint,
        webhook_base=webhook_base,
        sheet_tolerance_pct=sheet_tol,
        sheet_cleanup_tabs_label=sheet_cleanup_tabs_label,
        possible_holidays=possible_holidays,
    )


@hub_bp.route("/system/price-accuracy/run-scan", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_run_scan():
    from services.price_accuracy_service import run_price_accuracy_scan

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    pct_raw = request.form.get("threshold_pct") or request.args.get("threshold_pct") or "10"
    try:
        pct = float(pct_raw)
    except ValueError:
        pct = 10.0
    try:
        stats = run_price_accuracy_scan(threshold_pct=pct)
        skipped = int(stats.get("skipped_closed") or 0)
        skip_bit = (
            f" {skipped} weekday(s) where most names have no close — confirm below if those were market holidays."
            if skipped
            else ""
        )
        flash(
            f"Scan complete: {stats['securities_scanned']} securities, "
            f"{stats['spikes']} spikes, {stats['missing']} missing trade dates, {stats['zeros']} zero-priced rows."
            + skip_bit,
            "success",
        )
    except Exception as exc:
        flash(f"Scan failed: {exc}", "error")
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/mark-holiday", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_mark_holiday():
    from services.price_accuracy_service import mark_market_holiday_and_close_findings

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    raw = (request.form.get("holiday_date") or "").strip()
    if not raw:
        flash("Choose a date to mark as a market holiday.", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    res = mark_market_holiday_and_close_findings(raw, note="user_confirmed", source="ui")
    if not res.get("ok"):
        flash("Could not mark that date as a holiday.", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    flash(
        f"Marked {res.get('date')} as a market holiday. Closed {res.get('closed', 0)} missing/zero finding(s). "
        "Later scans will not flag that date.",
        "success",
    )
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/bulk-ack-spikes-by-security", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_bulk_ack_spikes_by_security():
    from services.price_accuracy_service import acknowledge_spikes_bulk, spike_finding_ids_latest_run_for_security

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    sid_raw = request.form.get("security_id")
    try:
        sid = int(sid_raw)
    except (TypeError, ValueError):
        flash("Missing or invalid security for bulk verify.", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    ids = spike_finding_ids_latest_run_for_security(sid)
    if not ids:
        flash(f"No spike rows in the current scan for security #{sid}.", "info")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    stats = acknowledge_spikes_bulk(ids, user_id=current_user.id, source="ui_bulk_symbol")
    flash(
        f"Security #{sid}: bulk spike verify — {stats['new_ack']} newly recorded, "
        f"{stats['already_acked']} already verified ({len(ids)} rows).",
        "success",
    )
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/verify-from-sheets-by-security", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_verify_from_sheets_by_security():
    from services.price_accuracy_service import spike_finding_ids_latest_run_for_security, verify_spikes_from_google_sheets

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    sid_raw = request.form.get("security_id")
    try:
        sid = int(sid_raw)
    except (TypeError, ValueError):
        flash("Missing or invalid security.", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    ids = spike_finding_ids_latest_run_for_security(sid)
    if not ids:
        flash(f"No spike rows in the current scan for security #{sid}.", "info")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    try:
        res = verify_spikes_from_google_sheets(finding_ids=[str(i) for i in ids], user_id=current_user.id)
    except FileNotFoundError as exc:
        flash(f"Google Sheets verification needs service_account.json: {exc}", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    except Exception as exc:
        logger.warning("verify_from_sheets_by_security: %s", exc, exc_info=True)
        flash(f"Verify from Google Sheet failed: {exc}", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    msg = (
        f"Security #{sid} — Google Sheet vs DB ({res.get('tolerance_pct', 0):g}%): "
        + _price_accuracy_verify_flash_bits(res)
    )
    category = (
        "success"
        if res.get("verified")
        else ("warning" if res.get("mismatch") or res.get("no_sheet_data") else "info")
    )
    flash(msg, category)
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/bulk-ack-spikes", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_bulk_ack_spikes():
    from services.price_accuracy_service import acknowledge_spikes_bulk

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    ids = request.form.getlist("finding_id")
    if not ids:
        flash("Select at least one spike (checkboxes), or use Verify from Google Sheet for all.", "warning")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    stats = acknowledge_spikes_bulk(ids, user_id=current_user.id, source="ui_bulk")
    flash(
        f"Bulk verified: {stats['new_ack']} newly recorded, "
        f"{stats['already_acked']} already verified, out of {stats['requested']} selected.",
        "success",
    )
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/verify-from-sheets", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_verify_from_sheets():
    from services.price_accuracy_service import verify_spikes_from_google_sheets

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    all_flag = request.form.get("all_spikes") == "1"
    ids = request.form.getlist("finding_id")
    if not all_flag and not ids:
        flash(
            "Select spikes with checkboxes, or click “Verify all from Google Sheet” without selecting.",
            "warning",
        )
        return redirect(url_for("hub.price_accuracy_dashboard"))
    try:
        if all_flag:
            res = verify_spikes_from_google_sheets(
                all_spikes_for_latest_run=True,
                user_id=current_user.id,
            )
        else:
            res = verify_spikes_from_google_sheets(finding_ids=ids, user_id=current_user.id)
    except FileNotFoundError as exc:
        flash(f"Google Sheets verification needs service_account.json: {exc}", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    except Exception as exc:
        logger.warning("verify_from_sheets: %s", exc, exc_info=True)
        flash(f"Verify from Google Sheet failed: {exc}", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))

    if res.get("error") == "no_price_accuracy_run":
        flash("Run a scan first, then retry verification from Sheets.", "warning")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    msg = (
        f"Google Sheet vs DB ({res.get('tolerance_pct', 0):g}% — price level or same % move): "
        + _price_accuracy_verify_flash_bits(res)
    )
    if res.get("verified"):
        category = "success"
    elif res.get("mismatch") or res.get("no_sheet_data"):
        category = "warning"
    else:
        category = "info"
    flash(msg, category)
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/full-sheets-pipeline", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_full_sheets_pipeline():
    """
    One run: sync all Prices from Google Sheet → DB (same as cron price_update_sheets),
    refresh the accuracy scan, then GOOGLEFINANCE-verify every spike in the new report.
    """
    from api.v1.cron_jobs import run_price_update_sheets
    from services.price_accuracy_service import run_price_accuracy_scan, verify_spikes_from_google_sheets

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))

    pct_raw = request.form.get("threshold_pct") or "10"
    try:
        pct = float(pct_raw)
    except ValueError:
        pct = 10.0

    raw_out = run_price_update_sheets()
    body = {}
    status_code = 500
    if isinstance(raw_out, tuple) and len(raw_out) >= 2:
        status_code = int(raw_out[1])
        try:
            body = raw_out[0].get_json(silent=True) or {}
        except Exception:
            body = {}

    sync_ok = status_code == 200 and body.get("success")
    pdata = body.get("data") or {}

    if not sync_ok:
        flash(
            (
                "Google Sheet prices import failed "
                f"({body.get('message') or body.get('error') or status_code}); "
                "scan and spike verification were not run."
            ),
            "error",
        )
        return redirect(url_for("hub.price_accuracy_dashboard"))

    price_part = (
        f"Prices (Sheet→DB): {pdata.get('updated_count', 0)} securities updated, "
        f"{pdata.get('historical_saved_count', 0)} historical rows saved "
        "(only during IST close window)."
    )

    try:
        scan_stats = run_price_accuracy_scan(threshold_pct=pct)
        scan_part = (
            f"Scan ({pct:g}% spike threshold): "
            f"{scan_stats['securities_scanned']} symbols, "
            f"{scan_stats['spikes']} spikes, "
            f"{scan_stats['missing']} missing dates, "
            f"{scan_stats['zeros']} zero/invalid."
        )
    except Exception as exc:
        logger.error("full_sheets_pipeline scan: %s", exc, exc_info=True)
        flash(f"{price_part} Scan failed: {exc}", "warning")
        return redirect(url_for("hub.price_accuracy_dashboard"))

    try:
        res = verify_spikes_from_google_sheets(
            all_spikes_for_latest_run=True,
            user_id=current_user.id,
        )
    except FileNotFoundError as exc:
        flash(f"{price_part} {scan_part} GOOGLEFINANCE verify skipped: {exc}", "warning")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    except Exception as exc:
        logger.error("full_sheets_pipeline verify: %s", exc, exc_info=True)
        flash(f"{price_part} {scan_part} Verify failed: {exc}", "warning")
        return redirect(url_for("hub.price_accuracy_dashboard"))

    if res.get("error") == "no_price_accuracy_run":
        flash(f"{price_part} {scan_part} Verify could not load the new run (unexpected).", "warning")
        return redirect(url_for("hub.price_accuracy_dashboard"))

    vrf = (
        f"Verify all spikes via Sheet ({res.get('tolerance_pct', 0):g}%): "
        + _price_accuracy_verify_flash_bits(res)
    )

    full_flash = f"{price_part} {scan_part} {vrf}"

    if res.get("verified"):
        category = "success"
    elif res.get("mismatch") or res.get("no_sheet_data"):
        category = "warning"
    else:
        category = "info"

    flash(full_flash, category)
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/clean-google-sheet-tabs", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_clean_google_sheet_tabs():
    """Remove accumulated data rows (keep row 1 header) on configured scratchpad tabs."""
    import os

    from services.google_sheets_cleanup import cleanup_workbook_tabs, default_cleanup_worksheet_names

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))

    tabs = list(default_cleanup_worksheet_names())
    if request.form.get("include_accuracy_queue_tab") == "1":
        qtab = (
            os.environ.get("GOOGLE_SHEETS_PRICE_ACCURACY_WORKSHEET", "").strip()
            or "PriceAccuracyQueue"
        )
        if qtab not in tabs:
            tabs.append(qtab)

    try:
        meta = cleanup_workbook_tabs(tabs, header_rows=1, chunk_size=600)
    except FileNotFoundError as exc:
        flash(f"Sheet cleanup requires service_account.json: {exc}", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    except Exception as exc:
        logger.exception("clean_google_sheet_tabs: %s", exc)
        flash(f"Sheet cleanup failed: {exc}", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))

    parts = []
    for name, cnt in sorted((meta.get("tabs") or {}).items()):
        parts.append(f"{name}: −{cnt} data row(s)")

    if meta.get("skipped_missing"):
        parts.append(f"missing tabs skipped: {', '.join(meta['skipped_missing'])}")

    if meta.get("skipped_forbidden"):
        flash(
            f"Refused to clean protected tabs (Prices / Nifty): {', '.join(meta['skipped_forbidden'])}",
            "warning",
        )

    flash(" · ".join(parts) if parts else "No matching tabs cleaned.", "success")
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/ack-spike", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_ack_spike():
    from services.price_accuracy_service import acknowledge_spike

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    finding_id = request.form.get("finding_id")
    if not finding_id:
        flash("Missing finding.", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))

    finding = PriceAccuracyFinding.query.filter_by(id=int(finding_id), kind="SPIKE").first()
    if not finding or not finding.date_prev or not finding.date_next:
        flash("SPIKE finding not found.", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))

    ack_row = acknowledge_spike(
        security_id=finding.security_id,
        date_prev=finding.date_prev,
        date_next=finding.date_next,
        source="ui",
        user_id=current_user.id,
        notes=(request.form.get("notes") or "")[:500] or None,
    )
    if not ack_row:
        flash("Could not record verification (try again); if this persists check database constraints.", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    flash(
        "Verified — removed from report and skipped on future scans.",
        "success",
    )
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/system/price-accuracy/push-sheets-queue", methods=["POST"])
@login_required
@handle_errors
def price_accuracy_push_sheets_queue():
    from services.price_accuracy_service import findings_dicts_filtered_for_push, latest_run_snapshot
    from services.price_accuracy_sheets import push_queue_from_findings

    if not _hub_price_accuracy_access_ok():
        flash("Access denied.", "error")
        return redirect(url_for("hub.system_dashboard"))
    snap = latest_run_snapshot()
    if not snap:
        flash("Run a scan first so there are rows to publish.", "warning")
        return redirect(url_for("hub.price_accuracy_dashboard"))
    findings = snap.get("findings") or []
    push_mode = (request.form.get("push_mode") or "full").strip()

    filtered = findings

    if push_mode == "subset_missing":
        ids_raw = request.form.getlist("finding_id_missing")
        if not ids_raw:
            flash("Select at least one missing-date row (checkboxes), then push again.", "warning")
            return redirect(url_for("hub.price_accuracy_dashboard"))
        try:
            id_list = [int(x) for x in ids_raw]
        except ValueError:
            flash("Invalid finding ids for push.", "error")
            return redirect(url_for("hub.price_accuracy_dashboard"))
        filtered = findings_dicts_filtered_for_push(findings, finding_ids=id_list)
        if len(filtered) < len(id_list):
            flash("Some checkboxes referred to stale ids; publishing the rows that matched the latest scan.", "warning")
    elif push_mode == "by_security":
        sid_raw = request.form.get("security_id")
        kind_scope = (request.form.get("push_kind") or "").strip().upper()
        if not sid_raw:
            flash("Missing security for scoped push.", "error")
            return redirect(url_for("hub.price_accuracy_dashboard"))
        try:
            sid = int(sid_raw)
        except (TypeError, ValueError):
            flash("Invalid security_id for push.", "error")
            return redirect(url_for("hub.price_accuracy_dashboard"))
        k = kind_scope if kind_scope in ("MISSING", "SPIKE", "ZERO") else None
        filtered = findings_dicts_filtered_for_push(findings, security_id=sid, kind=k)

    elif push_mode == "full":
        filtered = list(findings)
    else:
        flash("Unsupported push_mode.", "error")
        return redirect(url_for("hub.price_accuracy_dashboard"))

    if not filtered:
        flash(
            "No rows match this push (selected ids, symbol filter, or kind). Nothing published.",
            "warning",
        )
        return redirect(url_for("hub.price_accuracy_dashboard"))

    findings = filtered
    try:
        meta = push_queue_from_findings(findings)
        scope_note = ""
        if push_mode == "subset_missing":
            scope_note = " (selected missing rows)"
        elif push_mode == "by_security":
            scope_note = " (filtered by symbol / kind)"
        flash(
            f"Published {meta.get('rows', 0)} row(s) to worksheet “{meta.get('worksheet')}”{scope_note}. "
            f"Open the workbook to correct prices or mark verified_ok.",
            "success",
        )
    except FileNotFoundError as exc:
        flash(f"Spreadsheet publish failed — service_account.json missing?: {exc}", "error")
    except Exception as exc:
        flash(f"Spreadsheet publish failed: {exc}", "error")
    return redirect(url_for("hub.price_accuracy_dashboard"))


@hub_bp.route("/business")
@login_required
@handle_errors
def business_dashboard():
    """Business Intelligence — analytics, practice, account management, referrals."""
    return render_template("hub/business_dashboard.html")


@hub_bp.route("/investment/reports/client-capital-gains-bajfinance.csv")
@login_required
@handle_errors
def client_capital_gains_bajfinance_report_csv():
    """
    Generate and download the Client Capital Gains + Bajaj Finance + Top Gainers report.
    """
    if not (getattr(current_user, "is_manager", False) or getattr(current_user, "is_advisor", False)):
        flash("Access denied.", "error")
        return redirect(url_for("hub.investment_dashboard"))

    from services.client_capital_gains_bajfinance_report_service import generate_csv_report

    out_path, meta = generate_csv_report(out_dir=current_app.config.get("TMP_DIR") or "tmp")
    fy_start = meta.get("fy_start")
    fy_end = meta.get("fy_end")
    fname = f"client_capital_gains_bajfinance_{getattr(fy_start, 'year', 'fy')}_{getattr(fy_end, 'year', 'fy')}.csv"
    from services.audit_service import log_data_export

    log_data_export(
        "client_capital_gains_bajfinance_csv",
        details={
            "fy_start": str(fy_start),
            "fy_end": str(fy_end),
            "client_count": meta.get("client_count"),
        },
    )
    return send_file(out_path, as_attachment=True, download_name=fname, mimetype="text/csv")


@hub_bp.route("/client/<int:client_id>")
@login_required
@handle_errors
def client_view(client_id):
    client = Client.query.get_or_404(client_id)
    issues_by_agent = {}
    for agent_name, categories in AGENT_CATEGORIES.items():
        if not categories:
            continue
        issues = (
            DataIntegrityIssue.query.filter(
                DataIntegrityIssue.client_id == client_id,
                DataIntegrityIssue.check_category.in_(categories),
                DataIntegrityIssue.status.in_(["open", "baseline"]),
            )
            .order_by(db.case({"critical": 3, "warning": 2, "info": 1}, value=DataIntegrityIssue.severity).desc())
            .all()
        )
        if issues:
            issues_by_agent[agent_name] = issues

    client_alerts, Alert = [], None
    try:
        from alert_system_models import Alert as AlertModel

        Alert = AlertModel
        client_alerts = (
            AlertModel.query.filter_by(client_id=client_id, status="active").order_by(AlertModel.created_at.desc()).all()
        )
    except Exception:
        pass

    patterns = ClientBehaviorPattern.query.filter_by(client_id=client_id).all()
    users = User.query.filter_by(is_active=True).order_by(User.username).all()

    open_tasks = []
    try:
        from models import OpsTask

        open_tasks = (
            OpsTask.query.filter_by(client_id=client_id)
            .filter(OpsTask.status.in_(["pending", "in_progress", "snoozed"]))
            .all()
        )
    except Exception:
        open_tasks = []

    return render_template(
        "hub/client_view.html",
        client=client,
        issues_by_agent=issues_by_agent,
        client_alerts=client_alerts,
        patterns=patterns,
        open_tasks=open_tasks,
        ai_suggestions=[s for s in session.get("hub_ai_suggestions", []) if s.get("client_id") == client_id],
        users=users,
        Alert=Alert,
    )


@hub_bp.route("/issues")
@login_required
@handle_errors
def issue_queue():
    severity_filter = request.args.get("severity", "all")
    category_filter = request.args.get("category", "all")
    client_id_filter = request.args.get("client_id", type=int)
    check_filter = request.args.get("check", "all")
    page = request.args.get("page", 1, type=int)

    query = DataIntegrityIssue.query.filter(DataIntegrityIssue.status.in_(["open", "baseline"]))
    if severity_filter != "all":
        query = query.filter(DataIntegrityIssue.severity == severity_filter)
    if category_filter != "all":
        query = query.filter(DataIntegrityIssue.check_category == category_filter)
    if client_id_filter:
        query = query.filter(DataIntegrityIssue.client_id == client_id_filter)
    if check_filter != "all":
        query = query.filter(DataIntegrityIssue.check_name == check_filter)

    issues = query.order_by(
        db.case({"critical": 3, "warning": 2, "info": 1}, value=DataIntegrityIssue.severity).desc(),
        DataIntegrityIssue.detected_at.asc(),
    ).paginate(page=page, per_page=50)

    all_categories = [
        row[0]
        for row in db.session.query(DataIntegrityIssue.check_category)
        .filter(DataIntegrityIssue.status.in_(["open", "baseline"]))
        .distinct()
        .all()
    ]
    all_checks = [
        row[0]
        for row in db.session.query(DataIntegrityIssue.check_name)
        .filter(DataIntegrityIssue.status.in_(["open", "baseline"]))
        .distinct()
        .all()
    ]
    from access_control import get_accessible_clients_ordered
    all_clients = get_accessible_clients_ordered()
    return render_template(
        "hub/issue_queue.html",
        issues=issues,
        severity_filter=severity_filter,
        category_filter=category_filter,
        client_id_filter=client_id_filter,
        check_filter=check_filter,
        all_categories=sorted(all_categories),
        all_checks=sorted(all_checks),
        all_clients=all_clients,
    )


@hub_bp.route("/alerts")
@login_required
@handle_errors
def alert_center():
    status_tab = request.args.get("status", "active")
    try:
        from alert_system_models import Alert

        alerts = Alert.query.filter_by(status=status_tab).order_by(Alert.created_at.desc()).limit(50).all()
        alerts_available = True
    except Exception:
        alerts = []
        alerts_available = False

    users = User.query.filter_by(is_active=True).order_by(User.username).all()
    return render_template(
        "hub/alert_center.html",
        alerts=alerts,
        status_tab=status_tab,
        alerts_available=alerts_available,
        users=users,
    )


@hub_bp.route("/alerts/<int:alert_id>/action", methods=["POST"])
@login_required
@handle_errors
def alert_action(alert_id):
    from alert_system_models import Alert

    alert = Alert.query.get_or_404(alert_id)
    action = request.form.get("action")
    note = request.form.get("note", "")
    hours = request.form.get("hours", 24, type=int)
    escalate_to = request.form.get("escalate_to", type=int)

    if action == "acknowledge":
        alert.acknowledge(current_user.id)
    elif action == "snooze":
        alert.snooze(hours=hours)
    elif action == "resolve":
        alert.resolve(current_user.id, notes=note)
    elif action == "escalate":
        alert.escalate(escalate_to or current_user.id)
    elif action == "note" and note:
        alert.add_note(current_user.id, note)
    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "status": alert.status})
    flash("Alert updated.", "success")
    return redirect(safe_referrer_path(request.referrer) or url_for("hub.alert_center"))


@hub_bp.route("/alerts/<int:alert_id>/convert", methods=["POST"])
@login_required
@handle_errors
def convert_alert(alert_id):
    from alert_system_models import Alert

    alert = Alert.query.get_or_404(alert_id)
    target = request.form.get("target", "task")
    if target == "task":
        try:
            from models import OpsTask
        except Exception:
            flash("OpsTask model is not available in this deployment.", "error")
            return redirect(url_for("hub.alert_center"))

        deadline_str = request.form.get("deadline")
        priority = request.form.get("priority", "medium")
        assigned_to = request.form.get("assigned_to", type=int) or current_user.id
        deadline = datetime.fromisoformat(deadline_str) if deadline_str else datetime.utcnow() + timedelta(days=3)
        task = OpsTask(
            name=request.form.get("name", alert.title)[:200],
            deadline=deadline,
            client_id=alert.client_id,
            assigned_to=assigned_to,
            notes=f"Converted from Alert #{alert.id}: {(alert.description or '')[:200]}",
            priority=priority,
            created_by=current_user.id,
            status="pending",
        )
        db.session.add(task)
        db.session.flush()
        alert.resolve(current_user.id, notes=f"Converted to Task #{task.id}")
        db.session.commit()
        try:
            from services.google_calendar_service import sync_ops_task_google_calendar
            from services.google_tasks_service import sync_ops_task_google_tasks

            sync_ops_task_google_calendar(task.id)
            sync_ops_task_google_tasks(task.id)
        except Exception:
            pass
        flash(f"Alert converted to Task #{task.id}.", "success")
        return redirect(url_for("tasks.view_task", task_id=task.id))

    if target == "ticket":
        from models import ServiceTicket

        priority = request.form.get("priority", "medium")
        assigned_to = request.form.get("assigned_to", type=int) or current_user.id
        date_str = datetime.utcnow().strftime("%Y%m%d")
        count = ServiceTicket.query.filter(ServiceTicket.ticket_number.like(f"TKT-{date_str}-%")).count()
        ticket_number = f"TKT-{date_str}-{count + 1:04d}"
        sla_map = {"critical": 4, "high": 24, "medium": 72, "low": 168}
        sla_hours = sla_map.get(priority, 72)

        ticket = ServiceTicket(
            ticket_number=ticket_number,
            client_id=alert.client_id,
            title=request.form.get("title", alert.title)[:200],
            description=alert.description or alert.title,
            alert_type=alert.alert_type or "general",
            priority=priority,
            sla_hours=sla_hours,
            sla_deadline=datetime.utcnow() + timedelta(hours=sla_hours),
            assigned_to=assigned_to,
            created_by=current_user.id,
        )
        db.session.add(ticket)
        db.session.flush()
        alert.snooze(hours=sla_hours)
        alert.add_note(current_user.id, f"Raised Ticket {ticket.ticket_number}")
        db.session.commit()
        flash(f"Ticket {ticket.ticket_number} created, alert snoozed.", "success")
        return redirect(url_for("tickets.view_ticket", ticket_id=ticket.id))

    flash("Invalid conversion target.", "error")
    return redirect(url_for("hub.alert_center"))


@hub_bp.route("/performance")
@login_required
@handle_errors
def performance_view():
    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.check_category == PPM_CATEGORY, DataIntegrityIssue.status.in_(["open", "baseline"])
    ).all()
    systemic = []
    client_specific = []
    if issues:
        avg_under = mean((i.details or {}).get("underperformance_pct", 0) for i in issues)
        for issue in issues:
            details = issue.details or {}
            excess = details.get("underperformance_pct", 0) - avg_under
            enriched = {
                "issue": issue,
                "client": issue.client,
                "underperformance_pct": details.get("underperformance_pct", 0),
                "benchmark_xirr_pct": details.get("benchmark_xirr_pct", 0),
                "excess_vs_peers": round(excess, 2),
                "comparison_type": details.get("comparison_type", "full_portfolio"),
            }
            if excess > 3.0:
                client_specific.append(enriched)
            else:
                systemic.append(enriched)
        client_specific.sort(key=lambda x: x["excess_vs_peers"], reverse=True)

    avg_under = round(mean((i.details or {}).get("underperformance_pct", 0) for i in issues), 2) if issues else 0
    return render_template(
        "hub/performance_view.html",
        systemic=systemic,
        client_specific=client_specific,
        avg_underperformance=avg_under,
        total_issues=len(issues),
    )


@hub_bp.route("/issues/create-alert", methods=["POST"])
@login_required
@handle_errors
def create_alert_bulk():
    from alert_system_models import Alert

    issue_ids = request.form.getlist("issue_ids[]", type=int)
    if not issue_ids:
        flash("No issues selected.", "warning")
        return redirect(url_for("hub.issue_queue"))

    issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.id.in_(issue_ids), DataIntegrityIssue.status.in_(["open", "baseline"])
    ).all()
    by_client = defaultdict(list)
    for issue in issues:
        by_client[issue.client_id].append(issue)

    created = 0
    for client_id, client_issues in by_client.items():
        try:
            from services.task_assignment_service import get_assignee_for_alert

            assignee = (
                get_assignee_for_alert(
                    alert_type="data_integrity",
                    alert_subtype=client_issues[0].check_category,
                    issues=client_issues,
                    client_id=client_id,
                )
                or current_user.id
            )
        except Exception:
            assignee = current_user.id

        max_sev = max(client_issues, key=lambda x: _severity_rank(x.severity)).severity
        desc_lines = [f"- [{i.check_name}] {(i.message or '')[:100]}" for i in client_issues[:10]]
        from services.alert_creation_policy import guard_alert_creation
        if not guard_alert_creation("routes.intelligence_hub.create_alert_bulk"):
            continue
        alert = Alert(
            alert_type="data_integrity",
            alert_subtype="multiple"
            if len(set(i.check_category for i in client_issues)) > 1
            else client_issues[0].check_category.lower(),
            severity=max_sev,
            status="active",
            client_id=client_id,
            user_id=assignee,
            title=f"Data issues: {client_issues[0].client.name if client_issues[0].client else 'Client'} ({len(client_issues)} issues)",
            description="\n".join(desc_lines),
        )
        db.session.add(alert)
        db.session.flush()
        for issue in client_issues:
            issue.alert_id = alert.id
        created += 1

    db.session.commit()
    flash(f"{created} alert(s) created.", "success")
    return redirect(url_for("hub.alert_center"))


@hub_bp.route("/ai/analyze", methods=["POST"])
@login_required
def ai_analyze():
    issue_ids = request.form.getlist("issue_ids[]", type=int)
    client_id = request.form.get("client_id", type=int)
    if not issue_ids:
        query = DataIntegrityIssue.query.filter(DataIntegrityIssue.status.in_(["open", "baseline"]))
        if client_id:
            query = query.filter(DataIntegrityIssue.client_id == client_id)
        issues = query.order_by(
            db.case({"critical": 3, "warning": 2, "info": 1}, value=DataIntegrityIssue.severity).desc()
        ).limit(10)
        issue_ids = [i.id for i in issues]
    if not issue_ids:
        return jsonify({"success": False, "error": "No open issues found to analyze."}), 400

    try:
        from services.hub_ai_service import HubAIUnavailableError, hub_ai_service

        recommendations = hub_ai_service.analyze_issues(issue_ids, current_user.id)
        session["hub_ai_suggestions"] = recommendations
        session.modified = True
        return jsonify({"success": True, "count": len(recommendations), "recommendations": recommendations})
    except HubAIUnavailableError as exc:
        return jsonify({"success": False, "error": str(exc), "type": "unavailable"}), 503
    except Exception as exc:
        logger.error("AI analysis failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 500


@hub_bp.route("/ai/approve", methods=["POST"])
@login_required
def ai_approve():
    rec_json = request.form.get("recommendation")
    if not rec_json:
        return jsonify({"success": False, "error": "No recommendation provided"}), 400
    try:
        rec = json.loads(rec_json)
    except json.JSONDecodeError:
        return jsonify({"success": False, "error": "Invalid recommendation JSON"}), 400

    try:
        from services.hub_ai_service import hub_ai_service

        result = hub_ai_service.execute_recommendation(rec, current_user.id)
        suggestions = session.get("hub_ai_suggestions", [])
        session["hub_ai_suggestions"] = [s for s in suggestions if s.get("issue_id") != rec.get("issue_id")]
        session.modified = True
        return jsonify(result)
    except Exception as exc:
        logger.error("AI approve failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "error": str(exc)}), 500


@hub_bp.route("/ai/skip", methods=["POST"])
@login_required
def ai_skip():
    issue_id = request.form.get("issue_id", type=int)
    reason = request.form.get("reason", "User skipped")
    suggestions = session.get("hub_ai_suggestions", [])
    session["hub_ai_suggestions"] = [s for s in suggestions if s.get("issue_id") != issue_id]
    session.modified = True
    try:
        skipped = next((s for s in suggestions if s.get("issue_id") == issue_id), None)
        if skipped:
            feedback = AgentFeedback(
                category=skipped.get("check_category", "GENERAL"),
                related_check="hub_ai",
                title=f"AI suggestion skipped: {skipped.get('title', '')}",
                description=f"Suggested: {skipped.get('action')} - Skipped because: {reason}",
                status="open",
                created_by=current_user.id,
            )
            db.session.add(feedback)
            db.session.commit()
    except Exception:
        pass
    return jsonify({"success": True})
