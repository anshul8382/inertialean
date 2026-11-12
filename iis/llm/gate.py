"""Limit concurrent LLM HTTP calls + track tokens / abort for Progress UI.

Default allows a small pool of parallel Hive requests (independent sections).
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

ProgressFn = Callable[[dict[str, Any]], None]


class LLMAbortError(RuntimeError):
    """Raised when the user force-aborts an analysis job mid-LLM."""


def _max_parallel() -> int:
    try:
        from iis.config import settings

        n = int(getattr(settings, "llm_max_parallel", 3) or 3)
    except Exception:
        n = 3
    return max(1, min(8, n))


class LLMGate:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(_max_parallel())
        self._parallel = _max_parallel()
        self._in_flight = 0
        self._abort = threading.Event()
        self._job_id: str | None = None
        self._progress: ProgressFn | None = None
        self._client: Any = None
        self.reset_stats()

    def _ensure_slots(self) -> None:
        """Rebuild semaphore if config parallel count changed (e.g. tests)."""
        n = _max_parallel()
        if n == self._parallel:
            return
        # Only shrink/grow when idle
        if self._in_flight:
            return
        self._slots = threading.BoundedSemaphore(n)
        self._parallel = n

    def reset_stats(self) -> None:
        with self._lock:
            self.calls_total = 0
            self.calls_ok = 0
            self.calls_fail = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.tokens_estimated = False
            self.by_module: dict[str, int] = {}
            self.current_module: str | None = None
            self.current_started_at: float | None = None
            self.last_prompt_chars = 0
            self.last_latency_sec = 0.0
            self.last_error: str | None = None
            self._in_flight = 0

    def bind(self, job_id: str, progress: ProgressFn | None = None) -> None:
        with self._lock:
            self._job_id = job_id
            self._progress = progress
            self._abort.clear()
            self.reset_stats()

    def unbind(self, job_id: str | None = None) -> None:
        with self._lock:
            if job_id is None or self._job_id == job_id:
                self._job_id = None
                self._progress = None
                self._abort.clear()

    def request_abort(self, job_id: str | None = None) -> bool:
        with self._lock:
            if job_id and self._job_id and job_id != self._job_id:
                return False
            self._abort.set()
            client = self._client
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        self._notify()
        return True

    def aborted(self) -> bool:
        return self._abort.is_set()

    def check_abort(self) -> None:
        if self._abort.is_set():
            raise LLMAbortError("Analysis aborted by user")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = None
            if self.current_started_at is not None:
                elapsed = max(0, int(time.monotonic() - self.current_started_at))
            return {
                "llm_calls_total": self.calls_total,
                "llm_calls_ok": self.calls_ok,
                "llm_calls_fail": self.calls_fail,
                "llm_prompt_tokens": self.prompt_tokens,
                "llm_completion_tokens": self.completion_tokens,
                "llm_tokens_total": self.prompt_tokens + self.completion_tokens,
                "llm_tokens_estimated": self.tokens_estimated,
                "llm_by_module": dict(self.by_module),
                "llm_current_module": self.current_module,
                "llm_current_elapsed_sec": elapsed,
                "llm_last_prompt_chars": self.last_prompt_chars,
                "llm_last_latency_sec": self.last_latency_sec,
                "llm_last_error": self.last_error,
                "llm_aborted": self._abort.is_set(),
                "llm_in_flight": self._in_flight,
                "llm_max_parallel": self._parallel,
            }

    def _notify(self) -> None:
        fn = self._progress
        if not fn:
            return
        try:
            fn(self.snapshot())
        except Exception:
            pass

    @contextmanager
    def acquire(self, module: str, *, prompt_chars: int = 0):
        """Hold one of N parallel HTTP slots (N = IIS_LLM_MAX_PARALLEL)."""
        self.check_abort()
        with self._lock:
            self._ensure_slots()
        acquired = self._slots.acquire(timeout=0.5)
        while not acquired:
            self.check_abort()
            acquired = self._slots.acquire(timeout=0.5)
        with self._lock:
            self._in_flight += 1
            self.current_module = module or "llm"
            self.current_started_at = time.monotonic()
            self.last_prompt_chars = int(prompt_chars or 0)
            self.last_error = None
            self.by_module[self.current_module] = self.by_module.get(self.current_module, 0) + 1
            self.calls_total += 1
            call_n = self.calls_total
            mod_n = self.by_module[self.current_module]
            in_flight = self._in_flight
        self._notify()
        try:
            self.check_abort()
            yield {
                "call_n": call_n,
                "module_n": mod_n,
                "module": self.current_module,
                "in_flight": in_flight,
            }
        finally:
            with self._lock:
                self._in_flight = max(0, self._in_flight - 1)
                if self._in_flight == 0:
                    self.current_module = None
                    self.current_started_at = None
            try:
                self._slots.release()
            except ValueError:
                pass
            self._notify()

    def register_client(self, client: Any) -> None:
        with self._lock:
            self._client = client

    def clear_client(self, client: Any | None = None) -> None:
        with self._lock:
            if client is None or self._client is client:
                self._client = None

    def record_result(
        self,
        *,
        ok: bool,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        prompt_chars: int = 0,
        response_chars: int = 0,
        latency_sec: float = 0.0,
        error: str | None = None,
        estimated: bool = False,
    ) -> None:
        # Prefer API usage; else ~4 chars/token
        pt = prompt_tokens
        ct = completion_tokens
        est = estimated
        if pt is None:
            pt = max(1, (prompt_chars + 3) // 4) if prompt_chars else 0
            est = True
        if ct is None:
            ct = max(0, (response_chars + 3) // 4) if response_chars else 0
            if response_chars:
                est = True
        with self._lock:
            if ok:
                self.calls_ok += 1
            else:
                self.calls_fail += 1
            self.prompt_tokens += int(pt or 0)
            self.completion_tokens += int(ct or 0)
            if est:
                self.tokens_estimated = True
            self.last_latency_sec = float(latency_sec or 0)
            self.last_error = (error or None) and str(error)[:200]
        self._notify()


_gate = LLMGate()


def get_llm_gate() -> LLMGate:
    return _gate


def estimate_tokens(text: str | None) -> int:
    return max(0, (len(text or "") + 3) // 4)
