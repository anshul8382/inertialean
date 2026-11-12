"""Session research overview — financial movement, consensus, multiples, guidance, flags."""
from __future__ import annotations

from typing import Any

from iis.finance.flags import compute_data_flags, flags_to_payload, pivot_guidance
from iis.finance.growth import build_growth_summary, compute_cagr_from_series
from iis.finance.metrics import get_key_metrics_summary
from iis.finance.multiples import build_multiples_response, get_estimates_summary, get_multiples_table
from iis.finance.ratios import build_period_metrics, compute_period_ratios
from iis.models.enums import PeriodType
from iis.models.session import ResearchSession
from iis.parsers.capital_iq.financials import query_financials


def _yoy_pct(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return ((curr - prev) / abs(prev)) * 100


def _price_source_label(source: str | None) -> str:
    labels = {
        "yfinance": "Yahoo Finance",
        "ollama": "Ollama",
        "ollama+web_search": "Ollama + web",
    }
    return labels.get(source or "", source or "unknown")


def _latest_pe_from_financials(items) -> float | None:
    for needle in ("price_to_earnings", "pe_ratio", "p/e", "price to earnings"):
        matches = query_financials(items, line_item_contains=needle)
        actuals = [m for m in matches if m.value is not None and m.period_type == PeriodType.ACTUAL]
        if actuals:
            actuals.sort(key=lambda x: x.period_end or "", reverse=True)
            val = float(actuals[0].value)
            return val / 100 if val > 100 else val
    return None


def _build_financial_movement(items) -> dict[str, Any]:
    periods = build_period_metrics(items)
    sorted_periods = sorted(
        [p for p in periods if periods[p].get("total_revenue") or periods[p].get("revenue")],
        reverse=True,
    )
    actual_periods = sorted_periods[:4]
    rows: list[dict[str, Any]] = []

    metrics_spec = [
        ("total_revenue", "Total Revenue", "INR mn"),
        ("ebitda", "EBITDA", "INR mn"),
        ("net_income", "Net Income", "INR mn"),
    ]

    for key, label, unit in metrics_spec:
        values: list[tuple[str, float | None]] = []
        for p in actual_periods:
            v = periods[p].get(key) or periods[p].get("revenue")
            values.append((p, v))
        if not any(v for _, v in values):
            continue
        yoy = None
        if len(values) >= 2 and values[0][1] and values[1][1]:
            yoy = _yoy_pct(values[0][1], values[1][1])
        rows.append(
            {
                "metric": label,
                "unit": unit,
                "latest_period": values[0][0] if values else None,
                "latest_value": values[0][1] if values else None,
                "prior_value": values[1][1] if len(values) > 1 else None,
                "yoy_pct": round(yoy, 1) if yoy is not None else None,
            }
        )

    if len(actual_periods) >= 2:
        curr_r = compute_period_ratios(periods[actual_periods[0]])
        prev_r = compute_period_ratios(periods[actual_periods[1]])
        for key, label in (
            ("ebitda_margin", "EBITDA Margin"),
            ("net_margin", "Net Margin"),
            ("gross_margin", "Gross Margin"),
        ):
            c, p = curr_r.get(key), prev_r.get(key)
            if c is not None:
                rows.append(
                    {
                        "metric": label,
                        "unit": "percent",
                        "latest_period": actual_periods[0],
                        "latest_value": round(c * 100, 1),
                        "prior_value": round(p * 100, 1) if p is not None else None,
                        "yoy_pct": round((c - p) * 100, 1) if p is not None else None,
                    }
                )

    return {"title": "How the financials have moved", "rows": rows}


def _build_revenue_section(items) -> dict[str, Any] | None:
    summary = build_growth_summary(items)
    actuals = summary["historical"]
    estimates = summary["estimates"]
    if not actuals:
        return None

    hist_years = 5
    cagr = compute_cagr_from_series(actuals, hist_years) or summary.get("cagr_5y")
    kpis = []
    if cagr is not None:
        kpis.append({"label": f"{hist_years}Y Revenue CAGR", "value": f"{cagr:.1%}", "highlight": True})
    if summary.get("cagr_3y") is not None:
        kpis.append({"label": "3Y CAGR", "value": f"{summary['cagr_3y']:.1%}"})
    if estimates and estimates[0].yoy_growth is not None:
        kpis.append({"label": "Forward YoY (consensus)", "value": f"{estimates[0].yoy_growth:.1%}"})

    return {
        "type": "revenue_growth",
        "title": "Revenue & growth",
        "subtitle": "Capital IQ · INR millions",
        "kpis": kpis,
        "historical": [
            {
                "period": p.period_label[:32],
                "value_mn": p.value_mn,
                "yoy_pct": round(p.yoy_growth * 100, 1) if p.yoy_growth is not None else None,
            }
            for p in actuals[-5:]
        ],
        "estimates": [
            {
                "period": p.period_label[:32],
                "value_mn": p.value_mn,
                "yoy_pct": round(p.yoy_growth * 100, 1) if p.yoy_growth is not None else None,
            }
            for p in estimates[:3]
        ],
    }


def _build_consensus_section(session: ResearchSession) -> dict[str, Any]:
    est = get_estimates_summary(session)
    lines: list[dict[str, Any]] = []

    if est.get("target_price_mean"):
        lines.append(
            {
                "label": "Consensus target price",
                "value": f"₹{est['target_price_mean']:,.0f}/share",
                "detail": f"Median ₹{est.get('target_price_median', 0):,.0f}" if est.get("target_price_median") else None,
            }
        )
    if est.get("consensus_recommendation"):
        lines.append({"label": "Recommendation", "value": str(est["consensus_recommendation"])})

    for cm in [c for c in session.consensus_metrics if c.actual is None][:8]:
        if cm.mean is not None:
            lines.append(
                {
                    "label": f"{cm.metric} ({cm.period[:20]})",
                    "value": f"{cm.mean:,.2f}",
                    "detail": f"median {cm.median:,.2f}" if cm.median else None,
                }
            )

    return {
        "type": "consensus",
        "title": "Consensus estimates",
        "subtitle": "Capital IQ Estimates",
        "items": lines[:10],
    }


def _build_multiples_section(session: ResearchSession) -> dict[str, Any] | None:
    table = get_multiples_table(session)
    trailing_pe = _latest_pe_from_financials(session.financial_line_items)

    payload = build_multiples_response(session, period="NTM", multiple_type="pe")
    if not payload and not trailing_pe:
        return None

    extra_kpis = []
    if trailing_pe is not None:
        extra_kpis.append({"label": "Trailing / reported P/E", "value": f"{trailing_pe:.1f}x"})

    if session.market_quote and session.market_quote.price:
        src = _price_source_label(session.market_quote.source)
        extra_kpis.append(
            {
                "label": f"Market price ({src})",
                "value": f"₹{session.market_quote.price:,.0f}",
            }
        )

    if payload:
        payload["title"] = "Valuation multiples"
        payload["subtitle"] = "Forward P/E from Capital IQ Multiples sheet"
        payload["kpis"] = extra_kpis + (payload.get("kpis") or [])
        payload["trailing_pe"] = trailing_pe
        return payload

    return {
        "type": "valuation_multiples",
        "title": "Valuation multiples",
        "subtitle": "Upload Estimates export for forward P/E",
        "kpis": extra_kpis,
        "rows": [],
        "trailing_pe": trailing_pe,
    }


def _build_guidance_section(session: ResearchSession) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    track_headline = None
    try:
        from iis.finance.guidance_track import guidance_track_rows, guidance_track_summary

        rows = guidance_track_rows(session.guidance_records, session.financial_line_items)
        track_headline = guidance_track_summary(rows).get("headline")
        for r in rows[:8]:
            if r.get("outcome"):
                text = (
                    f"{r['fiscal_year']} {r['metric']}: guided {r['guidance_fmt']}, "
                    f"actual {r['actual_fmt']} ({r['outcome']}"
                    + (f", {r['delta_label']}" if r.get("delta_label") else "")
                    + ")"
                )
            elif r.get("guidance") is not None:
                text = f"{r['fiscal_year']} {r['metric']}: management guidance {r['guidance_fmt']}"
                if r.get("consensus") is not None:
                    text += f" vs consensus {r['consensus_fmt']}"
            else:
                continue
            items.append(
                {
                    "fiscal_year": r["fiscal_year"],
                    "metric": r["metric"],
                    "guidance": r.get("guidance"),
                    "actual": r.get("actual"),
                    "consensus": r.get("consensus"),
                    "outcome": r.get("outcome"),
                    "text": text,
                }
            )
    except Exception:
        pivoted = pivot_guidance(session.guidance_records)
        for (_base, fy), bucket in sorted(pivoted.items(), key=lambda x: x[0][1], reverse=True)[:6]:
            g, a, c = bucket.get("guidance"), bucket.get("actual"), bucket.get("consensus")
            metric = bucket["metric"]
            if g is not None and a is not None:
                diff = ((a - g) / abs(g) * 100) if g else None
                outcome = "met" if diff is not None and diff >= -3 else "missed"
                items.append(
                    {
                        "fiscal_year": fy,
                        "metric": metric,
                        "guidance": g,
                        "actual": a,
                        "outcome": outcome,
                        "text": f"{fy} {metric}: guided {g:,.0f}, actual {a:,.0f} ({outcome})",
                    }
                )
            elif g is not None:
                text = f"{fy} {metric}: management guidance {g:,.0f}"
                if c is not None:
                    text += f" vs consensus {c:,.0f}"
                items.append({"fiscal_year": fy, "metric": metric, "guidance": g, "consensus": c, "text": text})

    art = next((a for a in session.agent_artifacts if a.artifact_type == "guidance_summary"), None)
    themes: list[str] = []
    commentary = ""
    reflections: list[dict] = []
    current_outlook = ""
    if art:
        themes = list(art.payload.get("themes") or [])[:5]
        commentary = (art.payload.get("commentary") or "")[:500]
        current_outlook = (art.payload.get("current_outlook") or "")[:400]
        reflections = list(art.payload.get("guidance_reflections") or [])[:5]

    return {
        "type": "guidance",
        "title": "Management guidance",
        "subtitle": "Prior guidance vs actuals + concall reflection (after analyze)",
        "track_headline": track_headline,
        "items": items[:8],
        "themes": themes,
        "commentary_preview": commentary,
        "current_outlook": current_outlook,
        "reflections": reflections,
    }


def _build_porters_section(session: ResearchSession) -> dict[str, Any]:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "porters_five_forces"), None)
    labels = {
        "competitive_rivalry": "Competitive rivalry",
        "supplier_power": "Supplier power",
        "buyer_power": "Buyer power",
        "threat_of_substitutes": "Threat of substitutes",
        "threat_of_new_entrants": "Threat of new entrants",
    }

    if not art:
        return {
            "type": "porters",
            "title": "Porter's Five Forces",
            "subtitle": "Competitive landscape — from annual report & transcripts",
            "pending": True,
            "forces": [],
            "note": "Upload annual report or concall PDFs, then run **Analyze** to generate Porter's analysis.",
        }

    forces = [
        {
            "key": k,
            "label": labels.get(k, k.replace("_", " ").title()),
            "text": str(art.payload.get(k, "")),
        }
        for k in labels
    ]
    return {
        "type": "porters",
        "title": "Porter's Five Forces",
        "subtitle": "Annual report · concall · MD&A excerpts",
        "pending": False,
        "confidence": art.confidence,
        "sources": art.provenance[:4],
        "forces": forces,
    }


