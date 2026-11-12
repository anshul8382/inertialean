"""Pull richer annual-report + brokerage content into the research report.

Pure Python — no Ollama. Uses already-ingested pdf_chunks, annual_report_analysis,
library brokerage files, and the Telegram report index.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from iis.models.session import ResearchSession

_BROKER_NAME_RE = re.compile(
    r"(?i)\b("
    r"motilal|icici|kotak|nuvama|systematix|emkay|edelweiss|jm\s*financial|"
    r"axis|nomura|goldman|morgan\s*stanley|clsa|macquarie|jefferies|ambit|"
    r"dam\s*capital|antique|sharekhan|mirae|nirmal\s*bang|pl\s*capital|"
    r"phillip|ubs|hsbc|citi|brokerage|initiating\s+coverage"
    r")\b"
)

_AR_PRIORITY = (
    "SWOT",
    "Business Overview",
    "MD&A",
    "Directors Report",
    "Chairman's Message",
    "CEO Message",
    "Segments",
    "Competition",
    "Outlook",
    "Risk Factors",
    "Corporate Governance",
)

_THESIS_CUES = re.compile(
    r"(?i)\b("
    r"target\s*price|tp\s*[:=]|we\s+(?:recommend|maintain|upgrade|downgrade)|"
    r"rating|accumulate|outperform|underweight|overweight|"
    r"investment\s+thesis|key\s+takeaway|catalyst|risks?\b|"
    r"valuation|earnings\s+estimate|fy2[0-9]|revenue\s+cagr"
    r")\b"
)


def _clip(text: str, n: int = 420) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= n:
        return t
    return t[: n - 1].rstrip() + "…"


def _artifact(session: ResearchSession, atype: str) -> dict[str, Any]:
    art = next((a for a in session.agent_artifacts if a.artifact_type == atype), None)
    return art.payload if art and isinstance(art.payload, dict) else {}


def annual_report_narrative_markdown(
    session: ResearchSession,
    *,
    max_sections: int = 10,
    max_points_per_section: int = 5,
) -> list[str]:
    """Structured excerpts from annual_report_analysis (+ chunk fallback)."""
    lines: list[str] = []
    ar = _artifact(session, "annual_report_analysis")
    # Prefer typed full-section LLM bullets (leadership / outlook / gov / MD&A / financial)
    try:
        from iis.finance.ar_section_pipeline import structured_sections_markdown

        structured_lines = structured_sections_markdown(ar.get("structured_sections"))
        if structured_lines:
            lines.extend(structured_lines)
            lines.append("")
    except Exception:
        pass
    files = ar.get("files") or {}
    used = 0

    # Prefer priority section order across files
    ordered: list[tuple[str, str, dict]] = []
    for fname, fdata in files.items():
        sections = (fdata or {}).get("sections") or {}
        for pref in _AR_PRIORITY:
            for sname, analysis in sections.items():
                if pref.lower() in str(sname).lower() or str(sname).lower() in pref.lower():
                    ordered.append((fname, sname, analysis if isinstance(analysis, dict) else {}))
        for sname, analysis in sections.items():
            if any(sname == x[1] and fname == x[0] for x in ordered):
                continue
            ordered.append((fname, sname, analysis if isinstance(analysis, dict) else {}))

    seen_sec: set[str] = set()
    for fname, sname, analysis in ordered:
        key = f"{fname}::{sname}"
        if key in seen_sec:
            continue
        seen_sec.add(key)
        summary = (analysis.get("summary") or "").strip()
        points = [str(p).strip() for p in (analysis.get("key_points") or []) if str(p).strip()]
        facts = [str(p).strip() for p in (analysis.get("quantitative_facts") or []) if str(p).strip()]
        outlook = [str(p).strip() for p in (analysis.get("outlook_statements") or []) if str(p).strip()]
        risks = [str(p).strip() for p in (analysis.get("risks_mentioned") or []) if str(p).strip()]
        competitive = [str(p).strip() for p in (analysis.get("competitive_notes") or []) if str(p).strip()]
        if not any([summary, points, facts, outlook, risks, competitive]):
            continue
        lines.append(f"### {sname} — `{Path(fname).name}`")
        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")
        if points:
            lines.append("**Key points**")
            lines.append("")
            for p in points[:max_points_per_section]:
                lines.append(f"- {_clip(p, 320)}")
            lines.append("")
        if facts:
            lines.append("**Quantitative facts**")
            lines.append("")
            for p in facts[:max_points_per_section]:
                lines.append(f"- {_clip(p, 280)}")
            lines.append("")
        if competitive:
            lines.append("**Competitive notes**")
            lines.append("")
            for p in competitive[:4]:
                lines.append(f"- {_clip(p, 280)}")
            lines.append("")
        if outlook:
            lines.append("**Outlook (as stated)**")
            lines.append("")
            for p in outlook[:4]:
                lines.append(f"- {_clip(p, 280)}")
            lines.append("")
        if risks and sname.lower() not in ("risk factors",):
            lines.append("**Risks noted in this section**")
            lines.append("")
            for p in risks[:4]:
                lines.append(f"- {_clip(p, 280)}")
            lines.append("")
        used += 1
        if used >= max_sections:
            break

    # Explicit SWOT block from artifact
    swot = ar.get("swot") if isinstance(ar.get("swot"), dict) else None
    if swot and any(swot.get(k) for k in ("strengths", "weaknesses", "opportunities", "threats")):
        lines.append("### SWOT disclosed in annual report")
        lines.append("")
        src = swot.get("section") or swot.get("file") or "AR"
        lines.append(f"_Source: {src}_")
        lines.append("")
        for label, key in (
            ("Strengths", "strengths"),
            ("Weaknesses", "weaknesses"),
            ("Opportunities", "opportunities"),
            ("Threats", "threats"),
        ):
            items = swot.get(key) or []
            if items:
                lines.append(f"**{label}:** " + "; ".join(_clip(str(x), 160) for x in items[:6]))
                lines.append("")

    # Chunk fallback when analysis artifact is thin
    if used < 3:
        lines.extend(_ar_chunk_fallback(session, already=used, want=6 - used))

    return lines


def _ar_chunk_fallback(session: ResearchSession, *, already: int, want: int) -> list[str]:
    lines: list[str] = []
    if want <= 0:
        return lines
    prefer = ("business overview", "md&a", "directors", "chairman", "outlook", "risk", "segment", "swot")
    chunks = [
        c
        for c in (session.pdf_chunks or [])
        if getattr(c, "doc_type", None) in (None, "annual_report") or c.doc_type == "annual_report"
    ]
    scored: list[tuple[int, Any]] = []
    for c in chunks:
        hint = f"{c.section_title or ''} {c.section_hint or ''}".lower()
        text = c.text or ""
        if len(text) < 120:
            continue
        score = sum(1 for p in prefer if p in hint) * 3 + min(len(text) // 400, 3)
        if score:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    seen_files: set[str] = set()
    n = 0
    for _, c in scored:
        fn = Path(c.file or "ar.pdf").name
        key = f"{fn}:{c.section_title or c.section_hint or c.page}"
        if key in seen_files:
            continue
        seen_files.add(key)
        title = c.section_title or c.section_hint or f"p.{c.page}"
        lines.append(f"### {title} — `{fn}` (PDF excerpt)")
        lines.append("")
        lines.append(_clip(c.text or "", 560))
        lines.append("")
        n += 1
        if n >= want:
            break
    return lines


def _brokerage_filenames(session: ResearchSession) -> set[str]:
    names: set[str] = set()
    try:
        from iis.library.store import get_company_library_store

        if session.company_key:
            inv = get_company_library_store().load(session.company_key).inventory()
            for row in inv.get("brokerage_reports") or []:
                fn = row.get("filename") if isinstance(row, dict) else None
                if fn:
                    names.add(Path(fn).name.lower())
    except Exception:
        pass
    for f in session.uploaded_files or []:
        name = Path(f).name
        if _BROKER_NAME_RE.search(name) or "broker" in name.lower():
            names.add(name.lower())
    for d in session.pdf_documents or []:
        name = Path(d.file or "").name
        if not name:
            continue
        if _BROKER_NAME_RE.search(name) or getattr(d, "doc_type", "") == "brokerage_report":
            names.add(name.lower())
    return names


def brokerage_research_markdown(session: ResearchSession, *, max_reports: int = 8) -> list[str]:
    """Street research: Telegram index + ingested brokerage PDF excerpts."""
    lines: list[str] = []

    # 1) Index metadata (ratings / dates / types)
    try:
        from iis.integrations.telegram_report_index import find_reports_for_company, load_index

        name = session.company.name if session.company else None
        hits = find_reports_for_company(name, index=load_index()) if name else []
    except Exception:
        hits = []

    if hits:
        lines.append("### Broker notes on file (Telegram research index)")
        lines.append("")
        lines.append("| Broker | Date | Rating | Type | File |")
        lines.append("|---|---|---|---|---|")
        for h in hits[:18]:
            lines.append(
                f"| {h.get('broker') or '—'} | {h.get('report_date') or '—'} | "
                f"{h.get('rating') or h.get('rating_detail') or '—'} | "
                f"{h.get('report_type') or '—'} | `{Path(str(h.get('file_name') or '')).name[:48]}` |"
            )
        lines.append("")
        lines.append(
            "_Ratings are street views only — your recommendation stays blank until you decide._"
        )
        lines.append("")

    # 2) PDF chunk excerpts from brokerage files in the session/library
    broker_files = _brokerage_filenames(session)
    by_file: dict[str, list[Any]] = {}
    for c in session.pdf_chunks or []:
        fn = Path(c.file or "").name
        if not fn:
            continue
        low = fn.lower()
        is_broker = low in broker_files or bool(_BROKER_NAME_RE.search(fn))
        if not is_broker:
            continue
        by_file.setdefault(fn, []).append(c)

    if by_file:
        lines.append("### Excerpts from brokerage PDFs (uploaded / synced)")
        lines.append("")
        count = 0
        for fn, chunks in list(by_file.items())[:max_reports]:
            # Prefer thesis / rating / TP passages
            ranked = sorted(
                chunks,
                key=lambda c: (
                    3 if _THESIS_CUES.search((c.text or "")[:800]) else 0
                    + min(len(c.text or "") // 300, 2)
                ),
                reverse=True,
            )
            picks = ranked[:3]
            if not picks:
                continue
            lines.append(f"#### `{fn}`")
            lines.append("")
            for c in picks:
                page = f"p.{c.page}" if c.page else "excerpt"
                lines.append(f"- **{page}:** {_clip(c.text or '', 480)}")
            lines.append("")
            count += 1
            if count >= max_reports:
                break
    return lines


def ratio_interpretation_markdown(session: ResearchSession) -> list[str]:
    """Thorough ratio read: values + interpretation, strengths, concerns."""
    from iis.finance.ratios import (
        RATIO_CATALOG,
        compute_all_ratios,
        format_ratio_value,
    )

    result = compute_all_ratios(session.financial_line_items or [])
    latest = result.get("latest_ratios") or {}
    period = result.get("latest_period") or "latest"
    if not latest:
        return ["_No ratios available — upload Capital IQ Financials._", ""]

    lines: list[str] = [
        f"### Ratio analysis & interpretation _(period: {period})_",
        "",
        "| Ratio | Value | Read-through |",
        "|---|---|---|",
    ]
    strengths: list[str] = []
    concerns: list[str] = []

    def add_row(key: str, interp: str, *, strength: str | None = None, concern: str | None = None):
        val = latest.get(key)
        if val is None:
            return
        label = (RATIO_CATALOG.get(key) or {}).get("label") or key.replace("_", " ").title()
        lines.append(f"| {label} | {format_ratio_value(key, val)} | {interp} |")
        if strength:
            strengths.append(strength)
        if concern:
            concerns.append(concern)

    # Profitability
    gm = latest.get("gross_margin")
    if gm is not None:
        add_row(
            "gross_margin",
            "Pricing vs input costs"
            + (" — healthy buffer" if gm >= 0.35 else " — watch commodity/mix" if gm < 0.2 else ""),
            strength=f"Gross margin {format_ratio_value('gross_margin', gm)}" if gm >= 0.3 else None,
            concern=f"Thin gross margin {format_ratio_value('gross_margin', gm)}" if gm < 0.18 else None,
        )
    em = latest.get("ebitda_margin")
    if em is not None:
        add_row(
            "ebitda_margin",
            "Operating profitability"
            + (" — strong" if em >= 0.2 else " — modest" if em >= 0.1 else " — weak"),
            strength=f"EBITDA margin {format_ratio_value('ebitda_margin', em)}" if em >= 0.18 else None,
            concern=f"Low EBITDA margin {format_ratio_value('ebitda_margin', em)}" if em < 0.08 else None,
        )
    nm = latest.get("net_margin")
    if nm is not None:
        add_row(
            "net_margin",
            "Bottom-line conversion after interest & tax",
            concern=f"Net margin under pressure ({format_ratio_value('net_margin', nm)})" if nm < 0.04 else None,
        )

    # Returns
    roce = latest.get("roce")
    if roce is not None:
        add_row(
            "roce",
            "Capital efficiency"
            + (" — value-creating" if roce >= 0.15 else " — below typical cost of capital hurdle" if roce < 0.1 else ""),
            strength=f"ROCE {format_ratio_value('roce', roce)}" if roce >= 0.15 else None,
            concern=f"ROCE {format_ratio_value('roce', roce)} — returns look soft" if roce < 0.1 else None,
        )
    roe = latest.get("roe")
    if roe is not None:
        add_row(
            "roe",
            "Equity returns (leverage-sensitive)",
            strength=f"ROE {format_ratio_value('roe', roe)}" if roe >= 0.15 else None,
            concern=f"ROE {format_ratio_value('roe', roe)}" if roe is not None and roe < 0.08 else None,
        )
    roa = latest.get("roa")
    if roa is not None:
        add_row("roa", "Asset productivity")

    # Leverage / coverage
    nd = latest.get("net_debt_to_ebitda")
    if nd is not None:
        add_row(
            "net_debt_to_ebitda",
            "Balance-sheet leverage"
            + (" — conservative" if nd < 1 else " — elevated" if nd > 3 else " — moderate"),
            strength=f"Net debt/EBITDA {format_ratio_value('net_debt_to_ebitda', nd)}" if nd < 1 else None,
            concern=f"Leverage elevated at {format_ratio_value('net_debt_to_ebitda', nd)}" if nd > 3 else None,
        )
    de = latest.get("debt_to_equity")
    if de is not None:
        add_row(
            "debt_to_equity",
            "Funding mix",
            concern=f"Debt/Equity {format_ratio_value('debt_to_equity', de)}" if de > 1.5 else None,
        )
    ic = latest.get("interest_coverage")
    if ic is not None:
        add_row(
            "interest_coverage",
            "Ability to service interest"
            + (" — comfortable" if ic >= 4 else " — tight" if ic < 2 else ""),
            concern=f"Interest coverage only {format_ratio_value('interest_coverage', ic)}" if ic < 2.5 else None,
            strength=f"Interest coverage {format_ratio_value('interest_coverage', ic)}" if ic >= 5 else None,
        )

    # Cash / quality
    cfo_ni = latest.get("cfo_to_ni")
    if cfo_ni is not None:
        add_row(
            "cfo_to_ni",
            "Earnings quality (cash vs accrual profit)"
            + (" — cash-backed" if cfo_ni >= 0.9 else " — watch accruals" if cfo_ni < 0.7 else ""),
            strength=f"CFO/NI {format_ratio_value('cfo_to_ni', cfo_ni)}" if cfo_ni >= 1.0 else None,
            concern=f"CFO/NI {format_ratio_value('cfo_to_ni', cfo_ni)} — earnings quality flag" if cfo_ni < 0.7 else None,
        )
    fcf_m = latest.get("fcf_margin")
    if fcf_m is not None:
        add_row(
            "fcf_margin",
            "Free-cash conversion after capex",
            strength=f"FCF margin {format_ratio_value('fcf_margin', fcf_m)}" if fcf_m >= 0.08 else None,
            concern=f"Weak/negative FCF margin ({format_ratio_value('fcf_margin', fcf_m)})" if fcf_m < 0 else None,
        )
    capex = latest.get("capex_to_sales")
    if capex is not None:
        add_row("capex_to_sales", "Reinvestment intensity")

    # Working capital
    for key, label_extra in (
        ("receivable_days", "Collection cycle"),
        ("inventory_days", "Inventory cycle"),
        ("payable_days", "Payables cycle"),
        ("current_ratio", "Near-term liquidity"),
        ("asset_turnover", "Asset sweat"),
    ):
        if latest.get(key) is not None:
            concern = None
            strength = None
            val = latest[key]
            if key == "receivable_days" and val is not None and val > 90:
                concern = f"Receivable days elevated ({format_ratio_value(key, val)})"
            if key == "inventory_days" and val is not None and val > 120:
                concern = f"Inventory days elevated ({format_ratio_value(key, val)})"
            if key == "current_ratio" and val is not None and val < 1.0:
                concern = f"Current ratio below 1× ({format_ratio_value(key, val)})"
            if key == "current_ratio" and val is not None and val >= 1.5:
                strength = f"Current ratio {format_ratio_value(key, val)}"
            add_row(key, label_extra, strength=strength, concern=concern)

    lines.append("")
    if strengths:
        lines.append("**Strengths from ratios**")
        lines.append("")
        for s in strengths[:8]:
            lines.append(f"- {s}")
        lines.append("")
    if concerns:
        lines.append("**Concerns / watch-items from ratios**")
        lines.append("")
        for c in concerns[:8]:
            lines.append(f"- {c}")
        lines.append("")
    if not strengths and not concerns:
        lines.append("_Ratios available; no strong threshold signals — review trends vs peers._")
        lines.append("")
    return lines
