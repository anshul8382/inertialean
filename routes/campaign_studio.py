from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from services import content_intelligence_service as ci

campaign_studio_bp = Blueprint("campaign_studio", __name__)


@campaign_studio_bp.route("/campaign-studio")
@login_required
def index():
    return render_template("campaign_studio/index.html")


@campaign_studio_bp.route("/campaign-studio/generate-tags", methods=["POST"])
@login_required
def generate_tags():
    data = request.get_json(force=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400
    try:
        return jsonify(ci.generate_adviser_tag_suggestions(content))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/campaign-studio/save-content", methods=["POST"])
@login_required
def save_content():
    data = request.get_json(force=True) or {}
    confirmed = data.get("confirmed_tags") or {}
    if not isinstance(confirmed, dict):
        return jsonify({"error": "confirmed_tags must be an object"}), 400
    campaign_id = data.get("campaign_studio_campaign_id")
    if campaign_id is None:
        return jsonify({"error": "campaign_studio_campaign_id is required"}), 400
    try:
        campaign_id = int(campaign_id)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid campaign_studio_campaign_id"}), 400
    try:
        content_id = ci.save_content_with_tags(
            title=data.get("title") or "",
            format=data.get("format") or "",
            topic_category=data.get("topic_category") or "",
            content_text=data.get("content_text") or "",
            confirmed_tags=confirmed,
            campaign_studio_campaign_id=campaign_id,
        )
        return jsonify({"content_id": content_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/campaign-studio/match-persons/<int:content_id>", methods=["GET"])
@login_required
def match_persons(content_id):
    try:
        return jsonify(ci.match_persons_for_content(content_id))
    except LookupError:
        return jsonify({"error": "content not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/campaign-studio/log-decision", methods=["POST"])
@login_required
def log_decision():
    data = request.get_json(force=True) or {}
    try:
        content_id = int(data.get("content_id"))
        person_id = int(data.get("person_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "content_id and person_id are required integers"}), 400
    try:
        log_id = ci.log_send_decision(
            content_id=content_id,
            person_id=person_id,
            decision=data.get("decision") or "Send",
            channel=data.get("channel") or "",
        )
        return jsonify({"log_id": log_id})
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/campaign-studio/update-engagement/<int:log_id>", methods=["POST"])
@login_required
def update_engagement(log_id):
    data = request.get_json(force=True) or {}
    campaign_id = data.get("campaign_id") or data.get("content_id")
    if campaign_id is None:
        return jsonify({"error": "campaign_id (or content_id) is required in body"}), 400
    try:
        ci.update_engagement_feedback(
            log_id,
            engagement=data.get("engagement") or "None",
            followup_needed=bool(data.get("followup_needed")),
            notes=data.get("notes") or "",
            campaign_id=int(campaign_id),
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/persons/<int:person_id>/suggest-tags", methods=["POST"])
@login_required
def person_suggest_tags(person_id):
    try:
        return jsonify(ci.person_suggest_tags_json(person_id))
    except LookupError:
        return jsonify({"error": "person not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/persons/<int:person_id>/confirm-tags", methods=["POST"])
@login_required
def person_confirm_tags(person_id):
    data = request.get_json(force=True) or {}
    try:
        ci.person_confirm_tags(person_id, data)
        return jsonify({"success": True})
    except LookupError:
        return jsonify({"error": "person not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/persons/<int:person_id>/content-profile", methods=["GET"])
@login_required
def person_content_profile_get(person_id):
    try:
        return jsonify(ci.person_content_profile(person_id))
    except LookupError:
        return jsonify({"error": "person not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/persons/<int:person_id>/content-profile", methods=["POST"])
@login_required
def person_content_profile_save(person_id):
    data = request.get_json(force=True) or {}
    try:
        ci.save_person_content_profile(person_id, data)
        return jsonify({"success": True})
    except LookupError:
        return jsonify({"error": "person not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_bp.route("/campaign-studio/send-log/<int:campaign_id>", methods=["GET"])
@login_required
def campaign_send_log(campaign_id):
    try:
        return jsonify(ci.get_campaign_send_log(campaign_id))
    except LookupError:
        return jsonify({"error": "campaign not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