def _fmt_assumption_value(a) -> str:
    val = a.user_value if a.user_value is not None else a.value
    if isinstance(val, float) and a.unit == "decimal":
        if "margin" in a.key or "growth" in a.key or "rate" in a.key or "conversion" in a.key:
            return f"{val:.1%}"
        return f"{val:.2f}"
    return str(val)


def _build_mos_section(session: ResearchSession) -> dict[str, Any]:
    from iis.models.enums import GateId

    gate_ok = session.is_gate_approved(GateId.VALUATION_ASSUMPTIONS)
    v = session.valuation
    assumptions = session.assumptions

    if not assumptions and not v:
        return {
            "type": "valuation_mos",
            "title": "Valuation & margin of safety",
            "subtitle": "DCF model · Yahoo Finance live price for MoS",
            "pending": True,
            "note": "Run **Analyze** to propose DCF assumptions, then edit them in chat (no gate approval required).",
        }

    kpis: list[dict[str, Any]] = []
    preview = not gate_ok
    if v and v.intrinsic_value_per_share is not None:
        iv_label = "Intrinsic value (preview)" if preview else "Intrinsic value (DCF)"
        kpis.append({"label": iv_label, "value": f"₹{v.intrinsic_value_per_share:,.0f}/sh", "highlight": True})
        if v.margin_of_safety_pct is not None:
            mos_label = "Margin of safety (preview)" if preview else "Margin of safety"
            highlight = v.margin_of_safety_pct >= 0.25
            kpis.append(
                {
                    "label": mos_label,
                    "value": f"{v.margin_of_safety_pct:.1%}",
                    "highlight": highlight and not preview,
                }
            )
    elif assumptions:
        kpis.append({"label": "Status", "value": "Assumptions proposed", "highlight": True})

    price = None
    price_label = None
    if v and v.market_price:
        price, price_label = v.market_price, v.market_price_source or _price_source_label(None)
    elif session.market_quote:
        price = session.market_quote.price
        price_label = f"{session.market_quote.exchange} · {_price_source_label(session.market_quote.source)}"

    if price is not None:
        kpis.append({"label": "Market price", "value": f"₹{price:,.0f}/sh"})
    if v and v.current_price:
        kpis.append({"label": "Consensus target", "value": f"₹{v.current_price:,.0f}/sh"})

    assumption_rows = [
        {
            "key": a.key,
            "label": a.label,
            "value": _fmt_assumption_value(a),
            "raw_value": a.user_value if a.user_value is not None else a.value,
            "proposed": a.value,
            "unit": a.unit,
            "approved": a.approved,
            "rationale": (a.rationale or "")[:160],
            "editable": True,
            "needs_user_input": a.needs_user_input,
            "review_prompt": a.review_prompt,
        }
        for a in assumptions
    ]

    valuation_review = None
    if v and v.sensitivities:
        valuation_review = v.sensitivities.get("valuation_review")
    if not valuation_review:
        art = next((a for a in session.agent_artifacts if a.artifact_type == "valuation_review"), None)
        if art:
            valuation_review = art.payload

    fcf_projection = []
    fcf_note = None
    if v and v.sensitivities:
        fcf_projection = v.sensitivities.get("fcf_projection") or []
        fcf_note = v.sensitivities.get("fcf_note") or v.sensitivities.get("fcf_build_rationale")
        ss = (v.assumptions or {}).get("steady_state_fcf_to_ebitda")
        if ss and not fcf_note:
            fcf_note = f"When growth moderates, steady-state FCF ≈ {ss:.0%} of EBITDA."

    sensitivities = []
    assumption_bumps = []
    inverse_dcf = None

    if v and v.sensitivities:
        inv = v.sensitivities.get("inverse_dcf")
        if inv and inv.get("market_price"):
            inverse_dcf = inv

        bumps = v.sensitivities.get("assumption_bumps") or []
        for row in bumps:
            pct = row.get("iv_change_pct")
            if pct is None:
                continue
            assumption_bumps.append(
                {
                    "label": row["label"],
                    "iv_per_share": row.get("iv_per_share"),
                    "iv_change_pct": pct,
                    "mos_pct": row.get("mos_pct"),
                    "base_iv_per_share": row.get("base_iv_per_share"),
                }
            )

        tg_scenarios = v.sensitivities.get("terminal_growth_scenarios") or {}
        for k, val in list(tg_scenarios.items())[:4]:
            if val is not None:
                sensitivities.append({"scenario": k.replace("_", " "), "value": f"₹{val:,.0f}/sh"})
        if not tg_scenarios:
            for k, val in list(v.sensitivities.items())[:4]:
                if k in ("inverse_dcf", "assumption_bumps", "terminal_growth_scenarios"):
                    continue
                if val is not None and isinstance(val, (int, float)):
                    sensitivities.append({"scenario": k.replace("_", " "), "value": f"₹{val:,.0f}/sh"})

    note = None
    if valuation_review and valuation_review.get("needs_input_count", 0) > 0 and not gate_ok:
        note = valuation_review.get("co_create_note")
    elif assumptions and not gate_ok:
        note = "Review DCF assumptions below (edit in **Approval gates → Valuation**). Approve **Gate 4** to lock inputs for the research note."
    elif gate_ok and v and v.intrinsic_value_per_share is None:
        note = "Assumptions approved but intrinsic value could not be computed — check shares outstanding in financials."

    return {
        "type": "valuation_mos",
        "title": "Valuation & margin of safety",
        "subtitle": "5Y DCF · inverse DCF · assumption sensitivity",
        "pending": False,
        "gate_approved": gate_ok,
        "kpis": kpis,
        "assumptions": assumption_rows,
        "sensitivities": sensitivities,
        "assumption_bumps": assumption_bumps,
        "inverse_dcf": inverse_dcf,
        "fcf_projection": fcf_projection,
        "fcf_note": fcf_note,
        "valuation_review": valuation_review,
        "intrinsic_value_per_share": v.intrinsic_value_per_share if v else None,
        "note": note,
        "method": v.method if v else "dcf",
    }


