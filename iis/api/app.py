from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr

from iis.auth.deps import get_current_user, get_user_store
from iis.auth.models import User, to_public
from iis.auth.store import UserStore
from iis.config import settings
from iis.models.enums import GateId, SessionStatus
from iis.orchestrator.orchestrator import Orchestrator

STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_DIR = Path(__file__).parent.parent.parent / ".iis_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Inertia Intelligence System", version="0.3.0")
orch = Orchestrator()


class CreateSessionRequest(BaseModel):
    company: str | None = None


class ChatRequest(BaseModel):
    question: str


class ReportUpdateRequest(BaseModel):
    markdown: str


class GateActionRequest(BaseModel):
    approve: bool = True
    comment: str | None = None


class AssumptionUpdateRequest(BaseModel):
    value: float | str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _session_summary(session) -> dict[str, Any]:
    from iis.library.store import get_company_library_store

    store = get_company_library_store()
    store.sync_to_session(session)
    library = store.inventory_for_session(session)
    line_items = len(session.financial_line_items)
    pdf_chunks = len(session.pdf_chunks)
    if library:
        line_items = max(line_items, library.get("total_line_items") or 0)
        pdf_chunks = max(pdf_chunks, library.get("total_pdf_documents") or 0)
    return {
        "id": session.id,
        "owner_id": session.owner_id,
        "company": session.company.model_dump() if session.company else None,
        "company_key": session.company_key,
        "library": library,
        "status": session.status.value,
        "uploaded_files": [Path(f).name for f in session.uploaded_files],
        "line_items": line_items,
        "pdf_chunks": pdf_chunks,
        "pdf_documents": [d.model_dump() for d in session.pdf_documents],
        "market_quote": session.market_quote.model_dump() if session.market_quote else None,
        "gates": {gid.value: session.gate(gid).status.value for gid in GateId},
        "assumptions": [
            {
                "key": a.key,
                "label": a.label,
                "value": a.user_value or a.value,
                "proposed": a.value,
                "user_value": a.user_value,
                "unit": a.unit,
                "rationale": a.rationale,
                "approved": a.approved,
            }
            for a in session.assumptions
        ],
        "valuation": session.valuation.model_dump() if session.valuation else None,
        "has_research_report": _session_has_research_report(session),
        "research_report_path": session.research_report_path,
        "has_direct_llm_report": bool(
            (getattr(session, "direct_llm_report_md", None) or "")
            and len(session.direct_llm_report_md or "") > 200
            or getattr(session, "direct_llm_report_path", None)
        ),
        "direct_llm_report_path": getattr(session, "direct_llm_report_path", None),
        "report_compare": next(
            (
                a.payload
                for a in session.agent_artifacts
                if a.artifact_type == "report_compare"
            ),
            None,
        ),
        "competitor_suggestions": next(
            (
                a.payload
                for a in session.agent_artifacts
                if a.artifact_type == "competitor_suggestions"
            ),
            None,
        ),
        "data_freshness": _freshness_payload(session),
        "data_readiness": _artifact(session, "data_readiness"),
        "user_asks": _artifact(session, "user_asks"),
        "brokerage_view": _artifact(session, "brokerage_view"),
        "reconciliation": _artifact(session, "reconciliation"),
        "critique": _artifact(session, "critique"),
    }


def _artifact(session, kind: str) -> dict | None:
    return next((a.payload for a in session.agent_artifacts if a.artifact_type == kind), None)


