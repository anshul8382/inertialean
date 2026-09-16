"""Campaign Studio: persist campaigns to database (list, create, update, delete)."""
import json
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import CampaignStudioCampaign

campaign_studio_api_bp = Blueprint("campaign_studio_api", __name__)


def _campaign_to_json(c):
    """Turn DB row into the shape the frontend expects (id = server id, keep rest)."""
    try:
        payload = json.loads(c.payload) if isinstance(c.payload, str) else (c.payload or {})
    except Exception:
        payload = {}
    ci = payload.get("contentIntelligence") or {}
    return {
        "id": c.id,
        "serverId": c.id,
        "title": c.title,
        "ts": payload.get("ts"),
        "dpCount": payload.get("dpCount"),
        "status": payload.get("status"),
        "article": payload.get("article"),
        "articleUrl": payload.get("articleUrl", ""),
        "posts": payload.get("posts", {}),
        "auditLog": payload.get("auditLog", []),
        "type": payload.get("type"),
        "coverImageUrl": payload.get("coverImageUrl"),
        "designSuggestions": payload.get("designSuggestions"),
        "contentIntelligence": {
            "confirmed_tags": ci.get("confirmed_tags"),
            "saved_at": ci.get("saved_at"),
            "topic_category": ci.get("topic_category"),
            "send_log": ci.get("send_log") or [],
        } if ci else None,
    }


@campaign_studio_api_bp.route("/campaigns", methods=["GET"])
@login_required
def list_campaigns():
    """List current user's Campaign Studio campaigns, newest first."""
    try:
        rows = CampaignStudioCampaign.query.filter_by(user_id=current_user.id).order_by(
            CampaignStudioCampaign.updated_at.desc()
        ).limit(100).all()
        return jsonify([_campaign_to_json(c) for c in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@campaign_studio_api_bp.route("/campaigns", methods=["POST"])
@login_required
def create_campaign():
    """Create a new campaign (body: full campaign object from frontend)."""
    try:
        data = request.get_json(force=True)
        title = (data.get("title") or "").strip() or "Untitled campaign"
        payload = {
            "title": title,
            "ts": data.get("ts"),
            "dpCount": data.get("dpCount"),
            "status": data.get("status", "article_ready"),
            "article": data.get("article"),
            "articleUrl": data.get("articleUrl", ""),
            "posts": data.get("posts", {}),
            "auditLog": data.get("auditLog", []),
            "type": data.get("type"),
            "coverImageUrl": data.get("coverImageUrl"),
            "designSuggestions": data.get("designSuggestions"),
        }
        row = CampaignStudioCampaign(
            user_id=current_user.id,
            title=title,
            payload=json.dumps(payload),
        )
        db.session.add(row)
        db.session.commit()
        return jsonify(_campaign_to_json(row)), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@campaign_studio_api_bp.route("/campaigns/<int:campaign_id>", methods=["PUT"])
@login_required
def update_campaign(campaign_id):
    """Update an existing campaign (e.g. articleUrl, posts)."""
    try:
        row = CampaignStudioCampaign.query.filter_by(id=campaign_id, user_id=current_user.id).first()
        if not row:
            return jsonify({"error": "Campaign not found"}), 404
        data = request.get_json(force=True)
        try:
            payload = json.loads(row.payload) if isinstance(row.payload, str) else (row.payload or {})
        except Exception:
            payload = {}
        if "title" in data:
            row.title = (data.get("title") or "").strip() or row.title
        if "articleUrl" in data:
            payload["articleUrl"] = data.get("articleUrl", "")
        if "posts" in data:
            payload["posts"] = data.get("posts", payload.get("posts", {}))
        if "article" in data:
            payload["article"] = data.get("article")
        if "auditLog" in data:
            payload["auditLog"] = data.get("auditLog", [])
        if "status" in data:
            payload["status"] = data.get("status")
        if "coverImageUrl" in data:
            payload["coverImageUrl"] = data.get("coverImageUrl")
        if "designSuggestions" in data:
            payload["designSuggestions"] = data.get("designSuggestions")
        row.payload = json.dumps(payload)
        db.session.commit()
        return jsonify(_campaign_to_json(row))
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@campaign_studio_api_bp.route("/campaigns/<int:campaign_id>", methods=["DELETE"])
@login_required
def delete_campaign(campaign_id):
    """Delete a campaign."""
    try:
        row = CampaignStudioCampaign.query.filter_by(id=campaign_id, user_id=current_user.id).first()
        if not row:
            return jsonify({"error": "Campaign not found"}), 404
        db.session.delete(row)
        db.session.commit()
        return jsonify({"ok": True}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
