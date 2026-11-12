"""Concall pack — guidance typing, Q&A, margins, capex, commentary change inputs."""
from __future__ import annotations

from typing import Any

from iis.finance.packs.common import DOC_CC, hit_claims, qa_claims
from iis.finance.packs.models import empty_pack, finalize_pack, new_claim
from iis.models.enums import PdfDocType
from iis.models.session import ResearchSession

CC_SECTIONS = [
    "growth_quality",  # A
    "guidance_outlook",  # B
    "margins",  # C
    "capex_capacity",  # D
    "competitive_positioning",  # E
    "management_credibility",  # F
    "qa_insights",  # G
    "working_capital_cash",  # H
    "new_initiatives",  # I
    "risks_challenges",  # J
    "guidance_vs_actual",  # K (filled from CIQ join)
    "commentary_changes",  # L (matrix filled in joins)
]

_GUIDANCE_PARAMS = [
    ("revenue_growth", ["revenue growth guidance", "expect revenue growth", "top line growth"]),
    ("ebitda_margins", ["EBITDA margin guidance", "margin outlook", "target margins"]),
    ("volume_growth", ["volume growth", "volume expansion"]),
    ("capex", ["capex guidance", "planned capex", "capital expenditure plan"]),
    ("capacity", ["new capacity", "commissioning", "capacity addition"]),
    ("order_book", ["order book", "order inflow", "execution timeline"]),
    ("market_share", ["market share", "competitive positioning"]),
    ("new_businesses", ["new business contribution", "new segment profitability"]),
]


