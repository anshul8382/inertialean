"""Critique — investor checklists computed in Python from CIQ data and filing text.

Verdicts are rule-based (thresholds shown next to every item). Governance items are text
evidence only (file + page), never a judgement. The LLM may explain these results but does
not change them.
"""
from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any

from iis.finance.reconciler import annual_series
from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession


def _item(name: str, verdict: str, value: str, rule: str, evidence: str = "") -> dict[str, Any]:
    return {"item": name, "verdict": verdict, "value": value, "rule": rule, "evidence": evidence}


def _band(v: float | None, good: float, bad: float, *, higher_is_better: bool = True) -> str:
    if v is None:
        return "n/a"
    if higher_is_better:
        return "pass" if v >= good else ("fail" if v < bad else "watch")
    return "pass" if v <= good else ("fail" if v > bad else "watch")


def _last(series: dict[int, float], n: int) -> dict[int, float]:
    keys = sorted(series)[-n:]
    return {k: series[k] for k in keys}


def _ratio_series(num: dict[int, float], den: dict[int, float]) -> dict[int, float]:
    return {y: num[y] / den[y] for y in num if y in den and den[y]}


def _fy(y: int) -> str:
    return f"FY{str(y)[2:]}"


# ── checklists ───────────────────────────────────────────────────────────


def accounting_quality(session: ResearchSession) -> list[dict[str, Any]]:
    it = session.financial_line_items
    pat = annual_series(it, "net_income", "income_statement")
    cfo = annual_series(it, "cash_from_ops", "cash_flow")
    rev = annual_series(it, "total_revenue", "income_statement")
    ta = annual_series(it, "total_assets", "balance_sheet")
    ar = annual_series(it, "accounts_receivable", "balance_sheet") or annual_series(it, "total_receivables", "balance_sheet")
    inv = annual_series(it, "inventory", "balance_sheet")
    cogs = annual_series(it, "cost_of_goods_sold", "income_statement")
    ebt = annual_series(it, "ebt_incl_unusual_items", "income_statement")
    oth = annual_series(it, "interest_and_invest_income", "income_statement")
    unusual = annual_series(it, "other_unusual_items", "income_statement")
    out: list[dict[str, Any]] = []

    yrs = [y for y in sorted(pat) if y in cfo][-3:]
    if yrs:
        r = sum(cfo[y] for y in yrs) / sum(pat[y] for y in yrs) if sum(pat[y] for y in yrs) else None
        out.append(_item("Cash conversion (3Y CFO / PAT)", _band(r, 0.8, 0.5), f"{r:.2f}x" if r is not None else "—",
                         "pass ≥ 0.8x, fail < 0.5x", f"{_fy(yrs[0])}–{_fy(yrs[-1])} cumulative"))
    y = max((y for y in pat if y in cfo and y in ta and (y - 1) in ta), default=None)
    if y:
        acc = (pat[y] - cfo[y]) / ((ta[y] + ta[y - 1]) / 2)
        out.append(_item("Accruals ratio ((PAT − CFO) / avg assets)", _band(acc, 0.05, 0.10, higher_is_better=False),
                         f"{acc:+.1%}", "pass ≤ 5%, fail > 10%", _fy(y)))
    dso = {y: ar[y] / rev[y] * 365 for y in ar if y in rev and rev[y]}
    if len(dso) >= 3:
        ys = sorted(dso)
        prior = statistics.mean(dso[y] for y in ys[-4:-1])
        delta = dso[ys[-1]] - prior
        out.append(_item("Receivable days vs prior 3Y avg", _band(delta, 10, 25, higher_is_better=False),
                         f"{dso[ys[-1]]:.0f} days ({delta:+.0f})", "pass ≤ +10 days, fail > +25", _fy(ys[-1])))
    dio = {y: inv[y] / cogs[y] * 365 for y in inv if y in cogs and cogs[y]}
    if len(dio) >= 3:
        ys = sorted(dio)
        prior = statistics.mean(dio[y] for y in ys[-4:-1])
        delta = dio[ys[-1]] - prior
        out.append(_item("Inventory days vs prior 3Y avg", _band(delta, 15, 35, higher_is_better=False),
                         f"{dio[ys[-1]]:.0f} days ({delta:+.0f})", "pass ≤ +15 days, fail > +35", _fy(ys[-1])))
    y = max((y for y in oth if y in ebt and ebt[y]), default=None)
    if y:
        share = oth[y] / ebt[y]
        out.append(_item("Interest & investment income share of PBT", _band(share, 0.10, 0.25, higher_is_better=False),
                         f"{share:.0%}", "pass ≤ 10%, fail > 25%", _fy(y)))
    if unusual and ebt:
        ys = [y for y in sorted(unusual) if y in ebt and ebt[y]][-5:]
        hits = [y for y in ys if abs(unusual[y]) / abs(ebt[y]) > 0.05]
        if ys:
            out.append(_item("Recurring 'unusual' items (> 5% of PBT)", "pass" if len(hits) <= 1 else ("watch" if len(hits) == 2 else "fail"),
                             f"{len(hits)} of {len(ys)} years", "pass ≤ 1 year, fail ≥ 3 years",
                             ", ".join(_fy(y) for y in hits)))
    return out


