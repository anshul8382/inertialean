from __future__ import annotations

from iis.agents.annual_report_reader import get_annual_report_context
from iis.agents.base import BaseAgent
from iis.config import settings
from iis.ingest.pdf import search_pdf_chunks
from iis.llm.factory import get_llm_client
from iis.models.artifacts import AgentArtifact
from iis.models.enums import EventType, PdfDocType, SourceTier
from iis.models.session import ResearchSession

_FORCE_QUERIES = {
    "competitive_rivalry": "competition competitor rivalry market share industry peers",
    "supplier_power": "supplier raw material input cost vendor dependency",
    "buyer_power": "customer concentration buyer bargaining pricing power OEM",
    "threat_of_substitutes": "substitute alternative technology displacement",
    "threat_of_new_entrants": "barrier entry new entrant capacity license regulation",
}


class PortersAgent(BaseAgent):
    agent_id = "porters"

    def __init__(self, llm=None) -> None:
        self.llm = llm or get_llm_client(finance=True)

    def run(self, session: ResearchSession) -> ResearchSession:
        # Always search all relevant ingested PDFs (AR / concall / presentation)
        chunks = search_pdf_chunks(
            session.pdf_chunks,
            "competition industry competitor market share rivalry",
            limit=8,
            doc_types=[
                PdfDocType.ANNUAL_REPORT.value,
                PdfDocType.TRANSCRIPT.value,
                PdfDocType.EARNINGS_PRESENTATION.value,
            ],
            section_hints=["Competition", "Industry", "MD&A", "Q&A", "Risk Factors"],
        )
        if not chunks:
            chunks = search_pdf_chunks(
                session.pdf_chunks,
                "competition industry competitor market",
                limit=6,
            )

        forces = self._python_forces(session, chunks)

        use_llm = bool(getattr(settings, "analyze_llm_enrichment", False)) and self.llm.is_available()
        if use_llm and chunks:
            ar_context = get_annual_report_context(session)
            context_parts = []
            if ar_context:
                context_parts.append(f"Annual report analysis:\n{ar_context[:4000]}")
            context_parts.append(
                "\n\n".join(
                    f"[{c.file} p.{c.page} | {c.doc_type}] {c.text[:800]}" for c in chunks
                )
            )
            context = "\n\n".join(context_parts)
            company = session.company.name if session.company else "the company"
            try:
                result = self.llm.extract_json(
                    f"Analyze {company} using Porter's Five Forces for Indian equity research. "
                    f"Return JSON with keys: competitive_rivalry, supplier_power, buyer_power, "
                    f"threat_of_substitutes, threat_of_new_entrants. Each value: 2-3 sentences with evidence.\n\n{context}",
                    system="You are a buy-side analyst covering Indian equities. Cite evidence from excerpts.",
                    timeout=45.0,
                )
                forces.update({k: v for k, v in result.items() if k in forces and v})
            except Exception:
                pass

        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.artifact_type != "porters_five_forces"
        ]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id=self.agent_id,
                artifact_type="porters_five_forces",
                payload={
                    **forces,
                    "extraction_mode": "llm_enrichment" if use_llm else "python_excerpts",
                    "sources_searched": len(chunks),
                },
                confidence=0.75 if chunks else 0.3,
                provenance=[c.file for c in chunks],
                source_tier=SourceTier.AGENT,
            )
        )
        session.add_event(
            EventType.INFERENCE,
            agent=self.agent_id,
            details={"sources": len(chunks), "llm_enrichment": use_llm},
        )
        return session

    def _python_forces(self, session: ResearchSession, base_chunks: list) -> dict[str, str]:
        """Build each force from keyword hits across ingested files (no LLM)."""
        forces: dict[str, str] = {}
        for key, query in _FORCE_QUERIES.items():
            hits = search_pdf_chunks(
                session.pdf_chunks,
                query,
                limit=3,
                doc_types=[
                    PdfDocType.ANNUAL_REPORT.value,
                    PdfDocType.TRANSCRIPT.value,
                    PdfDocType.EARNINGS_PRESENTATION.value,
                ],
            )
            if not hits and base_chunks:
                hits = base_chunks[:2]
            if hits:
                bits = []
                for c in hits[:2]:
                    snippet = (c.text or "").strip().replace("\n", " ")
                    if len(snippet) > 320:
                        snippet = snippet[:317] + "…"
                    bits.append(f"[{c.file} p.{c.page}] {snippet}")
                forces[key] = " ".join(bits)
            else:
                forces[key] = (
                    "Insufficient data in uploaded AR / concalls / presentations — "
                    "re-upload competition or industry sections if needed."
                )
        return forces
