"""Regulatory IA statement — live SEBI grid + quarter/half/year freeze."""
from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from services.protected_export_service import attachment_response

logger = logging.getLogger(__name__)

regulatory_ia_statement_bp = Blueprint(
    "regulatory_ia_statement",
    __name__,
)


def _denied():
    from utils.permissions import has_route_access

    if current_user.is_manager or has_route_access(
        "regulatory_ia_statement.ia_statement"
    ):
        return None
    if has_route_access("regulatory_advisory_register.advisory_register"):
        return None
    if has_route_access("main.regulatory_client_master") or has_route_access(
        "main.practice_analytics"
    ):
        return None
    flash("You do not have permission to access the IA statement.", "error")
    return redirect(url_for("main.dashboard"))


@regulatory_ia_statement_bp.route("/regulatory/ia-statement")
@login_required
def ia_statement():
    redir = _denied()
    if redir:
        return redir

    from services.ia_regulatory_statement_service import (
        VALID_PERIOD_TYPES,
        build_statement_view,
        get_frozen_snapshot,
        list_frozen_snapshots,
        list_period_options,
        normalize_period_key,
    )
    from services.audit_service import log_audit_event

    period_type = (request.args.get("period_type") or "quarter").strip().lower()
    if period_type not in VALID_PERIOD_TYPES:
        period_type = "quarter"
    period_key = normalize_period_key(
        period_type, (request.args.get("period_key") or "").strip() or None
    )
    # Default: use frozen snapshot when present (live AUA recompute is slow).
    # Pass view=live to force a fresh compute.
    view_mode = (request.args.get("view") or "auto").strip().lower()

    has_frozen = get_frozen_snapshot(period_type, period_key) is not None
    if view_mode == "live":
        prefer_frozen = False
        effective_view = "live"
    elif view_mode == "frozen":
        prefer_frozen = True
        effective_view = "frozen"
    else:
        prefer_frozen = has_frozen
        effective_view = "frozen" if has_frozen else "live"

    payload = build_statement_view(
        period_type=period_type,
        period_key=period_key,
        prefer_frozen=prefer_frozen,
    )
    options = list_period_options()
    frozen_list = list_frozen_snapshots()

    try:
        log_audit_event(
            "view",
            resource_type="ia_regulatory_statement",
            details={
                "period_type": period_type,
                "period_key": period_key,
                "mode": payload.get("mode"),
            },
        )
    except Exception:
        pass

    return render_template(
        "regulatory/ia_statement.html",
        payload=payload,
        period_type=period_type,
        period_key=period_key,
        view_mode=effective_view,
        period_options=options,
        frozen_list=frozen_list,
        column_keys=("A", "B", "C", "D", "E", "F"),
        has_frozen=has_frozen,
    )


@regulatory_ia_statement_bp.route("/regulatory/ia-statement/download")
@login_required
def download_ia_statement():
    redir = _denied()
    if redir:
        return redir

    from services.ia_regulatory_statement_service import (
        build_statement_view,
        build_workbook_bytes,
        normalize_period_key,
    )
    from services.audit_service import log_data_export

    period_type = (request.args.get("period_type") or "quarter").strip().lower()
    period_key = normalize_period_key(
        period_type, (request.args.get("period_key") or "").strip() or None
    )
    prefer_frozen = (request.args.get("view") or "live").strip().lower() == "frozen"
    payload = build_statement_view(
        period_type=period_type,
        period_key=period_key,
        prefer_frozen=prefer_frozen,
    )
    data, fname = build_workbook_bytes(payload)
    try:
        log_data_export(
            resource_type="ia_regulatory_statement",
            details={"period_key": period_key, "filename": fname},
        )
    except Exception:
        pass
    return attachment_response(data, fname)


@regulatory_ia_statement_bp.route("/regulatory/ia-statement/freeze", methods=["POST"])
@login_required
def freeze_ia_statement():
    redir = _denied()
    if redir:
        return redir

    from services.ia_regulatory_statement_service import (
        freeze_statement,
        normalize_period_key,
    )

    period_type = (request.form.get("period_type") or "quarter").strip().lower()
    period_key = normalize_period_key(
        period_type, (request.form.get("period_key") or "").strip() or None
    )
    force = request.form.get("force") == "1"

    result = freeze_statement(
        period_type=period_type,
        period_key=period_key,
        user_id=getattr(current_user, "id", None),
        force=force,
    )
    if not result.get("ok"):
        flash(result.get("error") or "Freeze failed.", "error")
    elif result.get("already_frozen"):
        flash(f"Period {period_key} was already frozen.", "info")
    else:
        flash(f"Frozen snapshot for {period_key}.", "success")
    return redirect(
        url_for(
            "regulatory_ia_statement.ia_statement",
            period_type=period_type,
            period_key=period_key,
            view="frozen",
        )
    )


@regulatory_ia_statement_bp.route(
    "/regulatory/ia-statement/confirm-active", methods=["POST"]
)
@login_required
def confirm_client_active():
    """Confirm unbilled client remains active, or mark inactive."""
    redir = _denied()
    if redir:
        return redir

    from models import Client
    from extensions import db
    from services.client_regulatory_flags_service import apply_active_change

    client_id = request.form.get("client_id", type=int)
    action = (request.form.get("action") or "keep_active").strip()
    period_type = (request.form.get("period_type") or "quarter").strip()
    period_key = (request.form.get("period_key") or "").strip()

    client = db.session.get(Client, client_id) if client_id else None
    if not client:
        flash("Client not found.", "error")
    elif action == "mark_inactive":
        apply_active_change(client, False)
        db.session.commit()
        flash(f'Marked "{client.name}" inactive.', "success")
    else:
        flash(f'Confirmed "{client.name}" remains active.', "success")

    return redirect(
        url_for(
            "regulatory_ia_statement.ia_statement",
            period_type=period_type,
            period_key=period_key or None,
            view="live",
        )
    )
