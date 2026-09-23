"""
Cloud LLM helpers — Ollama fallback removed on Lean/KVM.

``should_fallback_from_http`` and format helpers remain for proxies/tagger.
``generate_text_via_ollama`` always raises; cloud→local fallback never runs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class LLMFallbackUnavailable(Exception):
    """Raised when cloud LLM cannot be used and local fallback is not available."""


def _config_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        try:
            from flask import has_app_context, current_app

            if has_app_context():
                val = current_app.config.get(name)
                if val is not None:
                    return bool(val)
        except Exception:
            pass
        return default
    return raw.strip().lower() in ("true", "1", "on", "yes")


def llm_fallback_to_ollama_enabled() -> bool:
    """
    Legacy flag for ``should_fallback_from_http`` tests/callers.

    On Lean/KVM, generate_text_via_ollama always raises even if this is true —
    there is no Ollama host. Prefer leaving LLM_FALLBACK_TO_OLLAMA unset/false.
    """
    return _config_bool("LLM_FALLBACK_TO_OLLAMA", default=False)


def should_fallback_from_http(status_code: int, error_payload: Optional[dict] = None) -> bool:
    if not llm_fallback_to_ollama_enabled():
        return False
    if status_code in (401, 403, 429, 500, 502, 503, 504, 529):
        return True
    if status_code == 400 and error_payload:
        err = error_payload.get("error") if isinstance(error_payload.get("error"), dict) else error_payload
        if isinstance(err, dict):
            etype = (err.get("type") or "").lower()
            msg = (err.get("message") or "").lower()
            if etype in ("rate_limit_error", "invalid_request_error", "authentication_error"):
                return True
            if "usage limit" in msg or "rate limit" in msg or "credit" in msg or "billing" in msg:
                return True
    return False


def extract_json_object(text: str) -> dict:
    """Parse JSON from model output that may include markdown fences."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", s)
        if not match:
            raise
        return json.loads(match.group(0))


def generate_text_via_ollama(
    system: str,
    user: str,
    *,
    max_tokens: int = 1500,
    temperature: float = 0.2,
) -> str:
    raise LLMFallbackUnavailable(
        "Local LLM (Ollama) fallback is not part of this Lean/KVM build. "
        "Configure a cloud API key or use non-AI workflows."
    )


def format_anthropic_message(text: str, *, model: str = "ollama-local") -> dict:
    # Provider label kept as "ollama" for legacy callers/tests; generation is disabled.
    return {
        "id": "msg_local_removed",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "_llm_provider": "ollama",
    }


def format_openai_chat_completion(text: str, *, model: str = "ollama-local") -> dict:
    return {
        "id": "chatcmpl-local-removed",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "model": model,
        "_llm_provider": "ollama",
    }


def try_cloud_then_ollama_anthropic(
    *,
    api_key: str,
    payload: dict,
    cloud_request_fn,
) -> Tuple[dict, int]:
    """Cloud only — name kept for import compatibility; no Ollama I/O."""
    tools = payload.get("tools")

    if not api_key:
        if tools:
            raise LLMFallbackUnavailable(
                "ANTHROPIC_API_KEY not configured. Web-search research requires a cloud API key."
            )
        raise LLMFallbackUnavailable(
            "ANTHROPIC_API_KEY not configured. Local Ollama fallback was removed on Lean/KVM."
        )

    data, status = cloud_request_fn()
    if status == 200:
        return data, status

    if tools:
        return data, status

    return data, status


def try_cloud_then_ollama_openai(
    *,
    api_key: str,
    payload: dict,
    cloud_request_fn,
) -> Tuple[dict, int]:
    """Cloud only — name kept for import compatibility; no Ollama I/O."""
    if not api_key:
        raise LLMFallbackUnavailable(
            "OPENAI_API_KEY not configured. Local Ollama fallback was removed on Lean/KVM."
        )

    data, status = cloud_request_fn()
    if status == 200:
        return data, status

    return data, status
