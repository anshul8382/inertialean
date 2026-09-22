"""Notification centre API — findings + peer messages."""
from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from extensions import csrf
from services import notification_centre_service as ncs
from services import peer_message_service as pms

logger = logging.getLogger(__name__)

notification_centre_bp = Blueprint("notification_centre_api", __name__)


@notification_centre_bp.route("/notifications", methods=["GET"])
@login_required
def list_notifications():
    view = (request.args.get("view") or "open").lower().strip()
    scope = (request.args.get("scope") or "mine").lower().strip()
    payload = ncs.list_for_user(current_user, view=view, scope=scope)
    return jsonify(payload)


@notification_centre_bp.route("/notifications/count", methods=["GET"])
@login_required
def notification_count():
    from services.db_cutover import finding_notification_enabled, peer_messages_enabled

    # Bell always uses mine scope so managers are not flooded by firm radar.
    payload = ncs.list_for_user(current_user, view="open", scope="mine")
    urgent = payload.get("urgent_count") or 0
    messages = payload.get("message_count") or 0
    open_count = payload.get("count") or 0
    bell = urgent + messages
    if bell == 0:
        bell = open_count
    return jsonify(
        {
            "enabled": bool(
                finding_notification_enabled() or peer_messages_enabled()
            ),
            "count": open_count,
            "urgent_count": urgent,
            "message_count": messages,
            "bell_count": bell,
        }
    )


@notification_centre_bp.route("/notifications/decide", methods=["POST"])
@login_required
@csrf.exempt
def decide_notification():
    data = request.get_json(silent=True) or {}
    entity_type = (data.get("entity_type") or "").strip()
    entity_id = data.get("entity_id")
    decision = (data.get("decision") or "").strip()
    signal_id = data.get("signal_id")
    reason = data.get("reason")
    create_task = bool(data.get("create_task"))

    if not entity_type or not entity_id or not decision:
        return jsonify({"ok": False, "error": "missing_fields"}), 400

    snooze_until = None
    if data.get("snooze_until"):
        try:
            snooze_until = datetime.fromisoformat(str(data["snooze_until"]).replace("Z", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_snooze_until"}), 400

    task_deadline = None
    if data.get("task_deadline"):
        try:
            task_deadline = datetime.fromisoformat(str(data["task_deadline"]).replace("Z", ""))
        except ValueError:
            return jsonify({"ok": False, "error": "invalid_task_deadline"}), 400

    if entity_type == "peer_message":
        result = pms.apply_decision(
            current_user,
            message_id=int(entity_id),
            decision=decision,
            snooze_until=snooze_until,
        )
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    result = ncs.apply_decision(
        current_user,
        entity_type=entity_type,
        entity_id=int(entity_id),
        decision=decision,
        signal_id=signal_id,
        snooze_until=snooze_until,
        reason=reason,
        create_task=create_task,
        task_deadline=task_deadline,
        member_ids=data.get("member_ids"),
    )
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@notification_centre_bp.route("/notifications/peers", methods=["GET"])
@login_required
def list_peers():
    return jsonify({"ok": True, "users": pms.list_recipients(current_user.id)})


@notification_centre_bp.route("/notifications/message", methods=["POST"])
@login_required
@csrf.exempt
def send_peer_message():
    data = request.get_json(silent=True) or {}
    to_user_id = data.get("to_user_id")
    body = data.get("body") or ""
    if not to_user_id:
        return jsonify({"ok": False, "error": "missing_to_user"}), 400
    result = pms.send_message(
        from_user=current_user, to_user_id=int(to_user_id), body=body
    )
    status = 200 if result.get("ok") else 400
    return jsonify(result), status
