"""5Y DCF engine: forward valuation, inverse (implied growth), and assumption sensitivity."""
from __future__ import annotations

from typing import Any


def dcf_equity_value_per_share(
    base_revenue: float,
    *,
    discount_rate: float,
    terminal_growth: float,
    revenue_growth_y1_5: float,
    ebitda_margin: float,
    fcf_conversion: float,
    shares_mn: float,
    net_debt_mn: float = 0,
) -> float | None:
    """Unlevered DCF → equity value per share (INR). All monetary inputs in ₹ mn."""
    if shares_mn <= 0 or base_revenue <= 0 or discount_rate <= terminal_growth:
        return None

    fcf_stream: list[float] = []
    rev = base_revenue
    for _ in range(5):
        rev *= 1 + revenue_growth_y1_5
        fcf_stream.append(rev * ebitda_margin * fcf_conversion)

    terminal = fcf_stream[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv = sum(f / (1 + discount_rate) ** (i + 1) for i, f in enumerate(fcf_stream))
    ev = pv + terminal / (1 + discount_rate) ** 5
    return round((ev - net_debt_mn) / shares_mn, 2)


def dcf_enterprise_value(
    base_revenue: float,
    assumptions: dict[str, float],
    *,
    fcf_conversion_default: float = 0.65,
) -> float | None:
    try:
        wacc = float(assumptions["discount_rate"])
        tg = float(assumptions["terminal_growth"])
        growth = float(assumptions["revenue_growth_y1_5"])
        margin = float(assumptions["ebitda_margin"])
        fcf_conv = float(assumptions.get("fcf_conversion", fcf_conversion_default))
    except (KeyError, ValueError, TypeError):
        return None

    if wacc <= tg or base_revenue <= 0:
        return None

    fcf_stream: list[float] = []
    rev = base_revenue
    for _ in range(5):
        rev *= 1 + growth
        fcf_stream.append(rev * margin * fcf_conv)

    terminal = fcf_stream[-1] * (1 + tg) / (wacc - tg)
    pv = sum(f / (1 + wacc) ** (i + 1) for i, f in enumerate(fcf_stream))
    return pv + terminal / (1 + wacc) ** 5


def _bisect_implied(
    target: float,
    evaluate,
    *,
    low: float,
    high: float,
    tol: float = 0.5,
    max_iter: int = 80,
) -> float | None:
    """Find x in [low, high] where evaluate(x) ≈ target."""
    f_lo = evaluate(low)
    f_hi = evaluate(high)
    if f_lo is None or f_hi is None:
        return None
    if f_lo > f_hi:
        low, high = high, low
        f_lo, f_hi = f_hi, f_lo
    if target <= f_lo:
        return low if abs(f_lo - target) <= tol else None
    if target >= f_hi:
        return high if abs(f_hi - target) <= tol else None

    for _ in range(max_iter):
        mid = (low + high) / 2
        val = evaluate(mid)
        if val is None:
            return None
        if abs(val - target) <= tol:
            return round(mid, 5)
        if val < target:
            low = mid
        else:
            high = mid
    return round((low + high) / 2, 5)


def inverse_dcf_implied_growth(
    market_price: float,
    base_revenue: float,
    assumptions: dict[str, float],
    shares_mn: float,
    net_debt_mn: float = 0,
    *,
    fcf_conversion_default: float = 0.65,
) -> dict[str, Any]:
    """
    What revenue CAGR / terminal growth is embedded in market_price?
    Holds all other inputs fixed; solves each implied variable separately.
    """
    if market_price <= 0 or not shares_mn or base_revenue <= 0:
        return {}

    try:
        wacc = float(assumptions["discount_rate"])
        margin = float(assumptions["ebitda_margin"])
        fcf_conv = float(assumptions.get("fcf_conversion", fcf_conversion_default))
        base_growth = float(assumptions["revenue_growth_y1_5"])
        base_tg = float(assumptions["terminal_growth"])
    except (KeyError, ValueError, TypeError):
        return {}

    base_iv = dcf_equity_value_per_share(
        base_revenue,
        discount_rate=wacc,
        terminal_growth=base_tg,
        revenue_growth_y1_5=base_growth,
        ebitda_margin=margin,
        fcf_conversion=fcf_conv,
        shares_mn=shares_mn,
        net_debt_mn=net_debt_mn,
    )

    def eval_revenue_growth(g: float) -> float | None:
        return dcf_equity_value_per_share(
            base_revenue,
            discount_rate=wacc,
            terminal_growth=base_tg,
            revenue_growth_y1_5=g,
            ebitda_margin=margin,
            fcf_conversion=fcf_conv,
            shares_mn=shares_mn,
            net_debt_mn=net_debt_mn,
        )

    def eval_terminal_growth(tg: float) -> float | None:
        if tg >= wacc - 0.001:
            return None
        return dcf_equity_value_per_share(
            base_revenue,
            discount_rate=wacc,
            terminal_growth=tg,
            revenue_growth_y1_5=base_growth,
            ebitda_margin=margin,
            fcf_conversion=fcf_conv,
            shares_mn=shares_mn,
            net_debt_mn=net_debt_mn,
        )

    implied_revenue_growth = _bisect_implied(
        market_price, eval_revenue_growth, low=-0.05, high=0.80, tol=1.0
    )
    if implied_revenue_growth is None:
        iv_at_cap = eval_revenue_growth(0.80)
        if iv_at_cap is not None and iv_at_cap < market_price:
            pass  # flagged below after result dict is built
        elif iv_at_cap is not None and abs(iv_at_cap - market_price) <= 1.0:
            implied_revenue_growth = 0.80

    implied_terminal_growth = _bisect_implied(
        market_price,
        eval_terminal_growth,
        low=0.0,
        high=wacc - 0.005,
        tol=1.0,
    )

    result: dict[str, Any] = {
        "market_price": round(market_price, 2),
        "model_iv_per_share": base_iv,
        "assumed_revenue_growth_y1_5": base_growth,
        "assumed_terminal_growth": base_tg,
    }
    if implied_revenue_growth is not None:
        result["implied_revenue_growth_y1_5"] = implied_revenue_growth
        result["revenue_growth_gap_pp"] = round((implied_revenue_growth - base_growth) * 100, 1)
    else:
        iv_at_cap = eval_revenue_growth(0.80)
        if iv_at_cap is not None and iv_at_cap < market_price:
            result["implied_revenue_growth_y1_5_min"] = 0.80
            result["revenue_growth_saturated"] = True
    if implied_terminal_growth is not None:
        result["implied_terminal_growth"] = implied_terminal_growth
        result["terminal_growth_gap_pp"] = round((implied_terminal_growth - base_tg) * 100, 1)
    return result


def compute_assumption_sensitivity(
    base_revenue: float,
    assumptions: dict[str, float],
    shares_mn: float,
    net_debt_mn: float = 0,
    *,
    market_price: float | None = None,
    fcf_conversion_default: float = 0.65,
) -> list[dict[str, Any]]:
    """
    Bump key assumptions and show % change in intrinsic value vs base case.
    Example row: 5Y CAGR −2pp → IV −40%.
    """
    if not shares_mn or base_revenue <= 0:
        return []

    try:
        wacc = float(assumptions["discount_rate"])
        tg = float(assumptions["terminal_growth"])
        growth = float(assumptions["revenue_growth_y1_5"])
        margin = float(assumptions["ebitda_margin"])
        fcf_conv = float(assumptions.get("fcf_conversion", fcf_conversion_default))
    except (KeyError, ValueError, TypeError):
        return []

    base_iv = dcf_equity_value_per_share(
        base_revenue,
        discount_rate=wacc,
        terminal_growth=tg,
        revenue_growth_y1_5=growth,
        ebitda_margin=margin,
        fcf_conversion=fcf_conv,
        shares_mn=shares_mn,
        net_debt_mn=net_debt_mn,
    )
    if not base_iv or base_iv <= 0:
        return []

    bumps: list[tuple[str, float, str]] = [
        ("revenue_growth_y1_5", -0.02, "5Y revenue CAGR −2pp"),
        ("revenue_growth_y1_5", -0.01, "5Y revenue CAGR −1pp"),
        ("revenue_growth_y1_5", +0.01, "5Y revenue CAGR +1pp"),
        ("revenue_growth_y1_5", +0.02, "5Y revenue CAGR +2pp"),
        ("terminal_growth", -0.01, "Terminal growth −1pp"),
        ("terminal_growth", +0.01, "Terminal growth +1pp"),
        ("terminal_growth", -0.02, "Terminal growth −2pp"),
        ("ebitda_margin", -0.02, "EBITDA margin −2pp"),
        ("ebitda_margin", -0.01, "EBITDA margin −1pp"),
        ("ebitda_margin", +0.01, "EBITDA margin +1pp"),
        ("discount_rate", +0.01, "WACC +1pp"),
        ("discount_rate", +0.02, "WACC +2pp"),
        ("discount_rate", -0.01, "WACC −1pp"),
    ]

    rows: list[dict[str, Any]] = []
    for key, delta, label in bumps:
        mod = {
            "discount_rate": wacc,
            "terminal_growth": tg,
            "revenue_growth_y1_5": growth,
            "ebitda_margin": margin,
            "fcf_conversion": fcf_conv,
        }
        new_val = mod[key] + delta
        if key == "terminal_growth" and new_val >= wacc - 0.001:
            continue
        if key == "revenue_growth_y1_5" and new_val < -0.10:
            continue
        if key == "ebitda_margin" and new_val <= 0:
            continue
        if key == "discount_rate" and new_val <= 0.03:
            continue
        mod[key] = new_val

        iv = dcf_equity_value_per_share(
            base_revenue,
            discount_rate=mod["discount_rate"],
            terminal_growth=mod["terminal_growth"],
            revenue_growth_y1_5=mod["revenue_growth_y1_5"],
            ebitda_margin=mod["ebitda_margin"],
            fcf_conversion=fcf_conv,
            shares_mn=shares_mn,
            net_debt_mn=net_debt_mn,
        )
        if iv is None:
            continue

        iv_change_pct = (iv - base_iv) / base_iv
        row: dict[str, Any] = {
            "label": label,
            "assumption_key": key,
            "bump": delta,
            "iv_per_share": iv,
            "base_iv_per_share": base_iv,
            "iv_change_pct": round(iv_change_pct, 4),
        }
        if market_price and market_price > 0 and iv > 0:
            row["mos_pct"] = round((iv - market_price) / market_price, 4)
        rows.append(row)

    return rows


def compute_fcf_build_sensitivity(
    base_revenue: float,
    assumptions: dict[str, float],
    drivers: Any,
    shares_mn: float,
    net_debt_mn: float = 0,
    *,
    market_price: float | None = None,
) -> list[dict[str, Any]]:
    """Bump key assumptions using the explicit FCF build-up (same engine as base IV)."""
    from iis.finance.fcf_build import FCFDrivers, dcf_equity_from_fcf_build

    if not shares_mn or base_revenue <= 0:
        return []

    try:
        wacc = float(assumptions["discount_rate"])
        tg = float(assumptions["terminal_growth"])
        growth = float(assumptions["revenue_growth_y1_5"])
        margin = float(assumptions["ebitda_margin"])
    except (KeyError, ValueError, TypeError):
        return []

    def eval_iv(**overrides: float) -> float | None:
        drv = FCFDrivers(
            tax_rate=float(overrides.get("tax_rate", drivers.tax_rate)),
            ebit_margin=drivers.ebit_margin,
            da_pct_revenue=drivers.da_pct_revenue,
            capex_pct_revenue=float(overrides.get("capex_pct_revenue", drivers.capex_pct_revenue)),
            nwc_pct_revenue=float(overrides.get("nwc_pct_revenue", drivers.nwc_pct_revenue)),
            wc_investment_rate=float(overrides.get("wc_investment_rate", drivers.wc_investment_rate)),
            rationale=drivers.rationale,
        )
        iv, _ = dcf_equity_from_fcf_build(
            base_revenue,
            discount_rate=float(overrides.get("discount_rate", wacc)),
            terminal_growth=float(overrides.get("terminal_growth", tg)),
            revenue_growth_y1_5=float(overrides.get("revenue_growth_y1_5", growth)),
            ebitda_margin=float(overrides.get("ebitda_margin", margin)),
            drivers=drv,
            shares_mn=shares_mn,
            net_debt_mn=net_debt_mn,
        )
        return iv

    base_iv = eval_iv()
    if not base_iv or base_iv <= 0:
        return []

    bumps: list[tuple[str, float, str]] = [
        ("revenue_growth_y1_5", -0.02, "5Y revenue CAGR −2pp"),
        ("revenue_growth_y1_5", -0.01, "5Y revenue CAGR −1pp"),
        ("revenue_growth_y1_5", +0.01, "5Y revenue CAGR +1pp"),
        ("revenue_growth_y1_5", +0.02, "5Y revenue CAGR +2pp"),
        ("terminal_growth", -0.01, "Terminal growth −1pp"),
        ("terminal_growth", +0.01, "Terminal growth +1pp"),
        ("terminal_growth", -0.02, "Terminal growth −2pp"),
        ("ebitda_margin", -0.02, "EBITDA margin −2pp"),
        ("ebitda_margin", -0.01, "EBITDA margin −1pp"),
        ("ebitda_margin", +0.01, "EBITDA margin +1pp"),
        ("wc_investment_rate", -0.05, "WC investment −5pp"),
        ("wc_investment_rate", +0.05, "WC investment +5pp"),
        ("discount_rate", +0.01, "WACC +1pp"),
        ("discount_rate", +0.02, "WACC +2pp"),
        ("discount_rate", -0.01, "WACC −1pp"),
    ]

    rows: list[dict[str, Any]] = []
    for key, delta, label in bumps:
        base_vals = {
            "discount_rate": wacc,
            "terminal_growth": tg,
            "revenue_growth_y1_5": growth,
            "ebitda_margin": margin,
            "wc_investment_rate": drivers.wc_investment_rate,
        }
        new_val = base_vals[key] + delta
        if key == "terminal_growth" and new_val >= wacc - 0.001:
            continue
        if key == "revenue_growth_y1_5" and new_val < -0.10:
            continue
        if key == "ebitda_margin" and new_val <= 0:
            continue
        if key == "discount_rate" and new_val <= 0.03:
            continue
        if key == "wc_investment_rate" and not (0.01 <= new_val <= 1.0):
            continue

        iv = eval_iv(**{key: new_val})
        if iv is None:
            continue

        row: dict[str, Any] = {
            "label": label,
            "assumption_key": key,
            "bump": delta,
            "iv_per_share": iv,
            "base_iv_per_share": base_iv,
            "iv_change_pct": round((iv - base_iv) / base_iv, 4),
        }
        if market_price and market_price > 0 and iv > 0:
            row["mos_pct"] = round((iv - market_price) / market_price, 4)
        rows.append(row)

    return rows


def inverse_dcf_implied_growth_fcf_build(
    market_price: float,
    base_revenue: float,
    assumptions: dict[str, float],
    drivers: Any,
    shares_mn: float,
    net_debt_mn: float = 0,
) -> dict[str, Any]:
    """Inverse DCF using the FCF build-up engine (consistent with displayed projection)."""
    from iis.finance.fcf_build import dcf_equity_from_fcf_build

    if market_price <= 0 or not shares_mn or base_revenue <= 0:
        return {}

    try:
        wacc = float(assumptions["discount_rate"])
        margin = float(assumptions["ebitda_margin"])
        base_growth = float(assumptions["revenue_growth_y1_5"])
        base_tg = float(assumptions["terminal_growth"])
    except (KeyError, ValueError, TypeError):
        return {}

    def eval_revenue_growth(g: float) -> float | None:
        iv, _ = dcf_equity_from_fcf_build(
            base_revenue,
            discount_rate=wacc,
            terminal_growth=base_tg,
            revenue_growth_y1_5=g,
            ebitda_margin=margin,
            drivers=drivers,
            shares_mn=shares_mn,
            net_debt_mn=net_debt_mn,
        )
        return iv

    def eval_terminal_growth(tg: float) -> float | None:
        if tg >= wacc - 0.001:
            return None
        iv, _ = dcf_equity_from_fcf_build(
            base_revenue,
            discount_rate=wacc,
            terminal_growth=tg,
            revenue_growth_y1_5=base_growth,
            ebitda_margin=margin,
            drivers=drivers,
            shares_mn=shares_mn,
            net_debt_mn=net_debt_mn,
        )
        return iv

    base_iv = eval_revenue_growth(base_growth)
    implied_revenue_growth = _bisect_implied(
        market_price, eval_revenue_growth, low=-0.05, high=0.80, tol=1.0
    )
    implied_terminal_growth = _bisect_implied(
        market_price, eval_terminal_growth, low=0.0, high=wacc - 0.005, tol=1.0
    )

    result: dict[str, Any] = {
        "market_price": round(market_price, 2),
        "model_iv_per_share": base_iv,
        "assumed_revenue_growth_y1_5": base_growth,
        "assumed_terminal_growth": base_tg,
    }
    if implied_revenue_growth is not None:
        result["implied_revenue_growth_y1_5"] = implied_revenue_growth
        result["revenue_growth_gap_pp"] = round((implied_revenue_growth - base_growth) * 100, 1)
    else:
        iv_at_cap = eval_revenue_growth(0.80)
        if iv_at_cap is not None and iv_at_cap < market_price:
            result["implied_revenue_growth_y1_5_min"] = 0.80
            result["revenue_growth_saturated"] = True
    if implied_terminal_growth is not None:
        result["implied_terminal_growth"] = implied_terminal_growth
        result["terminal_growth_gap_pp"] = round((implied_terminal_growth - base_tg) * 100, 1)
    return result


def terminal_growth_scenarios(
    base_revenue: float,
    assumptions: dict[str, float],
    shares_mn: float,
    net_debt_mn: float = 0,
    *,
    fcf_conversion_default: float = 0.65,
) -> dict[str, float | None]:
    """Legacy: IV at 4–7% terminal growth."""
    if not shares_mn:
        return {}
    try:
        wacc = float(assumptions["discount_rate"])
        growth = float(assumptions["revenue_growth_y1_5"])
        margin = float(assumptions["ebitda_margin"])
        fcf_conv = float(assumptions.get("fcf_conversion", fcf_conversion_default))
    except (KeyError, ValueError, TypeError):
        return {}

    scenarios: dict[str, float | None] = {}
    for tg in (0.04, 0.05, 0.06, 0.07):
        iv = dcf_equity_value_per_share(
            base_revenue,
            discount_rate=wacc,
            terminal_growth=tg,
            revenue_growth_y1_5=growth,
            ebitda_margin=margin,
            fcf_conversion=fcf_conv,
            shares_mn=shares_mn,
            net_debt_mn=net_debt_mn,
        )
        scenarios[f"terminal_growth_{tg:.0%}"] = iv
    return scenarios
