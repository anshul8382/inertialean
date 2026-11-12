"""Full equity research report (Layer 1 brief + Layer 2 body).

Rules:
- Never invent facts. If a section has nothing from uploads/calculations, omit it.
- Do not pad with “analyst to complete” / “you decide” prompts.
- Calculations and model outputs are allowed and labeled.
- Broker/consensus views are street data only (shown when available).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iis.finance.checklist import BUCKET_LABELS, FLAG_CATEGORY_TO_BUCKET, load_checklist_profile
from iis.finance.flags import compute_data_flags, flags_to_payload
from iis.finance.growth import compute_revenue_cagr, get_revenue_series
from iis.finance.metrics import get_key_metrics_summary
from iis.finance.multiples import get_estimates_summary, get_multiples_table
from iis.finance.ratios import build_period_metrics, compute_period_ratios, format_ratio_value, get_latest_ratio_summary
from iis.finance.scenario_analysis import build_scenarios, scenarios_to_markdown
from iis.models.enums import GateId
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import get_latest_actual, query_financials


def _dcf_on() -> bool:
    try:
        from iis.config import settings

        return bool(getattr(settings, "dcf_enabled", False))
    except Exception:
        return False


def build_research_report(session: ResearchSession) -> str:
    """Return one markdown document: Investment Brief then Full Report."""
    company = session.company.name if session.company else "Unknown Company"
    parts: list[str] = [
        f"# Research Report — {company}",
        "",
        f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"Session `{session.id[:8]}`*",
        "",
    ]
    try:
        from iis.finance.data_freshness import freshness_ask_markdown

        fresh_lines = freshness_ask_markdown(session)
        if fresh_lines:
            parts.extend(fresh_lines)
    except Exception:
        pass
    parts.extend(["---", ""])
    parts.extend(_layer1_brief(session))
    parts.extend(["", "---", "", "# Layer 2 — Full research report", ""])
    parts.extend(_layer2_full(session))
    return "\n".join(parts).rstrip() + "\n"


# ── helpers ──────────────────────────────────────────────────────────────

_KEY_CONSENSUS = (
    "Revenue", "EBITDA", "EBIT", "Net Income (Excl. Excep)", "EPS Normalized", "Capital Expenditure", "Cash From Operations",
)


def _artifact(session: ResearchSession, artifact_type: str) -> dict[str, Any]:
    art = next((a for a in session.agent_artifacts if a.artifact_type == artifact_type), None)
    return art.payload if art else {}


def _fmt_mn(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}"


def _fmt_pct(v: float | None, *, already_pct: bool = False) -> str:
    if v is None:
        return "—"
    x = v if already_pct else v * 100
    return f"{x:.1f}%"


def _price_and_date(session: ResearchSession) -> tuple[float | None, str | None, str | None]:
    from iis.finance.market_data import RELIABLE_PRICE_SOURCES, is_reliable_quote

    v = session.valuation
    if v and v.market_price and v.market_price_source in RELIABLE_PRICE_SOURCES:
        dt = v.market_price_fetched_at.strftime("%Y-%m-%d") if v.market_price_fetched_at else None
        return float(v.market_price), dt, v.market_price_source
    if is_reliable_quote(session.market_quote):
        q = session.market_quote
        return float(q.price), q.fetched_at.strftime("%Y-%m-%d"), q.source
    return None, None, None


def _flags_payload(session: ResearchSession) -> list[dict[str, Any]]:
    art = _artifact(session, "data_flags")
    flags = art.get("flags") if art else None
    if flags:
        return list(flags)
    return flags_to_payload(compute_data_flags(session)).get("flags") or []


def _broker_street_views(session: ResearchSession) -> list[dict[str, Any]]:
    """Broker ratings from Telegram report index (street views only)."""
    name = session.company.name if session.company else None
    if not name:
        return []
    try:
        from iis.integrations.telegram_report_index import find_reports_for_company, load_index

        hits = find_reports_for_company(name, index=load_index())
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in hits:
        key = f"{h.get('broker')}|{h.get('report_date')}|{h.get('rating')}|{h.get('file_name')}"
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "broker": h.get("broker") or "—",
                "report_date": h.get("report_date") or "—",
                "rating": h.get("rating") or h.get("rating_detail") or "—",
                "report_type": h.get("report_type") or "—",
                "file_name": h.get("file_name") or "",
            }
        )
        if len(rows) >= 25:
            break
    return rows


def _section(title: str, body: list[str]) -> list[str]:
    """Emit a ## section only when body has real content — never placeholder padding."""
    content = [ln for ln in body if ln is not None]
    nonempty = [ln for ln in content if str(ln).strip()]
    if not nonempty:
        return []
    lines = [f"## {title}", ""]
    lines.extend(content)
    if lines[-1] != "":
        lines.append("")
    return lines


def _has_content(lines: list[str]) -> bool:
    return any(str(ln).strip() for ln in lines)


# ── Layer 1 ──────────────────────────────────────────────────────────────


def _layer1_brief(session: ResearchSession) -> list[str]:
    company = session.company
    name = company.name if company else "Unknown"
    bse = company.bse_code if company else None
    nse = company.nse_ticker if company else None
    metrics = get_key_metrics_summary(session.financial_line_items)
    ratios = get_latest_ratio_summary(session.financial_line_items)
    est = get_estimates_summary(session)
    price, price_date, price_src = _price_and_date(session)
    v = session.valuation
    flags = _flags_payload(session)
    review_flags = [f for f in flags if f.get("severity") == "review"]

    lines: list[str] = [
        "# Layer 1 — Investment brief",
        "",
        "## Snapshot",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Company | {name} |",
        f"| Ticker | NSE: {nse or '—'} · BSE: {bse or '—'} |",
        f"| Market capitalisation | {_mcap_line(session, metrics, price)} |",
    ]
    if price is not None:
        lines.append(
            f"| Current market price | ₹{price:,.2f}"
            + (f" ({price_date}" if price_date else "")
            + (f", {price_src})" if price_src else (")" if price_date else ""))
            + " |"
        )
    else:
        lines.append("| Current market price | price unavailable (price sheet and NSE/BSE feed returned nothing) |")
    if _dcf_on() and v and v.intrinsic_value_per_share is not None:
        lines.append(f"| Model fair value (DCF) | ₹{v.intrinsic_value_per_share:,.0f}/share |")
        if v.margin_of_safety_pct is not None:
            lines.append(f"| Margin of safety vs price | {v.margin_of_safety_pct:.1%} |")
        if price and v.intrinsic_value_per_share > 0:
            try:
                ann = (v.intrinsic_value_per_share / price) ** (1 / 3) - 1
                lines.append(f"| Implied ann. return (3Y to FV) | {ann:.1%} _(model)_ |")
            except Exception:
                pass
    lines.append("")

    # Initiating-coverage order: business → cases → questions → then numbers
    try:
        from iis.finance.business_overview import (
            build_business_overview,
            cases_markdown,
            overview_markdown,
            qual_vs_numbers_markdown,
            research_questions_markdown,
        )

        # Python-first overview during report build. LLM overview review can hang
        # the whole analyze job (Hive); analyst pass may still polish later.
        ov = build_business_overview(session, llm_review=False)
        lines.extend(["## Business overview", ""])
        lines.extend(overview_markdown(ov))
        try:
            from iis.finance.business_checklist import checklist_markdown

            lines.extend(checklist_markdown(ov.get("checklist") or {}, session=None))
        except Exception:
            pass
        lines.extend(["## Investment case & rejection case", ""])
        lines.extend(cases_markdown(session))
        lines.extend(research_questions_markdown(session))
        lines.extend(qual_vs_numbers_markdown(session))
    except Exception:
        reasons = _reasons_to_own(session, metrics, ratios, review_flags)
        if reasons:
            lines.extend(["### Why own (from sources on file)", ""])
            lines.extend(reasons)
            lines.append("")
        risks = _key_risks_brief(session, review_flags)
        if risks:
            lines.extend(["### Key risks (from sources on file)", ""])
            lines.extend(risks)
            lines.append("")

    lines.extend(_investment_dashboard(session, metrics, ratios, flags, v, price))
    try:
        from iis.finance.history_snapshot import build_ten_year_snapshot, snapshot_markdown

        snap = build_ten_year_snapshot(session.financial_line_items, years=10)
        n = snap.get("years_on_file") or 0
        title = f"### {n}-year financial snapshot (CIQ actuals)" if n else "### Financial snapshot (CIQ actuals)"
        if n and n < 10:
            title = f"### Financial snapshot — {n} of 10 years on file (CIQ actuals)"
        lines.extend(["## Financial data", ""])
        lines.extend(snapshot_markdown(snap, title=title))
    except Exception:
        pass
    # Full CIQ statements + estimates as tables (also feeds Hive via Layer 1)
    try:
        from iis.finance.ciq_tables import (
            estimates_tables_markdown,
            financials_as_uploaded_markdown,
        )

        fin_tables = financials_as_uploaded_markdown(
            session,
            statements=("income_statement", "balance_sheet", "cash_flow", "ratios", "segments"),
            max_periods=6,
            compact=False,
        )
        if fin_tables:
            lines.extend(fin_tables)
        est_tables = estimates_tables_markdown(session)
        if est_tables:
            lines.extend(["## Capital IQ estimates", ""])
            lines.extend(est_tables)
    except Exception:
        pass
    lines.extend(_checklist_brief(session, flags))
    lines.extend(_broker_consensus_brief(session, est))
    try:
        from iis.finance.ciq_tables import brokerage_report_sections_markdown

        br = brokerage_report_sections_markdown(session)
        if br:
            lines.extend(br)
    except Exception:
        pass
    return lines


