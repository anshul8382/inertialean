"""Distributor portal — view-only client summaries and payout sheet."""
from __future__ import annotations

from datetime import date

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from access_control import require_manager

distributor_bp = Blueprint("distributor", __name__, url_prefix="/distributor")


def _require_distributor():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.is_admin or current_user.is_manager:
        return
    if not getattr(current_user, "is_distributor", False):
        abort(403)


def _preview_context():
    from services.distributor_service import (
        can_use_distributor_preview,
        get_preview_distributor,
    )

    preview = None
    if can_use_distributor_preview(current_user):
        preview = get_preview_distributor()
    return {
        "preview_distributor": preview,
        "is_preview": bool(preview),
    }


@distributor_bp.route("/preview", methods=["GET", "POST"])
@login_required
@require_manager
def preview():
    """Admin/manager: pick a distributor and view their portal as preview."""
    from services.distributor_service import (
        clear_preview_distributor,
        get_preview_distributor,
        list_distributors,
        set_preview_distributor,
    )

    if request.method == "POST":
        action = (request.form.get("action") or "start").strip().lower()
        if action == "exit":
            clear_preview_distributor()
            flash("Exited distributor preview.", "info")
            return redirect(url_for("distributor.preview"))
        dist_id = request.form.get("distributor_id", type=int)
        if not dist_id:
            flash("Select a distributor.", "error")
            return redirect(url_for("distributor.preview"))
        ok, msg = set_preview_distributor(dist_id)
        flash(msg, "success" if ok else "error")
        if ok:
            return redirect(url_for("distributor.dashboard"))
        return redirect(url_for("distributor.preview"))

    distributors = list_distributors()
    active = get_preview_distributor()
    return render_template(
        "distributor/preview.html",
        distributors=distributors,
        active_preview=active,
        **_preview_context(),
    )


@distributor_bp.route("/preview/exit", methods=["POST", "GET"])
@login_required
@require_manager
def preview_exit():
    from services.distributor_service import clear_preview_distributor

    clear_preview_distributor()
    flash("Exited distributor preview.", "info")
    next_url = request.args.get("next") or request.form.get("next")
    if next_url and str(next_url).startswith("/"):
        return redirect(next_url)
    return redirect(url_for("distributor.preview"))


