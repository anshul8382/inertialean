"""Admin Advisor Review + manager assignment quick reference."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from access_control import require_admin, require_advisor_or_manager

advisor_review_bp = Blueprint(
    "advisor_review",
    __name__,
    url_prefix="/advisor-review",
)
advisor_review_bp.strict_slashes = False


@advisor_review_bp.route("/")
@login_required
@require_admin
def dashboard():
    """Admin only: per-advisor AUM, reviews, committed investment, MoM growth."""
    if not getattr(current_user, "is_admin", False):
        flash("Access denied. Admin privileges are required.", "error")
        return redirect(url_for("main.dashboard"))
    from services.advisor_review_service import get_advisor_book_rows, count_unassigned_active_clients

    rows = get_advisor_book_rows()
    return render_template(
        "advisor_review/dashboard.html",
        rows=rows,
        unassigned_count=count_unassigned_active_clients(),
    )


@advisor_review_bp.route("/advisor/<int:advisor_id>")
@login_required
@require_admin
def advisor_detail(advisor_id: int):
    """Admin only: drill-down for one advisor's book."""
    if not getattr(current_user, "is_admin", False):
        flash("Access denied. Admin privileges are required.", "error")
        return redirect(url_for("main.dashboard"))
    from services.advisor_review_service import get_advisor_detail

    detail = get_advisor_detail(advisor_id)
    if not detail:
        flash("Advisor not found.", "error")
        return redirect(url_for("advisor_review.dashboard"))
    return render_template("advisor_review/detail.html", detail=detail)


@advisor_review_bp.route("/assignments")
@login_required
@require_advisor_or_manager
def assignments_overview():
    """
    Assignments roster (no performance metrics).

    - Admin / manager: all advisor ↔ client assignments; manage links if manager/admin
    - Advisor: own clients only (view); no performance / other advisors' books
    """
    from services.advisor_review_service import get_advisor_assignment_overview

    is_admin = bool(getattr(current_user, "is_admin", False))
    # User.is_manager is True for admins as well
    is_manager = bool(getattr(current_user, "is_manager", False))
    can_manage = is_admin or is_manager
    sees_all_assignments = is_admin or is_manager

    scope_id = None if sees_all_assignments else current_user.id
    overview = get_advisor_assignment_overview(advisor_id=scope_id)
    return render_template(
        "advisor_review/assignments_overview.html",
        overview=overview,
        is_admin=is_admin,
        can_manage_assignments=can_manage,
        sees_all_assignments=sees_all_assignments,
    )