def balance_sheet(session: ResearchSession) -> list[dict[str, Any]]:
    it = session.financial_line_items
    debt = annual_series(it, "total_debt", "balance_sheet")
    cash = annual_series(it, "total_cash_st_investments", "balance_sheet") or annual_series(it, "cash_and_equivalents", "balance_sheet")
    ebitda = annual_series(it, "ebitda", "income_statement")
    ebit = annual_series(it, "ebit", "income_statement")
    interest = annual_series(it, "interest_expense", "income_statement")
    eq = annual_series(it, "total_equity", "balance_sheet")
    ca = annual_series(it, "total_current_assets", "balance_sheet")
    cl = annual_series(it, "total_current_liabilities", "balance_sheet")
    out: list[dict[str, Any]] = []
    y = max((y for y in debt if y in ebitda and ebitda[y]), default=None)
    if y:
        nd = debt[y] - cash.get(y, 0.0)
        r = nd / ebitda[y]
        out.append(_item("Net debt / EBITDA", _band(r, 1.5, 3.0, higher_is_better=False), f"{r:.2f}x",
                         "pass ≤ 1.5x, fail > 3x", f"{_fy(y)} net debt ₹{nd:,.0f} mn"))
    y = max((y for y in ebit if y in interest and interest[y]), default=None)
    if y:
        cov = ebit[y] / abs(interest[y])
        out.append(_item("Interest coverage (EBIT / interest)", _band(cov, 5, 2), f"{cov:.1f}x", "pass ≥ 5x, fail < 2x", _fy(y)))
    y = max((y for y in debt if y in eq and eq[y]), default=None)
    if y:
        de = debt[y] / eq[y]
        out.append(_item("Debt / equity", _band(de, 0.5, 1.0, higher_is_better=False), f"{de:.2f}x", "pass ≤ 0.5x, fail > 1x", _fy(y)))
    y = max((y for y in ca if y in cl and cl[y]), default=None)
    if y:
        cr = ca[y] / cl[y]
        out.append(_item("Current ratio", _band(cr, 1.2, 0.9), f"{cr:.2f}x", "pass ≥ 1.2x, fail < 0.9x", _fy(y)))
    return out


