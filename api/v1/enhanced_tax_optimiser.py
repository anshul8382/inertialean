from __future__ import annotations

import uuid
from datetime import date

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.exc import OperationalError, ProgrammingError

from access_control import require_advisor_or_manager
from extensions import db
from models import AssetClass
from services.enhanced_tax_optimiser_service import (
    build_enhanced_tax_report,
    list_asset_class_tax_rulesets,
)
from services.tax_optimiser_settings_service import (
    list_asset_class_catalog,
    settings_to_api_dict,
    update_settings_from_payload,
)
from services.asset_class_service import apply_tax_fields_from_payload
from services.tax_optimiser_tax_rules_service import (
    create_tax_rule,
    delete_tax_rule,
    list_tax_rules,
    set_active_tax_rule,
    update_tax_rule,
)
from services.tax_optimiser_interactive_service import (
    apply_session_action,
    render_session_email_html,
    reset_review_session,
    save_review_session,
    schedule_tax_optimiser_followup_tasks,
    send_session_email,
    start_review_session,
)
from services.tax_optimiser_live_session_store import (
    load_interactive_session,
    save_interactive_session,
)
from services.fy_tax_utils import fy_bounds_for_start_year, fy_dropdown_options, parse_fy_start_year
from services.capital_gains_service import get_capital_gains_fifo_full_replay


enhanced_tax_optimiser_bp = Blueprint("enhanced_tax_optimiser", __name__)


def _store_error_response(exc: Exception):
    current_app.logger.warning("tax optimiser live session DB error: %s", exc)
    return jsonify(
        {
            "ok": False,
            "error": (
                "Interactive session storage failed (database). "
                "Run migration: python migrations/add_tax_optimiser_live_session_table.py"
            ),
        }
    ), 503


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/report", methods=["GET"])
@login_required
@require_advisor_or_manager
def enhanced_tax_report():
    fy_sy = request.args.get("fy_start_year", type=int)
    if fy_sy is None and request.args.get("fy"):
        fy_sy = parse_fy_start_year(request.args.get("fy"))
    rec_only_raw = (request.args.get("recommended_trades_only") or "").strip().lower()
    recommended_trades_only = bool(rec_only_raw) and rec_only_raw not in ("0", "false", "no")
    data = build_enhanced_tax_report(
        as_of_date=request.args.get("as_of_date"),
        fy_start_year=fy_sy,
        client_id_filter=request.args.get("client_id", type=int),
        client_name_filter=request.args.get("client_name"),
        advisor_user_id_for_review_status=current_user.id,
        recommended_trades_only=recommended_trades_only,
    )
    return jsonify({"ok": True, "data": data})


@enhanced_tax_optimiser_bp.route(
    "/enhanced-tax-optimiser/client/<int:client_id>/mark-reviewed", methods=["POST"]
)
@login_required
@require_advisor_or_manager
def enhanced_tax_mark_client_reviewed(client_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        fy_start_year = int(payload.get("fy_start_year"))
    except (TypeError, ValueError, AttributeError):
        fy_start_year = None
    if fy_start_year is None:
        return jsonify({"ok": False, "error": "fy_start_year is required"}), 400

    from services.tax_optimiser_review_status_service import mark_client_reviewed_for_fy

    out = mark_client_reviewed_for_fy(
        advisor_user_id=int(current_user.id),
        client_id=int(client_id),
        fy_start_year=int(fy_start_year),
    )
    if not out.get("ok"):
        return jsonify({"ok": False, "error": out.get("error", "mark reviewed failed")}), 400
    return jsonify({"ok": True, "interactive_review": out.get("interactive_review")})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/fy-options", methods=["GET"])
@login_required
@require_advisor_or_manager
def enhanced_tax_fy_options():
    back = request.args.get("back_years", type=int) or 8
    back = min(max(back, 1), 15)
    return jsonify({"ok": True, "options": fy_dropdown_options(back_years=back)})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/asset-classes", methods=["GET"])
@login_required
@require_advisor_or_manager
def asset_classes():
    inc = str(request.args.get("all") or "").lower() in ("1", "true", "yes")
    rows = list_asset_class_tax_rulesets(include_inactive=inc)
    return jsonify({"ok": True, "rows": rows})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/settings", methods=["GET"])
@login_required
@require_advisor_or_manager
def tax_optimiser_settings_get():
    return jsonify({"ok": True, "settings": settings_to_api_dict()})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/settings", methods=["POST", "PUT"])
@login_required
@require_advisor_or_manager
def tax_optimiser_settings_save():
    payload = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "settings": update_settings_from_payload(payload)})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/tax-rules", methods=["GET"])
