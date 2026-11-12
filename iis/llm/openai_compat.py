"""OpenAI-compatible chat client (Hive / thehive.ai and similar gateways)."""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from iis.config import settings

_rate_lock = threading.Lock()
_last_call = {"t": 0.0}
_avail_cache: dict[str, tuple[float, bool]] = {}
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _content_chars(content: Any) -> int:
    """Approx text budget; multimodal image parts count as a fixed weight."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                n += len(part.get("text") or "")
            elif part.get("type") == "image_url":
                url = ((part.get("image_url") or {}) if isinstance(part.get("image_url"), dict) else {}).get(
                    "url"
                ) or ""
                # Rough: image tokens dominate; use payload size / 4 as char proxy
                n += max(500, len(url) // 4)
            else:
                n += 80
        return n
    return len(str(content))


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        bits: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                bits.append(part.get("text") or "")
            elif isinstance(part, dict) and part.get("type") == "image_url":
                bits.append("[image]")
        return "\n".join(bits)
    return str(content or "")


def _extract_assistant_text(
    message: dict[str, Any],
    *,
    allow_reasoning_fallback: bool = True,
) -> tuple[str, bool]:
    """Return (text, used_reasoning_fallback).

    DeepSeek-on-Hive often fills ``reasoning_content`` and leaves ``content`` empty
    when max_tokens is too low. Callers that need a finished report should pass
    ``allow_reasoning_fallback=False`` and raise/retry instead of saving CoT.
    """
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content, False
    if isinstance(content, list):
        joined = "\n".join(
            (p.get("text") or "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ).strip()
        if joined:
            return joined, False
    reasoning = message.get("reasoning_content")
    if allow_reasoning_fallback and isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip(), True
    return "", False


def _throttle() -> None:
    gap = float(getattr(settings, "llm_min_interval_sec", 0.25) or 0.0)
    with _rate_lock:
        wait = _last_call["t"] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call["t"] = time.monotonic()


class OpenAICompatClient:
    """Chat + JSON extract against OpenAI-style /chat/completions."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.hive_base_url).rstrip("/")
        self.api_key = (api_key if api_key is not None else settings.hive_api_key) or ""
        self.model = model or settings.hive_model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def is_available(self) -> bool:
        if not self.api_key.strip():
            return False
        cached = _avail_cache.get(self.base_url)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self.base_url}/models", headers=self._headers())
                # Some gateways 401/404 models but still accept chat — key present is enough
                ok = resp.status_code < 500
        except httpx.HTTPError:
            ok = bool(self.api_key.strip())
        _avail_cache[self.base_url] = (time.monotonic(), ok)
        return ok

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        format_json: bool = False,
        temperature: float = 0.2,
        timeout: float | None = None,
        max_tokens: int | None = None,
        allow_reasoning_fallback: bool = True,
        reasoning_effort: str | None = None,
    ) -> str:
        if not self.api_key.strip():
            raise RuntimeError(
                "Hive API key missing — set IIS_HIVE_API_KEY in .env "
                "(project root or export in the shell)."
            )
        # Hard cap — Hive sometimes sits ~300s then returns 500; fail sooner and loop.
        sec = timeout if timeout is not None else float(
            getattr(settings, "llm_chat_timeout_sec", None)
            or getattr(settings, "ollama_chat_timeout_sec", 60.0)
            or 60.0
        )
        # Initiating-coverage write-ups may run up to ~15 min; still hard-cap.
        sec = min(900.0, max(20.0, float(sec)))
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max(64, int(max_tokens))
        # DeepSeek-on-Hive: none|low|high|max — cut hidden CoT on JSON / scoring calls
        effort = reasoning_effort
        if effort is None and format_json:
            effort = str(getattr(settings, "hive_json_reasoning_effort", "") or "").strip() or None
        if effort:
            payload["reasoning_effort"] = effort
        if format_json:
            payload["response_format"] = {"type": "json_object"}
            # Some models ignore response_format — reinforce in last user message
            if messages and messages[-1].get("role") == "user":
                messages = list(messages)
                last = dict(messages[-1])
                c = last.get("content")
                suffix = "\n\nRespond with a single JSON object only."
                if isinstance(c, str):
                    last["content"] = c + suffix
                elif isinstance(c, list):
                    last["content"] = list(c) + [{"type": "text", "text": suffix.strip()}]
                else:
                    last["content"] = suffix.strip()
                messages[-1] = last
                payload["messages"] = messages

        from iis.context.manager import current_agent, log_llm_call
        from iis.llm.gate import LLMAbortError, get_llm_gate

        gate = get_llm_gate()
        prompt_chars = sum(_content_chars(m.get("content")) for m in payload["messages"])
        module = current_agent() or "hive"
        limits = httpx.Timeout(connect=10.0, read=float(sec), write=60.0, pool=10.0)
        # Up to 2 retries (empty-content / 5xx) — quality over saving tokens
        retries = min(2, max(0, int(getattr(settings, "llm_max_retries", 2) or 0)))
        started = time.monotonic()
        last_err: str | None = None
        attempt = 0

        with gate.acquire(module, prompt_chars=prompt_chars) as meta:
            for attempt in range(1, retries + 2):
                gate.check_abort()
                _throttle()
                client: httpx.Client | None = None
                try:
                    client = httpx.Client(timeout=limits)
                    gate.register_client(client)
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                except LLMAbortError:
                    raise
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_err = f"{type(exc).__name__}: {exc}"
                    if gate.aborted():
                        raise LLMAbortError("Analysis aborted by user") from exc
                    if attempt <= retries:
                        time.sleep(min(4.0, 1.2 * attempt))
                        continue
                    break
                finally:
                    gate.clear_client(client)
                    if client is not None:
                        try:
                            client.close()
                        except Exception:
                            pass

                # Hive sometimes returns HTTP 200 with {"status_code":500,...}
                body_preview = (resp.text or "")[:400]
                if '"status_code":500' in body_preview or '"status_code": 500' in body_preview:
                    last_err = f"Unexpected Hive response: {body_preview[:300]}"
                    if attempt <= retries:
                        time.sleep(min(4.0, 1.2 * attempt))
                        continue
                    break
                if resp.status_code in _RETRY_STATUS and attempt <= retries:
                    last_err = f"HTTP {resp.status_code}"
                    time.sleep(min(6.0, 1.5 * attempt))
                    continue
                if resp.status_code >= 400:
                    last_err = f"Hive chat HTTP {resp.status_code}: {body_preview}"
                    break
                try:
                    data = resp.json()
                    msg = data["choices"][0]["message"]
                    if not isinstance(msg, dict):
                        raise TypeError("message not a dict")
                    content, used_reasoning = _extract_assistant_text(
                        msg, allow_reasoning_fallback=allow_reasoning_fallback
                    )
                except (KeyError, IndexError, TypeError, ValueError):
                    last_err = f"Unexpected Hive response: {body_preview[:300]}"
                    break

                # Empty visible content: DeepSeek often burns the budget on hidden CoT.
                # Retry with reasoning off + larger max_tokens (quality first).
                if not (content or "").strip():
                    cur_max = int(payload.get("max_tokens") or 0)
                    if attempt <= retries:
                        payload["reasoning_effort"] = "none"
                        payload["max_tokens"] = min(
                            100000,
                            max(cur_max * 2 if cur_max else 16000, 16000),
                        )
                        last_err = (
                            "empty content (reasoning-only); "
                            f"retry with reasoning=none max_tokens={payload['max_tokens']}"
                        )
                        time.sleep(0.5)
                        continue
                    # Last resort: use reasoning_content if it looks like a finished report
                    reasoning = msg.get("reasoning_content") if isinstance(msg, dict) else None
                    if isinstance(reasoning, str) and len(reasoning.strip()) > 400:
                        content = reasoning.strip()
                        used_reasoning = True
                    else:
                        last_err = (
                            "Hive returned empty assistant content "
                            "(model used tokens on hidden reasoning only)"
                        )
                        break
                if not (content or "").strip():
                    last_err = (
                        "Hive returned empty assistant content "
                        "(model used tokens on hidden reasoning only)"
                    )
                    break

                usage = data.get("usage") if isinstance(data, dict) else None
                pt = ct = None
                est = True
                if isinstance(usage, dict):
                    try:
                        pt = int(usage.get("prompt_tokens") or 0) or None
                        ct = int(usage.get("completion_tokens") or 0) or None
                        if pt is not None or ct is not None:
                            est = False
                    except (TypeError, ValueError):
                        pass
                latency = time.monotonic() - started
                gate.record_result(
                    ok=True,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    prompt_chars=prompt_chars,
                    response_chars=len(content or ""),
                    latency_sec=latency,
                    estimated=est,
                )
                log_llm_call(
                    provider="hive",
                    model=self.model,
                    messages=payload["messages"],
                    response=content,
                    latency_sec=latency,
                    ok=True,
                    attempts=attempt,
                )
                _ = meta  # call/module counters already on gate
                _ = used_reasoning
                return content

            latency = time.monotonic() - started
            gate.record_result(
                ok=False,
                prompt_chars=prompt_chars,
                response_chars=0,
                latency_sec=latency,
                error=last_err,
                estimated=True,
            )
            log_llm_call(
                provider="hive",
                model=self.model,
                messages=payload["messages"],
                response=None,
                latency_sec=latency,
                ok=False,
                error=last_err,
                attempts=attempt or 1,
            )
            raise RuntimeError(last_err or "Hive chat failed")

    def extract_json(
        self,
        prompt: str,
        system: str = "",
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        raw = self.chat(
            messages,
            format_json=True,
            temperature=0.1,
            timeout=timeout if timeout is not None else 90.0,
        )
        text = (raw or "").strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            import re

            m = re.search(r"\{[\s\S]*\}", text)
            if not m:
                raise
            return json.loads(m.group(0))
