"""Weekly Portfolio Discussion — Anshul-only concern queue + workspace."""
from __future__ import annotations

import logging
from datetime import date
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import Client, User

logger = logging.getLogger(__name__)

portfolio_discussion_bp = Blueprint(
    "portfolio_discussion",
    __name__,
    url_prefix="/portfolio-discussion",
)
portfolio_discussion_bp.strict_slashes = False


def handle_errors(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.error("Error in %s: %s", f.__name__, e, exc_info=True)
            flash(f"An error occurred: {e}", "error")
            return redirect(url_for("portfolio_discussion.queue"))

    return decorated_function


def _require_access():
    from services.portfolio_discussion_service import user_may_view_portfolio_discussion

    if not user_may_view_portfolio_discussion(current_user):
        abort(403)


def _assignees():
    return User.query.filter_by(is_active=True).order_by(User.username).all()


@portfolio_discussion_bp.route("/")
@login_required
@handle_errors
def queue():
    _require_access()
    from services.portfolio_discussion_service import build_concern_queue

    data = build_concern_queue(limit=50, as_of=date.today())
    return render_template(
        "portfolio_discussion/queue.html",
        queue=data,
        today=date.today(),
    )


@portfolio_discussion_bp.route("/<int:client_id>", methods=["GET"])
@login_required
@handle_errors
def workspace(client_id):
    _require_access()
    from services.portfolio_discussion_service import get_discussion_context

    ctx = get_discussion_context(client_id)
    if not ctx:
        abort(404)
    return render_template(
        "portfolio_discussion/workspace.html",
        ctx=ctx,
        client=ctx["client"],
        assignees=_assignees(),
        today=date.today(),
    )


@portfolio_discussion_bp.route("/<int:client_id>/save", methods=["POST"])
@login_required
@handle_errors
def save(client_id):
    _require_access()
    Client.query.get_or_404(client_id)
    from services.portfolio_discussion_service import (
        parse_tasks_from_form,
        portfolio_discussion_table_ready,
        save_discussion,
    )

    mark_complete = request.form.get("mark_complete") in ("1", "true", "on", "yes")
    if mark_complete and not portfolio_discussion_table_ready():
        flash(
            "Cannot mark reviewed: portfolio_discussion_state table missing. "
            "Run migrations/add_portfolio_discussion_state.py",
            "error",
        )
        return redirect(url_for("portfolio_discussion.workspace", client_id=client_id))

    note_text = request.form.get("note_text") or ""
    tasks = parse_tasks_from_form(request.form)
    try:
        result = save_discussion(
            client_id,
            current_user,
            note_text=note_text,
            tasks=tasks,
            mark_complete=mark_complete,
            as_of=date.today(),
            commit=True,
        )
    except Exception as e:
        flash(f"Save failed: {e}", "error")
        return redirect(url_for("portfolio_discussion.workspace", client_id=client_id))

    parts = []
    if result.get("note_saved"):
        parts.append("note saved")
    if result.get("tasks_created"):
        parts.append(f"{result['tasks_created']} task(s) created")
    if result.get("marked_reviewed"):
        parts.append(f"marked reviewed (hidden until {result.get('hide_until')})")
    flash((" · ".join(parts) if parts else "Nothing to save.") + ".", "success" if parts else "info")

    if result.get("marked_reviewed"):
        return redirect(url_for("portfolio_discussion.queue"))
    return redirect(url_for("portfolio_discussion.workspace", client_id=client_id))


@portfolio_discussion_bp.route("/<int:client_id>/mark-reviewed", methods=["POST"])
@login_required
@handle_errors
def mark_reviewed_route(client_id):
    _require_access()
    Client.query.get_or_404(client_id)
    from services.portfolio_discussion_service import (
        mark_reviewed,
        portfolio_discussion_table_ready,
    )

    if not portfolio_discussion_table_ready():
        flash(
            "Cannot mark reviewed: portfolio_discussion_state table missing. "
            "Run migrations/add_portfolio_discussion_state.py",
            "error",
        )
        return redirect(url_for("portfolio_discussion.workspace", client_id=client_id))

    note_preview = (request.form.get("note_text") or "").strip() or None
    try:
        state = mark_reviewed(
            client_id,
            current_user.id,
            as_of=date.today(),
            note_preview=note_preview,
            commit=True,
        )
        flash(f"Marked reviewed — hidden until {state.hide_until}.", "success")
    except Exception as e:
        flash(f"Mark reviewed failed: {e}", "error")
        return redirect(url_for("portfolio_discussion.workspace", client_id=client_id))
    return redirect(url_for("portfolio_discussion.queue"))
