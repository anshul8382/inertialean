"""Python tools the LLM analyst may call after data collection.

All tools read from the already-ingested session (CIQ, AR, concalls, artifacts).
They never invent facts — they only retrieve / format what Python already extracted.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from iis.models.session import ResearchSession

ToolFn = Callable[[ResearchSession, dict[str, Any]], str]

TOOL_SPECS: list[dict[str, str]] = [
    {
        "name": "get_business_overview",
        "description": "Business overview + initiating-coverage detail checklist (products, mix, geo, clients, capacity) scored from documents, investment/rejection cases, research questions, qual-vs-numbers.",
        "args": "{}",
    },
    {
        "name": "get_key_metrics",
        "description": "Latest revenue, EBITDA, margins, CAGR from Capital IQ Financials.",
        "args": "{}",
    },
    {
        "name": "get_ratios",
        "description": "Latest ratio summary (ROE, ROCE, leverage, FCF, etc.).",
        "args": "{}",
    },
    {
        "name": "get_valuation",
        "description": "DCF fair value, MoS, key assumptions if computed.",
        "args": "{}",
    },
    {
        "name": "get_guidance_track",
        "description": "Management guidance vs actuals from Estimates + Financials.",
        "args": "{}",
    },
    {
        "name": "get_swot",
        "description": "SWOT from annual report / qualitative assembly.",
        "args": "{}",
    },
    {
        "name": "get_flags",
        "description": "Data-quality / financial red flags already computed.",
        "args": "{}",
    },
    {
        "name": "search_pdf",
        "description": "Keyword search over ingested PDF chunks (AR, concall, presentations).",
        "args": '{"query": "string", "limit": 5}',
    },
    {
        "name": "search_concall_qa",
        "description": "Search concall transcript Q&A pairs.",
        "args": '{"query": "string", "limit": 5}',
    },
    {
        "name": "get_dossier_topic",
        "description": "Evidence-dossier excerpts for a topic key (e.g. risks, outlook, competition).",
        "args": '{"topic": "string", "limit": 6}',
    },
    {
        "name": "get_porters",
        "description": "Porter five-forces artifact from uploaded files.",
        "args": "{}",
    },
    {
        "name": "get_brokerage_matrix",
        "description": "Broker estimates (parsed from broker PDFs) vs CIQ consensus, ratings, target prices, staleness flags.",
        "args": "{}",
    },
    {
        "name": "get_checklists",
        "description": "Investor checklists computed in Python: accounting quality, balance sheet, moat, management, governance evidence.",
        "args": "{}",
    },
    {
        "name": "get_reconciliation",
        "description": "Data consistency checks (basis, balance sheet identity, CIQ snapshots, price cross-check) and open data gaps.",
        "args": "{}",
    },
    {
        "name": "get_peers",
        "description": "User-confirmed peers with CIQ metrics from their own files; proposed peers awaiting confirmation.",
        "args": "{}",
    },
    {
        "name": "get_facts",
        "description": "Fact sheet with IDs (CIQ actuals/estimates, ratios, price, broker numbers) and provenance.",
        "args": '{"filter": "optional substring, e.g. revenue or FY27"}',
    },
    {
        "name": "get_research_pack",
        "description": (
            "Typed research packs on disk: ar | concall | broker | index. "
            "Optional section key (e.g. guidance_outlook, investment_thesis, mgmt_vs_broker). "
            "Broker pack = STREET views only."
        ),
        "args": '{"pack": "ar|concall|broker|index", "section": "optional", "limit": 12}',
    },
]


def tool_catalog_text() -> str:
    lines = ["Available Python tools (call only these):"]
    for t in TOOL_SPECS:
        lines.append(f"- {t['name']}{t['args']}: {t['description']}")
    return "\n".join(lines)


def run_tool(session: ResearchSession, name: str, args: dict[str, Any] | None = None) -> str:
    args = args or {}
    fn = _REGISTRY.get(name)
    if not fn:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        return fn(session, args)
    except Exception as exc:
        return json.dumps({"error": str(exc)[:300]})


def _get_key_metrics(session: ResearchSession, _args: dict) -> str:
    from iis.finance.metrics import get_key_metrics_summary
    from iis.finance.growth import compute_revenue_cagr

    m = get_key_metrics_summary(session.financial_line_items or [])
    cagr = compute_revenue_cagr(session.financial_line_items or [], years=3)
    out = {**m, "revenue_cagr_3y": cagr, "line_items": len(session.financial_line_items or [])}
    return json.dumps(out, default=str)[:4000]


def _get_ratios(session: ResearchSession, _args: dict) -> str:
    from iis.finance.ratios import format_ratio_value, get_latest_ratio_summary

    r = get_latest_ratio_summary(session.financial_line_items or [])
    pretty = {k: format_ratio_value(k, v) for k, v in (r or {}).items() if v is not None}
    return json.dumps(pretty, default=str)[:4000]


def _get_valuation(session: ResearchSession, _args: dict) -> str:
    v = session.valuation
    if not v:
        return json.dumps({"status": "no_valuation_yet"})
    return json.dumps(
        {
            "intrinsic_value_per_share": v.intrinsic_value_per_share,
            "margin_of_safety_pct": v.margin_of_safety_pct,
            "method": getattr(v, "method", None),
            "notes": (getattr(v, "notes", None) or "")[:500],
        },
        default=str,
    )[:3000]


def _get_guidance_track(session: ResearchSession, _args: dict) -> str:
    from iis.finance.guidance_track import guidance_track_rows, guidance_track_summary

    rows = guidance_track_rows(session.guidance_records, session.financial_line_items)
    summary = guidance_track_summary(rows)
    return json.dumps({"summary": summary, "rows": rows[:15]}, default=str)[:5000]


def _get_swot(session: ResearchSession, _args: dict) -> str:
    try:
        from iis.finance.swot import resolve_swot

        swot = resolve_swot(session) or {}
    except Exception:
        art = next(
            (a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"),
            None,
        )
        swot = (art.payload.get("swot") if art else None) or {}
    return json.dumps(swot, default=str)[:4000]


def _get_flags(session: ResearchSession, _args: dict) -> str:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "data_flags"), None)
    if not art:
        return json.dumps({"flags": []})
    return json.dumps(art.payload, default=str)[:4000]


def _search_pdf(session: ResearchSession, args: dict) -> str:
    from iis.ingest.pdf import search_pdf_chunks

    query = str(args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query required"})
    limit = min(int(args.get("limit") or 5), 8)
    hits = search_pdf_chunks(session.pdf_chunks, query, limit=limit)
    rows = [
        {
            "file": c.file,
            "page": c.page,
            "doc_type": getattr(c, "doc_type", None),
            "text": (c.text or "")[:500],
        }
        for c in hits
    ]
    return json.dumps({"hits": rows}, default=str)[:6000]


def _search_concall_qa(session: ResearchSession, args: dict) -> str:
    from iis.ingest.pdf import search_transcript_qa

    query = str(args.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query required"})
    limit = min(int(args.get("limit") or 5), 8)
    hits = search_transcript_qa(session.pdf_documents, query, limit=limit)
    rows = [
        {
            "file": qa.file,
            "page": qa.page,
            "question": (qa.question or "")[:200],
            "answer": (qa.answer or "")[:400],
        }
        for qa in hits
    ]
    return json.dumps({"qa": rows}, default=str)[:6000]


def _get_dossier_topic(session: ResearchSession, args: dict) -> str:
    from iis.finance.evidence_dossier import build_evidence_dossier

    topic = str(args.get("topic") or "").strip().lower()
    limit = min(int(args.get("limit") or 6), 10)
    dossier = build_evidence_dossier(session)
    def _row(h) -> dict:
        return {
            "topic": h.topic,
            "heading": h.heading,
            "file": h.source_file,
            "page": h.page,
            "text": (h.text or "")[:450],
        }

    hits = [
        _row(h)
        for h in dossier.hits
        if not topic or topic in (h.topic or "").lower() or topic in (h.heading or "").lower()
    ][:limit]
    if not hits and topic:
        hits = [_row(h) for h in dossier.hits if topic in (h.text or "").lower()][:limit]
    return json.dumps({"topic": topic, "hits": hits, "topic_counts": dossier.topic_counts}, default=str)[
        :6000
    ]


def _get_porters(session: ResearchSession, _args: dict) -> str:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "porters_five_forces"), None)
    if not art:
        return json.dumps({"status": "not_computed"})
    return json.dumps(art.payload, default=str)[:5000]


def _artifact_json(session: ResearchSession, atype: str, limit: int = 6000) -> str:
    art = next((a for a in session.agent_artifacts if a.artifact_type == atype), None)
    if not art:
        return json.dumps({"status": "not_computed"})
    return json.dumps(art.payload, default=str)[:limit]


def _get_brokerage_matrix(session: ResearchSession, _args: dict) -> str:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "brokerage_view"), None)
    if not art:
        return json.dumps({"status": "not_computed"})
    p = art.payload

    def _row(r: dict) -> dict:
        out = {k: r.get(k) for k in ("broker", "report_date", "covers", "rating", "target_price", "stale", "flags")}
        out["cells"] = {
            k: {"value": c.get("value"), "vs_ciq": c.get("vs_ciq"), "outlier": c.get("outlier") or None, "excluded": c.get("excluded") or None}
            for k, c in (r.get("cells") or {}).items()
        }
        return out

    rows = sorted(p.get("matrix") or [], key=lambda r: (bool(r.get("stale")), str(r.get("report_date") or "")), reverse=False)
    current = [_row(r) for r in rows if not r.get("stale")]
    stale = [_row(r) for r in rows if r.get("stale")]
    slim = {
        "note": "broker_median in consensus uses CURRENT (non-stale, non-excluded) broker cells only",
        "years": p.get("years"),
        "newest_results_quarter": p.get("newest_results_quarter"),
        "consensus": p.get("consensus"),
        "target_price": p.get("target_price"),
        "ratings_current": p.get("ratings_current"),
        "brokers_current": current,
        "brokers_stale": stale[:8],
    }
    return json.dumps(slim, default=str, separators=(",", ":"))[:9000]


def _get_checklists(session: ResearchSession, _args: dict) -> str:
    return _artifact_json(session, "critique", 7000)


def _get_reconciliation(session: ResearchSession, _args: dict) -> str:
    return _artifact_json(session, "reconciliation", 5000)


def _get_peers(session: ResearchSession, _args: dict) -> str:
    from iis.finance.competitor_discovery import confirmed_peer_libraries
    from iis.finance.metrics import get_key_metrics_summary
    from iis.finance.ratios import get_latest_ratio_summary
    from iis.library.store import get_company_library_store

    rows = []
    store = get_company_library_store()
    for p in confirmed_peer_libraries(session):
        lib = store.load(p["company_key"])
        m = get_key_metrics_summary(lib.financial_line_items)
        r = get_latest_ratio_summary(lib.financial_line_items)
        rows.append({"name": p["name"], "symbol": p.get("symbol"), "source": "peer CIQ Financials on file",
                     "metrics": {k: m.get(k) for k in ("revenue_mn", "ebitda_mn", "ebitda_margin", "net_income_mn", "revenue_cagr_3y")},
                     "ratios": {k: r.get(k) for k in ("roe", "roce", "net_debt_to_ebitda", "interest_coverage")}})
    art = next((a for a in session.agent_artifacts if a.artifact_type == "competitor_suggestions"), None)
    proposed = [x.get("name") for x in (art.payload.get("proposed") or [])] if art else []
    return json.dumps({"confirmed_with_ciq": rows, "proposed_not_confirmed": proposed,
                       "rule": "Only confirmed peers with CIQ files may be compared."}, default=str)[:6000]


def _get_facts(session: ResearchSession, args: dict) -> str:
    from iis.facts.store import build_fact_store

    flt = str(args.get("filter") or "").lower().strip()
    facts = build_fact_store(session)
    rows = [f for f in facts if not flt or flt in (f["label"] + " " + (f.get("period") or "")).lower()]
    return json.dumps({"facts": rows[:120], "total": len(facts)}, default=str)[:8000]


def _get_business_overview(session: ResearchSession, _args: dict) -> str:
    from iis.finance.business_overview import (
        build_business_overview,
        build_investment_and_rejection,
        build_qual_vs_numbers,
        build_research_questions,
    )

    ov = build_business_overview(session)
    # Checklist can be large — keep summary + open asks for the LLM tool budget
    chk = ov.get("checklist") or {}
    chk_slim = {
        "coverage_pct": chk.get("coverage_pct"),
        "summary": chk.get("summary"),
        "items": [
            {
                "id": it.get("id"),
                "item": it.get("item"),
                "status": it.get("status"),
                "priority": it.get("priority"),
                "ask": it.get("ask"),
                "snippets": (it.get("snippets") or [])[:2],
            }
            for it in (chk.get("items") or [])
        ],
        "open_asks": (chk.get("open_asks") or [])[:10],
    }
    ov_out = {k: v for k, v in ov.items() if k != "checklist"}
    ov_out["checklist"] = chk_slim
    return json.dumps(
        {
            "overview": ov_out,
            "cases": build_investment_and_rejection(session),
            "research_questions": build_research_questions(session),
            "qual_vs_numbers": build_qual_vs_numbers(session),
        },
        default=str,
    )[:12000]


def _get_research_pack(session: ResearchSession, args: dict) -> str:
    from iis.finance.packs.store import read_pack, read_pack_markdown

    which = str(args.get("pack") or "index").strip().lower()
    section = str(args.get("section") or "").strip() or None
    limit = min(int(args.get("limit") or 12), 24)
    name = {
        "ar": "ar_pack",
        "annual": "ar_pack",
        "annual_report": "ar_pack",
        "concall": "concall_pack",
        "transcript": "concall_pack",
        "broker": "broker_pack",
        "brokerage": "broker_pack",
        "index": "index",
    }.get(which, which if which.endswith("_pack") else f"{which}_pack")

    if name == "index" or which == "index":
        art = next((a for a in session.agent_artifacts if a.artifact_type == "research_packs"), None)
        if art:
            return json.dumps(art.payload, default=str)[:6000]
        from iis.finance.packs.store import packs_dir

        ip = packs_dir(session) / "index.json"
        if ip.exists():
            return ip.read_text(encoding="utf-8")[:6000]
        return json.dumps({"status": "packs_not_built", "hint": "Run Analyze to build research packs"})

    pack = read_pack(session, name if name.endswith("_pack") else f"{name}")
    # normalize filename
    if pack is None:
        for cand in (name, f"{which}_pack", "ar_pack", "concall_pack", "broker_pack"):
            pack = read_pack(session, cand)
            if pack:
                name = cand
                break
    if not pack:
        md = read_pack_markdown(session, name, max_chars=4000)
        if md:
            return json.dumps({"pack": name, "markdown_excerpt": md})[:8000]
        return json.dumps({"status": "not_found", "pack": name})

    if section:
        claims = (pack.get("sections") or {}).get(section) or []
        return json.dumps(
            {
                "pack": name,
                "section": section,
                "claims": claims[:limit],
                "claim_count": len(claims),
            },
            default=str,
        )[:9000]

    # Summary + top claims per section
    slim_sections = {}
    for sk, claims in (pack.get("sections") or {}).items():
        if not isinstance(claims, list) or not claims:
            continue
        slim_sections[sk] = claims[: min(3, limit)]
    return json.dumps(
        {
            "pack": name,
            "company": pack.get("company"),
            "claim_count": pack.get("claim_count"),
            "meta": pack.get("meta"),
            "sections_preview": slim_sections,
            "hint": "Pass section= to load more claims for one slot",
        },
        default=str,
    )[:9000]


_REGISTRY: dict[str, ToolFn] = {
    "get_business_overview": _get_business_overview,
    "get_brokerage_matrix": _get_brokerage_matrix,
    "get_checklists": _get_checklists,
    "get_reconciliation": _get_reconciliation,
    "get_peers": _get_peers,
    "get_facts": _get_facts,
    "get_key_metrics": _get_key_metrics,
    "get_ratios": _get_ratios,
    "get_valuation": _get_valuation,
    "get_guidance_track": _get_guidance_track,
    "get_swot": _get_swot,
    "get_flags": _get_flags,
    "search_pdf": _search_pdf,
    "search_concall_qa": _search_concall_qa,
    "get_dossier_topic": _get_dossier_topic,
    "get_porters": _get_porters,
    "get_research_pack": _get_research_pack,
}