def _mcap_line(session: ResearchSession, metrics: dict, price: float | None) -> str:
    # Prefer CIQ if present in line items
    for needle in ("market_capitalization", "market cap", "enterprise_value"):
        item = get_latest_actual(session.financial_line_items, needle) if "_" in needle else None
        if item and item.value:
            return f"₹{_fmt_mn(item.value)} mn ({item.line_item_raw[:40]})"
    shares = metrics.get("shares_outstanding_mn")
    if shares and price:
        return f"≈ ₹{_fmt_mn(shares * price)} mn _(price × shares; illustrative)_"
    return "—"


def _reasons_to_own(
    session: ResearchSession,
    metrics: dict,
    ratios: dict,
    review_flags: list,
) -> list[str]:
    bullets: list[str] = []
    cagr = compute_revenue_cagr(session.financial_line_items, years=3)
    if cagr is not None:
        bullets.append(f"- Revenue 3Y CAGR about **{_fmt_pct(cagr)}** (reported CIQ series).")
    em = metrics.get("ebitda_margin") or ratios.get("ebitda_margin")
    if em is not None:
        bullets.append(f"- Latest EBITDA margin **{_fmt_pct(em if abs(em) <= 1.5 else em / 100)}**.")
    roce = ratios.get("roce") or ratios.get("roe")
    if roce is not None:
        label = "ROCE" if ratios.get("roce") is not None else "ROE"
        basis = " (pre-tax EBIT ÷ capital employed)" if label == "ROCE" else ""
        bullets.append(f"- {label}{basis} **{format_ratio_value('roce' if label == 'ROCE' else 'roe', roce)}**.")
    porter = _artifact(session, "porters_five_forces")
    if porter:
        bullets.append("- Competitive framing available from Porter’s analysis on uploaded PDFs.")
    return bullets[:6]


def _key_risks_brief(session: ResearchSession, review_flags: list) -> list[str]:
    bullets: list[str] = []
    for f in review_flags[:4]:
        bullets.append(f"- **{f.get('headline', 'Flag')}** — {f.get('explanation', '')[:160]}")
    ar = _artifact(session, "annual_report_analysis")
    if ar:
        risks: list[str] = []
        for file_data in (ar.get("files") or {}).values():
            for analysis in (file_data.get("sections") or {}).values():
                risks.extend(analysis.get("risks_mentioned") or [])
        for r in risks:
            if len(bullets) >= 6:
                break
            bullets.append(f"- {r}")
    return bullets[:6]


def _investment_dashboard(
    session: ResearchSession,
    metrics: dict,
    ratios: dict,
    flags: list,
    v: Any,
    price: float | None,
) -> list[str]:
    review = [f for f in flags if f.get("severity") == "review"]
    # Heuristic labels only when we have data
    ebitda_m = metrics.get("ebitda_margin") or ratios.get("ebitda_margin")
    if ebitda_m is not None and abs(ebitda_m) > 1.5:
        ebitda_m = ebitda_m / 100
    rows: list[tuple[str, str]] = []
    if ebitda_m is not None:
        quality = "Excellent" if ebitda_m >= 0.25 else "Good" if ebitda_m >= 0.15 else "Average" if ebitda_m >= 0.08 else "Weak"
        rows.append(("Business quality (EBITDA mgn heuristic)", quality))

    nd_ebitda = ratios.get("net_debt_to_ebitda")
    cfo_ni = ratios.get("cfo_to_ni")
    if nd_ebitda is not None or cfo_ni is not None:
        if (nd_ebitda is not None and nd_ebitda < 1) and (cfo_ni is None or cfo_ni >= 0.7):
            health = "Strong"
        elif nd_ebitda is not None and nd_ebitda > 3:
            health = "Stretched"
        else:
            health = "Adequate"
        rows.append(("Financial health", health))

    cagr = compute_revenue_cagr(session.financial_line_items, years=3)
    if cagr is not None:
        growth = "Strong" if cagr >= 0.15 else "Moderate" if cagr >= 0.05 else "Soft"
        rows.append(("Growth (3Y rev CAGR)", f"{growth} · {_fmt_pct(cagr)}"))

    if _dcf_on() and v and v.margin_of_safety_pct is not None:
        mos = v.margin_of_safety_pct
        valuation = "Cheap" if mos >= 0.20 else "Fair" if mos >= -0.05 else "Expensive"
        rows.append(("Valuation vs DCF", f"{valuation} · MoS {mos:.1%}"))

    if _dcf_on():
        sc = build_scenarios(session, market_price=price)
        if sc.get("ok"):
            by = {s["name"]: s for s in sc["scenarios"]}
            parts = []
            for k in ("bear", "base", "bull"):
                ar = (by.get(k) or {}).get("ann_return")
                if ar is not None:
                    parts.append(f"{k}: {_fmt_pct(ar)}")
            if parts:
                rows.append(("Model expected return", " · ".join(parts)))

    if review:
        rows.append(("Data flags", f"{len(review)} review item(s)"))

    if not rows:
        return []
    out = [
        "### Investment dashboard",
        "",
        "| Metric | Assessment |",
        "|---|---|",
    ]
    for label, val in rows:
        out.append(f"| {label} | {val} |")
    out.append("")
    return out


