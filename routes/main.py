from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import os
from typing import Any

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file, session, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import desc, func

from access_control import require_advisor_or_manager, require_holdings_report_access
from extensions import db

main = Blueprint("main", __name__)


@main.before_request
def _enforce_main_client_scope():
    from access_control import enforce_client_id_from_view_args
    return enforce_client_id_from_view_args()

_MENU_ENDPOINTS = {
    # Clients
    "clients_v2.list_clients",
    "tasks.list_tasks",
    "parrva.submit_page",
    "main.whatsapp_dashboard",
    "main.whatsapp_templates",
    "main.whatsapp_meta_groups",
    "main.whatsapp_groups",
    "financial_analytics.dashboard",
    "main.practice_analytics",
    "leads.list_leads",
    "meetings.list_meetings",
    "meetings.new_meeting",
    "agreements.list_templates",
    "tickets.list_tickets",
    "tickets.new_ticket",
    # Maintenance
    "main.transactions",
    "main.list_cashflows",
    "main.add_cashflow",
    "main.edit_cashflow",
    "main.delete_cashflow",
    "main.bulk_delete_cashflows",
    "historical_prices.view_historical_prices",
    "main.trade_upload",
    "main.mprofit_trade_upload",
    "main.upload_securities",
    "main.cashflow_upload",
    "main.client_upload",
    "historical_prices.upload_page",
    "corporate_actions.upload_corporate_actions",
    "agreements.agreement_template_upload",
    "bni_referrals.list_bni_referrals",
    # Dashboards (monthly investments list, generic workflows, review schedules)
    "workflows.list_workflows",
    "clients.list_review_schedules",
    "rec_execution.dashboard",
    "rec_execution.by_client",
    "rec_execution.sla_settings",
    "main.list_monthly_investments",
    "recommended_trades.list_all_recommended_trades",
    "recommended_trades.add_recommended_trade",
    "main.daily_workflow_report",
    "review.portfolio_construction",
    "review.position_construction",
    # Reviews / maintenance review tools
    "enhanced_review.period_analysis_v2",
    "enhanced_review.performance_analysis_email",
    "enhanced_review.equity_model_vs_nifty",
    "unified_recommendations.unified_recommendations",
    "unified_recommendations.generate_unified_recommendations",
    "invoices.list_invoices",
    # Hub
    "hub.dashboard",
    "hub.operational_dashboard",
    "hub.investment_dashboard",
    "hub.system_dashboard",
    "hub.price_accuracy_dashboard",
    "hub.business_dashboard",
    # Tools
    "alerts.list_alerts",
    "alerts.alert_dashboard",
    "corporate_actions.list_actions",
    "account_management.dashboard",
    "account_management.analysis",
    "account_management.heads_list",
    "account_management.upload",
    "campaign_studio.index",
    "main.holdings_report",
    "main.data_upload_links",
    "main.maintenance_trade_data",
    "main.maintenance_reference_data",
    "main.maintenance_recommendations",
    "main.maintenance_reviews",
    "main.maintenance_portfolio_modelling",
    "main.maintenance_client_advisor_assignments",
    "main.list_asset_classes",
    "main.add_asset_class",
    "main.edit_asset_class",
    "main.models",
    "main.create_asset_model",
    "main.create_security_model",
    "main.securities",
    "main.add_security",
    "main.hot_stocks",
    # Airflow
    "airflow.dashboard",
    # Attendance
    "attendance.attendance_dashboard",
    "attendance.mark_attendance",
    "attendance.user_claims",
    "attendance.incentive_simulator",
    "attendance.incentive_simulator_calculate",
    "attendance.admin_attendance_management",
    "attendance.admin_claims_management",
    "attendance.admin_salary_calculation",
    # Admin
    "users.list_users",
    "roles.list_roles",
    "task_assignment_rules.list_rules",
    "alerts.sla_configuration",
    "settings.index",
    "settings.cron_jobs",
    "settings.system_info",
    "settings.application_logs",
    "settings.application_log_tail",
    "settings.holdings_cycle",
    "main.database_browser",
    "main.database_browser_preview",
    "main.sql_query",
    "main.sql_query_schema",
    # User dropdown
    "main.user_manual_pdf",
    "assistant.assistant_hub",
    "assistant.help_assistant_page",
    "assistant.help_assistant_chat",
    "assistant.help_knowledge_propose",
    "assistant.help_knowledge_approve",
    "assistant.help_knowledge_pending",
    "assistant.sql_assistant_page",
    "assistant.sql_ai_suggest",
    "auth.profile",
    "two_factor.setup_2fa",
    "auth.logout",
    # Missing links menu
}

_MENU_PREFIXES = (
    "clients_v2.",
    "tasks.",
    "parrva.",
    "financial_analytics.",
    "leads.",
    "meetings.",
    "agreements.",
    "tickets.",
    "workflows.",
    "monthly_investments.",
    "recommended_trades.",
    "rec_execution.",
    "review.",
    "enhanced_review.",
    "unified_recommendations.",
    "invoices.",
    "hub.",
    "alerts.",
    "account_management.",
    "campaign_studio.",
    "airflow.",
    "attendance.",
    "users.",
    "roles.",
    "task_assignment_rules.",
    "settings.",
    "auth.",
    "two_factor.",
)

_ROUTE_EXCLUDE_PREFIXES = (
    "/static/",
    "/api/",
)


def _route_is_already_in_menu(endpoint: str) -> bool:
    if endpoint in _MENU_ENDPOINTS:
        return True
    return endpoint.startswith(_MENU_PREFIXES)


def _route_should_be_excluded(endpoint: str, rule_path: str) -> bool:
    if not endpoint:
        return True
    if endpoint == "static":
        return True
    if rule_path.startswith(_ROUTE_EXCLUDE_PREFIXES):
        return True
    if endpoint.startswith(("api.", "auth.", "two_factor.")):
        return True
    return False


def _humanize_endpoint(endpoint: str) -> str:
    leaf = endpoint.split(".")[-1]
    return leaf.replace("_", " ").strip().title()


