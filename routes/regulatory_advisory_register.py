"""Regulatory Advisory Register — view, Excel download, FY import."""
from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from services.protected_export_service import attachment_response

logger = logging.getLogger(__name__)

regulatory_advisory_register_bp = Blueprint(
    "regulatory_advisory_register",
    __name__,
)


def _denied():
    from utils.permissions import has_route_access

    if current_user.is_manager or has_route_access(
        "regulatory_advisory_register.advisory_register"
    ):
        return None
    if has_route_access("main.regulatory_client_master") or has_route_access(
        "main.practice_analytics"
    ):
        return None
    flash("You do not have permission to access the Advisory Register.", "error")
    return redirect(url_for("main.dashboard"))


@regulatory_advisory_register_bp.route("/regulatory/advisory-register")
@login_required
def advisory_register():
    redir = _denied()
    if redir:
        return redir
    from services.advisory_register_service import (
        available_fy_options,
        list_register_entries,
    )
    from services.audit_service import log_audit_event

    fy = (request.args.get("fy") or "2025-26").strip()
    payload = list_register_entries(fy_label=fy)
    try:
        log_audit_event(
            "view",
            resource_type="advisory_register",
            details={"fy": fy, "row_count": payload.get("row_count")},
        )
    except Exception:
        pass
    return render_template(
        "regulatory/advisory_register.html",
        payload=payload,
        fy_options=available_fy_options(),
        fy_param=fy,
    )


@regulatory_advisory_register_bp.route("/regulatory/advisory-register/download")
@login_required
def download_advisory_register():
    redir = _denied()
    if redir:
        return redir
    from services.advisory_register_service import (
        build_workbook_bytes,
        list_register_entries,
    )
    from services.audit_service import log_data_export

    fy = (request.args.get("fy") or "2025-26").strip()
    payload = list_register_entries(fy_label=fy)
    data, fname = build_workbook_bytes(payload)
    try:
        log_data_export(
            "advisory_register_xlsx",
            details={"fy": fy, "row_count": payload.get("row_count")},
        )
    except Exception:
        pass
    return attachment_response(
        data,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@regulatory_advisory_register_bp.route(
    "/regulatory/advisory-register/import-fy", methods=["POST"]
)
@login_required
def import_advisory_register_fy():
    redir = _denied()
    if redir:
        return redir
    if not (current_user.is_manager or getattr(current_user, "is_admin", False)):
        flash("Only managers can run FY import.", "error")
        return redirect(url_for("regulatory_advisory_register.advisory_register"))

    from services.advisory_register_service import run_fy_import
    from services.db_cutover import advisory_register_enabled

    fy = (request.form.get("fy") or "2025-26").strip()
    dry_run = (request.form.get("dry_run") or "").lower() in ("1", "true", "yes", "on")

    if not advisory_register_enabled():
        flash(
            "Advisory register table is missing. Run: python migrations/add_advisory_register_entry.py",
            "error",
        )
        return redirect(
            url_for("regulatory_advisory_register.advisory_register", fy=fy)
        )

    try:
        result = run_fy_import(
            fy_label=fy,
            user_id=current_user.id,
            dry_run=dry_run,
            fetch_gmail=True,
        )
    except Exception as exc:
        logger.exception("Advisory register FY import failed")
        flash(f"Import failed: {exc}", "error")
        return redirect(
            url_for("regulatory_advisory_register.advisory_register", fy=fy)
        )

    if not result.get("ok"):
        flash(result.get("error") or "Import failed", "error")
    else:
        g = result.get("gmail") or {}
        d = result.get("db_backfill") or {}
        prefix = "[Dry run] " if dry_run else ""
        flash(
            f"{prefix}Gmail: {'skipped (FY 26+ policy)' if not result.get('gmail_used') else 'used'}; "
            f"fetched {g.get('fetched', 0)}, "
            f"DB-matched {g.get('from_db', 0)}, parsed {g.get('from_gmail_parse', 0)}; "
            f"DB backfill added {d.get('added', 0)}. "
            f"Gmail stop from: {result.get('gmail_stop_from') or 'n/a'}.",
            "success",
        )
    return redirect(url_for("regulatory_advisory_register.advisory_register", fy=fy))
