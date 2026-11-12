"""Fact store — every number Python collected, with an ID, source and reliability tier.

Tiers: 1 = primary data (CIQ Financials/Estimates, price feed)
       2 = broker reports (parsed tables, stated TP / rating)
       3 = derived in Python (ratios, checklists, model outputs) from tier-1 inputs
IDs are stable within one build (F1, F2, …) so the LLM can cite them and the verifier can check.
"""
from __future__ import annotations

from typing import Any

from iis.models.session import ResearchSession

_CIQ_LINES = [
    ("total_revenue", "income_statement", "Revenue", "INR mn"),
    ("ebitda", "income_statement", "EBITDA", "INR mn"),
    ("ebit", "income_statement", "EBIT", "INR mn"),
    ("net_income", "income_statement", "Net income (PAT)", "INR mn"),
    ("diluted_eps_excl_extra_items", "income_statement", "Diluted EPS", "INR"),
    ("gross_profit", "income_statement", "Gross profit", "INR mn"),
    ("cash_from_ops", "cash_flow", "Cash from operations", "INR mn"),
    ("capital_expenditure", "cash_flow", "Capex", "INR mn"),
    ("total_debt", "balance_sheet", "Total debt", "INR mn"),
    ("total_cash_st_investments", "balance_sheet", "Cash & ST investments", "INR mn"),
    ("total_equity", "balance_sheet", "Total equity", "INR mn"),
    ("total_assets", "balance_sheet", "Total assets", "INR mn"),
    ("inventory", "balance_sheet", "Inventory", "INR mn"),
    ("accounts_receivable", "balance_sheet", "Receivables", "INR mn"),
]


def build_fact_store(session: ResearchSession) -> list[dict[str, Any]]:
    from iis.finance.reconciler import annual_series

    facts: list[dict[str, Any]] = []

    def add(label: str, value: Any, unit: str, period: str | None, source: str, tier: int) -> None:
        if value is None:
            return
        facts.append({"id": f"F{len(facts) + 1}", "label": label, "value": value, "unit": unit,
                      "period": period, "source": source, "tier": tier})

    items = session.financial_line_items
    for li, st, label, unit in _CIQ_LINES:
        for fy, v in annual_series(items, li, st).items():
            add(label, round(v, 2), unit, f"FY{fy}", f"CIQ Financials · {st.replace('_', ' ')}", 1)

    for cm in session.consensus_metrics:
        if cm.metric not in ("Revenue", "EBITDA", "Net Income (GAAP)", "Net Income (Excl. Excep)", "EPS Normalized"):
            continue
        unit = "INR" if "EPS" in cm.metric else "INR mn"
        add(f"CIQ consensus mean {cm.metric}", round(cm.mean, 2), unit, cm.period, "CIQ Estimates · Consensus", 1)
        if cm.high is not None:
            add(f"CIQ consensus high {cm.metric}", round(cm.high, 2), unit, cm.period, "CIQ Estimates · Consensus", 1)
        if cm.low is not None:
            add(f"CIQ consensus low {cm.metric}", round(cm.low, 2), unit, cm.period, "CIQ Estimates · Consensus", 1)

    est = next((a.payload for a in session.agent_artifacts if a.artifact_type == "estimates_summary"), {})
    add("CIQ consensus target price (mean)", est.get("target_price_mean"), "INR", None, "CIQ Estimates · Consensus", 1)
    add("CIQ consensus target price (median)", est.get("target_price_median"), "INR", None, "CIQ Estimates · Consensus", 1)

    from iis.finance.market_data import is_reliable_quote

    q = session.market_quote
    if is_reliable_quote(q):
        add("Market price", q.price, "INR", q.fetched_at.date().isoformat(), q.source, 1)

    try:
        from iis.finance.ratios import compute_all_ratios

        allr = compute_all_ratios(items)
        for period, ratios in list(allr.get("ratios_by_period", {}).items())[:5]:
            for k, v in ratios.items():
                if v is not None:
                    add(f"Ratio {k}", round(v, 4), "ratio", str(period)[:10], "Python · computed from CIQ", 3)
    except Exception:
        pass

    v = session.valuation
    if v:
        add("DCF fair value per share (model)", v.intrinsic_value_per_share, "INR", None, "Python DCF model", 3)
        add("Margin of safety vs market (model)", v.margin_of_safety_pct, "ratio", None, "Python DCF model", 3)
        for k, val in (v.assumptions or {}).items():
            if isinstance(val, (int, float)):
                add(f"DCF assumption {k}", val, "", None, "Python DCF model", 3)

    bv = next((a.payload for a in session.agent_artifacts if a.artifact_type == "brokerage_view"), None)
    if bv:
        for r in bv.get("matrix") or []:
            for key, c in (r.get("cells") or {}).items():
                metric, fy = key.split("_FY")
                add(f"{r['broker']} estimate {metric}", c["value"], "INR" if metric == "eps" else "INR mn",
                    f"FY{fy}", f"Broker report {r['file']} ({r.get('report_date') or 'date n/a'})", 2)
            if r.get("target_price"):
                add(f"{r['broker']} target price", r["target_price"], "INR", r.get("report_date"),
                    f"Broker report {r['file']}", 2)

    crit = next((a.payload for a in session.agent_artifacts if a.artifact_type == "critique"), None)
    if crit:
        for block in (crit.get("checklists") or {}).values():
            for it in block.get("items") or []:
                if it.get("value") and it["verdict"] not in ("n/a", "review"):
                    add(f"Checklist: {it['item']} ({it['verdict']})", it["value"], "", None, "Python checklist", 3)
    return facts


def fact_sheet_text(facts: list[dict[str, Any]], *, limit_chars: int = 3500, tiers: tuple[int, ...] = (1, 2, 3)) -> str:
    lines = ["=== FACT SHEET (cite as [F#]) ==="]
    for f in facts:
        if f["tier"] not in tiers:
            continue
        lines.append(f"[{f['id']}] {f['label']} {f.get('period') or ''} = {f['value']} {f['unit']} ({f['source']})")
        if sum(len(x) + 1 for x in lines) > limit_chars:
            lines.append("[… more facts via get_facts tool]")
            break
    return "\n".join(lines)