def _collect_missing_routes(include_parameterized: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rule in current_app.url_map.iter_rules():
        endpoint = rule.endpoint or ""
        if _route_should_be_excluded(endpoint, rule.rule):
            continue
        if "GET" not in rule.methods:
            continue
        if _route_is_already_in_menu(endpoint):
            continue

        has_params = bool(rule.arguments)
        if has_params and not include_parameterized:
            continue

        resolved_url = None
        if not has_params:
            try:
                resolved_url = url_for(endpoint)
            except Exception:
                resolved_url = None

        rows.append(
            {
                "endpoint": endpoint,
                "label": _humanize_endpoint(endpoint),
                "rule": rule.rule,
                "methods": sorted([m for m in rule.methods if m not in {"HEAD", "OPTIONS"}]),
                "has_params": has_params,
                "url": resolved_url,
                "blueprint": endpoint.split(".")[0] if "." in endpoint else "app",
            }
        )

    rows.sort(key=lambda item: (item["blueprint"], item["endpoint"]))
    return rows


@main.route("/docs/user-manual.pdf")
@login_required
def user_manual_pdf():
    """Serve operator user manual as inline PDF (browser viewer)."""
    from services.user_manual_pdf_service import get_user_manual_pdf_path

    pdf_path = get_user_manual_pdf_path()
    if not pdf_path or not pdf_path.is_file():
        abort(404, description="User manual PDF is not available. Contact your administrator.")

    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name="INERTIA_User_Manual.pdf",
        max_age=3600,
    )


@main.route("/help/trade-edit-cashflow-review")
@login_required
def trade_edit_cashflow_guide():
    """Ops guide: manual cashflow review required after trade edits."""
    return render_template("help/trade_edit_cashflow_review.html")


@main.route("/")
@main.route("/dashboard")
@login_required
def dashboard():
    from models import Client, Workflow, ReviewWorkflow
    from access_control import get_accessible_clients
    try:
        accessible = get_accessible_clients()
        total_clients = len(accessible) if accessible else 0
        active_workflows = Workflow.query.filter(Workflow.stage != 'COMPLETED').count()
        pending_reviews = ReviewWorkflow.query.filter(
            ReviewWorkflow.status.in_(['initiated', 'sent', 'meeting'])
        ).count()
    except Exception:
        total_clients = 0
        active_workflows = 0
        pending_reviews = 0

    my_open_tasks = []
    my_open_tasks_count = 0
    try:
        from services.dashboard_open_tasks_service import (
            count_open_ops_tasks_for_user,
            fetch_open_ops_tasks_for_user,
        )

        my_open_tasks_count = count_open_ops_tasks_for_user(current_user.id)
        my_open_tasks = fetch_open_ops_tasks_for_user(current_user.id, limit=20)
    except Exception:
        pass

    data_price_rows = []
    data_price_overdue = 0
    try:
        from access_control import get_accessible_clients
        from services.client_health_dashboard_service import group_data_price_by_client

        accessible = get_accessible_clients()
        ids = {c.id for c in accessible} if accessible else set()
        data_price_rows = group_data_price_by_client(accessible_client_ids=ids)[:15]
        data_price_overdue = sum(1 for r in data_price_rows if r.get("overdue_count"))
    except Exception:
        pass

    cashflow_trade_issues = []
    cashflow_trade_issue_count = 0
    cashflow_trade_issue_as_of = None
    cashflow_trade_show_advisor = False
    try:
        from access_control import get_accessible_clients, user_can_view_all_clients
        from services.cashflow_trade_integrity_case_service import list_cashflow_trade_issue_clients

        accessible = get_accessible_clients()
        ids = {c.id for c in accessible} if accessible else set()
        pack = list_cashflow_trade_issue_clients(
            accessible_client_ids=ids,
            include_accepted=False,
            enrich_advisor=True,
        )
        cashflow_trade_issue_count = int(pack.get("total") or 0)
        cashflow_trade_issues = (pack.get("rows") or [])[:12]
        cashflow_trade_issue_as_of = pack.get("as_of")
        cashflow_trade_show_advisor = bool(
            getattr(current_user, "is_admin", False)
            or getattr(current_user, "is_manager", False)
            or user_can_view_all_clients()
        )
    except Exception:
        pass

    data_integrity_issues = []
    data_integrity_issue_count = 0
    data_integrity_issue_as_of = None
    data_integrity_show_advisor = False
    data_integrity_counts = {}
    try:
        from access_control import get_accessible_clients, user_can_view_all_clients
        from services.client_data_integrity_case_service import list_data_integrity_summaries

        accessible = get_accessible_clients()
        ids = {c.id for c in accessible} if accessible else set()
        di_pack = list_data_integrity_summaries(
            accessible_client_ids=ids,
            only_issues=True,
            limit=12,
            enrich_advisor=True,
        )
        data_integrity_issue_count = int(di_pack.get("total") or 0)
        data_integrity_issues = di_pack.get("rows") or []
        data_integrity_issue_as_of = di_pack.get("as_of")
        data_integrity_counts = di_pack.get("counts") or {}
        data_integrity_show_advisor = bool(
            getattr(current_user, "is_admin", False)
            or getattr(current_user, "is_manager", False)
            or user_can_view_all_clients()
        )
    except Exception:
        pass

    # Home dashboard: advisor ↔ client names only (no AUM / book metrics).
    # Full metrics stay on /advisor-review/ (admin).
    assignment_overview = None
    try:
        if getattr(current_user, "is_admin", False) or getattr(current_user, "is_manager", False):
            from services.advisor_review_service import get_advisor_assignment_overview

            assignment_overview = get_advisor_assignment_overview()
    except Exception:
        pass

    return render_template(
        "dashboard_stub.html",
        now=datetime.utcnow(),
        total_clients=total_clients,
        active_workflows=active_workflows,
        pending_reviews=pending_reviews,
        my_open_tasks=my_open_tasks,
        my_open_tasks_count=my_open_tasks_count,
        tasks_nav_enabled=current_app.config.get("NAV_TASKS_ENABLED", False),
        data_price_rows=data_price_rows,
        data_price_overdue=data_price_overdue,
        cashflow_trade_issues=cashflow_trade_issues,
        cashflow_trade_issue_count=cashflow_trade_issue_count,
        cashflow_trade_issue_as_of=cashflow_trade_issue_as_of,
        cashflow_trade_show_advisor=cashflow_trade_show_advisor,
        data_integrity_issues=data_integrity_issues,
        data_integrity_issue_count=data_integrity_issue_count,
        data_integrity_issue_as_of=data_integrity_issue_as_of,
        data_integrity_show_advisor=data_integrity_show_advisor,
        data_integrity_counts=data_integrity_counts,
        is_admin_user=bool(getattr(current_user, "is_admin", False)),
        is_manager_user=bool(getattr(current_user, "is_manager", False)),
        assignment_overview=assignment_overview,
    )