def _checklist_brief(session: ResearchSession, flags: list) -> list[str]:
    if not flags and not session.financial_line_items:
        return []
    profile = load_checklist_profile()
    by_bucket: dict[str, list] = defaultdict(list)
    for f in flags:
        cat = f.get("category") or "other"
        bucket = FLAG_CATEGORY_TO_BUCKET.get(cat, cat)
        if profile.bucket_enabled(bucket):
            by_bucket[bucket].append(f)

    lines = [
        "### Investor checklist (from uploaded data)",
        "",
        "| Bucket | Status | Notes |",
        "|---|---|---|",
    ]
    any_row = False
    for bucket, enabled in profile.buckets.items():
        if not enabled:
            continue
        label = BUCKET_LABELS.get(bucket, bucket)
        items = by_bucket.get(bucket) or []
        review = [f for f in items if f.get("severity") == "review"]
        if review:
            status = f"Flag ({len(review)})"
            note = review[0].get("headline", "")
        elif items:
            status = "Info only"
            note = items[0].get("headline", "")
        elif session.financial_line_items:
            status = "Clear"
            note = "No flags in this bucket"
        else:
            continue
        any_row = True
        lines.append(f"| {label} | {status} | {note[:80]} |")
    if not any_row:
        return []
    lines.append("")
    return lines


def _broker_consensus_brief(session: ResearchSession, est: dict) -> list[str]:
    lines: list[str] = []
    tp_mean = est.get("target_price_mean")
    tp_med = est.get("target_price_median")
    n_est = est.get("target_price_num_estimates")
    rec = est.get("consensus_recommendation")
    by_metric: dict[str, list] = defaultdict(list)
    for cm in session.consensus_metrics:
        by_metric[cm.metric].append(cm)
    brokers = _broker_street_views(session)
    if not (tp_mean or tp_med or rec or by_metric or brokers):
        return []

    lines.extend(["### Street / consensus snapshot", ""])
    if tp_mean or tp_med or rec:
        lines.append("| Field | Value |")
        lines.append("|---|---|")
        if tp_mean is not None:
            lines.append(f"| CIQ target price (mean) | ₹{float(tp_mean):,.0f} |")
        if tp_med is not None:
            lines.append(f"| CIQ target price (median) | ₹{float(tp_med):,.0f} |")
        if n_est is not None:
            lines.append(f"| # target estimates | {n_est} |")
        if rec:
            lines.append(f"| CIQ consensus recommendation | {rec} |")
        lines.append("")

    key_metrics = [m for m in _KEY_CONSENSUS if m in by_metric]
    fwd_by_m = {
        m: sorted([cm for cm in by_metric[m] if cm.actual is None], key=lambda x: x.period)
        for m in key_metrics
    }
    past_by_m = {
        m: sorted([cm for cm in by_metric[m] if cm.actual is not None], key=lambda x: x.period)
        for m in key_metrics
    }
    fwd_periods = sorted({cm.period for rows in fwd_by_m.values() for cm in rows})
    past_periods = sorted({cm.period for rows in past_by_m.values() for cm in rows})
    if fwd_periods and any(fwd_by_m.values()):
        lines.append("**Consensus estimates (CIQ, open years)** — mean (n)")
        lines.append("")
        lines.append("| Metric | " + " | ".join(fwd_periods) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(fwd_periods)) + "|")
        for metric in key_metrics:
            by_p = {cm.period: cm for cm in fwd_by_m.get(metric) or []}
            if not by_p:
                continue
            cells = []
            for p in fwd_periods:
                cm = by_p.get(p)
                if not cm or cm.mean is None:
                    cells.append("—")
                else:
                    n = f" (n={cm.num_estimates})" if cm.num_estimates else ""
                    cells.append(f"{_fmt_mn(cm.mean)}{n}")
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")
        lines.append("")
    if past_periods and any(past_by_m.values()):
        lines.append("**Reported vs final consensus (CIQ, completed years)** — actual / final (surprise)")
        lines.append("")
        lines.append("| Metric | " + " | ".join(past_periods) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(past_periods)) + "|")
        for metric in key_metrics:
            by_p = {cm.period: cm for cm in past_by_m.get(metric) or []}
            if not by_p:
                continue
            cells = []
            for p in past_periods:
                cm = by_p.get(p)
                if not cm or cm.actual is None:
                    cells.append("—")
                else:
                    surprise = (cm.actual / cm.mean - 1) if cm.mean else None
                    sur = f" ({surprise:+.1%})" if surprise is not None else ""
                    cells.append(f"{_fmt_mn(cm.actual)} / {_fmt_mn(cm.mean)}{sur}")
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")
        lines.append("")

    if brokers:
        lines.append("**Individual broker views (Telegram index)**")
        lines.append("")
        lines.append("| Broker | Date | Rating | Type |")
        lines.append("|---|---|---|---|")
        for b in brokers[:15]:
            lines.append(
                f"| {b['broker']} | {b['report_date']} | {b['rating']} | {b['report_type']} |"
            )
        lines.append("")
    return lines


# ── Layer 2 ──────────────────────────────────────────────────────────────


def _sec_research_plan(session: ResearchSession) -> list[str]:
    """Hive planner: company card → what to look for."""
    try:
        from iis.finance.research_planner import research_plan_markdown

        body = research_plan_markdown(session)
    except Exception:
        body = []
    if not _has_content(body):
        return []
    return _section("0. Research plan — what to look for", body)


def _sec_unified_doc_digest(session: ResearchSession) -> list[str]:
    """Fast-path Hive digest (packed AR/concall/broker → one call)."""
    try:
        from iis.finance.one_shot_doc_synthesis import unified_doc_synthesis_markdown

        body = unified_doc_synthesis_markdown(session)
    except Exception:
        body = []
    if not _has_content(body):
        return []
    return _section("1a. Document digest (one-shot Hive)", body)


def _layer2_full(session: ResearchSession) -> list[str]:
    # Analyst modules: quality first; valuation kept separate; reviewer + evidence last
    out: list[str] = []
    for builder in (
        _sec_research_plan,
        _sec_business_overview,
        _sec_unified_doc_digest,
        _sec_investment_rejection,
        _sec_research_questions,
        _sec_qual_vs_numbers,
        _sec_research_packs,
        _sec_annual_report_detail,
        _sec_competitive_advantage,
        _sec_competitors,
        _sec_street_view,
        _sec_brokerage_research,
        _sec_swot,
        _sec_management,
        _sec_financials,
        _sec_checklists,
        _sec_growth_outlook,
        _sec_forecasts,
        _sec_valuation,
        _sec_risks,
        _sec_catalysts,
        _sec_independent_reviewer,
        _sec_evidence_register,
        _sec_evidence_dossier,
        _sec_conclusion,
        _sec_data_checks,
        _sec_open_requests,
        _sec_sources,
    ):
        out.extend(builder(session))
    return out


