import os
import requests
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required

from services.llm_fallback_service import (
    LLMFallbackUnavailable,
    generate_text_via_ollama,
    format_anthropic_message,
    llm_fallback_to_ollama_enabled,
    try_cloud_then_ollama_anthropic,
)

claude_proxy_bp = Blueprint("claude_proxy", __name__)


@claude_proxy_bp.route("/api/v1/claude-proxy", methods=["POST"])
@login_required
def claude_proxy():
    """Proxy Claude API calls so the API key stays server-side. Falls back to Ollama when configured."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or current_app.config.get("ANTHROPIC_API_KEY", "")

    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"error": "Invalid JSON body"}), 400

    if payload.get("tools") and payload.get("max_tokens", 0) < 4000:
        payload["max_tokens"] = 4000

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "web-search-2025-03-05",
    }

    def cloud_request():
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
            timeout=120,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            err = data.get("error", {}) or data
            msg = err.get("message", err.get("type", str(err))) if isinstance(err, dict) else str(err)
            current_app.logger.warning(
                "Claude API error status=%s type=%s message=%s",
                resp.status_code,
                err.get("type", "") if isinstance(err, dict) else "",
                (msg[:200] if isinstance(msg, str) else str(msg)),
            )
        return data, resp.status_code

    try:
        data, status = try_cloud_then_ollama_anthropic(
            api_key=api_key,
            payload=payload,
            cloud_request_fn=cloud_request,
        )
        return jsonify(data), status
    except LLMFallbackUnavailable as exc:
        return jsonify({"error": {"type": "configuration_error", "message": str(exc)}}), 503
    except requests.exceptions.Timeout:
        if payload.get("tools") or not llm_fallback_to_ollama_enabled():
            current_app.logger.warning("Claude API timed out")
            return jsonify({"error": "Claude API timed out"}), 504
        system = payload.get("system") or ""
        user_msg = ""
        for m in payload.get("messages") or []:
            if m.get("role") == "user":
                user_msg = m.get("content") or ""
                break
        try:
            text = generate_text_via_ollama(
                system,
                user_msg,
                max_tokens=int(payload.get("max_tokens") or 1500),
            )
            current_app.logger.warning("Claude API timed out — using Ollama fallback")
            return jsonify(format_anthropic_message(text)), 200
        except LLMFallbackUnavailable:
            return jsonify({"error": "Claude API timed out"}), 504
    except Exception as exc:
        current_app.logger.error("Claude proxy error: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
