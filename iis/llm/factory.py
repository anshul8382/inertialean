"""Select LLM backend: Ollama (local) or Hive (cloud OpenAI-compatible)."""
from __future__ import annotations

from typing import Any, Protocol


class LLMClient(Protocol):
    model: str

    def is_available(self) -> bool: ...

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_json: bool = False,
        temperature: float = 0.2,
        timeout: float | None = None,
    ) -> str: ...

    def extract_json(
        self,
        prompt: str,
        system: str = "",
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]: ...


def get_llm_client(*, finance: bool = False) -> LLMClient:
    """Return the configured chat client (Hive or Ollama)."""
    from iis.config import settings

    provider = (getattr(settings, "llm_provider", None) or "ollama").strip().lower()
    if provider in ("hive", "thehive", "thehive.ai"):
        from iis.llm.openai_compat import OpenAICompatClient

        model = (
            settings.hive_finance_model
            if finance and getattr(settings, "hive_finance_model", None)
            else settings.hive_model
        )
        return OpenAICompatClient(model=model)

    if finance:
        from iis.llm.finance_client import FinanceOllamaClient

        return FinanceOllamaClient()

    from iis.llm.ollama_client import OllamaClient

    return OllamaClient()


def llm_provider_name() -> str:
    from iis.config import settings

    return (getattr(settings, "llm_provider", None) or "ollama").strip().lower()