def _sec_independent_reviewer(session: ResearchSession) -> list[str]:
    """Hive independent reviewer over AR + concall deep-reads (challenges, not confirms)."""
    ar = _artifact(session, "annual_report_analysis")
    guidance = _artifact(session, "guidance_summary")
    body: list[str] = [
        "_Challenges AR and concall deep-reads; separates facts from assumptions. "
        "Does not invent financials — Capital IQ remains authoritative for ₹._",
        "",
    ]
    any_ok = False
    critic_map = ar.get("critic") if isinstance(ar.get("critic"), dict) else {}
    # Also from structured_sections.by_file
    by_file = (ar.get("structured_sections") or {}).get("by_file") or {}
    files = set(critic_map.keys()) | set(by_file.keys())
    try:
        from iis.finance.ar_critic import critic_markdown
    except Exception:
        critic_markdown = None  # type: ignore
    for fname in sorted(files):
        critic = critic_map.get(fname) or (by_file.get(fname) or {}).get("critic")
        if not isinstance(critic, dict) or not critic.get("ok"):
            continue
        any_ok = True
        body.append(f"#### AR · `{Path(str(fname)).name}`")
        body.append("")
        if critic_markdown:
            body.extend(critic_markdown(critic))
        else:
            for h in critic.get("headlines") or []:
                body.append(f"- **{h}**")
            body.append("")

    # Concall unit critics
    struct = guidance.get("structured_concall") if isinstance(guidance, dict) else None
    critics = (struct or {}).get("critics") if isinstance(struct, dict) else None
    if isinstance(critics, list) and critics:
        concall_lines = [
            c for c in critics if isinstance(c, dict) and c.get("ok")
        ]
        if concall_lines:
            any_ok = True
            body.append("#### Earnings calls")
            body.append("")
            for c in concall_lines:
                body.append(
                    f"- `{c.get('unit_id')}`: **{c.get('verdict')}** "
                    f"(vs CIQ guidance: {c.get('guidance_consistency', 'n_a')})"
                )
                for iss in (c.get("issues") or [])[:4]:
                    if isinstance(iss, dict):
                        body.append(
                            f"  - [{iss.get('severity', '?')}] {iss.get('issue', '')}"
                        )
            body.append("")

    # Broker STREET critics
    bv = _artifact(session, "brokerage_view")
    bstruct = bv.get("structured_broker") if isinstance(bv, dict) else None
    bcritics = (bstruct or {}).get("critics") if isinstance(bstruct, dict) else None
    if isinstance(bcritics, list) and bcritics:
        broker_ok = [c for c in bcritics if isinstance(c, dict) and c.get("ok")]
        if broker_ok:
            any_ok = True
            body.append("#### Brokerage (STREET)")
            body.append("")
            for c in broker_ok:
                body.append(
                    f"- `{c.get('unit_id')}`: **{c.get('verdict')}** "
                    f"(hygiene: {c.get('street_hygiene', 'ok')})"
                )
                for iss in (c.get("issues") or [])[:4]:
                    if isinstance(iss, dict):
                        body.append(
                            f"  - [{iss.get('severity', '?')}] {iss.get('issue', '')}"
                        )
            body.append("")
    if not any_ok:
        return []
    return _section("Independent reviewer (Hive)", body)


def _sec_evidence_register(session: ResearchSession) -> list[str]:
    """Flat claim → file/page register from AR + concall Hive analyst loops."""
    ar = _artifact(session, "annual_report_analysis")
    guidance = _artifact(session, "guidance_summary")
    rows = list(ar.get("evidence_register") or [])
    if not rows:
        by_file = (ar.get("structured_sections") or {}).get("by_file") or {}
        for block in by_file.values():
            if isinstance(block, dict):
                rows.extend(block.get("evidence_register") or [])
    # Append concall + broker evidence
    for r in list(guidance.get("evidence_register") or []):
        if isinstance(r, dict):
            rows.append(r)
    struct = guidance.get("structured_concall") if isinstance(guidance, dict) else None
    if isinstance(struct, dict):
        for r in struct.get("evidence_register") or []:
            if isinstance(r, dict):
                rows.append(r)
    bv = _artifact(session, "brokerage_view")
    for r in list(bv.get("evidence_register") or []):
        if isinstance(r, dict):
            rows.append(r)
    bstruct = bv.get("structured_broker") if isinstance(bv, dict) else None
    if isinstance(bstruct, dict):
        for r in bstruct.get("evidence_register") or []:
            if isinstance(r, dict):
                rows.append(r)
    if not rows:
        return []
    body: list[str] = [
        "_Material narrative claims from Hive AR / concall / broker deep-read with page refs. "
        "₹ totals still come from Capital IQ. Broker bullets are STREET opinion only._",
        "",
        "| Claim | Source | Pages | Mode |",
        "|---|---|---|---|",
    ]
    for r in rows[:100]:
        claim = str(r.get("claim") or "").replace("|", "/").replace("\n", " ")[:180]
        fname = Path(str(r.get("file") or "")).name.replace("|", "/")
        pages = str(r.get("pages") or "—").replace("|", "/")
        mode = str(r.get("mode") or "—").replace("|", "/")
        src = str(r.get("source") or "")
        if src == "concall" and mode != "relevance":
            mode = f"concall/{mode}"
        elif src == "broker" and mode != "relevance":
            mode = f"STREET/{mode}"
        body.append(f"| {claim} | `{fname}` | {pages} | {mode} |")
    body.append("")
    return _section("Evidence register", body)


def _sec_research_packs(session: ResearchSession) -> list[str]:
    """Typed AR / concall / broker packs (claim IDs for citation / hover)."""
    try:
        from iis.finance.packs.store import packs_dir, read_pack

        root = packs_dir(session)
    except Exception:
        return []
    body: list[str] = [
        "_Python-retrieved packs on disk. Cite claim IDs in brackets; broker = STREET only._",
        "",
    ]
    any_pack = False
    for name, label in (
        ("ar_pack", "Annual report pack"),
        ("concall_pack", "Concall pack"),
        ("broker_pack", "Brokerage pack (STREET)"),
    ):
        pack = read_pack(session, name)
        if not pack:
            continue
        any_pack = True
        body.append(f"**{label}** — {pack.get('claim_count', 0)} claims · `{root / (name + '.md')}`")
        # Preview: up to 2 claims from first non-empty sections
        shown = 0
        for sk, claims in (pack.get("sections") or {}).items():
            if not claims or shown >= 4:
                continue
            c0 = claims[0]
            body.append(
                f"- `{sk}` / **{c0.get('parameter')}** [{c0.get('id')}] "
                f"_{c0.get('claim_type')}_ — {(c0.get('quote') or '')[:220]}"
            )
            shown += 1
        # Mgmt vs broker highlight
        if name == "broker_pack":
            gaps = (pack.get("sections") or {}).get("mgmt_vs_broker") or []
            if gaps:
                body.append(f"- **Mgmt vs broker gaps:** {len(gaps)} (see pack J)")
        body.append("")
    if not any_pack:
        return []
    art = next((a for a in session.agent_artifacts if a.artifact_type == "research_packs"), None)
    if art and isinstance(art.payload, dict):
        order = (art.payload.get("paths") and None) or None
        _ = order
        # reading order from index if present
        try:
            import json
            from iis.finance.packs.store import packs_dir as _pd

            idx = _pd(session) / "index.json"
            if idx.exists():
                data = json.loads(idx.read_text(encoding="utf-8"))
                for line in data.get("reading_order") or []:
                    body.append(f"- {line}")
                body.append("")
        except Exception:
            pass
    return _section("1b. Research packs (AR · Concall · Broker)", body)


def _sec_investment_rejection(session: ResearchSession) -> list[str]:
    try:
        from iis.finance.business_overview import cases_markdown

        body = cases_markdown(session)
    except Exception:
        body = []
    return _section("1a. Investment case and rejection case", body)


