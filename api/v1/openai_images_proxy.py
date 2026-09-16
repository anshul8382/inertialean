"""Proxy for OpenAI Images API (DALL·E 3) — Campaign Studio cover images."""
import os
import requests
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required

openai_images_proxy_bp = Blueprint("openai_images_proxy", __name__)

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"


@openai_images_proxy_bp.route("/api/v1/openai-images-proxy", methods=["POST"])
@login_required
def openai_images():
    """Proxy OpenAI images/generations (DALL·E 3) for Campaign Studio."""
    api_key = os.environ.get("OPENAI_API_KEY") or current_app.config.get("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "OPENAI_API_KEY not configured on server"}), 503

    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"error": "Invalid JSON body"}), 400

    if "model" not in payload:
        payload["model"] = "dall-e-3"
    if "size" not in payload:
        payload["size"] = "1792x1024"
    if "quality" not in payload:
        payload["quality"] = "standard"
    if "n" not in payload:
        payload["n"] = 1

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        resp = requests.post(
            OPENAI_IMAGES_URL,
            headers=headers,
            json=payload,
            timeout=90,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.Timeout:
        return jsonify({"error": "OpenAI Images API timed out"}), 504
    except Exception as exc:
        current_app.logger.error(f"OpenAI images proxy error: {exc}")
        return jsonify({"error": str(exc)}), 500