def moat_quality(session: ResearchSession) -> list[dict[str, Any]]:
    it = session.financial_line_items
    rev = annual_series(it, "total_revenue", "income_statement")
    ebitda = annual_series(it, "ebitda", "income_statement")
    gp = annual_series(it, "gross_profit", "income_statement")
    roc = annual_series(it, "return_on_capital_", "ratios")
    roe = annual_series(it, "return_on_equity_", "ratios")
    capex = annual_series(it, "capital_expenditure", "cash_flow")
    out: list[dict[str, Any]] = []

    def _pct_series(s: dict[int, float]) -> dict[int, float]:
        return {y: (v / 100 if abs(v) > 1.5 else v) for y, v in s.items()}

    for label, series, good, bad in (("Return on capital (CIQ, after-tax EBIT ÷ total capital)", roc, 0.15, 0.10), ("Return on equity (CIQ)", roe, 0.15, 0.10)):
        s = _last(_pct_series(series), 5)
        if len(s) >= 3:
            avg = statistics.mean(s.values())
            low_y = min(s, key=s.get)
            out.append(_item(f"{label} — {len(s)}Y average", _band(avg, good, bad), f"{avg:.1%} (low {s[low_y]:.1%} in {_fy(low_y)})",
                             f"pass ≥ {good:.0%}, fail < {bad:.0%}", ", ".join(f"{_fy(y)} {v:.1%}" for y, v in s.items())))
    m = _last(_ratio_series(ebitda, rev), 5)
    if len(m) >= 3:
        sd = statistics.pstdev(m.values())
        out.append(_item("EBITDA margin stability (std dev)", _band(sd, 0.03, 0.06, higher_is_better=False),
                         f"{sd * 100:.1f} pp around {statistics.mean(m.values()):.1%}", "pass ≤ 3pp, fail > 6pp",
                         ", ".join(f"{_fy(y)} {v:.1%}" for y, v in m.items())))
    g = _last(_ratio_series(gp, rev), 5)
    if len(g) >= 3:
        out.append(_item("Gross margin level", _band(statistics.mean(g.values()), 0.40, 0.25),
                         f"{statistics.mean(g.values()):.1%} avg", "pass ≥ 40%, fail < 25% (pricing power proxy)",
                         ", ".join(f"{_fy(y)} {v:.1%}" for y, v in g.items())))
    ys = sorted(rev)
    if len(ys) >= 4:
        n = min(5, len(ys) - 1)
        a, b = rev[ys[-1 - n]], rev[ys[-1]]
        if a > 0:
            cagr = (b / a) ** (1 / n) - 1
            out.append(_item(f"Revenue CAGR ({n}Y)", _band(cagr, 0.10, 0.05), f"{cagr:.1%}", "pass ≥ 10%, fail < 5%",
                             f"{_fy(ys[-1 - n])} ₹{a:,.0f} mn → {_fy(ys[-1])} ₹{b:,.0f} mn"))
    ci = _last(_ratio_series({y: abs(v) for y, v in capex.items()}, rev), 5)
    if ci:
        out.append(_item("Capex intensity (capex / sales, avg)", "info", f"{statistics.mean(ci.values()):.1%}",
                         "context only", ", ".join(f"{_fy(y)} {v:.1%}" for y, v in ci.items())))
    return out


def management_credibility(session: ResearchSession) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        from iis.finance.guidance_track import guidance_track_rows

        rows = guidance_track_rows(session.guidance_records, session.financial_line_items)
    except Exception:
        rows = []
    scored = [r for r in rows if r.get("outcome") and not r.get("spend_metric")]
    spend = [r for r in rows if r.get("outcome") and r.get("spend_metric")]
    if scored:
        met = sum(1 for r in scored if r["outcome"] == "met/beat")
        rate = met / len(scored)
        out.append(_item("Guidance met/beat (CIQ guidance vs actual)", _band(rate, 0.7, 0.4), f"{met}/{len(scored)} ({rate:.0%})",
                         "pass ≥ 70%, fail < 40%",
                         "; ".join(f"{r['metric']} {r['fiscal_year']}: {r['outcome']} ({r.get('delta_label')})" for r in scored[:6])))
    if spend:
        ok = sum(1 for r in spend if r["outcome"] != "spent above guidance")
        rate = ok / len(spend)
        out.append(_item("Spend discipline (capex/costs within guidance)", _band(rate, 0.7, 0.4), f"{ok}/{len(spend)} ({rate:.0%})",
                         "pass ≥ 70% within or below guidance, fail < 40%",
                         "; ".join(f"{r['metric']} {r['fiscal_year']}: {r['outcome']} ({r.get('delta_label')})" for r in spend[:6])))
    if not scored and not spend:
        out.append(_item("Guidance track record", "n/a", "—", "needs CIQ Guidance sheet with actuals"))
    return out


