from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from iis.models.enums import PeriodType, SourceTier


class SourceRef(BaseModel):
    file: str
    sheet: str | None = None
    row: int | None = None
    page: int | None = None
    url: str | None = None
    snippet: str | None = None


class FinancialLineItem(BaseModel):
    statement: str
    line_item: str
    line_item_raw: str
    period_end: str | None
    period_label: str
    period_type: PeriodType
    value: float | None
    unit: str = "INR_millions"
    currency: str = "INR"
    source: SourceRef
    source_tier: SourceTier = SourceTier.EXTRACTED


class CompanyInfo(BaseModel):
    name: str
    bse_code: str | None = None
    nse_ticker: str | None = None
    accounting_standard: str | None = None


class ConsensusMetric(BaseModel):
    metric: str
    period: str
    mean: float | None = None
    median: float | None = None
    high: float | None = None
    low: float | None = None
    num_estimates: int | None = None
    actual: float | None = None
    source: SourceRef


class GuidanceRecord(BaseModel):
    metric: str
    fiscal_year: str
    guidance: float | None = None
    guidance_low: float | None = None
    guidance_high: float | None = None
    consensus: float | None = None
    actual: float | None = None
    pct_difference: float | None = None
    source: SourceRef


class PdfChunk(BaseModel):
    file: str
    page: int
    text: str
    section_hint: str | None = None
    section_title: str | None = None
    doc_type: str = "unknown"
    chunk_index: int = 0


class TranscriptQA(BaseModel):
    file: str
    page: int
    question: str
    answer: str
    speaker: str | None = None


class PdfDocument(BaseModel):
    file: str
    doc_type: str
    page_count: int
    chunk_count: int
    qa_pairs: list[TranscriptQA] = Field(default_factory=list)
    sections_found: list[str] = Field(default_factory=list)


class MarketQuote(BaseModel):
    symbol: str
    exchange: str
    price: float
    currency: str = "INR"
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    source: str = "ollama"
    confidence: str = "medium"
    disclaimer: str | None = None
    source_urls: list[str] = Field(default_factory=list)


class AssumptionProposal(BaseModel):
    key: str
    label: str
    value: float | str
    unit: str | None = None
    rationale: str
    source_refs: list[SourceRef] = Field(default_factory=list)
    approved: bool = False
    user_value: float | str | None = None
    user_edited: bool = False
    needs_user_input: bool = False
    review_prompt: str | None = None


class ValuationResult(BaseModel):
    intrinsic_value_per_share: float | None = None
    current_price: float | None = None
    market_price: float | None = None
    market_price_source: str | None = None
    market_price_fetched_at: datetime | None = None
    margin_of_safety_pct: float | None = None
    method: str = "dcf"
    assumptions: dict[str, Any] = Field(default_factory=dict)
    sensitivities: dict[str, Any] = Field(default_factory=dict)


class AgentArtifact(BaseModel):
    agent_id: str
    artifact_type: str
    payload: dict[str, Any]
    confidence: float = 1.0
    provenance: list[str] = Field(default_factory=list)
    source_tier: SourceTier = SourceTier.AGENT
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ExternalFact(BaseModel):
    question: str
    answer: str
    urls: list[str] = Field(default_factory=list)
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)
    user_confirmed: bool = False


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    source_tier: SourceTier
    confidence: str = "high"
    note: str | None = None
    handoff: dict[str, Any] | None = None
    structured: dict[str, Any] | None = None  # rich UI payload (tables, charts)
    evidence: dict[str, Any] | None = None  # query plan, gates, facts used
    proposals: list[dict[str, Any]] = Field(default_factory=list)  # confirm chips for rules/notes/actions