@main.route("/clients")
@login_required
def clients():
    """Canonical client UI is on the `clients` blueprint (`/clients/`)."""
    return redirect(url_for("clients_v2.list_clients"))


@main.route("/clients/add")
@login_required
def add_client():
    return redirect(url_for("clients_v2.add_client"))


@main.route("/client/<int:client_id>")
@login_required
def client_details(client_id):
    """Compatibility: url_for('main.client_details', ...) and bookmarks at /client/<id>."""
    return redirect(url_for("clients.client_details", client_id=client_id))


@main.route("/transactions")
@login_required
def transactions():
    from access_control import get_accessible_clients_ordered, can_access_client

    clients = get_accessible_clients_ordered()
    selected_client_id = request.args.get("client_id", type=int)
    if selected_client_id and not can_access_client(selected_client_id):
        flash("Access denied. You can only access your assigned clients.", "error")
        selected_client_id = None
    return render_template(
        "transactions.html",
        transactions=[],
        clients=clients,
        selected_client_id=selected_client_id,
    )


@main.route("/transactions/bulk-delete", methods=["POST"])
@login_required
def bulk_delete_transactions():
    """Template form action; the UI submits bulk deletes via the V2 API in JS."""
    flash("Bulk delete is handled on this page after you select a client (via the API).", "info")
    return redirect(url_for("main.transactions"))


def _edit_transaction_template_ctx(transaction, clients, securities, *, require_cashflow_ack=False):
    """Shared template context for trade edit + cashflow acknowledgment gate."""
    cashflows_url = url_for("main.list_cashflows", client_id=transaction.client_id)
    trade_cashflow_guide_url = url_for("main.trade_edit_cashflow_guide")
    pending = session.get("pending_cashflow_review") or {}
    if not require_cashflow_ack and pending.get("transaction_id") == transaction.id:
        require_cashflow_ack = True
    return {
        "transaction": transaction,
        "clients": clients,
        "securities": securities,
        "cashflows_url": cashflows_url,
        "trade_cashflow_guide_url": trade_cashflow_guide_url,
        "require_cashflow_ack": require_cashflow_ack,
    }


