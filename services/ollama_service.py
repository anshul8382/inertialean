"""
Ollama client — DISABLED by default in Inertia2026-lean.

Local LLM (Ollama) is not part of the clean VPS deploy. Callers already handle
OllamaUnavailableError / local_ai_available() == False with deterministic fallbacks.

Set ENABLE_AI_SERVICES=true AND OLLAMA_ENABLED=true to re-enable (not recommended on VPS).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

PREFERRED_MODELS = ["llama3.1:8b", "llama3.2:3b", "mistral:7b", "tinyllama:1.1b"]


class OllamaUnavailableError(Exception):
    pass


def ollama_enabled() -> bool:
    """Explicit opt-in; default off in lean builds."""
    return os.environ.get("OLLAMA_ENABLED", "").lower() in ("true", "1", "on", "yes")


def ollama_base_url() -> str:
    try:
        from flask import current_app

        url = (current_app.config.get("OLLAMA_BASE_URL") or "").strip()
        if url:
            return url.rstrip("/")
    except RuntimeError:
        pass
    return (os.environ.get("OLLAMA_BASE_URL") or "").rstrip("/")


def ai_services_enabled() -> bool:
    try:
        from flask import current_app

        return bool(current_app.config.get("ENABLE_AI_SERVICES"))
    except RuntimeError:
        return os.environ.get("ENABLE_AI_SERVICES", "").lower() in ("true", "1", "on", "yes")


def require_ai_enabled() -> None:
    if not ai_services_enabled():
        raise OllamaUnavailableError(
            "AI services are disabled (ENABLE_AI_SERVICES is not true)."
        )
    if not ollama_enabled():
        raise OllamaUnavailableError(
            "Ollama is disabled in this build (set OLLAMA_ENABLED=true only if a remote LLM host exists)."
        )


class OllamaService:
    def __init__(self) -> None:
        self._model: Optional[str] = None

    def reset_model_cache(self) -> None:
        self._model = None

    def get_model(self) -> str:
        require_ai_enabled()
        if not ollama_base_url():
            raise OllamaUnavailableError("OLLAMA_BASE_URL is not configured.")
        # Lazy import so requests is unused when Ollama is off
        import requests

        if self._model:
            return self._model
        base = ollama_base_url()
        try:
            resp = requests.get(f"{base}/api/tags", timeout=5)
            if resp.status_code == 200:
                available = [m["name"] for m in resp.json().get("models", [])]
                for preferred in PREFERRED_MODELS:
                    for avail in available:
                        if avail.startswith(preferred.split(":")[0]):
                            self._model = avail
                            return self._model
                if available:
                    self._model = available[0]
                    return self._model
        except Exception as exc:
            logger.warning("Ollama tags check failed (%s): %s", base, exc)
        raise OllamaUnavailableError(f"Ollama is not reachable at {base}.")

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
        allow_when_ai_disabled: bool = False,
    ) -> str:
        if not allow_when_ai_disabled:
            require_ai_enabled()
        else:
            if not ollama_enabled() or not ollama_base_url():
                raise OllamaUnavailableError("Ollama disabled.")
        import requests

        model = self.get_model()
        base = ollama_base_url()
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        env_timeout = (os.environ.get("OLLAMA_REQUEST_TIMEOUT") or "").strip()
        timeout = max(30, int(env_timeout)) if env_timeout.isdigit() else 120
        resp = requests.post(f"{base}/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        return (resp.json().get("response") or "").strip()

    def ping(self) -> dict:
        if not ai_services_enabled() or not ollama_enabled() or not ollama_base_url():
            return {
                "ok": False,
                "base_url": ollama_base_url() or "(disabled)",
                "error": "Ollama disabled in lean build",
            }
        import requests

        base = ollama_base_url()
        try:
            resp = requests.get(f"{base}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return {"ok": True, "base_url": base, "models": models}
        except Exception as exc:
            return {"ok": False, "base_url": base, "error": str(exc)}


ollama_service = OllamaService()
