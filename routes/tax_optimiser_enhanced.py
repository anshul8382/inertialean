from flask import Blueprint, jsonify, make_response, render_template, request, url_for
from flask_login import login_required

from access_control import require_advisor_or_manager
from services.fy_tax_utils import fy_dropdown_options

tax_optimiser_enhanced_bp = Blueprint("tax_optimiser_enhanced", __name__)

# Bumped when the enhanced tax optimiser HTML/JS changes materially (helps verify deploy vs browser cache).
TXO_ENHANCED_UI_BUILD = "interactive-review-status-report-20260324-mark-reviewed-no-unrealised-loss"


@tax_optimiser_enhanced_bp.route("/tools/tax-optimiser-enhanced")
@login_required
@require_advisor_or_manager
def tax_optimiser_enhanced_report():
    html = render_template(
        "tools/tax_optimiser_enhanced.html",
        txo_enhanced_ui_build=TXO_ENHANCED_UI_BUILD,
        fy_options=fy_dropdown_options(back_years=8),
        txo_fy_options_url=url_for("tax_optimiser_enhanced.tax_optimiser_fy_options", back_years=8),
    )
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@tax_optimiser_enhanced_bp.route("/tools/tax-optimiser-enhanced/fy-options", methods=["GET"])
@login_required
@require_advisor_or_manager
def tax_optimiser_fy_options():
    """
    Same payload as /api/v1/enhanced-tax-optimiser/fy-options but on the page blueprint.
    Avoids 404 when nginx or an older API bundle does not expose the v1 route.
    """
    back = request.args.get("back_years", type=int) or 8
    back = min(max(back, 1), 15)
    return jsonify({"ok": True, "options": fy_dropdown_options(back_years=back)})