def _sec_research_questions(session: ResearchSession) -> list[str]:
    try:
        from iis.finance.business_overview import research_questions_markdown

        body = research_questions_markdown(session)
    except Exception:
        body = []
    return _section("1b. Research questions to carry forward", body)


def _sec_qual_vs_numbers(session: ResearchSession) -> list[str]:
    try:
        from iis.finance.business_overview import qual_vs_numbers_markdown

        body = qual_vs_numbers_markdown(session)
    except Exception:
        body = []
    return _section("1c. Qualitative claims vs financial evidence", body)


def _sec_street_view(session: ResearchSession) -> list[str]:
    try:
        from iis.finance.brokerage_view import brokerage_view_markdown

        body = brokerage_view_markdown(session)
    except Exception:
        body = []
    return _section("3a. Street view — broker estimates vs CIQ consensus", body)


def _sec_checklists(session: ResearchSession) -> list[str]:
    try:
        from iis.finance.critique import critique_markdown

        body = critique_markdown(session)
    except Exception:
        body = []
    return _section("6a. Investor checklists (rule-based, Python)", body)


def _sec_data_checks(session: ResearchSession) -> list[str]:
    body: list[str] = []
    try:
        from iis.finance.reconciler import reconciliation_markdown

        body.extend(reconciliation_markdown(session))
    except Exception:
        pass
    # Capex side-by-side from AR structured handlers
    ar = _artifact(session, "annual_report_analysis")
    by_file = (ar.get("structured_sections") or {}).get("by_file") or {}
    capex_lines: list[str] = []
    for fname, block in by_file.items():
        if not isinstance(block, dict):
            continue
        for h in (block.get("handlers") or {}).values():
            if not isinstance(h, dict):
                continue
            recon = h.get("capex_reconcile")
            if not isinstance(recon, dict):
                continue
            capex_lines.append(f"#### Capex check — `{Path(str(fname)).name}`")
            capex_lines.append("")
            for m in recon.get("ar_capex_mentions") or []:
                capex_lines.append(f"- AR narrative: {m}")
            for m in recon.get("ciq_capex_lines") or []:
                capex_lines.append(f"- {m}")
            if recon.get("note"):
                capex_lines.append(f"- _{recon['note']}_")
            capex_lines.append("")
    if capex_lines:
        body.append("### Capex: AR narrative vs Capital IQ")
        body.append("")
        body.extend(capex_lines)
    # Critic contradictions
    for fname, critic in (ar.get("critic") or {}).items() if isinstance(ar.get("critic"), dict) else []:
        if not isinstance(critic, dict):
            continue
        contras = critic.get("contradictions") or []
        if contras:
            body.append(f"### Reviewer contradictions — `{Path(str(fname)).name}`")
            body.append("")
            for c in contras[:10]:
                body.append(f"- {c}")
            body.append("")
    body.append(
        "_Hard rule: geographic **non-current assets** must never be labeled as "
        "Market / revenue mix — use CIQ Geographic · Revenues only._"
    )
    body.append("")
    return _section("Data checks (reconciliation)", body)


def _sec_open_requests(session: ResearchSession) -> list[str]:
    try:
        from iis.agents.user_communicator import open_asks

        asks = open_asks(session)
    except Exception:
        asks = []
    body = [f"- {a['prompt']}" for a in asks if a.get("prompt")]
    if body:
        body.append("")
    return _section("Open data requests", body)


def _sec_evidence_dossier(session: ResearchSession) -> list[str]:
    """Keyword / heading extracts from AR, concall, brokerage PDFs."""
    try:
        from iis.finance.evidence_dossier import build_evidence_dossier

        d = build_evidence_dossier(session)
    except Exception:
        return []
    if not d.hits:
        return []
    body: list[str] = [
        f"_{len(d.hits)} extracts across {len(d.topic_counts)} topics "
        f"(keyword / heading match — not invented)._",
        "",
    ]
    # Compact: top hits per topic already ordered
    md = d.markdown(max_per_topic=5)
    # Drop the dossier H1 — we already have a section title
    lines = md.splitlines()
    while lines and not lines[0].startswith("## "):
        lines.pop(0)
    body.extend(lines)
    body.append("")
    return _section("Evidence from filings & research PDFs", body)


def _sec_business_overview(session: ResearchSession) -> list[str]:
    try:
        from iis.finance.business_overview import build_business_overview, overview_markdown

        # llm_review=False — Hive overview review was hanging the whole analyze report step
        ov = build_business_overview(session, llm_review=False)
        body = overview_markdown(ov)
    except Exception:
        body = []
    guidance = _artifact(session, "guidance_summary")
    if guidance.get("themes") or guidance.get("commentary"):
        body.append("### Management / guidance themes")
        body.append("")
        if guidance.get("commentary"):
            body.append(str(guidance.get("commentary"))[:800])
            body.append("")
        if guidance.get("themes"):
            body.append("**Themes:** " + ", ".join(str(t) for t in guidance["themes"][:10]))
            body.append("")
    return _section("1. Business overview — products, markets, geo, clients", body)


def _sec_annual_report_detail(session: ResearchSession) -> list[str]:
    try:
        from iis.finance.report_enrichment import annual_report_narrative_markdown

        body = annual_report_narrative_markdown(session)
    except Exception:
        body = []
    # Drop soft empty placeholders from enrichment
    if not _has_content(body) or (
        len(body) == 2 and "No annual-report" in (body[0] or "")
    ):
        return []
    if body and "No annual-report" in (body[0] or ""):
        return []
    return _section("1b. Annual report excerpts", body)


def _sec_brokerage_research(session: ResearchSession) -> list[str]:
    body: list[str] = []
    try:
        from iis.finance.ciq_tables import brokerage_report_sections_markdown

        body.extend(brokerage_report_sections_markdown(session))
    except Exception:
        # Fallback to prior helpers
        try:
            from iis.finance.broker_pipeline import structured_broker_markdown

            bv = _artifact(session, "brokerage_view")
            struct = bv.get("structured_broker") if isinstance(bv, dict) else None
            hive_md = structured_broker_markdown(struct if isinstance(struct, dict) else None)
            if hive_md:
                body.extend(hive_md)
                body.append("")
        except Exception:
            pass
        try:
            from iis.finance.report_enrichment import brokerage_research_markdown

            body.extend(brokerage_research_markdown(session))
        except Exception:
            pass
    if not _has_content(body):
        return []
    if any("No brokerage PDFs matched" in (ln or "") for ln in body) and not any(
        ln.startswith("|") or ln.startswith("- ") for ln in body
    ):
        return []
    return _section("3b. Brokerage research", body)


def _sec_competitive_advantage(session: ResearchSession) -> list[str]:
    body: list[str] = []
    porter = _artifact(session, "porters_five_forces")
    if porter:
        body.append("### Porter’s five forces (from uploaded PDFs / model)")
        body.append("")
        for k, v in porter.items():
            if isinstance(v, str) and v.strip():
                body.append(f"**{k.replace('_', ' ').title()}:** {v}")
                body.append("")
    return _section("2. Competitive advantage and business quality", body)


