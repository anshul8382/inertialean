"""Household (ClientGroup) routes — consolidated details and review snapshot."""
from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from access_control import can_access_client, get_accessible_clients
from extensions import db
from models import Client

logger = logging.getLogger(__name__)

households_bp = Blueprint("households", __name__)


def _require_group_access(group):
    from services.household_portfolio_service import user_can_access_group

    if not group or not user_can_access_group(group, can_access_client):
        flash("Access denied or household not found.", "error")
        return False
    return True


@households_bp.route("/")
@login_required
def list_households():
    from services.household_portfolio_service import _tables_available
    from models import ClientGroup

    if not _tables_available():
        flash(
            "Household tables are not installed yet. Run migrations/add_client_group_tables.py on the server.",
            "warning",
        )
        return render_template("households/list.html", groups=[])

    accessible_ids = {c.id for c in get_accessible_clients()}
    groups = ClientGroup.query.order_by(ClientGroup.name).all()
    visible = []
    for g in groups:
        member_ids = {m.client_id for m in (g.members or [])}
        if member_ids and member_ids.issubset(accessible_ids):
            visible.append(g)
    return render_template("households/list.html", groups=visible)


@households_bp.route("/<int:group_id>")
@login_required
def household_details(group_id):
    from services.household_portfolio_service import build_household_context, get_group

    group = get_group(group_id)
    if not _require_group_access(group):
        return redirect(url_for("clients_v2.list_clients"))

    ctx = build_household_context(group_id)
    if not ctx:
        flash("Household has no members.", "error")
        return redirect(url_for("households.list_households"))

    from utils.portfolio_asset_class_display import sort_asset_class_grouped_dict

    by_ac = ctx["holdings"]["by_asset_class"]
    flat = {ac: g["items"] for ac, g in by_ac.items()}
    sorted_flat = sort_asset_class_grouped_dict(flat)
    ordered_sections = []
    for ac, items in sorted_flat.items():
        ordered_sections.append(
            {
                "asset_class": ac,
                "total": by_ac[ac]["total"],
                "holdings": items,
            }
        )

    return render_template(
        "households/details.html",
        group=ctx["group"],
        members=ctx["members"],
        totals=ctx["totals"],
        household_xirr=ctx["household_xirr"],
        holding_sections=ordered_sections,
        holdings_total=ctx["holdings"]["total_value"],
    )


@households_bp.route("/<int:group_id>/review")
@login_required
def household_review(group_id):
    from services.household_portfolio_service import build_household_context, get_group

    group = get_group(group_id)
    if not _require_group_access(group):
        return redirect(url_for("clients_v2.list_clients"))

    ctx = build_household_context(group_id)
    if not ctx:
        flash("Household has no members.", "error")
        return redirect(url_for("households.list_households"))

    by_ac = ctx["holdings"]["by_asset_class"]
    allocation = [
        {"asset_class": ac, "value": g["total"]}
        for ac, g in sorted(by_ac.items(), key=lambda x: x[1]["total"], reverse=True)
    ]

    return render_template(
        "households/review.html",
        group=ctx["group"],
        members=ctx["members"],
        totals=ctx["totals"],
        household_xirr=ctx["household_xirr"],
        allocation=allocation,
        top_holdings=ctx["holdings"]["holdings"][:15],
    )


@households_bp.route("/create", methods=["GET", "POST"])
@login_required
def create_household():
    from services.household_portfolio_service import (
        MEMBER_ROLES,
        _tables_available,
        add_member,
        create_group,
    )
    from services.audit_service import log_audit_event

    if not _tables_available():
        flash("Household tables are not installed yet. Run the migration first.", "error")
        return redirect(url_for("clients_v2.list_clients"))

    primary_id = request.values.get("primary_client_id", type=int)
    if request.method == "GET":
        primary_name = ""
        if primary_id:
            pc = Client.query.get(primary_id)
            if pc and can_access_client(primary_id):
                primary_name = pc.name or ""
            else:
                primary_id = None
        return render_template(
            "households/create.html",
            primary_client_id=primary_id,
            primary_client_name=primary_name,
            roles=MEMBER_ROLES,
        )

    name = (request.form.get("name") or "").strip()
    primary_id = request.form.get("primary_client_id", type=int)
    if not primary_id or not can_access_client(primary_id):
        flash("Primary client is required.", "error")
        return redirect(url_for("households.create_household"))

    try:
        group = create_group(name, primary_id, user_id=current_user.id)
        extra_ids = request.form.getlist("member_client_ids")
        for raw in extra_ids:
            try:
                cid = int(raw)
            except (TypeError, ValueError):
                continue
            if cid == primary_id or not can_access_client(cid):
                continue
            role = request.form.get(f"role_{cid}", "other")
            try:
                add_member(group.id, cid, role=role)
            except ValueError as ve:
                flash(str(ve), "warning")

        log_audit_event(
            "household.created",
            client_id=primary_id,
            resource_type="client_group",
            resource_id=str(group.id),
            details={"name": group.name},
            commit=True,
        )
        flash(f'Household "{group.name}" created.', "success")
        return redirect(url_for("households.household_details", group_id=group.id))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("households.create_household", primary_client_id=primary_id))
    except Exception as e:
        db.session.rollback()
        logger.exception("create_household failed")
        flash(f"Could not create household: {e}", "error")
        return redirect(url_for("households.create_household"))


@households_bp.route("/<int:group_id>/members", methods=["GET", "POST"])
@login_required
def manage_members(group_id):
    from services.household_portfolio_service import (
        MEMBER_ROLES,
        add_member,
        get_group,
        remove_member,
        update_group_name,
    )
    from services.audit_service import log_audit_event

    group = get_group(group_id)
    if not _require_group_access(group):
        return redirect(url_for("clients_v2.list_clients"))

    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "rename":
                update_group_name(group_id, request.form.get("name") or group.name)
                flash("Household name updated.", "success")
            elif action == "add":
                cid = request.form.get("client_id", type=int)
                role = request.form.get("role") or "other"
                if not cid or not can_access_client(cid):
                    flash("Select a valid client.", "error")
                else:
                    add_member(group_id, cid, role=role)
                    log_audit_event(
                        "household.member_added",
                        client_id=cid,
                        resource_type="client_group",
                        resource_id=str(group_id),
                        details={"role": role},
                        commit=True,
                    )
                    flash("Member added.", "success")
            elif action == "remove":
                cid = request.form.get("client_id", type=int)
                if cid:
                    remove_member(group_id, cid)
                    log_audit_event(
                        "household.member_removed",
                        client_id=cid,
                        resource_type="client_group",
                        resource_id=str(group_id),
                        details={},
                        commit=True,
                    )
                    flash("Member removed.", "success")
        except ValueError as e:
            flash(str(e), "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Update failed: {e}", "error")
        return redirect(url_for("households.manage_members", group_id=group_id))

    member_ids = {m.client_id for m in (group.members or [])}
    return render_template(
        "households/manage.html",
        group=group,
        members=group.members,
        exclude_client_ids=sorted(member_ids),
        roles=MEMBER_ROLES,
    )
