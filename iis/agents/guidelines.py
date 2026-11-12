from __future__ import annotations

from typing import Callable

from iis.agents.annual_report_reader import get_annual_report_context
from iis.agents.base import BaseAgent
from iis.config import settings
from iis.finance.guidance_track import (
    guidance_track_prompt_block,
    guidance_track_rows,
    guidance_track_summary,
)
from iis.ingest.pdf import search_pdf_chunks, search_transcript_qa
from iis.llm.factory import get_llm_client
from iis.models.artifacts import AgentArtifact
from iis.models.enums import EventType, PdfDocType, SourceTier
from iis.models.session import ResearchSession

ProgressCb = Callable[[str], None]


class GuidelinesAgent(BaseAgent):
    agent_id = "guidelines"

    def __init__(self, llm=None) -> None:
        self.llm = llm or get_llm_client(finance=True)
        self._progress_cb: ProgressCb | None = None

    def _notify(self, msg: str) -> None:
        if self._progress_cb:
            try:
                self._progress_cb(msg)
            except Exception:
                pass
        # Also keep a light session event for audit
        try:
            # use instance session via closure — set in run
            sess = getattr(self, "_session", None)
            if sess is not None:
                sess.add_event(
                    EventType.PROGRESS,
                    agent=self.agent_id,
                    details={"message": msg[:240]},
                )
        except Exception:
            pass

    def run(
        self,
        session: ResearchSession,
        progress_cb: ProgressCb | None = None,
    ) -> ResearchSession:
        self._progress_cb = progress_cb
        self._session = session
        track_rows = guidance_track_rows(session.guidance_records, session.financial_line_items)
        track_summary = guidance_track_summary(track_rows)
        track_block = guidance_track_prompt_block(
            session.guidance_records, session.financial_line_items
        )

        guidance_summary = [
            {
                "metric": r["metric"],
                "fiscal_year": r["fiscal_year"],
                "guidance": r["guidance"],
                "consensus": r["consensus"],
                "actual": r["actual"],
                "outcome": r.get("outcome"),
                "actual_source": r.get("actual_source"),
            }
            for r in track_rows[:25]
        ]

        qa_hits = search_transcript_qa(
            session.pdf_documents,
            "guidance outlook growth margin revenue capex capacity volume volume growth",
            limit=10,
        )

        transcript_chunks = search_pdf_chunks(
            session.pdf_chunks,
            "guidance outlook growth margin capex capacity volume",
            limit=6,
            doc_types=[PdfDocType.TRANSCRIPT.value, PdfDocType.EARNINGS_PRESENTATION.value],
            section_hints=["Q&A", "Guidance", "Outlook", "Concall"],
        )

        qa_text = "\n\n".join(
            f"Q: {qa.question}\nA: {qa.answer}" for qa in qa_hits if qa.answer
        )

        themes: list[str] = []
        commentary = ""
        key_quotes: list[str] = []
        reflections: list[dict] = []
        current_outlook = ""
        concall_structured: dict | None = None

        # Always process guidance track + all concall/transcript hits in Python
        if track_rows:
            commentary = (
                f"{track_summary['headline']}. "
                "See guidance vs actual table (Capital IQ Estimates + Financials backfill)."
            )
        if qa_hits:
            themes = [
                f"Q&A: {(qa.question or '')[:80]}"
                for qa in qa_hits[:6]
                if qa.question
            ]
            key_quotes = [
                f"Q: {(qa.question or '')[:100]} → A: {(qa.answer or '')[:180]}"
                for qa in qa_hits[:6]
                if qa.answer
            ]
            if not commentary:
                commentary = "Key transcript Q&A excerpts:\n" + "\n".join(
                    f"- {q}" for q in key_quotes[:5]
                )
            outlook_bits = []
            for qa in qa_hits:
                blob = f"{qa.question or ''} {qa.answer or ''}".lower()
                if any(k in blob for k in ("outlook", "guid", "expect", "capex", "margin")):
                    outlook_bits.append((qa.answer or "")[:280])
            if outlook_bits:
                current_outlook = outlook_bits[0]

        # Hive concall loop: relevance → deep-read → critic → evidence
        if bool(getattr(settings, "concall_structured_llm_enabled", False)) and self.llm.is_available():
            try:
                from iis.finance.concall_pipeline import run_concall_pipeline
                from iis.llm.gate import LLMAbortError

                self._notify("Concall Hive loop (quick-read→deep-read)…")
                try:
                    concall_structured = run_concall_pipeline(
                        session, progress_cb=self._notify
                    )
                except LLMAbortError:
                    raise
                if concall_structured and (
                    concall_structured.get("ok") or concall_structured.get("units")
                ):
                    if concall_structured.get("themes"):
                        themes = list(concall_structured["themes"])[:12]
                    if concall_structured.get("commentary"):
                        commentary = str(concall_structured["commentary"])
                    if concall_structured.get("current_outlook"):
                        current_outlook = str(concall_structured["current_outlook"])
                    if concall_structured.get("key_quotes"):
                        key_quotes = list(concall_structured["key_quotes"])[:10]
                    if concall_structured.get("guidance_reflections"):
                        reflections = list(concall_structured["guidance_reflections"])[:10]
            except Exception as exc:
                self._notify(f"Concall Hive loop skipped: {exc}"[:200])

        # Optional light LLM enrichment only if Hive loop did not run
        use_llm = (
            bool(getattr(settings, "analyze_llm_enrichment", False))
            and self.llm.is_available()
            and not (concall_structured and concall_structured.get("ok"))
        )
        if use_llm:
            context_parts: list[str] = []
            ar_context = get_annual_report_context(session)
            if ar_context:
                context_parts.append(f"Annual report analysis:\n{ar_context[:3000]}")
            context_parts.append(f"Prior management guidance vs actual results:\n{track_block}")
            if qa_text:
                context_parts.append(f"Concall / transcript Q&A:\n{qa_text[:3500]}")
            if transcript_chunks:
                context_parts.append(
                    "\n\n".join(f"[{c.file} p.{c.page}] {c.text[:500]}" for c in transcript_chunks)
                )
            if context_parts:
                try:
                    result = self.llm.extract_json(
                        "You are analysing Indian-listed company management communication.\n"
                        "Using ONLY the evidence below (do not invent numbers):\n"
                        "1) Reflect on whether recent concall commentary is consistent with prior guidance.\n"
                        "2) Summarise current outlook / guidance tone from the latest call.\n"
                        "3) Compare prior guidance delivery (met/missed) with how management talks now.\n\n"
                        "Return JSON with keys:\n"
                        "  themes: string[]\n"
                        "  summary: string  (overall commentary)\n"
                        "  current_outlook: string  (what management is guiding / signalling now)\n"
                        "  key_quotes: string[]\n"
                        "  guidance_reflections: array of "
                        "{fiscal_year, metric, prior_guidance, transcript_evidence, assessment}\n"
                        "assessment should be one of: consistent | walked_back | raised | unclear\n\n"
                        + "\n\n".join(context_parts),
                        system=(
                            "Buy-side equity analyst. Use only provided evidence. "
                            "Never invent financial figures. If evidence is thin, say so."
                        ),
                        timeout=45.0,
                    )
                    themes = list(result.get("themes") or themes)[:8]
                    if result.get("summary"):
                        commentary = str(result.get("summary"))
                    if result.get("current_outlook"):
                        current_outlook = str(result.get("current_outlook") or "")
                    key_quotes = list(result.get("key_quotes") or key_quotes)[:6]
                    raw_ref = result.get("guidance_reflections") or []
                    if isinstance(raw_ref, list):
                        reflections = [x for x in raw_ref if isinstance(x, dict)][:8]
                except Exception:
                    pass

        hive_ok = bool(concall_structured and concall_structured.get("ok"))
        artifact = AgentArtifact(
            agent_id=self.agent_id,
            artifact_type="guidance_summary",
            payload={
                "guidance_from_estimates": guidance_summary,
                "track_record": track_summary,
                "track_rows": guidance_summary,
                "themes": themes,
                "commentary": commentary,
                "current_outlook": current_outlook,
                "key_quotes": key_quotes,
                "guidance_reflections": reflections,
                "transcript_qa": [
                    {
                        "file": qa.file,
                        "page": qa.page,
                        "question": qa.question[:200],
                        "answer": qa.answer[:300],
                    }
                    for qa in qa_hits[:10]
                ],
                "sources": [{"file": c.file, "page": c.page} for c in transcript_chunks],
                "structured_concall": concall_structured,
                "evidence_register": list(
                    (concall_structured or {}).get("evidence_register") or []
                ),
                "hive_loop": hive_ok,
            },
            confidence=(
                0.9
                if hive_ok
                else (0.85 if (themes or reflections) else (0.7 if track_rows else (0.55 if qa_hits else 0.35)))
            ),
            provenance=list({c.file for c in transcript_chunks} | {qa.file for qa in qa_hits}),
            source_tier=SourceTier.AGENT,
        )
        session.agent_artifacts = [
            a for a in session.agent_artifacts if a.artifact_type != "guidance_summary"
        ]
        session.agent_artifacts.append(artifact)
        session.add_event(
            EventType.INFERENCE,
            agent=self.agent_id,
            details={
                "themes": len(themes),
                "qa_pairs_used": len(qa_hits),
                "track_rows": len(track_rows),
                "reflections": len(reflections),
                "concall_hive_units": len((concall_structured or {}).get("units") or []),
                "concall_hive_ok": hive_ok,
            },
        )
        return session
