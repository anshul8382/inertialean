"""
Ollama / local LLM — removed from Inertia2026-lean (KVM).

There is no Ollama host on this deploy. This module is a permanent stub so
legacy callers fail closed immediately without network I/O or timeouts.

Do not re-enable via env on KVM; restore from git history only if a future
host actually runs Ollama.
"""

from __future__ import annotations

from typing import Optional


class OllamaUnavailableError(Exception):
    """Raised when any local-LLM call is attempted on Lean/KVM."""


def ollama_enabled() -> bool:
    return False


def ollama_base_url() -> str:
    return ""


def ai_services_enabled() -> bool:
    return False


def require_ai_enabled() -> None:
    raise OllamaUnavailableError(
        "Local AI (Ollama) is not part of this Lean/KVM build."
    )


class OllamaService:
    def __init__(self) -> None:
        self._model: Optional[str] = None

    def reset_model_cache(self) -> None:
        self._model = None

    def get_model(self) -> str:
        require_ai_enabled()
        raise OllamaUnavailableError("Ollama removed from Lean/KVM.")

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 1200,
        temperature: float = 0.1,
        allow_when_ai_disabled: bool = False,
    ) -> str:
        raise OllamaUnavailableError(
            "Local AI (Ollama) is not part of this Lean/KVM build."
        )

    def ping(self) -> dict:
        return {
            "ok": False,
            "base_url": "(removed)",
            "error": "Ollama removed from Lean/KVM build",
            "models": [],
        }


ollama_service = OllamaService()