def _build_flags_section(session: ResearchSession) -> dict[str, Any]:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "data_flags"), None)
    if art:
        payload = art.payload
    else:
        payload = flags_to_payload(compute_data_flags(session))

    review = [f for f in payload.get("flags", []) if f.get("severity") == "review"]
    return {
        "type": "data_flags",
        "title": "Data flags to verify",
        "subtitle": "Plain-language anomalies — check in Capital IQ",
        "total": payload.get("total", 0),
        "review_count": payload.get("review_count", len(review)),
        "flags": review[:8] or payload.get("flags", [])[:5],
    }


def build_research_briefing(session: ResearchSession) -> dict[str, Any]:
    """Step-1 narrative: who the company is, what we ingested, what we found so far."""
    from pathlib import Path

    from iis.models.enums import GateId

    co = session.company
    name = co.name if co else "Unknown company"
    identity_bits = []
    if co and co.nse_ticker:
        identity_bits.append(f"NSE {co.nse_ticker}")
    if co and co.bse_code:
        identity_bits.append(f"BSE {co.bse_code}")
    identity = " · ".join(identity_bits) if identity_bits else "Identity from uploaded Capital IQ workbooks"

    sources: list[dict[str, str]] = []
    for path in session.uploaded_files or []:
        fname = Path(path).name
        lower = fname.lower()
        kind = "Document"
        if lower.endswith((".xls", ".xlsx")):
            kind = "Estimates" if "estimate" in lower else "Capital IQ Financials"
        elif lower.endswith(".pdf"):
            if any(k in lower for k in ("ar", "annual")):
                kind = "Annual report"
            elif any(k in lower for k in ("concall", "transcript", "earnings call")):
                kind = "Concall / transcript"
            elif "presentation" in lower or "investor" in lower:
                kind = "Presentation"
            else:
                kind = "PDF"
        sources.append({"kind": kind, "filename": fname})

    for doc in session.pdf_documents or []:
        fname = getattr(doc, "file", None) or getattr(doc, "filename", None) or "PDF"
        if isinstance(fname, str) and "/" in fname:
            fname = Path(fname).name
        if not any(s["filename"] == fname for s in sources):
            sources.append({"kind": getattr(doc, "doc_type", None) or "PDF", "filename": str(fname)})

    items = session.financial_line_items or []
    metrics = get_key_metrics_summary(items) if items else {}
    periods = sorted(
        {i.period_label for i in items if getattr(i, "period_label", None)},
        reverse=True,
    )[:8]

    analysis_done: list[str] = []
    if items:
        analysis_done.append(
            f"Parsed Capital IQ financials → {len(items)} line items"
            + (f" across {len(periods)} periods" if periods else "")
        )
    if session.consensus_metrics or get_estimates_summary(session):
        analysis_done.append("Loaded broker consensus / Capital IQ estimates")
    if session.pdf_chunks or session.pdf_documents:
        n_pdf = len(session.pdf_documents) or len({c.file for c in session.pdf_chunks})
        analysis_done.append(f"Indexed {n_pdf} PDF document(s) for Q&A ({len(session.pdf_chunks)} text chunks)")
    if session.guidance_records:
        analysis_done.append(f"Extracted {len(session.guidance_records)} guidance datapoints")
    art_types = {a.artifact_type for a in session.agent_artifacts}
    if "porters_five_forces" in art_types:
        analysis_done.append("Ran Porter's Five Forces synthesis")
    if "normalized_financials" in art_types:
        analysis_done.append("Built normalized ratios / statements")
    if session.assumptions:
        analysis_done.append(f"Proposed {len(session.assumptions)} DCF assumptions (editable)")
    if session.valuation and session.valuation.intrinsic_value_per_share is not None:
        analysis_done.append("Computed FCF DCF intrinsic value and margin of safety vs market")
    if not analysis_done:
        analysis_done.append("Waiting for Capital IQ Financials upload to begin extraction")

    findings: list[str] = []
    if metrics.get("revenue_mn"):
        findings.append(f"Latest revenue about ₹{metrics['revenue_mn']:,.0f} mn")
    if metrics.get("ebitda_margin") is not None:
        findings.append(f"EBITDA margin ~{metrics['ebitda_margin']:.1%}")
    if metrics.get("revenue_cagr_3y") is not None:
        findings.append(f"3Y revenue CAGR ~{metrics['revenue_cagr_3y']:.1%}")
    est = get_estimates_summary(session)
    if est.get("target_price_mean"):
        findings.append(f"Consensus target ~₹{est['target_price_mean']:,.0f}")
    table = get_multiples_table(session)
    ntm = table.get("NTM") or (table.get(list(table.keys())[0]) if table else None)
    if isinstance(ntm, dict) and ntm.get("pe"):
        findings.append(f"Forward P/E (NTM) ~{ntm['pe']:.1f}x")

    flags_payload = _build_flags_section(session)
    flag_items: list[dict[str, Any]] = []
    for f in (flags_payload.get("flags") or [])[:8]:
        headline = f.get("headline") or f.get("title") or f.get("id") or "Data flag"
        explanation = f.get("explanation") or f.get("message") or f.get("detail") or ""
        verify = f.get("verify") or []
        ask = f"Explain this data flag in plain English and what I should verify: {headline}"
        flag_items.append(
            {
                "id": f.get("id"),
                "severity": f.get("severity") or "review",
                "category": f.get("category"),
                "headline": headline,
                "explanation": explanation,
                "verify": verify,
                "figures": f.get("figures") or [],
                "ask": ask,
            }
        )
    if flag_items:
        findings.append(f"{len(flag_items)} data flag(s) need a look before you lean on the numbers")

    has_assumptions = bool(session.assumptions)
    has_report = bool(getattr(session, "research_report_md", None))
    g1 = session.is_gate_approved(GateId.EXTRACTED_FINANCIALS)
    g4 = session.is_gate_approved(GateId.VALUATION_ASSUMPTIONS)

    freshness = None
    try:
        from iis.finance.data_freshness import assess_data_freshness

        freshness = assess_data_freshness(session)
    except Exception:
        pass

    if freshness and freshness.get("needs_update"):
        next_step = {
            "id": "refresh_ciq",
            "title": "Update stale Capital IQ files",
            "detail": (freshness.get("asks") or ["Re-download Financials / Estimates from CIQ and upload."])[0],
        }
    elif not items:
        next_step = {
            "id": "upload",
            "title": "Upload Capital IQ Financials",
            "detail": "Drop the Financials.xls (and Estimates) so we can brief you with real numbers.",
        }
    elif not has_assumptions:
        next_step = {
            "id": "run_analysis",
            "title": "Run analysis",
            "detail": "Skim the numbers in chat if you like, then Run Analysis for DCF assumptions you can edit.",
        }
    elif not has_report:
        next_step = {
            "id": "report",
            "title": "Edit valuation in chat → generate report",
            "detail": "Say e.g. set WACC to 11%, then Generate Research Report.",
        }
    else:
        next_step = {
            "id": "finalize",
            "title": "Finalize to Word when ready",
            "detail": "Regenerate the report after chat edits, then export .docx to Drive.",
        }

    headline = (
        f"{name} — research pack from your uploads"
        + (f" ({identity})" if identity_bits else "")
        + "."
    )
    narrative = (
        f"We are co-creating research on **{name}**. "
        "Nothing here is a live Capital IQ feed — everything below comes from the files you uploaded. "
        "Flow: review in chat → Run Analysis → tweak assumptions in chat → Generate Research Report → Finalize to Word."
    )

    return {
        "headline": headline,
        "narrative": narrative,
        "company": {
            "name": name,
            "identity": identity,
            "nse_ticker": co.nse_ticker if co else None,
            "bse_code": co.bse_code if co else None,
        },
        "basis": {
            "title": "Basis of this research",
            "points": [
                identity,
                f"{len(sources)} source file(s) in this session" if sources else "Sources pending upload",
                f"{len(items)} financial line items extracted" if items else "No financial line items yet",
                f"Periods seen: {', '.join(periods[:5])}" if periods else "Periods will appear after Financials.xls is parsed",
            ],
        },
        "sources": sources[:20],
        "analysis_done": {
            "title": "What we already did",
            "points": analysis_done,
        },
        "findings": {
            "title": "What we found so far",
            "points": findings or ["Upload / parse Financials to populate headline findings."],
            "flags": [f"{x['headline']}: {x['explanation']}" for x in flag_items],
            "flag_items": flag_items,
        },
        "next_step": next_step,
        "gates": {
            "financials_approved": g1,
            "valuation_approved": g4,
            "has_assumptions": has_assumptions,
        },
        "data_freshness": {
            "needs_update": bool(freshness and freshness.get("needs_update")),
            "has_warnings": bool(freshness and freshness.get("has_warnings")),
            "user_ask": (freshness or {}).get("user_ask"),
            "asks": (freshness or {}).get("asks") or [],
            "latest_actual_period": (freshness or {}).get("latest_actual_period"),
        }
        if freshness
        else None,
    }


