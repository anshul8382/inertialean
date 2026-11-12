"""Valuation assumption review — surface issues and co-create inputs with the analyst.

Each company has different FCF/WC/capex quirks. Instead of hard-coding fixes, we
flag what the financials show, ask how to estimate, and let the user override.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from iis.finance.fcf_build import extract_fcf_drivers
from iis.finance.ratios import build_period_metrics, compute_period_ratios
from iis.models.artifacts import AssumptionProposal, FinancialLineItem
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import get_latest_actual


@dataclass
class ValuationReviewItem:
    id: str
    assumption_keys: list[str]
    severity: str  # needs_input | review | info
    headline: str
    observation: str
    question: str
    options: list[dict[str, str]] = field(default_factory=list)
    chat_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fy_int(period) -> int | None:
    import re

    m = re.search(r"(20\d{2})", str(period or ""))
    return int(m.group(1)) if m else None


def _fmt_mn(v: float | None) -> str:
    return f"₹{v:,.0f} mn" if v is not None else "N/A"


def compute_valuation_review(
    session: ResearchSession,
    *,
    assumptions: list[AssumptionProposal] | None = None,
    intrinsic: float | None = None,
    market_price: float | None = None,
) -> list[ValuationReviewItem]:
    items: list[FinancialLineItem] = session.financial_line_items
    if not items:
        return []

    assumptions = assumptions or session.assumptions
    assump_map = {a.key: a for a in assumptions}
    review: list[ValuationReviewItem] = []

    revenue = get_latest_actual(items, "total_revenue")
    ebitda = get_latest_actual(items, "ebitda")
    periods = build_period_metrics(items)
    sorted_p = sorted(periods.keys(), reverse=True)

    # --- Capex intensity: model steady-state vs history / consensus ---
    if "capex_pct_revenue" in assump_map:
        from iis.finance.fcf_build import extract_fcf_drivers

        try:
            drv = extract_fcf_drivers(items)
        except Exception:
            drv = None
        a = assump_map["capex_pct_revenue"]
        model_capex = a.user_value if a.user_value is not None else a.value
        hist = drv.capex_hist_median if drv else None
        cons = None
        cons_capex = {c.period: c.mean for c in session.consensus_metrics if c.metric == "Capital Expenditure" and c.actual is None}
        cons_rev = {c.period: c.mean for c in session.consensus_metrics if c.metric == "Revenue" and c.actual is None}
        common = sorted(set(cons_capex) & set(cons_rev))
        if common and cons_rev[common[0]]:
            cons = (abs(cons_capex[common[0]]) / cons_rev[common[0]], common[0])
        if hist is not None and isinstance(model_capex, (int, float)) and hist - float(model_capex) > 0.03:
            opts = [{"id": "hist", "label": f"Use historical median {hist:.1%}"}]
            if cons:
                opts.append({"id": "consensus", "label": f"Use CIQ consensus {cons[1]} capex/revenue {cons[0]:.1%}"})
            opts.append({"id": "keep", "label": f"Keep {float(model_capex):.1%} (maintenance-level, ≥ D&A {drv.da_pct_revenue:.1%})"})
            review.append(
                ValuationReviewItem(
                    id="capex_intensity_gap",
                    assumption_keys=["capex_pct_revenue"],
                    severity="needs_input",
                    headline="Capex assumption is well below the company's history",
                    observation=(
                        f"Model uses {float(model_capex):.1%} of sales; reported median is {hist:.1%}"
                        + (f" and CIQ consensus for {cons[1]} implies {cons[0]:.1%}" if cons else "")
                        + ". The intrinsic value is very sensitive to this choice."
                    ),
                    question="Which capex intensity should the steady-state DCF use?",
                    options=opts,
                    chat_prompt="e.g. 'set capex to 12%'",
                )
            )

    # --- Revenue growth: model vs CIQ consensus path ---
    if "revenue_growth_y1_5" in assump_map and revenue and revenue.value:
        g = assump_map["revenue_growth_y1_5"]
        model_g = g.user_value if g.user_value is not None else g.value
        cons_rev = sorted(
            ((c.period, c.mean) for c in session.consensus_metrics if c.metric == "Revenue" and c.actual is None and c.mean),
            key=lambda x: x[0],
        )
        base_year = _fy_int(revenue.period_end)
        last = cons_rev[-1] if cons_rev else None
        n = (_fy_int(last[0]) - base_year) if last and base_year else 0
        if last and n > 0 and isinstance(model_g, (int, float)):
            cons_cagr = (last[1] / revenue.value) ** (1 / n) - 1
            if abs(cons_cagr - float(model_g)) > 0.03:
                from iis.finance.growth import compute_revenue_cagr

                hist = compute_revenue_cagr(items, years=3)
                opts = [{"id": "consensus", "label": f"Use CIQ consensus revenue CAGR FY{base_year}→{last[0]} {cons_cagr:.1%}"}]
                if hist is not None:
                    opts.append({"id": "hist", "label": f"Use 3Y historical CAGR {hist:.1%}"})
                opts.append({"id": "keep", "label": f"Keep {float(model_g):.1%}"})
                review.append(
                    ValuationReviewItem(
                        id="revenue_growth_gap",
                        assumption_keys=["revenue_growth_y1_5"],
                        severity="needs_input",
                        headline="Model revenue growth differs from CIQ consensus",
                        observation=(
                            f"Model uses {float(model_g):.1%} p.a.; CIQ consensus revenue implies {cons_cagr:.1%} p.a. "
                            f"from FY{base_year} actual ₹{revenue.value:,.0f} mn to {last[0]} ₹{last[1]:,.0f} mn."
                        ),
                        question="Which revenue growth path should the DCF use?",
                        options=opts,
                        chat_prompt="e.g. 'set revenue growth to 12%'",
                    )
                )

    # --- Spot FCF vs EBITDA (WC / capex story) ---
    if sorted_p:
        latest_p = sorted_p[0]
        pm = periods[latest_p]
        rev = pm.get("total_revenue")
        ebitda_v = pm.get("ebitda")
        ratios = compute_period_ratios(pm)
        fcf = ratios.get("fcf_mn")
        d_nwc = next(
            (i.value for i in items if i.line_item == "change_in_net_working_capital" and i.period_end == latest_p),
            None,
        )
        inv_chg = next(
            (i.value for i in items if i.line_item == "change_in_inventories" and i.period_end == latest_p),
            None,
        )
        capex = pm.get("capital_expenditure")

        if ebitda_v and ebitda_v > 0 and fcf is not None and fcf < 0:
            wc_keys = [k for k in ("wc_investment_rate", "nwc_pct_revenue", "fcf_conversion") if k in assump_map]
            review.append(
                ValuationReviewItem(
                    id="fcf_depressed_spot",
                    assumption_keys=wc_keys or ["fcf_conversion"],
                    severity="needs_input",
                    headline="Latest FCF is negative despite positive EBITDA",
                    observation=(
                        f"FY{latest_p[:4]}: EBITDA {_fmt_mn(ebitda_v)}, FCF {_fmt_mn(fcf)}. "
                        f"ΔNWC {_fmt_mn(d_nwc)}, inventory change {_fmt_mn(inv_chg)}, capex {_fmt_mn(capex)}. "
                        "This often reflects inventory / working-capital build during growth — not necessarily "
                        "steady-state cash generation."
                    ),
                    question="For the DCF, how should we treat FCF conversion as growth moderates?",
                    options=[
                        {
                            "id": "normalize_wc",
                            "label": "Use lower WC investment once growth fades toward terminal rate",
                        },
                        {
                            "id": "exclude_outlier",
                            "label": "Base FCF on a normal year (exclude latest WC spike)",
                        },
                        {
                            "id": "manual",
                            "label": "I'll set WC / FCF assumptions manually in Gate 4",
                        },
                    ],
                    chat_prompt=(
                        "Tell me how to estimate FCF for the DCF — e.g. 'use 40% FCF/EBITDA at steady state' "
                        "or 'assume WC investment drops to 20% of revenue growth after year 3'."
                    ),
                )
            )

    # --- Volatile historical FCF/EBITDA ---
    conversions: list[float] = []
    for p in sorted_p[:5]:
        pm = periods[p]
        eb = pm.get("ebitda")
        f = compute_period_ratios(pm).get("fcf_mn")
        if eb and eb > 0 and f is not None:
            conversions.append(f / eb)
    if len(conversions) >= 3:
        pos = [c for c in conversions if c > 0]
        neg = sum(1 for c in conversions if c < 0)
        if neg >= 1 and pos:
            spread = max(pos) - min(pos) if pos else 0
            if spread > 0.25 or neg >= 2:
                review.append(
                    ValuationReviewItem(
                        id="fcf_conversion_volatile",
                        assumption_keys=["fcf_conversion", "wc_investment_rate"],
                        severity="needs_input",
                        headline="Historical FCF ÷ EBITDA is unstable across years",
                        observation=(
                            f"Recent FCF/EBITDA ratios include negative years and a wide spread "
                            f"({', '.join(f'{c:.0%}' for c in conversions[:4])}). "
                            "A single historical average may misrepresent forward cash flow."
                        ),
                        question="Which period or method should we use for forward FCF conversion?",
                        options=[
                            {"id": "steady_state", "label": "Estimate steady-state FCF once growth normalizes"},
                            {"id": "avg_positive", "label": "Average positive FCF years only"},
                            {"id": "manual_pct", "label": "I'll specify a % in chat or Gate 4"},
                        ],
                        chat_prompt="What FCF-to-EBITDA (or WC/capex) assumptions do you want in the model?",
                    )
                )

    # --- IV vs market large gap ---
    if intrinsic and market_price and intrinsic > 0 and market_price > 0:
        gap = abs(intrinsic - market_price) / market_price
        if gap > 0.5:
            direction = "below" if intrinsic < market_price else "above"
            review.append(
                ValuationReviewItem(
                    id="iv_market_divergence",
                    assumption_keys=["revenue_growth_y1_5", "ebitda_margin", "wc_investment_rate", "fcf_conversion"],
                    severity="needs_input",
                    headline=f"Model IV is {gap:.0%} {direction} market price",
                    observation=(
                        f"DCF intrinsic value ₹{intrinsic:,.0f}/sh vs market ₹{market_price:,.0f}/sh. "
                        "Large gaps usually mean growth, margins, or WC/FCF assumptions need your judgment — "
                        "not that the formula is wrong."
                    ),
                    question="Which drivers should we revisit together before you approve Gate 4?",
                    options=[
                        {"id": "growth", "label": "Revenue growth path (explicit + terminal fade)"},
                        {"id": "wc_fcf", "label": "WC / inventory and FCF conversion"},
                        {"id": "margin", "label": "Steady-state margin"},
                        {"id": "all", "label": "Walk through all flagged assumptions"},
                    ],
                    chat_prompt=(
                        "Let's co-create the valuation assumptions — tell me which inputs you disagree with "
                        "and what you'd use instead (e.g. '15% 5Y growth fading to 8%', '50% FCF/EBITDA at maturity')."
                    ),
                )
            )

    # --- High WC intensity (inventory-heavy businesses) ---
    try:
        drivers = extract_fcf_drivers(items)
        if drivers.nwc_pct_revenue > 0.35:
            review.append(
                ValuationReviewItem(
                    id="high_nwc_intensity",
                    assumption_keys=["nwc_pct_revenue", "wc_investment_rate"],
                    severity="review",
                    headline="Working capital is a large share of revenue",
                    observation=(
                        f"Net WC ≈ {drivers.nwc_pct_revenue:.0%} of revenue; "
                        f"historical WC investment ≈ {drivers.wc_investment_rate:.0%} of incremental revenue. "
                        f"{drivers.rationale}"
                    ),
                    question="Does this match how you expect inventory / receivables to behave as growth slows?",
                    options=[
                        {"id": "accept", "label": "Use reported ratios — I'll adjust if needed"},
                        {"id": "lower_terminal", "label": "Use lower WC drag in terminal / outer years"},
                        {"id": "discuss", "label": "Discuss in chat before approving"},
                    ],
                    chat_prompt="How should inventory and working capital affect FCF in your DCF view?",
                )
            )
    except Exception:
        pass

    # --- Mark assumptions that need user input ---
    flagged_keys = {k for item in review if item.severity == "needs_input" for k in item.assumption_keys}
    for a in assumptions:
        if a.key in flagged_keys:
            a.needs_user_input = True
            prompts = [i.question for i in review if a.key in i.assumption_keys and i.severity == "needs_input"]
            if prompts and not a.review_prompt:
                a.review_prompt = prompts[0]

    return review


def review_to_payload(items: list[ValuationReviewItem]) -> dict[str, Any]:
    needs = [i for i in items if i.severity == "needs_input"]
    return {
        "items": [i.to_dict() for i in items],
        "needs_input_count": len(needs),
        "co_create_note": (
            "Some DCF inputs need your judgment before Gate 4 approval. "
            "Review the items below, edit values in **Valuation** gate, or tell us in chat how to estimate."
            if needs
            else None
        ),
    }


def apply_assumption_keys_for_review(session: ResearchSession) -> None:
    """Attach needs_user_input flags from stored review artifact."""
    art = next((a for a in session.agent_artifacts if a.artifact_type == "valuation_review"), None)
    if not art:
        return
    flagged: set[str] = set()
    for item in art.payload.get("items", []):
        if item.get("severity") == "needs_input":
            flagged.update(item.get("assumption_keys") or [])
    for a in session.assumptions:
        if a.key in flagged:
            a.needs_user_input = True
