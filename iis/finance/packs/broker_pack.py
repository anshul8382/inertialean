"""Brokerage pack — thesis, estimates, valuation, catalysts; street ≠ fact."""
from __future__ import annotations

from typing import Any

from iis.finance.packs.common import DOC_BR, hit_claims
from iis.finance.packs.models import empty_pack, finalize_pack, new_claim
from iis.models.session import ResearchSession

BR_SECTIONS = [
    "investment_thesis",  # A
    "financial_estimates",  # B
    "valuation",  # C
    "earnings_assumptions",  # D
    "peer_comparison",  # E
    "industry_outlook",  # F
    "catalysts",  # G
    "risks_downside",  # H
    "estimate_revisions",  # I
    "mgmt_vs_broker",  # J filled in joins
    "analyst_track_record",  # K
    "valuation_sensitivity",  # L
]


def build_broker_pack(session: ResearchSession) -> dict[str, Any]:
    company = session.company.name if session.company else (session.company_key or "Company")
    pack = empty_pack(
        pack_type="brokerage",
        company=company,
        company_key=session.company_key,
        template_sections=BR_SECTIONS,
    )

    # Pull brokerage_view artifact if present
    bv = next((a for a in session.agent_artifacts if a.artifact_type == "brokerage_view"), None)
    if bv and isinstance(bv.payload, dict):
        pack["meta"]["brokerage_view"] = {
            "rows": len(bv.payload.get("matrix") or []),
            "summary": (bv.payload.get("summary") or "")[:400],
        }
        for row in (bv.payload.get("matrix") or [])[:12]:
            if not isinstance(row, dict):
                continue
            pack["sections"]["financial_estimates"].append(
                new_claim(
                    section="financial_estimates",
                    parameter="street_matrix_row",
                    quote=(
                        f"{row.get('broker') or row.get('source') or 'broker'}: "
                        f"rating={row.get('rating')} TP={row.get('target_price')} "
                        f"FY={row.get('fiscal_year')} "
                        f"rev={row.get('revenue') or row.get('sales')} "
                        f"EPS={row.get('eps')}"
                    )[:500],
                    claim_type="estimate",
                    reliability="medium",
                    source_file=str(row.get("file") or row.get("source") or "brokerage_view"),
                    extra={"row": row},
                )
            )

    pack["sections"]["investment_thesis"] = hit_claims(
        session,
        section="investment_thesis",
        parameter="thesis",
        queries=[
            "investment thesis",
            "we recommend",
            "target price",
            "key takeaway",
            "initiate coverage",
            "upgrade downgrade",
        ],
        doc_types=DOC_BR,
        max_claims=10,
        claim_type_default="opinion",
    )

    pack["sections"]["financial_estimates"].extend(
        hit_claims(
            session,
            section="financial_estimates",
            parameter="projections",
            queries=[
                "revenue estimate FY",
                "EBITDA margin FY",
                "EPS estimate",
                "PAT growth",
                "ROCE estimate",
                "free cash flow forecast",
            ],
            doc_types=DOC_BR,
            max_claims=12,
            claim_type_default="estimate",
        )
    )
    pack["sections"]["financial_estimates"] = pack["sections"]["financial_estimates"][:24]

    pack["sections"]["valuation"] = hit_claims(
        session,
        section="valuation",
        parameter="methodology",
        queries=[
            "target price based on",
            "P/E multiple",
            "EV/EBITDA",
            "DCF valuation",
            "SOTP",
            "peer multiple",
            "re-rating",
        ],
        doc_types=DOC_BR,
        max_claims=12,
        claim_type_default="opinion",
    )

    pack["sections"]["earnings_assumptions"] = hit_claims(
        session,
        section="earnings_assumptions",
        parameter="drivers",
        queries=[
            "volume growth assumption",
            "ASP realisation",
            "capacity utilisation",
            "raw material assumption",
            "operating leverage",
            "tax rate assumption",
        ],
        doc_types=DOC_BR,
        max_claims=10,
        claim_type_default="estimate",
    )

    pack["sections"]["peer_comparison"] = hit_claims(
        session,
        section="peer_comparison",
        parameter="peers",
        queries=["peer comparison", "trading at premium", "peer P/E", "versus peers"],
        doc_types=DOC_BR,
        max_claims=8,
        claim_type_default="opinion",
    )

    pack["sections"]["industry_outlook"] = hit_claims(
        session,
        section="industry_outlook",
        parameter="industry",
        queries=[
            "industry growth",
            "demand supply",
            "capacity addition industry",
            "regulatory environment",
            "import export",
        ],
        doc_types=DOC_BR,
        max_claims=8,
        claim_type_default="opinion",
    )

    pack["sections"]["catalysts"] = hit_claims(
        session,
        section="catalysts",
        parameter="triggers",
        queries=[
            "key catalyst",
            "capacity commissioning",
            "margin recovery",
            "debt reduction",
            "trigger for re-rating",
        ],
        doc_types=DOC_BR,
        max_claims=8,
        claim_type_default="opinion",
    )

    pack["sections"]["risks_downside"] = hit_claims(
        session,
        section="risks_downside",
        parameter="risks",
        queries=[
            "key risks",
            "downside",
            "earnings downgrade",
            "execution delay",
            "valuation compression",
        ],
        doc_types=DOC_BR,
        max_claims=10,
        claim_type_default="opinion",
    )

    pack["sections"]["estimate_revisions"] = hit_claims(
        session,
        section="estimate_revisions",
        parameter="revisions",
        queries=["we revise estimates", "upgrade earnings", "cut estimates", "raise TP", "lower TP"],
        doc_types=DOC_BR,
        max_claims=8,
        claim_type_default="estimate",
    )

    pack["sections"]["valuation_sensitivity"] = hit_claims(
        session,
        section="valuation_sensitivity",
        parameter="sensitivity",
        queries=["sensitivity analysis", "bull bear base case", "scenario analysis"],
        doc_types=DOC_BR,
        max_claims=6,
        claim_type_default="opinion",
    )

    pack["sections"]["mgmt_vs_broker"] = []
    pack["sections"]["analyst_track_record"] = []
    pack["meta"]["note"] = (
        "All broker claims are STREET views — not company facts. "
        "Compare against AR/CIQ/concall packs before relying on them."
    )
    return finalize_pack(pack)
