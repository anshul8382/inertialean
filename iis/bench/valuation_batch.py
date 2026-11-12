"""Batch valuation benchmark across many companies (Yahoo Finance + DCF review)."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from iis.agents.margin_of_safety import MarginOfSafetyAgent
from iis.config import settings
from iis.finance.valuation_review import compute_valuation_review, review_to_payload
from iis.finance.yahoo_financials import fetch_yahoo_financials
from iis.llm.ollama_client import OllamaClient
from iis.models.session import ResearchSession

# Nifty 50 + liquid large-caps for India DCF training set
BENCHMARK_TICKERS: list[str] = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "ITC", "SBIN",
    "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "TITAN",
    "SUNPHARMA", "BAJFINANCE", "WIPRO", "ULTRACEMCO", "NESTLEIND", "HCLTECH", "TECHM",
    "POWERGRID", "NTPC", "ONGC", "M&M", "ADANIENT", "ADANIPORTS", "TATASTEEL",
    "INDUSINDBK", "JSWSTEEL", "BAJAJFINSV", "GRASIM", "COALINDIA", "DIVISLAB",
    "DRREDDY", "CIPLA", "APOLLOHOSP", "EICHERMOT", "HEROMOTOCO", "BRITANNIA",
    "HDFCLIFE", "SBILIFE", "BPCL", "TATACONSUM", "UPL", "HINDALCO", "BEL",
    "TRENT", "PIDILITE", "SHREECEM", "AMBUJACEM", "DLF", "GODREJCP", "DABUR",
    "COLPAL", "MARICO", "SIEMENS", "HAVELLS",
]


@dataclass
class CompanyBenchResult:
    ticker: str
    name: str | None = None
    sector: str | None = None
    status: str = "ok"  # ok | skipped | error
    error: str | None = None
    intrinsic_per_share: float | None = None
    market_price: float | None = None
    iv_market_gap_pct: float | None = None
    margin_of_safety_pct: float | None = None
    needs_input_count: int = 0
    flag_ids: list[str] = field(default_factory=list)
    flag_headlines: list[str] = field(default_factory=list)
    assumptions: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def _gap_pct(intrinsic: float | None, market: float | None) -> float | None:
    if not intrinsic or not market or market <= 0:
        return None
    return abs(intrinsic - market) / market


def run_single_company(ticker: str, agent: MarginOfSafetyAgent | None = None) -> CompanyBenchResult:
    agent = agent or MarginOfSafetyAgent()
    result = CompanyBenchResult(ticker=ticker.upper())
    try:
        company, items, meta = fetch_yahoo_financials(ticker)
        result.meta = meta
        result.name = company.name if company else ticker
        result.sector = meta.get("sector")

        if not meta.get("has_revenue") or not meta.get("has_ebitda"):
            result.status = "skipped"
            result.error = "Insufficient Yahoo financials (revenue/EBITDA missing)"
            return result
        if len(items) < 8:
            result.status = "skipped"
            result.error = f"Too few line items ({len(items)})"
            return result

        session = ResearchSession()
        session.company = company
        session.financial_line_items = items
        session.init_gates()
        session = agent.run(session)

        v = session.valuation
        review_art = next((a for a in session.agent_artifacts if a.artifact_type == "valuation_review"), None)
        review = review_art.payload if review_art else {}
        if not review and v and v.sensitivities:
            review = v.sensitivities.get("valuation_review") or {}

        result.intrinsic_per_share = v.intrinsic_value_per_share if v else None
        result.market_price = v.market_price if v else None
        result.margin_of_safety_pct = v.margin_of_safety_pct if v else None
        result.iv_market_gap_pct = _gap_pct(result.intrinsic_per_share, result.market_price)
        result.needs_input_count = review.get("needs_input_count", 0)
        for item in review.get("items", []):
            result.flag_ids.append(item.get("id", ""))
            if item.get("headline"):
                result.flag_headlines.append(item["headline"])
        if v and v.assumptions:
            for k in (
                "discount_rate", "terminal_growth", "revenue_growth_y1_5", "ebitda_margin",
                "wc_investment_rate", "nwc_pct_revenue", "capex_pct_revenue",
            ):
                if k in v.assumptions:
                    result.assumptions[k] = v.assumptions[k]
        if result.intrinsic_per_share is None:
            result.status = "skipped"
            result.error = "DCF could not compute intrinsic value"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc)[:200]
    return result


def aggregate_results(results: list[CompanyBenchResult]) -> dict[str, Any]:
    ok = [r for r in results if r.status == "ok"]
    flag_counter = Counter(fid for r in ok for fid in r.flag_ids if fid)
    gaps = [r.iv_market_gap_pct for r in ok if r.iv_market_gap_pct is not None]
    needs = [r.needs_input_count for r in ok]
    large_gaps = sorted(
        [r for r in ok if r.iv_market_gap_pct and r.iv_market_gap_pct > 0.5],
        key=lambda x: x.iv_market_gap_pct or 0,
        reverse=True,
    )[:15]
    return {
        "total": len(results),
        "ok": len(ok),
        "skipped": sum(1 for r in results if r.status == "skipped"),
        "errors": sum(1 for r in results if r.status == "error"),
        "flag_frequency": dict(flag_counter.most_common()),
        "avg_iv_market_gap_pct": sum(gaps) / len(gaps) if gaps else None,
        "median_iv_market_gap_pct": sorted(gaps)[len(gaps) // 2] if gaps else None,
        "companies_needing_input": sum(1 for r in ok if r.needs_input_count > 0),
        "avg_needs_input_count": sum(needs) / len(needs) if needs else 0,
        "large_gap_examples": [
            {
                "ticker": r.ticker,
                "name": r.name,
                "sector": r.sector,
                "iv": r.intrinsic_per_share,
                "market": r.market_price,
                "gap_pct": r.iv_market_gap_pct,
                "flags": r.flag_ids,
            }
            for r in large_gaps
        ],
    }


def ollama_synthesize_adjustments(summary: dict[str, Any], llm: OllamaClient | None = None) -> dict[str, Any]:
    llm = llm or OllamaClient()
    if not llm.is_available():
        return {"available": False, "note": "Ollama not running — skipped LLM synthesis"}

    prompt = f"""You are reviewing a batch DCF valuation benchmark on {summary['total']} Indian listed companies.
