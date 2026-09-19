"""LLM fallback helpers — no network.

Lean VPS has no Ollama; LLM_FALLBACK_TO_OLLAMA defaults to false.
Fallback behaviour is only asserted when that flag is explicitly enabled.
"""

import pytest

from services.llm_fallback_service import (
    extract_json_object,
    format_anthropic_message,
    should_fallback_from_http,
)


def test_no_fallback_when_ollama_disabled(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_TO_OLLAMA", "false")
    payload = {
        "error": {
            "type": "invalid_request_error",
            "message": "You have reached your specified API usage limits.",
        }
    }
    assert should_fallback_from_http(429, None) is False
    assert should_fallback_from_http(400, payload) is False


def test_should_fallback_usage_limit_400(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_TO_OLLAMA", "true")
    payload = {
        "error": {
            "type": "invalid_request_error",
            "message": "You have reached your specified API usage limits.",
        }
    }
    assert should_fallback_from_http(400, payload) is True


def test_should_fallback_429(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_TO_OLLAMA", "true")
    assert should_fallback_from_http(429, None) is True


def test_should_not_fallback_400_generic(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_TO_OLLAMA", "true")
    assert should_fallback_from_http(400, {"error": {"type": "bad_request", "message": "missing field"}}) is False


def test_extract_json_object_with_fences():
    raw = '```json\n{"tags": {"behav_friction": true}}\n```'
    assert extract_json_object(raw)["tags"]["behav_friction"] is True


def test_format_anthropic_message_provider():
    msg = format_anthropic_message("hello")
    assert msg["_llm_provider"] == "ollama"
    assert msg["content"][0]["text"] == "hello"
