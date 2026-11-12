"""SWOT from annual reports — prefer company-disclosed SWOT over inferred lists.

Indian ARs often include an explicit Strengths / Weaknesses / Opportunities / Threats
block (MD&A, Directors’ Report, or a dedicated SWOT section). We extract that first;
ratio/flag heuristics are only a fallback when the AR has no SWOT text.
"""
from __future__ import annotations

import re
from typing import Any

from iis.models.session import ResearchSession

_SWOT_HEADINGS = (
    ("strengths", "strengths"),
    ("weaknesses", "weaknesses"),
    ("opportunities", "opportunities"),
    ("threats", "threats"),
)

# Split on SWOT headings (with optional numbering / bullets)
_HEADING_RE = re.compile(
    r"(?im)^\s*(?:\(?[1-4a-dA-D]\)?[.\)]\s*)?"
    r"(?:key\s+)?"
    r"(strengths?|weaknesses?|opportunit(?:y|ies)|threats?)\s*[:\-–]?\s*$"
)

_BULLET_RE = re.compile(
    r"(?m)^\s*(?:[\u2022\u25cf\u25aa\-\*\u2013\u2014]|\d+[.)])\s+(.+?)\s*$"
)


def _norm_bucket(label: str) -> str | None:
    low = label.lower().rstrip("s")
    if low.startswith("strength"):
        return "strengths"
    if low.startswith("weakness"):
        return "weaknesses"
    if low.startswith("opportunit"):
        return "opportunities"
    if low.startswith("threat"):
        return "threats"
    return None


def _clean_item(s: str) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    t = re.sub(r"^[\u2022\u25cf\-\*\d.)\s]+", "", t).strip()
    if len(t) < 8 or len(t) > 400:
        return ""
    # Drop TOC-ish lines
    if re.fullmatch(r"[\d.\s]+", t):
        return ""
    return t


def extract_swot_from_text(text: str) -> dict[str, list[str]]:
    """Parse an AR SWOT block into four lists. Empty dict buckets if not found."""
    out: dict[str, list[str]] = {k: [] for k, _ in _SWOT_HEADINGS}
    if not text or len(text) < 40:
        return out

    # Normalize common inline labels: "Strengths: foo. Weaknesses: bar"
    working = text
    for raw, key in (
        (r"(?i)\bstrengths?\s*[:\-–]", "\nStrengths:\n"),
        (r"(?i)\bweaknesses?\s*[:\-–]", "\nWeaknesses:\n"),
        (r"(?i)\bopportunit(?:y|ies)\s*[:\-–]", "\nOpportunities:\n"),
        (r"(?i)\bthreats?\s*[:\-–]", "\nThreats:\n"),
    ):
        working = re.sub(raw, key, working)

    lines = working.splitlines()
    current: str | None = None
    buffers: dict[str, list[str]] = {k: [] for k in out}

    for line in lines:
        hm = _HEADING_RE.match(line.strip())
        if hm:
            current = _norm_bucket(hm.group(1))
            continue
        # Inline "Strengths: item"
        inline = re.match(
            r"(?i)^\s*(strengths?|weaknesses?|opportunit(?:y|ies)|threats?)\s*[:\-–]\s*(.+)\s*$",
            line,
        )
        if inline:
            current = _norm_bucket(inline.group(1))
            item = _clean_item(inline.group(2))
            if current and item:
                buffers[current].append(item)
            continue
        if not current:
            continue
        bm = _BULLET_RE.match(line)
        if bm:
            item = _clean_item(bm.group(1))
            if item:
                buffers[current].append(item)
            continue
        # Continuation / plain paragraph under a heading
        item = _clean_item(line)
        if item and not item.lower().startswith(("page ", "source")):
            # Prefer bullets; allow short paragraphs under heading
            if len(item) >= 20 or buffers[current]:
                buffers[current].append(item)

    for k, items in buffers.items():
        # Dedupe preserve order
        seen: set[str] = set()
        for it in items:
            key = it.lower()
            if key in seen:
                continue
            seen.add(key)
            out[k].append(it)
        out[k] = out[k][:8]
    return out


