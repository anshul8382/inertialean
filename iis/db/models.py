from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ResearchSessionRow(Base):
    __tablename__ = "research_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="created")
    company_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    market_quote_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    valuation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gates_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    assumptions_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    uploaded_files_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    external_facts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    chat_history_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    evidence_facts_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FinancialLineItemRow(Base):
    __tablename__ = "financial_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)


class PdfChunkRow(Base):
    __tablename__ = "pdf_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    chunk_id: Mapped[str] = mapped_column(String(128), index=True)
    file: Mapped[str] = mapped_column(String(512))
    page: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    section_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    doc_type: Mapped[str] = mapped_column(String(32), default="unknown")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)


class ChunkEmbeddingRow(Base):
    __tablename__ = "chunk_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    chunk_id: Mapped[str] = mapped_column(String(128), index=True)
    model: Mapped[str] = mapped_column(String(64))
    embedding_json: Mapped[list] = mapped_column(JSON)


class PdfDocumentRow(Base):
    __tablename__ = "pdf_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)


class AgentArtifactRow(Base):
    __tablename__ = "agent_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)


class ConsensusMetricRow(Base):
    __tablename__ = "consensus_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)


class GuidanceRecordRow(Base):
    __tablename__ = "guidance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)


class ResearchEventRow(Base):
    __tablename__ = "research_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)


class AnalysisJobRow(Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    steps_json: Mapped[list | None] = mapped_column(JSON, nullable=True)


Index("ix_pdf_chunks_session_file", PdfChunkRow.session_id, PdfChunkRow.file)
