"""Structured annual-report section reading: full text → LLM bullets + guidelines.

Typed handlers (not generic snippet retrieval):
  leadership_letters, future_outlook, governance, financial_capital, mda

MD&A explicitly skips India-GDP boilerplate and keeps sector-specific industry/
policy sections. Financial capital Capex is reconciled against CIQ.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from iis.config import settings
from iis.finance.ar_section_guidelines import SECTION_GUIDELINES
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

# (handler_id, match patterns against section title / hint)
_SECTION_MATCHERS: list[tuple[str, tuple[str, ...]]] = [
    (
        "leadership_letters",
        (
            "chairman",
            "chairperson",
            "ceo message",
            "ceo's message",
            "managing director",
            "letter from the",
            "letter to shareholder",
            "from the desk",
            "md's message",
            "message from",
        ),
    ),
    (
        "future_outlook",
        (
            "future outlook",
            "outlook",
            "future prospects",
            "way forward",
            "looking ahead",
        ),
    ),
    (
        "governance",
        (
            "corporate governance",
            "related party",
            "board's report",
            "boards report",
            "report on corporate governance",
        ),
    ),
    (
        "financial_capital",
        (
            "financial capital",
            "capital allocation",
            "cash flow",
            "funds flow",
        ),
    ),
    (
        "mda",
        (
            "md&a",
            "management discussion",
            "management’s discussion",
            "management's discussion",
        ),
    ),
]

_GDP_BOILERPLATE = re.compile(
    r"(?is)(?:"
    r"(?:india(?:'?s)?|indian)\s+(?:gdp|economy|macro(?:economic)?)\s+"
    r"(?:grew|growth|expanded|estimated|projected|expected).{0,400}"
    r"|(?:global\s+)?gdp\s+growth\s+(?:was|is|of)\s+\d"
    r"|international\s+monetary\s+fund.{0,120}gdp"
    r")"
)

_HANDLER_ORDER = (
    "leadership_letters",
    "future_outlook",
    "governance",
    "financial_capital",
    "mda",
    "other_keep",
)

_HANDLER_TITLES = {
    "leadership_letters": "Leadership letters (LLM bullets)",
    "future_outlook": "Future outlook (LLM bullets)",
    "governance": "Governance (LLM + guidelines)",
    "financial_capital": "Financial capital (LLM + CIQ Capex check)",
    "mda": "MD&A — sector & company (LLM; GDP skipped)",
    "other_keep": "Other thesis-relevant AR sections (LLM)",
}


def _notify(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _match_handler(section_name: str) -> str | None:
    low = (section_name or "").lower()
    for hid, pats in _SECTION_MATCHERS:
        if any(p in low for p in pats):
            # Avoid classifying every "Outlook" inside governance titles wrongly —
            # more specific matchers already listed first for letters/gov.
            if hid == "future_outlook" and any(
                g in low for g in ("governance", "corporate social", "brsr")
            ):
                continue
            return hid
    return None


def _section_full_text(chunks: list[Any], *, max_chars: int) -> tuple[str, list[int]]:
    parts: list[str] = []
    pages: list[int] = []
    used = 0
    for c in chunks:
        text = (getattr(c, "text", None) or "").strip()
        if len(text) < 40:
            continue
        page = getattr(c, "page", None)
        piece = f"[p.{page}] {text}" if page is not None else text
        if used + len(piece) > max_chars:
            room = max_chars - used
            if room < 200:
                break
            parts.append(piece[:room])
            if page is not None:
                pages.append(int(page))
            break
        parts.append(piece)
        used += len(piece) + 2
        if page is not None:
            pages.append(int(page))
    return "\n\n".join(parts), sorted(set(pages))


def _strip_gdp_boilerplate(text: str) -> str:
    """Drop generic India/global GDP paragraphs; keep sector/company prose."""
    if not text:
        return ""
    # Split into rough paragraphs
    paras = re.split(r"\n{2,}|(?<=\.)\s+(?=[A-Z])", text)
    kept: list[str] = []
    for p in paras:
        p = p.strip()
        if len(p) < 60:
            kept.append(p)
            continue
        if _GDP_BOILERPLATE.search(p) and not re.search(
            r"agrochem|crop\s+protect|biological|CSM|CRDMO|pharma|"
            r"policy\s+and\s+trade|structural\s+shift|sector|industry\s+structure",
            p,
            re.I,
        ):
            continue
        kept.append(p)
    return "\n\n".join(kept)


def _parse_bullets(raw: str) -> list[str]:
    lines: list[str] = []
    for ln in (raw or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        s = re.sub(r"^[-*•]\s+", "", s)
        s = re.sub(r"^\d+[.)]\s+", "", s)
        if len(s) < 8:
            continue
        # Keep markdown headings as-is for grouping
        if s.startswith("#"):
            lines.append(s)
            continue
        lines.append(s)
    return lines[:40]


def _llm_bullets(
    *,
    handler_id: str,
    company: str,
    filename: str,
    section_name: str,
    text: str,
    timeout: float,
) -> list[str]:
    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    system = SECTION_GUIDELINES.get(handler_id) or SECTION_GUIDELINES["mda"]
    prompt = (
        f"Company: {company}\n"
        f"File: {filename}\n"
        f"Section: {section_name}\n\n"
        "Summarise the FULL section text below per the system rules. "
        "Bullets only.\n\n"
        "=== SECTION TEXT ===\n"
        f"{text}\n"
    )
    llm = get_llm_client(finance=True)
    if not llm.is_available():
        raise RuntimeError("llm_unavailable")
    with llm_agent(f"ar_section_{handler_id}"):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                timeout=timeout,
                max_tokens=int(getattr(settings, "ar_section_max_tokens", 1800) or 1800),
                allow_reasoning_fallback=False,
            )
        except TypeError:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                timeout=timeout,
            )
        except LLMAbortError:
            raise
    return _parse_bullets(raw or "")


def _llm_bullets_multi(
    *,
    company: str,
    filename: str,
    items: list[dict[str, Any]],
    timeout: float,
) -> dict[str, list[str]]:
    """One Hive call: deep-read several smaller sections. Returns section → bullets."""
    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    llm = get_llm_client(finance=True)
    if not llm.is_available():
        raise RuntimeError("llm_unavailable")
    blocks = []
    for it in items:
        mode = it.get("mode") or "other_keep"
        guide = (SECTION_GUIDELINES.get(mode) or SECTION_GUIDELINES["other_keep"])[:400]
        blocks.append(
            f"===== SECTION: {it['section']} =====\n"
            f"Mode: {mode}\nGuidelines: {guide}\n\n"
            f"{it.get('text') or ''}\n"
        )
    system = (
        "You are doing a DEEP READ of multiple annual-report sections for initiating coverage.\n"
        "For EACH section, output markdown:\n"
        "## <exact SECTION name>\n"
        "- bullet\n- bullet\n"
        "Use only facts from that section. Do not invent numbers. CIQ owns ₹ totals.\n"
        "Keep bullets dense and analyst-useful."
    )
    prompt = (
        f"Company: {company}\nFile: {filename}\n"
        f"Deep-read these {len(items)} sections:\n\n" + "\n".join(blocks)
    )
    max_tok = int(getattr(settings, "ar_section_max_tokens", 2500) or 2500) * min(3, len(items))
    max_tok = min(6000, max(2500, max_tok))
    with llm_agent("ar_section_batch"):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                timeout=timeout,
                max_tokens=max_tok,
                allow_reasoning_fallback=False,
            )
        except TypeError:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                timeout=timeout,
            )
        except LLMAbortError:
            raise
    # Split on ## headers
    out: dict[str, list[str]] = {str(it["section"]): [] for it in items}
    current: str | None = None
    for ln in (raw or "").splitlines():
        m = re.match(r"^##\s+(.+)$", ln.strip())
        if m:
            title = m.group(1).strip()
            current = None
            for sname in out:
                if sname.lower() == title.lower() or sname.lower() in title.lower() or title.lower() in sname.lower():
                    current = sname
                    break
            if current is None:
                current = title
                out.setdefault(current, [])
            continue
        if current is None:
            continue
        s = ln.strip()
        if not s:
            continue
        s = re.sub(r"^[-*•]\s+", "", s)
        s = re.sub(r"^\d+[.)]\s+", "", s)
        if len(s) >= 8:
            out[current].append(s)
    for it in items:
        sname = str(it["section"])
        if not out.get(sname):
            # fallback: empty — caller may solo-retry
            out[sname] = out.get(sname) or []
    return {k: v[:40] for k, v in out.items()}


def _ciq_capex_snapshot(session: ResearchSession) -> dict[str, Any]:
    try:
        from iis.finance.metric_series import build_capex_analysis

        return build_capex_analysis(session.financial_line_items or [], recent_years=4) or {}
    except Exception:
        return {}


def _extract_ar_capex_mentions(bullets: list[str], text: str) -> list[str]:
    blob = "\n".join(bullets) + "\n" + (text or "")
    hits: list[str] = []
    for m in re.finditer(
        r"(?i)(?:capex|capital\s+expenditure|capital\s+outlay)[^\n.]{0,80}"
        r"(?:₹|INR|Rs\.?|INR\s*mn|₹\s*mn|crore|cr\.?)[^\n.]{0,40}",
        blob,
    ):
        hits.append(re.sub(r"\s+", " ", m.group(0)).strip()[:180])
        if len(hits) >= 6:
            break
    # Also catch "AR Capex:" tagged bullets
    for b in bullets:
        if re.search(r"(?i)ar\s*capex|capex", b) and re.search(r"\d", b):
            if b not in hits:
                hits.append(b[:180])
    return hits[:8]


def _reconcile_capex(ar_mentions: list[str], ciq: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in (ciq.get("rows") or []) if isinstance(r, dict)]
    # Prefer last 4 CIQ years
    rows = rows[-4:] if len(rows) > 4 else rows
    ciq_lines: list[str] = []
    for row in rows:
        y = row.get("period") or row.get("period_end") or ""
        v = row.get("capex_mn")
        if v is None:
            continue
        pct = row.get("capex_pct_revenue")
        extra = f" ({pct:.1%} of sales)" if isinstance(pct, (int, float)) else ""
        try:
            ciq_lines.append(f"CIQ Capex {y}: ₹{float(v):,.0f} mn{extra}")
        except (TypeError, ValueError):
            ciq_lines.append(f"CIQ Capex {y}: {v}{extra}")
    if ciq.get("recent_avg_mn") is not None:
        ciq_lines.append(
            f"CIQ recent avg Capex: ₹{ciq['recent_avg_mn']:,.0f} mn"
            + (
                f" ({ciq['recent_avg_pct_sales']:.1%} of sales)"
                if ciq.get("recent_avg_pct_sales") is not None
                else ""
            )
        )
    return {
        "ar_capex_mentions": ar_mentions,
        "ciq_capex_lines": ciq_lines,
        "note": (
            "Cross-check AR Capex narrative vs Capital IQ cash-flow Capex. "
            "CIQ is authoritative for ₹ totals; AR adds purpose/plant colour."
            if (ar_mentions or ciq_lines)
            else "No Capex figures found to reconcile."
        ),
    }


def collect_handler_texts(
    sections: dict[str, list[Any]],
    *,
    max_chars: int,
) -> dict[str, dict[str, Any]]:
    """Map handler_id → combined full text from matching AR sections."""
    buckets: dict[str, dict[str, Any]] = {}
    for sname, chunks in (sections or {}).items():
        hid = _match_handler(sname)
        if not hid:
            continue
        text, pages = _section_full_text(chunks, max_chars=max_chars)
        if len(text) < 80:
            continue
        if hid == "mda":
            text = _strip_gdp_boilerplate(text)
            if len(text) < 80:
                continue
        slot = buckets.setdefault(
            hid,
            {"sections": [], "text_parts": [], "pages": [], "chars": 0},
        )
        slot["sections"].append(sname)
        # Cap total per handler
        room = max_chars - slot["chars"]
        if room < 200:
            continue
        part = text[:room]
        slot["text_parts"].append(f"--- {sname} ---\n{part}")
        slot["chars"] += len(part)
        slot["pages"].extend(pages)
    return buckets


def _apply_critic_drops(handlers: dict[str, Any], drop_bullets: list[str]) -> None:
    if not drop_bullets:
        return
    needles = [re.sub(r"\s+", " ", str(d)).strip().lower()[:120] for d in drop_bullets if d]
    for h in handlers.values():
        if not isinstance(h, dict):
            continue
        kept: list[str] = []
        for b in h.get("bullets") or []:
            bl = re.sub(r"\s+", " ", str(b)).strip().lower()
            if any(n and n in bl for n in needles):
                continue
            kept.append(b)
        h["bullets"] = kept


def _deep_read_one(
    session: ResearchSession,
    *,
    company: str,
    filename: str,
    section_name: str,
    mode: str,
    chunks: list[Any],
    max_chars: int,
    timeout: float,
    ciq_capex: dict[str, Any],
    progress_cb: ProgressCb | None,
) -> dict[str, Any]:
    text, pages = _section_full_text(chunks, max_chars=max_chars)
    if mode == "mda":
        text = _strip_gdp_boilerplate(text)
    if len(text) < 80:
        return {
            "title": _HANDLER_TITLES.get(mode, mode),
            "sections": [section_name],
            "pages": pages,
            "bullets": [],
            "chars_sent": len(text),
            "guidelines": mode,
            "error": "section_too_short_after_clean",
        }
    _notify(
        progress_cb,
        f"AR deep-read · {mode} · {section_name[:40]} · {len(text):,} chars",
    )
    try:
        bullets = _llm_bullets(
            handler_id=mode if mode in SECTION_GUIDELINES else "other_keep",
            company=company,
            filename=filename,
            section_name=section_name,
            text=text,
            timeout=timeout,
        )
    except Exception as exc:
        logger.info("AR deep-read failed %s/%s: %s", filename, section_name, exc)
        return {
            "title": _HANDLER_TITLES.get(mode, mode),
            "sections": [section_name],
            "pages": pages,
            "bullets": [],
            "chars_sent": len(text),
            "guidelines": mode,
            "error": str(exc)[:240],
        }
    entry: dict[str, Any] = {
        "title": _HANDLER_TITLES.get(mode, mode),
        "sections": [section_name],
        "pages": pages,
        "bullets": bullets,
        "chars_sent": len(text),
        "guidelines": mode,
        "source_section": section_name,
    }
    if mode == "financial_capital" or re.search(r"capex|capital\s+expenditure", text, re.I):
        mentions = _extract_ar_capex_mentions(bullets, text)
        if mentions or mode == "financial_capital":
            entry["capex_reconcile"] = _reconcile_capex(mentions, ciq_capex)
    return entry


def _merge_handler_entry(bucket: dict[str, Any], entry: dict[str, Any]) -> None:
    bucket.setdefault("sections", [])
    bucket.setdefault("pages", [])
    bucket.setdefault("bullets", [])
    for s in entry.get("sections") or []:
        if s not in bucket["sections"]:
            bucket["sections"].append(s)
    for p in entry.get("pages") or []:
        if p not in bucket["pages"]:
            bucket["pages"].append(p)
    bucket["bullets"].extend(entry.get("bullets") or [])
    bucket["chars_sent"] = int(bucket.get("chars_sent") or 0) + int(entry.get("chars_sent") or 0)
    if entry.get("capex_reconcile") and not bucket.get("capex_reconcile"):
        bucket["capex_reconcile"] = entry["capex_reconcile"]
    if entry.get("error"):
        bucket.setdefault("errors", []).append(entry["error"])


def build_evidence_register(
    *,
    filename: str,
    handlers: dict[str, Any],
    relevance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Flat claim → file/page list for the report evidence register."""
    rows: list[dict[str, Any]] = []
    for hid, h in (handlers or {}).items():
        if not isinstance(h, dict):
            continue
        pages = h.get("pages") or []
        page_s = ",".join(str(p) for p in pages[:8])
        for b in h.get("bullets") or []:
            if str(b).startswith("#"):
                continue
            rows.append(
                {
                    "claim": str(b)[:400],
                    "file": filename,
                    "pages": page_s,
                    "mode": hid,
                    "sections": ", ".join(h.get("sections") or [])[:120],
                }
            )
    for row in (relevance or {}).get("sections") or []:
        if not isinstance(row, dict) or row.get("decision") != "keep":
            continue
        why = (row.get("why") or "").strip()
        if why:
            pages = row.get("pages") or []
            rows.append(
                {
                    "claim": f"[relevance] {why}"[:400],
                    "file": filename,
                    "pages": ",".join(str(p) for p in pages[:8]),
                    "mode": "relevance",
                    "sections": str(row.get("section") or ""),
                }
            )
    return rows[:200]


