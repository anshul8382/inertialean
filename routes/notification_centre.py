"""Notification centre UI page."""
from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from services import notification_centre_service as ncs
from services import peer_message_service as pms
from services.db_cutover import peer_messages_enabled

notification_centre_ui_bp = Blueprint("notification_centre", __name__)


@notification_centre_ui_bp.route("/notifications")
@login_required
def centre():
    view = (request.args.get("view") or "open").lower().strip()
    if view not in ("open", "snoozed", "messages"):
        view = "open"
    scope = (request.args.get("scope") or "mine").lower().strip()
    payload = ncs.list_for_user(current_user, view=view, scope=scope)
    peers = pms.list_recipients(current_user.id) if peer_messages_enabled() else []
    return render_template(
        "notifications/centre.html",
        payload=payload,
        items=payload.get("items") or [],
        sections=payload.get("sections") or [],
        view=view,
        scope=payload.get("scope") or "mine",
        can_firm_scope=payload.get("can_firm_scope"),
        peers=peers,
        peer_enabled=peer_messages_enabled(),
    )