def _sec_competitors(session: ResearchSession) -> list[str]:
    body: list[str] = []
    peer_rows = _local_peer_comparison(session)
    if peer_rows:
        body.append("### Peer comparison (user-confirmed peers, from their CIQ Financials)")
        body.append("")
        body.append("| Company | Rev ₹ mn | EBITDA mgn | ROE | ROCE | Net debt/EBITDA |")
        body.append("|---|---:|---:|---:|---:|---:|")
        for r in peer_rows:
            body.append(
                f"| {r['name']} | {_fmt_mn(r.get('revenue'))} | "
                f"{_fmt_pct(r.get('ebitda_margin'))} | "
                f"{format_ratio_value('roe', r.get('roe'))} | "
                f"{format_ratio_value('roce', r.get('roce'))} | "
                f"{format_ratio_value('net_debt_to_ebitda', r.get('net_debt_to_ebitda'))} |"
            )
        body.append("")

    mult = get_multiples_table(session)
    if mult:
        body.append("### Own-company multiples (CIQ Estimates)")
        body.append("")
        body.append("| Period | P/E | EV/EBITDA | TEV/Rev | P/B |")
        body.append("|---|---:|---:|---:|---:|")
        for period, data in sorted(mult.items()):
            pe = data.get("pe")
            ev = data.get("ev_ebitda")
            tr = data.get("tev_rev")
            pb = data.get("pbv")
            body.append(
                f"| {period} | "
                f"{f'{pe:.1f}x' if pe is not None else '—'} | "
                f"{f'{ev:.1f}x' if ev is not None else '—'} | "
                f"{f'{tr:.1f}x' if tr is not None else '—'} | "
                f"{f'{pb:.1f}x' if pb is not None else '—'} |"
            )
        body.append("")
    try:
        from iis.finance.competitor_discovery import competitor_ask_markdown

        body.extend(competitor_ask_markdown(session))
    except Exception:
        pass
    return _section("3. Competitor analysis and relative positioning", body)


def _local_peer_comparison(session: ResearchSession) -> list[dict[str, Any]]:
    """Subject vs user-confirmed peers that have CIQ Financials on file — nobody else."""
    from iis.finance.competitor_discovery import confirmed_peer_libraries

    peers = confirmed_peer_libraries(session)
    if not peers:
        return []
    m = get_key_metrics_summary(session.financial_line_items)
    r = get_latest_ratio_summary(session.financial_line_items)
    name = session.company.name if session.company else "Subject"
    rows: list[dict[str, Any]] = [
        {
            "name": f"**{name}**",
            "revenue": m.get("revenue_mn"),
            "ebitda_margin": m.get("ebitda_margin") or r.get("ebitda_margin"),
            "roe": r.get("roe"),
            "roce": r.get("roce"),
            "net_debt_to_ebitda": r.get("net_debt_to_ebitda"),
        }
    ]
    try:
        from iis.library.store import get_company_library_store

        store = get_company_library_store()
        for p in peers[:8]:
            lib = store.load(p["company_key"])
            if not lib.financial_line_items:
                continue
            pm = get_key_metrics_summary(lib.financial_line_items)
            pr = get_latest_ratio_summary(lib.financial_line_items)
            rows.append(
                {
                    "name": p["name"][:40],
                    "revenue": pm.get("revenue_mn"),
                    "ebitda_margin": pm.get("ebitda_margin") or pr.get("ebitda_margin"),
                    "roe": pr.get("roe"),
                    "roce": pr.get("roce"),
                    "net_debt_to_ebitda": pr.get("net_debt_to_ebitda"),
                }
            )
    except Exception:
        pass
    return rows if len(rows) > 1 else []


def _sec_swot(session: ResearchSession) -> list[str]:
    body: list[str] = []
    try:
        from iis.finance.swot import resolve_swot

        sw = resolve_swot(session)
    except Exception:
        sw = {}
    strengths = [str(x) for x in (sw.get("strengths") or []) if x]
    weaknesses = [str(x) for x in (sw.get("weaknesses") or []) if x]
    opportunities = [str(x) for x in (sw.get("opportunities") or []) if x]
    threats = [str(x) for x in (sw.get("threats") or []) if x]
    if not any([strengths, weaknesses, opportunities, threats]):
        return []
    if sw.get("note"):
        body.append(f"_{sw['note']}_")
        body.append("")
    src = sw.get("section") or sw.get("file") or sw.get("source")
    if src:
        body.append(f"_Source: {src}_")
        body.append("")
    body.append("| | Items |")
    body.append("|---|---|")
    if strengths:
        body.append(f"| **Strengths** | {'; '.join(strengths[:6])} |")
    if weaknesses:
        body.append(f"| **Weaknesses** | {'; '.join(weaknesses[:6])} |")
    if opportunities:
        body.append(f"| **Opportunities** | {'; '.join(opportunities[:6])} |")
    if threats:
        body.append(f"| **Threats / key risks** | {'; '.join(threats[:6])} |")
    body.append("")
    return _section("4. SWOT analysis", body)


def _sec_management(session: ResearchSession) -> list[str]:
    body: list[str] = []
    guidance = _artifact(session, "guidance_summary")

    # AR structured LLM: leadership letters, outlook, governance (full section → bullets)
    try:
        from iis.finance.ar_section_pipeline import structured_sections_markdown

        ar = _artifact(session, "annual_report_analysis")
        ar_struct = structured_sections_markdown(ar.get("structured_sections"))
        if ar_struct:
            # Prefer leadership / outlook / governance blocks for this section
            keep_heads = (
                "leadership",
                "future outlook",
                "governance",
            )
            filtered: list[str] = []
            take = False
            for ln in ar_struct:
                if ln.startswith("### "):
                    take = any(k in ln.lower() for k in keep_heads)
                if ln.startswith("## Annual report structured"):
                    filtered.append("### From annual report (full-section LLM summaries)")
                    filtered.append("")
                    take = False
                    continue
                if take:
                    filtered.append(ln)
            if filtered:
                body.extend(filtered)
                body.append("")
    except Exception:
        pass

    track_lines: list[str] = []
    try:
        from iis.finance.guidance_track import guidance_track_markdown

        track_lines = guidance_track_markdown(session.guidance_records, session.financial_line_items)
    except Exception:
        track_lines = []
    if _has_content(track_lines):
        body.append("### Prior guidance vs actual results")
        body.append("")
        body.extend(track_lines)

    # Concall Hive structured read (relevance → deep-read → critic)
    try:
        from iis.finance.concall_pipeline import structured_concall_markdown

        struct = guidance.get("structured_concall")
        concall_md = structured_concall_markdown(
            struct if isinstance(struct, dict) else None
        )
        if concall_md:
            body.extend(concall_md)
            body.append("")
    except Exception:
        pass

    if guidance.get("current_outlook"):
        body.append("### Current management outlook (from concalls)")
        body.append("")
        body.append(str(guidance["current_outlook"])[:1000])
        body.append("")

    reflections = guidance.get("guidance_reflections") or []
    if reflections:
        body.append("### Concall reflection on prior guidance")
        body.append("")
        body.append("| FY | Metric | Assessment | Evidence |")
        body.append("|---|---|---|---|")
        for ref in reflections[:8]:
            body.append(
                f"| {ref.get('fiscal_year') or '—'} | {ref.get('metric') or '—'} | "
                f"{ref.get('assessment') or '—'} | "
                f"{str(ref.get('transcript_evidence') or ref.get('prior_guidance') or '—')[:160]} |"
            )
        body.append("")

    if guidance.get("commentary"):
        body.append("### Commentary")
        body.append("")
        body.append(str(guidance["commentary"])[:1200])
        body.append("")
    if guidance.get("themes"):
        body.append("**Themes:** " + ", ".join(str(t) for t in guidance["themes"][:8]))
        body.append("")

    qa = guidance.get("transcript_qa") or []
    if qa:
        try:
            from iis.ingest.pdf import sanitize_qa_pairs
            from iis.models.artifacts import TranscriptQA

            cleaned = sanitize_qa_pairs(
                [
                    TranscriptQA(
                        file=str(item.get("file") or ""),
                        page=int(item.get("page") or 0),
                        question=str(item.get("question") or ""),
                        answer=str(item.get("answer") or ""),
                    )
                    for item in qa
                ]
            )
        except Exception:
            cleaned = []
        if cleaned:
            body.append("### Concall Q&A excerpts (guidance-related)")
            body.append("")
            for item in cleaned[:6]:
                body.append(
                    f"- _{Path(item.file or '').name}_"
                    + (f" p.{item.page}" if item.page else "")
                    + f": **Q:** {item.question[:180]}  \n  **A:** {item.answer[:320]}"
                )
            body.append("")

    return _section("5. Management quality and corporate governance", body)


