from __future__ import annotations

import json
from typing import Any

import httpx

from iis.config import settings


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format_json: bool = False,
        temperature: float = 0.2,
        timeout: float | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if format_json:
            payload["format"] = "json"

        # Default: short timeout for chat polish; callers can raise for heavy jobs.
        # Use explicit Timeout — a bare float can fail to abort hung llama-server reads.
        import time

        from iis.context.manager import log_llm_call

        sec = timeout if timeout is not None else float(getattr(settings, "ollama_chat_timeout_sec", 20.0) or 20.0)
        limits = httpx.Timeout(connect=5.0, read=float(sec), write=30.0, pool=5.0)
        started = time.monotonic()
        try:
            with httpx.Client(timeout=limits) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["message"]["content"]
            log_llm_call(
                provider="ollama",
                model=self.model,
                messages=messages,
                response=content,
                latency_sec=time.monotonic() - started,
                ok=True,
            )
            return content
        except Exception as exc:
            log_llm_call(
                provider="ollama",
                model=self.model,
                messages=messages,
                response=None,
                latency_sec=time.monotonic() - started,
                ok=False,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            raise

    def extract_json(self, prompt: str, system: str = "", *, timeout: float | None = None) -> dict[str, Any]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # JSON extraction for agents may need longer; default 60s unless set
        raw = self.chat(messages, format_json=True, timeout=timeout if timeout is not None else 60.0)
        return json.loads(raw)