def build_session_overview(session: ResearchSession) -> dict[str, Any]:
    """Structured overview shown when user opens a session after ingest."""
    company = session.company.name if session.company else "Unknown"
    items = session.financial_line_items
    has_data = bool(items) or bool(session.guidance_records) or bool(session.consensus_metrics)

    overview: dict[str, Any] = {
        "type": "session_overview",
        "company": company,
        "bse_code": session.company.bse_code if session.company else None,
        "nse_ticker": session.company.nse_ticker if session.company else None,
        "status": session.status.value,
        "has_data": has_data,
        "summary_text": _overview_intro(session),
        "briefing": build_research_briefing(session),
        "sections": [],
    }

    if not has_data:
        overview["sections"] = []
        overview["headline_kpis"] = []
        return overview

    movement = _build_financial_movement(items) if items else None
    if movement and movement.get("rows"):
        overview["sections"].append({"id": "financials", **movement})

    revenue = _build_revenue_section(items) if items else None
    if revenue:
        overview["sections"].append({"id": "revenue", **revenue})

    if session.consensus_metrics or get_estimates_summary(session):
        overview["sections"].append({"id": "consensus", **_build_consensus_section(session)})

    multiples = _build_multiples_section(session)
    if multiples:
        overview["sections"].append({"id": "multiples", **multiples})

    if session.guidance_records or any(
        a.artifact_type == "guidance_summary" for a in session.agent_artifacts
    ):
        overview["sections"].append({"id": "guidance", **_build_guidance_section(session)})

    overview["sections"].append({"id": "porters", **_build_porters_section(session)})
    overview["sections"].append({"id": "mos", **_build_mos_section(session)})

    flags = _build_flags_section(session)
    if flags.get("total", 0) > 0 or flags.get("flags"):
        overview["sections"].append({"id": "flags", **flags})

    metrics = get_key_metrics_summary(items) if items else {}
    overview["headline_kpis"] = _headline_kpis(session, metrics)

    return overview


