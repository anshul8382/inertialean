"""
Fallback to local Ollama when cloud LLMs are unavailable (rate limits, missing keys, errors).

Used by claude/openai proxies and content-intelligence tag generation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class LLMFallbackUnavailable(Exception):
    """Raised when cloud failed and Ollama cannot be used."""


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
    """When true, proxies and tagger may use Ollama after cloud failure. Lean default: off."""
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


def _ollama_combined_prompt(system: str, user: str) -> str:
    return (
        f"{system.strip()}\n\n"
        "---\n\n"
        f"{user.strip()}\n\n"
        "Respond with only what was requested (no preamble)."
    )


def generate_text_via_ollama(
    system: str,
    user: str,
    *,
    max_tokens: int = 1200,
    temperature: float = 0.2,
) -> str:
    from services.ollama_service import OllamaUnavailableError, ollama_service

    prompt = _ollama_combined_prompt(system, user)
    try:
        return ollama_service.generate(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            allow_when_ai_disabled=True,
        )
    except OllamaUnavailableError as exc:
        raise LLMFallbackUnavailable(str(exc)) from exc


def format_anthropic_message(text: str, *, model: str = "ollama-local") -> dict:
    return {
        "id": "msg_ollama_fallback",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "_llm_provider": "ollama",
    }


def format_openai_chat_completion(text: str, *, model: str = "ollama-local") -> dict:
    return {
        "id": "chatcmpl-ollama-fallback",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "_llm_provider": "ollama",
    }


def try_cloud_then_ollama_anthropic(
    *,
    api_key: str,
    payload: dict,
    cloud_request_fn,
) -> Tuple[dict, int]:
    """
    cloud_request_fn: callable returning (response_json, status_code).
  """
    tools = payload.get("tools")
    system = payload.get("system") or ""
    user_msg = ""
    for m in payload.get("messages") or []:
        if m.get("role") == "user":
            user_msg = m.get("content") or ""
            break
    max_tokens = int(payload.get("max_tokens") or 1500)

    if not api_key:
        if tools:
            raise LLMFallbackUnavailable(
                "ANTHROPIC_API_KEY not configured. Web-search research requires a cloud API key."
            )
        if not llm_fallback_to_ollama_enabled():
            raise LLMFallbackUnavailable("ANTHROPIC_API_KEY not configured on server")
        text = generate_text_via_ollama(system, user_msg, max_tokens=max_tokens)
        logger.info("Claude proxy: no API key — using Ollama fallback")
        return format_anthropic_message(text), 200

    data, status = cloud_request_fn()
    if status == 200:
        return data, status

    if tools:
        return data, status

    if should_fallback_from_http(status, data if isinstance(data, dict) else None):
        try:
            text = generate_text_via_ollama(system, user_msg, max_tokens=max_tokens)
            logger.warning(
                "Claude proxy: cloud status=%s — using Ollama fallback",
                status,
            )
            return format_anthropic_message(text), 200
        except LLMFallbackUnavailable:
            pass

    return data, status


def try_cloud_then_ollama_openai(
    *,
    api_key: str,
    payload: dict,
    cloud_request_fn,
) -> Tuple[dict, int]:
    messages = payload.get("messages") or []
    system = ""
    user_msg = ""
    for m in messages:
        role = m.get("role")
        if role == "system":
            system = m.get("content") or ""
        elif role == "user":
            user_msg = m.get("content") or ""
    max_tokens = int(payload.get("max_tokens") or 2000)

    if not api_key:
        if not llm_fallback_to_ollama_enabled():
            raise LLMFallbackUnavailable("OPENAI_API_KEY not configured on server")
        text = generate_text_via_ollama(system, user_msg, max_tokens=max_tokens)
        logger.info("OpenAI proxy: no API key — using Ollama fallback")
        return format_openai_chat_completion(text), 200

    data, status = cloud_request_fn()
    if status == 200:
        return data, status

    if should_fallback_from_http(status, data if isinstance(data, dict) else None):
        try:
            text = generate_text_via_ollama(system, user_msg, max_tokens=max_tokens)
            logger.warning(
                "OpenAI proxy: cloud status=%s — using Ollama fallback",
                status,
            )
            return format_openai_chat_completion(text), 200
        except LLMFallbackUnavailable:
            pass

    return data, status
