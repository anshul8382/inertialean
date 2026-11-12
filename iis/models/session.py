from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from iis.models.artifacts import (
    AgentArtifact,
    AssumptionProposal,
    ChatResponse,
    CompanyInfo,
    ConsensusMetric,
    ExternalFact,
    FinancialLineItem,
    GuidanceRecord,
    MarketQuote,
    PdfChunk,
    PdfDocument,
    ValuationResult,
)
from iis.models.enums import EventType, GateId, GateStatus, SessionStatus
from iis.models.evidence import EvidenceFact


class ResearchEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: EventType
    agent: str | None = None
    payload_ref: str | None = None
    user_action: str | None = None
    user_comment: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class GateState(BaseModel):
    gate_id: GateId
    status: GateStatus = GateStatus.PENDING
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    reviewed_at: datetime | None = None
    reviewer_comment: str | None = None


class ResearchSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    owner_id: str | None = None
    company: CompanyInfo | None = None
    company_key: str | None = None  # persistent library key (bse-500114, etc.)
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    uploaded_files: list[str] = Field(default_factory=list)
    # basename → doc_kind when user picked a section for multi-upload
    upload_file_kinds: dict[str, str] = Field(default_factory=dict)
    financial_line_items: list[FinancialLineItem] = Field(default_factory=list)
    consensus_metrics: list[ConsensusMetric] = Field(default_factory=list)
    guidance_records: list[GuidanceRecord] = Field(default_factory=list)
    pdf_chunks: list[PdfChunk] = Field(default_factory=list)
    pdf_documents: list[PdfDocument] = Field(default_factory=list)
    market_quote: MarketQuote | None = None
    agent_artifacts: list[AgentArtifact] = Field(default_factory=list)
    assumptions: list[AssumptionProposal] = Field(default_factory=list)
    valuation: ValuationResult | None = None
    external_facts: list[ExternalFact] = Field(default_factory=list)
    evidence_facts: list[EvidenceFact] = Field(default_factory=list)
    chat_history: list[ChatResponse] = Field(default_factory=list)
    events: list[ResearchEvent] = Field(default_factory=list)
    gates: dict[str, GateState] = Field(default_factory=dict)
    # Latest generated research report (Layer 1 + Layer 2 markdown); edited via chat / regenerate
    research_report_md: str | None = None
    research_report_path: str | None = None
    # Separate Direct-LLM report (document-read path) — own UI tab for A/B compare
    direct_llm_report_md: str | None = None
    direct_llm_report_path: str | None = None

    def init_gates(self) -> None:
        for gate_id in GateId:
            if gate_id.value not in self.gates:
                self.gates[gate_id.value] = GateState(gate_id=gate_id)

    def gate(self, gate_id: GateId) -> GateState:
        self.init_gates()
        return self.gates[gate_id.value]

    def is_gate_approved(self, gate_id: GateId) -> bool:
        return self.gate(gate_id).status == GateStatus.APPROVED

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()

    def add_event(
        self,
        event_type: EventType,
        agent: str | None = None,
        details: dict[str, Any] | None = None,
        user_action: str | None = None,
        user_comment: str | None = None,
    ) -> ResearchEvent:
        event = ResearchEvent(
            event_type=event_type,
            agent=agent,
            details=details or {},
            user_action=user_action,
            user_comment=user_comment,
        )
        self.events.append(event)
        self.touch()
        return event