@login_required
@require_advisor_or_manager
def tax_optimiser_tax_rules_list():
    try:
        rows = list_tax_rules()
    except ProgrammingError as e:
        current_app.logger.exception("tax-rules list: schema / SQL error")
        msg = str(getattr(e, "orig", e) or e)
        return jsonify(
            {
                "ok": False,
                "error": msg,
                "rows": [],
                "hint": (
                    "If the error mentions a missing table, run once on this database: "
                    "python migrations/add_tax_optimiser_tax_rule_table.py"
                ),
            }
        ), 503
    except OperationalError as e:
        current_app.logger.exception("tax-rules list: database unavailable")
        return jsonify(
            {
                "ok": False,
                "error": str(getattr(e, "orig", e) or e),
                "rows": [],
            }
        ), 503
    except Exception as e:
        current_app.logger.exception("tax-rules list: unexpected error")
        return jsonify({"ok": False, "error": str(e), "rows": []}), 500
    return jsonify({"ok": True, "rows": rows})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/tax-rules", methods=["POST"])
@login_required
@require_advisor_or_manager
def tax_optimiser_tax_rules_create():
    payload = request.get_json(silent=True) or {}
    try:
        row = create_tax_rule(payload)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "row": row})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/tax-rules/<int:rule_id>", methods=["PATCH", "PUT"])
@login_required
@require_advisor_or_manager
def tax_optimiser_tax_rules_patch(rule_id):
    payload = request.get_json(silent=True) or {}
    try:
        row = update_tax_rule(rule_id, payload)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "row": row})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/tax-rules/<int:rule_id>", methods=["DELETE"])
@login_required
@require_advisor_or_manager
def tax_optimiser_tax_rules_delete(rule_id):
    try:
        delete_tax_rule(rule_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/tax-rules/<int:rule_id>/activate", methods=["POST"])
@login_required
@require_advisor_or_manager
def tax_optimiser_tax_rules_activate(rule_id):
    try:
        row = set_active_tax_rule(rule_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "row": row})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/asset-class-catalog", methods=["GET"])
