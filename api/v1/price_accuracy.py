#!/usr/bin/env python3
"""
Server-to-server hooks for Google Apps Script / automation:
 • acknowledge a verified consecutive-date spike pair
 • apply historical closes from a correction queue

Set PRICE_ACCURACY_API_TOKEN on the server; send header X-Price-Accuracy-Token.
"""

from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from services.price_accuracy_service import (
    acknowledge_spike,
    apply_historical_price_fixes,
    close_finding_verified_from_sheet,
)


price_accuracy_bp = Blueprint("price_accuracy", __name__, url_prefix="/price-accuracy")


def _bad_token_resp():
    cfg = current_app.config.get("PRICE_ACCURACY_API_TOKEN") if current_app else None
    if not (cfg or "").strip():
        return jsonify({"ok": False, "error": "PRICE_ACCURACY_API_TOKEN is not configured on the server"}), 503

    hdr = request.headers.get("X-Price-Accuracy-Token", "").strip()
    auth = request.headers.get("Authorization", "").strip()
    if not hdr and auth.lower().startswith("bearer "):
        hdr = auth[7:].strip()
    token = hdr
    expected = str(current_app.config.get("PRICE_ACCURACY_API_TOKEN", "")).strip()
    if not token or token != expected:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


def _parse_date_only(s):
    if not s:
        return None
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


@price_accuracy_bp.route("/webhook/spike-ack", methods=["POST"])
def webhook_spike_ack():
    chk = _bad_token_resp()
    if chk:
        return chk
    payload = request.get_json(silent=True) or {}

    sid = payload.get("security_id")
    d_prev_raw = payload.get("date_prev")
    d_next_raw = payload.get("date_next")
    if sid is None or not d_prev_raw or not d_next_raw:
        return jsonify({"ok": False, "error": "security_id, date_prev, date_next required"}), 400

    notes = payload.get("notes")
    src = payload.get("source") or "google_sheets_webhook"

    try:
        d_prev = _parse_date_only(d_prev_raw)
        d_next = _parse_date_only(d_next_raw)
        ack = acknowledge_spike(
            security_id=int(sid),
            date_prev=d_prev,
            date_next=d_next,
            source=src[:30],
            user_id=None,
            notes=str(notes)[:500] if notes is not None else None,
        )
        return jsonify({"ok": True, "ack_id": ack.id if ack else None})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@price_accuracy_bp.route("/webhook/findings/verified-by-id", methods=["POST"])
def webhook_ack_by_finding_id():
    """Close a finding from spreadsheet verified_ok (SPIKE ack, or MISSING/ZERO suppress+delete)."""
    chk = _bad_token_resp()
    if chk:
        return chk
    payload = request.get_json(silent=True) or {}
    fid = payload.get("finding_id")
    if fid is None:
        return jsonify({"ok": False, "error": "finding_id required"}), 400
    result = close_finding_verified_from_sheet(
        int(fid),
        source=str(payload.get("source") or "google_sheets_webhook")[:30],
        notes=str(payload.get("notes") or "")[:500] or None,
    )
    if not result.get("ok"):
        err = result.get("error") or "not_found"
        status = 404 if err in ("finding_not_found", "SPIKE finding missing dates") else 400
        return jsonify({"ok": False, "error": err}), status
    return jsonify({"ok": True, "kind": result.get("kind"), "ack_id": result.get("ack_id")})


@price_accuracy_bp.route("/webhook/apply-prices", methods=["POST"])
def webhook_apply_prices():
    """
    Apply closes from the sheet correction queue.

    Sheet / GOOGLEFINANCE sources refuse CA-adjusted prices (as-traded NSE required).
    Response includes rejected_adjusted, rejected_samples, and user_message when blocked.
    """
    chk = _bad_token_resp()
    if chk:
        return chk
    payload = request.get_json(silent=True) or {}
    fixes = payload.get("fixes") or payload.get("rows")
    if not isinstance(fixes, list):
        return jsonify({"ok": False, "error": "fixes must be an array"}), 400

    norm = []
    for row in fixes:
        if isinstance(row, dict):
            norm.append(row)
            continue
        return jsonify({"ok": False, "error": "each fix must be an object"}), 400

    src = payload.get("source") or "google_sheets_webhook"
    result = apply_historical_price_fixes(norm, source=str(src)[:50])
    # Partial success is still ok=True; callers must read rejected_adjusted / user_message.
    body = {"ok": True, **result}
    if int(result.get("rejected_adjusted") or 0) > 0 and int(result.get("updated") or 0) + int(
        result.get("created") or 0
    ) == 0:
        body["ok"] = False
        body["error"] = "all_rows_rejected_ca_adjusted"
    return jsonify(body)