def _build_deep_read_packs(
    to_read: list[dict[str, Any]],
    sections: dict[str, list[Any]],
    *,
    max_chars: int,
) -> list[list[dict[str, Any]]]:
    """Group sections into solo (large) or multi (small) deep-read packs."""
    batch_on = bool(getattr(settings, "ar_deep_read_batch_enabled", True))
    solo_at = max(4000, int(getattr(settings, "ar_deep_read_solo_chars", 14000) or 14000))
    batch_chars = max(solo_at, int(getattr(settings, "ar_deep_read_batch_chars", 72000) or 72000))
    batch_n = max(1, int(getattr(settings, "ar_deep_read_batch_sections", 6) or 6))

    prepared: list[dict[str, Any]] = []
    for row in to_read:
        sname = str(row.get("section") or "")
        mode = str(row.get("mode") or _match_handler(sname) or "other_keep")
        if row.get("_legacy_bucket"):
            text = "\n\n".join((row["_legacy_bucket"] or {}).get("text_parts") or [])
            pages = list((row["_legacy_bucket"] or {}).get("pages") or [])
            prepared.append(
                {
                    "section": sname,
                    "mode": mode,
                    "text": text[:max_chars],
                    "pages": pages,
                    "legacy": row.get("_legacy_bucket"),
                }
            )
            continue
        chunks = sections.get(sname) or []
        if not chunks:
            for k, v in sections.items():
                if sname.lower() in k.lower() or k.lower() in sname.lower():
                    chunks = v
                    sname = k
                    break
        text, pages = _section_full_text(chunks, max_chars=max_chars)
        if mode == "mda":
            text = _strip_gdp_boilerplate(text)
        if len(text) < 80:
            continue
        prepared.append(
            {"section": sname, "mode": mode, "text": text, "pages": pages, "legacy": None}
        )

    if not batch_on:
        return [[p] for p in prepared]

    packs: list[list[dict[str, Any]]] = []
    small_buf: list[dict[str, Any]] = []
    small_chars = 0

    def flush_small() -> None:
        nonlocal small_buf, small_chars
        if small_buf:
            packs.append(small_buf)
            small_buf = []
            small_chars = 0

    for p in prepared:
        n = len(p.get("text") or "")
        if n >= solo_at or p.get("legacy"):
            flush_small()
            packs.append([p])
            continue
        if small_buf and (len(small_buf) >= batch_n or small_chars + n > batch_chars):
            flush_small()
        small_buf.append(p)
        small_chars += n
    flush_small()
    return packs


