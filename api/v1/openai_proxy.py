import os
import requests
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required

from services.llm_fallback_service import (
    LLMFallbackUnavailable,
    generate_text_via_ollama,
    format_openai_chat_completion,
    llm_fallback_to_ollama_enabled,
    try_cloud_then_ollama_openai,
)

openai_proxy_bp = Blueprint("openai_proxy", __name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


@openai_proxy_bp.route("/api/v1/openai-proxy", methods=["POST"])
@login_required
def openai_proxy():
    """Proxy OpenAI API calls for Campaign Studio. Falls back to Ollama when configured."""
    api_key = os.environ.get("OPENAI_API_KEY") or current_app.config.get("OPENAI_API_KEY", "")

    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"error": "Invalid JSON body"}), 400

    if "model" not in payload:
        payload["model"] = "gpt-4o"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    def cloud_request():
        resp = requests.post(
            OPENAI_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            err = data.get("error", {}) or data
            msg = err.get("message", err.get("code", str(err))) if isinstance(err, dict) else str(err)
            current_app.logger.warning(
                "OpenAI API error status=%s code=%s message=%s",
                resp.status_code,
                err.get("code", "") if isinstance(err, dict) else "",
                (msg[:200] if isinstance(msg, str) else str(msg)),
            )
        return data, resp.status_code

    try:
        data, status = try_cloud_then_ollama_openai(
            api_key=api_key,
            payload=payload,
            cloud_request_fn=cloud_request,
        )
        return jsonify(data), status
    except LLMFallbackUnavailable as exc:
        return jsonify({"error": {"message": str(exc)}}), 503
    except requests.exceptions.Timeout:
        if not llm_fallback_to_ollama_enabled():
            return jsonify({"error": "OpenAI API timed out"}), 504
        messages = payload.get("messages") or []
        system = ""
        user_msg = ""
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content") or ""
            elif m.get("role") == "user":
                user_msg = m.get("content") or ""
        try:
            text = generate_text_via_ollama(
                system,
                user_msg,
                max_tokens=int(payload.get("max_tokens") or 2000),
            )
            current_app.logger.warning("OpenAI API timed out — using Ollama fallback")
            return jsonify(format_openai_chat_completion(text)), 200
        except LLMFallbackUnavailable:
            return jsonify({"error": "OpenAI API timed out"}), 504
    except Exception as exc:
        current_app.logger.error("OpenAI proxy error: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 500
