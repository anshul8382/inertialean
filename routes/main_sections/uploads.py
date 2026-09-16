"""Maintenance uploads and CSV templates (extracted from routes/main.py)."""
from __future__ import annotations

import csv
import io

from flask import current_app, redirect, render_template, request, send_file, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from access_control import require_advisor_or_manager, require_admin
from services.pii_masking import mask_client_name, mask_email
from services.protected_export_service import PII_MASK_EXPORT_VERSION, attachment_response
from extensions import db


def _maintenance_clients_ordered():
    from access_control import get_accessible_clients_ordered
    return get_accessible_clients_ordered()


def register(main):

    # --- Maintenance: data uploads (advisor/manager) ---

    @main.route("/upload/securities", methods=["GET", "POST"])
    @login_required
    @require_advisor_or_manager
    def upload_securities():
        from models import AssetClass, Security
        from routes.forms import SecuritiesCSVUploadForm
        from services.securities_csv_upload_service import process_securities_csv

        form = SecuritiesCSVUploadForm()
        upload_results = None
        if form.validate_on_submit():
            r = process_securities_csv(
                form.file.data,
                has_header=bool(form.has_header.data),
                user_id=current_user.id,
                db_session=db.session,
                Security=Security,
                AssetClass=AssetClass,
            )
            upload_results = {
                "success_count": r["success_count"],
                "error_count": r["error_count"],
                "errors": r["errors"],
            }
            if r["success_count"]:
                try:
                    db.session.commit()
                    msg = f"Imported {r['success_count']} securities."
                    if r["error_count"]:
                        msg += f" {r['error_count']} row(s) skipped — see below."
                        flash(msg, "warning")
                    else:
                        flash(msg, "success")
                except Exception as exc:
                    db.session.rollback()
                    current_app.logger.exception("securities csv commit")
                    flash(f"Database error: {exc}", "danger")
            else:
                db.session.rollback()
                flash("No securities were imported.", "warning")
        elif request.method == "POST":
            flash("Please correct the form errors.", "danger")
        return render_template("upload_securities.html", form=form, upload_results=upload_results)


    @main.route("/upload/trades")
    @login_required
    @require_advisor_or_manager
    def trade_upload():
        clients = _maintenance_clients_ordered()
        return render_template("trade_upload.html", clients=clients)


    @main.route("/upload/mprofit-trades")
    @login_required
    @require_advisor_or_manager
    def mprofit_trade_upload():
        clients = _maintenance_clients_ordered()
        return render_template("mprofit_trade_upload.html", clients=clients)


    @main.route("/upload/cashflows")
    @login_required
    @require_advisor_or_manager
    def cashflow_upload():
        clients = _maintenance_clients_ordered()
        return render_template("cashflow_upload.html", clients=clients)


    @main.route("/upload/clients")
    @login_required
    @require_advisor_or_manager
    def client_upload():
        return render_template("client_upload.html")


    @main.route("/maintenance/download/cashflow-template")
    @login_required
    @require_advisor_or_manager
    def download_cashflow_template():
        from services.audit_service import log_data_export

        log_data_export("cashflow_upload_template_csv")
        csv_content = "Date,Amount,asset_type\n1-Nov-2023,-50000,Equity\n"
        return send_file(
            io.BytesIO(csv_content.encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name="cashflow_upload_template.csv",
        )


    @main.route("/maintenance/download/client-template")
    @login_required
    @require_advisor_or_manager
    def download_client_template():
        headers = [
            "Name",
            "Email",
            "Phone",
            "Address",
            "Risk Profile",
            "Date of Birth",
            "Starting AUA",
            "Designation",
            "LinkedIn Profile URL",
            "Date of Joining",
            "Type of Engagement",
            "Portfolio Inherited",
            "Company Name",
            "Industry",
        ]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        w.writerow(
            [
                "Sample Client",
                "sample@example.com",
                "",
                "",
                "Moderate",
                "15-Nov-1985",
                "0",
                "",
                "",
                "",
                "SIP",
                "No",
                "",
                "",
            ]
        )
        from services.audit_service import log_data_export

        log_data_export("client_upload_template_csv")
        data = buf.getvalue().encode("utf-8")
        return send_file(
            io.BytesIO(data),
            mimetype="text/csv",
            as_attachment=True,
            download_name="client_upload_template.csv",
        )


    @main.route("/maintenance/download/client-data")
    @login_required
    @require_admin
    def download_client_data():
        from models import Client
        from services.audit_service import log_data_export

        log_data_export(
            "clients_bulk_csv",
            details={
                "row_count": Client.query.count(),
                "pii_masked": True,
                "mask_version": PII_MASK_EXPORT_VERSION,
            },
        )
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "Name",
                "Email",
                "Phone",
                "Address",
                "Risk Profile",
                "Date of Birth",
                "Starting AUA",
                "Designation",
                "LinkedIn Profile URL",
                "Date of Joining",
                "Type of Engagement",
                "Portfolio Inherited",
                "Company Name",
                "Industry",
            ]
        )
        from access_control import get_accessible_clients_ordered
        for c in get_accessible_clients_ordered():
            w.writerow(
                [
                    mask_client_name(c.name),
                    mask_email(c.email),
                    c.phone or "",
                    c.address or "",
                    c.risk_profile or "",
                    c.date_of_birth.isoformat() if c.date_of_birth else "",
                    str(c.starting_aua) if c.starting_aua is not None else "",
                    c.designation or "",
                    c.linkedin_profile_url or "",
                    c.date_of_joining.isoformat() if c.date_of_joining else "",
                    c.type_of_engagement or "",
                    "Yes" if c.portfolio_inherited else "No",
                    c.company_name or "",
                    c.industry or "",
                ]
            )
        data = buf.getvalue().encode("utf-8")
        return attachment_response(
            data,
            mimetype="text/csv; charset=utf-8",
            download_name="clients_export_masked.csv",
        )


    @main.route("/upload/corporate-actions")
    @login_required
    @require_advisor_or_manager
    def corporate_actions_upload():
        if not current_app.view_functions.get("corporate_actions.upload_corporate_actions"):
            flash("Corporate actions module is not enabled on this deployment.", "warning")
            return redirect(url_for("main.dashboard"))
        return redirect(url_for("corporate_actions.upload_corporate_actions"))


