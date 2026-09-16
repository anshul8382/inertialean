import os
import requests
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required

perplexity_proxy_bp = Blueprint("perplexity_proxy", __name__)

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"


@perplexity_proxy_bp.route("/api/v1/perplexity-proxy", methods=["POST"])
@login_required
def perplexity_proxy():
    """Proxy Perplexity API calls for Campaign Studio research phase."""
    api_key = os.environ.get("PERPLEXITY_API_KEY") or current_app.config.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        return jsonify({"error": "PERPLEXITY_API_KEY not configured on server"}), 503

    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"error": "Invalid JSON body"}), 400

    # Default to sonar-pro (has live web search built-in)
    if "model" not in payload:
        payload["model"] = "sonar-pro"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        resp = requests.post(
            PERPLEXITY_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            err = data.get("error", {}) or data
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            current_app.logger.warning(
                "Perplexity API error status=%s message=%s",
                resp.status_code,
                (msg[:200] if isinstance(msg, str) else str(msg)),
            )
        return jsonify(data), resp.status_code
    except requests.exceptions.Timeout:
        current_app.logger.warning("Perplexity API timed out")
        return jsonify({"error": "Perplexity API timed out"}), 504
    except Exception as exc:
        current_app.logger.error("Perplexity proxy error: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