Data source: Yahoo Finance annual statements. The system flags assumption issues and co-creates with analysts instead of hard-coding fixes.

Aggregate results:
{json.dumps(summary, indent=2, default=str)[:12000]}

Return JSON with:
{{
  "executive_summary": "2-3 sentences",
  "system_adjustments": [
    {{"priority": "high|medium|low", "area": "...", "issue": "...", "proposed_change": "...", "user_prompt_template": "question to ask analyst"}}
  ],
  "flag_patterns": ["pattern observed across companies"],
  "data_quality_gaps": ["yahoo vs capital iq gaps"],
  "recommended_next_steps": ["action for product team"]
}}
Focus on generic improvements (WC/FCF flags, sector defaults, data mapping) NOT company-specific fixes."""

    try:
        model = settings.ollama_finance_model or settings.ollama_model
        client = OllamaClient(model=model)
        out = client.extract_json(prompt, system="Respond only with valid JSON.")
        out["available"] = True
        out["model"] = model
        return out
    except Exception as exc:
        return {"available": True, "error": str(exc)[:300]}


def run_batch(
    tickers: list[str] | None = None,
    *,
    use_llm: bool = True,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    tickers = tickers or BENCHMARK_TICKERS
    agent = MarginOfSafetyAgent()
    results: list[CompanyBenchResult] = []
    for i, t in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {t}...", flush=True)
        results.append(run_single_company(t, agent))

    summary = aggregate_results(results)
    llm_out: dict[str, Any] = {}
    if use_llm:
        print("Synthesizing adjustments with Ollama...", flush=True)
        llm_out = ollama_synthesize_adjustments(summary)

    payload = {
        "run_at": datetime.utcnow().isoformat(),
        "ticker_count": len(tickers),
        "summary": summary,
        "llm_recommendations": llm_out,
        "results": [asdict(r) for r in results],
    }

    out_dir = output_dir or (settings.data_dir / "bench")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"valuation_batch_{stamp}.json"
    md_path = out_dir / f"valuation_batch_{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["markdown_path"] = str(md_path)
    return payload


def _render_markdown(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        "# Valuation batch benchmark",
        "",
        f"Run: {payload['run_at']} · {payload['ticker_count']} companies · Yahoo Finance",
        "",
        "## Summary",
        f"- **Successful DCF runs:** {s['ok']} / {s['total']}",
        f"- **Skipped:** {s['skipped']} · **Errors:** {s['errors']}",
        f"- **Companies with co-creation flags:** {s['companies_needing_input']}",
        f"- **Avg IV vs market gap:** {s['avg_iv_market_gap_pct']:.0%}" if s.get("avg_iv_market_gap_pct") else "- **Avg IV vs market gap:** N/A",
        "",
        "### Flag frequency",
    ]
    for fid, cnt in (s.get("flag_frequency") or {}).items():
        lines.append(f"- `{fid}`: {cnt} companies")

    lines.extend(["", "### Largest IV vs market gaps", ""])
    for ex in s.get("large_gap_examples") or []:
        gap = ex.get("gap_pct")
        gap_s = f"{gap:.0%}" if gap is not None else "N/A"
        lines.append(
            f"- **{ex['ticker']}** ({ex.get('sector', '?')}): IV ₹{ex.get('iv', 0):,.0f} vs market ₹{ex.get('market', 0):,.0f} ({gap_s}) — flags: {', '.join(ex.get('flags') or [])}"
        )

    llm = payload.get("llm_recommendations") or {}
    if llm.get("executive_summary"):
        lines.extend(["", "## Ollama recommendations", "", llm["executive_summary"], ""])
    for adj in llm.get("system_adjustments") or []:
        lines.append(f"### [{adj.get('priority', '?').upper()}] {adj.get('area', 'Adjustment')}")
        lines.append(f"- **Issue:** {adj.get('issue', '')}")
        lines.append(f"- **Proposed change:** {adj.get('proposed_change', '')}")
        if adj.get("user_prompt_template"):
            lines.append(f"- **Ask user:** _{adj['user_prompt_template']}_")
        lines.append("")

    lines.extend(["", "## Per-company results", ""])
    lines.append("| Ticker | Status | IV/sh | Market | Gap | Flags | Needs input |")
    lines.append("|--------|--------|-------|--------|-----|-------|-------------|")
    for r in payload.get("results") or []:
        gap = r.get("iv_market_gap_pct")
        gap_s = f"{gap:.0%}" if gap is not None else "—"
        iv = r.get("intrinsic_per_share")
        mp = r.get("market_price")
        lines.append(
            f"| {r['ticker']} | {r['status']} | {f'₹{iv:,.0f}' if iv else '—'} | {f'₹{mp:,.0f}' if mp else '—'} | {gap_s} | {', '.join(r.get('flag_ids') or []) or '—'} | {r.get('needs_input_count', 0)} |"
        )
    return "\n".join(lines)
