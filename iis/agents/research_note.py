from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Callable

from iis.agents.base import BaseAgent
from iis.config import settings
from iis.finance.research_report import build_research_report
from iis.models.enums import EventType
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, float], None]


class ResearchNoteAgent(BaseAgent):
    """Python collects all facts → LLM analyst may request more via tools → final report."""

    agent_id = "research_note"

    def __init__(self, llm=None) -> None:
        self.llm = llm

    def run(
        self,
        session: ResearchSession,
        progress_cb: ProgressCb | None = None,
    ) -> tuple[str, ResearchSession]:
        def _p(phase: str, detail: str, prog: float) -> None:
            if progress_cb:
                try:
                    progress_cb(phase, detail, prog)
                except Exception:
                    pass

        # ── Phase 1: Python collects everything ──────────────────────────
        _p("research_packs", "Building AR / concall / broker packs…", 0.86)
        try:
            from iis.finance.packs import build_all_research_packs

            build_all_research_packs(session)
        except Exception:
            pass

        _p("evidence_dossier", "Calling evidence_dossier (Python)…", 0.87)
        try:
            from iis.finance.evidence_dossier import write_evidence_dossier

            write_evidence_dossier(session)
        except Exception:
            pass

        _p(
            "python_dossier",
            "Calling build_research_report (Python overview + slots)…",
            0.89,
        )
        try:
            python_draft = build_research_report(session) or ""
        except Exception as exc:
            python_draft = (
                f"# Research Report\n\n_Python draft failed: {exc}_\n"
            )
        if len((python_draft or "").strip()) < 200:
            python_draft = (
                (python_draft or "")
                + "\n\n_Minimal draft — re-run analysis after checking uploads._\n"
            )
        mode = "python_dossier"
        # Persist Python draft immediately so Report is never empty if LLM hangs
        session.research_report_md = python_draft
        note = python_draft

        # ── Phase 2: Final Hive call — initiating coverage (fundamental analyst) ──
        if getattr(settings, "initiating_coverage_llm_enabled", True):
            wall = float(getattr(settings, "initiating_coverage_timeout_sec", 900.0) or 900.0)
            wall = min(900.0, max(120.0, wall))
            _p(
                "initiating_coverage",
                f"Hive — fundamental analyst writing initiating coverage (max {int(wall)}s)…",
                0.93,
            )
            try:
                from iis.finance.initiating_coverage import write_initiating_coverage
                from iis.llm.gate import LLMAbortError, get_llm_gate
                from iis.models.artifacts import AgentArtifact
                from iis.models.enums import SourceTier

                # Keep Python pack as a separate artifact — Report tab is the Hive note only
                session.agent_artifacts = [
                    a
                    for a in session.agent_artifacts
                    if a.artifact_type != "python_research_pack"
                ]
                session.agent_artifacts.append(
                    AgentArtifact(
                        agent_id="research_note",
                        artifact_type="python_research_pack",
                        payload={"markdown": python_draft, "chars": len(python_draft or "")},
                        confidence=0.9,
                        provenance=["python"],
                        source_tier=SourceTier.EXTRACTED,
                    )
                )

                pool = ThreadPoolExecutor(max_workers=1)

                def _run_ic():
                    return write_initiating_coverage(
                        session,
                        python_draft,
                        progress_cb=lambda m: _p("initiating_coverage", m, 0.94),
                    )

                fut = pool.submit(_run_ic)
                coverage = None
                try:
                    try:
                        coverage = fut.result(timeout=wall)
                    except FuturesTimeout:
                        logger.info(
                            "Initiating coverage wall timeout after %.0fs — keeping Python draft",
                            wall,
                        )
                        try:
                            fut.cancel()
                        except Exception:
                            pass
                    except LLMAbortError:
                        raise
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
                snap = get_llm_gate().snapshot()
                _p(
                    "initiating_coverage",
                    (
                        f"Initiating coverage done · {snap.get('llm_calls_total', 0)} calls · "
                        f"{'~' if snap.get('llm_tokens_estimated') else ''}"
                        f"{int(snap.get('llm_tokens_total') or 0):,} tokens"
                    ),
                    0.96,
                )
                if coverage and len(coverage) > 500:
                    company = (
                        session.company.name
                        if session.company
                        else (session.company_key or "Company")
                    )
                    # Hive narrative + always-on tables + Hive-selected optionals
                    try:
                        from iis.finance.report_finalize import finalize_initiating_report
                        from iis.models.artifacts import AgentArtifact
                        from iis.models.enums import SourceTier

                        note, fin_meta = finalize_initiating_report(
                            session, coverage, company=company
                        )
                        session.agent_artifacts = [
                            a
                            for a in session.agent_artifacts
                            if a.artifact_type != "report_finalize"
                        ]
                        session.agent_artifacts.append(
                            AgentArtifact(
                                agent_id="research_note",
                                artifact_type="report_finalize",
                                payload=fin_meta,
                                confidence=0.9,
                                provenance=["python"],
                                source_tier=SourceTier.EXTRACTED,
                            )
                        )
                    except Exception as exc:
                        logger.info("report finalize fallback: %s", exc)
                        note = (
                            f"# Initiating coverage — {company}\n\n"
                            + coverage.strip()
                        )
                        try:
                            from iis.finance.ciq_tables import ciq_appendix_markdown

                            appendix = ciq_appendix_markdown(session)
                            if appendix:
                                note = note.rstrip() + "\n\n" + appendix
                        except Exception:
                            pass
                    mode = "initiating_coverage"
                    session.research_report_md = note
                    _p(
                        "initiating_coverage",
                        f"Hive + data tables finalized · {len(note):,} chars",
                        0.96,
                    )
                else:
                    mode = "python_dossier_fallback"
                    _p(
                        "initiating_coverage",
                        "⚠ Hive initiating coverage empty/failed — Report is Python pack (not analyst write-up)",
                        0.96,
                    )
            except Exception as exc:
                logger.info("Initiating coverage skipped: %s", exc)
                mode = "python_dossier_fallback"
                note = python_draft
                _p(
                    "initiating_coverage",
                    f"⚠ Hive initiating coverage error — Report is Python pack ({exc})",
                    0.96,
                )
        elif getattr(settings, "llm_analyst_enabled", False):
            # Legacy tool-loop polish (off by default)
            wall = float(getattr(settings, "llm_analyst_timeout_sec", 90.0) or 90.0)
            wall = min(120.0, max(45.0, wall))
            _p(
                "llm_analyst",
                f"LLM analyst — tool loop (max {int(wall)}s)…",
                0.93,
            )
            try:
                from iis.llm.analyst_agent import write_final_report
                from iis.llm.gate import LLMAbortError, get_llm_gate

                pool = ThreadPoolExecutor(max_workers=1)
                fut = pool.submit(write_final_report, session, python_draft)
                polished = None
                try:
                    try:
                        polished = fut.result(timeout=wall)
                    except FuturesTimeout:
                        logger.info("LLM analyst wall timeout after %.0fs — keeping Python draft", wall)
                        try:
                            fut.cancel()
                        except Exception:
                            pass
                    except LLMAbortError:
                        raise
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
                snap = get_llm_gate().snapshot()
                _p(
                    "llm_analyst",
                    (
                        f"LLM done · {snap.get('llm_calls_total', 0)} calls · "
                        f"{'~' if snap.get('llm_tokens_estimated') else ''}"
                        f"{int(snap.get('llm_tokens_total') or 0):,} tokens"
                    ),
                    0.96,
                )
                if polished and len(polished) > 400:
                    head, _, layer2 = python_draft.partition("# Layer 2")
                    note = (
                        head.rstrip()
                        + "\n\n---\n\n# Analyst write-up (LLM, numbers verified against collected data)\n\n"
                        + polished
                    )
                    if layer2:
                        note += (
                            "\n\n---\n\n# Appendix — Python data pack (source tables for every number)\n"
                            + (layer2.split("\n", 1)[1] if "\n" in layer2 else "")
                        )
                    mode = "python_then_llm_analyst"
            except Exception as exc:
                logger.info("LLM analyst skipped: %s", exc)
                note = python_draft
        elif getattr(settings, "report_llm_assemble", False):
            try:
                from iis.finance.report_assemble import assemble_report_from_dossier

                polished = assemble_report_from_dossier(session, use_llm=True, persist=False)
                if polished and len(polished) > max(400, len(python_draft) // 3):
                    note = python_draft.split("# Layer 2", 1)[0].rstrip() + "\n\n---\n\n" + polished
                    mode = "python_then_llm_assemble"
            except Exception:
                note = python_draft

        _p("finalize", "Report · saving write-up…", 0.97)
        session.research_report_md = note
        session.add_event(
            EventType.NOTE_GENERATED,
            agent=self.agent_id,
            details={"length": len(note), "format": mode},
        )
        return note, session
