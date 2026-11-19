from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from iis.agents.annual_report_reader import AnnualReportReaderAgent
from iis.agents.data_extractor import DataExtractorAgent
from iis.agents.financials_normalizer import FinancialsNormalizerAgent
from iis.agents.guidelines import GuidelinesAgent
from iis.agents.margin_of_safety import MarginOfSafetyAgent
from iis.agents.porters import PortersAgent
from iis.agents.research_note import ResearchNoteAgent
from iis.agents.user_interaction import UserInteractionAgent
from iis.config import settings
from iis.finance.market_data import RELIABLE_PRICE_SOURCES, fetch_live_price
from iis.llm.factory import get_llm_client
from iis.llm.finance_client import FinanceOllamaClient
from iis.models.enums import EventType, GateId, GateStatus, SessionStatus
from iis.models.session import ResearchSession
from iis.jobs.runner import AnalysisJobRunner
from iis.review.gates import gate_preview
from iis.storage.session_store import get_session_store
from iis.storage.paths import find_company_research_report
from iis.library.detect import company_key_from_info
from iis.library.store import get_company_library_store

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, store=None, llm=None) -> None:
        self.store = store or get_session_store()
        self.llm = llm or get_llm_client(finance=True)
        self.job_runner = AnalysisJobRunner(self)
        self.data_extractor = DataExtractorAgent()
        self.normalizer = FinancialsNormalizerAgent()
        self.annual_report = AnnualReportReaderAgent(FinanceOllamaClient())
        self.guidelines = GuidelinesAgent(self.llm)
        self.porters = PortersAgent(self.llm)
        self.mos = MarginOfSafetyAgent(self.llm)
        self.note_agent = ResearchNoteAgent(self.llm)
        self.uia = UserInteractionAgent(self.llm)

    def create_session(
        self,
        company_hint: str | None = None,
        owner_id: str | None = None,
        *,
        pull_drive: bool = True,
    ) -> ResearchSession:
        session = ResearchSession(owner_id=owner_id)
        session.init_gates()
        if company_hint:
            from iis.models.artifacts import CompanyInfo

            session.company_key = company_key_from_info(None, company_hint)
            session.company = CompanyInfo(name=company_hint)
            get_company_library_store().sync_to_session(session)
            from iis.library.reload import reload_company_from_disk

            try:
                reload_summary = reload_company_from_disk(session, pull_drive=pull_drive)
                session.add_event(EventType.PARSE, details={"auto_reload": reload_summary})
            except Exception as e:
                # Never block Upload & Process on Drive / disk scan failures
                logger.warning("create_session reload failed for %s: %s", company_hint, e)
                session.add_event(
                    EventType.PARSE,
                    details={"auto_reload_error": str(e), "pull_drive": pull_drive},
                )
        session.add_event(EventType.UPLOAD, details={"company_hint": company_hint, "owner_id": owner_id})
        self.store.save(session)
        return session

    def _load(self, session_id: str, owner_id: str | None = None) -> ResearchSession:
        if owner_id is not None:
            session = self.store.load_for_user(session_id, owner_id)
        else:
            session = self.store.load(session_id)
        session = get_company_library_store().sync_to_session(session)
        if session.company_key or (session.company and session.company.name):
            try:
                from iis.library.reload import reload_company_from_disk

                reload_company_from_disk(session, pull_drive=False)
            except Exception:
                pass
        return session

    def upload_files(
        self,
        session_id: str,
        paths: list[str | Path],
        owner_id: str | None = None,
        *,
        file_kinds: dict[str, str] | None = None,
    ) -> ResearchSession:
        session = self._load(session_id, owner_id)
        if file_kinds:
            session.upload_file_kinds = {**(session.upload_file_kinds or {}), **file_kinds}
        for p in paths:
            path = Path(p).resolve()
            if not path.exists():
                raise FileNotFoundError(str(path))
            session.uploaded_files.append(str(path))
            session.add_event(
                EventType.UPLOAD,
                details={"file": str(path), "kind": (file_kinds or {}).get(path.name)},
            )
        self.store.save(session)
        return session

    def set_company_hint(self, session_id: str, company_hint: str, owner_id: str | None = None) -> ResearchSession:
        session = self._load(session_id, owner_id)
        hint = company_hint.strip()
        if hint:
            session.company_key = company_key_from_info(session.company, hint)
            if not session.company:
                from iis.models.artifacts import CompanyInfo

                session.company = CompanyInfo(name=hint)
            get_company_library_store().sync_to_session(session)
        self.store.save(session)
        return session

    def ingest(self, session_id: str, owner_id: str | None = None) -> ResearchSession:
        session = self._load(session_id, owner_id)
        try:
            from iis.library.reload import reload_company_from_disk

            reload_company_from_disk(session, pull_drive=True)
        except Exception:
            pass
        session = self.data_extractor.run(session)
        get_company_library_store().sync_to_session(session)
        try:
            from iis.finance.analysis_pack import write_analysis_pack

            write_analysis_pack(session)
        except Exception:
            pass
        self.store.save(session)
        return session

    def refresh_market_price(self, session_id: str, owner_id: str | None = None) -> ResearchSession:
        session = self._load(session_id, owner_id)
        estimates_summary = next(
            (a.payload for a in session.agent_artifacts if a.artifact_type == "estimates_summary"),
            {},
        )
        quote = fetch_live_price(session.company, estimates_summary, llm=self.llm, force_refresh=True)
        if quote:
            session.market_quote = quote
            session.add_event(
                EventType.MARKET_PRICE_FETCH,
                agent="orchestrator",
                details={"price": quote.price, "source": quote.source, "exchange": quote.exchange},
            )
            if session.assumptions and all(a.approved for a in session.assumptions):
                session = self.mos.run(session)
        elif session.market_quote and session.market_quote.source not in RELIABLE_PRICE_SOURCES:
            session.market_quote = None
        self.store.save(session)
        return session

    def analyze(self, session_id: str, *, skip_gates: bool = False, owner_id: str | None = None) -> ResearchSession:
        """One uninterrupted analysis pass → full research report stored for the UI.

        No gate approvals required. User reviews the report afterward and refines via chat.
        skip_gates kept for API compat.
        """
        session = self._load(session_id, owner_id)
        _ = skip_gates

        session.status = SessionStatus.ANALYZING
        session = self.normalizer.run(session)
        session = self.annual_report.run(session)
        session = self.guidelines.run(session)
        session = self.porters.run(session)
        session = self.mos.run(session)
        # Suggest peers (web) + ask for missing CIQ uploads — never blocks
        try:
            from iis.finance.competitor_discovery import apply_competitor_discovery

            session = apply_competitor_discovery(session)
        except Exception:
            pass
        # Flag stale Financials / Estimates and ask for a re-download
        try:
            from iis.finance.data_freshness import apply_freshness_artifact

            session = apply_freshness_artifact(session)
        except Exception:
            pass

        # Mark review checkpoints as system-completed (informational only — no user interrupt)
        session.init_gates()
        for gid in GateId:
            g = session.gate(gid)
            if g.status != GateStatus.APPROVED:
                g.status = GateStatus.APPROVED
                g.reviewed_at = datetime.utcnow()
                g.reviewer_comment = "Auto-completed with full analysis pass (chat to refine)"

        try:
            from iis.finance.analysis_pack import write_analysis_pack

            write_analysis_pack(session)
        except Exception:
            pass
        try:
            from iis.finance.evidence_dossier import write_evidence_dossier

            write_evidence_dossier(session)
        except Exception:
            pass

        # Full report in the same pass — stored on session + disk for UI Report tab
        try:
            note, session = self.note_agent.run(session)
            if note:
                company_name = session.company.name if session.company else None
                if company_name:
                    from datetime import date

                    from iis.config import settings
                    from iis.integrations.gdrive import company_folder_name, sync_research_note

                    safe = company_folder_name(company_name).replace(" ", "_")
                    local_dir = (
                        settings.data_dir / "companies" / (session.company_key or safe) / "reports"
                    )
                    local_dir.mkdir(parents=True, exist_ok=True)
                    local_path = local_dir / f"{safe}_Research_Report_{date.today().isoformat()}.md"
                    local_path.write_text(note, encoding="utf-8")
                    session.research_report_path = str(local_path)
                    try:
                        sync_research_note(company_name, note)
                    except Exception:
                        pass
            session.status = SessionStatus.COMPLETE
            session.add_event(
                EventType.NOTE_GENERATED,
                agent="orchestrator",
                details={"via": "analyze_full_pass", "length": len(note or "")},
            )
        except Exception as e:
            session.status = SessionStatus.READY
            session.add_event(
                EventType.INFERENCE,
                agent="orchestrator",
                details={"report_error": str(e), "via": "analyze_full_pass"},
            )

        self.store.save(session)
        return session

    def preview_gate(self, session_id: str, gate_id: GateId, owner_id: str | None = None) -> dict:
        session = self._load(session_id, owner_id)
        return gate_preview(session, gate_id)

    def approve_gate(
        self,
        session_id: str,
        gate_id: GateId,
        *,
        approve: bool = True,
        comment: str | None = None,
        owner_id: str | None = None,
    ) -> ResearchSession:
        session = self._load(session_id, owner_id)
        gate = session.gate(gate_id)
        if approve:
            gate.status = GateStatus.APPROVED
        else:
            gate.status = GateStatus.PENDING if gate_id == GateId.VALUATION_ASSUMPTIONS else GateStatus.REJECTED
        gate.reviewer_comment = comment
        gate.reviewed_at = datetime.utcnow()

        if gate_id == GateId.VALUATION_ASSUMPTIONS:
            if approve:
                for a in session.assumptions:
                    a.approved = True
                    session.add_event(
                        EventType.ASSUMPTION_APPROVED,
                        agent="orchestrator",
                        details={"key": a.key, "value": a.user_value or a.value},
                    )
                session = self.mos.run(session)
            else:
                for a in session.assumptions:
                    a.approved = False

        session.add_event(
            EventType.GATE_APPROVED if approve else EventType.GATE_REJECTED,
            agent="orchestrator",
            details={"gate": gate_id.value},
            user_action="approved" if approve else "rejected",
            user_comment=comment,
        )
        self.store.save(session)
        return session

    def update_assumption(
        self,
        session_id: str,
        key: str,
        value: float | str,
        owner_id: str | None = None,
    ) -> ResearchSession:
        session = self._load(session_id, owner_id)
        for a in session.assumptions:
            if a.key == key:
                a.user_value = value
                a.user_edited = True
                a.approved = False
                a.needs_user_input = False
                a.review_prompt = None
                session.add_event(
                    EventType.ASSUMPTION_PROPOSED,
                    agent="orchestrator",
                    details={"key": key, "value": value, "user_edit": True},
                )
                break
        else:
            raise KeyError(f"Unknown assumption: {key}")

        session.gate(GateId.VALUATION_ASSUMPTIONS).status = GateStatus.PENDING
        session = self.mos.run(session)
        self.store.save(session)
        return session

    def confirm_external_fact(
        self, session_id: str, index: int, confirmed: bool = True, owner_id: str | None = None
    ) -> ResearchSession:
        session = self._load(session_id, owner_id)
        if index < 0 or index >= len(session.external_facts):
            raise IndexError("External fact index out of range")
        session.external_facts[index].user_confirmed = confirmed
        session.add_event(
            EventType.EXTERNAL_FACT,
            agent="orchestrator",
            details={"index": index, "confirmed": confirmed},
        )
        self.store.save(session)
        return session

    def ask(self, session_id: str, question: str, owner_id: str | None = None):
        session = self._load(session_id, owner_id)
        response = self.uia.ask(session, question)
        session.add_event(EventType.CHAT_QA, agent="user_interaction", details={"question": question})
        action = (response.handoff or {}).get("action")
        if action == "update_assumption":
            try:
                self.update_assumption(
                    session_id,
                    response.handoff["key"],
                    response.handoff["value"],
                    owner_id=owner_id,
                )
            except KeyError:
                self.store.save(session)
        elif action in ("rerun_analysis", "update_report"):
            self.store.save(session)
            # Kick async analyze; UI also may call /analyze — prefer async job
            try:
                if action == "update_report":
                    try:
                        self.ingest(session_id, owner_id=owner_id)
                    except Exception:
                        pass
                job = self.analyze_async(session_id, owner_id=owner_id)
                response.handoff = {
                    **(response.handoff or {}),
                    "action": action,
                    "job_id": getattr(job, "id", None),
                    "status": getattr(job, "status", "running"),
                }
            except Exception as e:
                response.answer = (response.answer or "") + f"\n\n_Could not start analysis: {e}_"
                self.store.save(session)
        elif action == "regenerate_report":
            try:
                note = self.generate_note(session_id, owner_id=owner_id)
                response.answer = (response.answer or "") + "\n\nReport regenerated."
                response.handoff = {
                    "action": "show_report",
                    "markdown_len": len(note or ""),
                }
            except Exception as e:
                response.answer = (response.answer or "") + f"\n\n_Regenerate failed: {e}_"
                self.store.save(session)
        else:
            self.store.save(session)
        return response

    def generate_note(self, session_id: str, owner_id: str | None = None) -> str:
        session = self._load(session_id, owner_id)
        note, session = self.note_agent.run(session)
        session.status = SessionStatus.COMPLETE

        company_name = session.company.name if session.company else None
        local_path = None
        if company_name and note:
            from datetime import date

            from iis.config import settings
            from iis.integrations.gdrive import company_folder_name, sync_research_note

            safe = company_folder_name(company_name).replace(" ", "_")
            local_dir = settings.data_dir / "companies" / (session.company_key or safe) / "reports"
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / f"{safe}_Research_Report_{date.today().isoformat()}.md"
            local_path.write_text(note, encoding="utf-8")
            session.research_report_path = str(local_path)
            sync_research_note(company_name, note)

        self.store.save(session)
        return note

    def save_research_report(
        self,
        session_id: str,
        markdown: str,
        owner_id: str | None = None,
    ) -> ResearchSession:
        """Persist chat-edited report markdown (does not regenerate from scratch)."""
        session = self._load(session_id, owner_id)
        session.research_report_md = markdown
        company_name = session.company.name if session.company else None
        if company_name and markdown:
            from datetime import date

            from iis.config import settings
            from iis.integrations.gdrive import company_folder_name

            safe = company_folder_name(company_name).replace(" ", "_")
            local_dir = settings.data_dir / "companies" / (session.company_key or safe) / "reports"
            local_dir.mkdir(parents=True, exist_ok=True)
            path = local_dir / f"{safe}_Research_Report_{date.today().isoformat()}.md"
            path.write_text(markdown, encoding="utf-8")
            session.research_report_path = str(path)
        session.touch()
        self.store.save(session)
        return session

    def finalize_report_docx(self, session_id: str, owner_id: str | None = None) -> dict:
        """Convert current report markdown → .docx, save locally, push to Drive."""
        from iis.integrations.docx_export import export_report_docx

        session = self._load(session_id, owner_id)
        md = session.research_report_md
        if not md:
            md = self.generate_note(session_id, owner_id=owner_id)
            session = self._load(session_id, owner_id)
        result = export_report_docx(session, md)
        if result.get("path"):
            session.research_report_path = result["path"]
            self.store.save(session)
        return result

    def get_session(self, session_id: str, owner_id: str | None = None) -> ResearchSession:
        return self._load(session_id, owner_id)

    def list_sessions(self, owner_id: str | None = None) -> list[ResearchSession]:
        return self.store.list_sessions(owner_id=owner_id if settings.auth_required else None)

    def list_session_cards(self, owner_id: str | None = None, *, limit: int = 20) -> list[dict]:
        """Sidebar cards only — full session is loaded when the user opens one."""
        owner = owner_id if settings.auth_required else None
        if hasattr(self.store, "list_session_cards"):
            return self.store.list_session_cards(owner, limit=limit)
        # Fallback for older stores
        cards = []
        for s in self.store.list_sessions(owner_id=owner)[:limit]:
            cards.append(
                {
                    "id": s.id,
                    "owner_id": s.owner_id,
                    "company": s.company.model_dump() if s.company else None,
                    "company_key": s.company_key,
                    "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    "has_research_report": bool(
                        (s.research_report_md and len(s.research_report_md) > 200)
                        or s.research_report_path
                        or find_company_research_report(s.company_key)
                    ),
                }
            )
        return cards

    def analyze_async(self, session_id: str, owner_id: str | None = None):
        return self.job_runner.start(session_id, owner_id=owner_id)

    def abort_analysis_job(self, job_id: str) -> bool:
        return self.job_runner.abort(job_id)

    def get_analysis_job(self, job_id: str):
        return self.job_runner.get_job(job_id)

    def review_evidence_fact(
        self,
        session_id: str,
        fact_id: str,
        *,
        approve: bool = True,
        owner_id: str | None = None,
    ) -> ResearchSession:
        session = self._load(session_id, owner_id)
        for fact in session.evidence_facts:
            if fact.id == fact_id:
                fact.status = "approved" if approve else "rejected"
                break
        else:
            raise KeyError(f"Evidence fact {fact_id} not found")
        session.add_event(
            EventType.GATE_APPROVED if approve else EventType.GATE_REJECTED,
            agent="orchestrator",
            details={"fact_id": fact_id, "approved": approve},
        )
        self.store.save(session)
        return session