def _headline_kpis(session: ResearchSession, metrics: dict) -> list[dict[str, str]]:
    kpis: list[dict[str, str]] = []
    if metrics.get("revenue_mn"):
        kpis.append({"label": "Latest revenue", "value": f"₹{metrics['revenue_mn']:,.0f} mn"})
    if metrics.get("ebitda_margin") is not None:
        kpis.append({"label": "EBITDA margin", "value": f"{metrics['ebitda_margin']:.1%}"})
    if metrics.get("revenue_cagr_3y") is not None:
        kpis.append({"label": "3Y revenue CAGR", "value": f"{metrics['revenue_cagr_3y']:.1%}"})

    est = get_estimates_summary(session)
    if est.get("target_price_mean"):
        kpis.append({"label": "Target price", "value": f"₹{est['target_price_mean']:,.0f}"})

    table = get_multiples_table(session)
    ntm = table.get("NTM") or (table.get(list(table.keys())[0]) if table else None)
    if isinstance(ntm, dict) and ntm.get("pe"):
        kpis.append({"label": "Forward P/E (NTM)", "value": f"{ntm['pe']:.1f}x"})

    flags_art = next((a for a in session.agent_artifacts if a.artifact_type == "data_flags"), None)
    rc = flags_art.payload.get("review_count", 0) if flags_art else len(
        [f for f in compute_data_flags(session) if f.severity == "review"]
    )
    if rc:
        kpis.append({"label": "Flags to review", "value": str(rc), "highlight": True})

    v = session.valuation
    from iis.models.enums import GateId

    if session.is_gate_approved(GateId.VALUATION_ASSUMPTIONS) and v and v.intrinsic_value_per_share:
        kpis.append({"label": "Intrinsic value", "value": f"₹{v.intrinsic_value_per_share:,.0f}"})
    if v and v.margin_of_safety_pct is not None and session.is_gate_approved(GateId.VALUATION_ASSUMPTIONS):
        kpis.append({"label": "MoS", "value": f"{v.margin_of_safety_pct:.0%}"})

    return kpis[:8]


def _overview_intro(session: ResearchSession) -> str:
    briefing = build_research_briefing(session)
    return briefing.get("narrative") or (
        f"Research overview for **{briefing.get('company', {}).get('name', 'this company')}**."
    )