def _sec_financials(session: ResearchSession) -> list[str]:
    body: list[str] = [
        "_Financial quality numbers below are from **Capital IQ** (Python). "
        "LLM narrative must not override these totals._",
        "",
    ]
    if not session.financial_line_items:
        return []

    try:
        from iis.finance.history_snapshot import build_ten_year_snapshot, snapshot_markdown

        body.extend(snapshot_markdown(build_ten_year_snapshot(session.financial_line_items, years=10)))
    except Exception:
        periods = build_period_metrics(session.financial_line_items)
        ordered = sorted(periods.keys(), reverse=True)[:10]
        if ordered:
            body.append("### Standardised historical table (₹ mn, actuals)")
            body.append("")
            header = "| Metric | " + " | ".join(p[:12] for p in ordered) + " |"
            sep = "|---|" + "|".join(["---:"] * len(ordered)) + "|"
            body.append(header)
            body.append(sep)
            for key, label in (
                ("total_revenue", "Revenue"),
                ("ebitda", "EBITDA"),
                ("net_income", "PAT"),
                ("operating_cash_flow", "CFO"),
                ("capital_expenditure", "Capex"),
            ):
                vals = [_fmt_mn(periods[p].get(key)) for p in ordered]
                body.append(f"| {label} | " + " | ".join(vals) + " |")
            body.append("")

    # Full CIQ statements as uploaded (IS / BS / CF / ratios / segments)
    try:
        from iis.finance.ciq_tables import financials_as_uploaded_markdown

        body.extend(
            financials_as_uploaded_markdown(session, compact=False, max_periods=8)
        )
    except Exception:
        pass

    series = get_revenue_series(session.financial_line_items)
    actuals = [p for p in series if not p.is_estimate][-8:]
    if actuals:
        body.append("### Revenue trend")
        body.append("")
        body.append("| Period | Revenue ₹ mn | YoY |")
        body.append("|---|---:|---:|")
        for p in actuals:
            yoy = f"{p.yoy_growth:.1%}" if p.yoy_growth is not None else "—"
            body.append(f"| {p.period_label[:28]} | {_fmt_mn(p.value_mn)} | {yoy} |")
        body.append("")

    ratios = get_latest_ratio_summary(session.financial_line_items)
    if ratios:
        body.append("### Latest profitability / returns / balance sheet")
        body.append("")
        body.append("| Ratio | Value |")
        body.append("|---|---|")
        for key in (
            "gross_margin",
            "ebitda_margin",
            "net_margin",
            "roe",
            "roce",
            "roa",
            "debt_to_equity",
            "net_debt_to_ebitda",
            "interest_coverage",
            "cfo_to_ni",
            "fcf_margin",
            "receivable_days",
        ):
            if key in ratios and ratios[key] is not None:
                body.append(f"| {key.replace('_', ' ').title()} | {format_ratio_value(key, ratios[key])} |")
        body.append("")

    cagr3 = compute_revenue_cagr(session.financial_line_items, years=3)
    cagr5 = compute_revenue_cagr(session.financial_line_items, years=5)
    if cagr3 is not None or cagr5 is not None:
        body.append(
            f"Revenue CAGR — 3Y: **{_fmt_pct(cagr3)}** · 5Y: **{_fmt_pct(cagr5)}**."
        )
        body.append("")

    try:
        from iis.finance.report_enrichment import ratio_interpretation_markdown

        body.extend(ratio_interpretation_markdown(session))
    except Exception:
        pass

    return _section("6. Financial performance and historical trends", body)


def _sec_growth_outlook(session: ResearchSession) -> list[str]:
    body: list[str] = []
    guidance = _artifact(session, "guidance_summary")
    if guidance.get("themes") or guidance.get("commentary"):
        body.append("### Management stated plans / themes")
        body.append("")
        if guidance.get("themes"):
            body.append("- " + "\n- ".join(str(t) for t in guidance["themes"]))
            body.append("")
        if guidance.get("commentary"):
            body.append(str(guidance["commentary"])[:1000])
            body.append("")
    # Capex from Capital IQ (authoritative) — always when available
    try:
        from iis.finance.metric_series import build_capex_analysis, format_capex_analysis

        capex = build_capex_analysis(session.financial_line_items or [], recent_years=4)
        if capex:
            body.append("### Capex (Capital IQ — authoritative ₹)")
            body.append("")
            body.append(format_capex_analysis(capex))
            body.append("")
    except Exception:
        pass
    # Full CIQ estimates tables (all metrics, high/low/n)
    try:
        from iis.finance.ciq_tables import estimates_tables_markdown

        est_md = estimates_tables_markdown(session)
        if est_md:
            body.extend(est_md)
    except Exception:
        fwd = [cm for cm in session.consensus_metrics if cm.actual is None and cm.metric in _KEY_CONSENSUS]
        if fwd:
            by_m: dict[str, dict[str, Any]] = defaultdict(dict)
            periods: set[str] = set()
            for cm in fwd:
                by_m[cm.metric][cm.period] = cm
                periods.add(cm.period)
            ordered = sorted(periods)
            body.append("### Street forward estimates (CIQ consensus)")
            body.append("")
            body.append("| Metric | " + " | ".join(ordered) + " |")
            body.append("|---|" + "|".join(["---:"] * len(ordered)) + "|")
            for metric in _KEY_CONSENSUS:
                if metric not in by_m:
                    continue
                cells = [_fmt_mn(by_m[metric][p].mean) if p in by_m[metric] else "—" for p in ordered]
                body.append(f"| {metric} | " + " | ".join(cells) + " |")
            body.append("")
    return _section("7. Growth drivers and future business outlook", body)