def swot_from_annual_report_artifact(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Prefer explicit SWOT stored on annual_report_analysis."""
    if not payload:
        return None
    top = payload.get("swot")
    if isinstance(top, dict) and any(top.get(k) for k in ("strengths", "weaknesses", "opportunities", "threats")):
        return {
            "strengths": list(top.get("strengths") or [])[:8],
            "weaknesses": list(top.get("weaknesses") or [])[:8],
            "opportunities": list(top.get("opportunities") or [])[:8],
            "threats": list(top.get("threats") or [])[:8],
            "source": top.get("source") or "annual_report",
            "section": top.get("section"),
            "file": top.get("file"),
        }

    # Per-file section analyses may already have swot_* keys or section named SWOT
    for filename, file_data in (payload.get("files") or {}).items():
        sections = (file_data or {}).get("sections") or {}
        for section_name, analysis in sections.items():
            if not isinstance(analysis, dict):
                continue
            if analysis.get("strengths") or analysis.get("swot"):
                sw = analysis.get("swot") if isinstance(analysis.get("swot"), dict) else analysis
                bucket = {
                    "strengths": list(sw.get("strengths") or [])[:8],
                    "weaknesses": list(sw.get("weaknesses") or [])[:8],
                    "opportunities": list(sw.get("opportunities") or [])[:8],
                    "threats": list(sw.get("threats") or [])[:8],
                    "source": "annual_report",
                    "section": section_name,
                    "file": filename,
                }
                if any(bucket[k] for k in ("strengths", "weaknesses", "opportunities", "threats")):
                    return bucket
            # Parse summary / key_points if section looks like SWOT
            if "swot" in section_name.lower() or "strength" in section_name.lower():
                blob = "\n".join(
                    str(x)
                    for x in (
                        [analysis.get("summary") or ""]
                        + list(analysis.get("key_points") or [])
                        + list(analysis.get("quantitative_facts") or [])
                    )
                    if x
                )
                parsed = extract_swot_from_text(blob)
                if any(parsed.values()):
                    return {**parsed, "source": "annual_report", "section": section_name, "file": filename}
    return None


def swot_from_pdf_chunks(session: ResearchSession) -> dict[str, Any] | None:
    """Scan annual-report chunks for SWOT headings when artifact has none."""
    chunks = [
        c
        for c in (session.pdf_chunks or [])
        if not getattr(c, "doc_type", None) or c.doc_type == "annual_report"
    ]
    swot_chunks = []
    other_ar = []
    for c in chunks:
        title = f"{c.section_title or ''} {c.section_hint or ''}".lower()
        text = c.text or ""
        if "swot" in title or re.search(r"\bstrengths?\b.{0,80}\bweaknesses?\b", text[:600], re.I | re.S):
            swot_chunks.append(c)
        elif any(h in title for h in ("md&a", "directors", "business overview", "risk")):
            other_ar.append(c)

    ordered = swot_chunks or other_ar[:12]
    parsed: dict[str, list[str]] = {k: [] for k, _ in _SWOT_HEADINGS}
    if ordered:
        blob = "\n".join(c.text or "" for c in ordered[:20])
        parsed = extract_swot_from_text(blob)
    if not any(parsed.values()):
        blob2 = "\n".join(
            c.text or ""
            for c in chunks
            if re.search(r"\b(strengths?|weaknesses?|opportunities|threats?)\b", c.text or "", re.I)
        )
        parsed = extract_swot_from_text(blob2[:20000])
        ordered = chunks[:1]
    if not any(parsed.values()):
        return None
    ref = ordered[0] if ordered else None
    return {
        **parsed,
        "source": "annual_report",
        "section": (getattr(ref, "section_title", None) or getattr(ref, "section_hint", None)) if ref else None,
        "file": getattr(ref, "file", None) if ref else None,
    }


def resolve_ar_swot(session: ResearchSession) -> dict[str, Any] | None:
    """Best available SWOT disclosed in the annual report."""
    art = next(
        (a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"),
        None,
    )
    from_art = swot_from_annual_report_artifact(art.payload if art else None)
    if from_art:
        return from_art
    return swot_from_pdf_chunks(session)


_GROWTH_CUES = re.compile(
    r"(?i)\b("
    r"growth|expand|expansion|opportunity|opportunities|new market|capacity|"
    r"capex|pipeline|launch|ramp[- ]?up|diversif|inorganic|acquisition|"
    r"market share|export|premiumi[sz]ation|volume growth"
    r")\b"
)
_RISK_CUES = re.compile(
    r"(?i)\b("
    r"risk|uncertain|volatil|pressure|headwind|challeng|concern|"
    r"litigation|regulatory|commodity|input cost|competition intens|"
    r"geopolit|currency|fx |delever|impairment|write[- ]?off"
    r")\b"
)
_STRENGTH_CUES = re.compile(
    r"(?i)\b("
    r"leader|leading|market share|brand|moat|cost advantage|"
    r"margin expansion|record|strong balance|net cash|debt[- ]free|"
    r"cash generation|roce|return on capital"
    r")\b"
)


def _clip(s: str, n: int = 220) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _from_ar_sections(session: ResearchSession) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {
        "strengths": [],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
    }
    art = next(
        (a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"),
        None,
    )
    if not art:
        return out
    for file_data in (art.payload.get("files") or {}).values():
        for section_name, analysis in ((file_data or {}).get("sections") or {}).items():
            if not isinstance(analysis, dict):
                continue
            low = section_name.lower()
            for r in analysis.get("risks_mentioned") or []:
                item = _clip(str(r))
                if item:
                    out["threats"].append(f"{item} _(AR · {section_name})_")
            for o in analysis.get("outlook_statements") or []:
                text = str(o)
                item = _clip(text)
                if not item:
                    continue
                if _GROWTH_CUES.search(text):
                    out["opportunities"].append(f"{item} _(AR outlook · {section_name})_")
                elif _STRENGTH_CUES.search(text):
                    out["strengths"].append(f"{item} _(AR · {section_name})_")
            for kp in analysis.get("key_points") or []:
                text = str(kp)
                item = _clip(text)
                if not item:
                    continue
                if "risk" in low or _RISK_CUES.search(text):
                    out["threats"].append(f"{item} _(AR · {section_name})_")
                elif _GROWTH_CUES.search(text):
                    out["opportunities"].append(f"{item} _(AR · {section_name})_")
                elif _STRENGTH_CUES.search(text):
                    out["strengths"].append(f"{item} _(AR · {section_name})_")
    return out


def _from_guidance_and_concall(session: ResearchSession) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {
        "strengths": [],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
    }
    g = next(
        (a.payload for a in session.agent_artifacts if a.artifact_type == "guidance_summary"),
        None,
    )
    if isinstance(g, dict):
        for t in g.get("themes") or []:
            text = str(t)
            item = _clip(text)
            if not item:
                continue
            if _RISK_CUES.search(text):
                out["threats"].append(f"{item} _(management themes)_")
            elif _GROWTH_CUES.search(text):
                out["opportunities"].append(f"{item} _(management themes)_")
            else:
                out["opportunities"].append(f"{item} _(management themes)_")
        commentary = g.get("commentary")
        if commentary:
            for sent in re.split(r"(?<=[.!?])\s+", str(commentary)):
                item = _clip(sent, 200)
                if len(item) < 30:
                    continue
                if _RISK_CUES.search(sent):
                    out["threats"].append(f"{item} _(guidance commentary)_")
                elif _GROWTH_CUES.search(sent) or _STRENGTH_CUES.search(sent):
                    out["opportunities"].append(f"{item} _(guidance commentary)_")

    # Concall Q&A — growth / risk language from transcripts
    try:
        from iis.ingest.pdf import search_transcript_qa

        for topic, bucket in (
            ("growth opportunity expansion capacity", "opportunities"),
            ("risk concern headwind challenge margin", "threats"),
            ("competitive advantage brand leadership", "strengths"),
        ):
            for qa in search_transcript_qa(session.pdf_documents or [], topic, limit=4):
                ans = _clip(getattr(qa, "answer", "") or "", 200)
                q = _clip(getattr(qa, "question", "") or "", 120)
                if not ans and not q:
                    continue
                line = ans or q
                out[bucket].append(f"{line} _(concall)_")
    except Exception:
        pass

    # Raw PDF chunks tagged outlook / risk
    for c in session.pdf_chunks or []:
        hint = f"{getattr(c, 'section_hint', '') or ''} {getattr(c, 'section_title', '') or ''}".lower()
        text = c.text or ""
        if "risk" in hint and _RISK_CUES.search(text[:400]):
            out["threats"].append(_clip(text, 200) + " _(AR risk section)_")
        if any(x in hint for x in ("outlook", "chairman", "ceo", "md&a")) and _GROWTH_CUES.search(text[:500]):
            out["opportunities"].append(_clip(text, 200) + " _(management / AR)_")
    return out


def _dedupe_bucket(items: list[str], limit: int = 6) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = re.sub(r"\s+", " ", it.lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def synthesize_qualitative_swot(session: ResearchSession) -> dict[str, Any]:
    """When no formal SWOT in AR: build from key risks, management, concall growth."""
    merged = {
        "strengths": [],
        "weaknesses": [],
        "opportunities": [],
        "threats": [],
    }
    for part in (_from_ar_sections(session), _from_guidance_and_concall(session)):
        for k in merged:
            merged[k].extend(part.get(k) or [])

    # Light financial cues as strengths / concerns (labeled)
    try:
        from iis.finance.metrics import get_key_metrics_summary
        from iis.finance.ratios import format_ratio_value, get_latest_ratio_summary
        from iis.finance.growth import compute_revenue_cagr

        ratios = get_latest_ratio_summary(session.financial_line_items or [])
        cagr = compute_revenue_cagr(session.financial_line_items or [], years=3)
        if cagr is not None and cagr >= 0.1:
            merged["strengths"].append(f"Revenue 3Y CAGR {cagr:.1%} _(from Financials)_")
        elif cagr is not None and cagr < 0:
            merged["weaknesses"].append(f"Revenue 3Y CAGR {cagr:.1%} _(from Financials)_")
        if ratios.get("roce") is not None and (ratios["roce"] or 0) >= 0.15:
            merged["strengths"].append(
                f"ROCE {format_ratio_value('roce', ratios['roce'])} _(from Financials)_"
            )
        if ratios.get("net_debt_to_ebitda") is not None and (ratios["net_debt_to_ebitda"] or 0) > 3:
            merged["weaknesses"].append(
                f"Net debt/EBITDA {format_ratio_value('net_debt_to_ebitda', ratios['net_debt_to_ebitda'])} _(leverage)_"
            )
        if ratios.get("cfo_to_ni") is not None and (ratios["cfo_to_ni"] or 0) < 0.7:
            merged["weaknesses"].append(
                f"CFO/NI {format_ratio_value('cfo_to_ni', ratios['cfo_to_ni'])} — earnings quality watch _(from Financials)_"
            )
        _ = get_key_metrics_summary  # keep import used if metrics needed later
    except Exception:
        pass

    try:
        from iis.finance.flags import compute_data_flags

        for f in compute_data_flags(session)[:5]:
            h = getattr(f, "headline", None) or ""
            if not h:
                continue
            sev = getattr(f, "severity", "") or ""
            if sev in ("high", "review", "medium"):
                merged["weaknesses"].append(f"{h} _(data flag)_")
    except Exception:
        pass

    result = {k: _dedupe_bucket(v) for k, v in merged.items()}
    result["source"] = "qualitative_ar_concall"
    result["note"] = (
        "No formal SWOT table found in the annual report — "
        "assembled from key risks, management outlook, concall themes, and Financials."
    )
    return result


def resolve_swot(session: ResearchSession) -> dict[str, Any]:
    """Prefer company SWOT in AR; else qualitative risks / management / concall."""
    ar = resolve_ar_swot(session)
    if ar and any(ar.get(k) for k in ("strengths", "weaknesses", "opportunities", "threats")):
        ar = dict(ar)
        ar.setdefault("note", "Extracted from annual report SWOT / related disclosure.")
        return ar
    return synthesize_qualitative_swot(session)
