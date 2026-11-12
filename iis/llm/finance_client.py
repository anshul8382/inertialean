from __future__ import annotations

from typing import Any

from iis.config import settings
from iis.llm.ollama_client import OllamaClient


def _provider() -> str:
    return (getattr(settings, "llm_provider", None) or "ollama").strip().lower()


class FinanceOllamaClient:
    """Finance LLM client — Hive (cloud) or local Ollama finance model."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._provider = _provider()
        if self._provider in ("hive", "thehive", "thehive.ai"):
            from iis.llm.openai_compat import OpenAICompatClient

            self._impl: Any = OpenAICompatClient(
                model=model
                or getattr(settings, "hive_finance_model", None)
                or settings.hive_model
            )
            self._preferred_model = self._impl.model
            self.base_url = self._impl.base_url
            self.model = self._impl.model
        else:
            self._preferred_model = model or settings.ollama_finance_model
            self._impl = OllamaClient(base_url=base_url, model=self._preferred_model)
            self.base_url = self._impl.base_url
            self.model = self._impl.model

    def is_available(self) -> bool:
        return self._impl.is_available()

    def is_finance_model_available(self) -> bool:
        if self._provider in ("hive", "thehive", "thehive.ai"):
            return self.is_available()
        if not self.is_available():
            return False
        try:
            import httpx

            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    return False
                names = [m.get("name", "") for m in resp.json().get("models", [])]
                target = self._preferred_model
                base = target.split(":")[0]
                return any(n == target or n.startswith(f"{base}:") for n in names)
        except Exception:
            return False

    def active_model(self) -> str:
        if self._provider in ("hive", "thehive", "thehive.ai"):
            return self._preferred_model
        if settings.ollama_finance_fallback and not self.is_finance_model_available():
            return settings.ollama_model
        return self._preferred_model

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_json: bool = False,
        temperature: float = 0.1,
        timeout: float | None = None,
    ) -> str:
        if self._provider in ("hive", "thehive", "thehive.ai"):
            return self._impl.chat(
                messages,
                format_json=format_json,
                temperature=temperature,
                timeout=timeout,
            )
        saved = self._impl.model
        self._impl.model = self.active_model()
        try:
            return self._impl.chat(
                messages,
                format_json=format_json,
                temperature=temperature,
                timeout=timeout,
            )
        finally:
            self._impl.model = saved

    def extract_json(
        self,
        prompt: str,
        system: str = "",
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._provider in ("hive", "thehive", "thehive.ai"):
            return self._impl.extract_json(prompt, system=system, timeout=timeout)
        saved = self._impl.model
        self._impl.model = self.active_model()
        try:
            return self._impl.extract_json(prompt, system=system, timeout=timeout)
        finally:
            self._impl.model = saved
