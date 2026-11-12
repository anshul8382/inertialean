from enum import Enum


class SourceTier(str, Enum):
    APPROVED = "approved"
    EXTRACTED = "extracted"
    AGENT = "agent"
    SESSION = "session"
    EXTERNAL = "external"


class PeriodType(str, Enum):
    ACTUAL = "actual"
    ESTIMATE = "estimate"
    PRESS_RELEASE = "press_release"
    RECLASSIFIED = "reclassified"
    UNKNOWN = "unknown"


class GateId(str, Enum):
    EXTRACTED_FINANCIALS = "extracted_financials"
    TEXT_INFERENCES = "text_inferences"
    NORMALIZATION = "normalization"
    VALUATION_ASSUMPTIONS = "valuation_assumptions"


class GateStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class SessionStatus(str, Enum):
    CREATED = "created"
    INGESTING = "ingesting"
    AWAITING_REVIEW = "awaiting_review"
    ANALYZING = "analyzing"
    READY = "ready"
    COMPLETE = "complete"


class EventType(str, Enum):
    UPLOAD = "upload"
    PARSE = "parse"
    INFERENCE = "inference"
    PROGRESS = "progress"
    ASSUMPTION_PROPOSED = "assumption_proposed"
    ASSUMPTION_APPROVED = "assumption_approved"
    GATE_APPROVED = "gate_approved"
    GATE_REJECTED = "gate_rejected"
    CHAT_QA = "chat_qa"
    VALUATION_RUN = "valuation_run"
    NOTE_GENERATED = "note_generated"
    EXTERNAL_FACT = "external_fact"
    MARKET_PRICE_FETCH = "market_price_fetch"


class PdfDocType(str, Enum):
    ANNUAL_REPORT = "annual_report"
    TRANSCRIPT = "transcript"
    EARNINGS_PRESENTATION = "earnings_presentation"
    BROKERAGE_REPORT = "brokerage_report"
    UNKNOWN = "unknown"