def build_concall_pack(session: ResearchSession) -> dict[str, Any]:
    company = session.company.name if session.company else (session.company_key or "Company")
    pack = empty_pack(
        pack_type="concall",
        company=company,
        company_key=session.company_key,
        template_sections=CC_SECTIONS,
    )
    transcripts = [
        d for d in (session.pdf_documents or [])
        if getattr(d, "doc_type", None) == PdfDocType.TRANSCRIPT.value
        or str(getattr(d, "doc_type", "")).endswith("transcript")
    ]
    pack["meta"]["transcript_files"] = [getattr(d, "file", None) or getattr(d, "filename", None) for d in transcripts][:20]
    pack["meta"]["qa_pair_count"] = sum(len(getattr(d, "qa_pairs", None) or []) for d in (session.pdf_documents or []))

    # A growth quality
    pack["sections"]["growth_quality"] = hit_claims(
        session,
        section="growth_quality",
        parameter="core_vs_one_off",
        queries=["organic growth", "one-time", "exceptional", "volume price mix", "base business"],
        doc_types=DOC_CC,
        max_claims=6,
        classify_forward=True,
    ) + qa_claims(
        session,
        section="growth_quality",
        parameter="core_vs_one_off_qa",
        queries=["growth driver", "one off", "organic"],
        max_claims=4,
    )

    # B guidance — classify explicit / qualitative / aspirational
    guidance_claims: list[dict[str, Any]] = []
    for param, queries in _GUIDANCE_PARAMS:
        guidance_claims.extend(
            hit_claims(
                session,
                section="guidance_outlook",
                parameter=param,
                queries=queries,
                doc_types=DOC_CC,
                max_claims=3,
                classify_forward=True,
            )
        )
        guidance_claims.extend(
            qa_claims(
                session,
                section="guidance_outlook",
                parameter=param,
                queries=queries[:2],
                max_claims=2,
            )
        )
    pack["sections"]["guidance_outlook"] = guidance_claims[:40]

    pack["sections"]["margins"] = hit_claims(
        session,
        section="margins",
        parameter="margin_drivers",
        queries=[
            "raw material cost",
            "employee cost",
            "operating leverage",
            "product mix margin",
            "utilisation margin",
            "pricing power",
        ],
        doc_types=DOC_CC,
        max_claims=10,
        classify_forward=True,
    ) + qa_claims(
        session, section="margins", parameter="margin_qa",
        queries=["margin", "EBITDA", "gross margin"], max_claims=6,
    )

    pack["sections"]["capex_capacity"] = hit_claims(
        session,
        section="capex_capacity",
        parameter="capex_plan",
        queries=["capex plan", "capacity addition", "utilisation", "commissioning", "funding capex", "ROCE project"],
        doc_types=DOC_CC,
        max_claims=10,
        classify_forward=True,
    )

    pack["sections"]["competitive_positioning"] = hit_claims(
        session,
        section="competitive_positioning",
        parameter="competition",
        queries=["competitor", "market share", "imports", "price competition", "industry capacity"],
        doc_types=DOC_CC,
        max_claims=8,
        classify_forward=True,
    ) + qa_claims(
        session, section="competitive_positioning", parameter="competition_qa",
        queries=["competition", "market share", "imports"], max_claims=4,
    )

    pack["sections"]["management_credibility"] = qa_claims(
        session,
        section="management_credibility",
        parameter="tone_and_specificity",
        queries=["why", "delay", "miss", "challenge", "uncertain", "cannot comment"],
        max_claims=10,
    )

    # G — high-value analyst Q themes
    pack["sections"]["qa_insights"] = qa_claims(
        session,
        section="qa_insights",
        parameter="analyst_questions",
        queries=[
            "margin declining",
            "receivable days",
            "capex delay",
            "market share",
            "employee cost",
            "acquisition return",
            "guidance revise",
            "working capital",
        ],
        max_claims=16,
    )
    # Repeated question themes (simple keyword clustering)
    themes: dict[str, int] = {}
    for c in pack["sections"]["qa_insights"]:
        q = ((c.get("extra") or {}).get("question") or c.get("quote") or "").lower()
        for theme in ("margin", "receivable", "capex", "competition", "guidance", "working capital", "debt"):
            if theme in q:
                themes[theme] = themes.get(theme, 0) + 1
    pack["meta"]["repeated_qa_themes"] = {k: v for k, v in themes.items() if v >= 2}

    pack["sections"]["working_capital_cash"] = hit_claims(
        session,
        section="working_capital_cash",
        parameter="wc_cash",
        queries=["inventory", "receivables", "payable", "working capital", "cash flow"],
        doc_types=DOC_CC,
        max_claims=8,
        classify_forward=True,
    ) + qa_claims(
        session, section="working_capital_cash", parameter="wc_qa",
        queries=["working capital", "receivable", "inventory"], max_claims=4,
    )

    pack["sections"]["new_initiatives"] = hit_claims(
        session,
        section="new_initiatives",
        parameter="growth_initiatives",
        queries=["new product", "new geography", "acquisition", "partnership", "R&D"],
        doc_types=DOC_CC,
        max_claims=8,
        classify_forward=True,
    )

    pack["sections"]["risks_challenges"] = hit_claims(
        session,
        section="risks_challenges",
        parameter="risks",
        queries=["demand slowdown", "regulatory", "raw material shortage", "customer concentration", "project delay"],
        doc_types=DOC_CC,
        max_claims=8,
        classify_forward=True,
    )

    # K — attach CIQ guidance track rows as claims (deterministic)
    try:
        from iis.finance.guidance_track import guidance_track_rows, guidance_track_summary

        rows = guidance_track_rows(session.guidance_records, session.financial_line_items)
        summary = guidance_track_summary(rows)
        pack["meta"]["guidance_track_summary"] = summary
        gclaims: list[dict[str, Any]] = []
        for r in rows[:20]:
            gclaims.append(
                new_claim(
                    section="guidance_vs_actual",
                    parameter=str(r.get("metric") or "metric"),
                    quote=(
                        f"{r.get('metric')} FY{r.get('fiscal_year')}: "
                        f"guidance={r.get('guidance')} actual={r.get('actual')} "
                        f"outcome={r.get('outcome')} {r.get('delta_label') or ''}"
                    ),
                    claim_type="explicit",
                    value=r.get("guidance"),
                    period=str(r.get("fiscal_year") or ""),
                    reliability="high",
                    source_file="capital_iq_guidance",
                    extra={"row": {k: r.get(k) for k in ("metric", "fiscal_year", "guidance", "actual", "outcome", "delta_label")}},
                )
            )
        pack["sections"]["guidance_vs_actual"] = gclaims
    except Exception:
        pack["sections"]["guidance_vs_actual"] = []

    pack["sections"]["commentary_changes"] = []  # filled by joins across calls
    return finalize_pack(pack)
