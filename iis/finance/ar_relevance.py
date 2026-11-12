"""AR quick-read (relevance): batched keep/skim/skip, then deep-read uses keeps."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from iis.config import settings
from iis.finance.inertia_methodology import (
    DEEP_READ_MODES,
    gaps_prompt_block,
    methodology_prompt_block,
)
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

_MODE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("leadership_letters", ("chairman", "chairperson", "ceo", "managing director", "letter", "message from")),
    ("future_outlook", ("outlook", "future prospect", "way forward", "looking ahead")),
    ("governance", ("governance", "related party", "board's report", "boards report")),
    ("financial_capital", ("financial capital", "capital allocation", "cash flow")),
    ("mda", ("md&a", "management discussion")),
]


def _notify(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _guess_mode(section_name: str) -> str:
    low = (section_name or "").lower()
    for mode, pats in _MODE_HINTS:
        if any(p in low for p in pats):
            return mode
    return "other_keep"


def _section_text(chunks: list[Any], *, max_chars: int) -> tuple[str, list[int]]:
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
            if room < 300:
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


def _parse_json(raw: str) -> dict[str, Any] | list[Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    try:
        data = json.loads(text)
        if isinstance(data, (dict, list)):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, (dict, list)):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def _normalise_decision(row: dict[str, Any], section_name: str) -> dict[str, Any]:
    decision = str(row.get("decision") or "skim").strip().lower()
    if decision not in ("keep", "skim", "skip"):
        decision = "skim"
    mode = str(row.get("mode") or _guess_mode(section_name)).strip().lower()
    if mode not in DEEP_READ_MODES:
        mode = _guess_mode(section_name)
    checklist_ids = row.get("checklist_ids") or row.get("checklist") or []
    if not isinstance(checklist_ids, list):
        checklist_ids = []
    try:
        conf = float(row.get("confidence") if row.get("confidence") is not None else 0.5)
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "section": section_name,
        "decision": decision,
        "why": str(row.get("why") or row.get("reason") or "")[:500],
        "mode": mode,
        "checklist_ids": [str(x) for x in checklist_ids[:12]],
        "confidence": max(0.0, min(1.0, conf)),
    }


def _pack_batches(
    prepared: list[dict[str, Any]],
    *,
    max_sections: int,
    max_chars: int,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    used = 0
    for item in prepared:
        cost = len(item.get("excerpt") or "") + 80
        if cur and (len(cur) >= max_sections or used + cost > max_chars):
            batches.append(cur)
            cur = []
            used = 0
        cur.append(item)
        used += cost
    if cur:
        batches.append(cur)
    return batches


def score_batch_relevance(
    session: ResearchSession,
    *,
    company: str,
    filename: str,
    batch: list[dict[str, Any]],
    timeout: float,
) -> list[dict[str, Any]]:
    """One Hive call: score many section excerpts → keep|skim|skip each."""
    from iis.context.manager import llm_agent
    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    llm = get_llm_client(finance=True)
    names = [str(b["section"]) for b in batch]
    system = (
        methodology_prompt_block()
        + "\n\nQUICK-READ (relevance only — do not deep-summarise).\n"
        "For EACH section below decide keep|skim|skip for initiating coverage.\n"
        "KEEP = thesis-critical (business, outlook, MD&A sector, capital, governance substance).\n"
        "SKIP = TOC fluff, pure boilerplate, ads, index-only.\n"
        "SKIM = mixed / low signal.\n"
        "Respond JSON only:\n"
        '{"sections":[{"section":"<exact name>","decision":"keep|skim|skip","why":"...",'
        '"mode":"leadership_letters|future_outlook|governance|financial_capital|mda|other_keep",'
        '"checklist_ids":[],"confidence":0.0}]}'
    )
    blocks = []
    for b in batch:
        blocks.append(
            f"### SECTION: {b['section']}\n"
            f"Mode hint: {b.get('mode_hint') or _guess_mode(b['section'])}\n"
            f"{b.get('excerpt') or ''}\n"
        )
    user = (
        f"Company: {company}\nFile: {filename}\n"
        f"Score these {len(batch)} sections (use exact SECTION names):\n\n"
        f"{gaps_prompt_block(session)}\n\n"
        + "\n".join(blocks)
    )
    max_tok = int(
        getattr(settings, "ar_relevance_batch_max_tokens", 3500)
        or max(600, 280 * len(batch))
    )
    with llm_agent("ar_relevance"):
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                timeout=timeout,
                max_tokens=max_tok,
                allow_reasoning_fallback=False,
                format_json=True,
                reasoning_effort=str(
                    getattr(settings, "hive_json_reasoning_effort", "low") or "low"
                ),
            )
        except TypeError:
            raw = llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                timeout=timeout,
                format_json=True,
            )
        except LLMAbortError:
            raise

    data = _parse_json(raw or "")
    rows_in: list[Any] = []
    if isinstance(data, dict):
        rows_in = list(data.get("sections") or data.get("results") or [])
        if not rows_in and data.get("section"):
            rows_in = [data]
    elif isinstance(data, list):
        rows_in = data

    by_name: dict[str, dict[str, Any]] = {}
    for row in rows_in:
        if not isinstance(row, dict):
            continue
        sname = str(row.get("section") or row.get("name") or "").strip()
        if not sname:
            continue
        by_name[sname.lower()] = row
        # fuzzy: also index without whitespace noise
        by_name[re.sub(r"\s+", " ", sname.lower())] = row

    out: list[dict[str, Any]] = []
    for b in batch:
        sname = str(b["section"])
        raw_row = by_name.get(sname.lower()) or by_name.get(re.sub(r"\s+", " ", sname.lower()))
        if not raw_row:
            # Match partial
            for k, v in by_name.items():
                if sname.lower() in k or k in sname.lower():
                    raw_row = v
                    break
        if raw_row:
            parsed = _normalise_decision(raw_row, sname)
        else:
            parsed = {
                "section": sname,
                "decision": "skim",
                "why": "missing_from_batch_response",
                "mode": b.get("mode_hint") or _guess_mode(sname),
                "checklist_ids": [],
                "confidence": 0.25,
            }
        parsed["pages"] = b.get("pages") or []
        parsed["chars_scored"] = len(b.get("excerpt") or "")
        parsed["batch"] = True
        out.append(parsed)
    # Ensure we return one row per input name order
    _ = names
    return out


def score_section_relevance(
    session: ResearchSession,
    *,
    company: str,
    filename: str,
    section_name: str,
    text: str,
    timeout: float,
) -> dict[str, Any]:
    """Single-section fallback (used if batching disabled)."""
    rows = score_batch_relevance(
        session,
        company=company,
        filename=filename,
        batch=[
            {
                "section": section_name,
                "excerpt": text,
                "pages": [],
                "mode_hint": _guess_mode(section_name),
            }
        ],
        timeout=timeout,
    )
    return rows[0] if rows else _normalise_decision({}, section_name)


def run_ar_relevance_pass(
    session: ResearchSession,
    *,
    filename: str,
    sections: dict[str, list[Any]],
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """Quick-read every AR section (batched) for initiating-coverage relevance."""
    if not bool(getattr(settings, "ar_relevance_llm_enabled", True)):
        return {"ok": False, "skipped": True, "reason": "ar_relevance_llm_enabled=false"}

    from iis.llm.factory import get_llm_client
    from iis.llm.gate import LLMAbortError

    if not get_llm_client(finance=True).is_available():
        return {"ok": False, "error": "llm_unavailable", "file": filename}

    excerpt_n = max(400, int(getattr(settings, "ar_relevance_excerpt_chars", 2200) or 2200))
    # Legacy single-section path used full max_chars; batch uses short excerpts
    solo_max = max(8000, int(getattr(settings, "ar_relevance_max_chars", 40000) or 40000))
    timeout = float(getattr(settings, "ar_relevance_timeout_sec", 120.0) or 120.0)
    company = session.company.name if session.company else (session.company_key or "Company")
    batch_on = bool(getattr(settings, "ar_relevance_batch_enabled", True))
    max_sec = max(1, int(getattr(settings, "ar_relevance_batch_sections", 12) or 12))
    max_chars = max(20000, int(getattr(settings, "ar_relevance_batch_chars", 100000) or 100000))
    parallel = max(1, int(getattr(settings, "llm_max_parallel", 3) or 3))

    prepared: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for sname, chunks in (sections or {}).items():
        text, pages = _section_text(
            chunks, max_chars=excerpt_n if batch_on else solo_max
        )
        if len(text) < 80:
            results.append(
                {
                    "section": sname,
                    "decision": "skip",
                    "why": "too_short",
                    "mode": _guess_mode(sname),
                    "checklist_ids": [],
                    "confidence": 1.0,
                    "pages": pages,
                    "chars_scored": len(text),
                }
            )
            continue
        prepared.append(
            {
                "section": sname,
                "excerpt": text,
                "pages": pages,
                "mode_hint": _guess_mode(sname),
                "approx": len(text),
            }
        )
    prepared.sort(key=lambda x: -int(x.get("approx") or 0))

    if not prepared:
        return {"ok": True, "file": filename, "sections": results, "counts": {"keep": 0, "skim": 0, "skip": len(results)}}

    if not batch_on:
        batches = [[p] for p in prepared]
    else:
        batches = _pack_batches(prepared, max_sections=max_sec, max_chars=max_chars)

    _notify(
        progress_cb,
        f"AR quick-read · {len(prepared)} sections in {len(batches)} batch(es) "
        f"(parallel≤{parallel}) · {filename[:40]}",
    )

    def _one(bi: int, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _notify(
            progress_cb,
            f"AR quick-read batch {bi + 1}/{len(batches)} · {len(batch)} sections",
        )
        return score_batch_relevance(
            session,
            company=company,
            filename=filename,
            batch=batch,
            timeout=timeout,
        )

    try:
        if len(batches) == 1 or parallel <= 1:
            for bi, batch in enumerate(batches):
                results.extend(_one(bi, batch))
        else:
            with ThreadPoolExecutor(max_workers=min(parallel, len(batches))) as pool:
                futs = {pool.submit(_one, bi, batch): bi for bi, batch in enumerate(batches)}
                # Preserve batch order when collecting
                ordered: dict[int, list[dict[str, Any]]] = {}
                for fut in as_completed(futs):
                    bi = futs[fut]
                    try:
                        ordered[bi] = fut.result()
                    except LLMAbortError:
                        raise
                    except Exception as exc:
                        logger.info("AR quick-read batch %s failed: %s", bi, exc)
                        # Fallback skim for that batch
                        ordered[bi] = [
                            {
                                "section": b["section"],
                                "decision": "skim",
                                "why": f"batch_error: {exc}"[:240],
                                "mode": b.get("mode_hint") or _guess_mode(b["section"]),
                                "checklist_ids": [],
                                "confidence": 0.2,
                                "pages": b.get("pages") or [],
                                "chars_scored": len(b.get("excerpt") or ""),
                                "error": str(exc)[:200],
                            }
                            for b in batches[bi]
                        ]
                for bi in range(len(batches)):
                    results.extend(ordered.get(bi) or [])
    except LLMAbortError:
        raise

    counts = {"keep": 0, "skim": 0, "skip": 0}
    for r in results:
        d = r.get("decision") or "skim"
        counts[d] = counts.get(d, 0) + 1

    return {
        "ok": True,
        "file": filename,
        "sections": results,
        "counts": counts,
        "batched": batch_on,
        "batches": len(batches),
    }


def sections_to_deep_read(
    relevance: dict[str, Any],
    *,
    open_gap_ids: set[str] | None = None,
    include_all_skims: bool | None = None,
    max_sections: int | None = None,
) -> list[dict[str, Any]]:
    """Select keep (+ optional skim) for deep-read after quick-read.

    Caps to top-N by confidence (priority modes boosted) so Analyze stays bounded.
    """
    if include_all_skims is None:
        include_all_skims = bool(getattr(settings, "ar_deep_read_include_skims", False))
    if max_sections is None:
        max_sections = int(getattr(settings, "ar_max_deep_read_sections", 12) or 12)
    open_gap_ids = open_gap_ids or set()
    priority = {
        "mda": 3.0,
        "future_outlook": 2.5,
        "leadership_letters": 2.2,
        "financial_capital": 2.0,
        "governance": 1.3,
        "other_keep": 1.0,
    }
    out: list[dict[str, Any]] = []
    for row in relevance.get("sections") or []:
        if not isinstance(row, dict):
            continue
        decision = row.get("decision")
        ids = set(row.get("checklist_ids") or [])
        if decision == "keep":
            out.append(row)
        elif decision == "skim" and (include_all_skims or (ids & open_gap_ids)):
            out.append(row)

    def _score(r: dict[str, Any]) -> float:
        try:
            conf = float(r.get("confidence") or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        mode = str(r.get("mode") or "other_keep")
        return conf * priority.get(mode, 1.0) + (0.5 if r.get("decision") == "keep" else 0.0)

    out.sort(key=_score, reverse=True)
    if max_sections > 0 and len(out) > max_sections:
        out = out[:max_sections]
    return out
