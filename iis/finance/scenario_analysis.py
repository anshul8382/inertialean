"""Bear / base / bull valuation scenarios from session DCF assumptions.

Figures are model outputs — not a recommendation. Missing inputs → empty result.
"""
from __future__ import annotations

from typing import Any

from iis.finance.dcf_sanity import clamp_dcf_margin
from iis.finance.fcf_build import FCFDrivers, dcf_equity_from_fcf_build, extract_fcf_drivers
from iis.finance.metrics import get_net_debt_mn, get_shares_outstanding_mn
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import get_latest_actual


def _assumption_map(session: ResearchSession) -> dict[str, float]:
    out: dict[str, float] = {}
    if session.valuation and session.valuation.assumptions:
        for k, v in session.valuation.assumptions.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
    for a in session.assumptions:
        val = a.user_value if a.user_value is not None else a.value
        try:
            out[a.key] = float(val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return out


def _build_drivers(session: ResearchSession, effective: dict[str, float]) -> FCFDrivers:
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


def _ann_return(fair: float | None, price: float | None, years: float = 3.0) -> float | None:
    """Illustrative annualised return if price converges to fair value over `years`."""
    if fair is None or price is None or price <= 0 or years <= 0:
        return None
    try:
        return round((fair / price) ** (1 / years) - 1, 4)
    except Exception:
        return None


def build_scenarios(
    session: ResearchSession,
    *,
    horizon_years: float = 3.0,
    market_price: float | None = None,
) -> dict[str, Any]:
    """
    Return bear/base/bull rows from approved (or proposed) DCF assumptions.

    Bear: growth −3pp (floor 2%), margin −1.5pp, WACC +1pp
    Base: session assumptions
    Bull: growth +3pp (cap 30%), margin +1.5pp, WACC −0.5pp (floor terminal+1pp)
    """
    revenue = get_latest_actual(session.financial_line_items, "total_revenue")
    shares = get_shares_outstanding_mn(session.financial_line_items)
    net_debt = get_net_debt_mn(session.financial_line_items) or 0.0
    base_map = _assumption_map(session)

    empty: dict[str, Any] = {
        "ok": False,
        "horizon_years": horizon_years,
        "market_price": market_price,
        "scenarios": [],
        "note": "Not enough inputs for scenario DCF (need revenue, shares, and growth/margin/WACC assumptions).",
    }
    if not revenue or not revenue.value or not shares:
        return empty
    required = ("discount_rate", "terminal_growth", "revenue_growth_y1_5", "ebitda_margin")
    if any(k not in base_map for k in required):
        return empty

    price = market_price
    # valuation.current_price holds the consensus *target*, never a market price.
    if price is None and session.valuation:
        price = session.valuation.market_price
    if price is None and session.market_quote:
        price = session.market_quote.price

    def run_case(name: str, adj: dict[str, float], label: str) -> dict[str, Any]:
        eff = dict(base_map)
        eff.update(adj)
        wacc = float(eff["discount_rate"])
        tg = float(eff["terminal_growth"])
        if wacc <= tg:
            wacc = tg + 0.02
            eff["discount_rate"] = wacc
        growth = round(min(max(float(eff["revenue_growth_y1_5"]), 0.02), 0.30), 4)
        margin = clamp_dcf_margin(float(eff["ebitda_margin"]))
        eff["revenue_growth_y1_5"] = growth
        eff["ebitda_margin"] = margin
        drivers = _build_drivers(session, eff)
        iv, stream = dcf_equity_from_fcf_build(
            float(revenue.value),
            discount_rate=wacc,
            terminal_growth=tg,
            revenue_growth_y1_5=growth,
            ebitda_margin=margin,
            drivers=drivers,
            shares_mn=shares,
            net_debt_mn=float(net_debt),
        )
        upside = None
        if iv is not None and price:
            upside = round((iv - price) / price, 4)
        return {
            "name": name,
            "label": label,
            "revenue_growth": growth,
            "ebitda_margin": margin,
            "discount_rate": wacc,
            "terminal_growth": tg,
            "fair_value": iv,
            "upside_vs_price": upside,
            "ann_return": _ann_return(iv, price, horizon_years),
            "fcf_years": len(stream),
        }

    wacc0 = float(base_map["discount_rate"])
    growth0 = float(base_map["revenue_growth_y1_5"])
    margin0 = float(base_map["ebitda_margin"])

    scenarios = [
        run_case(
            "bear",
            {
                "revenue_growth_y1_5": growth0 - 0.03,
                "ebitda_margin": margin0 - 0.015,
                "discount_rate": wacc0 + 0.01,
            },
            "Bear — slower growth, thinner margins, higher WACC",
        ),
        run_case("base", {}, "Base — session DCF assumptions"),
        run_case(
            "bull",
            {
                "revenue_growth_y1_5": growth0 + 0.03,
                "ebitda_margin": margin0 + 0.015,
                "discount_rate": max(wacc0 - 0.005, float(base_map["terminal_growth"]) + 0.015),
            },
            "Bull — stronger growth/margins, slightly lower WACC",
        ),
    ]

    return {
        "ok": True,
        "horizon_years": horizon_years,
        "market_price": price,
        "base_revenue_mn": float(revenue.value),
        "shares_mn": shares,
        "scenarios": scenarios,
        "note": (
            "Model outputs from session assumptions (± growth/margin/WACC). "
            "Not a recommendation. Replace with company-specific scenarios when ready."
        ),
    }


def scenarios_to_markdown(payload: dict[str, Any]) -> list[str]:
    lines = [
        "### Scenario analysis (illustrative)",
        "",
        f"_{payload.get('note') or ''}_",
        "",
    ]
    if not payload.get("ok"):
        lines.append("_Not in uploaded sources / model inputs insufficient._")
        lines.append("")
        return lines

    price = payload.get("market_price")
    years = payload.get("horizon_years") or 3
    if price:
        lines.append(f"Reference price for upside/return: **₹{price:,.2f}** · horizon **{years:g} years**.")
        lines.append("")

    lines.append(
        "| Scenario | Rev growth | EBITDA margin | WACC | Fair value ₹ | Upside | Ann. return |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for s in payload.get("scenarios") or []:
        g = s.get("revenue_growth")
        m = s.get("ebitda_margin")
        w = s.get("discount_rate")
        fv = s.get("fair_value")
        up = s.get("upside_vs_price")
        ar = s.get("ann_return")
        lines.append(
            "| {name} | {g} | {m} | {w} | {fv} | {up} | {ar} |".format(
                name=str(s.get("name") or "").title(),
                g=f"{g:.1%}" if g is not None else "—",
                m=f"{m:.1%}" if m is not None else "—",
                w=f"{w:.1%}" if w is not None else "—",
                fv=f"{fv:,.0f}" if fv is not None else "—",
                up=f"{up:.1%}" if up is not None else "—",
                ar=f"{ar:.1%}" if ar is not None else "—",
            )
        )
    lines.append("")
    return lines