@main.route("/transaction/<int:transaction_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(transaction_id):
    """HTML edit form; persistence uses TransactionOrchestrator (same path as API v2 PUT)."""
    from models import Client, Security, Transaction
    from services.transaction_orchestrator import TransactionOrchestrator
    from access_control import can_access_client, get_accessible_clients_ordered
    from sqlalchemy import func

    transaction = Transaction.query.get_or_404(transaction_id)
    if not can_access_client(transaction.client_id):
        flash("Access denied. You can only access your assigned clients.", "error")
        return redirect(url_for("main.transactions"))
    if transaction.type not in ("BUY", "SELL"):
        flash(
            f"Transaction #{transaction_id} is type {transaction.type}; only BUY/SELL can be edited here.",
            "warning",
        )
        return redirect(url_for("main.transactions"))

    clients = get_accessible_clients_ordered()
    securities = Security.query.order_by(func.lower(Security.symbol)).all()

    if request.method == "GET":
        require_ack = request.args.get("cashflow_ack") == "1"
        return render_template(
            "edit_transaction.html",
            **_edit_transaction_template_ctx(
                transaction, clients, securities, require_cashflow_ack=require_ack
            ),
        )

    try:
        if not request.form.get("ack_cashflow_before_save"):
            flash(
                "Please acknowledge that related cashflow entries must be updated manually before saving.",
                "danger",
            )
            return render_template(
                "edit_transaction.html",
                **_edit_transaction_template_ctx(transaction, clients, securities),
            )

        client_id = int(request.form.get("client_id") or 0)
        security_id = int(request.form.get("security_id") or 0)
        txn_type = (request.form.get("type") or "").strip().upper()
        quantity = float(request.form.get("quantity") or 0)
        price = float(request.form.get("price") or 0)
        date_raw = (request.form.get("transaction_date") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        if len(notes) > 1000:
            notes = notes[:1000]

        if txn_type not in ("BUY", "SELL"):
            flash("Transaction type must be BUY or SELL.", "danger")
            return render_template(
                "edit_transaction.html",
                **_edit_transaction_template_ctx(transaction, clients, securities),
            )

        if quantity <= 0 or price < 0:
            flash("Quantity must be positive and price cannot be negative.", "danger")
            return render_template(
                "edit_transaction.html",
                **_edit_transaction_template_ctx(transaction, clients, securities),
            )

        if not date_raw:
            flash("Transaction date is required.", "danger")
            return render_template(
                "edit_transaction.html",
                **_edit_transaction_template_ctx(transaction, clients, securities),
            )

        try:
            transaction_dt = datetime.fromisoformat(date_raw)
        except ValueError:
            flash("Invalid transaction date.", "danger")
            return render_template(
                "edit_transaction.html",
                **_edit_transaction_template_ctx(transaction, clients, securities),
            )

        Client.query.get_or_404(client_id)
        Security.query.get_or_404(security_id)

        orchestrator = TransactionOrchestrator(db.session)
        result = orchestrator.update_single_transaction(
            transaction_id,
            {
                "client_id": client_id,
                "security_id": security_id,
                "type": txn_type,
                "quantity": quantity,
                "price": price,
                "transaction_date": transaction_dt,
            },
        )

        if not result.get("success"):
            db.session.rollback()
            flash(result.get("message") or "Update failed.", "danger")
            transaction = Transaction.query.get_or_404(transaction_id)
            return render_template(
                "edit_transaction.html",
                **_edit_transaction_template_ctx(transaction, clients, securities),
            )

        updated = Transaction.query.get(transaction_id)
        if updated:
            updated.notes = notes or None
        db.session.commit()

        session["pending_cashflow_review"] = {
            "transaction_id": transaction_id,
            "client_id": client_id,
            "acknowledged": False,
        }
        flash(
            "Trade updated (holdings adjusted). Cashflows were not changed — review and adjust manually if needed.",
            "warning",
        )
        return redirect(
            url_for(
                "main.edit_transaction",
                transaction_id=transaction_id,
                cashflow_ack=1,
            )
        )
    except Exception as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        transaction = Transaction.query.get_or_404(transaction_id)
        return render_template(
            "edit_transaction.html",
            **_edit_transaction_template_ctx(transaction, clients, securities),
        )


@main.route(
    "/transaction/<int:transaction_id>/acknowledge-cashflow-review",
    methods=["POST"],
)
@login_required
def acknowledge_trade_cashflow_review(transaction_id):
    """Clear post-update cashflow warning only after explicit user acknowledgment."""
    from models import Transaction
    from access_control import can_access_client

    transaction = Transaction.query.get_or_404(transaction_id)
    if not can_access_client(transaction.client_id):
        flash("Access denied. You can only access your assigned clients.", "error")
        return redirect(url_for("main.transactions"))

    if not request.form.get("ack_cashflow_review"):
        flash("Please check the acknowledgment box to continue.", "danger")
        return redirect(
            url_for(
                "main.edit_transaction",
                transaction_id=transaction_id,
                cashflow_ack=1,
            )
        )

    pending = session.get("pending_cashflow_review") or {}
    if pending.get("transaction_id") == transaction_id:
        session.pop("pending_cashflow_review", None)

    next_dest = (request.form.get("next") or "cashflows").strip().lower()
    flash(
        "Acknowledged. Please update related cashflow entries if amounts or dates changed.",
        "info",
    )
    if next_dest == "transactions":
        return redirect(url_for("main.transactions", client_id=transaction.client_id))
    return redirect(url_for("main.list_cashflows", client_id=transaction.client_id))


@main.route("/transaction/<int:transaction_id>/delete", methods=["POST"])
@login_required
def delete_transaction(transaction_id):
    flash(
        f"Transaction #{transaction_id}: use the in-table delete control (V2 API), not this form.",
        "info",
    )
    return redirect(url_for("main.transactions"))


@main.route("/cashflows")
@login_required
def list_cashflows():
    from models import Cashflow
    from access_control import (
        can_access_client,
        get_accessible_clients_ordered,
        scope_query_to_accessible_clients,
    )
    from services.cashflow_service import CashflowService

    clients = get_accessible_clients_ordered()

    selected_client_id = request.args.get("client_id", type=int)
    if selected_client_id and not can_access_client(selected_client_id):
        flash("Access denied. You can only access your assigned clients.", "error")
        return redirect(url_for("main.list_cashflows"))
    selected_type = request.args.get("type") or None
    if selected_type not in (None, "", "INFLOW", "OUTFLOW"):
        selected_type = None
    if selected_type == "":
        selected_type = None

    view_mode = (request.args.get("view") or "detail").strip().lower()
    if view_mode not in ("detail", "date", "month", "year"):
        view_mode = "detail"

    start_date_s = (request.args.get("start_date") or "").strip() or None
    end_date_s = (request.args.get("end_date") or "").strip() or None

    start_dt = None
    end_dt_exclusive = None
    if start_date_s:
        try:
            start_dt = datetime.strptime(start_date_s, "%Y-%m-%d")
        except ValueError:
            start_date_s = None
    if end_date_s:
        try:
            end_day = datetime.strptime(end_date_s, "%Y-%m-%d")
            end_dt_exclusive = end_day + timedelta(days=1)
        except ValueError:
            end_date_s = None

    q = scope_query_to_accessible_clients(Cashflow.query, Cashflow.client_id)
    if selected_client_id:
        q = q.filter(Cashflow.client_id == selected_client_id)
    if selected_type:
        q = q.filter(Cashflow.type == selected_type)
    if start_dt is not None:
        q = q.filter(Cashflow.date >= start_dt)
    if end_dt_exclusive is not None:
        q = q.filter(Cashflow.date < end_dt_exclusive)

    cashflows = q.order_by(Cashflow.date.desc()).all()

    aggregates = []
    if view_mode in CashflowService.AGGREGATE_PERIODS:
        # Single client → group by period for that client.
        # All Clients → net across clients within each period.
        aggregates = CashflowService.aggregate_cashflows(
            cashflows,
            view_mode,
            group_by_client=bool(selected_client_id),
        )

    return render_template(
        "cashflows.html",
        cashflows=cashflows,
        aggregates=aggregates,
        view_mode=view_mode,
        clients=clients,
        selected_client_id=selected_client_id,
        selected_type=selected_type or "",
        start_date=start_date_s,
        end_date=end_date_s,
    )


@main.route("/cashflows/bulk-delete", methods=["POST"])
@login_required
def bulk_delete_cashflows():
    """Bulk delete from cashflows.html (checkboxes + modal)."""
    from services.cashflow_service import CashflowService

    raw = (request.form.get("cashflow_ids") or "").strip()
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            flash("Invalid cashflow selection.", "warning")
            return redirect(url_for("main.list_cashflows"))

    if not ids:
        flash("No cashflows selected.", "info")
        return redirect(url_for("main.list_cashflows"))

    result = CashflowService.bulk_delete_cashflows(ids)
    if not result.get("success"):
        db.session.rollback()
        err = "; ".join(result.get("hard_errors") or ["Unknown error"])
        flash(f"Bulk delete failed: {err}", "danger")
        return redirect(url_for("main.list_cashflows"))

    try:
        db.session.commit()
    except Exception as exc:  # pragma: no cover
        db.session.rollback()
        flash(f"Could not complete bulk delete: {exc}", "danger")
        return redirect(url_for("main.list_cashflows"))

    n_del = int(result.get("deleted_count") or 0)
    n_nf = int(result.get("not_found_count") or 0)
    if n_del:
        msg = f"Deleted {n_del} cashflow(s)."
        if n_nf:
            msg += f" {n_nf} were not found (already removed or invalid id)."
        flash(msg, "success")
    elif n_nf:
        flash(f"No cashflows deleted; {n_nf} id(s) were not found.", "warning")
    return redirect(url_for("main.list_cashflows"))


@main.route("/cashflow/<int:cashflow_id>/delete", methods=["POST"])
@login_required
def delete_cashflow(cashflow_id):
    """Single cashflow delete from cashflows.html confirmation modal."""
    from services.cashflow_service import CashflowService

    result = CashflowService.delete_cashflow(cashflow_id)
    if result.get("success"):
        try:
            db.session.commit()
            flash("Cashflow deleted.", "success")
        except Exception as exc:  # pragma: no cover - DB layer
            db.session.rollback()
            flash(f"Could not delete cashflow: {exc}", "danger")
    else:
        db.session.rollback()
        flash(result.get("error") or "Could not delete cashflow.", "danger")
    return redirect(url_for("main.list_cashflows"))


@main.route("/investments/monthly")
@login_required
def list_monthly_investments():
    """Monthly investment hub (summary cards + links). Permission key unchanged."""
    from services.monthly_investment_hub_service import get_monthly_investment_hub_context

    ctx = get_monthly_investment_hub_context()
    return render_template("monthly_investment_hub.html", **ctx)


@main.route("/investments/monthly/new", methods=["GET"])
@login_required
def new_monthly_investment():
    """Compatibility: old navbar/bookmarks pointed here; creation lives on the blueprint."""
    return redirect(url_for("monthly_investments.new_monthly_investment"))


@main.route("/investments/monthly/investment/<int:id>", methods=["GET"])
@login_required
def view_monthly_investment(id):
    """Compatibility: links from reports and rec execution."""
    return redirect(url_for("monthly_investments.view_monthly_investment", id=id))


@main.route("/workflows/daily-report")
@login_required
def daily_workflow_report():
    from services.daily_workflow_report_page_service import build_daily_workflow_report_page_context

    ctx = build_daily_workflow_report_page_context()
    return render_template("daily_workflow_report.html", **ctx)


@main.route("/cashflows/add", methods=["GET", "POST"])
@login_required
def add_cashflow():
    from routes.forms import CashflowForm
    from services.cashflow_service import CashflowService

    form = CashflowForm()
    if form.validate_on_submit():
        raw = float(form.amount.data)
        if raw == 0:
            flash("Amount must be non-zero.", "danger")
            return render_template("add_cashflow.html", form=form)

        if form.type.data == "INFLOW":
            amount = -abs(raw)
        else:
            amount = abs(raw)

        description = (form.description.data or "").strip()
        result = CashflowService.create_manual_cashflow(
            client_id=form.client_id.data,
            date_obj=form.date.data,
            amount=amount,
            description=description,
            created_by=current_user.id,
            cashflow_type=form.type.data,
        )

        if not result.get("success"):
            flash(result.get("error") or "Could not add cashflow.", "danger")
            return render_template("add_cashflow.html", form=form)

        try:
            db.session.commit()
        except Exception as exc:  # pragma: no cover
            db.session.rollback()
            flash(f"Could not save cashflow: {exc}", "danger")
            return render_template("add_cashflow.html", form=form)

        flash("Cashflow added.", "success")
        return redirect(url_for("main.list_cashflows", client_id=form.client_id.data))

    return render_template("add_cashflow.html", form=form, is_edit=False)


def _cashflow_date_for_form(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


@main.route("/cashflow/<int:cashflow_id>/edit", methods=["GET", "POST"])
@login_required
def edit_cashflow(cashflow_id):
    """Edit an existing cashflow row (used after trade edits and for advisor notes)."""
    from models import Cashflow
    from routes.forms import CashflowForm
    from services.cashflow_service import CashflowService
    from access_control import can_access_client

    cashflow = Cashflow.query.get_or_404(cashflow_id)
    if not can_access_client(cashflow.client_id):
        flash("Access denied. You can only access your assigned clients.", "error")
        return redirect(url_for("main.list_cashflows"))

    form = CashflowForm()
    if request.method == "GET":
        form.client_id.data = cashflow.client_id
        form.type.data = cashflow.type or ("INFLOW" if float(cashflow.amount or 0) < 0 else "OUTFLOW")
        form.amount.data = abs(float(cashflow.amount or 0))
        form.date.data = _cashflow_date_for_form(cashflow.date)
        form.description.data = cashflow.description or ""

    if form.validate_on_submit():
        if not can_access_client(form.client_id.data):
            flash("Access denied. You can only access your assigned clients.", "error")
            return render_template("add_cashflow.html", form=form, is_edit=True, cashflow_id=cashflow_id)

        raw = float(form.amount.data)
        if raw == 0:
            flash("Amount must be non-zero.", "danger")
            return render_template("add_cashflow.html", form=form, is_edit=True, cashflow_id=cashflow_id)

        if form.type.data == "INFLOW":
            amount = -abs(raw)
        else:
            amount = abs(raw)

        result = CashflowService.update_cashflow(
            cashflow_id,
            {
                "client_id": form.client_id.data,
                "date": form.date.data,
                "amount": amount,
                "description": (form.description.data or "").strip(),
                "cashflow_type": form.type.data,
            },
        )
        if not result.get("success"):
            flash(result.get("error") or "Could not update cashflow.", "danger")
            return render_template("add_cashflow.html", form=form, is_edit=True, cashflow_id=cashflow_id)

        try:
            db.session.commit()
        except Exception as exc:  # pragma: no cover
            db.session.rollback()
            flash(f"Could not save cashflow: {exc}", "danger")
            return render_template("add_cashflow.html", form=form, is_edit=True, cashflow_id=cashflow_id)

        flash("Cashflow updated.", "success")
        return redirect(url_for("main.list_cashflows", client_id=form.client_id.data, view="detail"))

    return render_template("add_cashflow.html", form=form, is_edit=True, cashflow_id=cashflow_id)


@main.route("/recommendations")
@login_required
def recommendations():
    """Trade recommendations list with inline edit; per-client batch UI lives on recommended trades."""
    from models import Recommendation
    from access_control import (
        can_access_client,
        get_accessible_clients_ordered,
        scope_query_to_accessible_clients,
    )

    client_id = request.args.get("client_id", type=int)
    if client_id:
        if not can_access_client(client_id):
            flash("Access denied. You can only access your assigned clients.", "error")
            return redirect(url_for("main.recommendations"))
        return redirect(
            url_for("recommended_trades.client_recommended_trades", client_id=client_id)
        )

    action_filter = (request.args.get("action") or "").strip()
    status_filter = (request.args.get("status") or "").strip()

    query = scope_query_to_accessible_clients(
        Recommendation.query.filter(*Recommendation.executable_trade_sql_filters()),
        Recommendation.client_id,
    )
    if action_filter:
        query = query.filter(func.lower(Recommendation.action) == action_filter.lower())
    if status_filter:
        query = query.filter(Recommendation.status == status_filter.lower())

    recommendations_list = query.order_by(
        Recommendation.created_at.is_(None).asc(),
        desc(Recommendation.created_at),
        desc(Recommendation.id),
    ).all()

    clients = get_accessible_clients_ordered()

    return render_template(
        "recommendations.html",
        recommendations=recommendations_list,
        clients=clients,
        selected_client_id=None,
        selected_action=action_filter.upper() if action_filter else "",
        selected_status=status_filter.upper() if status_filter else "",
        now=datetime.utcnow(),
    )


@main.route("/recommendations/generate")
@login_required
def generate_recommendations():
    return redirect(url_for("unified_recommendations.generate_unified_recommendations"))


@main.route("/recommendations/bulk-delete", methods=["POST"])
@login_required
def bulk_delete_recommendations():
    from models import Recommendation

    recommendation_ids_str = request.form.get("recommendation_ids", "")
    if not recommendation_ids_str:
        flash("No recommendations selected for deletion", "error")
        return redirect(url_for("main.recommendations"))

    recommendation_ids = [
        int(part)
        for part in recommendation_ids_str.split(",")
        if part.strip().isdigit()
    ]
    if not recommendation_ids:
        flash("Invalid recommendation IDs provided", "error")
        return redirect(url_for("main.recommendations"))

    deleted_count = 0
    try:
        for rec_id in recommendation_ids:
            recommendation = Recommendation.query.get(rec_id)
            if recommendation and recommendation.is_trade_recommendation:
                db.session.delete(recommendation)
                deleted_count += 1
        db.session.commit()
        flash(
            f"{deleted_count} recommendation(s) deleted successfully.",
            "success",
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("bulk_delete_recommendations failed")
        flash(f"Error deleting recommendations: {exc}", "error")

    return redirect(url_for("main.recommendations"))


@main.route("/recommendations/api/delete", methods=["POST"])
@login_required
def api_delete_recommendation():
    from models import Recommendation

    try:
        rec = Recommendation.query.get(request.form.get("id"))
        if not rec:
            return jsonify(success=False, error="Recommendation not found")
        if not rec.is_trade_recommendation:
            return jsonify(success=False, error="Not a trade recommendation")
        db.session.delete(rec)
        db.session.commit()
        return jsonify(success=True)
    except Exception as exc:
        db.session.rollback()
        return jsonify(success=False, error=str(exc))


@main.route("/workflow-cleanup")
@login_required
def workflow_cleanup():
    return jsonify({"ok": True, "message": "Workflow cleanup placeholder"})


@main.route("/review-schedules")
@login_required
def list_review_schedules():
    return redirect(url_for("clients.list_review_schedules"))


@main.route("/client/<int:client_id>/review-schedule")
@login_required
def client_review_schedule(client_id):
    """Compatibility: url_for('main.client_review_schedule') and old bookmarks.
    Path uses singular /client/... so it does not shadow the clients blueprint
    route at /clients/<id>/review-schedule.
    """
    return redirect(url_for("clients.client_review_schedule", client_id=client_id))


@main.route("/client/<int:client_id>/recommendations", methods=["GET", "POST"])
@login_required
def client_recommendations(client_id):
    """Compatibility: url_for('main.client_recommendations', client_id=..., amount=...)."""
    dest = url_for("unified_recommendations.client_recommendations", client_id=client_id)
    q = request.query_string.decode()
    if q:
        dest = f"{dest}?{q}"
    return redirect(dest)


@main.route("/client/<int:client_id>/update-planning-synopsis", methods=["POST"])
@login_required
def update_planning_synopsis(client_id):
    """Compatibility endpoint for client details inline editor (legacy singular /client path)."""
    from models import Client
    from services.client_notes_service import append_planning_synopsis_note, note_author_name, parse_notes_entries

    client = Client.query.get_or_404(client_id)
    new_note = (request.form.get("planning_synopsis") or "").strip()
    if not new_note:
        return jsonify({"success": False, "error": "Note text is required"}), 400
    try:
        client.planning_synopsis = append_planning_synopsis_note(
            client.planning_synopsis,
            new_note,
            note_author_name(current_user),
        )
        db.session.commit()
        return jsonify({"success": True, "entries": parse_notes_entries(client.planning_synopsis)})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to update planning synopsis for client %s: %s", client_id, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@main.route("/client/<int:client_id>/update-background-notes", methods=["POST"])
@login_required
def update_background_notes(client_id):
    """Compatibility endpoint for client details inline editor (legacy singular /client path)."""
    from models import Client
    from services.client_notes_service import append_background_notes_note, note_author_name, parse_notes_entries

    client = Client.query.get_or_404(client_id)
    new_note = (request.form.get("background_notes") or "").strip()
    if not new_note:
        return jsonify({"success": False, "error": "Note text is required"}), 400
    try:
        client.background_notes = append_background_notes_note(
            client.background_notes,
            new_note,
            note_author_name(current_user),
        )
        db.session.commit()
        return jsonify({"success": True, "entries": parse_notes_entries(client.background_notes)})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Failed to update background notes for client %s: %s", client_id, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@main.route("/whatsapp/dashboard")
@login_required
def whatsapp_dashboard():
    try:
        return render_template("whatsapp/dashboard.html")
    except Exception as e:
        flash(f"WhatsApp dashboard unavailable: {e}", "warning")
        return redirect(url_for("main.dashboard"))


@main.route("/whatsapp/templates")
@login_required
def whatsapp_templates():
    try:
        return render_template("whatsapp/templates.html")
    except Exception as e:
        flash(f"WhatsApp templates unavailable: {e}", "warning")
        return redirect(url_for("main.whatsapp_dashboard"))


@main.route("/whatsapp/meta-groups")
@login_required
def whatsapp_meta_groups():
    return redirect(url_for("main.whatsapp_groups"))


@main.route("/whatsapp/groups")
@login_required
def whatsapp_groups():
    try:
        return render_template("whatsapp/groups.html")
    except Exception as e:
        flash(f"WhatsApp Groups monitoring unavailable: {e}", "warning")
        return redirect(url_for("main.whatsapp_dashboard"))


@main.route("/models")
@login_required
def models():
    from services.allocation_model_admin_service import list_allocation_models_context

    return render_template("models.html", **list_allocation_models_context())


@main.route("/model/asset/create", methods=["GET", "POST"])
@login_required
def create_asset_model():
    from models import AssetClass
    from services.allocation_model_admin_service import create_asset_allocation_model_from_request

    if request.method == "POST":
        ok, err = create_asset_allocation_model_from_request(request.form, current_user.id)
        if err:
            flash(err, "error")
        else:
            flash("Asset model created successfully.", "success")
            return redirect(url_for("main.models"))
        return render_template("create_asset_model.html", asset_classes=AssetClass.query.order_by(AssetClass.name.asc()).all())
    return render_template("create_asset_model.html", asset_classes=AssetClass.query.order_by(AssetClass.name.asc()).all())


@main.route("/model/security/create", methods=["GET", "POST"])
@login_required
def create_security_model():
    from models import AssetClass
    from services.allocation_model_admin_service import (
        create_security_allocation_model_from_request,
        stocks_for_create_security_model,
    )

    if request.method == "POST":
        ok, err = create_security_allocation_model_from_request(request.form, current_user.id)
        if err:
            flash(err, "error")
        else:
            flash("Security model created successfully.", "success")
            return redirect(url_for("main.models"))
        return render_template(
            "create_security_model.html",
            asset_classes=AssetClass.query.order_by(AssetClass.name.asc()).all(),
            stocks=stocks_for_create_security_model(),
        )
    return render_template(
        "create_security_model.html",
        asset_classes=AssetClass.query.order_by(AssetClass.name.asc()).all(),
        stocks=stocks_for_create_security_model(),
    )


@main.route("/model/asset/import", methods=["POST"])
@login_required
def import_asset_model_from_client():
    from services.allocation_model_admin_service import create_asset_allocation_model_by_import
    from access_control import can_access_client
    from services.audit_service import log_audit_event
    from utils.internal_next import normalize_internal_next

    try:
        client_id = int(request.form.get("client_id") or 0)
    except (TypeError, ValueError):
        client_id = 0
    if not client_id:
        flash("Client is required.", "error")
        next_url = normalize_internal_next(request.form.get("next"))
        return redirect(next_url or url_for("main.models"))
    if not can_access_client(client_id):
        flash("Access denied. You can only access your assigned clients.", "error")
        next_url = normalize_internal_next(request.form.get("next"))
        return redirect(next_url or url_for("main.models"))

    ok, err, _model_id = create_asset_allocation_model_by_import(client_id=client_id, user_id=current_user.id)
    if err:
        flash(err, "error")
    else:
        flash("Asset model imported successfully.", "success")
        log_audit_event(
            "allocation_model_import",
            resource_type="asset_allocation_model",
            resource_id=str(_model_id) if _model_id else None,
            client_id=client_id,
            details={"import_kind": "asset", "source_client_id": client_id},
            commit=True,
        )
    next_url = normalize_internal_next(request.form.get("next"))
    return redirect(next_url or url_for("main.models"))


@main.route("/model/security/import", methods=["POST"])
@login_required
def import_security_model_from_client():
    from services.allocation_model_admin_service import create_security_allocation_model_by_import
    from access_control import can_access_client
    from services.audit_service import log_audit_event
    from utils.internal_next import normalize_internal_next

    try:
        client_id = int(request.form.get("client_id") or 0)
    except (TypeError, ValueError):
        client_id = 0
    try:
        asset_class_id = int(request.form.get("asset_class_id") or 0)
    except (TypeError, ValueError):
        asset_class_id = 0

    if not client_id:
        flash("Client is required.", "error")
        next_url = normalize_internal_next(request.form.get("next"))
        return redirect(next_url or url_for("main.models"))
    if not asset_class_id:
        flash("Asset class is required.", "error")
        next_url = normalize_internal_next(request.form.get("next"))
        return redirect(next_url or (url_for("main.models") + "#security-models"))
    if not can_access_client(client_id):
        flash("Access denied. You can only access your assigned clients.", "error")
        next_url = normalize_internal_next(request.form.get("next"))
        return redirect(next_url or (url_for("main.models") + "#security-models"))

    ok, err, _model_id = create_security_allocation_model_by_import(
        client_id=client_id,
        asset_class_id=asset_class_id,
        user_id=current_user.id,
    )
    if err:
        flash(err, "error")
    else:
        flash("Security model imported successfully.", "success")
        log_audit_event(
            "allocation_model_import",
            resource_type="security_allocation_model",
            resource_id=str(_model_id) if _model_id else None,
            client_id=client_id,
            details={
                "import_kind": "security",
                "source_client_id": client_id,
                "asset_class_id": asset_class_id,
            },
            commit=True,
        )
    next_url = normalize_internal_next(request.form.get("next"))
    return redirect(next_url or (url_for("main.models") + "#security-models"))


@main.route("/model/asset/<int:model_id>")
@login_required
def view_asset_model(model_id):
    from services.allocation_model_admin_service import view_asset_model_context

    ctx = view_asset_model_context(model_id)
    if not ctx:
        flash("Asset model not found.", "error")
        return redirect(url_for("main.models"))
    return render_template("view_asset_model.html", **ctx)


@main.route("/model/security/<int:model_id>")
@login_required
def view_security_model(model_id):
    from services.allocation_model_admin_service import view_security_model_context

    ctx = view_security_model_context(model_id)
    if not ctx:
        flash("Security model not found.", "error")
        return redirect(url_for("main.models"))
    return render_template("view_security_model.html", **ctx)


@main.route("/model/asset/<int:model_id>/edit", methods=["GET", "POST"])
@login_required
def edit_asset_model(model_id):
    from services.allocation_model_admin_service import edit_asset_model_context, update_asset_allocation_model_from_request

    if request.method == "POST":
        ok, err = update_asset_allocation_model_from_request(model_id, request.form)
        if err:
            flash(err, "error")
        else:
            flash("Asset model updated successfully.", "success")
            return redirect(url_for("main.view_asset_model", model_id=model_id))
    ctx = edit_asset_model_context(model_id)
    if not ctx:
        flash("Asset model not found.", "error")
        return redirect(url_for("main.models"))
    return render_template("edit_asset_model.html", **ctx)


@main.route("/model/security/<int:model_id>/edit", methods=["GET", "POST"])
@login_required
def edit_security_model(model_id):
    from services.allocation_model_admin_service import edit_security_model_context, update_security_allocation_model_from_request

    if request.method == "POST":
        ok, err = update_security_allocation_model_from_request(model_id, request.form)
        if err:
            flash(err, "error")
        else:
            flash("Security model updated successfully.", "success")
            return redirect(url_for("main.view_security_model", model_id=model_id))
    ctx = edit_security_model_context(model_id)
    if not ctx:
        flash("Security model not found.", "error")
        return redirect(url_for("main.models"))
    return render_template("edit_security_model.html", **ctx)


@main.route("/hot-stocks")
@login_required
def hot_stocks():
    from services.security_admin_service import build_hot_stocks_context

    return render_template("hot_stocks.html", **build_hot_stocks_context())


@main.route("/hot-stocks/add/<int:security_id>", methods=["POST"])
@login_required
def add_hot_stock(security_id):
    from services.security_admin_service import mark_hot

    ok, err = mark_hot(security_id, True)
    if err:
        flash(err, "error")
    else:
        flash("Added to hot stocks.", "success")
    return redirect(url_for("main.hot_stocks"))


@main.route("/hot-stocks/remove/<int:security_id>", methods=["POST"])
@login_required
def remove_hot_stock(security_id):
    from services.security_admin_service import mark_hot

    ok, err = mark_hot(security_id, False)
    if err:
        flash(err, "error")
    else:
        flash("Removed from hot stocks.", "success")
    return redirect(url_for("main.hot_stocks"))


@main.route("/hot-stocks/bulk-add", methods=["POST"])
@login_required
def bulk_add_hot_stocks():
    from services.security_admin_service import bulk_mark_hot

    ids = request.form.getlist("security_ids")
    try:
        id_ints = [int(x) for x in ids if x]
    except ValueError:
        id_ints = []
    n, _ = bulk_mark_hot(id_ints)
    flash(f"Marked {n} securities as hot.", "success")
    return redirect(url_for("main.hot_stocks"))


@main.route("/cron-management")
@login_required
def cron_management():
    return jsonify({"ok": True, "message": "Cron management placeholder"})


@main.route("/fetch-live-prices")
@login_required
def fetch_live_prices():
    return jsonify({"ok": True, "message": "Fetch live prices placeholder"})


@main.route("/load-stocks-from-sheets")
@login_required
def load_stocks_from_sheets():
    return jsonify({"ok": True, "message": "Load stocks from sheets placeholder"})


@main.route("/load-stocks-with-prices")
@login_required
def load_stocks_with_prices():
    return jsonify({"ok": True, "message": "Load stocks with prices placeholder"})


@main.route("/update-prices-from-sheets")
@login_required
def update_prices_from_sheets():
    return jsonify({"ok": True, "message": "Update prices from sheets placeholder"})


@main.route("/setup-stocks-and-scheduler")
@login_required
def setup_stocks_and_scheduler():
    return jsonify({"ok": True, "message": "Setup stocks and scheduler placeholder"})


@main.route("/service-readiness")
@login_required
def service_readiness():
    return jsonify({"ok": True, "message": "Service readiness placeholder"})


def _practice_analytics_denied():
    from utils.permissions import has_route_access

    if current_user.is_manager or has_route_access("main.practice_analytics"):
        return None
    flash("You do not have permission to access Practice Analytics.", "error")
    return redirect(url_for("main.dashboard"))


@main.route("/practice-analytics")
@login_required
def practice_analytics():
    redir = _practice_analytics_denied()
    if redir:
        return redir
    from services.practice_analytics_service import build_practice_analytics_context

    ctx = build_practice_analytics_context()
    return render_template("practice_analytics.html", **ctx)


@main.route("/practice-analytics/download")
@login_required
def download_practice_analytics():
    if not getattr(current_user, "is_admin", False):
        flash("Excel export requires admin privileges.", "error")
        return redirect(url_for("main.practice_analytics"))
    redir = _practice_analytics_denied()
    if redir:
        return redir
    from services.audit_service import log_data_export
    from services.practice_analytics_service import build_practice_analytics_workbook_bytes
    from services.protected_export_service import (
        PII_MASK_EXPORT_VERSION,
        attachment_response,
        mask_practice_analytics_workbook_bytes,
    )

    data, fname = build_practice_analytics_workbook_bytes()
    data = mask_practice_analytics_workbook_bytes(data)
    if "masked" not in fname:
        fname = fname.replace("practice_analytics_", "practice_analytics_masked_", 1)

    log_data_export(
        "practice_analytics_xlsx",
        details={
            "filename": fname,
            "pii_masked": True,
            "mask_version": PII_MASK_EXPORT_VERSION,
        },
    )
    return attachment_response(
        data,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@main.route("/alerts/generate")
@login_required
def generate_alerts():
    return jsonify({"ok": True, "message": "Generate alerts placeholder"})


@main.route("/admin")
@login_required
def admin_dashboard():
    return jsonify({"ok": True, "message": "Admin dashboard placeholder"})


@main.route("/admin/users")
@login_required
def user_management():
    return jsonify({"ok": True, "message": "User management placeholder"})


@main.route("/admin/system-settings")
@login_required
def system_settings():
    return jsonify({"ok": True, "message": "System settings placeholder"})


@main.route("/reports/monthly-investments-stage-aging")
@login_required
def monthly_investments_stage_aging_report():
    from services.monthly_investment_reports_service import build_stage_aging_report_context

    ctx = build_stage_aging_report_context()
    return render_template("reports/monthly_investments_stage_aging.html", **ctx)


@main.route("/reports/monthly-investments-days-from-recos")
@login_required
def monthly_investments_days_from_recos_report():
    from services.monthly_investment_reports_service import build_days_from_recos_report_context

    ctx = build_days_from_recos_report_context()
    return render_template("reports/monthly_investments_days_from_recos.html", **ctx)


@main.route("/tools/period-analysis-test")
@login_required
def period_analysis_test():
    return jsonify({"ok": True, "message": "Period analysis test – stub."})




# --- Extracted route modules (routes/main_sections/) ---
from routes.main_sections import register_all as _register_main_sections
_register_main_sections(main)

def calculate_xirr(cashflows, guess=0.1):
    # Lightweight fallback for modules importing routes.main.calculate_xirr.
    # Full analytics implementations can override this in dedicated services.
    if not cashflows:
        return 0.0
    return float(guess)


def calculate_nifty_xirr(*_args, **_kwargs):
    return 0.0


def update_holdings_from_transactions(*_args, **_kwargs):
    return {"ok": True}


def update_cashflows_from_transactions(*_args, **_kwargs):
    return {"ok": True}
