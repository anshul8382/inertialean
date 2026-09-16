"""Maintenance: BNI referral lookup per client (BNI TY Notes)."""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from access_control import user_is_advisor_or_manager_or_ops
from extensions import db
from models import Client, ClientBniReferral
from services.bni_ty_notes_service import (
    compose_bni_ty_notes,
    deliver_bni_ty_notes_email,
    utc_bounds_for_calendar_days,
    validate_on_demand_range,
)

bni_referrals_bp = Blueprint("bni_referrals", __name__, url_prefix="/maintenance/bni-referrals")


def _require_maint_access():
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if not (user_is_advisor_or_manager_or_ops(current_user) or getattr(current_user, "is_admin", False)):
        flash("Access denied.", "error")
        return redirect(url_for("main.dashboard"))
    return None


def _default_report_dates():
    end = datetime.utcnow().date()
    start = end - timedelta(days=7)
    return start, end


def _parse_report_date(value: str | None, fallback):
    if not value or not str(value).strip():
        return fallback
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


@bni_referrals_bp.route("/", methods=["GET"])
@login_required
def list_bni_referrals():
    redir = _require_maint_access()
    if redir:
        return redir

    q = (request.args.get("q") or "").strip()
    base = db.session.query(Client, ClientBniReferral).outerjoin(
        ClientBniReferral, ClientBniReferral.client_id == Client.id
    )
    if q:
        like = f"%{q.lower()}%"
        base = base.filter(func.lower(Client.name).like(like))
    rows = base.order_by(func.lower(Client.name)).limit(500).all()

    from access_control import get_accessible_clients_ordered
    clients_for_select = get_accessible_clients_ordered()
    ds, de = _default_report_dates()
    ds = _parse_report_date(request.args.get("report_start"), ds)
    de = _parse_report_date(request.args.get("report_end"), de)
    return render_template(
        "maintenance/bni_referrals.html",
        rows=rows,
        clients_for_select=clients_for_select,
        search_q=q,
        report_start=ds.isoformat(),
        report_end=de.isoformat(),
        report_preview_html=None,
    )


@bni_referrals_bp.route("/run-report", methods=["POST"])
@login_required
def run_bni_ty_notes_on_demand():
    redir = _require_maint_access()
    if redir:
        return redir

    action = (request.form.get("action") or "").strip().lower()
    q = (request.form.get("q") or "").strip()
    ds, de = _default_report_dates()
    try:
        start_d = _parse_report_date(request.form.get("start_date"), ds)
        end_d = _parse_report_date(request.form.get("end_date"), de)
    except ValueError:
        flash("Invalid date format. Use YYYY-MM-DD.", "error")
        return redirect(url_for("bni_referrals.list_bni_referrals", q=q or None))

    since, until = utc_bounds_for_calendar_days(start_d, end_d)
    err = validate_on_demand_range(since, until)
    if err:
        flash(err, "error")
        return redirect(
            url_for(
                "bni_referrals.list_bni_referrals",
                q=q or None,
                report_start=start_d.isoformat(),
                report_end=end_d.isoformat(),
            )
        )

    base = db.session.query(Client, ClientBniReferral).outerjoin(
        ClientBniReferral, ClientBniReferral.client_id == Client.id
    )
    if q:
        like = f"%{q.lower()}%"
        base = base.filter(func.lower(Client.name).like(like))
    rows = base.order_by(func.lower(Client.name)).limit(500).all()
    from access_control import get_accessible_clients_ordered
    clients_for_select = get_accessible_clients_ordered()

    if action == "preview":
        payload = compose_bni_ty_notes(since, until)
        return render_template(
            "maintenance/bni_referrals.html",
            rows=rows,
            clients_for_select=clients_for_select,
            search_q=q,
            report_start=start_d.isoformat(),
            report_end=end_d.isoformat(),
            report_preview_html=payload["html"],
            report_preview_bni=payload["bni_count"],
            report_preview_non_bni=payload["non_bni_count"],
        )

    if action == "send_email":
        out = deliver_bni_ty_notes_email(since, until, dry_run=False, update_watermark=False)
        if out.get("error") == "no_recipients":
            flash("No admin/ops_manager users with email configured; email not sent.", "error")
        else:
            flash(
                f"BNI TY Notes email sent for UTC window {out['since']} → {out['until']} "
                f"({out['bni_count']} BNI rows, {out['non_bni_count']} non-BNI). "
                "Scheduled weekly watermark was not changed.",
                "success",
            )
        return redirect(
            url_for(
                "bni_referrals.list_bni_referrals",
                q=q or None,
                report_start=start_d.isoformat(),
                report_end=end_d.isoformat(),
            )
        )

    flash("Unknown action.", "error")
    return redirect(url_for("bni_referrals.list_bni_referrals", q=q or None))


@bni_referrals_bp.route("/save", methods=["POST"])
@login_required
def save_bni_referral():
    redir = _require_maint_access()
    if redir:
        return redir

    client_id = request.form.get("client_id", type=int)
    referral_name = (request.form.get("referral_name") or "").strip()
    if not client_id:
        flash("Select a client.", "error")
        return redirect(url_for("bni_referrals.list_bni_referrals"))

    client = Client.query.get(client_id)
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("bni_referrals.list_bni_referrals"))

    row = ClientBniReferral.query.filter_by(client_id=client_id).first()
    if not referral_name:
        if row:
            db.session.delete(row)
            db.session.commit()
            flash("BNI referral removed for client.", "success")
        else:
            flash("Nothing to save (empty name).", "info")
        return redirect(url_for("bni_referrals.list_bni_referrals"))

    if row:
        row.referral_name = referral_name
    else:
        db.session.add(ClientBniReferral(client_id=client_id, referral_name=referral_name))
    db.session.commit()
    flash("BNI referral saved.", "success")
    return redirect(url_for("bni_referrals.list_bni_referrals"))
