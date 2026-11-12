"""Add evidence facts from agent inferences for Gate 2 review."""

from __future__ import annotations

from uuid import uuid4

from iis.models.enums import GateId
from iis.models.evidence import EvidenceFact
from iis.models.session import ResearchSession


def add_ar_evidence_facts(session: ResearchSession, ar_files: list[str], all_analyses: dict) -> None:
    """Create reviewable facts from annual report section extractions."""
    for filename in ar_files:
        file_data = all_analyses.get(filename, {})
        for section_name, analysis in file_data.get("sections", {}).items():
            pages = analysis.get("pages") or []
            page = pages[0] if pages else None
            summary = analysis.get("summary") or ""
            if summary:
                session.evidence_facts.append(
                    EvidenceFact(
                        id=str(uuid4()),
                        claim=f"{section_name} summary",
                        evidence=summary,
                        source_file=filename,
                        source_page=page,
                        quote=summary[:300],
                        agent_id="annual_report_reader",
                        gate_id=GateId.TEXT_INFERENCES,
                    )
                )
            for fact in (analysis.get("quantitative_facts") or [])[:5]:
                session.evidence_facts.append(
                    EvidenceFact(
                        id=str(uuid4()),
                        claim=str(fact)[:120],
                        evidence=str(fact),
                        source_file=filename,
                        source_page=page,
                        quote=str(fact)[:300],
                        agent_id="annual_report_reader",
                        gate_id=GateId.TEXT_INFERENCES,
                    )
                )


def add_transcript_evidence_facts(session: ResearchSession) -> None:
    """Create reviewable facts from extracted transcript Q&A pairs."""
    for doc in session.pdf_documents:
        if doc.doc_type != "transcript":
            continue
        for qa in doc.qa_pairs[:20]:
            session.evidence_facts.append(
                EvidenceFact(
                    id=str(uuid4()),
                    claim=qa.question[:200],
                    evidence=qa.answer[:500],
                    source_file=doc.file,
                    source_page=qa.page,
                    quote=qa.answer[:300],
                    agent_id="data_extractor",
                    gate_id=GateId.TEXT_INFERENCES,
                )
            )
