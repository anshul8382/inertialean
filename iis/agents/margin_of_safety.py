from __future__ import annotations

from iis.agents.base import BaseAgent
from iis.config import settings
from iis.finance.dcf import (
    compute_fcf_build_sensitivity,
    inverse_dcf_implied_growth_fcf_build,
    terminal_growth_scenarios,
)
from iis.finance.dcf_sanity import clamp_dcf_margin
from iis.finance.fcf_build import (
    FCFDrivers,
    dcf_equity_from_fcf_build,
    extract_fcf_drivers,
    project_fcf_years,
    steady_state_fcf_pct_of_ebitda,
)
from iis.finance.market_data import fetch_live_price, is_reliable_quote
from iis.finance.metrics import compute_revenue_cagr, get_key_metrics_summary, get_net_debt_mn, get_shares_outstanding_mn
from iis.finance.valuation_review import compute_valuation_review, review_to_payload
from iis.llm.factory import get_llm_client
from iis.models.artifacts import AgentArtifact, AssumptionProposal, SourceRef, ValuationResult
from iis.models.enums import EventType
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import get_latest_actual


class MarginOfSafetyAgent(BaseAgent):
    agent_id = "margin_of_safety"

    DERIVED_FROM_FINANCIALS = frozenset(
        {
            "ebitda_margin",
            "revenue_growth_y1_5",
            "tax_rate",
            "capex_pct_revenue",
            "nwc_pct_revenue",
            "wc_investment_rate",
        }
    )

    def __init__(self, llm=None) -> None:
        self.llm = llm or get_llm_client(finance=True)

    def _build_drivers(self, session: ResearchSession, effective: dict) -> FCFDrivers:
        base = extract_fcf_drivers(session.financial_line_items)
        return FCFDrivers(
            tax_rate=float(effective.get("tax_rate", base.tax_rate)),
            ebit_margin=base.ebit_margin,
            da_pct_revenue=base.da_pct_revenue,
            capex_pct_revenue=float(effective.get("capex_pct_revenue", base.capex_pct_revenue)),
            nwc_pct_revenue=float(effective.get("nwc_pct_revenue", base.nwc_pct_revenue)),
            wc_investment_rate=float(effective.get("wc_investment_rate", base.wc_investment_rate)),
            rationale=base.rationale,
        )

    def run(self, session: ResearchSession) -> ResearchSession:
        revenue = get_latest_actual(session.financial_line_items, "total_revenue")
        ebitda = get_latest_actual(session.financial_line_items, "ebitda")
        if revenue and ebitda and ebitda.period_end != revenue.period_end:
            same = next(
                (
                    i
                    for i in session.financial_line_items
                    if i.line_item == "ebitda" and i.period_end == revenue.period_end
                ),
                None,
            )
            if same:
                ebitda = same
        metrics = get_key_metrics_summary(session.financial_line_items)
        drivers = extract_fcf_drivers(session.financial_line_items)

        estimates_summary = next(
            (a.payload for a in session.agent_artifacts if a.artifact_type == "estimates_summary"),
            {},
        )
        target_price = estimates_summary.get("target_price_mean")

        market_quote = session.market_quote
        if settings.fetch_live_prices:
            fresh = fetch_live_price(session.company, estimates_summary, llm=self.llm, force_refresh=True)
            if fresh:
                market_quote = fresh
                session.market_quote = fresh
                session.add_event(
                    EventType.MARKET_PRICE_FETCH,
                    agent=self.agent_id,
                    details={
                        "price": market_quote.price,
                        "exchange": market_quote.exchange,
                        "source": market_quote.source,
                        "confidence": market_quote.confidence,
                    },
                )
        if not is_reliable_quote(market_quote):
            market_quote = None
            session.market_quote = None

        live_price = market_quote.price if market_quote else None
        market_price_source = market_quote.source if market_quote else None
        market_price_fetched_at = market_quote.fetched_at if market_quote else None

        hist_cagr = compute_revenue_cagr(session.financial_line_items, years=3)
        hist_growth = hist_cagr if hist_cagr is not None else 0.12
        proposed_growth = round(min(max(hist_growth, 0.05), 0.25), 4)
        ebitda_margin = clamp_dcf_margin(metrics.get("ebitda_margin"))
        ss_fcf = steady_state_fcf_pct_of_ebitda(0.06, ebitda_margin, drivers)

        assumptions = [
            AssumptionProposal(
                key="discount_rate",
                label="Discount Rate (WACC)",
                value=0.12,
                unit="decimal",
                rationale="India equity WACC proxy: ~12% for large-cap consumer/industrials",
                source_refs=[SourceRef(file="system", sheet="default_assumptions")],
            ),
            AssumptionProposal(
                key="terminal_growth",
                label="Terminal Growth Rate",
                value=0.06,
                unit="decimal",
                rationale="Nominal India GDP long-term growth proxy, capped below discount rate",
                source_refs=[SourceRef(file="system", sheet="default_assumptions")],
            ),
            AssumptionProposal(
                key="revenue_growth_y1_5",
                label="5Y Revenue CAGR (fades to terminal)",
                value=proposed_growth,
                unit="decimal",
                rationale=(
                    (f"3Y hist CAGR {hist_cagr:.1%}" if hist_cagr is not None else "No revenue history — 12% placeholder")
                    + (f", bounded to {proposed_growth:.1%} (model range 5–25%)" if hist_cagr is not None and proposed_growth != round(hist_cagr, 4) else "")
                    + " — fades toward terminal by Y5; see valuation review to use CIQ consensus instead"
                ),
                source_refs=[SourceRef(file="financials", sheet="Income Statement")],
            ),
            AssumptionProposal(
                key="ebitda_margin",
                label="Steady-State EBITDA Margin",
                value=round(ebitda_margin, 4),
                unit="decimal",
                rationale=f"Latest actual EBITDA margin: {ebitda_margin:.1%}",
                source_refs=[SourceRef(file="financials", sheet="Income Statement")],
            ),
            AssumptionProposal(
                key="tax_rate",
                label="Effective Tax Rate",
                value=drivers.tax_rate,
                unit="decimal",
                rationale="Median effective tax rate (income tax ÷ pre-tax profit) from reported financials",
                source_refs=[SourceRef(file="financials", sheet="Income Statement")],
            ),
            AssumptionProposal(
                key="capex_pct_revenue",
                label="Capex / Revenue",
                value=drivers.capex_pct_revenue,
                unit="decimal",
                rationale=(
                    f"Steady-state capex {drivers.capex_pct_revenue:.1%} of sales (≥ D&A {drivers.da_pct_revenue:.1%}); "
                    + (f"historical median {drivers.capex_hist_median:.1%} includes expansion capex — set your view in chat"
                       if drivers.capex_hist_median is not None else "no capex history on file")
                ),
                source_refs=[SourceRef(file="financials", sheet="Cash Flow")],
            ),
            AssumptionProposal(
                key="nwc_pct_revenue",
                label="Net Working Capital / Revenue",
                value=drivers.nwc_pct_revenue,
                unit="decimal",
                rationale="Inventory + receivables − payables as % of revenue (balance sheet)",
                source_refs=[SourceRef(file="financials", sheet="Balance Sheet")],
            ),
            AssumptionProposal(
                key="wc_investment_rate",
                label="WC Investment / ΔRevenue (growth yrs)",
                value=drivers.wc_investment_rate,
                unit="decimal",
                rationale=(
                    "ΔNWC ÷ ΔRevenue in growth years — high for inventory build; "
                    f"falls toward NWC% once growth moderates (steady-state FCF ≈ {ss_fcf:.0%} of EBITDA)"
                ),
                source_refs=[SourceRef(file="financials", sheet="Cash Flow")],
            ),
        ]

        existing = {a.key: a for a in session.assumptions}
        for a in assumptions:
            if a.key not in existing:
                continue
            prev = existing[a.key]
            a.approved = prev.approved
            if a.key in self.DERIVED_FROM_FINANCIALS:
                if prev.user_edited and prev.user_value is not None:
                    a.user_value = prev.user_value
                    a.user_edited = True
                else:
                    a.user_value = None
                    a.user_edited = False
            else:
                a.user_value = prev.user_value

        session.assumptions = assumptions
        for a in assumptions:
            session.add_event(
                EventType.ASSUMPTION_PROPOSED,
                agent=self.agent_id,
                details={"key": a.key, "value": a.user_value or a.value},
            )

        shares = get_shares_outstanding_mn(session.financial_line_items)
        net_debt = get_net_debt_mn(session.financial_line_items)
        model_assumptions = {a.key: a.user_value or a.value for a in assumptions}
        effective = dict(model_assumptions)
        effective["ebitda_margin"] = clamp_dcf_margin(float(effective["ebitda_margin"]))
        effective["revenue_growth_y1_5"] = round(min(max(float(effective["revenue_growth_y1_5"]), 0.05), 0.25), 4)

        intrinsic = None
        mos = None
        fcf_projection: list = []
        enterprise_value = None
        # MoS only against a real market price; a broker target price is not a market price.
        market_for_mos = live_price
        fcf_drivers = self._build_drivers(session, effective)

        if revenue and revenue.value and shares:
            intrinsic, fcf_projection = dcf_equity_from_fcf_build(
                revenue.value,
                discount_rate=float(effective["discount_rate"]),
                terminal_growth=float(effective["terminal_growth"]),
                revenue_growth_y1_5=float(effective["revenue_growth_y1_5"]),
                ebitda_margin=float(effective["ebitda_margin"]),
                drivers=fcf_drivers,
                shares_mn=shares,
                net_debt_mn=net_debt or 0,
            )
            if fcf_projection:
                enterprise_value = sum(r["ufcf_mn"] for r in fcf_projection)  # rough; EV from DCF below
                last = fcf_projection[-1]["ufcf_mn"]
                wacc = float(effective["discount_rate"])
                tg = float(effective["terminal_growth"])
                terminal = last * (1 + tg) / (wacc - tg)
                pv = sum(r["ufcf_mn"] / (1 + wacc) ** r["year"] for r in fcf_projection)
                enterprise_value = pv + terminal / (1 + wacc) ** 5
            if market_for_mos and intrinsic:
                mos = (intrinsic - market_for_mos) / market_for_mos if market_for_mos else None

        rev_val = revenue.value if revenue and revenue.value else 0
        fcf_note = None
        if fcf_projection and any(r["ufcf_mn"] < 0 for r in fcf_projection[:2]):
            fcf_note = (
                "Y1–Y2 UFCF can be negative when revenue growth requires large WC build "
                f"({fcf_drivers.wc_investment_rate:.0%} of ΔRevenue). "
                "Review WC investment in Gate 4 — spike years are trimmed from history when computing defaults."
            )
        sens_payload: dict = {
            "fcf_projection": fcf_projection,
            "fcf_build_rationale": fcf_drivers.rationale,
            "fcf_note": fcf_note,
        }

        if shares and rev_val:
            tg_assump = {
                **model_assumptions,
                "fcf_conversion": ss_fcf,
            }
            sens_payload["terminal_growth_scenarios"] = terminal_growth_scenarios(
                rev_val,
                tg_assump,
                shares,
                net_debt or 0,
            )
            sens_payload["assumption_bumps"] = compute_fcf_build_sensitivity(
                rev_val,
                model_assumptions,
                fcf_drivers,
                shares,
                net_debt or 0,
                market_price=live_price,
            )

        if live_price and shares and rev_val:
            sens_payload["inverse_dcf"] = inverse_dcf_implied_growth_fcf_build(
                live_price,
                rev_val,
                model_assumptions,
                fcf_drivers,
                shares,
                net_debt or 0,
            )

        review_items = compute_valuation_review(
            session,
            assumptions=assumptions,
            intrinsic=intrinsic,
            market_price=live_price,
        )
        review_payload = review_to_payload(review_items)
        sens_payload["valuation_review"] = review_payload
        session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "valuation_review"]
        session.agent_artifacts.append(
            AgentArtifact(
                agent_id=self.agent_id,
                artifact_type="valuation_review",
                payload=review_payload,
                confidence=1.0,
                provenance=["financials", "dcf"],
            )
        )

        session.valuation = ValuationResult(
            intrinsic_value_per_share=intrinsic,
            current_price=float(target_price) if target_price else None,
            market_price=live_price,
            market_price_source=market_price_source,
            market_price_fetched_at=market_price_fetched_at,
            margin_of_safety_pct=mos,
            method="dcf_fcf_build",
            assumptions={
                **model_assumptions,
                "steady_state_fcf_to_ebitda": round(ss_fcf, 4),
            },
            sensitivities=sens_payload,
        )
        if enterprise_value is not None:
            session.valuation.assumptions["enterprise_value_mn"] = round(enterprise_value, 1)
            session.valuation.assumptions["shares_outstanding_mn"] = shares
            session.valuation.assumptions["net_debt_mn"] = net_debt
        if live_price:
            session.valuation.assumptions["live_market_price"] = live_price
            session.valuation.assumptions["mos_vs_live_price"] = mos

        session.add_event(EventType.VALUATION_RUN, agent=self.agent_id)
        return session