def _freshness_payload(session) -> dict | None:
    art = next(
        (a for a in session.agent_artifacts if a.artifact_type == "data_freshness"),
        None,
    )
    if art:
        return art.payload
    try:
        from iis.finance.data_freshness import assess_data_freshness

        return assess_data_freshness(session)
    except Exception:
        return None


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/iis-boot.js")
async def iis_boot_js():
    """Inject public base path + Inertia-auth flags for the SPA when proxied under /iis."""
    from fastapi.responses import Response

    base = (settings.public_base_path or "").rstrip("/")
    auth_only = "true" if settings.inertia_auth_only else "false"
    # Prefer explicit IIS_APP_HOME_URL; else "/" when proxied under Inertia.
    home = (settings.app_home_url or "").strip()
    if not home and (base == "/iis" or base.endswith("/iis") or settings.inertia_auth_only):
        home = "/"
    body = (
        f"window.IIS_BASE={base!r};\n"
        f"window.IIS_INERTIA_AUTH_ONLY={auth_only};\n"
        f"window.IIS_APP_HOME={home!r};\n"
    )
    return Response(body, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


# Modern UI assets (app.js, etc.)
try:
    from fastapi.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
except Exception:
    @app.get("/static/{path:path}")
    async def static_fallback(path: str):
        target = STATIC_DIR / path
        if not target.is_file() or not str(target.resolve()).startswith(str(STATIC_DIR.resolve())):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(target, headers={"Cache-Control": "no-cache"})


@app.get("/api/drive/status")
async def drive_status_api(user: User = Depends(get_current_user)):
    """Google Shared Drive sync health (token missing = files stay local only)."""
    from iis.integrations.gdrive import drive_status_dict

    return drive_status_dict()


class DrivePushRequest(BaseModel):
    company_key: str | None = None
    company_name: str | None = None


@app.post("/api/drive/push")
async def drive_push_api(
    body: DrivePushRequest | None = None,
    user: User = Depends(get_current_user),
):
    """Push local company library files to Shared Drive (after OAuth)."""
    from iis.integrations.gdrive import drive_configured, push_company_library_to_drive

    if not drive_configured():
        raise HTTPException(
            400,
            "Google Drive not authenticated. In a terminal run: iis drive auth",
        )
    body = body or DrivePushRequest()
    return push_company_library_to_drive(
        company_key=body.company_key,
        company_name=body.company_name,
    )


@app.get("/api/health")
async def health():
    from iis.db.engine import database_available
    from iis.storage.session_store import DatabaseSessionStore, get_session_store

    store = get_session_store()
    llm_ok = orch.llm.is_available()
    provider = (getattr(settings, "llm_provider", None) or "ollama").strip().lower()
    return {
        "status": "ok",
        "llm_provider": provider,
        "llm": llm_ok,
        "ollama": llm_ok if provider == "ollama" else False,
        "hive": llm_ok if provider in ("hive", "thehive", "thehive.ai") else False,
        "auth_required": settings.auth_required or settings.inertia_auth_only,
        "inertia_auth_only": settings.inertia_auth_only,
        "public_base_path": (settings.public_base_path or "").rstrip("/"),
        "live_prices": settings.fetch_live_prices,
        "database": "mysql" if isinstance(store, DatabaseSessionStore) else "json",
        "database_connected": database_available() if isinstance(store, DatabaseSessionStore) else True,
        "semantic_search": settings.enable_semantic_search,
        "price_sheet": _price_sheet_status(),
    }


def _price_sheet_status() -> dict:
    try:
        from iis.finance.price_sheet import sheet_status

        return sheet_status()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/api/auth/register")
async def register(body: RegisterRequest, store: UserStore = Depends(get_user_store)):
    try:
        user = store.register(body.email, body.password, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return to_public(user)


@app.post("/api/auth/login")
async def login(body: LoginRequest, store: UserStore = Depends(get_user_store)):
    user = store.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    return to_public(user)


@app.get("/api/auth/me")
async def me(user: User = Depends(get_current_user)):
    return to_public(user)


@app.post("/api/sessions")
async def create_session(body: CreateSessionRequest, user: User = Depends(get_current_user)):
    session = orch.create_session(body.company, owner_id=user.id)
    return _session_summary(session)


@app.get("/api/sessions")
async def list_sessions(user: User = Depends(get_current_user), limit: int = 20):
    """Light cards for Recent sessions. Full payload only via GET /api/sessions/{id}."""
    owner = user.id if settings.auth_required else None
    lim = max(1, min(int(limit or 20), 40))
    return orch.list_session_cards(owner_id=owner, limit=lim)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, user: User = Depends(get_current_user)):
    try:
        session = orch.get_session(session_id, owner_id=user.id if settings.auth_required else None)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return _session_summary(session)


@app.get("/api/library")
async def list_company_libraries(user: User = Depends(get_current_user)):
    from iis.library.store import get_company_library_store

    return {"companies": get_company_library_store().list_inventories()}


@app.get("/api/library/index")
async def library_file_index(rebuild: bool = False, user: User = Depends(get_current_user)):
    """Company-wise index of all uploaded files (so peer packs are findable later)."""
    from iis.library.file_index import load_file_index, rebuild_file_index
    from iis.library.store import get_company_library_store

    store = get_company_library_store()
    if rebuild:
        return rebuild_file_index(store)
    idx = load_file_index(store.base_dir)
    if not idx.get("companies"):
        idx = rebuild_file_index(store)
    # Compact response for UI
    return {
        "updated_at": idx.get("updated_at"),
        "company_count": idx.get("company_count") or len(idx.get("companies") or []),
        "companies": [
            {
                "company_key": c.get("company_key"),
                "name": c.get("name"),
                "nse_ticker": c.get("nse_ticker"),
                "bse_code": c.get("bse_code"),
                "document_count": c.get("document_count"),
                "line_items": c.get("line_items"),
                "documents": [
                    {"filename": d.get("filename"), "doc_kind": d.get("doc_kind"), "fiscal_year": d.get("fiscal_year")}
                    for d in (c.get("documents") or [])
                ],
            }
            for c in (idx.get("companies") or [])
        ],
    }


@app.get("/api/library/find")
async def library_find(q: str, user: User = Depends(get_current_user)):
    """Find previously uploaded files / companies by name, ticker, or filename."""
    from iis.library.file_index import find_in_index
    from iis.library.store import get_company_library_store

    if not (q or "").strip():
        raise HTTPException(400, "Query q required")
    return find_in_index(q.strip(), store=get_company_library_store())


@app.get("/api/library/lookup")
async def lookup_company_library(
    company: str,
    pull_drive: bool = True,
    pull_telegram: bool = False,
    user: User = Depends(get_current_user),
):
    """Preview saved data for a company name before starting a session.

    When Drive is configured, pulls Shared Drive files that are not already local.
    Telegram PDF pull defaults off (slow) — research/start still pulls reports.
    """
    from iis.library.reload import sync_company_hint_from_drive
    from iis.library.store import get_company_library_store

    if not company.strip():
        raise HTTPException(400, "Company name required")
    hint = company.strip()
    drive_sync = None
    if pull_drive or pull_telegram:
        try:
            drive_sync = sync_company_hint_from_drive(
                hint, pull_drive=pull_drive, pull_telegram=pull_telegram
            )
        except Exception as e:
            drive_sync = {"error": str(e), "pulled": False}
    store = get_company_library_store()
    result = store.lookup_by_hint(hint)
    if drive_sync is not None:
        result["drive_sync"] = drive_sync
        if drive_sync.get("telegram"):
            result["telegram_sync"] = drive_sync["telegram"]
    # Stale CIQ warning on Continue / lookup
    if result.get("matched"):
        try:
            from iis.finance.data_freshness import assess_data_freshness
            from iis.models.artifacts import CompanyInfo
            from iis.models.session import ResearchSession

            scratch = ResearchSession()
            scratch.company_key = result.get("company_key")
            if result.get("company"):
                scratch.company = CompanyInfo(**result["company"])
            lib = store.load(result["company_key"])
            scratch.financial_line_items = lib.financial_line_items
            scratch.consensus_metrics = lib.consensus_metrics
            scratch.guidance_records = lib.guidance_records
            result["data_freshness"] = assess_data_freshness(scratch)
        except Exception:
            pass
    return result


@app.get("/api/library/{company_key}")
async def company_library(company_key: str, user: User = Depends(get_current_user)):
    from iis.library.store import get_company_library_store

    return get_company_library_store().load(company_key).inventory()


@app.get("/api/sessions/{session_id}/overview")
async def session_overview(session_id: str, user: User = Depends(get_current_user)):
    from iis.finance.overview import build_session_overview

    owner = user.id if settings.auth_required else None
    try:
        session = orch.get_session(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return build_session_overview(session)


@app.post("/api/sessions/{session_id}/upload")
async def upload_files(
    session_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
):
    saved: list[str] = []
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    owner = user.id if settings.auth_required else None

    for uf in files:
        dest = session_dir / uf.filename
        dest.write_bytes(await uf.read())
        saved.append(str(dest))

    try:
        session = orch.upload_files(session_id, saved, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")

    return {"uploaded": [Path(p).name for p in saved], "session": _session_summary(session)}


def _parse_sections_form(raw: str | None, file_count: int) -> list[str | None]:
    import json

    if not raw:
        return [None] * file_count
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [None] * file_count
    if not isinstance(data, list):
        return [None] * file_count
    out: list[str | None] = []
    for i in range(file_count):
        out.append(data[i] if i < len(data) else None)
    return out


_UI_SECTION_TO_KIND = {
    "ciq_financials": "capital_iq_financials",
    "ciq_estimates": "capital_iq_estimates",
    "annual_report": "annual_report",
    "concall": "transcript",
    "presentation": "earnings_presentation",
    "brokerage": "brokerage_report",
    "screenshot": "screenshot",
}


@app.post("/api/sessions/{session_id}/process")
async def process_files(
    session_id: str,
    files: list[UploadFile] = File(...),
    company: str | None = Form(None),
    sections: str | None = Form(None),
    user: User = Depends(get_current_user),
):
    """Upload files and run ingestion in one step. `sections` = JSON array of UI section keys."""
    owner = user.id if settings.auth_required else None
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    section_keys = _parse_sections_form(sections, len(files))

    for uf in files:
        name = Path(uf.filename or "upload.bin").name
        if not name or name in (".", ".."):
            name = "upload.bin"
        dest = session_dir / name
        dest.write_bytes(await uf.read())
        saved.append(str(dest))

    try:
        if company:
            orch.set_company_hint(session_id, company, owner_id=owner)
        kinds = {
            Path(p).name: _UI_SECTION_TO_KIND[s]
            for p, s in zip(saved, section_keys)
            if s and s in _UI_SECTION_TO_KIND
        }
        orch.upload_files(session_id, saved, owner_id=owner, file_kinds=kinds)
        session = orch.ingest(session_id, owner_id=owner)
        try:
            from iis.agents.user_communicator import apply_user_asks

            apply_user_asks(session)
            orch.store.save(session)
        except Exception:
            pass
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")

    return {
        "uploaded": [Path(p).name for p in saved],
        "session": _session_summary(session),
    }


@app.post("/api/research/start")
async def start_research(
    user: User = Depends(get_current_user),
    company: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    sections: str | None = Form(None),
):
    """Create a session, upload files, and ingest — one-shot research start."""
    owner = user.id if settings.auth_required else None
    # When files are attached, skip Drive pull on create so upload isn't blocked / timed out.
    session = orch.create_session(company, owner_id=owner, pull_drive=not bool(files))

    if files:
        session_dir = UPLOAD_DIR / session.id
        session_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        section_keys = _parse_sections_form(sections, len(files))
        for uf in files:
            name = Path(uf.filename or "upload.bin").name
            if not name or name in (".", ".."):
                name = "upload.bin"
            dest = session_dir / name
            dest.write_bytes(await uf.read())
            saved.append(str(dest))
        kinds = {
            Path(p).name: _UI_SECTION_TO_KIND[s]
            for p, s in zip(saved, section_keys)
            if s and s in _UI_SECTION_TO_KIND
        }
        try:
            orch.upload_files(session.id, saved, owner_id=owner, file_kinds=kinds)
            session = orch.ingest(session.id, owner_id=owner)
            session = orch.get_session(session.id, owner_id=owner)
            if not session.financial_line_items and saved:
                session = orch.ingest(session.id, owner_id=owner)
                session = orch.get_session(session.id, owner_id=owner)
        except Exception as e:
            raise HTTPException(400, f"Upload/ingest failed: {e}") from e
    else:
        # No new uploads — load persisted company library (already-saved companies)
        session = orch.get_session(session.id, owner_id=owner)
        try:
            from iis.library.reload import reload_company_from_disk

            reload_company_from_disk(session, pull_drive=False)
            session = orch.get_session(session.id, owner_id=owner)
        except Exception:
            pass

    # Pull Telegram brokerage PDFs for this company into library + session analysis
    telegram_sync = None
    try:
        from iis.integrations.telegram_inbox import sync_pdfs_for_company, telegram_session_exists

        cname = company or (session.company.name if session.company else None)
        if cname and settings.telegram_enabled and telegram_session_exists() and settings.telegram_group:
            telegram_sync = sync_pdfs_for_company(cname, search_limit=80, max_files=25, ingest=True)
            session = orch.get_session(session.id, owner_id=owner)
            try:
                from iis.library.reload import reload_company_from_disk

                reload_company_from_disk(session, pull_drive=False)
                session = orch.get_session(session.id, owner_id=owner)
            except Exception:
                pass
            try:
                from iis.finance.analysis_pack import write_analysis_pack

                write_analysis_pack(session)
                orch.store.save(session)
            except Exception:
                pass
    except Exception as e:
        telegram_sync = {"error": str(e)}

    summary = _session_summary(session)
    summary["loaded_from_library"] = bool(
        not files and (summary.get("line_items") or summary.get("library", {}).get("total_line_items"))
    )
    if telegram_sync is not None:
        summary["telegram_sync"] = telegram_sync
    return summary


@app.post("/api/sessions/{session_id}/reingest")
async def reingest_session(session_id: str, user: User = Depends(get_current_user)):
    """Pull Drive (if configured), scan fixed company folders, re-ingest."""
    owner = user.id if settings.auth_required else None
    try:
        session = orch.ingest(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return _session_summary(session)


@app.post("/api/sessions/{session_id}/reload-library")
async def reload_library(session_id: str, user: User = Depends(get_current_user)):
    """Scan local company folders + Google Drive and auto-load any new files."""
    from iis.library.reload import reload_company_from_disk

    owner = user.id if settings.auth_required else None
    try:
        session = orch.get_session(session_id, owner_id=owner)
        summary = reload_company_from_disk(session, pull_drive=True)
        orch.store.save(session)
        out = _session_summary(session)
        out["reload"] = summary
        return out
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")


@app.post("/api/sessions/{session_id}/documents/reclassify")
async def reclassify_document(
    session_id: str,
    body: dict,
    user: User = Depends(get_current_user),
):
    """Move a saved file to another section (correct auto-classification)."""
    from iis.library.store import get_company_library_store

    owner = user.id if settings.auth_required else None
    filename = (body or {}).get("filename")
    section = (body or {}).get("section")  # UI section key
    kind = (body or {}).get("doc_kind")
    if not filename:
        raise HTTPException(400, "filename required")
    if section and section in _UI_SECTION_TO_KIND:
        kind = _UI_SECTION_TO_KIND[section]
    if not kind:
        raise HTTPException(400, "section or doc_kind required")

    try:
        session = orch.get_session(session_id, owner_id=owner)
        key = session.company_key
        if not key:
            raise HTTPException(400, "Session has no company library yet")
        result = get_company_library_store().reclassify_document(key, filename, kind)
        # keep upload_file_kinds in sync for re-ingest
        session.upload_file_kinds[filename] = kind
        get_company_library_store().sync_to_session(session)
        orch.store.save(session)
        out = _session_summary(session)
        out["reclassified"] = result
        return out
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except PermissionError:
        raise HTTPException(403, "Access denied")


@app.post("/api/sessions/{session_id}/ingest")
async def ingest(session_id: str, user: User = Depends(get_current_user)):
    owner = user.id if settings.auth_required else None
    try:
        session = orch.ingest(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return _session_summary(session)


@app.post("/api/sessions/{session_id}/analyze")
async def analyze(
    session_id: str,
    force: bool = False,
    async_run: bool | None = None,
    user: User = Depends(get_current_user),
):
    owner = user.id if settings.auth_required else None
    use_async = settings.analyze_async_default if async_run is None else async_run
    try:
        if use_async:
            job = orch.analyze_async(session_id, owner_id=owner)
            # Seed reading material immediately
            try:
                from iis.finance.waiting_room import build_waiting_room

                session = orch.get_session(session_id, owner_id=owner)
                job.reading = build_waiting_room(session)
                if job.reading:
                    c0 = job.reading[0]
                    job.excerpt = f"{c0.get('title')}: {str(c0.get('body') or '')[:280]}"
                    job.message = "Starting full analysis pass…"
                    job.current_step = "normalizer"
            except Exception:
                pass
            return {
                "job_id": job.id,
                "status": job.status,
                "async": True,
                "message": job.message,
                "reading": job.reading,
                "excerpt": job.excerpt,
            }
        session = orch.analyze(session_id, skip_gates=force, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError as e:
        msg = str(e)
        code = 400 if "must be approved" in msg else 403
        raise HTTPException(code, msg)
    return _session_summary(session)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user: User = Depends(get_current_user)):
    job = orch.get_analysis_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.model_dump(mode="json")


@app.post("/api/jobs/{job_id}/abort")
async def abort_job(job_id: str, user: User = Depends(get_current_user)):
    """Force-stop a running analysis (closes in-flight Hive call when possible)."""
    job = orch.get_analysis_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    ok = orch.abort_analysis_job(job_id)
    if not ok:
        raise HTTPException(400, "Job is not running (already finished or unknown)")
    job = orch.get_analysis_job(job_id) or job
    return {
        "ok": True,
        "status": getattr(job, "status", "aborted"),
        "message": getattr(job, "message", "Aborted"),
        "llm_calls_total": getattr(job, "llm_calls_total", 0),
        "llm_tokens_total": getattr(job, "llm_tokens_total", 0),
    }


class OvernightAddRequest(BaseModel):
    companies: list[str]


@app.get("/api/overnight")
async def overnight_status(user: User = Depends(get_current_user)):
    """Overnight batch queue status (persisted on disk)."""
    from iis.jobs.overnight_batch import get_overnight_batch

    return get_overnight_batch().status()


@app.post("/api/overnight/add")
async def overnight_add(body: OvernightAddRequest, user: User = Depends(get_current_user)):
    """Queue company names only (uses library already on disk). Prefer /enqueue with files."""
    from iis.jobs.overnight_batch import MAX_QUEUE, get_overnight_batch

    try:
        get_overnight_batch().add_companies(body.companies or [])
    except ValueError as e:
        raise HTTPException(400, str(e))
    st = get_overnight_batch().status()
    st["hint"] = (
        f"Queued. Active pending+running capped at {MAX_QUEUE}. "
        "Prefer attaching CIQ/AR/concalls via /api/overnight/enqueue."
    )
    return st


@app.post("/api/overnight/enqueue")
async def overnight_enqueue(
    company: str = Form(...),
    files: list[UploadFile] = File(...),
    sections: str | None = Form(None),
    user: User = Depends(get_current_user),
):
    """Queue one company with CIQ / AR / multiple concalls attached now.

    Overnight worker will ingest these files then run full analysis → report.
    `sections` = JSON array of UI keys aligned with `files` (ciq_financials, annual_report, concall, …).
    """
    from iis.jobs.overnight_batch import MAX_QUEUE, get_overnight_batch

    _ = user
    company = (company or "").strip()
    if not company:
        raise HTTPException(400, "Company name required")
    if not files:
        raise HTTPException(400, "Attach Capital IQ, annual report, and/or concall files")

    section_keys = _parse_sections_form(sections, len(files))
    staging = UPLOAD_DIR / "overnight_inbox" / uuid4_hex()
    staging.mkdir(parents=True, exist_ok=True)
    pairs: list[tuple[Path, str | None]] = []
    for uf, sec in zip(files, section_keys):
        name = Path(uf.filename or "file.bin").name
        dest = staging / name
        dest.write_bytes(await uf.read())
        pairs.append((dest, sec))

    try:
        get_overnight_batch().enqueue_with_files(company, pairs)
    except ValueError as e:
        raise HTTPException(400, str(e))

    st = get_overnight_batch().status()
    st["hint"] = (
        f"Queued **{company}** with {len(pairs)} file(s). "
        f"Add more (max {MAX_QUEUE}), then Start overnight — analysis runs one-by-one."
    )
    return st


def uuid4_hex() -> str:
    from uuid import uuid4

    return uuid4().hex[:12]


@app.post("/api/overnight/start")
async def overnight_start(user: User = Depends(get_current_user)):
    from iis.jobs.overnight_batch import get_overnight_batch

    try:
        return get_overnight_batch().start()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/overnight/stop")
async def overnight_stop(user: User = Depends(get_current_user)):
    from iis.jobs.overnight_batch import get_overnight_batch

    return get_overnight_batch().stop()


@app.delete("/api/overnight/items/{item_id}")
async def overnight_remove(item_id: str, user: User = Depends(get_current_user)):
    from iis.jobs.overnight_batch import get_overnight_batch

    return get_overnight_batch().remove_item(item_id)


@app.post("/api/overnight/clear-finished")
async def overnight_clear_finished(user: User = Depends(get_current_user)):
    from iis.jobs.overnight_batch import get_overnight_batch

    return get_overnight_batch().clear_finished()


@app.get("/api/sessions/{session_id}/waiting-room")
async def session_waiting_room(session_id: str, user: User = Depends(get_current_user)):
    """Snippets to read while analysis runs (flags, concalls, brokers, overview)."""
    from iis.finance.waiting_room import build_waiting_room

    owner = user.id if settings.auth_required else None
    try:
        session = orch.get_session(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return {"cards": build_waiting_room(session)}


@app.post("/api/sessions/{session_id}/evidence/{fact_id}")
async def review_evidence_fact(
    session_id: str,
    fact_id: str,
    body: GateActionRequest,
    user: User = Depends(get_current_user),
):
    owner = user.id if settings.auth_required else None
    try:
        session = orch.review_evidence_fact(
            session_id, fact_id, approve=body.approve, owner_id=owner
        )
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except KeyError as e:
        raise HTTPException(404, str(e))
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return _session_summary(session)


@app.post("/api/sessions/{session_id}/price")
async def refresh_price(session_id: str, user: User = Depends(get_current_user)):
    owner = user.id if settings.auth_required else None
    try:
        session = orch.refresh_market_price(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return _session_summary(session)


def _owned_session(session_id: str, user: User):
    owner = user.id if settings.auth_required else None
    try:
        return orch.get_session(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")


class PeerUpdateRequest(BaseModel):
    confirm: list[str] = []
    reject: list[str] = []
    add: list[str] = []
    confirm_all: bool = False
    replace: bool = False


@app.get("/api/sessions/{session_id}/asks")
async def session_asks(session_id: str, user: User = Depends(get_current_user)):
    """Everything the system needs from the user (peer confirmations, files, data fixes)."""
    from iis.agents.user_communicator import apply_user_asks, asks_markdown, open_asks

    session = _owned_session(session_id, user)
    apply_user_asks(session)
    orch.store.save(session)
    asks = open_asks(session)
    ready = _artifact(session, "data_readiness") or {}
    return {
        "asks": asks,
        "markdown": asks_markdown(asks, session=session),
        "data_readiness": ready,
        "later_count": ready.get("later_count") or 0,
        "summary": ready.get("summary") or "",
    }


@app.get("/api/sessions/{session_id}/peers")
async def get_peers(session_id: str, user: User = Depends(get_current_user)):
    from iis.finance.competitor_discovery import build_peer_payload

    session = _owned_session(session_id, user)
    return build_peer_payload(session)


@app.post("/api/sessions/{session_id}/peers")
async def post_peers(session_id: str, body: PeerUpdateRequest, user: User = Depends(get_current_user)):
    """Confirm / reject / add peers (saved per company, reused in future research)."""
    from iis.agents.user_communicator import apply_user_asks
    from iis.finance.competitor_discovery import apply_competitor_discovery, update_peers

    session = _owned_session(session_id, user)
    if not session.company_key:
        raise HTTPException(400, "Session has no company yet")
    update_peers(session, confirm=body.confirm, reject=body.reject, add=body.add,
                 confirm_all=body.confirm_all, replace=body.replace)
    apply_competitor_discovery(session)
    apply_user_asks(session)
    orch.store.save(session)
    return _artifact(session, "competitor_suggestions")


class ChatProposalRequest(BaseModel):
    proposal: dict
    accept: bool = True


@app.post("/api/sessions/{session_id}/chat/proposals")
async def confirm_chat_proposal(
    session_id: str,
    body: ChatProposalRequest,
    user: User = Depends(get_current_user),
):
    """Accept or dismiss a chat_router proposal (company rule / note / re-analyze)."""
    from iis.context.chat_router import apply_proposal

    session = _owned_session(session_id, user)
    if not body.accept:
        return {"ok": True, "dismissed": True}
    result = apply_proposal(session, body.proposal or {})
    action = result.get("action")
    job_id = None
    if result.get("ok") and action in ("rerun_analysis", "update_report"):
        owner = user.id if settings.auth_required else None
        if action == "update_report":
            try:
                orch.ingest(session_id, owner_id=owner)
            except Exception:
                pass
        try:
            job = orch.analyze_async(session_id, owner_id=owner)
            job_id = getattr(job, "id", None)
            result["job_id"] = job_id
            result["handoff"] = {"action": action, "job_id": job_id}
        except Exception as e:
            result["error"] = str(e)
    elif result.get("ok") and action == "regenerate_report":
        owner = user.id if settings.auth_required else None
        try:
            note = orch.generate_note(session_id, owner_id=owner)
            result["note_len"] = len(note or "")
            result["handoff"] = {"action": "show_report"}
        except Exception as e:
            result["error"] = str(e)
    orch.store.save(session)
    return result


@app.post("/api/sessions/{session_id}/peers/upload")
async def upload_peer_files(
    session_id: str,
    peer_name: str = Form(...),
    files: list[UploadFile] = File(...),
    sections: str | None = Form(None),
    user: User = Depends(get_current_user),
):
    """Upload Capital IQ / AR / concalls for a named peer into that peer's library."""
    from pathlib import Path

    from iis.agents.user_communicator import apply_user_asks
    from iis.finance.competitor_discovery import apply_competitor_discovery, update_peers
    from iis.library.detect import company_key_from_info
    from iis.library.store import get_company_library_store
    from iis.models.artifacts import CompanyInfo

    session = _owned_session(session_id, user)
    if not session.company_key:
        raise HTTPException(400, "Session has no company yet")
    name = (peer_name or "").strip()
    if not name:
        raise HTTPException(400, "peer_name required")
    if not files:
        raise HTTPException(400, "files required")

    update_peers(session, add=[name], confirm=[name])
    peer_info = CompanyInfo(name=name)
    peer_key = company_key_from_info(peer_info, name)
    if not peer_key:
        raise HTTPException(400, "Could not resolve peer company key")

    store = get_company_library_store()
    section_keys = _parse_sections_form(sections, len(files))
    staging = UPLOAD_DIR / session_id / "peer_inbox" / peer_key
    staging.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    kinds: dict[str, str] = {}
    for uf, sec in zip(files, section_keys):
        dest = staging / Path(uf.filename or "file.bin").name
        dest.write_bytes(await uf.read())
        saved.append(str(dest))
        if sec and sec in _UI_SECTION_TO_KIND:
            kinds[dest.name] = _UI_SECTION_TO_KIND[sec]

    # Ingest into a scratch session pointed at the peer so Excel/PDF land in peer library
    peer_session = orch.create_session(name, owner_id=user.id if settings.auth_required else None)
    try:
        orch.upload_files(peer_session.id, saved, owner_id=user.id if settings.auth_required else None, file_kinds=kinds)
        orch.ingest(peer_session.id, owner_id=user.id if settings.auth_required else None)
    except Exception as e:
        raise HTTPException(400, f"Peer ingest failed: {e}") from e

    apply_competitor_discovery(session)
    apply_user_asks(session)
    orch.store.save(session)
    payload = _artifact(session, "competitor_suggestions")
    return {
        "ok": True,
        "peer_name": name,
        "peer_key": peer_key,
        "files": [Path(p).name for p in saved],
        "peers": payload,
        "hint": "Peer files saved. Say **rerun analysis** or click Update report to refresh comparisons.",
    }


class ContextRuleRequest(BaseModel):
    text: str
    scope: str = "global"
    session_id: str | None = None


@app.get("/api/context")
async def get_context(session_id: str | None = None, user: User = Depends(get_current_user)):
    from iis.context.manager import build_context_block, context_snapshot

    session = _owned_session(session_id, user) if session_id else None
    snap = context_snapshot(session)
    snap["llm_block"] = build_context_block(session)
    return snap


@app.post("/api/context")
async def add_context_rule(body: ContextRuleRequest, user: User = Depends(get_current_user)):
    from iis.context.manager import add_directive

    session = _owned_session(body.session_id, user) if body.session_id else None
    try:
        rule = add_directive(session, body.text, scope=body.scope, source="api")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if session:
        orch.store.save(session)
    return rule


@app.delete("/api/context/{rule_id}")
async def delete_context_rule(rule_id: str, session_id: str | None = None, user: User = Depends(get_current_user)):
    from iis.context.manager import remove_directive

    session = _owned_session(session_id, user) if session_id else None
    removed = remove_directive(session, rule_id)
    if not removed:
        raise HTTPException(404, "Rule not found")
    if session:
        orch.store.save(session)
    return removed


class AnalystNotesRequest(BaseModel):
    text: str = ""
    session_id: str | None = None


@app.get("/api/notes")
async def get_analyst_notes_api(session_id: str | None = None, user: User = Depends(get_current_user)):
    from iis.context.manager import get_analyst_notes

    session = _owned_session(session_id, user) if session_id else None
    key = session.company_key if session else None
    return {"company_key": key, "text": get_analyst_notes(key) if key else ""}


@app.put("/api/notes")
async def put_analyst_notes_api(body: AnalystNotesRequest, user: User = Depends(get_current_user)):
    from iis.context.manager import set_analyst_notes

    if not body.session_id:
        raise HTTPException(400, "session_id required")
    session = _owned_session(body.session_id, user)
    if not session.company_key:
        raise HTTPException(400, "Session has no company yet")
    text = set_analyst_notes(session.company_key, body.text)
    return {"company_key": session.company_key, "text": text}


@app.get("/api/llm-calls")
async def llm_calls(limit: int = 50, user: User = Depends(get_current_user)):
    """Audit log of LLM calls (sizes, latency, agent, transcript path)."""
    from iis.context.manager import recent_llm_calls

    return {
        "calls": recent_llm_calls(min(max(limit, 1), 500)),
        "transcript_dir": str(getattr(settings, "llm_transcript_dir", "")),
        "full_transcripts": bool(getattr(settings, "llm_log_full_transcripts", True)),
    }


@app.get("/api/llm-calls/transcript")
async def llm_call_transcript(path: str, user: User = Depends(get_current_user)):
    """Read a saved transcript (.md or .json) under llm_transcript_dir."""
    from pathlib import Path

    root = Path(getattr(settings, "llm_transcript_dir", Path.home() / ".iis" / "logs" / "llm_transcripts")).resolve()
    rel = (path or "").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise HTTPException(400, "Invalid transcript path")
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)) or not target.exists() or not target.is_file():
        raise HTTPException(404, "Transcript not found")
    # Prefer sibling .json when asking for .md content structured
    text = target.read_text(encoding="utf-8", errors="replace")
    return {
        "path": rel,
        "abs_path": str(target),
        "format": target.suffix.lstrip("."),
        "content": text[:500_000],
    }


@app.get("/api/sessions/{session_id}/gates/{gate_id}")
async def preview_gate(session_id: str, gate_id: str, user: User = Depends(get_current_user)):
    owner = user.id if settings.auth_required else None
    try:
        preview = orch.preview_gate(session_id, GateId(gate_id), owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except ValueError:
        raise HTTPException(400, f"Invalid gate: {gate_id}")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return preview


@app.post("/api/sessions/{session_id}/gates/{gate_id}")
async def gate_action(
    session_id: str, gate_id: str, body: GateActionRequest, user: User = Depends(get_current_user)
):
    owner = user.id if settings.auth_required else None
    try:
        session = orch.approve_gate(
            session_id, GateId(gate_id), approve=body.approve, comment=body.comment, owner_id=owner
        )
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except ValueError:
        raise HTTPException(400, f"Invalid gate: {gate_id}")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return _session_summary(session)


@app.patch("/api/sessions/{session_id}/assumptions/{key}")
async def update_assumption(
    session_id: str, key: str, body: AssumptionUpdateRequest, user: User = Depends(get_current_user)
):
    owner = user.id if settings.auth_required else None
    try:
        session = orch.update_assumption(session_id, key, body.value, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except KeyError as e:
        raise HTTPException(400, str(e))
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return _session_summary(session)


@app.get("/api/checklist")
async def get_checklist(user: User = Depends(get_current_user)):
    """Load analysis checklist profile (buckets, thresholds, optional scores)."""
    from iis.finance.checklist import checklist_ui_payload

    return checklist_ui_payload()


class ChecklistUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    buckets: dict[str, bool] | None = None
    pack_sections: dict[str, bool] | None = None
    thresholds: dict[str, float] | None = None
    enable_quality_scores: bool | None = None
    highlight_ratios: list[str] | None = None
    regenerate_session_id: str | None = None


@app.put("/api/checklist")
async def put_checklist(body: ChecklistUpdateRequest, user: User = Depends(get_current_user)):
    """Save checklist profile; optionally regenerate analysis_pack.md for a session."""
    from iis.finance.analysis_pack import write_analysis_pack
    from iis.finance.checklist import apply_checklist_update, checklist_ui_payload

    profile = apply_checklist_update(body.model_dump(exclude_none=True, exclude={"regenerate_session_id"}))
    pack_path = None
    if body.regenerate_session_id:
        owner = user.id if settings.auth_required else None
        try:
            session = orch.get_session(body.regenerate_session_id, owner_id=owner)
            path = write_analysis_pack(session)
            orch.store.save(session)
            pack_path = str(path) if path else None
        except FileNotFoundError:
            raise HTTPException(404, "Session not found for pack regenerate")
        except PermissionError:
            raise HTTPException(403, "Access denied")
    out = checklist_ui_payload(profile)
    out["pack_path"] = pack_path
    return out


@app.post("/api/checklist/reset")
async def reset_checklist(user: User = Depends(get_current_user)):
    """Restore default checklist profile."""
    from iis.finance.checklist import checklist_ui_payload, default_profile, save_checklist_profile

    p = default_profile()
    save_checklist_profile(p)
    return checklist_ui_payload(p)


@app.post("/api/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest, user: User = Depends(get_current_user)):
    owner = user.id if settings.auth_required else None
    try:
        response = orch.ask(session_id, body.question, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return response.model_dump()


@app.post("/api/sessions/{session_id}/note")
async def generate_note(session_id: str, user: User = Depends(get_current_user)):
    """Generate Layer 1 + Layer 2 research report markdown (chat-editable; no Gate 4 required)."""
    owner = user.id if settings.auth_required else None
    try:
        note = orch.generate_note(session_id, owner_id=owner)
        session = orch.get_session(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return {
        "note": note,
        "status": SessionStatus.COMPLETE.value,
        "path": session.research_report_path,
    }


def _session_has_research_report(session) -> bool:
    md = session.research_report_md or ""
    if len(md) > 200:
        return True
    from iis.storage.paths import find_company_research_report, resolve_iis_path

    if resolve_iis_path(session.research_report_path):
        return True
    return find_company_research_report(getattr(session, "company_key", None)) is not None


def _report_markdown(session) -> tuple[str | None, str | None]:
    """Prefer in-memory md; fall back to path (incl. Mac→server remap) or company reports/."""
    from iis.storage.paths import find_company_research_report, resolve_iis_path

    md = session.research_report_md
    path = session.research_report_path
    if md and len(md.strip()) > 50:
        # Still rewrite broken absolute paths when we can
        resolved = resolve_iis_path(path)
        if resolved and str(resolved) != path:
            session.research_report_path = str(resolved)
            try:
                orch.store.save(session)
            except Exception:
                pass
            path = str(resolved)
        return md, path

    candidates = []
    resolved = resolve_iis_path(path)
    if resolved:
        candidates.append(resolved)
    company_hit = find_company_research_report(getattr(session, "company_key", None))
    if company_hit and company_hit not in candidates:
        candidates.append(company_hit)

    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8")
            if not text.strip():
                continue
            session.research_report_md = text
            session.research_report_path = str(p)
            try:
                orch.store.save(session)
            except Exception:
                pass
            return text, str(p)
        except Exception:
            continue
    return md, path


@app.get("/api/sessions/{session_id}/report")
async def get_research_report(session_id: str, user: User = Depends(get_current_user)):
    owner = user.id if settings.auth_required else None
    try:
        session = orch.get_session(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    md, path = _report_markdown(session)
    return {
        "markdown": md,
        "path": path,
    }


def _direct_report_markdown(session) -> tuple[str | None, str | None]:
    from iis.storage.paths import resolve_iis_path

    md = getattr(session, "direct_llm_report_md", None)
    path = getattr(session, "direct_llm_report_path", None)
    if md and len(md.strip()) > 50:
        return md, path
    resolved = resolve_iis_path(path)
    if resolved:
        try:
            text = resolved.read_text(encoding="utf-8")
            if text.strip():
                session.direct_llm_report_md = text
                session.direct_llm_report_path = str(resolved)
                try:
                    orch.store.save(session)
                except Exception:
                    pass
                return text, str(resolved)
        except Exception:
            pass
    return md, path


@app.get("/api/sessions/{session_id}/direct-report")
async def get_direct_llm_report(session_id: str, user: User = Depends(get_current_user)):
    """Separate Direct-LLM report tab (document-read path) + A/B token compare."""
    owner = user.id if settings.auth_required else None
    try:
        session = orch.get_session(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    md, path = _direct_report_markdown(session)
    compare = next(
        (a.payload for a in session.agent_artifacts if a.artifact_type == "report_compare"),
        None,
    )
    return {
        "markdown": md,
        "path": path,
        "compare": compare,
    }


@app.post("/api/sessions/{session_id}/direct-report")
async def regenerate_direct_llm_report(session_id: str, user: User = Depends(get_current_user)):
    """Re-run Direct LLM report only (does not overwrite pipeline Report tab)."""
    owner = user.id if settings.auth_required else None
    try:
        session = orch.get_session(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    from iis.finance.direct_llm_report import build_direct_llm_report

    result = build_direct_llm_report(session)
    try:
        orch.store.save(session)
    except Exception:
        pass
    return {
        "ok": bool(result.get("ok")),
        "path": result.get("path") or session.direct_llm_report_path,
        "compare": result.get("compare"),
        "length": len(session.direct_llm_report_md or ""),
        "error": result.get("error"),
    }


@app.put("/api/sessions/{session_id}/report")
async def save_research_report(
    session_id: str,
    body: ReportUpdateRequest,
    user: User = Depends(get_current_user),
):
    owner = user.id if settings.auth_required else None
    try:
        session = orch.save_research_report(session_id, body.markdown, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    return {"ok": True, "path": session.research_report_path, "length": len(body.markdown or "")}


@app.post("/api/sessions/{session_id}/report/finalize-docx")
async def finalize_report_docx(session_id: str, user: User = Depends(get_current_user)):
    """Convert current report markdown to Word and upload to Drive when configured."""
    owner = user.id if settings.auth_required else None
    try:
        result = orch.finalize_report_docx(session_id, owner_id=owner)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found")
    except PermissionError:
        raise HTTPException(403, "Access denied")
    if not result.get("ok"):
        raise HTTPException(500, "; ".join(result.get("errors") or ["docx export failed"]))
    return result
