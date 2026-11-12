from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

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
from iis.models.enums import SessionStatus
from iis.models.evidence import AnalysisJob, EvidenceFact
from iis.models.session import GateState, ResearchEvent, ResearchSession


def chunk_id_for(file: str, page: int, chunk_index: int) -> str:
    return f"{file}::{page}::{chunk_index}"


class SessionRepository:
    """Persist ResearchSession to MySQL/SQLite via SQLAlchemy."""

    def save(self, session: ResearchSession) -> None:
        from iis.db.engine import session_scope
        from iis.db.models import (
            AgentArtifactRow,
            AnalysisJobRow,
            ChunkEmbeddingRow,
            ConsensusMetricRow,
            FinancialLineItemRow,
            GuidanceRecordRow,
            PdfChunkRow,
            PdfDocumentRow,
            ResearchEventRow,
            ResearchSessionRow,
        )

        session.touch()
        with session_scope() as db:
            row = db.get(ResearchSessionRow, session.id)
            if row is None:
                row = ResearchSessionRow(id=session.id)
                db.add(row)

            row.owner_id = session.owner_id
            row.status = session.status.value
            row.company_json = session.company.model_dump(mode="json") if session.company else None
            row.market_quote_json = (
                session.market_quote.model_dump(mode="json") if session.market_quote else None
            )
            row.valuation_json = session.valuation.model_dump(mode="json") if session.valuation else None
            row.gates_json = {k: v.model_dump(mode="json") for k, v in session.gates.items()}
            row.assumptions_json = [a.model_dump(mode="json") for a in session.assumptions]
            row.uploaded_files_json = list(session.uploaded_files)
            row.external_facts_json = [f.model_dump(mode="json") for f in session.external_facts]
            row.chat_history_json = [c.model_dump(mode="json") for c in session.chat_history]
            row.evidence_facts_json = [f.model_dump(mode="json") for f in session.evidence_facts]
            row.created_at = session.created_at
            row.updated_at = session.updated_at

            self._replace_children(db, session)

    def _replace_children(self, db, session: ResearchSession) -> None:
        from iis.db.models import (
            AgentArtifactRow,
            ConsensusMetricRow,
            FinancialLineItemRow,
            GuidanceRecordRow,
            PdfChunkRow,
            PdfDocumentRow,
            ResearchEventRow,
        )

        sid = session.id
        for model, rows in (
            (FinancialLineItemRow, session.financial_line_items),
            (PdfChunkRow, session.pdf_chunks),
            (PdfDocumentRow, session.pdf_documents),
            (AgentArtifactRow, session.agent_artifacts),
            (ConsensusMetricRow, session.consensus_metrics),
            (GuidanceRecordRow, session.guidance_records),
            (ResearchEventRow, session.events),
        ):
            db.query(model).filter(model.session_id == sid).delete()
            if model is PdfChunkRow:
                for chunk in rows:
                    db.add(
                        PdfChunkRow(
                            session_id=sid,
                            chunk_id=chunk_id_for(chunk.file, chunk.page, chunk.chunk_index),
                            file=chunk.file,
                            page=chunk.page,
                            text=chunk.text,
                            section_hint=chunk.section_hint,
                            section_title=chunk.section_title,
                            doc_type=chunk.doc_type,
                            chunk_index=chunk.chunk_index,
                        )
                    )
            elif model is PdfDocumentRow:
                for doc in rows:
                    db.add(PdfDocumentRow(session_id=sid, payload_json=doc.model_dump(mode="json")))
            elif model is ResearchEventRow:
                for event in rows:
                    db.add(ResearchEventRow(session_id=sid, payload_json=event.model_dump(mode="json")))
            else:
                for item in rows:
                    db.add(model(session_id=sid, payload_json=item.model_dump(mode="json")))

    def load(self, session_id: str) -> ResearchSession:
        from iis.db.engine import session_scope
        from iis.db.models import (
            AgentArtifactRow,
            ConsensusMetricRow,
            FinancialLineItemRow,
            GuidanceRecordRow,
            PdfChunkRow,
            PdfDocumentRow,
            ResearchEventRow,
            ResearchSessionRow,
        )

        with session_scope() as db:
            row = db.get(ResearchSessionRow, session_id)
            if row is None:
                raise FileNotFoundError(f"Session {session_id} not found")

            financial_line_items = [
                FinancialLineItem.model_validate(r.payload_json)
                for r in db.query(FinancialLineItemRow).filter(FinancialLineItemRow.session_id == session_id).all()
            ]
            pdf_chunks = [
                PdfChunk(
                    file=r.file,
                    page=r.page,
                    text=r.text,
                    section_hint=r.section_hint,
                    section_title=r.section_title,
                    doc_type=r.doc_type,
                    chunk_index=r.chunk_index,
                )
                for r in db.query(PdfChunkRow).filter(PdfChunkRow.session_id == session_id).all()
            ]
            pdf_documents = [
                PdfDocument.model_validate(r.payload_json)
                for r in db.query(PdfDocumentRow).filter(PdfDocumentRow.session_id == session_id).all()
            ]
            agent_artifacts = [
                AgentArtifact.model_validate(r.payload_json)
                for r in db.query(AgentArtifactRow).filter(AgentArtifactRow.session_id == session_id).all()
            ]
            consensus_metrics = [
                ConsensusMetric.model_validate(r.payload_json)
                for r in db.query(ConsensusMetricRow).filter(ConsensusMetricRow.session_id == session_id).all()
            ]
            guidance_records = [
                GuidanceRecord.model_validate(r.payload_json)
                for r in db.query(GuidanceRecordRow).filter(GuidanceRecordRow.session_id == session_id).all()
            ]
            events = [
                ResearchEvent.model_validate(r.payload_json)
                for r in db.query(ResearchEventRow).filter(ResearchEventRow.session_id == session_id).all()
            ]

            gates: dict[str, GateState] = {}
            if row.gates_json:
                for k, v in row.gates_json.items():
                    gates[k] = GateState.model_validate(v)

            return ResearchSession(
                id=row.id,
                owner_id=row.owner_id,
                company=CompanyInfo.model_validate(row.company_json) if row.company_json else None,
                status=SessionStatus(row.status),
                created_at=row.created_at,
                updated_at=row.updated_at,
                uploaded_files=list(row.uploaded_files_json or []),
                financial_line_items=financial_line_items,
                consensus_metrics=consensus_metrics,
                guidance_records=guidance_records,
                pdf_chunks=pdf_chunks,
                pdf_documents=pdf_documents,
                market_quote=MarketQuote.model_validate(row.market_quote_json) if row.market_quote_json else None,
                agent_artifacts=agent_artifacts,
                assumptions=[AssumptionProposal.model_validate(a) for a in (row.assumptions_json or [])],
                valuation=ValuationResult.model_validate(row.valuation_json) if row.valuation_json else None,
                external_facts=[ExternalFact.model_validate(f) for f in (row.external_facts_json or [])],
                chat_history=[ChatResponse.model_validate(c) for c in (row.chat_history_json or [])],
                evidence_facts=[EvidenceFact.model_validate(f) for f in (row.evidence_facts_json or [])],
                events=events,
                gates=gates,
            )

    def list_session_cards(
        self,
        owner_id: str | None = None,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Light rows for sidebar — no child tables / full session hydrate."""
        from iis.db.engine import session_scope
        from iis.db.models import ResearchSessionRow

        with session_scope() as db:
            q = db.query(ResearchSessionRow).order_by(ResearchSessionRow.updated_at.desc())
            if owner_id is not None:
                q = q.filter(ResearchSessionRow.owner_id == owner_id)
            rows = q.limit(max(1, min(limit, 80))).all()
            cards: list[dict[str, Any]] = []
            for r in rows:
                company = r.company_json if isinstance(r.company_json, dict) else None
                uploaded = r.uploaded_files_json or []
                names = []
                for f in uploaded[:8]:
                    try:
                        from pathlib import Path

                        names.append(Path(str(f)).name)
                    except Exception:
                        names.append(str(f))
                cards.append(
                    {
                        "id": r.id,
                        "owner_id": r.owner_id,
                        "company": company,
                        "company_key": (company or {}).get("company_key") if company else None,
                        "status": r.status,
                        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        "uploaded_files": names,
                        "line_items": 0,
                        "pdf_chunks": 0,
                        "pdf_documents": 0,
                        "has_research_report": False,
                        "has_direct_llm_report": False,
                    }
                )
            return cards

    def list_sessions(self, owner_id: str | None = None) -> list[ResearchSession]:
        from iis.db.engine import session_scope
        from iis.db.models import ResearchSessionRow

        with session_scope() as db:
            q = db.query(ResearchSessionRow).order_by(ResearchSessionRow.updated_at.desc())
            if owner_id is not None:
                q = q.filter(ResearchSessionRow.owner_id == owner_id)
            ids = [r.id for r in q.limit(20).all()]
        return [self.load(sid) for sid in ids]

    def delete(self, session_id: str) -> None:
        from iis.db.engine import session_scope
        from iis.db.models import (
            AgentArtifactRow,
            AnalysisJobRow,
            ChunkEmbeddingRow,
            ConsensusMetricRow,
            FinancialLineItemRow,
            GuidanceRecordRow,
            PdfChunkRow,
            PdfDocumentRow,
            ResearchEventRow,
            ResearchSessionRow,
        )

        with session_scope() as db:
            for model in (
                FinancialLineItemRow,
                PdfChunkRow,
                ChunkEmbeddingRow,
                PdfDocumentRow,
                AgentArtifactRow,
                ConsensusMetricRow,
                GuidanceRecordRow,
                ResearchEventRow,
                AnalysisJobRow,
            ):
                db.query(model).filter(model.session_id == session_id).delete()
            row = db.get(ResearchSessionRow, session_id)
            if row:
                db.delete(row)

    # --- Embeddings ---

    def save_embeddings(self, session_id: str, chunk_id: str, model: str, embedding: list[float]) -> None:
        from iis.db.engine import session_scope
        from iis.db.models import ChunkEmbeddingRow

        with session_scope() as db:
            existing = (
                db.query(ChunkEmbeddingRow)
                .filter(
                    ChunkEmbeddingRow.session_id == session_id,
                    ChunkEmbeddingRow.chunk_id == chunk_id,
                )
                .first()
            )
            if existing:
                existing.embedding_json = embedding
                existing.model = model
            else:
                db.add(
                    ChunkEmbeddingRow(
                        session_id=session_id,
                        chunk_id=chunk_id,
                        model=model,
                        embedding_json=embedding,
                    )
                )

    def load_embeddings(self, session_id: str) -> dict[str, list[float]]:
        from iis.db.engine import session_scope
        from iis.db.models import ChunkEmbeddingRow

        with session_scope() as db:
            rows = db.query(ChunkEmbeddingRow).filter(ChunkEmbeddingRow.session_id == session_id).all()
            return {r.chunk_id: list(r.embedding_json) for r in rows}

    # --- Analysis jobs ---

    def create_job(self, session_id: str, steps: list[str] | None = None) -> AnalysisJob:
        from iis.db.engine import session_scope
        from iis.db.models import AnalysisJobRow

        job = AnalysisJob(id=str(uuid4()), session_id=session_id, steps=steps or [])
        with session_scope() as db:
            db.add(
                AnalysisJobRow(
                    id=job.id,
                    session_id=session_id,
                    status=job.status,
                    progress=job.progress,
                    steps_json=job.steps,
                )
            )
        return job

    def update_job(self, job: AnalysisJob) -> None:
        from iis.db.engine import session_scope
        from iis.db.models import AnalysisJobRow

        with session_scope() as db:
            row = db.get(AnalysisJobRow, job.id)
            if row is None:
                return
            row.status = job.status
            row.progress = job.progress
            row.error = job.error
            row.started_at = job.started_at
            row.finished_at = job.finished_at
            row.steps_json = job.steps

    def get_job(self, job_id: str) -> AnalysisJob | None:
        from iis.db.engine import session_scope
        from iis.db.models import AnalysisJobRow

        with session_scope() as db:
            row = db.get(AnalysisJobRow, job_id)
            if row is None:
                return None
            return AnalysisJob(
                id=row.id,
                session_id=row.session_id,
                status=row.status,
                progress=row.progress,
                error=row.error,
                started_at=row.started_at,
                finished_at=row.finished_at,
                steps=list(row.steps_json or []),
            )

    def list_jobs(self, session_id: str) -> list[AnalysisJob]:
        from iis.db.engine import session_scope
        from iis.db.models import AnalysisJobRow

        with session_scope() as db:
            rows = (
                db.query(AnalysisJobRow)
                .filter(AnalysisJobRow.session_id == session_id)
                .order_by(AnalysisJobRow.started_at.desc())
                .all()
            )
            return [
                AnalysisJob(
                    id=r.id,
                    session_id=r.session_id,
                    status=r.status,
                    progress=r.progress,
                    error=r.error,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                    steps=list(r.steps_json or []),
                )
                for r in rows
            ]
