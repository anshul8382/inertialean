"""
Assistant LLM helper (local-only)
=================================
Thin wrapper over ``services.ollama_service`` for applied-intelligence modules.

- **Local only:** routes prompts to the local Ollama (llama3.1:8b). Never sends
  client data to a cloud LLM.
- **Structured output:** ``generate_json`` parses and returns JSON from the model
  output, tolerating code fences / surrounding prose. Returns ``None`` on failure
  so callers can fail closed rather than act on garbage.
- Callers own guardrails: build compact prompts, validate the parsed result, and
  keep decisions deterministic (the model phrases/extracts; Python decides).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from services.ollama_service import (
    OllamaUnavailableError,
    ai_services_enabled,
    ollama_enabled,
    ollama_service,
)

logger = logging.getLogger(__name__)


def local_ai_available() -> bool:
    """True only when AI + Ollama are explicitly enabled (lean default: False)."""
    return ai_services_enabled() and ollama_enabled()


def generate_text(prompt: str, *, max_tokens: int = 400, temperature: float = 0.1) -> Optional[str]:
    """Generate text via the local model. Returns None if AI is disabled/unreachable."""
    try:
        return ollama_service.generate(prompt, max_tokens=max_tokens, temperature=temperature)
    except OllamaUnavailableError as exc:
        logger.info("Local AI unavailable: %s", exc)
        return None
    except Exception as exc:  # network/HTTP/timeout — fail closed
        logger.warning("Local AI generation failed: %s", exc)
        return None


def _extract_json_blob(raw: str) -> Optional[str]:
    """Return the first JSON object/array substring from a model response."""
    if not raw:
        return None
    text = raw.strip()
    # Strip common ```json ... ``` fences.
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    # Find the outermost {...} or [...] span.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
    return None


def generate_json(prompt: str, *, max_tokens: int = 500, temperature: float = 0.0) -> Optional[Any]:
    """Generate and parse a JSON value from the local model. None on any failure."""
    raw = generate_text(prompt, max_tokens=max_tokens, temperature=temperature)
    if not raw:
        return None
    blob = _extract_json_blob(raw)
    if not blob:
        logger.warning("Local AI returned no parseable JSON: %s", raw[:200])
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError as exc:
        logger.warning("Local AI JSON parse failed (%s): %s", exc, blob[:200])
        return None