_GOV_TERMS: list[tuple[str, str]] = [
    ("Auditor qualification / emphasis of matter", r"qualified opinion|basis for qualified|emphasis of matter|adverse opinion|disclaimer of opinion"),
    ("Key audit matters", r"key audit matters?"),
    ("Related party transactions", r"related party transactions?"),
    ("Contingent liabilities", r"contingent liabilit"),
    ("Promoter pledge", r"pledge"),
    ("Auditor resignation / change", r"auditor.{0,40}(resign|change|casual vacancy)"),
]


def governance_evidence(session: ResearchSession) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ar = [c for c in session.pdf_chunks if c.doc_type == "annual_report"]
    for label, pat in _GOV_TERMS:
        rx = re.compile(pat, re.I)
        hits = [c for c in ar if rx.search(c.text or "")]
        if not hits:
            out.append(_item(label, "n/a", "not found in annual report text", "evidence only"))
            continue
        c = hits[0]
        m = rx.search(c.text or "")
        snip = re.sub(r"\s+", " ", (c.text or "")[max(0, m.start() - 100) : m.end() + 180]).strip()
        pages = sorted({h.page for h in hits})[:6]
        out.append(_item(label, "review", f"{len(hits)} passage(s), pages {', '.join(map(str, pages))}",
                         "evidence only — read the passage", f"{Path(c.file).name} p.{c.page}: …{snip}…"))
    # Promoter pledge as stated in broker shareholding tables
    for c in session.pdf_chunks:
        if c.doc_type != "brokerage_report" or c.page != 1:
            continue
        m = re.search(r"pledge[^\n]{0,30}?(\d+(?:\.\d+)?)\s*%|promoter pledge\s+(nil)", c.text or "", re.I)
        if m:
            val = (m.group(1) or m.group(2) or "").lower()
            zero = val in ("nil", "0", "0.0", "0.00")
            out.append(_item("Promoter pledge (broker shareholding table)", "pass" if zero else "watch",
                             "nil" if zero else f"{val}%", "pass = nil", f"{Path(c.file).name} p.1"))
            break
    return out


def run_critique(session: ResearchSession) -> dict[str, Any]:
    lists = {
        "accounting_quality": ("Accounting quality", accounting_quality(session)),
        "balance_sheet": ("Balance sheet strength", balance_sheet(session)),
        "moat": ("Moat / business quality", moat_quality(session)),
        "management": ("Management credibility", management_credibility(session)),
        "governance": ("Governance (text evidence)", governance_evidence(session)),
    }
    summary = {}
    for key, (_label, items) in lists.items():
        summary[key] = {v: sum(1 for i in items if i["verdict"] == v) for v in ("pass", "watch", "fail")}
    research_questions: list[str] = []
    qual_vs_numbers: list = []
    business_detail: dict[str, Any] = {}
    try:
        from iis.finance.business_checklist import build_business_checklist

        # Checklist is Python-only (slots). Do NOT call build_business_overview
        # here — that triggers LLM overview review and was hanging analyze for
        # many minutes (called twice via questions + qual_vs_numbers).
        business_detail = build_business_checklist(session)
        research_questions = list(business_detail.get("open_asks") or [])[:12]
    except Exception:
        business_detail = {}
    try:
        from iis.finance.business_overview import build_qual_vs_numbers

        qual_vs_numbers = build_qual_vs_numbers(session)
    except Exception:
        qual_vs_numbers = []
    return {
        "checklists": {k: {"label": lbl, "items": items} for k, (lbl, items) in lists.items()},
        "summary": summary,
        "research_questions": research_questions,
        "qual_vs_numbers": qual_vs_numbers,
        "business_detail_checklist": {
            "coverage_pct": business_detail.get("coverage_pct"),
            "summary": business_detail.get("summary"),
            "items": business_detail.get("items") or [],
            "open_asks": business_detail.get("open_asks") or [],
        },
    }


