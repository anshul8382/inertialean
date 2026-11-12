"""Annual report pack — business, statements/notes, governance, capital, WC, debt, risks…"""
from __future__ import annotations

from typing import Any

from iis.finance.packs.common import DOC_AR, hit_claims
from iis.finance.packs.models import empty_pack, finalize_pack
from iis.models.session import ResearchSession

AR_SECTIONS = [
    "business_positioning",  # A
    "statements_and_notes",  # B
    "governance",  # C
    "capital_allocation",  # D
    "working_capital",  # E
    "debt_obligations",  # F
    "shareholding",  # G
    "risk_factors",  # H
    "subsidiaries_segments",  # I
    "accounting_audit",  # J
]

_QUERIES: dict[str, list[tuple[str, list[str]]]] = {
    "business_positioning": [
        ("chairman_md_message", ["chairman's message", "managing director letter", "ceo message outlook"]),
        ("business_overview", ["business overview", "products applications", "revenue mix customers"]),
        ("mda_industry", ["management discussion analysis", "industry trends demand competition"]),
        ("segment_reporting", ["segment revenue", "operating segment results", "geographical segments"]),
        ("subsidiaries_jv", ["subsidiaries joint ventures", "associate companies investment"]),
        ("competitive_advantage", ["competitive advantage", "market share", "entry barriers"]),
    ],
    "statements_and_notes": [
        ("notes_revenue", ["revenue recognition note", "notes to accounts revenue"]),
        ("notes_exceptional", ["exceptional item", "extraordinary item", "one-time"]),
        ("notes_receivables", ["trade receivables", "expected credit loss", "doubtful debts"]),
        ("notes_inventory", ["inventories note", "inventory valuation"]),
        ("segment_results", ["segment results", "capital employed segment"]),
    ],
    "governance": [
        ("board", ["board of directors independent", "non-executive directors"]),
        ("related_party", ["related party transactions", "promoter related party"]),
        ("remuneration", ["managerial remuneration", "promoter remuneration"]),
        ("auditor", ["auditor report qualification", "emphasis of matter", "internal financial controls"]),
        ("contingent", ["contingent liabilities", "guarantees legal disputes"]),
        ("pledging", ["promoter pledging", "shares pledged"]),
    ],
    "capital_allocation": [
        ("capex", ["capital expenditure", "capex capacity expansion"]),
        ("capacity", ["capacity utilisation", "installed capacity", "commissioning"]),
        ("acquisitions", ["acquisition", "business combination goodwill"]),
        ("dividend_buyback", ["dividend payout", "buyback of shares"]),
        ("roce_incremental", ["return on capital employed", "incremental capital"]),
    ],
    "working_capital": [
        ("receivables", ["receivable days", "trade receivables days"]),
        ("inventory_days", ["inventory days", "inventory turnover"]),
        ("payables", ["trade payables", "supplier credit"]),
        ("contract_assets", ["contract assets", "unbilled revenue"]),
        ("advances", ["advances to suppliers", "capital advances"]),
    ],
    "debt_obligations": [
        ("borrowings", ["long-term borrowings", "secured unsecured loans"]),
        ("leases", ["lease liabilities", "right of use"]),
        ("fx_debt", ["foreign currency loan", "external commercial borrowing"]),
        ("interest", ["interest coverage", "finance costs"]),
        ("subsidiary_debt", ["subsidiary borrowings", "corporate guarantee"]),
    ],
    "shareholding": [
        ("promoter", ["promoter shareholding", "promoter holding"]),
        ("institutions", ["FII DII shareholding", "mutual fund holding"]),
        ("esop", ["employee stock option", "ESOP dilution"]),
        ("kmp_changes", ["key managerial personnel", "resignation appointment CFO"]),
    ],
    "risk_factors": [
        ("industry_risk", ["risk factors competition", "demand slowdown risk"]),
        ("financial_risk", ["liquidity risk", "currency risk debt"]),
        ("operational_risk", ["supply chain risk", "capacity utilisation risk"]),
        ("regulatory_risk", ["regulatory risk", "environmental licence"]),
        ("customer_risk", ["customer concentration", "key customer dependence"]),
    ],
    "subsidiaries_segments": [
        ("sub_performance", ["subsidiary performance", "loss making subsidiary"]),
        ("loans_to_subs", ["loans to subsidiaries", "advances to associates"]),
        ("standalone_vs_consol", ["standalone consolidated", "difference standalone"]),
        ("minority", ["non-controlling interest", "minority interest"]),
    ],
    "accounting_audit": [
        ("revenue_policy", ["significant accounting policies revenue"]),
        ("depreciation", ["depreciation useful life", "property plant equipment"]),
        ("goodwill", ["goodwill impairment"]),
        ("deferred_tax", ["deferred tax assets"]),
        ("auditor_qualification", ["qualified opinion", "adverse opinion auditor"]),
        ("accounting_change", ["change in accounting policy"]),
    ],
}


def build_ar_pack(session: ResearchSession) -> dict[str, Any]:
    company = session.company.name if session.company else (session.company_key or "Company")
    pack = empty_pack(
        pack_type="annual_report",
        company=company,
        company_key=session.company_key,
        template_sections=AR_SECTIONS,
    )
    # Prefer AR analysis artifact summaries when present
    ar_art = next((a for a in session.agent_artifacts if a.artifact_type == "annual_report_analysis"), None)
    if ar_art and isinstance(ar_art.payload, dict):
        pack["meta"]["annual_report_analysis_files"] = list((ar_art.payload.get("files") or {}).keys())[:12]

    for section, items in _QUERIES.items():
        claims: list[dict[str, Any]] = []
        for parameter, queries in items:
            claims.extend(
                hit_claims(
                    session,
                    section=section,
                    parameter=parameter,
                    queries=queries,
                    doc_types=DOC_AR,
                    limit_per_query=2,
                    max_claims=4,
                )
            )
        pack["sections"][section] = claims[:24]
    return finalize_pack(pack)