def run_ar_section_pipeline(
    session: ResearchSession,
    *,
    filename: str,
    sections: dict[str, list[Any]],
    progress_cb: ProgressCb | None = None,
    relevance: dict[str, Any] | None = None,
    persist_cb: Any | None = None,
) -> dict[str, Any]:
    """Quick-read → deep-read (batched/parallel) → critic → gap-fill, with checkpoint resume."""
    if not bool(getattr(settings, "ar_structured_llm_enabled", False)):
        return {"ok": False, "skipped": True, "reason": "ar_structured_llm_enabled=false"}

    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from iis.finance.analyze_checkpoint import (
        ar_file_fingerprint,
        get_ar_file_state,
        patch_ar_file_state,
    )
    from iis.finance.ar_relevance import run_ar_relevance_pass, sections_to_deep_read
    from iis.finance.inertia_methodology import open_checklist_gaps
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    if not get_llm_client(finance=True).is_available():
        return {"ok": False, "error": "llm_unavailable", "file": filename}

    max_chars = max(8000, int(getattr(settings, "ar_section_max_chars", 48000) or 48000))
    timeout = float(getattr(settings, "ar_section_timeout_sec", 150.0) or 150.0)
    company = session.company.name if session.company else (session.company_key or "Company")
    parallel = max(1, int(getattr(settings, "llm_max_parallel", 3) or 3))
    fp = ar_file_fingerprint(filename, sections)
    cp = get_ar_file_state(session, filename=filename, fingerprint=fp)

    # Full-file resume
    if cp and cp.get("complete") and isinstance(cp.get("result"), dict):
        _notify(progress_cb, f"AR resume · checkpoint complete · {filename[:48]}")
        result = dict(cp["result"])
        result["resumed"] = True
        result["from_checkpoint"] = True
        return result

    deep_reads: dict[str, Any] = {}
    if cp and isinstance(cp.get("deep_reads"), dict):
        deep_reads = dict(cp["deep_reads"])
    resumed_hits = int((cp or {}).get("resumed_hits") or 0)
    _cp_lock = threading.Lock()

    def _persist(patch: dict[str, Any]) -> None:
        try:
            with _cp_lock:
                patch_ar_file_state(
                    session,
                    filename=filename,
                    fingerprint=fp,
                    patch=patch,
                    persist_cb=persist_cb,
                )
        except Exception as exc:
            logger.info("AR checkpoint persist failed: %s", exc)

    # ── Pass A: quick-read (batched relevance) ──────────────────────────
    if relevance is None and cp and isinstance(cp.get("relevance"), dict) and cp["relevance"].get("sections"):
        relevance = cp["relevance"]
        _notify(
            progress_cb,
            f"AR resume · quick-read cached ({len(relevance.get('sections') or [])} sections)",
        )
        resumed_hits += 1
    elif relevance is None and bool(getattr(settings, "ar_relevance_llm_enabled", True)):
        _notify(progress_cb, f"AR quick-read · {filename[:48]}")
        try:
            relevance = run_ar_relevance_pass(
                session,
                filename=filename,
                sections=sections,
                progress_cb=progress_cb,
            )
            _persist({"relevance": relevance, "deep_reads": deep_reads, "complete": False})
        except LLMAbortError:
            _persist({"relevance": relevance, "deep_reads": deep_reads, "complete": False})
            raise
        except Exception as exc:
            logger.info("AR relevance pass failed %s: %s", filename, exc)
            relevance = {"ok": False, "error": str(exc)[:300], "sections": []}

    gap_ids = {str(g.get("id")) for g in open_checklist_gaps(session) if g.get("id")}
    to_read = sections_to_deep_read(relevance or {}, open_gap_ids=gap_ids)
    if not to_read:
        buckets = collect_handler_texts(sections, max_chars=max_chars)
        for hid, bucket in buckets.items():
            to_read.append(
                {
                    "section": ", ".join(bucket["sections"]),
                    "decision": "keep",
                    "mode": hid,
                    "pages": bucket.get("pages") or [],
                    "_legacy_bucket": bucket,
                }
            )

    if not to_read:
        return {
            "ok": False,
            "error": "no_sections_to_deep_read",
            "file": filename,
            "relevance": relevance,
        }

    out: dict[str, Any] = {
        "file": filename,
        "handlers": {},
        "ok": True,
        "relevance": relevance,
    }
    ciq_capex = _ciq_capex_snapshot(session)

    # Restore cached deep-reads into handlers
    pending_rows = []
    for row in to_read:
        sname = str(row.get("section") or "")
        cached = deep_reads.get(sname)
        if isinstance(cached, dict) and (cached.get("bullets") or cached.get("error")):
            mode = str(cached.get("guidelines") or cached.get("mode") or row.get("mode") or "other_keep")
            slot = out["handlers"].setdefault(
                mode,
                {
                    "title": _HANDLER_TITLES.get(mode, mode),
                    "sections": [],
                    "pages": [],
                    "bullets": [],
                    "chars_sent": 0,
                    "guidelines": mode,
                },
            )
            _merge_handler_entry(slot, cached)
            resumed_hits += 1
        else:
            pending_rows.append(row)

    if resumed_hits:
        _notify(progress_cb, f"AR resume · skipped {resumed_hits} cached piece(s)")

    packs = _build_deep_read_packs(pending_rows, sections, max_chars=max_chars)
    _notify(
        progress_cb,
        f"AR deep-read · {len(pending_rows)} sections in {len(packs)} pack(s) · parallel≤{parallel}",
    )

    def _run_pack(pack: list[dict[str, Any]]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if len(pack) == 1:
            p = pack[0]
            sname = str(p["section"])
            mode = str(p.get("mode") or "other_keep")
            _notify(progress_cb, f"AR deep-read · {mode} · {sname[:36]} · {len(p.get('text') or ''):,} chars")
            try:
                if p.get("legacy"):
                    bullets = _llm_bullets(
                        handler_id=mode if mode in SECTION_GUIDELINES else "other_keep",
                        company=company,
                        filename=filename,
                        section_name=sname,
                        text=p.get("text") or "",
                        timeout=timeout,
                    )
                    entry = {
                        "title": _HANDLER_TITLES.get(mode, mode),
                        "sections": list((p["legacy"] or {}).get("sections") or [sname]),
                        "pages": list(p.get("pages") or []),
                        "bullets": bullets,
                        "chars_sent": len(p.get("text") or ""),
                        "guidelines": mode,
                    }
                else:
                    # Rebuild via helper using text already prepared
                    bullets = _llm_bullets(
                        handler_id=mode if mode in SECTION_GUIDELINES else "other_keep",
                        company=company,
                        filename=filename,
                        section_name=sname,
                        text=p.get("text") or "",
                        timeout=timeout,
                    )
                    entry = {
                        "title": _HANDLER_TITLES.get(mode, mode),
                        "sections": [sname],
                        "pages": list(p.get("pages") or []),
                        "bullets": bullets,
                        "chars_sent": len(p.get("text") or ""),
                        "guidelines": mode,
                        "source_section": sname,
                    }
                    if mode == "mda":
                        pass
                    if mode == "financial_capital" or re.search(
                        r"capex|capital\s+expenditure", p.get("text") or "", re.I
                    ):
                        entry["capex_reconcile"] = _reconcile_capex(
                            _extract_ar_capex_mentions(bullets, p.get("text") or ""),
                            ciq_capex,
                        )
            except LLMAbortError:
                raise
            except Exception as exc:
                entry = {
                    "title": _HANDLER_TITLES.get(mode, mode),
                    "sections": [sname],
                    "pages": list(p.get("pages") or []),
                    "bullets": [],
                    "error": str(exc)[:240],
                    "guidelines": mode,
                }
            entries.append(entry)
            return entries

        # Multi-section pack
        _notify(
            progress_cb,
            f"AR deep-read batch · {len(pack)} sections · "
            f"{sum(len(x.get('text') or '') for x in pack):,} chars",
        )
        try:
            by_sec = _llm_bullets_multi(
                company=company, filename=filename, items=pack, timeout=timeout
            )
        except LLMAbortError:
            raise
        except Exception as exc:
            logger.info("AR batch deep-read failed, falling back solo: %s", exc)
            for p in pack:
                entries.extend(_run_pack([p]))
            return entries

        for p in pack:
            sname = str(p["section"])
            mode = str(p.get("mode") or "other_keep")
            bullets = by_sec.get(sname) or []
            if not bullets:
                # Solo retry for empty parse
                try:
                    bullets = _llm_bullets(
                        handler_id=mode if mode in SECTION_GUIDELINES else "other_keep",
                        company=company,
                        filename=filename,
                        section_name=sname,
                        text=p.get("text") or "",
                        timeout=timeout,
                    )
                except LLMAbortError:
                    raise
                except Exception:
                    bullets = []
            entries.append(
                {
                    "title": _HANDLER_TITLES.get(mode, mode),
                    "sections": [sname],
                    "pages": list(p.get("pages") or []),
                    "bullets": bullets,
                    "chars_sent": len(p.get("text") or ""),
                    "guidelines": mode,
                    "source_section": sname,
                    "batched": True,
                }
            )
        return entries

    try:
        if not packs:
            pass
        elif len(packs) == 1 or parallel <= 1:
            for pack in packs:
                for entry in _run_pack(pack):
                    mode = str(entry.get("guidelines") or "other_keep")
                    sname = (entry.get("sections") or [""])[0]
                    deep_reads[str(sname)] = entry
                    slot = out["handlers"].setdefault(
                        mode,
                        {
                            "title": _HANDLER_TITLES.get(mode, mode),
                            "sections": [],
                            "pages": [],
                            "bullets": [],
                            "chars_sent": 0,
                            "guidelines": mode,
                        },
                    )
                    _merge_handler_entry(slot, entry)
                _persist(
                    {
                        "relevance": relevance,
                        "deep_reads": deep_reads,
                        "resumed_hits": resumed_hits,
                        "complete": False,
                    }
                )
        else:
            with ThreadPoolExecutor(max_workers=min(parallel, len(packs))) as pool:
                futs = {pool.submit(_run_pack, pack): i for i, pack in enumerate(packs)}
                for fut in as_completed(futs):
                    try:
                        entries = fut.result()
                    except LLMAbortError:
                        _persist(
                            {
                                "relevance": relevance,
                                "deep_reads": deep_reads,
                                "complete": False,
                            }
                        )
                        raise
                    for entry in entries:
                        mode = str(entry.get("guidelines") or "other_keep")
                        sname = (entry.get("sections") or [""])[0]
                        deep_reads[str(sname)] = entry
                        slot = out["handlers"].setdefault(
                            mode,
                            {
                                "title": _HANDLER_TITLES.get(mode, mode),
                                "sections": [],
                                "pages": [],
                                "bullets": [],
                                "chars_sent": 0,
                                "guidelines": mode,
                            },
                        )
                        _merge_handler_entry(slot, entry)
                    _persist(
                        {
                            "relevance": relevance,
                            "deep_reads": deep_reads,
                            "resumed_hits": resumed_hits,
                            "complete": False,
                        }
                    )
    except LLMAbortError:
        raise

    # ── Critic ──────────────────────────────────────────────────────────
    critic: dict[str, Any] | None = None
    if cp and isinstance(cp.get("critic"), dict) and cp["critic"].get("ok") and not pending_rows:
        critic = cp["critic"]
        _notify(progress_cb, "AR resume · critic cached")
    elif bool(getattr(settings, "ar_critic_llm_enabled", True)):
        try:
            from iis.finance.ar_critic import run_ar_critic

            critic = run_ar_critic(
                session,
                filename=filename,
                handlers=out["handlers"],
                relevance=relevance if isinstance(relevance, dict) else None,
                progress_cb=progress_cb,
            )
            if critic.get("ok"):
                _apply_critic_drops(out["handlers"], list(critic.get("drop_bullets") or []))
        except LLMAbortError:
            _persist(
                {
                    "relevance": relevance,
                    "deep_reads": deep_reads,
                    "critic": critic,
                    "complete": False,
                }
            )
            raise
        except Exception as exc:
            logger.info("AR critic failed %s: %s", filename, exc)
            critic = {"ok": False, "error": str(exc)[:300]}
    out["critic"] = critic
    _persist(
        {
            "relevance": relevance,
            "deep_reads": deep_reads,
            "critic": critic,
            "complete": False,
        }
    )

    # ── Gap-fill (one round) ────────────────────────────────────────────
    if (
        bool(getattr(settings, "ar_gap_fill_enabled", True))
        and isinstance(critic, dict)
        and critic.get("ok")
        and critic.get("gap_fill_sections")
        and not (cp or {}).get("gap_fill_done")
    ):
        already = {s for h in out["handlers"].values() for s in (h.get("sections") or [])}
        for gname in critic.get("gap_fill_sections") or []:
            gname = str(gname)
            if gname in already or gname in deep_reads:
                continue
            chunks = sections.get(gname)
            if not chunks:
                for k, v in sections.items():
                    if gname.lower() in k.lower() or k.lower() in gname.lower():
                        chunks = v
                        gname = k
                        break
            if not chunks:
                continue
            mode = _match_handler(gname) or "other_keep"
            _notify(progress_cb, f"AR gap-fill · {gname[:48]}")
            try:
                entry = _deep_read_one(
                    session,
                    company=company,
                    filename=filename,
                    section_name=gname,
                    mode=mode,
                    chunks=chunks,
                    max_chars=max_chars,
                    timeout=timeout,
                    ciq_capex=ciq_capex,
                    progress_cb=progress_cb,
                )
            except LLMAbortError:
                _persist(
                    {
                        "relevance": relevance,
                        "deep_reads": deep_reads,
                        "critic": critic,
                        "complete": False,
                    }
                )
                raise
            except Exception as exc:
                logger.info("AR gap-fill failed %s/%s: %s", filename, gname, exc)
                continue
            deep_reads[gname] = entry
            slot = out["handlers"].setdefault(
                mode,
                {
                    "title": _HANDLER_TITLES.get(mode, mode),
                    "sections": [],
                    "pages": [],
                    "bullets": [],
                    "chars_sent": 0,
                    "guidelines": mode,
                },
            )
            _merge_handler_entry(slot, entry)
            already.add(gname)

    out["evidence_register"] = build_evidence_register(
        filename=filename,
        handlers=out["handlers"],
        relevance=relevance if isinstance(relevance, dict) else None,
    )
    out["resumed_hits"] = resumed_hits
    _persist(
        {
            "relevance": relevance,
            "deep_reads": deep_reads,
            "critic": critic,
            "gap_fill_done": True,
            "complete": True,
            "result": out,
            "resumed_hits": resumed_hits,
        }
    )
    return out


def structured_sections_markdown(payload: dict[str, Any] | None) -> list[str]:
    """Render pipeline payload (session artifact or per-file block) to report lines."""
    if not payload:
        return []
    by_file = payload.get("by_file") if isinstance(payload.get("by_file"), dict) else None
    blocks: list[tuple[str, dict[str, Any]]] = []
    if by_file:
        for fname, res in by_file.items():
            if isinstance(res, dict):
                blocks.append((fname, res))
    elif payload.get("handlers"):
        blocks.append((str(payload.get("file") or "AR"), payload))

    lines: list[str] = []
    for fname, res in blocks:
        handlers = res.get("handlers") or {}
        if not handlers and not res.get("critic"):
            continue
        lines.append(f"## Annual report structured read — `{fname}`")
        lines.append("")
        # Relevance summary
        rel = res.get("relevance") if isinstance(res.get("relevance"), dict) else None
        if rel and rel.get("counts"):
            c = rel["counts"]
            lines.append(
                f"_Relevance: keep={c.get('keep', 0)} · skim={c.get('skim', 0)} · "
                f"skip={c.get('skip', 0)}_"
            )
            lines.append("")
        for hid in _HANDLER_ORDER:
            h = handlers.get(hid)
            if not isinstance(h, dict):
                continue
            title = h.get("title") or hid
            pages = h.get("pages") or []
            page_s = f" · p.{','.join(map(str, pages[:8]))}" if pages else ""
            lines.append(f"### {title}{page_s}")
            lines.append("")
            if h.get("error") and not h.get("bullets"):
                lines.append(f"_LLM summary failed: {h['error']}_")
                lines.append("")
                continue
            bullets = h.get("bullets") or []
            if not bullets:
                lines.append("_No bullets extracted._")
                lines.append("")
                continue
            for b in bullets:
                if str(b).startswith("#"):
                    lines.append(str(b))
                else:
                    lines.append(f"- {b}")
            lines.append("")
            recon = h.get("capex_reconcile")
            if isinstance(recon, dict):
                lines.append("#### Capex check (AR narrative vs Capital IQ)")
                lines.append("")
                for m in recon.get("ar_capex_mentions") or []:
                    lines.append(f"- AR: {m}")
                for m in recon.get("ciq_capex_lines") or []:
                    lines.append(f"- {m}")
                if recon.get("note"):
                    lines.append(f"- _{recon['note']}_")
                lines.append("")
        critic = res.get("critic")
        if isinstance(critic, dict) and critic.get("ok"):
            try:
                from iis.finance.ar_critic import critic_markdown

                lines.extend(critic_markdown(critic))
            except Exception:
                pass
    return lines
