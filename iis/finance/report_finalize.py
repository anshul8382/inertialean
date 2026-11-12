"""Assemble final report: Hive narrative + always-on tables + Hive-selected optionals."""
from __future__ import annotations

from typing import Any

from iis.finance.report_data_catalog import (
    ALWAYS_ON_IDS,
    DEFAULT_OMIT,
    build_report_data_catalog,
    parse_report_include_block,
    strip_report_include_block,
)
from iis.models.session import ResearchSession


def _fill_slot_markers(md: str, blocks: dict[str, str]) -> str:
    """Replace {{TABLE:id}} markers with rendered markdown."""
    out = md
    for bid, body in blocks.items():
        for token in (f"{{{{TABLE:{bid}}}}}", f"{{{{table:{bid}}}}}"):
            if token in out and body:
                out = out.replace(token, "\n\n" + body.strip() + "\n\n")
    # Drop unused markers
    import re

    out = re.sub(r"\{\{TABLE:[a-z0-9_]+\}\}", "", out, flags=re.I)
    return out


def render_always_on_blocks(session: ResearchSession) -> dict[str, str]:
    blocks: dict[str, str] = {}
    # Inertia scorecard first — critical-15, YoY basics, ratio trends, flags
    try:
        from iis.finance.inertia_scorecard import scorecard_markdown

        sc = scorecard_markdown(session)
        if sc:
            blocks["inertia_scorecard"] = "\n".join(sc)
    except Exception:
        pass
    try:
        from iis.finance.ciq_tables import (
            estimates_tables_markdown,
            financials_as_uploaded_markdown,
        )

        fin = financials_as_uploaded_markdown(session, compact=False, max_periods=8)
        if fin:
            blocks["ciq_financials"] = "\n".join(fin)
        est = estimates_tables_markdown(session)
        if est:
            blocks["ciq_estimates"] = "## Capital IQ estimates\n\n" + "\n".join(est)
    except Exception:
        pass
    try:
        from iis.finance.metric_series import build_capex_analysis, format_capex_analysis

        capex = build_capex_analysis(session.financial_line_items or [], recent_years=8)
        if capex:
            blocks["capex"] = (
                "## Capex (Capital IQ)\n\n" + format_capex_analysis(capex)
            )
    except Exception:
        pass
    try:
        from iis.finance.peer_timeline import peer_timeline_markdown

        peer = peer_timeline_markdown(session)
        if peer:
            blocks["peer_timeline"] = "\n".join(peer)
    except Exception:
        pass
    # Investor checklist concerns / full ratio pack (always when scorecard or critique exists)
    try:
        from iis.finance.investor_concerns import (
            apply_investor_concerns,
            investor_checklist_appendix_markdown,
        )

        apply_investor_concerns(session)
        chk = investor_checklist_appendix_markdown(session)
        if chk:
            blocks["investor_checklist"] = "\n".join(chk)
    except Exception:
        pass
    return blocks


