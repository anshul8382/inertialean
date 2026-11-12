from __future__ import annotations

import threading
import time
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from iis.finance.waiting_room import STAGE_COPY, build_waiting_room
from iis.models.enums import EventType, GateId, GateStatus, SessionStatus
from iis.models.evidence import AnalysisJob
from iis.models.session import ResearchSession


class AnalysisJobRunner:
    """Run analyze pipeline in a background thread with live progress for the UI."""

    STEPS = [s[0] for s in STAGE_COPY]

    def __init__(self, orchestrator) -> None:
        self.orch = orchestrator
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._jobs: dict[str, AnalysisJob] = {}

    def start(self, session_id: str, owner_id: str | None = None) -> AnalysisJob:
        from iis.storage.session_store import DatabaseSessionStore, get_session_store

        store = get_session_store()
        job_id = str(uuid4())
        if isinstance(store, DatabaseSessionStore):
            try:
                job = store.repository.create_job(session_id, steps=self.STEPS)
                job_id = job.id
            except Exception:
                job = AnalysisJob(id=job_id, session_id=session_id, steps=list(self.STEPS))
        else:
            job = AnalysisJob(id=job_id, session_id=session_id, steps=list(self.STEPS))

        with self._lock:
            self._jobs[job.id] = job

        def _run() -> None:
            job.status = "running"
            job.started_at = datetime.utcnow()
            self._publish(store, job)
            try:
                self._run_pipeline(job, session_id, owner_id=owner_id)
                if job.status == "aborted":
                    self._publish(store, job)
                    return
                if job.status == "failed":
                    self._publish(store, job)
                    return
                job.status = "complete"
                job.progress = 1.0
                job.current_step = "done"
                job.message = "Full analysis + report ready"
                job.finished_at = datetime.utcnow()
                self._publish(store, job)
            except Exception as exc:
                if job.status == "aborted":
                    self._publish(store, job)
                    return
                job.status = "failed"
                job.error = str(exc)
                job.message = f"Failed: {exc}"
                job.finished_at = datetime.utcnow()
                self._publish(store, job)

        thread = threading.Thread(target=_run, daemon=True, name=f"iis-analyze-{job.id[:8]}")
        with self._lock:
            self._threads[job.id] = thread
        thread.start()
        return job

    def _publish(self, store, job: AnalysisJob) -> None:
        with self._lock:
            self._jobs[job.id] = job.model_copy(deep=True)
        from iis.storage.session_store import DatabaseSessionStore

        if isinstance(store, DatabaseSessionStore):
            try:
                store.repository.update_job(job)
            except Exception:
                pass

    def get_job(self, job_id: str) -> AnalysisJob | None:
        with self._lock:
            mem = self._jobs.get(job_id)
            if mem:
                return mem.model_copy(deep=True)
        from iis.storage.session_store import DatabaseSessionStore, get_session_store

        store = get_session_store()
        if isinstance(store, DatabaseSessionStore):
            return store.repository.get_job(job_id)
        return None

    def abort(self, job_id: str) -> bool:
        """Force-stop a running analysis: abort in-flight LLM + mark job aborted."""
        from iis.llm.gate import get_llm_gate

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status not in ("running", "pending", "queued"):
                return False
            job.status = "aborted"
            job.error = "Aborted by user"
            job.message = "Aborted — stopping after current LLM call (if any)"
            job.phase = "aborted"
            job.llm_aborted = True
            job.finished_at = datetime.utcnow()
            snap = job.model_copy(deep=True)
        get_llm_gate().request_abort(job_id)
        from iis.storage.session_store import get_session_store

        try:
            self._publish(get_session_store(), snap)
        except Exception:
            pass
        return True

    def _apply_llm_stats(self, job: AnalysisJob, stats: dict[str, Any] | None = None) -> None:
        from iis.llm.gate import get_llm_gate

        s = stats if stats is not None else get_llm_gate().snapshot()
        job.llm_calls_total = int(s.get("llm_calls_total") or 0)
        job.llm_calls_ok = int(s.get("llm_calls_ok") or 0)
        job.llm_calls_fail = int(s.get("llm_calls_fail") or 0)
        job.llm_prompt_tokens = int(s.get("llm_prompt_tokens") or 0)
        job.llm_completion_tokens = int(s.get("llm_completion_tokens") or 0)
        job.llm_tokens_total = int(s.get("llm_tokens_total") or 0)
        job.llm_tokens_estimated = bool(s.get("llm_tokens_estimated"))
        job.llm_by_module = dict(s.get("llm_by_module") or {})
        job.llm_current_module = s.get("llm_current_module")
        job.llm_current_elapsed_sec = s.get("llm_current_elapsed_sec")
        job.llm_last_prompt_chars = s.get("llm_last_prompt_chars")
        job.llm_last_latency_sec = s.get("llm_last_latency_sec")
        job.llm_last_error = s.get("llm_last_error")
        job.llm_aborted = bool(s.get("llm_aborted"))

    def _set_step(
        self,
        store,
        job: AnalysisJob,
        session: ResearchSession,
        step_id: str,
        message: str,
        *,
        progress: float,
        phase: str | None = None,
    ) -> None:
        now = datetime.utcnow()
        if job.current_step != step_id:
            job.step_started_at = now
        job.current_step = step_id
        job.message = message
        job.progress = progress
        if phase is not None:
            job.phase = phase
        if job.step_started_at:
            try:
                job.step_elapsed_sec = max(
                    0, int((now - job.step_started_at.replace(tzinfo=None)).total_seconds())
                )
            except Exception:
                job.step_elapsed_sec = 0
        else:
            job.step_elapsed_sec = 0
        self._apply_llm_stats(job)
        try:
            job.reading = build_waiting_room(session, limit=10)
        except Exception:
            job.reading = job.reading or []
        # Rotate excerpt for the chat strip
        if job.reading:
            idx = int(progress * len(job.reading)) % len(job.reading)
            card = job.reading[idx]
            job.excerpt = f"{card.get('title')}: {str(card.get('body') or '')[:280]}"
        self._publish(store, job)

    def _heartbeat(
        self,
        store,
        job: AnalysisJob,
        session: ResearchSession,
        *,
        step_id: str,
        phase: str,
        message: str,
        lo: float,
        hi: float,
        stop: threading.Event,
        interval: float = 4.0,
    ) -> None:
        """Keep Progress UI alive during long LLM/Python work inside one stage."""
        tick = 0
        while not stop.wait(interval):
            tick += 1
            frac = min(0.92, tick * 0.04)
            elapsed = ""
            if job.step_started_at:
                secs = max(
                    0,
                    int((datetime.utcnow() - job.step_started_at.replace(tzinfo=None)).total_seconds()),
                )
                elapsed = f" · {secs // 60}m{secs % 60:02d}s"
            self._apply_llm_stats(job)
            msg = message
            if job.llm_current_module:
                est = "~" if job.llm_tokens_estimated else ""
                msg = (
                    f"LLM · {job.llm_current_module} "
                    f"(call #{job.llm_calls_total}"
                    f", ×{job.llm_by_module.get(job.llm_current_module, 0)}) · "
                    f"{est}{job.llm_tokens_total:,} tokens"
                )
            elif job.llm_calls_total:
                est = "~" if job.llm_tokens_estimated else ""
                msg = f"{message} · {job.llm_calls_total} LLM calls · {est}{job.llm_tokens_total:,} tokens"
            self._set_step(
                store,
                job,
                session,
                step_id,
                f"{msg}{elapsed}",
                progress=lo + (hi - lo) * frac,
                phase=f"llm:{job.llm_current_module}" if job.llm_current_module else phase,
            )

    @staticmethod
    def _save_merged(orch, session: ResearchSession) -> None:
        """Save without clobbering chat turns / session rules the user added while the job ran."""
        try:
            disk = orch.store.load(session.id)
        except Exception:
            disk = None
        if disk is not None:
            seen = {c.model_dump_json() for c in session.chat_history}
            session.chat_history.extend(c for c in disk.chat_history if c.model_dump_json() not in seen)
            ctx = next((a for a in disk.agent_artifacts if a.artifact_type == "session_context"), None)
            if ctx is not None:
                session.agent_artifacts = [
                    a for a in session.agent_artifacts if a.artifact_type != "session_context"
                ] + [ctx]
        orch.store.save(session)

    def _ensure_not_aborted(self, job: AnalysisJob) -> None:
        from iis.llm.gate import LLMAbortError, get_llm_gate

        if job.status == "aborted" or get_llm_gate().aborted():
            raise LLMAbortError("Analysis aborted by user")

    def _run_pipeline(self, job: AnalysisJob, session_id: str, *, owner_id: str | None) -> ResearchSession:
        from iis.config import settings
        from iis.finance.analysis_pack import write_analysis_pack
        from iis.library.store import get_company_library_store
        from iis.llm.gate import LLMAbortError, get_llm_gate
        from iis.storage.session_store import get_session_store

        store = get_session_store()
        orch = self.orch
        gate = get_llm_gate()

        def _on_llm(stats: dict[str, Any]) -> None:
            # Push token/call cloud into Progress without rewriting step message
            with self._lock:
                live = self._jobs.get(job.id) or job
                self._apply_llm_stats(live, stats)
                mod = stats.get("llm_current_module")
                if mod:
                    n = int(stats.get("llm_calls_total") or 0)
                    toks = int(stats.get("llm_tokens_total") or 0)
                    est = "~" if stats.get("llm_tokens_estimated") else ""
                    by = stats.get("llm_by_module") or {}
                    mod_n = by.get(mod, 0)
                    inflight = int(stats.get("llm_in_flight") or 0)
                    par = int(stats.get("llm_max_parallel") or 1)
                    parallel_bit = f" · ∥{inflight}/{par}" if par > 1 else ""
                    live.message = (
                        f"LLM · {mod} (call #{n}, this module ×{mod_n}) · "
                        f"{est}{toks:,} tokens{parallel_bit}"
                    )
                    live.phase = f"llm:{mod}"
                snap = live.model_copy(deep=True)
            self._publish(store, snap)

        gate.bind(job.id, progress=_on_llm)
        try:
            return self._run_pipeline_inner(job, session_id, owner_id=owner_id, store=store)
        except LLMAbortError:
            session = orch._load(session_id, owner_id)
            session.status = SessionStatus.READY
            # Keep any Python draft already written
            self._save_merged(orch, session)
            job.status = "aborted"
            job.error = "Aborted by user"
            job.message = (
                f"Aborted · {job.llm_calls_total} LLM calls · "
                f"{'~' if job.llm_tokens_estimated else ''}{job.llm_tokens_total:,} tokens"
            )
            job.phase = "aborted"
            job.finished_at = datetime.utcnow()
            self._apply_llm_stats(job)
            self._publish(store, job)
            return session
        finally:
            gate.unbind(job.id)

    def _run_pipeline_inner(
        self,
        job: AnalysisJob,
        session_id: str,
        *,
        owner_id: str | None,
        store,
    ) -> ResearchSession:
        from iis.config import settings
        from iis.finance.analysis_pack import write_analysis_pack
        from iis.library.store import get_company_library_store

        orch = self.orch
        session = orch._load(session_id, owner_id)
        get_company_library_store().sync_to_session(session)
        try:
            from iis.library.ingest import refresh_estimates_from_library

            refresh_estimates_from_library(session)
        except Exception:
            pass

        session.status = SessionStatus.ANALYZING
        n = max(len(self.STEPS), 1)
        analyze_t0 = time.monotonic()
        analyze_wall = float(getattr(settings, "analyze_max_wall_sec", 3600.0) or 3600.0)
        wall_hit = {"where": None}

        def _readiness_refresh(where: str, *, progress: float) -> None:
            """Re-sync library + progressive asks; never blocks the pipeline."""
            try:
                from iis.finance.data_readiness import refresh_session_from_libraries

                info = refresh_session_from_libraries(session) or {}
                summary = (info.get("summary") or "").strip()
                notes = "; ".join(info.get("notes") or [])
                detail = summary or "Checking uploads…"
                if notes:
                    detail = f"{notes} · {detail}"
                if len(detail) > 220:
                    detail = detail[:217] + "…"
                self._set_step(
                    store,
                    job,
                    session,
                    where,
                    detail,
                    progress=progress,
                    phase=f"data_readiness:{where}",
                )
                self._save_merged(orch, session)
            except Exception as exc:
                session.add_event(
                    EventType.INFERENCE,
                    agent="orchestrator",
                    details={"data_readiness_error": str(exc)[:300]},
                )

        # First pass — ask for top 3–4 uploads, then keep working
        self._set_step(
            store,
            job,
            session,
            "data_readiness",
            "First pass — checking what is on file…",
            progress=0.04,
            phase="data_readiness",
        )
        _readiness_refresh("data_readiness", progress=0.05)

        def _within_wall(where: str) -> bool:
            elapsed = time.monotonic() - analyze_t0
            if elapsed <= analyze_wall:
                return True
            if not wall_hit["where"]:
                wall_hit["where"] = where
                self._set_step(
                    store,
                    job,
                    session,
                    where,
                    (
                        f"Analyze {int(analyze_wall // 60)}-min wall at {where} "
                        f"(elapsed {int(elapsed)}s) — wrapping up with Python + one-shot digest"
                    ),
                    progress=max(job.progress or 0.5, 0.82),
                    phase="analyze_wall",
                )
            return False

        llm_on = bool(getattr(settings, "analyze_llm_enrichment", False))
        ar_msg = (
            "Processing annual report files (Python"
            + (" + LLM enrichment)" if llm_on else " · fast path, no section Hive loops)")
        )
        stages: list[tuple[str, str, Any]] = [
            ("normalizer", "Normalizing financials & ratios", orch.normalizer.run),
            ("annual_report", ar_msg, orch.annual_report.run),
            (
                "guidelines",
                "Guidance track from concalls (Python)",
                orch.guidelines.run,
            ),
            (
                "porters",
                "Competitive forces from uploaded files",
                orch.porters.run,
            ),
        ]
        if bool(getattr(settings, "dcf_enabled", False)):
            stages.append(
                ("margin_of_safety", "Building DCF / intrinsic value", orch.mos.run)
            )
        else:
            # Clear any stale DCF from a prior run so it cannot leak into the report
            session.valuation = None
            session.assumptions = [
                a
                for a in (session.assumptions or [])
                if not any(
                    k in str(getattr(a, "key", "") or "").lower()
                    for k in ("wacc", "terminal", "dcf", "discount")
                )
            ]

        for i, (sid, msg, fn) in enumerate(stages):
            self._ensure_not_aborted(job)
            if not _within_wall(sid):
                break
            base_p = (i + 0.2) / (n + 2)
            end_p = (i + 1) / (n + 2)
            self._set_step(
                store,
                job,
                session,
                sid,
                f"Calling {sid.replace('_', ' ')} — {msg}",
                progress=base_p,
                phase=f"start:{sid}",
            )

            def _make_cb(step_id: str, lo: float, hi: float):
                tick = {"n": 0}

                def _cb(detail: str) -> None:
                    tick["n"] += 1
                    # Ease within this stage so UI moves while LLM sections run
                    frac = min(0.85, tick["n"] * 0.12)
                    detail_s = detail or msg
                    low = detail_s.lower()
                    phase = (
                        "llm"
                        if ("llm" in low or "ollama" in low or "hive" in low or "gpt" in low)
                        else f"run:{step_id}"
                    )
                    self._set_step(
                        store,
                        job,
                        session,
                        step_id,
                        detail_s,
                        progress=lo + (hi - lo) * frac,
                        phase=phase,
                    )

                return _cb

            if sid in ("annual_report", "guidelines"):
                try:
                    session = fn(session, progress_cb=_make_cb(sid, base_p, end_p))
                except TypeError:
                    session = fn(session)
            else:
                session = fn(session)
            self._save_merged(orch, session)
            # Reload after heavy agents — session may be huge (8k+ chunks)
            try:
                session = orch._load(session_id, owner_id)
            except Exception:
                pass
            self._set_step(store, job, session, sid, f"Done — {msg}", progress=end_p)

            # After normalizer: one Hive call with company basics → what to look for
            if sid == "normalizer" and bool(
                getattr(settings, "research_planner_enabled", True)
            ):
                self._ensure_not_aborted(job)
                if _within_wall("research_plan"):
                    self._set_step(
                        store,
                        job,
                        session,
                        "research_plan",
                        "Hive — company card → what should we look for…",
                        progress=end_p + 0.01,
                        phase="research_plan_start",
                    )

                    def _plan_cb(detail: str) -> None:
                        self._ensure_not_aborted(job)
                        self._set_step(
                            store,
                            job,
                            session,
                            "research_plan",
                            detail,
                            progress=end_p + 0.015,
                            phase="research_plan",
                        )

                    try:
                        from iis.finance.research_planner import run_research_planner

                        plan = run_research_planner(session, progress_cb=_plan_cb) or {}
                        self._set_step(
                            store,
                            job,
                            session,
                            "research_plan",
                            (
                                f"Research plan ready · {len(plan.get('focus_bullets') or [])} focus items"
                                if plan.get("ok")
                                else f"Research plan skipped — {(plan.get('reason') or plan.get('error') or 'n/a')[:80]}"
                            ),
                            progress=end_p + 0.02,
                            phase="research_plan_done",
                        )
                        self._save_merged(orch, session)
                    except Exception as exc:
                        session.add_event(
                            EventType.INFERENCE,
                            agent="orchestrator",
                            details={"research_plan_error": str(exc)[:300]},
                        )

        from iis.agents.user_communicator import apply_user_asks
        from iis.finance.brokerage_view import apply_brokerage_view
        from iis.finance.competitor_discovery import apply_competitor_discovery
        from iis.finance.critique import apply_critique
        from iis.finance.data_freshness import apply_freshness_artifact
        from iis.finance.reconciler import apply_reconciliation

        post_stages = [
            (
                "brokerage_view",
                "Broker estimates vs CIQ (Python)…",
                apply_brokerage_view,
                0.62,
            ),
            ("competitors", "Peers from CIQ comps / AR / broker reports…", apply_competitor_discovery, 0.66),
            ("freshness", "Checking Capital IQ file freshness…", apply_freshness_artifact, 0.70),
            ("reconcile", "Cross-checking sources (reconciliation)…", apply_reconciliation, 0.74),
            ("critique", "Investor checklists (accounting, balance sheet, moat)…", apply_critique, 0.78),
            ("asks", "Collecting what we need from you…", apply_user_asks, 0.82),
        ]
        for sid, msg, fn, prog in post_stages:
            self._ensure_not_aborted(job)
            if not _within_wall(sid):
                break
            self._set_step(
                store,
                job,
                session,
                sid,
                f"Calling {sid.replace('_', ' ')} — {msg}",
                progress=prog,
                phase=f"start:{sid}",
            )
            stop_post = threading.Event()
            hb_post = threading.Thread(
                target=self._heartbeat,
                kwargs={
                    "store": store,
                    "job": job,
                    "session": session,
                    "step_id": sid,
                    "phase": f"run:{sid}",
                    "message": msg.rstrip("…").rstrip("."),
                    "lo": prog,
                    "hi": min(prog + 0.035, 0.85),
                    "stop": stop_post,
                    "interval": 5.0,
                },
                daemon=True,
                name=f"iis-hb-{sid[:8]}",
            )
            hb_post.start()
            try:
                session = fn(session) or session
            except Exception as e:
                session.add_event(
                    EventType.INFERENCE,
                    agent="orchestrator",
                    details={f"{sid}_error": str(e)[:300]},
                )
            finally:
                stop_post.set()
                hb_post.join(timeout=2.0)
            self._set_step(
                store,
                job,
                session,
                sid,
                f"Done — {msg}",
                progress=prog + 0.02,
                phase=f"done:{sid}",
            )
        self._save_merged(orch, session)
        # Mid-run #1 — peers known; refresh asks + pick up late uploads
        _readiness_refresh("data_readiness_mid", progress=0.825)

        session.init_gates()
        for gid in GateId:
            g = session.gate(gid)
            if g.status != GateStatus.APPROVED:
                g.status = GateStatus.APPROVED
                g.reviewed_at = datetime.utcnow()
                g.reviewer_comment = "Auto-completed with full analysis pass (chat to refine)"

        try:
            write_analysis_pack(session)
        except Exception:
            pass

        # Fast path: one packed-text Hive call (replaces AR/concall/broker section loops)
        self._ensure_not_aborted(job)
        if bool(getattr(settings, "unified_doc_llm_enabled", True)) and _within_wall(
            "unified_doc"
        ):
            self._set_step(
                store,
                job,
                session,
                "unified_doc",
                "One Hive pass over packed AR / concall / broker text…",
                progress=0.83,
                phase="unified_doc_start",
            )

            def _uni_cb(detail: str) -> None:
                self._ensure_not_aborted(job)
                self._set_step(
                    store,
                    job,
                    session,
                    "unified_doc",
                    detail,
                    progress=0.835,
                    phase="unified_doc",
                )

            try:
                from iis.finance.one_shot_doc_synthesis import run_one_shot_doc_synthesis

                uni = run_one_shot_doc_synthesis(session, progress_cb=_uni_cb) or {}
                self._set_step(
                    store,
                    job,
                    session,
                    "unified_doc",
                    (
                        f"One-shot digest ready · {len((uni.get('markdown') or '')):,} chars"
                        if uni.get("ok")
                        else f"One-shot skipped/failed — {(uni.get('reason') or uni.get('error') or 'n/a')[:80]}"
                    ),
                    progress=0.84,
                    phase="unified_doc_done",
                )
            except Exception as exc:
                session.add_event(
                    EventType.INFERENCE,
                    agent="orchestrator",
                    details={"unified_doc_error": str(exc)[:300]},
                )
            self._save_merged(orch, session)

        self._ensure_not_aborted(job)
        self._set_step(
            store,
            job,
            session,
            "research_packs",
            "Building AR / concall / broker research packs…",
            progress=0.84,
            phase="research_packs",
        )
        try:
            from iis.finance.packs import build_all_research_packs

            pack_summary = build_all_research_packs(session) or {}
            nclaims = sum(
                int((pack_summary.get("packs") or {}).get(k, {}).get("claims") or 0)
                for k in ("ar", "concall", "broker")
            )
            self._set_step(
                store,
                job,
                session,
                "research_packs",
                f"Research packs ready · ~{nclaims} claims on disk",
                progress=0.85,
                phase="research_packs_done",
            )
        except Exception as exc:
            session.add_event(
                EventType.INFERENCE,
                agent="orchestrator",
                details={"research_packs_error": str(exc)[:300]},
            )

        self._ensure_not_aborted(job)
        # Mid-run #2 — absorb late uploads before initiating coverage
        _readiness_refresh("data_readiness_pre_report", progress=0.855)

        self._ensure_not_aborted(job)
        # Report: Python draft first, then short LLM polish (1 round)
        self._set_step(
            store,
            job,
            session,
            "report",
            "Writing report — Python dossier…",
            progress=0.86,
            phase="python_dossier",
        )
        stop_hb = threading.Event()
        hb = threading.Thread(
            target=self._heartbeat,
            kwargs={
                "store": store,
                "job": job,
                "session": session,
                "step_id": "report",
                "phase": "report_running",
                "message": "Writing report (Python then initiating coverage)",
                "lo": 0.86,
                "hi": 0.96,
                "stop": stop_hb,
                "interval": 5.0,
            },
            daemon=True,
            name=f"iis-hb-{job.id[:8]}",
        )
        hb.start()

        def _report_cb(phase: str, detail: str, prog: float) -> None:
            self._ensure_not_aborted(job)
            self._set_step(
                store,
                job,
                session,
                "report",
                detail,
                progress=max(0.86, min(0.97, prog)),
                phase=phase,
            )

        report_ok = False
        note = ""
        pipeline_stats: dict | None = None
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

            from iis.llm.gate import LLMAbortError

            # Whole report stage hard cap (Python draft + sequential LLM loop)
            report_wall = float(getattr(settings, "report_stage_timeout_sec", 900.0) or 900.0)

            def _run_note():
                prev_llm = getattr(settings, "llm_analyst_enabled", True)
                if getattr(settings, "analyze_skip_llm_analyst", False):
                    settings.llm_analyst_enabled = False
                try:
                    try:
                        return orch.note_agent.run(session, progress_cb=_report_cb)
                    except TypeError:
                        return orch.note_agent.run(session)
                finally:
                    settings.llm_analyst_enabled = prev_llm

            # Do NOT use `with ThreadPoolExecutor`: on timeout, context-manager
            # shutdown(wait=True) blocks forever while the worker keeps spinning.
            pool = ThreadPoolExecutor(max_workers=1)
            fut = pool.submit(_run_note)
            timed_out = False
            try:
                try:
                    note, session = fut.result(timeout=report_wall)
                except FuturesTimeout:
                    timed_out = True
                    note = getattr(session, "research_report_md", None) or ""
                    self._set_step(
                        store,
                        job,
                        session,
                        "report",
                        f"Report stage timed out after {int(report_wall)}s — saving Python draft",
                        progress=0.95,
                        phase="report_timeout",
                    )
                except LLMAbortError:
                    note = getattr(session, "research_report_md", None) or ""
                    raise
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            note = note or getattr(session, "research_report_md", None) or ""
            if timed_out and note and len(note.strip()) > 200:
                # Treat Python draft as enough for path A so Direct LLM (path B) can still run
                pass
            # Snapshot gate stats for pipeline report (before Direct LLM burns more tokens)
            from iis.llm.gate import get_llm_gate

            pipeline_snap = get_llm_gate().snapshot()
            pipeline_stats = {
                "calls": int(pipeline_snap.get("llm_calls_total") or 0),
                "calls_ok": int(pipeline_snap.get("llm_calls_ok") or 0),
                "calls_fail": int(pipeline_snap.get("llm_calls_fail") or 0),
                "tokens_total": int(pipeline_snap.get("llm_tokens_total") or 0),
                "prompt_tokens": int(pipeline_snap.get("llm_prompt_tokens") or 0),
                "completion_tokens": int(pipeline_snap.get("llm_completion_tokens") or 0),
                "tokens_estimated": bool(pipeline_snap.get("llm_tokens_estimated")),
                "report_chars": len(note or ""),
                "path": None,
                "label": "Pipeline Report (Python + analyst tools)",
            }
            if note and len(note.strip()) > 200:
                session.research_report_md = note
                company_name = session.company.name if session.company else None
                if company_name:
                    from iis.integrations.gdrive import company_folder_name, sync_research_note

                    safe = company_folder_name(company_name).replace(" ", "_")
                    local_dir = (
                        settings.data_dir / "companies" / (session.company_key or safe) / "reports"
                    )
                    local_dir.mkdir(parents=True, exist_ok=True)
                    local_path = local_dir / f"{safe}_Research_Report_{date.today().isoformat()}.md"
                    local_path.write_text(note, encoding="utf-8")
                    session.research_report_path = str(local_path)
                    pipeline_stats["path"] = str(local_path)
                    try:
                        sync_research_note(company_name, note)
                    except Exception:
                        pass
                session.status = SessionStatus.COMPLETE
                session.add_event(
                    EventType.NOTE_GENERATED,
                    agent="orchestrator",
                    details={"via": "analyze_async_full_pass", "length": len(note)},
                )
                report_ok = True
            else:
                session.status = SessionStatus.READY
                session.add_event(
                    EventType.INFERENCE,
                    agent="orchestrator",
                    details={
                        "report_error": "empty_report",
                        "via": "analyze_async_full_pass",
                        "length": len(note or ""),
                    },
                )
        except Exception as e:
            session.status = SessionStatus.READY
            session.add_event(
                EventType.INFERENCE,
                agent="orchestrator",
                details={"report_error": str(e), "via": "analyze_async_full_pass"},
            )
            pipeline_stats = None
        finally:
            stop_hb.set()
            hb.join(timeout=2.0)

        self._save_merged(orch, session)

        # ── Direct LLM (path B) — raw saved PDFs + guidelines; independent of path A quality ──
        # Run whenever we have a company_key (already-saved library) even if pipeline timed out
        # with a Python draft, or produced a full report.
        run_direct = bool(getattr(settings, "direct_llm_report_enabled", True)) and (
            report_ok or bool(session.company_key)
        )
        if run_direct:
            self._ensure_not_aborted(job)
            self._set_step(
                store,
                job,
                session,
                "direct_llm",
                "Direct LLM — raw library PDFs + write guidelines…",
                progress=0.97,
                phase="direct_llm_start",
            )
            try:
                from iis.finance.direct_llm_report import build_direct_llm_report
                from iis.llm.gate import LLMAbortError as _Abort

                def _direct_cb(phase: str, detail: str, prog: float) -> None:
                    self._set_step(
                        store,
                        job,
                        session,
                        "direct_llm",
                        detail,
                        progress=max(0.97, min(0.995, prog)),
                        phase=phase,
                    )

                build_direct_llm_report(
                    session,
                    progress_cb=_direct_cb,
                    pipeline_stats=pipeline_stats,
                )
                self._save_merged(orch, session)
            except _Abort:
                raise
            except Exception as exc:
                session.add_event(
                    EventType.INFERENCE,
                    agent="orchestrator",
                    details={"direct_llm_error": str(exc)[:300]},
                )

        has_direct = bool(
            (getattr(session, "direct_llm_report_md", None) or "")
            and len(session.direct_llm_report_md or "") > 200
        )
        if report_ok or has_direct:
            self._set_step(
                store,
                job,
                session,
                "done",
                (
                    "Full analysis + Report + Direct LLM ready"
                    if report_ok and has_direct
                    else (
                        "Report ready (Direct LLM skipped or empty)"
                        if report_ok
                        else "Direct LLM ready from saved library PDFs (pipeline Report incomplete)"
                    )
                ),
                progress=1.0,
                phase="done",
            )
        else:
            # Do not claim "report ready" — Progress was lying when note step failed
            job.status = "failed"
            job.error = job.error or "Analysis finished but research report was not written"
            self._set_step(
                store,
                job,
                session,
                "report",
                "Report step failed — open Progress detail / re-run analysis",
                progress=0.9,
                phase="report_failed",
            )
            raise RuntimeError(job.error)
        return session
