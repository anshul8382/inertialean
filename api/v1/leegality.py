"""Leegality inbound webhooks (HMAC-verified via Private Salt)."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.leegality_service import apply_webhook_payload

logger = logging.getLogger(__name__)

# Mounted at /api/v1/leegality via api.v1._safe_register(..., url_prefix="/leegality")
leegality_bp = Blueprint("leegality", __name__)


@leegality_bp.route("/webhook", methods=["POST"])
def webhook():
    """
    Configure this URL on each invitee in the Leegality workflow:
    https://<your-host>/api/v1/leegality/webhook
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "json_required"}), 400

    result = apply_webhook_payload(payload)
    if not result.get("ok"):
        err = result.get("error") or "rejected"
        status = 403 if err == "invalid_mac" else 400
        logger.warning("Leegality webhook rejected: %s", err)
        return jsonify(result), status

    return jsonify(result), 200
