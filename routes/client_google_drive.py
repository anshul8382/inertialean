"""Client Google Drive folder linking + Suitability Report generate/download."""
from __future__ import annotations

import logging

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

client_google_drive_bp = Blueprint("client_google_drive", __name__)


def _require_client(client_id: int):
    from access_control import can_access_client
    from models import Client

    if not can_access_client(client_id):
        flash("You do not have access to this client.", "error")
        return None, redirect(url_for("main.dashboard"))
    client = Client.query.get_or_404(client_id)
    return client, None


@client_google_drive_bp.route("/advisor/client-drive-folders")
@login_required
def list_client_drive_folders():
    from access_control import get_accessible_clients_ordered
    from services.client_google_drive_service import (
        client_drive_columns_ready,
        folder_url,
        service_account_email,
    )

    clients = get_accessible_clients_ordered()
    ready = client_drive_columns_ready()
    missing_rows = []
    linked_rows = []
    for c in clients:
        fid = (getattr(c, "google_drive_folder_id", None) or "").strip() if ready else ""
        row = {
            "client": c,
            "folder_id": fid,
            "folder_url": folder_url(fid) if fid else "",
            "note": (getattr(c, "google_drive_folder_note", None) or "") if ready else "",
        }
        if fid:
            linked_rows.append(row)
        else:
            missing_rows.append(row)

    edit_id = request.args.get("edit", type=int)
    edit_row = None
    if linked_rows:
        if edit_id:
            edit_row = next((r for r in linked_rows if r["client"].id == edit_id), None)
        if edit_row is None:
            edit_row = linked_rows[0]

    return render_template(
        "advisor/client_drive_folders.html",
        missing_rows=missing_rows,
        linked_rows=linked_rows,
        edit_row=edit_row,
        sa_email=service_account_email(),
        columns_ready=ready,
    )


@client_google_drive_bp.route(
    "/clients/<int:client_id>/google-drive", methods=["POST"]
)
@login_required
def save_client_google_drive(client_id: int):
    from extensions import db
    from services.client_google_drive_service import (
        client_drive_columns_ready,
        normalize_folder_id,
    )

    client, redir = _require_client(client_id)
    if redir:
        return redir
    if not client_drive_columns_ready():
        flash(
            "Database columns missing. Run: python migrations/add_client_google_drive_folder.py",
            "error",
        )
        return redirect(url_for("clients.client_details", client_id=client_id))

    folder_id = normalize_folder_id(request.form.get("google_drive_folder_id"))
    note = (request.form.get("google_drive_folder_note") or "").strip()[:255]
    client.google_drive_folder_id = folder_id or None
    client.google_drive_folder_note = note or None
    db.session.commit()
    flash(
        "Google Drive folder saved." if folder_id else "Google Drive folder cleared.",
        "success",
    )
    next_url = (request.form.get("next") or "").strip()
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("clients.client_details", client_id=client_id))


@client_google_drive_bp.route(
    "/clients/<int:client_id>/suitability-report/generate", methods=["POST"]
)
@login_required
def generate_suitability_report(client_id: int):
    from services.audit_service import log_audit_event
    from services.suitability_report_service import create_and_upload_suitability_report

    client, redir = _require_client(client_id)
    if redir:
        return redir

    result = create_and_upload_suitability_report(client=client, user=current_user)
    if not result.get("ok"):
        flash(result.get("error") or "Could not generate suitability report.", "error")
        return redirect(url_for("clients.client_details", client_id=client_id))

    try:
        log_audit_event(
            "create",
            resource_type="suitability_report",
            resource_id=str(getattr(result.get("report"), "id", "") or ""),
            client_id=client_id,
            details={
                "drive_file_id": result.get("file_id"),
                "title": result.get("title"),
            },
        )
    except Exception:
        pass

    link = result.get("web_view_link")
    flash(
        "Suitability report created in Google Drive. Open it to edit.",
        "success",
    )
    if link:
        return redirect(link)
    return redirect(url_for("clients.client_details", client_id=client_id))


@client_google_drive_bp.route(
    "/clients/<int:client_id>/suitability-report/<int:report_id>/download"
)
@login_required
def download_suitability_report(client_id: int, report_id: int):
    from flask import Response

    from models.suitability_report import SuitabilityReport
    from services.audit_service import log_data_export
    from services.client_google_drive_service import (
        export_google_doc_as_docx,
        suitability_table_ready,
    )

    client, redir = _require_client(client_id)
    if redir:
        return redir
    if not suitability_table_ready():
        flash("Suitability report table not ready.", "error")
        return redirect(url_for("clients.client_details", client_id=client_id))

    report = SuitabilityReport.query.filter_by(
        id=report_id, client_id=client_id
    ).first_or_404()
    if not report.drive_file_id:
        flash("This report has no Drive file id.", "error")
        return redirect(url_for("clients.client_details", client_id=client_id))

    exported = export_google_doc_as_docx(report.drive_file_id)
    if not exported.get("ok"):
        flash(exported.get("error") or "Download failed.", "error")
        return redirect(url_for("clients.client_details", client_id=client_id))

    try:
        log_data_export(
            "suitability_report_docx",
            client_id=client_id,
            resource_type="suitability_report",
            resource_id=str(report_id),
        )
    except Exception:
        pass

    fname = f"suitability_report_{client_id}_{report_id}.docx"
    return Response(
        exported["content"],
        mimetype=exported.get("mimetype")
        or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
