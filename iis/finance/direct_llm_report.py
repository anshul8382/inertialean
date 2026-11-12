"""Direct-LLM initiating coverage — separate from the retrieval/analyst Report.

Pipeline A (Report tab): Python packs + retrieval + tool-loop analyst.
Pipeline B (Direct LLM / Compare): raw uploaded PDF pages + write guidelines only.
  No chunk retrieval, no research packs, no Python fact extraction into the prompt.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable

from iis.config import settings
from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str, str, float], None]

_PDF_KINDS = {"annual_report", "transcript", "earnings_presentation", "brokerage_report"}

_REPORT_SYSTEM = (
    "You write finished initiating-coverage equity research reports in markdown.\n"
    "OUTPUT RULES (critical):\n"
    "- Your entire reply IS the report. No preamble, no planning, no 'I need to', "
    "no 'Let me organize', no source catalogues, no batch-by-batch narration.\n"
    "- First line must be a markdown heading such as `## Business overview`.\n"
    "- Use only facts from the provided pages/notes. Never invent figures.\n"
    "- Do not use general knowledge about the company.\n"
    "- Label brokerage opinions as STREET.\n"
    "- Revenue/sales mix is NOT energy/ESG/BRSR/water mix.\n"
    "- Omit sections with no evidence.\n"
    "Section order when evidence exists: Business overview; Investment case; "
    "Rejection/bear; Financials that test the story; Guidance/outlook; "
    "Street thesis stress-test; Valuation; Risks & catalysts; Data gaps."
)

_BATCH_SYSTEM = (
    "Extract initiating-coverage FACTS from the attached PDF page images.\n"
    "OUTPUT RULES: bullet list only. No planning, no 'I need to', no essay.\n"
    "Include: business model, segments, geography, customers, financials, "
    "product/sales mix (NOT energy/ESG), guidance, capex, capacity, competition, "
    "risks, governance; if broker note: thesis/estimates/TP (STREET).\n"
    "Cite (file p.N). Do not invent numbers."
)

_COT_MARKERS = (
    "i need to write",
    "let me organize",
    "let me carefully",
    "organize the evidence",
    "catalogue what's in each",
    "batch 1 (",
    "sources:",
    "we need answer",
    "the user wants",
)


def _looks_like_planning(text: str) -> bool:
    """True when the model dumped chain-of-thought instead of a report."""
    t = (text or "").strip().lower()
    if not t:
        return True
    hits = sum(1 for m in _COT_MARKERS if m in t[:2500])
    if hits >= 2:
        return True
    if t.startswith("i need to") or t.startswith("let me "):
        return True
    # Real reports start with a heading fairly soon
    head = t[:400]
    if "## " not in head and "# " not in head and hits >= 1:
        return True
    return False


def _coerce_report_body(text: str) -> str:
    """If CoT precedes a real heading, keep from the first report heading onward."""
    import re

    raw = (text or "").strip()
    if not raw:
        return ""
    m = re.search(
        r"(?im)^(#{1,3}\s+(business overview|investment case|rejection|financials|"
        r"guidance|outlook|street|valuation|risks|data gaps|company overview).*)",
        raw,
    )
    if m and m.start() > 80:
        return raw[m.start() :].strip()
    return raw


def _company_label(session: ResearchSession) -> str:
    return session.company.name if session.company else (session.company_key or "Company")


def _limits() -> dict[str, Any]:
    return {
        "max_files": max(1, int(getattr(settings, "direct_llm_max_files", 8) or 8)),
        # Front matter alone misses strategy pages (~60–80 in large ARs) — take more pages
        "pages_per_file": max(1, int(getattr(settings, "direct_llm_pages_per_file", 24) or 24)),
        "pages_per_batch": max(1, int(getattr(settings, "direct_llm_pages_per_batch", 6) or 6)),
        "scale": float(getattr(settings, "direct_llm_image_scale", 1.15) or 1.15),
        "quality": int(getattr(settings, "direct_llm_jpeg_quality", 70) or 70),
        "synth_chars": max(4000, int(getattr(settings, "direct_llm_synth_chars", 28000) or 28000)),
        "timeout": float(getattr(settings, "direct_llm_timeout_sec", 120.0) or 120.0),
        # DeepSeek spends hidden reasoning tokens first — keep completion budget high
        "max_tokens": max(1024, int(getattr(settings, "direct_llm_max_tokens", 4096) or 4096)),
        "synth_max_tokens": max(
            2048, int(getattr(settings, "direct_llm_synth_max_tokens", 5000) or 5000)
        ),
    }


def _resolve_pdf_docs(session: ResearchSession) -> list[dict[str, Any]]:
    """Raw library PDFs only — no chunk/retrieval selection."""
    lim = _limits()
    key = session.company_key
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    if key:
        try:
            from iis.library.store import CompanyLibraryStore

            lib = CompanyLibraryStore().load(key)
            for d in lib.documents:
                kind = (d.doc_kind or "").strip()
                if kind not in _PDF_KINDS:
                    continue
                sp = (d.stored_path or "").strip()
                if not sp:
                    continue
                path = Path(sp)
                if not path.is_file():
                    continue
                name = d.filename or path.name
                if name in seen:
                    continue
                seen.add(name)
                found.append(
                    {
                        "file": name,
                        "doc_type": kind,
                        "path": str(path.resolve()),
                        "size": path.stat().st_size,
                    }
                )
        except Exception as exc:
            logger.info("direct LLM library resolve failed: %s", exc)

    # Fallback: company section folders by filename seen in session
    if key and len(found) < lim["max_files"]:
        root = settings.data_dir / "companies" / key.replace("/", "_")
        names_from_session = {
            getattr(c, "file", None)
            for c in (session.pdf_chunks or [])
            if getattr(c, "file", None)
        }
        for folder, kind in (
            ("annual_reports", "annual_report"),
            ("concalls", "transcript"),
            ("presentations", "earnings_presentation"),
            ("brokerage_reports", "brokerage_report"),
        ):
            ddir = root / folder
            if not ddir.is_dir():
                continue
            for path in sorted(ddir.glob("*.pdf")):
                if path.name in seen:
                    continue
                if names_from_session and path.name not in names_from_session:
                    # Prefer session-known files when present; else take all
                    if len(names_from_session) >= 1 and folder != "annual_reports":
                        continue
                seen.add(path.name)
                found.append(
                    {
                        "file": path.name,
                        "doc_type": kind,
                        "path": str(path.resolve()),
                        "size": path.stat().st_size,
                    }
                )

    order = {
        "annual_report": 0,
        "transcript": 1,
        "brokerage_report": 2,
        "earnings_presentation": 3,
    }
    found.sort(key=lambda d: (order.get(str(d.get("doc_type") or ""), 9), -(d.get("size") or 0)))
    # Keep doc-type diversity (don't fill all slots with giant ARs)
    selected: list[dict[str, Any]] = []
    ar_n = 0
    for d in found:
        if d.get("doc_type") == "annual_report":
            if ar_n >= 2:
                continue
            ar_n += 1
        selected.append(d)
        if len(selected) >= lim["max_files"]:
            break
    return selected




def _render_doc_pages(doc: dict[str, Any], lim: dict[str, Any]) -> list[dict[str, Any]]:
    from iis.finance.pdf_pages import render_pdf_pages

    pages = render_pdf_pages(
        doc["path"],
        max_pages=lim["pages_per_file"],
        scale=lim["scale"],
        jpeg_quality=lim["quality"],
    )
    for p in pages:
        p["file"] = doc["file"]
        p["doc_type"] = doc.get("doc_type")
    return pages


def _batch_pages(pages: list[dict[str, Any]], pages_per_batch: int) -> list[list[dict[str, Any]]]:
    n = max(1, pages_per_batch)
    return [pages[i : i + n] for i in range(0, len(pages), n)] or [[]]


def _user_content_for_pages(
    company: str,
    batch_idx: int,
    batch_total: int,
    pages: list[dict[str, Any]],
    *,
    final_single_shot: bool,
) -> list[dict[str, Any]]:
    files = sorted({str(p.get("file") or "") for p in pages})
    if final_single_shot:
        text = (
            f"Company: {company}\n"
            f"Source files (raw PDF pages attached as images): {', '.join(files)}\n\n"
            "Write a full initiating-coverage report in markdown from these pages only. "
            "Cite source filenames and page numbers inline like `(source: AR.pdf p.3)`.\n"
        )
    else:
        text = (
            f"Company: {company}\n"
            f"Page batch {batch_idx} of {batch_total}. "
            f"Files: {', '.join(files)}\n"
            "Pages attached as images below (raw document — not retrieved excerpts).\n"
            "Extract initiating-coverage facts visible on these pages only.\n"
        )
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for p in pages:
        parts.append(
            {
                "type": "text",
                "text": f"[{p.get('file')} · page {p.get('page')} · {p.get('doc_type')}]",
            }
        )
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{p.get('mime') or 'image/jpeg'};base64,{p.get('b64')}",
                },
            }
        )
    return parts


def _gate_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "calls": int(after.get("llm_calls_total") or 0) - int(before.get("llm_calls_total") or 0),
        "calls_ok": int(after.get("llm_calls_ok") or 0) - int(before.get("llm_calls_ok") or 0),
        "calls_fail": int(after.get("llm_calls_fail") or 0) - int(before.get("llm_calls_fail") or 0),
        "prompt_tokens": int(after.get("llm_prompt_tokens") or 0) - int(before.get("llm_prompt_tokens") or 0),
        "completion_tokens": int(after.get("llm_completion_tokens") or 0)
        - int(before.get("llm_completion_tokens") or 0),
        "tokens_total": int(after.get("llm_tokens_total") or 0) - int(before.get("llm_tokens_total") or 0),
        "tokens_estimated": bool(after.get("llm_tokens_estimated")),
    }


def build_direct_llm_report(
    session: ResearchSession,
    *,
    progress_cb: ProgressCb | None = None,
    pipeline_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Raw PDF pages + guidelines → separate Direct LLM report (no retrieval)."""
    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError, get_llm_gate

    def _p(phase: str, detail: str, prog: float) -> None:
        if progress_cb:
            try:
                progress_cb(phase, detail, prog)
            except Exception:
                pass

    if not bool(getattr(settings, "direct_llm_report_enabled", True)):
        return {"ok": False, "skipped": True, "reason": "direct_llm_report_enabled=false"}

    gate = get_llm_gate()
    before = gate.snapshot()
    lim = _limits()
    docs = _resolve_pdf_docs(session)
    if not docs:
        return {"ok": False, "error": "no_pdf_files"}

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        return {"ok": False, "error": "llm_unavailable"}

    company = _company_label(session)
    timeout = lim["timeout"]
    max_tokens = lim["max_tokens"]

    _p("direct_llm_render", f"Direct LLM · rendering {len(docs)} PDF(s) to page images…", 0.96)
    all_pages: list[dict[str, Any]] = []
    per_doc_meta: list[dict[str, Any]] = []
    for doc in docs:
        gate.check_abort()
        try:
            pages = _render_doc_pages(doc, lim)
        except Exception as exc:
            logger.info("direct LLM render failed %s: %s", doc.get("file"), exc)
            per_doc_meta.append(
                {
                    "file": doc.get("file"),
                    "doc_type": doc.get("doc_type"),
                    "error": str(exc)[:200],
                    "pages": 0,
                }
            )
            continue
        all_pages.extend(pages)
        per_doc_meta.append(
            {
                "file": doc.get("file"),
                "doc_type": doc.get("doc_type"),
                "pages": len(pages),
                "page_bytes": sum(int(p.get("bytes") or 0) for p in pages),
                "path": doc.get("path"),
            }
        )

    if not all_pages:
        return {"ok": False, "error": "pdf_render_failed", "per_doc": per_doc_meta}

    batches = _batch_pages(all_pages, lim["pages_per_batch"])
    single_shot = len(batches) == 1
    notes: list[str] = []

    with llm_agent("direct_llm_pages"):
        for bi, batch in enumerate(batches):
            gate.check_abort()
            _p(
                "direct_llm_read",
                (
                    f"Direct LLM · page batch {bi + 1}/{len(batches)} · "
                    f"{len(batch)} page image(s)"
                ),
                0.97 + 0.015 * (bi / max(len(batches), 1)),
            )
            content = _user_content_for_pages(
                company,
                bi + 1,
                len(batches),
                batch,
                final_single_shot=single_shot,
            )
            system = _REPORT_SYSTEM if single_shot else _BATCH_SYSTEM
            try:
                raw = llm.chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": content},
                    ],
                    temperature=0.15,
                    timeout=timeout,
                    max_tokens=lim["synth_max_tokens"] if single_shot else max(1200, max_tokens // 2),
                    allow_reasoning_fallback=False,
                )
            except LLMAbortError:
                raise
            except Exception as exc:
                logger.info("direct LLM page batch %s failed: %s", bi + 1, exc)
                raw = f"_Page batch {bi + 1} failed: {exc}_"
            note = _coerce_report_body((raw or "").strip()) if single_shot else (raw or "").strip()
            if single_shot and _looks_like_planning(note):
                # One strict retry for single-shot planning dumps
                try:
                    raw2 = llm.chat(
                        [
                            {"role": "system", "content": _REPORT_SYSTEM},
                            {
                                "role": "user",
                                "content": content
                                + [
                                    {
                                        "type": "text",
                                        "text": (
                                            "RETRY: Previous reply was planning text, not a report. "
                                            "Output the report now. First line: ## Business overview"
                                        ),
                                    }
                                ]
                                if isinstance(content, list)
                                else (
                                    str(content)
                                    + "\n\nRETRY: Output the report only. First line: ## Business overview"
                                ),
                            },
                        ],
                        temperature=0.1,
                        timeout=timeout,
                        max_tokens=lim["synth_max_tokens"],
                        allow_reasoning_fallback=False,
                    )
                    note = _coerce_report_body((raw2 or "").strip())
                except LLMAbortError:
                    raise
                except Exception as exc:
                    logger.info("direct LLM single-shot retry failed: %s", exc)
            if single_shot:
                notes.append(note)
            else:
                files = sorted({str(p.get("file") or "") for p in batch})
                pages_s = ",".join(str(p.get("page")) for p in batch)
                notes.append(
                    f"## Batch {bi + 1} ({', '.join(files)} · pages {pages_s})\n\n{note}\n"
                )

    if single_shot:
        report_md = notes[0] if notes else ""
    else:
        _p("direct_llm_synth", "Direct LLM · synthesizing report from page batches…", 0.985)
        notes_blob = "\n\n".join(notes)[: lim["synth_chars"]]
        synth_prompt = (
            f"Company: {company}\n\n"
            "Write the finished initiating-coverage REPORT now from the FACT NOTES below "
            "(notes came from raw PDF page images). Cite (file p.N).\n"
            "Your reply must START with `## Business overview` and contain ONLY the report — "
            "no planning, no 'I need to', no listing batches.\n\n"
            "=== FACT NOTES ===\n"
            f"{notes_blob}\n"
        )
        report_md = ""
        with llm_agent("direct_llm_synth"):
            try:
                report_md = (
                    llm.chat(
                        [
                            {"role": "system", "content": _REPORT_SYSTEM},
                            {"role": "user", "content": synth_prompt},
                        ],
                        temperature=0.15,
                        timeout=min(180.0, timeout + 30),
                        max_tokens=lim["synth_max_tokens"],
                        allow_reasoning_fallback=False,
                    )
                    or ""
                ).strip()
            except LLMAbortError:
                raise
            except Exception as exc:
                logger.info("direct LLM synth failed: %s", exc)
                report_md = (
                    "# Direct LLM report (partial)\n\n"
                    f"_Synthesis failed: {exc}_\n\n"
                    "## Page-batch notes\n\n"
                    + notes_blob[:12000]
                )

            report_md = _coerce_report_body(report_md)
            if _looks_like_planning(report_md):
                _p("direct_llm_synth", "Direct LLM · retry — model returned planning, not report…", 0.988)
                retry_prompt = (
                    f"Company: {company}\n\n"
                    "Your previous answer was internal planning, not a report. "
                    "Write the buy-side initiating-coverage report NOW.\n"
                    "FIRST LINE must be exactly: ## Business overview\n"
                    "Use ONLY these fact notes. Cite sources. No preamble.\n\n"
                    f"{notes_blob[: lim['synth_chars']]}\n"
                )
                try:
                    report_md = (
                        llm.chat(
                            [
                                {"role": "system", "content": _REPORT_SYSTEM},
                                {"role": "user", "content": retry_prompt},
                            ],
                            temperature=0.1,
                            timeout=min(180.0, timeout + 30),
                            max_tokens=lim["synth_max_tokens"],
                            allow_reasoning_fallback=False,
                        )
                        or ""
                    ).strip()
                    report_md = _coerce_report_body(report_md)
                except LLMAbortError:
                    raise
                except Exception as exc:
                    logger.info("direct LLM synth retry failed: %s", exc)

        if _looks_like_planning(report_md):
            # Last resort: do not publish CoT as the report
            report_md = (
                "# Direct LLM report\n\n"
                "_Model returned planning text instead of a report after retry. "
                "Re-run Direct LLM; page-batch notes are below for reference._\n\n"
                "## Page-batch notes\n\n"
                + notes_blob[:12000]
            )

    if len(report_md) < 200:
        report_md = (
            "# Direct LLM report\n\n_Insufficient model output._\n\n"
            + "\n\n".join(notes)[:8000]
        )

    header = (
        f"# Direct LLM initiating coverage — {company}\n\n"
        f"_Separate path from **Report** (retrieval + analyst). "
        f"Built from {len(docs)} raw PDF file(s) · {len(all_pages)} page image(s) "
        f"+ write guidelines only — no retrieval packs._\n\n"
        "---\n\n"
    )
    full = header + report_md

    path_str = None
    try:
        from iis.integrations.gdrive import company_folder_name

        safe = company_folder_name(company).replace(" ", "_")
        local_dir = (
            settings.data_dir / "companies" / (session.company_key or safe) / "reports"
        )
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / f"{safe}_Direct_LLM_Report_{date.today().isoformat()}.md"
        local_path.write_text(full, encoding="utf-8")
        path_str = str(local_path)
    except Exception as exc:
        logger.info("direct LLM disk save failed: %s", exc)

    session.direct_llm_report_md = full
    session.direct_llm_report_path = path_str

    after = gate.snapshot()
    direct_stats = _gate_delta(before, after)
    direct_stats.update(
        {
            "report_chars": len(full),
            "documents_read": len(docs),
            "pages_sent": len(all_pages),
            "read_batches": len(batches),
            "per_doc": per_doc_meta,
            "path": path_str,
            "mode": "raw_pdf_pages",
        }
    )

    compare = {
        "pipeline_report": pipeline_stats or {},
        "direct_llm_report": direct_stats,
        "company": company,
        "note": (
            "Left = Report tab: retrieval packs + LLM analyst. "
            "Right = Direct LLM: raw PDF page images + write guidelines only."
        ),
    }
    session.agent_artifacts = [
        a for a in session.agent_artifacts if a.artifact_type != "report_compare"
    ]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="direct_llm_report",
            artifact_type="report_compare",
            payload=compare,
            source_tier=SourceTier.AGENT,
        )
    )
    _p(
        "direct_llm_done",
        (
            f"Direct LLM ready · {len(all_pages)} pages · "
            f"{direct_stats.get('calls', 0)} calls · "
            f"~{direct_stats.get('tokens_total', 0):,} tokens"
        ),
        0.99,
    )
    return {"ok": True, "markdown": full, "path": path_str, "compare": compare}
