from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from iis.models.enums import GateId


class EvidenceFact(BaseModel):
    """Fact-level review item for Gate 2 and chat evidence packs."""

    id: str
    claim: str
    evidence: str
    source_file: str
    source_page: int | None = None
    quote: str | None = None
    agent_id: str = "system"
    gate_id: GateId | None = GateId.TEXT_INFERENCES
    status: str = "pending"  # pending | approved | rejected
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AnalysisJob(BaseModel):
    id: str
    session_id: str
    status: str = "pending"  # pending | running | complete | failed
    progress: float = 0.0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[str] = Field(default_factory=list)
    current_step: str | None = None
    # Fine-grained submodule inside current_step (e.g. business_overview, llm_analyst)
    phase: str | None = None
    message: str | None = None
    excerpt: str | None = None
    reading: list[dict[str, Any]] = Field(default_factory=list)
    step_started_at: datetime | None = None
    # Seconds on current_step (server-computed UTC) — UI should prefer this over Date.parse
    step_elapsed_sec: int | None = None
    # Live LLM accounting (serial gate — one HTTP call at a time)
    llm_calls_total: int = 0
    llm_calls_ok: int = 0
    llm_calls_fail: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_tokens_total: int = 0
    llm_tokens_estimated: bool = False
    llm_by_module: dict[str, int] = Field(default_factory=dict)
    llm_current_module: str | None = None
    llm_current_elapsed_sec: int | None = None
    llm_last_prompt_chars: int | None = None
    llm_last_latency_sec: float | None = None
    llm_last_error: str | None = None
    llm_aborted: bool = False


class QueryPlanStep(BaseModel):
    query_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None


class QueryPlan(BaseModel):
    query_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    steps: list[QueryPlanStep] = Field(default_factory=list)
    confidence: str = "medium"
    reason: str | None = None

    @property
    def is_multi_step(self) -> bool:
        return len(self.steps) > 1
