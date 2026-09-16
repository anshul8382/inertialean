"""Admin / maintenance tools (extracted from routes/main.py)."""
from __future__ import annotations

import io
import os

from flask import abort, current_app, jsonify, redirect, render_template, request, send_file, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from access_control import require_advisor_or_manager, require_holdings_report_access, require_manager
from extensions import db


def register(main):

    @main.route("/tools/database-browser")
    @login_required
    def database_browser():
        if not current_user.is_admin:
            abort(403)
        from services.database_tool_service import build_database_browser_context

        ctx = build_database_browser_context()
        return render_template("tools/database_browser.html", **ctx)


    @main.route("/tools/database-browser/preview/<string:table_name>")
    @login_required
    def database_browser_preview(table_name):
        if not current_user.is_admin:
            abort(403)
        from services.database_tool_service import preview_table_data

        limit = request.args.get("limit", default=50, type=int)
        return jsonify(preview_table_data(table_name, limit))


    @main.route("/tools/sql-query", methods=["GET", "POST"])
    @login_required
    def sql_query():
        if not current_user.is_admin:
            abort(403)
        from services.database_tool_service import (
            execute_readonly_query,
            recent_query_history_for_user,
            record_sql_history,
            validate_readonly_select,
        )

        query_text = ""
        error = None
        results = None
        execution_time = None
        row_count = None

        if request.method == "POST":
            query_text = (request.form.get("query") or "").strip()
            ok, normalized = validate_readonly_select(query_text)
            if not ok:
                error = normalized
                record_sql_history(
                    current_user.id,
                    query_text,
                    success=False,
                    error_message=error,
                    row_count=None,
                    execution_ms=None,
                )
            else:
                rows, _cols, ms, exec_err = execute_readonly_query(normalized)
                if exec_err:
                    error = exec_err
                    record_sql_history(
                        current_user.id,
                        query_text,
                        success=False,
                        error_message=exec_err,
                        row_count=None,
                        execution_ms=round(ms, 2) if ms is not None else None,
                    )
                else:
                    results = rows
                    execution_time = round(ms, 2) if ms is not None else None
                    row_count = len(rows)
                    record_sql_history(
                        current_user.id,
                        query_text,
                        success=True,
                        error_message=None,
                        row_count=row_count,
                        execution_ms=execution_time,
                    )

        query_history = recent_query_history_for_user(current_user.id)
        return render_template(
            "tools/sql_query.html",
            query=query_text,
            error=error,
            results=results,
            execution_time=execution_time,
            row_count=row_count,
            query_history=query_history,
        )


    @main.route("/tools/sql-query/schema")
    @login_required
    def sql_query_schema():
        if not current_user.is_admin:
            abort(403)
        from services.database_tool_service import build_sql_schema_api_payload

        return jsonify(build_sql_schema_api_payload())


    @main.route("/tools/data-uploads")
    @login_required
    @require_advisor_or_manager
    def data_upload_links():
        """Single-page hub listing all data upload entry points."""
        return render_template("tools/data_upload_links.html")


    @main.route("/maintenance/trade-data")
    @login_required
    @require_advisor_or_manager
    def maintenance_trade_data():
        """Hub: trade data and related reports (linked from Maintenance menu)."""
        from models import Client

        from access_control import get_accessible_clients_ordered
        clients = get_accessible_clients_ordered()
        return render_template("maintenance/trade_data.html", clients=clients)


    @main.route("/maintenance/reference-data")
    @login_required
    @require_advisor_or_manager
    def maintenance_reference_data():
        """Hub: reference data admin (linked from Maintenance menu)."""
        return render_template("maintenance/reference_data.html")


    @main.route("/maintenance/recommendations")
    @login_required
    @require_advisor_or_manager
    def maintenance_recommendations():
        """Hub: recommendations maintenance (linked from Maintenance menu)."""
        from models import Client

        from access_control import get_accessible_clients_ordered
        clients = get_accessible_clients_ordered()
        return render_template("maintenance/recommendations.html", clients=clients)


    @main.route("/maintenance/reviews")
    @login_required
    @require_advisor_or_manager
    def maintenance_reviews():
        """Hub: review tooling (linked from Maintenance menu)."""
        return render_template("maintenance/reviews.html")


    @main.route("/maintenance/portfolio-modelling")
    @login_required
    @require_advisor_or_manager
    def maintenance_portfolio_modelling():
        """Hub: portfolio modelling tools (linked from Maintenance menu)."""
        return render_template("maintenance/portfolio_modelling.html")

    @main.route("/maintenance/billing")
    @login_required
    @require_advisor_or_manager
    def maintenance_billing():
        """Hub: billing & invoices tooling (linked from Maintenance menu)."""
        return render_template("maintenance/billing.html")

    @main.route("/maintenance/billing/record-agreement", methods=["GET", "POST"])
    @login_required
    @require_advisor_or_manager
    def maintenance_record_existing_agreement():
        """Picker: choose a client, then record existing agreement."""
        from access_control import get_accessible_clients

        clients = get_accessible_clients()
        if request.method == "POST":
            raw_client_id = (request.form.get("client_id") or "").strip()
            if not raw_client_id.isdigit():
                flash("Please select a client.", "error")
                return render_template("maintenance/record_existing_agreement.html", clients=clients)
            client_id = int(raw_client_id)
            allowed_ids = {c.id for c in clients}
            if client_id not in allowed_ids:
                flash("You do not have access to this client.", "error")
                return render_template("maintenance/record_existing_agreement.html", clients=clients)
            return redirect(url_for("agreements.record_existing_agreement_for_client", client_id=client_id))

        return render_template("maintenance/record_existing_agreement.html", clients=clients)


    @main.route("/scheduler-status")
    @login_required
    def scheduler_status():
        return jsonify({"ok": True, "message": "Scheduler status – stub."})


    @main.route("/admin/mprofit-mappings")
    @login_required
    def admin_mprofit_mappings():
        return jsonify({"ok": True, "message": "MProfit mappings admin – stub."})


    @main.route("/tools/capital-gains")
    @login_required
    @require_advisor_or_manager
    def capital_gains_report():
        from services.capital_gains_service import get_capital_gains_report

        client_name_filter = (request.args.get("client_name") or "").strip()
        data = get_capital_gains_report(client_name_filter=client_name_filter or None)
        return render_template(
            "tools/capital_gains.html",
            fy_start=data["fy_start"],
            fy_end=data["fy_end"],
            client_name_filter=client_name_filter,
            clients=data["clients"],
        )


    @main.route("/tools/tax-optimiser")
    @login_required
    @require_advisor_or_manager
    def tax_optimiser():
        from services.tax_optimiser_page_service import build_tax_optimiser_page_context
        from services.tax_optimiser_settings_service import get_trade_charges_pct

        client_name_filter = (request.args.get("client_name") or "").strip()
        raw_tcp = request.args.get("trade_charges_pct")
        tcp_override = None
        if raw_tcp is not None and str(raw_tcp).strip() != "":
            try:
                tcp_override = float(raw_tcp)
            except (TypeError, ValueError):
                tcp_override = None
        if tcp_override is not None:
            tcp = tcp_override
        else:
            try:
                tcp = get_trade_charges_pct()
            except Exception:
                tcp = 0.1
        ctx = build_tax_optimiser_page_context(
            client_name_filter=client_name_filter,
            trade_charges_pct=tcp,
            trade_charges_pct_override=tcp_override,
        )
        return render_template("tools/tax_optimiser.html", **ctx)


    @main.route("/tools/holdings-report")
    @login_required
    @require_holdings_report_access
    def holdings_report():
        from services.holdings_report_service import get_holdings_report_rows
        from services.permission_service import user_can_view_sensitive_client_data

        client_name_filter = (request.args.get("client_name") or "").strip()
        security_name_filter = (request.args.get("security_name") or "").strip()
        rows = get_holdings_report_rows(
            client_name_filter=client_name_filter or None,
            security_name_filter=security_name_filter or None,
            mask_sensitive=not user_can_view_sensitive_client_data(current_user),
        )
        return render_template(
            "tools/holdings.html",
            rows=rows,
            client_name_filter=client_name_filter,
            security_name_filter=security_name_filter,
        )


    @main.route("/admin/missing-links")
    @login_required
    def missing_links_review():
        if not current_user.is_admin:
            abort(403)

        missing_routes = _collect_missing_routes(include_parameterized=True)
        direct_missing_routes = [row for row in missing_routes if not row["has_params"]]
        parameterized_missing_routes = [row for row in missing_routes if row["has_params"]]
        return render_template(
            "admin/missing_links_review.html",
            missing_routes=missing_routes,
            direct_missing_routes=direct_missing_routes,
            parameterized_missing_routes=parameterized_missing_routes,
        )


    @main.route("/documents/<int:document_id>/download")
    @login_required
    def download_document(document_id):
        """Serve a client Document file (uploads live under app UPLOAD_FOLDER)."""
        from models import Document

        doc = Document.query.get_or_404(document_id)
        fp = (doc.file_path or "").replace("\\", "/").strip()
        if not fp or fp.startswith("/") or ".." in fp.split("/"):
            abort(404)

        root = current_app.root_path
        upload_dir = os.path.normpath(
            current_app.config.get("UPLOAD_FOLDER") or os.path.join(root, "uploads")
        )
        abs_path = os.path.normpath(os.path.join(root, fp))
        abs_real = os.path.realpath(abs_path)
        upload_real = os.path.realpath(upload_dir)
        if abs_real != upload_real and not abs_real.startswith(upload_real + os.sep):
            abort(404)
        if not os.path.isfile(abs_real):
            abort(404)

        from services.audit_service import log_data_export

        log_data_export(
            "client_document",
            client_id=doc.client_id,
            resource_type="document",
            resource_id=document_id,
        )
        download_name = os.path.basename(abs_real)
        return send_file(abs_real, as_attachment=True, download_name=download_name)

