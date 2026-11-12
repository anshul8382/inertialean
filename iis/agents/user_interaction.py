from __future__ import annotations

import re
from typing import Any

from iis.agents.query_planner import plan_query, route_deterministic
from iis.config import settings
from iis.finance.analysis_pack import (
    answer_from_pack_deterministic,
    extract_pack_context,
    load_analysis_pack,
    write_analysis_pack,
)
from iis.finance.metric_series import build_capex_analysis, format_capex_analysis
from iis.ingest.pdf import search_pdf_chunks, search_transcript_qa
from iis.search.embeddings import EmbeddingService
from iis.llm.factory import get_llm_client
from iis.models.artifacts import ChatResponse, ExternalFact
from iis.models.enums import EventType, GateId, PeriodType, SourceTier
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import get_latest_actual, query_financials
from iis.search.web_search import format_external_results, search_web


class UserInteractionAgent:
    agent_id = "user_interaction"

    METRIC_ALIASES = {
        "revenue": "total_revenue",
        "sales": "total_revenue",
        "ebitda": "ebitda",
        "net income": "net_income",
        "profit": "net_income",
        "eps": "eps_normalized",
    }

    def __init__(self, llm=None) -> None:
        self.llm = llm or get_llm_client(finance=True)
        # Embeddings stay on local Ollama; chat/report use Hive when configured
        self.embedder = EmbeddingService()

    def _build_evidence_pack(
        self, session: ResearchSession, plan: dict | None, sources: list[dict] | None = None
    ) -> dict[str, Any]:
        from iis.models.enums import GateId

        session.init_gates()
        gates = {g.value: session.gate(g).status.value for g in GateId}
        approved_facts = [f.model_dump() for f in session.evidence_facts if f.status == "approved"][:5]
        return {
            "query_plan": plan,
            "gates": gates,
            "sources": sources or [],
            "approved_facts": approved_facts,
            "data_counts": {
                "line_items": len(session.financial_line_items),
                "pdf_chunks": len(session.pdf_chunks),
                "qa_pairs": sum(len(d.qa_pairs) for d in session.pdf_documents),
            },
        }

    def _attach_evidence(self, resp: ChatResponse, session: ResearchSession, plan: dict | None) -> ChatResponse:
        resp.evidence = self._build_evidence_pack(session, plan, resp.sources)
        return resp

    def ask(self, session: ResearchSession, question: str) -> ChatResponse:
        from iis.agents.user_communicator import (
            capture_directive,
            handle_context_command,
            handle_peer_command,
            parse_peer_command,
        )
        from iis.context.manager import llm_agent, record_turn

        q = question.strip()
        resp: ChatResponse | None = None
        note: str | None = None

        proposals: list = []
        try:
            from iis.context.chat_router import apply_proposal, classify_chat_turn

            proposals = classify_chat_turn(session, q)
        except Exception:
            proposals = []

        # Auto-apply proposals that don't need confirm (Remember:/rerun/regenerate)
        auto_notes: list[str] = []
        pending_proposals: list = []
        action_handoff = None
        for p in proposals:
            if p.get("auto") or not p.get("needs_confirm", True):
                try:
                    result = apply_proposal(session, p)
                    if result.get("ok") and p["kind"] in (
                        "company_rule",
                        "session_directive",
                        "analyst_note",
                    ):
                        auto_notes.append(f"Saved **{p['kind'].replace('_', ' ')}**: “{p['text'][:160]}”")
                    if result.get("action"):
                        action_handoff = {"action": result["action"]}
                except Exception:
                    pending_proposals.append(p)
            else:
                pending_proposals.append(p)

        peer_cmd = parse_peer_command(q)
        if peer_cmd and session.company_key:
            resp = ChatResponse(
                answer=handle_peer_command(session, peer_cmd),
                source_tier=SourceTier.SESSION,
                confidence="high",
                note="Peer decision saved",
                handoff={"action": "peers_updated"},
            )
        if resp is None:
            ctx_reply = handle_context_command(session, q)
            if ctx_reply:
                resp = ChatResponse(answer=ctx_reply, source_tier=SourceTier.SESSION, confidence="high", note="Research context")
        # Explicit Remember:/Rule: — skip if chat_router already auto-saved
        already_saved_rule = any(
            n.startswith("Saved **company rule") or n.startswith("Saved **session directive")
            for n in auto_notes
        )
        if resp is None and not already_saved_rule:
            captured = capture_directive(session, q)
            if captured:
                note, explicit = captured
                if explicit:
                    resp = ChatResponse(
                        answer=note,
                        source_tier=SourceTier.SESSION,
                        confidence="high",
                        note="Research context",
                    )
                    note = None
                else:
                    # Cue-only: do not auto-persist — propose confirm instead
                    note = None
                    if not any(p["kind"] in ("company_rule", "session_directive") for p in pending_proposals):
                        pending_proposals.append(
                            {
                                "id": "cue-directive",
                                "kind": "session_directive",
                                "text": q.strip(),
                                "title": "Save as analysis instruction for this session?",
                                "needs_confirm": True,
                                "auto": False,
                                "meta": {"scope": "session"},
                            }
                        )
        if resp is not None:
            session.chat_history.append(resp)
        else:
            # Pure action commands (rerun / regenerate) — short ack, no LLM
            if action_handoff and not peer_cmd:
                labels = {
                    "rerun_analysis": "Re-running analysis with data on file…",
                    "regenerate_report": "Regenerating the research report…",
                    "update_report": "Updating report from new files (reingest → analyze → regenerate)…",
                }
                resp = ChatResponse(
                    answer=labels.get(action_handoff["action"], "Working…"),
                    source_tier=SourceTier.SESSION,
                    confidence="high",
                    note="Action",
                    handoff=action_handoff,
                )
                session.chat_history.append(resp)
            else:
                with llm_agent("chat"):
                    resp = self._ask(session, q)
                if note:
                    resp.answer = f"_{note}_\n\n{resp.answer}"
                if auto_notes:
                    resp.answer = "\n".join(f"_{n}_" for n in auto_notes) + "\n\n" + resp.answer
        if action_handoff and (not resp.handoff or resp.handoff.get("action") in (None, "peers_updated")):
            if action_handoff["action"] != "peers_updated":
                resp.handoff = action_handoff
        if pending_proposals:
            resp.proposals = pending_proposals
            confirm_lines = [
                "",
                "**Confirm to apply:**",
            ]
            for p in pending_proposals[:4]:
                confirm_lines.append(f"- {p.get('title') or p.get('kind')}: “{(p.get('text') or '')[:120]}”")
            confirm_lines.append("_Use the buttons below, or ignore._")
            if not any("Confirm to apply" in (resp.answer or "") for _ in [0]):
                resp.answer = (resp.answer or "") + "\n" + "\n".join(confirm_lines)
        try:
            record_turn(session, q, resp.answer)
        except Exception:
            pass
        return resp

    def _verified(self, session: ResearchSession, text: str, evidence: list[str], agent: str) -> str | None:
        from iis.facts.verifier import verify_llm_output

        return verify_llm_output(session, text, evidence, agent=agent)

    def _ask(self, session: ResearchSession, q: str) -> ChatResponse:

        # 0) Ensure offline analysis pack exists (Python → .md, no web)
        if settings.prefer_analysis_pack:
            try:
                if not load_analysis_pack(session):
                    write_analysis_pack(session)
            except Exception:
                pass

        # 1) Instant Python modules (capex, flags, capacity excerpts)
        direct = answer_from_pack_deterministic(session, q)
        if direct:
            resp = ChatResponse(
                answer=direct["answer"],
                source_tier=SourceTier.EXTRACTED,
                confidence="high",
                note=f"Answered from {direct.get('source', 'python')} [{direct.get('query_type')}]",
            )
            resp = self._llm_refine_chat(session, q, resp)
            session.add_event(
                EventType.CHAT_QA,
                agent=self.agent_id,
                details={"question": q, "query_type": direct.get("query_type"), "tier": "pack_module"},
            )
            session.chat_history.append(resp)
            return self._attach_evidence(resp, session, {"query_type": direct.get("query_type")})

        # Broad research questions → Hive analyst (tools) first, not narrow Python-only routes
        if self._is_open_research_question(q):
            analyst = self._llm_analyst_answer(session, q, python_draft=None)
            if analyst:
                session.add_event(
                    EventType.CHAT_QA,
                    agent=self.agent_id,
                    details={"question": q, "tier": "llm_analyst_open"},
                )
                session.chat_history.append(analyst)
                return self._attach_evidence(analyst, session, {"query_type": "llm_analyst"})

        # 2) Deterministic router → Python handlers → optional LLM refine
        plan = route_deterministic(session, q)
        if plan and not self._is_open_research_question(q):
            planned = self._execute_plan(session, q, plan)
            if planned:
                planned = self._llm_refine_chat(session, q, planned)
                session.add_event(
                    EventType.CHAT_QA,
                    agent=self.agent_id,
                    details={"question": q, "query_type": plan.get("query_type"), "tier": "deterministic"},
                )
                session.chat_history.append(planned)
                return self._attach_evidence(planned, session, plan)

        lower_q = q.lower()

        # Stale CIQ / refresh asks
        if any(
            p in lower_q
            for p in (
                "stale",
                "outdated",
                "out of date",
                "update financials",
                "update estimates",
                "refresh financials",
                "refresh estimates",
                "data freshness",
                "how old are the files",
                "file age",
            )
        ):
            from iis.finance.data_freshness import assess_data_freshness, apply_freshness_artifact

            session = apply_freshness_artifact(session)
            fresh = assess_data_freshness(session)
            if fresh.get("user_ask"):
                ans = (
                    "**Capital IQ data freshness check**\n\n"
                    + fresh["user_ask"]
                    + "\n\nRe-download from Capital IQ → Continue with this company → drop the new .xls. "
                    "Old files stay in the company library archive."
                )
            else:
                fin = (fresh.get("financials_docs") or [{}])
                est = (fresh.get("estimates_docs") or [{}])
                ans = (
                    "Financials / Estimates look **current enough** for now.\n"
                    f"- Latest actuals period: {fresh.get('latest_actual_period') or '—'}\n"
                    f"- Financials age: {(fin[0].get('age_days') if fin else None)} days\n"
                    f"- Estimates age: {(est[0].get('age_days') if est else None)} days\n"
                    "Still re-export after each earnings print so consensus and LTM stay fresh."
                )
            resp = ChatResponse(
                answer=ans,
                source_tier=SourceTier.SESSION,
                confidence="high",
                note="Data freshness",
                structured={"type": "data_freshness", **fresh},
            )
            session.chat_history.append(resp)
            return resp

        # Library / already-uploaded peer files
        lib_hit = self._try_library_find_response(session, q)
        if lib_hit:
            session.chat_history.append(lib_hit)
            return lib_hit

        # Guidance track / concall reflection (before generic competitor keyword catch)
        if any(
            p in lower_q
            for p in (
                "guidance vs",
                "vs actual",
                "versus actual",
                "track record",
                "missed guidance",
                "beat guidance",
                "concall",
                "management guidance",
                "prior guidance",
            )
        ):
            gresp = self._try_guidance_response(session, q)
            if gresp:
                session.chat_history.append(gresp)
                return gresp

        # 2a0) Rerun analysis after user adds peer / more files
        if any(
            p in lower_q
            for p in (
                "rerun analysis",
                "re-run analysis",
                "run analysis again",
                "reanalyze",
                "re-analyse",
                "reanalyse",
            )
        ):
            return ChatResponse(
                answer=(
                    "Re-running analysis with whatever is on file now "
                    "(including any new peer CIQ uploads). "
                    "When it finishes, say **regenerate report** to refresh the write-up."
                ),
                source_tier=SourceTier.SESSION,
                confidence="high",
                note="Rerun analysis",
                handoff={"action": "rerun_analysis"},
            )

        # 2a0b) Competitor / peer questions → discovery ask
        if any(
            p in lower_q
            for p in (
                "competitor",
                "competitors",
                "peers",
                "peer companies",
                "who competes",
                "rival",
            )
        ):
            from iis.finance.competitor_discovery import apply_competitor_discovery, competitor_ask_markdown

            session = apply_competitor_discovery(session)
            art = next(
                (a for a in session.agent_artifacts if a.artifact_type == "competitor_suggestions"),
                None,
            )
            body = "\n".join(competitor_ask_markdown(session)).strip() or (
                "No peers found in your files yet. Name them with `add peer <name>`."
            )
            p = art.payload if art else {}
            resp = ChatResponse(
                answer=body,
                source_tier=SourceTier.SESSION,
                confidence="high" if p.get("confirmed") else "medium",
                note=(
                    "Peers proposed from CIQ comps / AR / broker reports / price-sheet sector"
                    + (" + web (proposal only)" if p.get("used_web_llm") else "")
                    + " — confirm before use"
                ),
                sources=[{"type": "competitor_suggestions", "names": p.get("suggested") or []}],
                structured={"type": "peers", **{k: p.get(k) for k in ("proposed", "confirmed", "rejected", "file_asks", "on_file")}},
            )
            session.chat_history.append(resp)
            return resp

        # 2a) Regenerate research report after chat edits
        if any(
            p in lower_q
            for p in (
                "regenerate report",
                "rebuild report",
                "update the report",
                "refresh the report",
                "generate research report",
                "generate the report",
            )
        ):
            from iis.finance.research_report import build_research_report

            note = build_research_report(session)
            session.research_report_md = note
            try:
                from datetime import date

                from iis.integrations.gdrive import company_folder_name

                company_name = session.company.name if session.company else None
                if company_name:
                    safe = company_folder_name(company_name).replace(" ", "_")
                    local_dir = (
                        settings.data_dir / "companies" / (session.company_key or safe) / "reports"
                    )
                    local_dir.mkdir(parents=True, exist_ok=True)
                    path = local_dir / f"{safe}_Research_Report_{date.today().isoformat()}.md"
                    path.write_text(note, encoding="utf-8")
                    session.research_report_path = str(path)
            except Exception:
                pass
            session.add_event(
                EventType.NOTE_GENERATED,
                agent=self.agent_id,
                details={"length": len(note), "via": "chat_regenerate"},
            )
            session.chat_history.append(
                ChatResponse(
                    answer=(
                        "Research report regenerated from current session data and assumptions "
                        f"({len(note):,} characters). Open the **Report** tab to read it, or click "
                        "**Generate Research Report** in the sidebar. Finalize to Word when ready.\n\n"
                        "_Your Buy/Hold/Sell rating is still blank — fill it when you decide._"
                    ),
                    source_tier=SourceTier.SESSION,
                    confidence="high",
                    note="Report refreshed",
                    handoff={"action": "show_report", "markdown_len": len(note)},
                )
            )
            return session.chat_history[-1]

        # 2b) DCF assumption edits (%, set/use) — never send to LLM planner
        if (
            re.search(r"\d+\s*%", q)
            or "update assumption" in lower_q
            or "gate 4" in lower_q
            or any(k in lower_q for k in ("set wacc", "set terminal", "set discount", "set capex", "use "))
        ) and any(
            k in lower_q
            for k in (
                "capex",
                "wacc",
                "terminal",
                "discount",
                "margin",
                "growth",
                "working capital",
                "nwc",
                "tax rate",
                "assumption",
            )
        ):
            handoff = self._assumption_handoff(session, q)
            session.chat_history.append(handoff)
            return handoff

        # 3) Optional LLM planner — OFF by default (complex Ollama hangs on Air)
        if settings.enable_query_planner and self.llm.is_available():
            try:
                plan = plan_query(session, q, self.llm)
            except Exception:
                plan = None
            if plan:
                planned = self._execute_plan(session, q, plan)
                if planned:
                    planned = self._maybe_polish(session, q, planned)
                    session.add_event(
                        EventType.CHAT_QA,
                        agent=self.agent_id,
                        details={"question": q, "query_type": plan.get("query_type"), "tier": "planned"},
                    )
                    session.chat_history.append(planned)
                    return self._attach_evidence(planned, session, plan)

        # 4) LLM analyst (may call Python tools) → else keyword / pack fallback
        analyst = self._llm_analyst_answer(session, q, python_draft=None)
        if analyst:
            session.add_event(
                EventType.CHAT_QA,
                agent=self.agent_id,
                details={"question": q, "tier": "llm_analyst"},
            )
            session.chat_history.append(analyst)
            return self._attach_evidence(analyst, session, {"query_type": "llm_analyst"})

        return self._ask_keyword_fallback(session, q)

    def _llm_refine_chat(
        self, session: ResearchSession, question: str, resp: ChatResponse
    ) -> ChatResponse:
        """After Python draft: LLM may request more Python info and refine the answer."""
        # Chat Hive path is independent of Analyze's llm_analyst_enabled
        if getattr(settings, "chat_llm_analyst", True):
            refined = self._llm_analyst_answer(session, question, python_draft=resp.answer)
            if refined and refined.answer and len(refined.answer) > 40:
                refined.note = ((resp.note or "") + " · llm_analyst").strip(" ·")
                refined.source_tier = resp.source_tier or refined.source_tier
                refined.confidence = resp.confidence or refined.confidence
                refined.sources = resp.sources or refined.sources
                return refined
        return self._maybe_polish(session, question, resp)

    def _llm_analyst_answer(
        self,
        session: ResearchSession,
        question: str,
        *,
        python_draft: str | None,
    ) -> ChatResponse | None:
        if not getattr(settings, "chat_llm_analyst", True):
            return None
        try:
            from iis.llm.analyst_agent import answer_user_question

            text = answer_user_question(session, question, python_draft=python_draft)
        except Exception:
            return None
        if not text or len(text.strip()) < 40:
            return None
        return ChatResponse(
            answer=text.strip(),
            source_tier=SourceTier.AGENT,
            confidence="medium",
            note="llm_analyst + python tools",
        )

    def _maybe_polish(self, session: ResearchSession, question: str, resp: ChatResponse) -> ChatResponse:
        """Light Ollama tidy when full analyst path is off."""
        if not settings.chat_llm_polish or settings.chat_llm_complex:
            return resp
        if not resp.answer or len(resp.answer) < 40:
            return resp
        if not self.llm.is_available():
            return resp
        try:
            polished = self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You tidy equity-research answers for readability. "
                            "Keep ALL numbers and facts exactly. Do not invent data. "
                            "Use short paragraphs and bullets. Return markdown only."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Question: {question}\n\nDraft answer:\n{resp.answer[:4500]}",
                    },
                ],
                timeout=float(settings.ollama_chat_timeout_sec or 20),
            )
            polished = self._verified(session, polished or "", [resp.answer], "chat_polish")
            if polished and len(polished.strip()) > 20:
                resp.answer = polished.strip()
                note = (resp.note or "") + (" · polished" if resp.note else "polished")
                resp.note = note.strip(" ·")
        except Exception:
            pass
        return resp

    def _execute_plan(self, session: ResearchSession, question: str, plan: dict) -> ChatResponse | None:
        qtype = str(plan.get("query_type", "")).lower().replace("-", "_")
        params: dict = plan.get("params") or {}

        if qtype == "multi":
            steps = plan.get("steps") or []
            parts: list[str] = []
            all_sources: list[dict] = []
            tier = SourceTier.EXTRACTED
            for step in steps:
                sub_plan = {
                    "query_type": step.get("query_type", ""),
                    "params": step.get("params") or {},
                    "reason": plan.get("reason"),
                }
                sub = self._execute_plan(session, question, sub_plan)
                if sub:
                    parts.append(sub.answer)
                    all_sources.extend(sub.sources)
                    if sub.source_tier == SourceTier.APPROVED:
                        tier = SourceTier.APPROVED
                    elif sub.source_tier == SourceTier.AGENT and tier != SourceTier.APPROVED:
                        tier = SourceTier.AGENT
            if not parts:
                return None
            return ChatResponse(
                answer="\n\n---\n\n".join(parts),
                sources=all_sources,
                source_tier=tier,
                confidence=plan.get("confidence", "medium"),
                note=f"Multi-step query ({len(steps)} steps)",
            )

        dispatch = {
            "revenue_growth": lambda: self._try_growth_lookup(session, question, params=params, force=True),
            "financial_metric": lambda: self._try_financial_lookup(session, question, params=params, force=True),
            "capex_analysis": lambda: self._try_capex_analysis(session, params=params),
            "capacity_utilization": lambda: self._try_capacity_from_pack(session, question),
            "ratio": lambda: self._try_ratio_lookup(session, question, params=params, force=True),
            "all_ratios": lambda: self._try_ratio_lookup(session, "show all financial ratios", params={"all": True}, force=True),
            "segment_revenue": lambda: self._try_segment_lookup(session, question, params=params, force=True),
            "consensus": lambda: self._try_consensus_lookup(session, question, params=params, force=True),
            "valuation_multiples": lambda: self._try_multiples_lookup(session, question, params=params, force=True),
            "guidance": lambda: self._try_guidance_response(session, question),
            "porters": lambda: self._try_artifact_lookup(session, "competition", artifact_type="porters"),
            "annual_report": lambda: self._try_artifact_lookup(session, "annual report", artifact_type="annual_report"),
            "transcript_qa": lambda: self._try_transcript_qa(session, question, params=params),
            "data_flags": lambda: self._try_data_flags(session, category=None),
            "red_flags": lambda: self._try_data_flags(session, category=None),
            "guidance_track_record": lambda: self._try_guidance_response(session, question),
            "valuation": lambda: self._valuation_response(session),
            "market_price": lambda: self._market_price_response(session),
            "gates": lambda: self._gates_response(session),
            "files": lambda: self._files_response(session),
            "generate_note": lambda: ChatResponse(
                answer="Use **Generate Research Note** or `iis note <session_id>`.",
                source_tier=SourceTier.SESSION,
                handoff={"action": "generate_note"},
            ),
            "pdf_rag": lambda: self._try_pdf_rag(session, question),
            "external": lambda: self._external_search(session, question),
            "analysis_pack": lambda: self._try_pack_grounded_llm(session, question),
        }

        handler = dispatch.get(qtype)
        if not handler:
            return None
        resp = handler()
        if resp and plan.get("reason"):
            resp.note = (resp.note or "") + f" [Query: {qtype}]"
        return resp

    def _ask_keyword_fallback(self, session: ResearchSession, q: str) -> ChatResponse:
        lower = q.lower()

        # Meta queries
        if any(kw in lower for kw in ("what files", "uploaded", "what data")):
            return self._files_response(session)

        if any(kw in lower for kw in ("gate", "approval", "pending approval", "review status")):
            return self._gates_response(session)

        # Historical capex / FCF questions → Python modules (not DCF assumption editor)
        if any(k in lower for k in ("capex", "capital expenditure")) and not re.search(r"\d+\s*%", q):
            capex = self._try_capex_analysis(session)
            if capex:
                session.chat_history.append(capex)
                return capex
        if any(k in lower for k in ("capacity utilization", "utilisation", "utilization", "installed capacity")):
            cap = self._try_capacity_from_pack(session, q)
            if cap:
                session.chat_history.append(cap)
                return cap

        # Assumption / valuation what-if (only when editing assumptions)
        if any(
            kw in lower
            for kw in (
                "terminal growth",
                "discount rate",
                "rerun valuation",
                "wc investment",
                "co-create",
                "estimate fcf",
                "estimate wc",
                "update assumption",
                "set wacc",
            )
        ) or (
            any(k in lower for k in ("fcf", "free cash", "working capital", "capex"))
            and (re.search(r"\d+\s*%", q) or "assumption" in lower or "gate 4" in lower)
        ):
            return self._assumption_handoff(session, q)

        if any(
            kw in lower
            for kw in (
                "valuation review",
                "dcf flag",
                "why is iv",
                "why is intrinsic",
                "iv so low",
                "iv so high",
            )
        ):
            return self._valuation_review_response(session)

        if "intrinsic value" in lower or "margin of safety" in lower or "fair value" in lower:
            return self._valuation_response(session)

        if any(kw in lower for kw in ("current price", "stock price", "market price", "share price", "ltp")):
            return self._market_price_response(session)

        if "document" in lower and ("note" in lower or "research" in lower):
            return ChatResponse(
                answer="Generating research note from approved research path. Use `iis note <session_id>` or type `note` in interactive mode.",
                source_tier=SourceTier.SESSION,
                handoff={"action": "generate_note"},
            )

        # Segment lookup (before generic revenue lookup)
        segment = self._try_segment_lookup(session, q)
        if segment:
            session.chat_history.append(segment)
            return segment

        # Financial ratios (Capital IQ + annual report)
        ratio_answer = self._try_ratio_lookup(session, q)
        if ratio_answer:
            session.chat_history.append(ratio_answer)
            return ratio_answer

        # Revenue / growth trends (before single-line revenue lookup)
        growth_answer = self._try_growth_lookup(session, q)
        if growth_answer:
            session.chat_history.append(growth_answer)
            return growth_answer

        multiples_answer = self._try_multiples_lookup(session, q)
        if multiples_answer:
            session.chat_history.append(multiples_answer)
            return multiples_answer

        # Structured financial lookup
        financial = self._try_financial_lookup(session, q)
        if financial:
            session.add_event(EventType.CHAT_QA, agent=self.agent_id, details={"question": q, "tier": "extracted"})
            session.chat_history.append(financial)
            return financial

        # Consensus / estimates
        consensus = self._try_consensus_lookup(session, q)
        if consensus:
            session.chat_history.append(consensus)
            return consensus

        # Agent artifacts (guidance, porter's)
        artifact = self._try_artifact_lookup(session, q)
        if artifact:
            session.chat_history.append(artifact)
            return artifact

        # Transcript Q&A
        transcript = self._try_transcript_qa(session, q)
        if transcript:
            session.chat_history.append(transcript)
            return transcript

        # Data flags / red flags
        if any(kw in lower for kw in ("red flag", "data flag", "anomal", "forensic", "guidance track", "missed guidance")):
            flags_resp = self._try_data_flags(
                session,
                category="guidance" if "guidance" in lower else None,
            )
            if flags_resp:
                session.chat_history.append(flags_resp)
                return flags_resp

        # Pack-grounded LLM (local analysis_pack.md + PDF excerpts) before raw PDF RAG
        pack_llm = self._try_pack_grounded_llm(session, q)
        if pack_llm:
            session.chat_history.append(pack_llm)
            return pack_llm

        # PDF RAG
        pdf_answer = self._try_pdf_rag(session, q)
        if pdf_answer:
            session.chat_history.append(pdf_answer)
            return pdf_answer

        # External search only if enabled — last resort
        if settings.enable_external_search:
            external = self._external_search(session, q)
            session.chat_history.append(external)
            return external

        return ChatResponse(
            answer=(
                "I couldn’t answer from the offline **analysis pack** or uploaded PDFs. "
                "Add the relevant Capital IQ / AR files, click **Reload folders / Drive**, "
                "or enable external search (`IIS_ENABLE_EXTERNAL_SEARCH=true`) for a last-resort web lookup."
            ),
            source_tier=SourceTier.SESSION,
            confidence="low",
            note="No pack / PDF hit; external search disabled",
        )

    def _files_response(self, session: ResearchSession) -> ChatResponse:
        files = session.uploaded_files or ["None"]
        company = session.company.name if session.company else "Unknown"
        return ChatResponse(
            answer=f"Research session for **{company}**.\n\nUploaded files:\n"
            + "\n".join(f"• {f}" for f in files)
            + f"\n\nFinancial line items: {len(session.financial_line_items)}\n"
            f"PDF chunks: {len(session.pdf_chunks)}\n"
            f"Consensus metrics: {len(session.consensus_metrics)}",
            source_tier=SourceTier.SESSION,
            confidence="high",
        )

    def _gates_response(self, session: ResearchSession) -> ChatResponse:
        session.init_gates()
        lines = []
        for gate_id in GateId:
            g = session.gate(gate_id)
            lines.append(f"• {gate_id.value}: **{g.status.value}**")
        return ChatResponse(
            answer="Approval gate status:\n" + "\n".join(lines),
            source_tier=SourceTier.SESSION,
            confidence="high",
        )

    def _assumption_handoff(self, session: ResearchSession, question: str) -> ChatResponse:
        lower = question.lower()
        match = re.search(r"(\d+(?:\.\d+)?)\s*%?", question)

        key_map = [
            (("terminal growth", "terminal g"), "terminal_growth"),
            (("discount rate", "wacc"), "discount_rate"),
            (("revenue growth", "5y growth", "cagr"), "revenue_growth_y1_5"),
            (("ebitda margin", "margin"), "ebitda_margin"),
            (("fcf conversion", "fcf/ebitda", "fcf to ebitda"), "wc_investment_rate"),
            (("wc investment", "working capital investment", "nwc investment"), "wc_investment_rate"),
            (("nwc", "working capital", "net working capital"), "nwc_pct_revenue"),
            (("capex", "capital expenditure"), "capex_pct_revenue"),
            (("tax rate",), "tax_rate"),
        ]
        for phrases, key in key_map:
            if any(p in lower for p in phrases) and match:
                val = float(match.group(1))
                if val > 1:
                    val /= 100
                if key not in {a.key for a in session.assumptions}:
                    continue
                label = next((a.label for a in session.assumptions if a.key == key), key)
                return ChatResponse(
                    answer=(
                        f"Got it — updating **{label}** to **{val:.1%}** and recalculating DCF. "
                        "Check Valuation overview for the new intrinsic value. "
                        "Edit further in chat anytime, then **regenerate report** when ready."
                    ),
                    source_tier=SourceTier.SESSION,
                    handoff={"action": "update_assumption", "key": key, "value": val},
                )

        review = self._get_valuation_review(session)
        if review and review.get("needs_input_count", 0) > 0:
            return self._valuation_review_response(session, question=question)

        return ChatResponse(
            answer=(
                "To co-create DCF assumptions, tell me what to use — e.g. "
                "'use 45% FCF/EBITDA at steady state', 'WC investment 30% of revenue growth', "
                "or '15% 5Y revenue CAGR'. I'll update Gate 4 and recalculate. "
                "You can also edit values directly under **Approval gates → Valuation**."
            ),
            source_tier=SourceTier.SESSION,
        )

    def _get_valuation_review(self, session: ResearchSession) -> dict | None:
        art = next((a for a in session.agent_artifacts if a.artifact_type == "valuation_review"), None)
        if art:
            return art.payload
        v = session.valuation
        if v and v.sensitivities:
            return v.sensitivities.get("valuation_review")
        return None

    def _valuation_review_response(self, session: ResearchSession, question: str = "") -> ChatResponse:
        review = self._get_valuation_review(session)
        if not review or not review.get("items"):
            return ChatResponse(
                answer="No valuation review flags yet. Run **Analyze** first to build the DCF and surface assumption issues.",
                source_tier=SourceTier.SESSION,
            )

        lines = [
            "Before approving Gate 4, let's align on a few DCF inputs. "
            "The model flagged the following based on your financials:\n"
        ]
        for item in review.get("items", []):
            sev = "⚠️" if item.get("severity") == "needs_input" else "ℹ️"
            lines.append(f"{sev} **{item.get('headline')}**")
            lines.append(f"  _{item.get('observation')}_")
            lines.append(f"  **Question:** {item.get('question')}")
            opts = item.get("options") or []
            if opts:
                lines.append("  Options: " + " · ".join(o.get("label", "") for o in opts[:3]))
            if item.get("chat_prompt"):
                lines.append(f"  _Reply in chat: {item['chat_prompt']}_")
            lines.append("")

        needs = review.get("needs_input_count", 0)
        if needs:
            lines.append(
                f"**{needs} item(s) need your input** — edit in Gate 4 or tell me your preferred estimates in chat."
            )

        return ChatResponse(
            answer="\n".join(lines).strip(),
            source_tier=SourceTier.EXTRACTED,
            confidence="high",
            note="Edit DCF assumptions in chat anytime — no Gate 4 approval required",
        )

    def _valuation_response(self, session: ResearchSession) -> ChatResponse:
        review = self._get_valuation_review(session)
        needs_input = review and review.get("needs_input_count", 0) > 0

        v = session.valuation
        if not v or v.intrinsic_value_per_share is None:
            proposed = ""
            if session.assumptions:
                proposed = "\n\nProposed assumptions:\n" + "\n".join(
                    f"• {a.label}: {a.user_value if a.user_value is not None else a.value}"
                    for a in session.assumptions[:6]
                )
            return ChatResponse(
                answer="Valuation not computed yet. Run **Analyze**, then edit assumptions in chat "
                '(e.g. "set WACC to 11%" or "use 14% revenue growth").'
                + proposed,
                source_tier=SourceTier.SESSION,
                confidence="medium",
                note="Chat-editable valuation",
            )

        mos_pct = f"{v.margin_of_safety_pct:.1%}" if v.margin_of_safety_pct is not None else "N/A"
        lines = [
            f"**Intrinsic value (DCF):** ₹{v.intrinsic_value_per_share:,.0f}/share",
            "_Review and tweak via chat — no formal valuation approval step._",
        ]
        if v.market_price:
            lines.append(f"**Live market price:** ₹{v.market_price:,.2f}/share ({v.market_price_source})")
        elif session.market_quote:
            q = session.market_quote
            lines.append(f"**Live market price:** ₹{q.price:,.2f}/share ({q.exchange} via {q.source})")
        lines.append(f"**Margin of safety vs live price:** {mos_pct}")
        if v.current_price:
            lines.append(f"**Consensus target:** ₹{float(v.current_price):,.0f}/share")
        lines.append(f"**Method:** {v.method}")
        if session.assumptions:
            lines.append("\n**Current assumptions** (say “set X to Y%” to change):")
            for a in session.assumptions[:8]:
                val = a.user_value if a.user_value is not None else a.value
                if isinstance(val, float) and a.unit == "decimal":
                    shown = f"{val:.2%}"
                else:
                    shown = str(val)
                mark = " ✎" if a.user_edited else ""
                lines.append(f"• {a.label}: {shown}{mark}")
        if needs_input:
            lines.append(
                f"\n⚠️ **{review['needs_input_count']} assumption(s)** flagged for judgment — "
                "ask *what valuation flags do you see?* or edit the values in chat."
            )

        return ChatResponse(
            answer="\n".join(lines),
            sources=[{"type": "valuation", "assumptions": v.assumptions}],
            source_tier=SourceTier.EXTRACTED,
            confidence="high",
            note="Chat-editable valuation (no Gate 4 required)",
        )

    def _market_price_response(self, session: ResearchSession) -> ChatResponse:
        from iis.finance.market_data import is_reliable_quote

        q = session.market_quote if is_reliable_quote(session.market_quote) else None
        v = session.valuation
        if q:
            src = {"price_sheet": "price sheet (lean server)", "yfinance": "NSE/BSE feed (Yahoo)"}.get(q.source, q.source)
            lines = [
                f"**Market price:** ₹{q.price:,.2f}/share ({src})",
                f"**Exchange:** {q.exchange}",
                f"**Symbol:** {q.symbol}",
                f"**Confidence:** {q.confidence}",
                f"**Source:** {q.source}",
                f"**Fetched:** {q.fetched_at.isoformat()}",
            ]
            if q.disclaimer:
                lines.append(f"**Note:** {q.disclaimer}")
            return ChatResponse(
                answer="\n".join(lines),
                sources=[{"type": "market_data", "source": q.source}],
                source_tier=SourceTier.EXTRACTED,
                confidence="high",
                note="Price from price sheet / exchange feed only — never inferred",
            )
        if v and v.market_price:
            return ChatResponse(
                answer=f"**Market price:** ₹{v.market_price:,.2f}/share (source: {v.market_price_source})",
                source_tier=SourceTier.SESSION,
                confidence="high",
            )
        return ChatResponse(
            answer="**Market price unavailable** — the price sheet and NSE/BSE feed returned nothing. "
            "Check the symbol in the price sheet, then click **Refresh price**.",
            source_tier=SourceTier.SESSION,
            confidence="low",
        )

    def _try_financial_lookup(
        self,
        session: ResearchSession,
        question: str,
        *,
        params: dict | None = None,
        force: bool = False,
    ) -> ChatResponse | None:
        params = params or {}
        lower = question.lower()
        if "segment" in lower and not force:
            return None

        line_item = params.get("metric")
        if line_item:
            line_item = self.METRIC_ALIASES.get(line_item.lower(), line_item.replace(" ", "_"))
        else:
            for alias, canonical in self.METRIC_ALIASES.items():
                if alias in lower:
                    line_item = canonical
                    break

        if not line_item:
            return None

        period_type = None
        pt = params.get("period_type")
        if pt == "estimate":
            period_type = PeriodType.ESTIMATE
        elif pt == "actual":
            period_type = PeriodType.ACTUAL
        elif "consensus" in lower or "estimate" in lower:
            period_type = PeriodType.ESTIMATE
        items = query_financials(session.financial_line_items, line_item=line_item, period_type=period_type)

        if not items:
            items = query_financials(session.financial_line_items, line_item_contains=line_item.replace("_", " "))

        if not items:
            latest = get_latest_actual(session.financial_line_items, line_item)
            if latest:
                items = [latest]
            else:
                return None

        items.sort(key=lambda x: x.period_end or "", reverse=True)
        item = items[0]
        tier = SourceTier.APPROVED if session.is_gate_approved(GateId.EXTRACTED_FINANCIALS) else SourceTier.EXTRACTED
        note = None if tier == SourceTier.APPROVED else "From extracted uploads — verify in chat if anything looks off."

        value_str = f"{item.value:,.0f}" if item.value is not None else "N/A"
        return ChatResponse(
            answer=f"**{item.line_item_raw}** for period **{item.period_label}**: "
            f"**{value_str}** {item.currency} millions",
            sources=[
                {
                    "file": item.source.file,
                    "sheet": item.source.sheet,
                    "row": item.source.row,
                    "tier": tier.value,
                }
            ],
            source_tier=tier,
            confidence="high",
            note=note,
        )

    def _try_growth_lookup(
        self,
        session: ResearchSession,
        question: str,
        *,
        params: dict | None = None,
        force: bool = False,
    ) -> ChatResponse | None:
        from iis.finance.growth import build_growth_summary, compute_cagr_from_series

        params = params or {}
        lower = question.lower()
        if not force:
            if not any(kw in lower for kw in ("growth", "cagr", "projection", "forecast", "outlook")):
                if not ("revenue" in lower and any(kw in lower for kw in ("last", "year", "5", "3", "historical", "trend"))):
                    return None

        if not session.financial_line_items:
            return None

        summary = build_growth_summary(session.financial_line_items)
        actuals = summary["historical"]
        estimates = summary["estimates"]
        if not actuals:
            return None

        tier = SourceTier.APPROVED if session.is_gate_approved(GateId.EXTRACTED_FINANCIALS) else SourceTier.EXTRACTED
        note = None if tier == SourceTier.APPROVED else "From extracted uploads — verify in chat if anything looks off."

        lines = ["**Revenue growth (Capital IQ, INR millions)**", ""]

        lines.append("**Historical revenue:**")
        for p in actuals[-6:]:
            yoy = f", YoY **{p.yoy_growth:.1%}**" if p.yoy_growth is not None else ""
            lines.append(f"- {p.period_label[:40]}: **{p.value_mn:,.0f}**{yoy}")

        hist_years = int(params.get("historical_years") or 5)
        cagr5 = summary.get("cagr_5y")
        cagr3 = summary.get("cagr_3y")
        cagr_hist = compute_cagr_from_series(actuals, hist_years) if actuals else None
        if cagr_hist is not None:
            lines.append(f"\n**{hist_years}-year revenue CAGR:** **{cagr_hist:.1%}**")
        elif cagr5 is not None:
            lines.append(f"\n**5-year revenue CAGR:** **{cagr5:.1%}**")
        if cagr3 is not None and hist_years != 3:
            lines.append(f"**3-year revenue CAGR:** **{cagr3:.1%}**")

        reported = summary.get("reported_yoy") or []
        if reported:
            lines.append("\n**Capital IQ reported YoY growth (Key Stats):**")
            for period, g in reported[-7:]:
                fy = period[:4]
                lines.append(f"- FY{fy}: **{g:.1%}**")

        if estimates:
            lines.append("\n**Consensus revenue projections:**")
            fwd_years = int(params.get("forward_years") or 3)
            for p in estimates[:fwd_years]:
                yoy = f", implied YoY **{p.yoy_growth:.1%}**" if p.yoy_growth is not None else ""
                lines.append(f"- {p.period_label[:40]}: **{p.value_mn:,.0f}**{yoy}")

        if estimates and len(estimates) >= 2:
            fwd_rates = [p.yoy_growth for p in estimates[:3] if p.yoy_growth is not None]
            if len(fwd_rates) >= 2:
                lines.append(
                    f"\n**Forward growth (consensus):** "
                    f"**{fwd_rates[0]:.1%}** YoY ({estimates[0].period_label[:20]}), "
                    f"then **{fwd_rates[1]:.1%}–{fwd_rates[-1]:.1%}** for outer years"
                )

        fwd_years = int(params.get("forward_years") or 3)
        kpis = []
        if cagr_hist is not None:
            kpis.append({"label": f"{hist_years}Y CAGR", "value": f"{cagr_hist:.1%}", "highlight": True})
        elif cagr5 is not None:
            kpis.append({"label": "5Y CAGR", "value": f"{cagr5:.1%}", "highlight": True})
        if cagr3 is not None:
            kpis.append({"label": "3Y CAGR", "value": f"{cagr3:.1%}"})
        if estimates and estimates[-1].yoy_growth is not None:
            kpis.append({"label": "Forward YoY", "value": f"~{estimates[-1].yoy_growth:.1%}"})

        structured = {
            "type": "revenue_growth",
            "title": "Revenue Growth",
            "subtitle": "Capital IQ · INR millions",
            "kpis": kpis,
            "historical": [
                {
                    "period": p.period_label[:32],
                    "value_mn": p.value_mn,
                    "yoy_pct": round(p.yoy_growth * 100, 1) if p.yoy_growth is not None else None,
                }
                for p in actuals[-6:]
            ],
            "estimates": [
                {
                    "period": p.period_label[:32],
                    "value_mn": p.value_mn,
                    "yoy_pct": round(p.yoy_growth * 100, 1) if p.yoy_growth is not None else None,
                }
                for p in estimates[:fwd_years]
            ],
        }

        return ChatResponse(
            answer="\n".join(lines),
            sources=[{"type": "capital_iq", "sheet": "Income Statement / Key Stats"}],
            source_tier=tier,
            confidence="high",
            note=note,
            structured=structured,
        )

    def _try_multiples_lookup(
        self,
        session: ResearchSession,
        question: str,
        *,
        params: dict | None = None,
        force: bool = False,
    ) -> ChatResponse | None:
        from iis.finance.multiples import build_multiples_response, format_multiples_answer

        params = params or {}
        lower = question.lower()
        if not force:
            if not any(
                kw in lower
                for kw in (
                    "forward pe", "forward p/e", "p/e", "price to earnings",
                    "ev/ebitda", "ebitda multiple", "multiple", "ntm pe", "peg", "p/bv",
                )
            ):
                return None
            if any(kw in lower for kw in ("target price", "price target", "analyst target")):
                return None

        payload = build_multiples_response(
            session,
            period=params.get("period"),
            multiple_type=params.get("multiple") or "pe",
        )
        if not payload:
            return ChatResponse(
                answer="Forward P/E and valuation multiples require the **Capital IQ Estimates** export (Multiples sheet). Upload the Estimates `.xls` file.",
                source_tier=SourceTier.SESSION,
                confidence="low",
            )

        tier = SourceTier.APPROVED if session.is_gate_approved(GateId.EXTRACTED_FINANCIALS) else SourceTier.EXTRACTED
        note = None if tier == SourceTier.APPROVED else "From extracted uploads — verify in chat if anything looks off."

        return ChatResponse(
            answer=format_multiples_answer(payload),
            sources=[{"type": "capital_iq", "sheet": "Multiples"}],
            source_tier=tier,
            confidence="high",
            note=note,
            structured=payload,
        )

    def _try_ratio_lookup(
        self,
        session: ResearchSession,
        question: str,
        *,
        params: dict | None = None,
        force: bool = False,
    ) -> ChatResponse | None:
        from iis.finance.ratios import RATIO_CATALOG, find_ratio_by_keyword, format_ratio_value

        params = params or {}
        lower = question.lower()
        if not force:
            if not any(kw in lower for kw in ("ratio", "margin", "roe", "roa", "roce", "turnover", "coverage", "liquidity", "leverage")):
                if "financial ratio" not in lower and "debt to equity" not in lower:
                    return None

        ratio_key = params.get("ratio") or find_ratio_by_keyword(question)
        if params.get("all"):
            ratio_key = None
            lower = "all financial ratio"

        # Capital IQ computed ratios (Gate 3 / after analyze)
        norm = next((a for a in session.agent_artifacts if a.artifact_type == "normalized_financials"), None)
        if norm and ratio_key:
            latest = norm.payload.get("latest_ratios") or {}
            period = norm.payload.get("latest_period", "latest")
            val = latest.get(ratio_key)
            if val is not None:
                meta = RATIO_CATALOG.get(ratio_key, {})
                tier = SourceTier.APPROVED if session.is_gate_approved(GateId.NORMALIZATION) else SourceTier.EXTRACTED
                return ChatResponse(
                    answer=f"**{meta.get('label', ratio_key)}** ({period}): **{format_ratio_value(ratio_key, val)}**\n"
                    f"Formula: {meta.get('formula', 'N/A')}\n"
                    f"Source: Capital IQ financials (computed)",
                    sources=[{"type": "normalized_financials", "period": period}],
                    source_tier=tier,
                    confidence="high",
                    note=None if tier == SourceTier.APPROVED else "Pending Gate 3 (normalization) approval",
                )

        if norm and ("all ratio" in lower or "financial ratio" in lower):
            catalog = norm.payload.get("ratio_catalog") or []
            if catalog:
                lines = [f"**Financial ratios** ({norm.payload.get('latest_period', 'latest')}):", ""]
                by_cat: dict[str, list] = {}
                for r in catalog:
                    by_cat.setdefault(r.get("category", "Other"), []).append(r)
                for cat, items in by_cat.items():
                    lines.append(f"**{cat}**")
                    for r in items:
                        lines.append(f"- {r['label']}: {format_ratio_value(r['name'], r['value'])}")
                    lines.append("")
                tier = SourceTier.APPROVED if session.is_gate_approved(GateId.NORMALIZATION) else SourceTier.EXTRACTED
                return ChatResponse(
                    answer="\n".join(lines),
                    source_tier=tier,
                    confidence="high",
                )

        # Annual report extracted ratios
        ar = next((a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"), None)
        if ar and ratio_key:
            for filename, fin in ar.payload.get("financial_statements", {}).items():
                ratios = fin.get("computed_ratios") or {}
                if ratio_key in ratios and ratios[ratio_key] is not None:
                    meta = RATIO_CATALOG.get(ratio_key, {})
                    return ChatResponse(
                        answer=f"**{meta.get('label', ratio_key)}** (from annual report): "
                        f"**{format_ratio_value(ratio_key, ratios[ratio_key])}**\n"
                        f"File: {filename}\n"
                        f"Model: {ar.payload.get('model_used', 'Ollama')}",
                        sources=[{"file": filename, "type": "annual_report"}],
                        source_tier=SourceTier.AGENT,
                        confidence="medium",
                        note="From AR financial statement extraction — verify at Gate 2",
                    )

        return None

    def _try_consensus_lookup(
        self,
        session: ResearchSession,
        question: str,
        *,
        params: dict | None = None,
        force: bool = False,
    ) -> ChatResponse | None:
        params = params or {}
        lower = question.lower()
        metric_param = params.get("metric", "")

        # Never route P/E questions to target price
        if any(kw in lower for kw in ("forward pe", "forward p/e", "p/e", "price to earnings", "ntm pe")):
            return None

        if not force and "target price" not in lower and "consensus" not in lower and "eps" not in lower:
            if metric_param not in ("target_price", "eps", "revenue"):
                return None

        summary = next(
            (a.payload for a in session.agent_artifacts if a.artifact_type == "estimates_summary"),
            {},
        )
        if ("target price" in lower or metric_param == "target_price") and summary.get("target_price_mean"):
            return ChatResponse(
                answer=f"Consensus **target price**: ₹{summary['target_price_mean']:,.0f} "
                f"(median ₹{summary.get('target_price_median', 'N/A')}, "
                f"{summary.get('target_price_num_estimates', '?')} analysts)",
                sources=[{"file": "estimates", "sheet": "Consensus"}],
                source_tier=SourceTier.EXTRACTED,
                confidence="high",
            )

        if "eps" in lower or metric_param == "eps":
            matches = [m for m in session.consensus_metrics if "eps" in m.metric.lower()]
            if matches:
                m = matches[0]
                return ChatResponse(
                    answer=f"Consensus **{m.metric}** ({m.period}): **{m.mean:,.2f}**",
                    sources=[{"file": m.source.file, "sheet": m.source.sheet}],
                    source_tier=SourceTier.EXTRACTED,
                    confidence="high",
                )

        if metric_param == "revenue" or ("consensus" in lower and "revenue" in lower):
            return self._try_growth_lookup(session, question, params={"forward_years": 3}, force=True)

        return None

    def _try_segment_lookup(
        self,
        session: ResearchSession,
        question: str,
        *,
        params: dict | None = None,
        force: bool = False,
    ) -> ChatResponse | None:
        lower = question.lower()
        segments = ["jewellery", "watches", "eyecare", "others"]
        segment = (params or {}).get("segment") if params else None
        if segment:
            segment = segment.lower()
        else:
            segment = next((s for s in segments if s in lower), None)
        if not segment:
            return None

        items = query_financials(
            session.financial_line_items,
            statement="segments",
            line_item=segment,
        )
        if not items:
            return None

        items.sort(key=lambda x: x.period_end or "", reverse=True)
        item = items[0]
        tier = SourceTier.APPROVED if session.is_gate_approved(GateId.EXTRACTED_FINANCIALS) else SourceTier.EXTRACTED
        val = item.value if item.value is not None else 0
        label = item.period_label or item.period_end or "latest"
        return ChatResponse(
            answer=f"**{segment.title()}** segment revenue ({label}): "
            f"**{val:,.0f}** INR millions",
            sources=[{"file": item.source.file, "sheet": "Segments", "row": item.source.row}],
            source_tier=tier,
            confidence="high",
            note=None if tier == SourceTier.APPROVED else "From extracted uploads — verify in chat if needed",
        )

    def _try_artifact_lookup(
        self,
        session: ResearchSession,
        question: str,
        *,
        artifact_type: str | None = None,
    ) -> ChatResponse | None:
        lower = question.lower()
        if artifact_type == "guidance" or (not artifact_type and ("guidance" in lower or "management say" in lower)):
            return self._try_guidance_response(session, question)

        if artifact_type == "annual_report" or (
            not artifact_type and ("annual report" in lower or "directors report" in lower or "md&a" in lower)
        ):
            art = next(
                (a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"),
                None,
            )
            if art:
                summary = art.payload.get("summary") or "Annual report sections analyzed."
                return ChatResponse(
                    answer=summary,
                    sources=[{"file": f} for f in art.provenance],
                    source_tier=SourceTier.AGENT,
                    confidence="medium" if art.confidence > 0.5 else "low",
                    note="From section-by-section annual report reading (Gate 2)",
                )

        if artifact_type == "porters" or (not artifact_type and ("porter" in lower or "competition" in lower or "competitive" in lower)):
            art = next((a for a in session.agent_artifacts if a.artifact_type == "porters_five_forces"), None)
            if art:
                text = "\n\n".join(f"**{k.replace('_', ' ').title()}:** {v}" for k, v in art.payload.items())
                return ChatResponse(
                    answer=text,
                    source_tier=SourceTier.AGENT,
                    confidence="medium" if art.confidence > 0.5 else "low",
                )
        return None

    def _try_capex_analysis(
        self, session: ResearchSession, *, params: dict | None = None
    ) -> ChatResponse | None:
        recent = int((params or {}).get("recent_years") or 3)
        payload = build_capex_analysis(session.financial_line_items, recent_years=recent)
        if not payload:
            return ChatResponse(
                answer=(
                    "No capital expenditure line items found in uploaded Capital IQ financials. "
                    "Confirm the Financials workbook includes cash-flow CapEx (or CapEx in key stats)."
                ),
                source_tier=SourceTier.SESSION,
                confidence="low",
                note="capex_analysis: empty series",
            )
        return ChatResponse(
            answer=format_capex_analysis(payload),
            sources=[{"type": "capital_iq", "metric": "capital_expenditure"}],
            source_tier=SourceTier.EXTRACTED,
            confidence="high",
            structured={"type": "capex_analysis", **{k: v for k, v in payload.items() if k != "series"}},
            note="Python module from Capital IQ (analysis pack)",
        )

    def _try_capacity_from_pack(self, session: ResearchSession, question: str) -> ChatResponse | None:
        pack = load_analysis_pack(session)
        if not pack:
            try:
                write_analysis_pack(session)
                pack = load_analysis_pack(session)
            except Exception:
                pack = None
        if not pack:
            return ChatResponse(
                answer="No analysis pack yet — ingest Capital IQ / AR files first, then ask again.",
                source_tier=SourceTier.SESSION,
                confidence="low",
            )
        ctx = extract_pack_context(pack, question)
        return ChatResponse(
            answer=f"**Capacity / utilization (from analysis pack):**\n\n{ctx}",
            source_tier=SourceTier.EXTRACTED,
            confidence="medium",
            note="Offline pack excerpts — no web",
            structured={"type": "capacity_utilization"},
        )

    def _try_pack_grounded_llm(self, session: ResearchSession, question: str) -> ChatResponse | None:
        """Answer novel questions using analysis_pack.md (+ light PDF) as the only ground truth."""
        pack = load_analysis_pack(session)
        if not pack and settings.prefer_analysis_pack:
            try:
                write_analysis_pack(session)
                pack = load_analysis_pack(session)
            except Exception:
                pack = None
        if not pack or not self.llm.is_available():
            return None

        ctx = extract_pack_context(pack, question)
        pdf_bits = ""
        if session.pdf_chunks:
            hits = (
                self.embedder.semantic_search(session, question, limit=2)
                if settings.enable_semantic_search
                else search_pdf_chunks(session.pdf_chunks, question, limit=2)
            )
            if hits:
                pdf_bits = "\n\n".join(f"[{h.file} p.{h.page}] {h.text[:500]}" for h in hits)

        company = session.company.name if session.company else "the company"
        try:
            answer = self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a buy-side equity analyst. Answer ONLY from the offline analysis pack "
                            "and optional PDF excerpts. If the pack does not contain the answer, say clearly "
                            "what is missing. Do not invent numbers. Cite section names when useful."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Company: {company}\n\nAnalysis pack excerpts:\n{ctx}\n\n"
                            + (f"PDF excerpts:\n{pdf_bits}\n\n" if pdf_bits else "")
                            + f"Question: {question}"
                        ),
                    },
                ]
            )
        except Exception:
            return None
        answer = self._verified(session, answer, [ctx, pdf_bits], "chat_pack")
        if not answer:
            return None

        return ChatResponse(
            answer=answer,
            sources=[{"type": "analysis_pack"}],
            source_tier=SourceTier.EXTRACTED,
            confidence="medium",
            note="Grounded on local analysis_pack.md (LLM synthesis)",
        )

    def _try_data_flags(self, session: ResearchSession, *, category: str | None = None) -> ChatResponse | None:
        from iis.finance.flags import compute_data_flags, format_flags_for_chat, flags_to_payload

        if not session.financial_line_items and not session.guidance_records:
            return None

        flag_objs = compute_data_flags(session)
        if category:
            flag_objs = [f for f in flag_objs if f.category == category]

        answer = format_flags_for_chat(flag_objs)
        payload = flags_to_payload(flag_objs)

        return ChatResponse(
            answer=answer,
            sources=[{"type": "data_flags", "count": payload["total"]}],
            source_tier=SourceTier.EXTRACTED,
            confidence="high",
            structured={
                "type": "data_flags",
                "total": payload["total"],
                "review_count": payload["review_count"],
                "flags": payload["flags"][:15],
            },
            note="Each flag cites figures you can verify in Capital IQ or the annual report. No composite scores.",
        )

    def _try_guidance_response(self, session: ResearchSession, question: str) -> ChatResponse | None:
        """Prior guidance vs actuals + concall reflection."""
        from iis.finance.guidance_track import guidance_track_markdown, guidance_track_summary, guidance_track_rows

        lower = (question or "").lower()
        rows = guidance_track_rows(session.guidance_records, session.financial_line_items)
        summary = guidance_track_summary(rows)
        parts: list[str] = [f"**{summary['headline']}**", ""]
        parts.extend(guidance_track_markdown(session.guidance_records, session.financial_line_items))

        art = next((a for a in session.agent_artifacts if a.artifact_type == "guidance_summary"), None)
        if art:
            p = art.payload
            if p.get("current_outlook") and any(
                k in lower for k in ("outlook", "concall", "transcript", "now", "current", "saying")
            ):
                parts.append("### Current outlook (concall)")
                parts.append(str(p["current_outlook"]))
                parts.append("")
            reflections = p.get("guidance_reflections") or []
            if reflections:
                parts.append("### Concall vs prior guidance")
                for ref in reflections[:6]:
                    parts.append(
                        f"- **{ref.get('fiscal_year') or '?'} {ref.get('metric') or ''}** "
                        f"({ref.get('assessment') or 'unclear'}): "
                        f"{ref.get('transcript_evidence') or ref.get('prior_guidance') or ''}"
                    )
                parts.append("")
            elif p.get("commentary"):
                parts.append("### Commentary")
                parts.append(str(p["commentary"])[:900])
                parts.append("")
            if p.get("themes"):
                parts.append("**Themes:** " + ", ".join(str(t) for t in p["themes"][:6]))

        if not rows and not art:
            return None

        return ChatResponse(
            answer="\n".join(parts),
            source_tier=SourceTier.EXTRACTED,
            confidence="high" if rows else "medium",
            note="Guidance track from Capital IQ Estimates (+ Financials backfill); concall via Analyze",
            sources=[{"type": "guidance_track", "rows": len(rows)}],
            structured={"type": "guidance_track", "summary": summary, "rows": rows[:12]},
        )

    @staticmethod
    def _is_open_research_question(question: str) -> bool:
        """Multi-part / analytical asks that need Hive → Python tools → answer."""
        q = (question or "").strip()
        lower = q.lower()
        if len(q) < 40:
            return False
        signals = 0
        if re.search(r"(?m)^\s*\d+[.)]", q) or q.count("?") >= 2:
            signals += 2
        if any(
            k in lower
            for k in (
                "peer",
                "competitor",
                "customer",
                "client",
                "margin",
                "roce",
                "segment",
                "vs ",
                " versus ",
                "why ",
                "how does",
                "what are",
                "who are",
                "expected",
                "order book",
                "pipeline",
            )
        ):
            signals += 1
        if any(k in lower for k in ("capex", "pharma", "electronics", "csm", "chemistry")):
            signals += 1
        return signals >= 2

    def _try_library_find_response(self, session: ResearchSession, question: str) -> ChatResponse | None:
        """Answer 'do we already have files for X?' using the company file index.

        Only for short, explicit library lookups — never for research questions.
        """
        lower = question.lower()
        if self._is_open_research_question(question):
            return None
        if len((question or "").strip()) > 120:
            return None
        if not any(
            k in lower
            for k in (
                "already have",
                "on file for",
                "uploaded for",
                "library for",
                "files for",
                "do we have files",
                "do we already have",
                "find files for",
                "what files for",
                "what files do we have",
            )
        ):
            return None
        from iis.library.file_index import find_in_index
        from iis.library.store import get_company_library_store

        # Prefer explicit company name after "for "
        q = question
        for sep in (" for ", " of ", " on "):
            if sep in lower:
                q = question[lower.index(sep) + len(sep) :].strip(" ?.")
                break
        # Reject if "company name" still looks like a full research ask
        if len(q) > 80 or self._is_open_research_question(q):
            return None
        if len(q) < 2:
            q = (session.company.name if session.company else "") or ""
        result = find_in_index(q, store=get_company_library_store())
        if not result.get("matched"):
            return ChatResponse(
                answer=(
                    f"No saved library match for **{q}**. "
                    "Upload Capital IQ Financials (+ Estimates); they will be indexed under that company "
                    "and reused next time without re-upload."
                ),
                source_tier=SourceTier.SESSION,
                confidence="medium",
            )
        lines = [f"Found **{len(result['companies'])}** company library(ies) for “{q}”:"]
        for c in result["companies"][:5]:
            lines.append(
                f"\n**{c.get('name')}** (`{c.get('company_key')}`) — "
                f"{c.get('document_count', 0)} files, {c.get('line_items', 0)} line items"
            )
            for d in (c.get("documents") or [])[:8]:
                lines.append(f"  • {d.get('filename')} ({d.get('doc_kind')})")
        lines.append(
            "\nSay **Continue** with that company name to load them, or keep researching "
            "the current name — peer packs stay indexed for comparison."
        )
        return ChatResponse(
            answer="\n".join(lines),
            source_tier=SourceTier.SESSION,
            confidence="high",
            note="Company file index",
            structured={"type": "library_find", **result},
        )

    def _try_transcript_qa(
        self, session: ResearchSession, question: str, *, params: dict | None = None, force: bool = False
    ) -> ChatResponse | None:
        if not session.pdf_documents:
            return None
        topic = (params or {}).get("topic") or question
        qa_hits = search_transcript_qa(session.pdf_documents, topic, limit=5)
        if not qa_hits:
            return None

        tier = SourceTier.APPROVED if session.is_gate_approved(GateId.TEXT_INFERENCES) else SourceTier.EXTRACTED
        lines = []
        sources = []
        for qa in qa_hits:
            lines.append(f"**Q:** {qa.question}\n**A:** {qa.answer[:800]}")
            sources.append({"file": qa.file, "page": qa.page, "type": "transcript_qa"})

        answer = "\n\n".join(lines)
        if self.llm.is_available() and len(qa_hits) > 1:
            try:
                context = "\n\n".join(f"Q: {q.question}\nA: {q.answer}" for q in qa_hits)
                summary = self.llm.chat(
                    [
                        {
                            "role": "system",
                            "content": "Summarize earnings call Q&A excerpts to answer the analyst question. Cite speakers if known.",
                        },
                        {
                            "role": "user",
                            "content": f"Question: {question}\n\nTranscript excerpts:\n{context[:6000]}",
                        },
                    ]
                )
                answer = self._verified(session, summary, [context[:6000]], "chat_transcript") or answer
            except Exception:
                pass

        return ChatResponse(
            answer=answer,
            sources=sources,
            source_tier=tier,
            confidence="high" if tier == SourceTier.APPROVED else "medium",
            note=None if tier == SourceTier.APPROVED else "Transcript Q&A pending Gate 2 approval",
            structured={"type": "transcript_qa", "count": len(qa_hits)},
        )

    def _try_pdf_rag(self, session: ResearchSession, question: str) -> ChatResponse | None:
        if not session.pdf_chunks:
            return None

        from iis.agents.annual_report_reader import get_annual_report_context

        ar_context = get_annual_report_context(session, max_chars=3000)
        chunks = (
            self.embedder.semantic_search(session, question, limit=4)
            if settings.enable_semantic_search
            else search_pdf_chunks(session.pdf_chunks, question, limit=4)
        )
        if not chunks and not ar_context:
            return None

        if self.llm.is_available():
            context_parts = []
            if ar_context:
                context_parts.append(f"Annual report analysis:\n{ar_context}")
            if chunks:
                context_parts.append(
                    "\n\n".join(f"[{c.file} p.{c.page}] {c.text}" for c in chunks)
                )
            context = "\n\n".join(context_parts)
            company = session.company.name if session.company else "the company"
            try:
                answer = self.llm.chat(
                    [
                        {
                            "role": "system",
                            "content": "Answer using ONLY the provided excerpts and annual report analysis. "
                            "Cite page numbers. If insufficient, say so. You are a buy-side analyst.",
                        },
                        {
                            "role": "user",
                            "content": f"Company: {company}\n\nContext:\n{context}\n\nQuestion: {question}",
                        },
                    ]
                )
                answer = self._verified(session, answer, [context], "chat_pdf_rag")
                if not answer:
                    raise ValueError("nothing verifiable")
                tier = SourceTier.APPROVED if session.is_gate_approved(GateId.TEXT_INFERENCES) else SourceTier.EXTRACTED
                return ChatResponse(
                    answer=answer,
                    sources=[{"file": c.file, "page": c.page} for c in chunks],
                    source_tier=tier,
                    confidence="medium",
                    note=None if tier == SourceTier.APPROVED else "PDF inference pending Gate 2 approval",
                )
            except Exception:
                pass

        return ChatResponse(
            answer=(chunks[0].text[:600] + "...") if chunks else (ar_context[:600] + "..."),
            sources=[{"file": chunks[0].file, "page": chunks[0].page}] if chunks else [],
            source_tier=SourceTier.EXTRACTED,
            confidence="low",
        )

    def _external_search(self, session: ResearchSession, question: str) -> ChatResponse:
        company = session.company.name if session.company else ""
        query = f"{company} {question} India stock BSE NSE"
        results = search_web(query)

        if not results:
            return ChatResponse(
                answer="This information was **not found** in your uploaded files or via external search.",
                source_tier=SourceTier.EXTERNAL,
                confidence="low",
                note="No results from internal or external sources",
            )

        context = format_external_results(results)
        answer_body = context

        if self.llm.is_available():
            try:
                answer_body = self.llm.chat(
                    [
                        {
                            "role": "system",
                            "content": "Synthesize a concise answer from web search results. "
                            "Always note these are external sources. Include URLs.",
                        },
                        {"role": "user", "content": f"Question: {question}\n\nSearch results:\n{context}"},
                    ]
                )
            except Exception:
                answer_body = context

        fact = ExternalFact(question=question, answer=answer_body, urls=[r.url for r in results])
        session.external_facts.append(fact)
        session.add_event(EventType.EXTERNAL_FACT, agent=self.agent_id, details={"urls": fact.urls})

        return ChatResponse(
            answer=f"⚠ **External query — not from your uploaded research pack**\n\n{answer_body}",
            sources=[{"url": r.url, "title": r.title, "tier": "external"} for r in results],
            source_tier=SourceTier.EXTERNAL,
            confidence="low",
            note="Verify independently before including in research note",
        )
