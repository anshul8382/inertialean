"""Optional quality / health scores from Capital IQ (opt-in via checklist profile).

Piotroski-style F-score: nine binary tests on consecutive fiscal years.
Not a substitute for verifiable data flags — enable only when you want a
compact scoreboard in the analysis pack.
"""
from __future__ import annotations

from typing import Any

from iis.finance.ratios import build_period_metrics, compute_period_ratios
from iis.models.artifacts import FinancialLineItem


def _period_pair(periods: dict[str, Any]) -> tuple[str, str] | None:
    keys = sorted(periods.keys(), reverse=True)
    if len(keys) < 2:
        return None
    return keys[0], keys[1]


def compute_piotroski_style(items: list[FinancialLineItem]) -> dict[str, Any] | None:
    """
    Classic Piotroski F-score signals adapted to CIQ fields.

    Signals (1 point each, max 9):
      1 ROA > 0
      2 CFO > 0
      3 CFO > Net Income (accrual quality)
      4 ΔROA > 0
      5 ΔLeverage (Debt/Assets) < 0  [improving]
      6 ΔCurrent ratio > 0 (when reported)
      7 No dilution proxy — skipped unless share count available (score N/A → 0)
      8 ΔGross margin > 0
      9 ΔAsset turnover > 0
    """
    periods = build_period_metrics(items)
    pair = _period_pair(periods)
    if not pair:
        return None
    curr_p, prev_p = pair
    curr_m, prev_m = periods[curr_p], periods[prev_p]
    curr_r = compute_period_ratios(curr_m)
    prev_r = compute_period_ratios(prev_m)

    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool | None, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "passed": passed,
                "points": 1 if passed else 0,
                "detail": detail,
            }
        )

    roa = curr_r.get("roa")
    add("roa_positive", (roa is not None and roa > 0), f"ROA={roa:.1%}" if roa is not None else "ROA n/a")

    cfo = curr_m.get("cash_from_ops")
    add("cfo_positive", (cfo is not None and cfo > 0), f"CFO={cfo:,.0f}" if cfo is not None else "CFO n/a")

    ni = curr_m.get("net_income")
    if cfo is not None and ni is not None:
        add("cfo_gt_ni", cfo > ni, f"CFO {cfo:,.0f} vs NI {ni:,.0f}")
    else:
        add("cfo_gt_ni", None, "CFO or NI missing")

    roa_prev = prev_r.get("roa")
    if roa is not None and roa_prev is not None:
        add("delta_roa", roa > roa_prev, f"ROA {roa_prev:.1%} → {roa:.1%}")
    else:
        add("delta_roa", None, "ROA history missing")

    dta = curr_r.get("debt_to_assets")
    dta_prev = prev_r.get("debt_to_assets")
    if dta is not None and dta_prev is not None:
        add("delta_leverage", dta < dta_prev, f"Debt/Assets {dta_prev:.2f} → {dta:.2f}")
    else:
        add("delta_leverage", None, "Debt/Assets n/a")

    cr = curr_r.get("current_ratio")
    cr_prev = prev_r.get("current_ratio")
    if cr is not None and cr_prev is not None:
        add("delta_current_ratio", cr > cr_prev, f"Current ratio {cr_prev:.2f} → {cr:.2f}")
    else:
        add("delta_current_ratio", None, "Current ratio not in Key Stats")

    # Equity issuance: only if diluted shares available both years
    sh = curr_m.get("shares_outstanding", "diluted_shares", "weighted_average_shares")
    sh_prev = prev_m.get("shares_outstanding", "diluted_shares", "weighted_average_shares")
    if sh is not None and sh_prev is not None and sh_prev > 0:
        add("no_dilution", sh <= sh_prev * 1.01, f"Shares {sh_prev:,.0f} → {sh:,.0f}")
    else:
        add("no_dilution", None, "Share count n/a — scored 0")

    gm = curr_r.get("gross_margin")
    gm_prev = prev_r.get("gross_margin")
    if gm is not None and gm_prev is not None:
        add("delta_gross_margin", gm > gm_prev, f"GM {gm_prev:.1%} → {gm:.1%}")
    else:
        add("delta_gross_margin", None, "Gross margin n/a")

    at = curr_r.get("asset_turnover")
    at_prev = prev_r.get("asset_turnover")
    if at is not None and at_prev is not None:
        add("delta_asset_turnover", at > at_prev, f"AT {at_prev:.2f} → {at:.2f}")
    else:
        add("delta_asset_turnover", None, "Asset turnover n/a")

    # None → 0 points (conservative)
    score = sum(1 for c in checks if c["passed"] is True)
    answered = sum(1 for c in checks if c["passed"] is not None)

    return {
        "type": "piotroski_style",
        "title": "Piotroski-style F-score (CIQ)",
        "period_current": curr_p,
        "period_prior": prev_p,
        "score": score,
        "max_score": 9,
        "signals_answered": answered,
        "checks": checks,
        "note": (
            "Optional composite. Prefer individual data flags for investment decisions. "
            "Missing inputs score 0 for that signal."
        ),
    }


def format_quality_scores(payload: dict[str, Any]) -> str:
    lines = [
        f"**{payload.get('title', 'Quality score')}**: "
        f"**{payload.get('score', 0)}/{payload.get('max_score', 9)}** "
        f"({payload.get('period_prior')} → {payload.get('period_current')})",
        "",
    ]
    for c in payload.get("checks") or []:
        mark = "✓" if c.get("passed") is True else ("✗" if c.get("passed") is False else "·")
        lines.append(f"- {mark} {c.get('name')}: {c.get('detail')}")
    if payload.get("note"):
        lines.append("")
        lines.append(f"_{payload['note']}_")
    return "\n".join(lines)


def compute_optional_quality_pack(items: list[FinancialLineItem]) -> dict[str, Any] | None:
    if not items:
        return None
    return compute_piotroski_style(items)
