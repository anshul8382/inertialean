"""Native AI assistants: in-app help chat and SQL query suggestions."""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

assistant_bp = Blueprint("assistant", __name__)


def _assistant_ui_enabled() -> bool:
    from services.ollama_service import ai_services_enabled, ollama_enabled

    return ai_services_enabled() and ollama_enabled()


def _redirect_if_lean_disabled():
    if not _assistant_ui_enabled():
        return redirect(url_for("main.user_manual_pdf"))
    return None


def _ollama_status():
    from services.ollama_service import ai_services_enabled, ollama_service

    ping = ollama_service.ping()
    return ai_services_enabled(), ping.get("ok"), ping.get("models") or []


@assistant_bp.route("/")
@login_required
def assistant_hub():
    bounced = _redirect_if_lean_disabled()
    if bounced:
        return bounced
    ai_enabled, ollama_ok, ollama_models = _ollama_status()
    return render_template(
        "tools/assistant_hub.html",
        ai_enabled=ai_enabled,
        ollama_ok=ollama_ok,
        ollama_models=ollama_models,
    )


@assistant_bp.route("/sql")
@login_required
def sql_assistant_page():
    if not current_user.is_admin:
        abort(403)
    return redirect(url_for("main.sql_query"))


@assistant_bp.route("/help")
@login_required
def help_assistant_page():
    bounced = _redirect_if_lean_disabled()
    if bounced:
        return bounced
    from services.help_knowledge_update_service import user_may_approve

    ai_enabled, ollama_ok, ollama_models = _ollama_status()
    return render_template(
        "tools/help_assistant.html",
        ai_enabled=ai_enabled,
        ollama_ok=ollama_ok,
        ollama_models=ollama_models,
        can_approve_knowledge=user_may_approve(current_user),
    )


@assistant_bp.route("/help/chat", methods=["POST"])
@login_required
def help_assistant_chat():
    if not _assistant_ui_enabled():
        abort(404)
    from services.help_assistant_service import ask_help_assistant

    data = request.get_json(silent=True) or {}
    question = data.get("question") or request.form.get("question") or ""
    history = data.get("history") if isinstance(data.get("history"), list) else None
    result = ask_help_assistant(question, history=history)
    status = 503 if result.get("unavailable") else (400 if not result.get("success") else 200)
    return jsonify(result), status


@assistant_bp.route("/help/knowledge/propose", methods=["POST"])
@login_required
def help_knowledge_propose():
    if not _assistant_ui_enabled():
        abort(404)
    from services.help_knowledge_update_service import propose_knowledge_entry

    data = request.get_json(silent=True) or {}
    result = propose_knowledge_entry(
        question=data.get("question") or "",
        proposed_text=data.get("proposed_text") or "",
        user_id=current_user.id,
        user_name=getattr(current_user, "username", None) or str(current_user.id),
    )
    status = 400 if not result.get("success") else 200
    return jsonify(result), status


@assistant_bp.route("/help/knowledge/approve", methods=["POST"])
@login_required
def help_knowledge_approve():
    if not _assistant_ui_enabled():
        abort(404)
    from services.help_knowledge_update_service import approve_knowledge_entry, user_may_approve

    if not user_may_approve(current_user):
        return jsonify({"success": False, "error": "Manager or admin access required to approve."}), 403

    data = request.get_json(silent=True) or {}
    proposal_id = (data.get("proposal_id") or "").strip()
    if not proposal_id:
        return jsonify({"success": False, "error": "proposal_id required."}), 400

    result = approve_knowledge_entry(
        proposal_id,
        approver_id=current_user.id,
        approver_name=getattr(current_user, "username", None) or str(current_user.id),
    )
    status = 400 if not result.get("success") else 200
    return jsonify(result), status


@assistant_bp.route("/help/knowledge/pending", methods=["GET"])
@login_required
def help_knowledge_pending():
    if not _assistant_ui_enabled():
        abort(404)
    from services.help_knowledge_update_service import list_pending_proposals, user_may_approve

    if user_may_approve(current_user):
        items = list_pending_proposals()
    else:
        items = list_pending_proposals(for_user_id=current_user.id)
    return jsonify({"success": True, "proposals": items})


@assistant_bp.route("/tools/sql-query/ai-suggest", methods=["POST"])
@login_required
def sql_ai_suggest():
    if not current_user.is_admin:
        return jsonify({"success": False, "error": "Admin access required."}), 403
    if not _assistant_ui_enabled():
        abort(404)

    from services.sql_assistant_service import suggest_readonly_sql

    data = request.get_json(silent=True) or {}
    nl = data.get("request") or request.form.get("request") or ""
    result = suggest_readonly_sql(nl)
    status = 503 if result.get("unavailable") else (400 if not result.get("success") else 200)

    if result.get("success"):
        try:
            from services.audit_service import log_audit_event

            log_audit_event(
                "sql_ai_suggest",
                user_id=current_user.id,
                resource_type="sql_query",
                details={
                    "request_preview": (nl or "")[:200],
                    "sql_preview": (result.get("sql") or "")[:300],
                    "model": result.get("model"),
                },
            )
        except Exception:
            pass

    return jsonify(result), status