def apply_critique(session: ResearchSession) -> ResearchSession:
    try:
        payload = run_critique(session)
    except Exception as exc:
        payload = {"checklists": {}, "error": str(exc)}
    session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "critique"]
    session.agent_artifacts.append(
        AgentArtifact(agent_id="critique", artifact_type="critique", payload=payload, source_tier=SourceTier.EXTRACTED)
    )
    return session


_V = {"pass": "✓ pass", "watch": "⚠ watch", "fail": "✗ fail", "n/a": "– n/a", "info": "· info", "review": "◎ review"}


def critique_markdown(session: ResearchSession) -> list[str]:
    p = next((a.payload for a in session.agent_artifacts if a.artifact_type == "critique"), None)
    if not p or not p.get("checklists"):
        return []
    lines: list[str] = []
    for block in p["checklists"].values():
        if not block["items"]:
            continue
        lines += [f"#### {block['label']}", "", "| Item | Verdict | Value | Rule | Evidence |", "|---|---|---|---|---|"]
        for i in block["items"]:
            ev = (i.get("evidence") or "").replace("|", "/")
            lines.append(f"| {i['item']} | {_V.get(i['verdict'], i['verdict'])} | {i['value']} | {i['rule']} | {ev[:220]} |")
        lines.append("")
    lines.append("_Verdicts are fixed rules applied to CIQ data; governance rows only point to passages to read._")
    lines.append("")
    # Prefer a live rebuild so IEPF/CSR/AGM junk filtered after code updates
    bd = p.get("business_detail_checklist") or {}
    try:
        from iis.finance.business_checklist import build_business_checklist

        live = build_business_checklist(session)
        if live.get("items"):
            bd = live
    except Exception:
        pass
    if bd.get("items"):
        icon = {"found": "✓", "partial": "◐", "missing": "✗"}
        s = bd.get("summary") or {}
        try:
            from iis.finance.business_overview import _filter_snips, _is_accounting_junk
        except Exception:
            _filter_snips = None  # type: ignore
            _is_accounting_junk = None  # type: ignore
        lines += [
            "#### Business detail checklist (from documents)",
            "",
            f"_Coverage {bd.get('coverage_pct', 0)}% — "
            f"found {s.get('found', 0)} · partial {s.get('partial', 0)} · missing {s.get('missing', 0)}_",
            "",
            "| Status | Detail | Evidence |",
            "|---|---|---|",
        ]
        for it in bd["items"]:
            snips = it.get("snippets") or []
            if _filter_snips is not None:
                snips = _filter_snips(snips, n=1)
            ev = " — ".join(snips[:1]) or "—"
            if _is_accounting_junk is not None and _is_accounting_junk(ev):
                ev = "—"
            ev = ev.replace("|", "/")[:140]
            lines.append(
                f"| {icon.get(it.get('status'), '?')} {it.get('status')} | {it.get('item')} | {ev} |"
            )
        lines.append("")
    qs = p.get("research_questions") or []
    if qs:
        lines += ["#### Research questions (carry forward)", ""]
        for i, q in enumerate(qs, 1):
            lines.append(f"{i}. {q}")
        lines.append("")
    qv = p.get("qual_vs_numbers") or []
    if qv:
        lines += ["#### Qualitative vs numbers", "", "| Claim | Numbers | Verdict |", "|---|---|---|"]
        for r in qv:
            lines.append(f"| {r.get('claim')} | {r.get('evidence')} | {r.get('verdict')} |")
        lines.append("")
    return lines
