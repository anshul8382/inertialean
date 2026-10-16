"""Notification centre UI page."""
from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from services import notification_centre_service as ncs
from services import peer_message_service as pms
from services.db_cutover import peer_messages_enabled
from services.distributor_preview import notification_subject_user

notification_centre_ui_bp = Blueprint("notification_centre", __name__)


@notification_centre_ui_bp.route("/notifications")
@login_required
def centre():
    view = (request.args.get("view") or "open").lower().strip()
    if view not in ("open", "snoozed", "messages"):
        view = "open"
    scope = (request.args.get("scope") or "mine").lower().strip()
    subject, is_preview = notification_subject_user(current_user)
    payload = ncs.list_for_user(subject, view=view, scope=scope)
    distributor_mode = bool(payload.get("distributor_mode")) or is_preview
    peer_on = bool(payload.get("peer_enabled")) and not is_preview
    peers = (
        pms.list_recipients(subject.id, from_user=subject) if peer_on else []
    )
    if is_preview:
        # Show who the distributor could message (read-only list for context)
        peers = pms.list_recipients(subject.id, from_user=subject)
        peer_on = bool(payload.get("peer_enabled"))
    return render_template(
        "notifications/centre.html",
        payload=payload,
        items=payload.get("items") or [],
        sections=payload.get("sections") or [],
        view=view,
        scope=payload.get("scope") or "mine",
        can_firm_scope=payload.get("can_firm_scope") and not distributor_mode and not is_preview,
        peers=peers,
        peer_enabled=peer_on,
        distributor_mode=distributor_mode,
        preview_readonly=is_preview,
    )
