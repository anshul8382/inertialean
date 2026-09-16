"""Shared Anthropic client for server-side LLM calls (Campaign Studio tags, hybrid reports)."""
from __future__ import annotations

import os
from typing import Optional

_client = None


def resolve_anthropic_api_key() -> str:
    """Env first, then Flask config — same as claude_proxy and hybrid reports."""
    k = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if k:
        return k
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            cfg = current_app.config.get("ANTHROPIC_API_KEY") or ""
            if isinstance(cfg, str) and cfg.strip():
                return cfg.strip()
    except Exception:
        pass
    return ""


def get_anthropic_client():
    """Return a process-wide Anthropic client, or None if API key is not configured."""
    global _client
    if _client is not None:
        return _client
    key = resolve_anthropic_api_key()
    if not key:
        return None
    import anthropic

    _client = anthropic.Anthropic(api_key=key)
    return _client
