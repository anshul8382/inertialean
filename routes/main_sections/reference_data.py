"""Reference data: asset classes and securities (extracted from routes/main.py)."""
from __future__ import annotations

from flask import abort, jsonify, redirect, render_template, request, flash, url_for
from flask_login import login_required, current_user

from extensions import db


def register(main):

    @main.route("/asset-classes")
    @login_required
    def list_asset_classes():
        from models import AssetClass

        asset_classes = AssetClass.query.order_by(AssetClass.name.asc()).all()
        return render_template("asset_classes.html", asset_classes=asset_classes)


    @main.route("/asset-classes/add", methods=["GET", "POST"])
    @login_required
    def add_asset_class():
        from routes.forms import AssetClassForm
        from services.asset_class_service import create_asset_class

        form = AssetClassForm()
        if form.validate_on_submit():
            tax_payload = {
                "ruleset": (form.tax_ruleset.data or "equity").strip().lower()[:32],
                "stcg_rate": form.tax_stcg_rate.data,
                "ltcg_rate": form.tax_ltcg_rate.data,
                "ltcg_exemption_limit": form.tax_ltcg_exemption_limit.data,
                "ltcg_minimum_months": form.tax_ltcg_minimum_months.data,
                "ltcg_eligibility_mode": (form.tax_ltcg_eligibility_mode.data or None) or None,
                "ltcg_holding_days": form.tax_ltcg_holding_days.data,
                "is_active": bool(form.tax_rules_active.data),
                "notes": (form.tax_notes.data or "").strip() or None,
            }
            ac, err = create_asset_class(
                user_id=int(current_user.id),
                name=form.name.data,
                description=form.description.data,
                tax_payload=tax_payload,
            )
            if err:
                flash(err, "danger")
            else:
                flash("Asset class created with tax parameters.", "success")
                return redirect(url_for("main.list_asset_classes"))
        return render_template("add_asset_class.html", form=form)


    @main.route("/asset-classes/<int:asset_class_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_asset_class(asset_class_id):
        from models import AssetClass
        from routes.forms import AssetClassForm
        from services.asset_class_service import update_asset_class

        ac = AssetClass.query.get_or_404(asset_class_id)
        if request.method == "POST":
            form = AssetClassForm()
        else:
            form = AssetClassForm(obj=ac)
            form.tax_ltcg_eligibility_mode.data = ac.tax_ltcg_eligibility_mode or ""

        if form.validate_on_submit():
            tax_payload = {
                "ruleset": (form.tax_ruleset.data or "equity").strip().lower()[:32],
                "stcg_rate": form.tax_stcg_rate.data,
                "ltcg_rate": form.tax_ltcg_rate.data,
                "ltcg_exemption_limit": form.tax_ltcg_exemption_limit.data,
                "ltcg_minimum_months": form.tax_ltcg_minimum_months.data,
                "ltcg_eligibility_mode": (form.tax_ltcg_eligibility_mode.data or None) or None,
                "ltcg_holding_days": form.tax_ltcg_holding_days.data,
                "is_active": bool(form.tax_rules_active.data),
                "notes": (form.tax_notes.data or "").strip() or None,
            }
            _, err = update_asset_class(
                asset_class_id,
                name=form.name.data,
                description=form.description.data,
                tax_payload=tax_payload,
            )
            if err:
                flash(err, "danger")
            else:
                flash("Asset class updated.", "success")
                return redirect(url_for("main.list_asset_classes"))
        return render_template("edit_asset_class.html", form=form, asset_class=ac)


    @main.route("/asset-classes/<int:asset_class_id>/delete", methods=["POST"])
    @login_required
    def delete_asset_class(asset_class_id):
        from services.asset_class_service import delete_asset_class

        err = delete_asset_class(asset_class_id)
        if err:
            flash(err, "danger")
        else:
            flash("Asset class deleted.", "success")
        return redirect(url_for("main.list_asset_classes"))


    @main.route("/securities")
    @login_required
    def securities():
        from services.security_admin_service import build_securities_list_context

        return render_template("securities.html", **build_securities_list_context())


    @main.route("/securities/add", methods=["GET", "POST"])
    @login_required
    def add_security():
        from models import AssetClass
        from services.security_admin_service import create_security_from_add_form

        if request.method == "POST":
            ok, err = create_security_from_add_form(request.form, current_user.id)
            if err:
                flash(err, "error")
            else:
                flash("Security created.", "success")
                return redirect(url_for("main.securities"))
        return render_template(
            "add_security.html",
            asset_classes=AssetClass.query.order_by(AssetClass.name.asc()).all(),
        )


    @main.route("/security/<int:security_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_security(security_id):
        from models import Security
        from routes.forms import SecurityForm
        from services.security_admin_service import update_security_from_validated_form

        security = Security.query.get_or_404(security_id)
        if request.method == "POST":
            form = SecurityForm(request.form, obj=security)
            if not form.validate():
                for _field, msgs in form.errors.items():
                    for m in msgs:
                        flash(m, "error")
            else:
                ok, err = update_security_from_validated_form(security, form, request.form)
                if err:
                    flash(err, "error")
                else:
                    flash("Security updated.", "success")
                    return redirect(url_for("main.securities"))
        else:
            form = SecurityForm(obj=security)
        return render_template("edit_security.html", form=form, security=security)


    @main.route("/security/<int:security_id>/refresh", methods=["POST"])
    @login_required
    def refresh_security(security_id):
        from services.security_admin_service import touch_security_refresh

        ok, err = touch_security_refresh(security_id)
        if err:
            flash(err, "error")
        else:
            flash("Timestamp updated. Use scheduled jobs or uploads to refresh prices from data sources.", "info")
        return redirect(url_for("main.securities"))


    @main.route("/security/<int:security_id>/delete", methods=["POST"])
    @login_required
    def delete_security(security_id):
        from services.security_admin_service import try_delete_security

        ok, err = try_delete_security(security_id)
        accept = request.headers.get("Accept") or ""
        if "application/json" in accept:
            if ok:
                return jsonify({"ok": True})
            return jsonify({"ok": False, "error": err or "Delete failed"}), 400
        if err:
            flash(err, "error")
        else:
            flash("Security deleted.", "success")
        return redirect(url_for("main.securities"))


    @main.route("/securities/record-market-close", methods=["POST"])
    @login_required
    def record_market_close_update():
        from services.security_admin_service import record_market_close_update as _record_mcu

        ok, err = _record_mcu(current_user.id)
        if err:
            flash(err, "error")
        else:
            flash("Market close update recorded for today.", "success")
        return redirect(url_for("main.securities"))