def _sec_forecasts(session: ResearchSession) -> list[str]:
    if not _dcf_on():
        return []
    if not session.assumptions:
        return []
    body: list[str] = []

    body.append("| Assumption | Value | Rationale |")
    body.append("|---|---|---|")
    for a in session.assumptions:
        val = a.user_value if a.user_value is not None else a.value
        if isinstance(val, float) and a.unit == "decimal":
            shown = f"{val:.2%}"
        else:
            shown = str(val)
        edited = " _(user edit)_" if a.user_edited else ""
        body.append(f"| {a.label} | {shown}{edited} | {(a.rationale or '')[:100]} |")
    body.append("")

    # Historical vs forward snapshot
    metrics = get_key_metrics_summary(session.financial_line_items)
    cagr = compute_revenue_cagr(session.financial_line_items, years=3)
    body.append("| Metric | Historical | Forecast (model) |")
    body.append("|---|---|---|")
    growth_a = next((x for x in session.assumptions if x.key == "revenue_growth_y1_5"), None)
    margin_a = next((x for x in session.assumptions if x.key == "ebitda_margin"), None)
    g_val = (growth_a.user_value if growth_a and growth_a.user_value is not None else growth_a.value) if growth_a else None
    m_val = (margin_a.user_value if margin_a and margin_a.user_value is not None else margin_a.value) if margin_a else None
    body.append(
        f"| Revenue growth | {_fmt_pct(cagr)} (3Y CAGR) | "
        f"{_fmt_pct(float(g_val) if g_val is not None else None)} (Y1–5 assumption) |"
    )
    hist_m = metrics.get("ebitda_margin")
    body.append(
        f"| EBITDA margin | {_fmt_pct(hist_m if hist_m is None or abs(hist_m) <= 1.5 else hist_m/100)} | "
        f"{_fmt_pct(float(m_val) if m_val is not None else None)} |"
    )
    body.append("")
    return _section("8. Financial forecasts and assumptions", body)


def _sec_valuation(session: ResearchSession) -> list[str]:
    body: list[str] = []
    v = session.valuation
    price, _, _ = _price_and_date(session)
    mult = get_multiples_table(session)
    has_dcf = _dcf_on() and bool(v and v.intrinsic_value_per_share is not None)

    if has_dcf:
        body.append("### DCF summary")
        body.append("")
        body.append(f"- **Fair value:** ₹{v.intrinsic_value_per_share:,.0f}/share")
        if price:
            body.append(f"- **Market price:** ₹{price:,.2f}")
        if v.margin_of_safety_pct is not None:
            body.append(f"- **MoS vs price:** {v.margin_of_safety_pct:.1%}")
        if v.current_price:
            body.append(f"- **Consensus target (CIQ):** ₹{float(v.current_price):,.0f}")
        body.append("")

    if _dcf_on():
        sc = build_scenarios(session, market_price=price)
        sc_md = scenarios_to_markdown(sc)
        if _has_content(sc_md):
            body.extend(sc_md)

    if mult:
        body.append("### Trading / forward multiples (CIQ)")
        body.append("")
        body.append("| Period | P/E | EV/EBITDA |")
        body.append("|---|---:|---:|")
        for period, data in sorted(mult.items()):
            pe, ev = data.get("pe"), data.get("ev_ebitda")
            body.append(
                f"| {period} | {f'{pe:.1f}x' if pe is not None else '—'} | "
                f"{f'{ev:.1f}x' if ev is not None else '—'} |"
            )
        body.append("")

    if not _has_content(body):
        return []
    return _section("9. Valuation and expected shareholder returns", body)


def _sec_risks(session: ResearchSession) -> list[str]:
    body: list[str] = []
    flags = _flags_payload(session)
    review = [f for f in flags if f.get("severity") == "review"]
    if review:
        body.append("### Data / financial red flags")
        body.append("")
        for f in review[:12]:
            body.append(f"- **{f.get('headline')}** — {f.get('explanation', '')}")
            verify = f.get("verify") or []
            if verify:
                body.append(f"  - Early check: {verify[0]}")
        body.append("")
    ar = _artifact(session, "annual_report_analysis")
    risks: list[str] = []
    if ar:
        for file_data in (ar.get("files") or {}).values():
            for analysis in (file_data.get("sections") or {}).values():
                risks.extend(analysis.get("risks_mentioned") or [])
    if risks:
        body.append("### Risks mentioned in filings / AR extracts")
        body.append("")
        for r in risks[:15]:
            body.append(f"- {r}")
        body.append("")
    return _section("10. Risks, red flags and thesis breakers", body)


def _sec_catalysts(session: ResearchSession) -> list[str]:
    """Only real catalysts from guidance / AR outlook — no generic template."""
    body: list[str] = []
    guidance = _artifact(session, "guidance_summary")
    items: list[str] = []
    for t in guidance.get("themes") or []:
        items.append(str(t))
    if guidance.get("current_outlook"):
        items.append(str(guidance["current_outlook"])[:400])
    ar = _artifact(session, "annual_report_analysis")
    if ar:
        for file_data in (ar.get("files") or {}).values():
            for analysis in (file_data.get("sections") or {}).values():
                for o in (analysis.get("outlook_statements") or [])[:4]:
                    items.append(str(o))
    items = [x.strip() for x in items if x and str(x).strip()]
    if not items:
        return []
    body.append("### Catalysts / outlook items on file")
    body.append("")
    for it in items[:12]:
        body.append(f"- {it[:320]}")
    body.append("")
    return _section("11. Catalysts and key monitorables", body)


def _sec_conclusion(session: ResearchSession) -> list[str]:
    """Facts only — no recommendation prompts."""
    body: list[str] = []
    price, _, _ = _price_and_date(session)
    if _dcf_on():
        v = session.valuation
        if v and v.margin_of_safety_pct is not None:
            body.append(f"- Model margin of safety vs price: **{v.margin_of_safety_pct:.1%}**")
        if v and v.intrinsic_value_per_share is not None:
            body.append(f"- Model fair value: **₹{v.intrinsic_value_per_share:,.0f}/share**")
    if price is not None:
        body.append(f"- Market price used: **₹{price:,.2f}**")
    if not body:
        return []
    body.append("")
    title = "12. Model summary" if _dcf_on() else "12. Price snapshot"
    return _section(title, body)


def _sec_sources(session: ResearchSession) -> list[str]:
    body: list[str] = [
        "### Sources on file",
        "",
    ]
    files = list(session.uploaded_files or [])
    # Library docs via inventory artifact if any
    try:
        from iis.library.store import get_company_library_store

        if session.company_key:
            inv = get_company_library_store().load(session.company_key).inventory()
            body.append(f"- Company library key: `{session.company_key}`")
            body.append(f"- Line items: {inv.get('total_line_items', 0)}")
            body.append(f"- PDF documents: {inv.get('total_pdf_documents', 0)}")
            for label, key in (
                ("CIQ Financials", "capital_iq_financials"),
                ("CIQ Estimates", "capital_iq_estimates"),
                ("Annual reports", "annual_report_files"),
                ("Concalls", "concall_files"),
                ("Brokerage", "brokerage_reports"),
            ):
                rows = inv.get(key) or []
                if rows:
                    body.append(f"- {label}: {len(rows)} file(s)")
            body.append("")
    except Exception:
        pass

    if files:
        body.append("### Session uploads")
        body.append("")
        for f in files[:30]:
            body.append(f"- `{f}`")
        body.append("")

    try:
        from iis.finance.data_freshness import freshness_ask_markdown

        body.extend(freshness_ask_markdown(session))
    except Exception:
        pass

    body.append("### Disclosures")
    body.append("")
    body.append(
        "- This draft is for **internal research**. Distinguish reported facts, management commentary, "
        "model outputs, and analyst judgment. Align client-facing versions with applicable SEBI requirements."
    )
    body.append(f"- Report draft time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    body.append(f"- Gates (informational): " + ", ".join(
        f"{g.value}={session.gate(g).status.value}" for g in GateId
    ))
    body.append("")

    body.append("### Appendix — recent research events")
    body.append("")
    for event in session.events[-12:]:
        body.append(
            f"- `{event.timestamp.strftime('%Y-%m-%d %H:%M')}` **{event.event_type.value}**"
        )
    body.append("")
    return _section("13. Sources, disclosures and appendix", body)