@distributor_bp.route("/pitch-deck")
@login_required
def pitch_deck():
    """
    Download the latest distributor pitch deck (PPT/PPTX).
    Place the file at static/distributor/Inertia_Distributor_Pitch_Deck.pptx
    or set DISTRIBUTOR_PITCH_DECK_PATH. Until configured, shows a friendly message.
    """
    _require_distributor()
    from services.distributor_service import get_pitch_deck_path

    path = get_pitch_deck_path()
    if not path:
        flash(
            "Pitch deck is not available yet. Your admin will upload the latest deck soon.",
            "info",
        )
        return redirect(request.referrer or url_for("distributor.dashboard"))

    suffix = path.suffix.lower()
    if suffix == ".ppt":
        mimetype = "application/vnd.ms-powerpoint"
    else:
        mimetype = (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
    download_name = f"Inertia_Distributor_Pitch_Deck{suffix or '.pptx'}"
    return send_file(
        path,
        mimetype=mimetype,
        as_attachment=True,
        download_name=download_name,
        max_age=300,
    )


@distributor_bp.route("/")
@distributor_bp.route("/dashboard")
@login_required
def dashboard():
    _require_distributor()
    from models import Client
    from services.distributor_service import (
        build_client_portal_row,
        distributor_schema_ready,
        effective_distributor_user,
        set_preview_distributor,
    )

    # Allow deep-link ?distributor_id= to start preview for managers/admins
    q_id = request.args.get("distributor_id", type=int)
    if q_id and (current_user.is_admin or current_user.is_manager):
        set_preview_distributor(q_id)

    if not distributor_schema_ready():
        flash("Distributor module is not enabled on this database yet.", "warning")
        return render_template(
            "distributor/dashboard.html",
            rows=[],
            schema_ready=False,
            **_preview_context(),
        )

    subject = effective_distributor_user(current_user)
    if current_user.is_distributor and not (
        current_user.is_admin or current_user.is_manager
    ):
        clients = current_user.get_accessible_clients()
    elif subject and getattr(subject, "is_distributor", False):
        clients = (
            Client.query.filter_by(distributor_id=subject.id)
            .order_by(Client.name)
            .all()
        )
    else:
        clients = []
        if current_user.is_admin or current_user.is_manager:
            flash(
                "Select a distributor to preview their portal.",
                "info",
            )
            return redirect(url_for("distributor.preview"))

    rows = [build_client_portal_row(c) for c in clients]
    return render_template(
        "distributor/dashboard.html",
        rows=rows,
        schema_ready=True,
        **_preview_context(),
    )


@distributor_bp.route("/clients/<int:client_id>")
@login_required
def client_summary(client_id: int):
    _require_distributor()
    from models import Client
    from services.distributor_service import (
        build_distributor_client_detail,
        distributor_schema_ready,
        get_preview_distributor,
    )

    if not distributor_schema_ready():
        flash("Distributor module is not enabled on this database yet.", "warning")
        return redirect(url_for("distributor.dashboard"))

    client = Client.query.get_or_404(client_id)
    preview = get_preview_distributor() if (
        current_user.is_admin or current_user.is_manager
    ) else None

    if current_user.is_distributor and not (
        current_user.is_admin or current_user.is_manager
    ):
        if not current_user.can_access_client(client):
            abort(403)
        if client.distributor_id != current_user.id:
            abort(403)
    elif preview:
        if client.distributor_id != preview.id:
            abort(403)
    elif not current_user.can_access_client(client):
        abort(403)

    detail = build_distributor_client_detail(client, light=False)
    return render_template(
        "distributor/client_summary.html",
        d=detail,
        client=client,
        **_preview_context(),
    )


@distributor_bp.route("/payouts")
@login_required
def payouts():
    _require_distributor()
    from services.distributor_service import (
        distributor_schema_ready,
        effective_distributor_user,
        month_payouts_for_distributor,
        set_preview_distributor,
    )

    q_id = request.args.get("distributor_id", type=int)
    if q_id and (current_user.is_admin or current_user.is_manager):
        set_preview_distributor(q_id)

    if not distributor_schema_ready():
        flash("Distributor module is not enabled on this database yet.", "warning")
        return redirect(url_for("distributor.dashboard"))

    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month

    subject = effective_distributor_user(current_user)
    if not subject or not getattr(subject, "is_distributor", False):
        if current_user.is_admin or current_user.is_manager:
            flash("Select a distributor to preview their payouts.", "info")
            return redirect(url_for("distributor.preview"))
        abort(403)

    data = month_payouts_for_distributor(subject.id, year, month)
    is_admin_view = bool(current_user.is_admin or current_user.is_manager)
    return render_template(
        "distributor/payouts.html",
        data=data,
        year=year,
        month=month,
        is_admin_view=is_admin_view,
        **_preview_context(),
    )


@distributor_bp.route("/admin/payouts", methods=["GET", "POST"])
@login_required
@require_manager
def admin_payouts():
    """Admin/manager: month batch release of pending distributor payouts."""
    from services.distributor_service import (
        admin_month_payout_overview,
        distributor_schema_ready,
        payout_release_schema_ready,
        release_month_payouts,
    )

    if not distributor_schema_ready():
        flash("Distributor module is not enabled on this database yet.", "warning")
        return redirect(url_for("distributor.preview"))

    today = date.today()
    year = request.args.get("year", type=int) or request.form.get("year", type=int) or today.year
    month = (
        request.args.get("month", type=int)
        or request.form.get("month", type=int)
        or today.month
    )

    if request.method == "POST":
        if not payout_release_schema_ready():
            flash(
                "Payout release schema not ready. Run migrations/add_distributor_payout_release.py.",
                "error",
            )
            return redirect(url_for("distributor.admin_payouts", year=year, month=month))

        dist_id = request.form.get("distributor_id", type=int)
        notes = (request.form.get("notes") or "").strip() or None
        if not dist_id:
            flash("Select a distributor.", "error")
            return redirect(url_for("distributor.admin_payouts", year=year, month=month))

        ok, msg, _batch = release_month_payouts(
            dist_id,
            year,
            month,
            actor_id=current_user.id,
            notes=notes,
        )
        flash(msg, "success" if ok else "error")
        return redirect(url_for("distributor.admin_payouts", year=year, month=month))

    overview = admin_month_payout_overview(year, month)
    return render_template(
        "distributor/admin_payouts.html",
        overview=overview,
        year=year,
        month=month,
        **_preview_context(),
    )


@distributor_bp.route("/assign/<int:client_id>", methods=["POST"])
@login_required
@require_manager
def assign(client_id: int):
    """Admin/manager: set or clear client distributor."""
    from services.distributor_service import assign_client_distributor

    raw = request.form.get("distributor_id")
    distributor_id = int(raw) if raw not in (None, "", "0") else None
    ok, msg = assign_client_distributor(
        client_id, distributor_id, actor_id=current_user.id
    )
    flash(msg, "success" if ok else "error")
    next_url = request.form.get("next") or request.referrer
    if next_url:
        return redirect(next_url)
    return redirect(url_for("clients.client_details", client_id=client_id))
