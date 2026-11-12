from __future__ import annotations

from pathlib import Path

from iis.agents.base import BaseAgent
from iis.config import settings
from iis.finance.flags import compute_data_flags, flags_to_payload
from iis.library.ingest import ingest_file_to_library
from iis.library.store import get_company_library_store
from iis.models.artifacts import AgentArtifact
from iis.models.enums import EventType, SessionStatus, SourceTier
from iis.models.session import ResearchSession
from iis.review.evidence import add_transcript_evidence_facts
from iis.search.embeddings import EmbeddingService


class DataExtractorAgent(BaseAgent):
    agent_id = "data_extractor"

    def run(self, session: ResearchSession) -> ResearchSession:
        session.status = SessionStatus.INGESTING
        store = get_company_library_store()

        # Pre-load company library if we already know the company
        store.sync_to_session(session)

        ingested = 0
        skipped = 0

        # Process Capital IQ Excel before PDFs so company identity is established
        paths = sorted(
            [Path(p) for p in session.uploaded_files if Path(p).exists()],
            key=lambda p: (0 if p.suffix.lower() in (".xls", ".xlsx") else 1, p.name.lower()),
        )

        for path in paths:
            try:
                _, status = ingest_file_to_library(session, path, store)
            except Exception as exc:
                session.add_event(
                    EventType.PARSE,
                    agent=self.agent_id,
                    details={"file": path.name, "error": str(exc)},
                )
                continue

            if status == "skipped_duplicate":
                skipped += 1
                session.add_event(
                    EventType.PARSE,
                    agent=self.agent_id,
                    details={"file": path.name, "status": "skipped_duplicate"},
                )
            elif status == "ingested":
                ingested += 1
                session.add_event(
                    EventType.PARSE,
                    agent=self.agent_id,
                    details={"file": path.name, "status": "ingested"},
                )
            elif status == "no_company":
                session.add_event(
                    EventType.PARSE,
                    agent=self.agent_id,
                    details={
                        "file": path.name,
                        "status": "pending_company",
                        "note": "Upload Capital IQ Financials first to identify company, or enter company name",
                    },
                )

        session.status = SessionStatus.AWAITING_REVIEW
        session.add_event(
            EventType.PARSE,
            agent=self.agent_id,
            details={"status": "complete", "ingested": ingested, "skipped_duplicate": skipped},
        )

        # Rebuild transcript evidence (no duplicates on re-ingest)
        session.evidence_facts = [f for f in session.evidence_facts if f.agent_id != "data_extractor"]
        add_transcript_evidence_facts(session)

        if settings.enable_semantic_search and session.pdf_chunks:
            embedder = EmbeddingService()
            indexed = embedder.index_session_chunks(session)
            session.add_event(
                EventType.PARSE,
                agent=self.agent_id,
                details={"embeddings_indexed": indexed},
            )

        if session.financial_line_items or session.guidance_records:
            flag_list = compute_data_flags(session)
            session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "data_flags"]
            session.agent_artifacts.append(
                AgentArtifact(
                    agent_id=self.agent_id,
                    artifact_type="data_flags",
                    payload=flags_to_payload(flag_list),
                    provenance=["financial_line_items", "guidance_records"],
                    source_tier=SourceTier.EXTRACTED,
                )
            )
        return session