@login_required
@require_advisor_or_manager
def asset_class_catalog():
    include_all = str(request.args.get("all") or "").lower() in ("1", "true", "yes")
    return jsonify(
        {"ok": True, "asset_classes": list_asset_class_catalog(include_all=include_all)}
    )


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/asset-classes/map", methods=["POST"])
@login_required
@require_advisor_or_manager
def asset_class_map():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("asset_class_name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "asset_class_name is required"}), 400

    try:
        asset_class_id = int(payload.get("asset_class_id")) if payload.get("asset_class_id") is not None else None
    except (TypeError, ValueError):
        asset_class_id = None
    if asset_class_id is None:
        return jsonify({"ok": False, "error": "asset_class_id is required"}), 400
    ac = db.session.get(AssetClass, asset_class_id)
    if not ac:
        return jsonify({"ok": False, "error": "asset_class_id not found"}), 404

    if name:
        ac.name = name[:100]

    body = {
        "ruleset": (payload.get("ruleset") or name or ac.tax_ruleset or "equity").strip().lower()[:32],
        "stcg_rate": payload.get("stcg_rate", ac.tax_stcg_rate),
        "ltcg_rate": payload.get("ltcg_rate", ac.tax_ltcg_rate),
        "ltcg_exemption_limit": payload.get("ltcg_exemption_limit", ac.tax_ltcg_exemption_limit),
        "is_active": payload.get("is_active", ac.tax_rules_active),
        "notes": payload.get("notes", ac.tax_notes),
    }
    try:
        body["ltcg_minimum_months"] = int(
            max(1, min(int(payload.get("ltcg_minimum_months", ac.tax_ltcg_minimum_months)), 600))
        )
    except (TypeError, ValueError):
        body["ltcg_minimum_months"] = ac.tax_ltcg_minimum_months or 12
    mode_raw = payload.get("ltcg_eligibility_mode")
    if mode_raw in (None, "", "inherit"):
        body["ltcg_eligibility_mode"] = None
    else:
        m = str(mode_raw).strip().lower()
        body["ltcg_eligibility_mode"] = m if m in ("twelve_months", "holding_days") else None
    if payload.get("ltcg_holding_days") is not None and str(payload.get("ltcg_holding_days")).strip() != "":
        try:
            body["ltcg_holding_days"] = int(max(1, min(int(payload["ltcg_holding_days"]), 3660)))
        except (TypeError, ValueError):
            body["ltcg_holding_days"] = ac.tax_ltcg_holding_days
    else:
        body["ltcg_holding_days"] = None

    apply_tax_fields_from_payload(ac, body)
    db.session.commit()
    return jsonify({"ok": True, "row": ac.tax_rules_api_dict()})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/fifo-replay", methods=["GET"])
@login_required
@require_advisor_or_manager
def enhanced_tax_fifo_replay():
    """
    Full FIFO replay for one client + security through FY end (same engine as capital gains report).
    UI uses this for sell-matching detail; BUY steps omitted in presentation layer.
    """
    client_id = request.args.get("client_id", type=int)
    security_id = request.args.get("security_id", type=int)
    fy_sy = request.args.get("fy_start_year", type=int)
    if client_id is None or security_id is None or fy_sy is None:
        return jsonify(
            {"ok": False, "error": "client_id, security_id, and fy_start_year are required"}
        ), 400
    _, fy_end = fy_bounds_for_start_year(fy_sy)
    replay = get_capital_gains_fifo_full_replay(client_id, security_id, fy_end)
    return jsonify({"ok": True, "replay": replay})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/review/session/start", methods=["POST"])
@login_required
@require_advisor_or_manager
def review_session_start():
    payload = request.get_json(silent=True) or {}
    session_data = start_review_session(current_user.id, payload)
    session_id = f"u{current_user.id}-{uuid.uuid4().hex[:16]}"
    try:
        save_interactive_session(session_id, current_user.id, session_data)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    return jsonify({"ok": True, "session_id": session_id, "session": session_data})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/review/session/<session_id>", methods=["GET"])
@login_required
@require_advisor_or_manager
def review_session_get(session_id):
    try:
        data = load_interactive_session(session_id, current_user.id)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    if not data:
        return jsonify({"ok": False, "error": "session not found"}), 404
    return jsonify({"ok": True, "session": data})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/review/session/<session_id>/action", methods=["POST"])
@login_required
@require_advisor_or_manager
def review_session_action(session_id):
    try:
        data = load_interactive_session(session_id, current_user.id)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    if not data:
        return jsonify({"ok": False, "error": "session not found"}), 404

    payload = request.get_json(silent=True) or {}
    suggestion_id = payload.get("suggestion_id")
    action = payload.get("action")
    note = payload.get("note", "")
    if not suggestion_id or not action:
        return jsonify({"ok": False, "error": "suggestion_id and action are required"}), 400

    try:
        updated = apply_session_action(
            data,
            suggestion_id=suggestion_id,
            action=action,
            note=note,
            reversal_action=payload.get("reversal_action"),
        )
        save_interactive_session(session_id, current_user.id, updated)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    return jsonify({"ok": True, "session": updated})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/review/session/<session_id>/reset", methods=["POST"])
@login_required
@require_advisor_or_manager
def review_session_reset(session_id):
    try:
        data = load_interactive_session(session_id, current_user.id)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    if not data:
        return jsonify({"ok": False, "error": "session not found"}), 404
    payload = request.get_json(silent=True) or {}
    try:
        updated = reset_review_session(data, payload_overrides=payload)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    try:
        save_interactive_session(session_id, current_user.id, updated)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    return jsonify({"ok": True, "session": updated})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/review/session/<session_id>/save", methods=["POST"])
@login_required
@require_advisor_or_manager
def review_session_save(session_id):
    try:
        data = load_interactive_session(session_id, current_user.id)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    if not data:
        return jsonify({"ok": False, "error": "session not found"}), 404
    out = save_review_session(data)
    if not out.get("ok"):
        return jsonify({"ok": False, "error": out.get("error", "save failed")}), 400
    payload = {k: v for k, v in out.items() if k != "ok"}
    return jsonify({"ok": True, "saved": payload})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/review/session/<session_id>/email", methods=["POST"])
@login_required
@require_advisor_or_manager
def review_session_email(session_id):
    try:
        data = load_interactive_session(session_id, current_user.id)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    if not data:
        return jsonify({"ok": False, "error": "session not found"}), 404
    payload = request.get_json(silent=True) or {}
    recipient = (payload.get("recipient_email") or "").strip()
    if not recipient:
        return jsonify({"ok": False, "error": "recipient_email is required"}), 400
    out = send_session_email(data, recipient_email=recipient)
    try:
        from services.tax_optimiser_review_status_service import (
            record_interactive_review_email_sent,
        )

        record_interactive_review_email_sent(data, advisor_user_id=current_user.id)
    except Exception as ex:
        current_app.logger.warning("record_interactive_review_email_sent: %s", ex)
    return jsonify({"ok": True, "email": out})


@enhanced_tax_optimiser_bp.route(
    "/enhanced-tax-optimiser/review/session/<session_id>/email-preview", methods=["POST"]
)
@login_required
@require_advisor_or_manager
def review_session_email_preview(session_id):
    try:
        data = load_interactive_session(session_id, current_user.id)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    if not data:
        return jsonify({"ok": False, "error": "session not found"}), 404
    html = render_session_email_html(data)
    return jsonify({"ok": True, "html": html})


@enhanced_tax_optimiser_bp.route(
    "/enhanced-tax-optimiser/review/session/<session_id>/followup-tasks", methods=["POST"]
)
@login_required
@require_advisor_or_manager
def review_session_followup_tasks(session_id):
    try:
        data = load_interactive_session(session_id, current_user.id)
    except (OperationalError, ProgrammingError) as e:
        return _store_error_response(e)
    if not data:
        return jsonify({"ok": False, "error": "session not found"}), 404
    payload = request.get_json(silent=True) or {}
    raw = (payload.get("follow_up_date") or "").strip()[:10]
    try:
        fud = date.fromisoformat(raw)
    except ValueError:
        return jsonify({"ok": False, "error": "follow_up_date must be YYYY-MM-DD"}), 400
    saved_raw = payload.get("saved_review_session_id")
    saved_id = None
    if saved_raw is not None and str(saved_raw).strip() != "":
        try:
            saved_id = int(saved_raw)
        except (TypeError, ValueError):
            saved_id = None
    assign_raw = payload.get("assigned_to_user_id")
    assign_id = None
    if assign_raw is not None and str(assign_raw).strip() != "":
        try:
            assign_id = int(assign_raw)
        except (TypeError, ValueError):
            assign_id = None
    try:
        out = schedule_tax_optimiser_followup_tasks(
            data,
            follow_up_date=fud,
            created_by_user_id=int(current_user.id),
            assigned_to_user_id=assign_id,
            saved_session_id=saved_id,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    if not out.get("ok"):
        return jsonify({"ok": False, "error": out.get("error", "follow-up failed")}), 400
    return jsonify({"ok": True, "followup": out})


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/strategies", methods=["GET"])
@login_required
@require_advisor_or_manager
def tax_optimiser_strategies_list():
    try:
        from models import TaxOptimiserStrategy

        rows = (
            TaxOptimiserStrategy.query.order_by(
                TaxOptimiserStrategy.sort_order.asc(),
                TaxOptimiserStrategy.id.asc(),
            ).all()
        )
        data = [
            {
                "id": r.id,
                "code": r.code,
                "enabled": bool(r.enabled),
                "sort_order": int(r.sort_order or 0),
                "params": r.params or {},
            }
            for r in rows
        ]
        return jsonify({"ok": True, "rows": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@enhanced_tax_optimiser_bp.route("/enhanced-tax-optimiser/strategies/<int:row_id>", methods=["PATCH", "PUT"])
@login_required
@require_advisor_or_manager
def tax_optimiser_strategies_patch(row_id):
    try:
        from models import TaxOptimiserStrategy

        row = TaxOptimiserStrategy.query.get(row_id)
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        payload = request.get_json(silent=True) or {}
        if "enabled" in payload:
            row.enabled = bool(payload["enabled"])
        if "sort_order" in payload:
            row.sort_order = int(payload["sort_order"])
        if "params" in payload and isinstance(payload["params"], dict):
            base = dict(row.params or {})
            base.update(payload["params"])
            row.params = base
        db.session.commit()
        return jsonify(
            {
                "ok": True,
                "row": {
                    "id": row.id,
                    "code": row.code,
                    "enabled": bool(row.enabled),
                    "sort_order": int(row.sort_order or 0),
                    "params": row.params or {},
                },
            }
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 400