def render_optional_blocks(
    session: ResearchSession,
    include: list[str],
) -> dict[str, str]:
    blocks: dict[str, str] = {}
    want = set(include)

    if "ratio_deep" in want:
        try:
            from iis.finance.report_enrichment import ratio_interpretation_markdown

            body = ratio_interpretation_markdown(session)
            if body:
                blocks["ratio_deep"] = "## Ratio analysis\n\n" + "\n".join(body)
        except Exception:
            pass
    if "investor_checklist" in want:
        # Already always-on; ignore duplicate
        pass

    if "segments" in want:
        try:
            from iis.finance.ciq_tables import statement_table_markdown

            seg = statement_table_markdown(
                list(session.financial_line_items or []),
                "segments",
                max_periods=8,
            )
            if seg:
                blocks["segments"] = "\n".join(seg)
        except Exception:
            pass

    if "guidance_track" in want:
        try:
            from iis.finance.guidance_track import guidance_track_markdown

            body = guidance_track_markdown(
                list(session.guidance_records or []),
                list(session.financial_line_items or []),
            )
            if body and not any("No pivoted guidance" in (ln or "") for ln in body[:2]):
                blocks["guidance_track"] = "## Guidance vs actual\n\n" + "\n".join(body)
        except Exception:
            pass

    if "brokerage_deep" in want:
        try:
            from iis.finance.ciq_tables import brokerage_report_sections_markdown

            body = brokerage_report_sections_markdown(session)
            if body:
                blocks["brokerage_deep"] = "\n".join(body)
        except Exception:
            pass

    if "related_party" in want:
        blocks["related_party"] = (
            "## Related-party / governance watch\n\n"
            "_Included because Hive flagged material RPT / pledge risk. "
            "See AR related-party notes and pack claims; verify in filings._\n"
        )

    if "auditor_fees" in want:
        note = ""
        try:
            cat = build_report_data_catalog(session)
            note = (cat.get("signals") or {}).get("auditor_fee_note") or ""
        except Exception:
            pass
        blocks["auditor_fees"] = (
            "## Auditor fees / opinion\n\n"
            + (f"_Signal: {note}_\n\n" if note else "")
            + "_Review audit fee trend and auditor opinion/change in the latest AR. "
            "Hive requested this block because fees or opinion look material._\n"
        )

    if "remuneration" in want:
        note = ""
        try:
            cat = build_report_data_catalog(session)
            note = (cat.get("signals") or {}).get("remuneration_note") or ""
        except Exception:
            pass
        blocks["remuneration"] = (
            "## Managerial remuneration\n\n"
            + (f"_Signal: {note}_\n\n" if note else "")
            + "_Included only because pay looks elevated or jumped sharply. "
            "Cross-check KMP remuneration tables in the AR._\n"
        )

    # csr_esg intentionally only if Hive includes — still rare
    if "csr_esg" in want:
        blocks["csr_esg"] = (
            "## CSR / ESG note\n\n"
            "_Usually omitted. Included only due to a concrete controversy or "
            "material cash commitment — verify in AR/BRSR._\n"
        )

    return blocks


def finalize_initiating_report(
    session: ResearchSession,
    hive_markdown: str,
    *,
    company: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Strip include-block, fill markers, append always-on + selected optionals."""
    selection = parse_report_include_block(hive_markdown)
    narrative = strip_report_include_block(hive_markdown)

    always = render_always_on_blocks(session)
    # Auto-include optionals with elevated signals if Hive omitted the block entirely
    include = list(selection.get("include") or [])
    omit = set(selection.get("omit") or DEFAULT_OMIT)
    try:
        cat = build_report_data_catalog(session)
        sig = cat.get("signals") or {}
        if sig.get("auditor_fee_spike") and "auditor_fees" not in include and "auditor_fees" not in omit:
            include.append("auditor_fees")
        if sig.get("remuneration_flag") and "remuneration" not in include and "remuneration" not in omit:
            include.append("remuneration")
        if sig.get("related_party_flag") and "related_party" not in include and "related_party" not in omit:
            include.append("related_party")
    except Exception:
        pass
    # Never let omit kill always-on
    include = [i for i in include if i not in ALWAYS_ON_IDS]
    optional = render_optional_blocks(session, include)

    all_blocks = {**always, **optional}
    narrative = _fill_slot_markers(narrative, all_blocks)

    name = company or (session.company.name if session.company else "Company")
    parts = [
        f"# Initiating coverage — {name}",
        "",
        "_Fundamental research analyst write-up (Hive). "
        "Core Capital IQ tables and any selected optional blocks follow. "
        "Hover **ref** chips for sources._",
        "",
        narrative.strip(),
        "",
        "---",
        "",
        "# Appendix — core data tables (always on when available)",
        "",
        "_Python-rendered from Capital IQ / peer libraries. "
        "Narrative must not override these numbers._",
        "",
    ]
    for bid in (
        "inertia_scorecard",
        "investor_checklist",
        "ciq_financials",
        "ciq_estimates",
        "capex",
        "peer_timeline",
    ):
        if bid in always:
            parts.append(always[bid].rstrip())
            parts.append("")

    selected_opt = [bid for bid in include if bid in optional]
    if selected_opt:
        parts.extend(
            [
                "---",
                "",
                "# Appendix — optional blocks (Hive-selected)",
                "",
                f"_Included: {', '.join(selected_opt)}"
                + (f" · {selection.get('notes')}" if selection.get("notes") else "")
                + "_",
                "",
            ]
        )
        for bid in selected_opt:
            parts.append(optional[bid].rstrip())
            parts.append("")

    meta = {
        "always_on": list(always.keys()),
        "optional_included": selected_opt,
        "optional_omitted": sorted(omit),
        "selection_notes": selection.get("notes"),
    }
    return "\n".join(parts).rstrip() + "\n", meta
