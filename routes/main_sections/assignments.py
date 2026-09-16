"""Maintenance: assign clients (advisor) and leads (owner user)."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from access_control import require_manager


def register(main):

    @main.route("/maintenance/assignments", methods=["GET", "POST"])
    @main.route("/maintenance/client-advisor-assignments", methods=["GET", "POST"])
    @login_required
    @require_manager
    def maintenance_client_advisor_assignments():
        from services.client_advisor_assignment_service import (
            assign_client_to_advisor,
            assign_lead_to_user,
            bulk_assign_clients_to_advisor,
            bulk_assign_leads_to_user,
            get_client_ids_for_assignment,
            get_clients_for_assignment,
            get_lead_ids_for_assignment,
            get_leads_for_assignment,
            list_assignable_users,
        )

        users = list_assignable_users()
        tab = request.args.get("tab", "clients")
        if tab not in ("clients", "leads"):
            tab = "clients"

        redirect_args = request.args.to_dict(flat=True)

        if request.method == "POST":
            bulk_scope = (request.form.get("bulk_scope") or "").strip()
            bulk_entity = (request.form.get("bulk_entity") or "clients").strip()
            advisor_id = request.form.get("advisor_id", type=int)
            notes = (request.form.get("notes") or "").strip()
            lead_id = request.form.get("lead_id", type=int)
            client_id = request.form.get("client_id", type=int)

            if bulk_scope in ("selected", "filtered"):
                if not advisor_id:
                    flash("Select a user to assign to.", "error")
                elif bulk_entity == "leads":
                    if bulk_scope == "filtered":
                        lq = (request.form.get("filter_lq") or "").strip()
                        filter_owner_id = request.form.get(
                            "filter_owner_id", type=int
                        )
                        lead_ids, truncated = get_lead_ids_for_assignment(
                            search=lq or None,
                            owner_id=filter_owner_id,
                        )
                        if truncated:
                            flash(
                                "Only the first 5,000 matching leads were included.",
                                "warning",
                            )
                    else:
                        lead_ids = request.form.getlist("lead_ids", type=int)

                    result = bulk_assign_leads_to_user(
                        lead_ids,
                        advisor_id,
                        current_user.id,
                    )
                    redirect_args["tab"] = "leads"
                else:
                    if bulk_scope == "filtered":
                        search = (request.form.get("filter_q") or "").strip()
                        filter_advisor_id = request.form.get(
                            "filter_advisor_id", type=int
                        )
                        unassigned_only = (
                            request.form.get("filter_unassigned") == "1"
                        )
                        client_ids, truncated = get_client_ids_for_assignment(
                            search=search or None,
                            advisor_id=filter_advisor_id,
                            unassigned_only=unassigned_only,
                        )
                        if truncated:
                            flash(
                                "Only the first 5,000 matching clients were included.",
                                "warning",
                            )
                    else:
                        client_ids = request.form.getlist("client_ids", type=int)

                    result = bulk_assign_clients_to_advisor(
                        client_ids,
                        advisor_id,
                        current_user.id,
                        notes=notes or None,
                    )
                    redirect_args["tab"] = "clients"

                category = (
                    "success"
                    if result.get("ok") and result.get("assigned")
                    else "info"
                    if result.get("ok")
                    else "error"
                )
                flash(result.get("message", "Bulk assignment finished."), category)
                if result.get("errors"):
                    flash("; ".join(result["errors"]), "error")

            elif lead_id:
                if not advisor_id:
                    flash("Select a user for this lead.", "error")
                else:
                    result = assign_lead_to_user(
                        lead_id, advisor_id, current_user.id
                    )
                    redirect_args["tab"] = "leads"
                    if result.get("ok"):
                        flash(
                            result.get("message", "Lead assignment updated."),
                            "info" if result.get("unchanged") else "success",
                        )
                    else:
                        flash(result.get("error", "Assignment failed."), "error")

            elif client_id:
                if not advisor_id:
                    flash("Select an advisor for this client.", "error")
                else:
                    result = assign_client_to_advisor(
                        client_id,
                        advisor_id,
                        current_user.id,
                        notes=notes or None,
                    )
                    redirect_args["tab"] = "clients"
                    if result.get("ok"):
                        flash(
                            result.get("message", "Advisor assignment updated."),
                            "info" if result.get("unchanged") else "success",
                        )
                    else:
                        flash(result.get("error", "Assignment failed."), "error")
            else:
                flash("Nothing to assign — select a client or lead.", "error")

            return redirect(
                url_for(
                    "main.maintenance_client_advisor_assignments",
                    **redirect_args,
                )
            )

        search = (request.args.get("q") or "").strip()
        filter_advisor_id = request.args.get("advisor_id", type=int)
        unassigned_only = request.args.get("unassigned") == "1"
        page = request.args.get("page", 1, type=int)

        lq = (request.args.get("lq") or "").strip()
        filter_owner_id = request.args.get("owner_id", type=int)
        lead_page = request.args.get("lead_page", 1, type=int)

        rows, pagination = get_clients_for_assignment(
            search=search or None,
            advisor_id=filter_advisor_id,
            unassigned_only=unassigned_only,
            page=max(1, page),
            per_page=50,
        )
        pick_clients, _ = get_clients_for_assignment(
            search=search or None,
            advisor_id=filter_advisor_id,
            unassigned_only=unassigned_only,
            page=1,
            per_page=300,
            active_only=True,
        )

        lead_rows, lead_pagination = get_leads_for_assignment(
            search=lq or None,
            owner_id=filter_owner_id,
            page=max(1, lead_page),
            per_page=50,
        )
        pick_leads, _ = get_leads_for_assignment(
            search=lq or None,
            owner_id=filter_owner_id,
            page=1,
            per_page=300,
        )

        return render_template(
            "maintenance/assignments.html",
            tab=tab,
            clients=rows,
            pick_clients=pick_clients,
            pagination=pagination,
            leads=lead_rows,
            pick_leads=pick_leads,
            lead_pagination=lead_pagination,
            users=users,
            search=search,
            filter_advisor_id=filter_advisor_id,
            unassigned_only=unassigned_only,
            lq=lq,
            filter_owner_id=filter_owner_id,
        )
