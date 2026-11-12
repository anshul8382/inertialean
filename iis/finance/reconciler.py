"""Reconciler — internal consistency of the collected data before anyone interprets it.

Each check returns status ok | warn | fail | n/a with the numbers used, so the report can show
exactly why a figure is trusted or not. Failures and missing inputs become a gap list that the
User Communicator turns into asks.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from iis.models.artifacts import AgentArtifact, FinancialLineItem
from iis.models.enums import PeriodType, SourceTier
from iis.models.session import ResearchSession


def _fy_end_month(items: list[FinancialLineItem]) -> int:
    months: dict[int, int] = {}
    for i in items:
        if i.line_item == "total_revenue" and i.period_end and "12 months" in (i.period_label or "") and "LTM" not in (i.period_label or ""):
            m = int(i.period_end[5:7])
            months[m] = months.get(m, 0) + 1
    return max(months, key=months.get) if months else 3


def annual_series(items: list[FinancialLineItem], line_item: str, statement: str | None = None) -> dict[int, float]:
    """FY → value for reported annual actuals (no LTM, estimates or press-release columns).

    CIQ labels balance-sheet columns with Excel serial dates and some years as 'Restated 12 months',
    so annual = period ends on the fiscal-year-end month.
    """
    fy_month = _fy_end_month(items)
    out: dict[int, float] = {}
    for i in items:
        if i.line_item != line_item or i.value is None or not i.period_end or len(i.period_end) < 7:
            continue
        if statement and i.statement != statement:
            continue
        label = (i.period_label or "").lower()
        if "ltm" in label or "press release" in label or label.rstrip().endswith("e"):
            continue
        if i.period_type in (PeriodType.ESTIMATE, PeriodType.PRESS_RELEASE):
            continue
        if int(i.period_end[5:7]) != fy_month:
            continue
        out.setdefault(int(i.period_end[:4]), float(i.value))
    return dict(sorted(out.items()))


def _check(cid: str, name: str, status: str, detail: str, **data: Any) -> dict[str, Any]:
    return {"id": cid, "check": name, "status": status, "detail": detail, **data}


def _pct(a: float, b: float) -> float:
    return (a - b) / abs(b) if b else 0.0


def run_reconciliation(session: ResearchSession) -> dict[str, Any]:
    items = session.financial_line_items
    checks: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    rev = annual_series(items, "total_revenue", "income_statement")
    years = sorted(rev)
    if not items:
        gaps.append({"kind": "missing_ciq_financials", "ask": "Upload Capital IQ Financials for the company."})
        return {"checks": checks, "gaps": gaps, "basis": None}

    # 1. Period coverage
    if years:
        expected = list(range(years[-1] - 4, years[-1] + 1))
        missing = [y for y in expected if y not in rev]
        checks.append(_check(
            "coverage", "Five years of annual actuals", "ok" if not missing else "warn",
            f"FY{years[0]}–FY{years[-1]} on file" + (f"; missing FY{', FY'.join(map(str, missing))}" if missing else ""),
            years=years,
        ))
    else:
        checks.append(_check("coverage", "Five years of annual actuals", "fail", "No annual revenue rows in CIQ Financials"))

    # 2. Balance sheet identity
    ta = annual_series(items, "total_assets", "balance_sheet")
    tle = annual_series(items, "total_liabilities_and_equity", "balance_sheet")
    tl = annual_series(items, "total_liabilities", "balance_sheet")
    te = annual_series(items, "total_equity", "balance_sheet")
    bad: list[str] = []
    tested = 0
    for y, assets in ta.items():
        rhs = tle.get(y)
        if rhs is None and y in tl and y in te:
            rhs = tl[y] + te[y]
        if rhs is None:
            continue
        tested += 1
        if abs(_pct(assets, rhs)) > 0.005:
            bad.append(f"FY{y}: assets {assets:,.0f} vs liabilities+equity {rhs:,.0f}")
    checks.append(_check(
        "bs_identity", "Assets = Liabilities + Equity", "n/a" if not tested else ("ok" if not bad else "fail"),
        "; ".join(bad) if bad else (f"Balances in {tested} year(s)" if tested else "Inputs missing"),
    ))

    # 3. Segments sum to revenue (latest year)
    segs = [i for i in items if i.statement == "segments" and i.value is not None and i.period_end]
    if segs and years:
        latest = years[-1]
        seg_rows = [i for i in segs if i.period_end.startswith(str(latest)) and "total" not in (i.line_item_raw or "").lower()]
        rev_like = [i for i in seg_rows if re.search(r"revenue|sales", (i.line_item_raw or ""), re.I)]
        pool = rev_like or []
        if pool:
            total = sum(i.value for i in pool)
            gap = _pct(total, rev[latest])
            checks.append(_check(
                "segments", "Segment revenue sums to total", "ok" if abs(gap) <= 0.05 else "warn",
                f"FY{latest}: segments {total:,.0f} vs total {rev[latest]:,.0f} ({gap:+.1%})",
            ))

    # 4. Consolidation basis
    basis = None
    if any("unconsolidated_financials_were_not_available" in i.line_item for i in items):
        basis = "consolidated"
        detail = "CIQ shows consolidated financials (standalone not available in this download)"
    else:
        basis = "as downloaded"
        detail = "CIQ download does not state the basis — confirm consolidated vs standalone"
        gaps.append({"kind": "confirm_basis", "ask": "Confirm whether the CIQ download is consolidated or standalone."})
    checks.append(_check("basis", "Consolidation basis", "ok" if basis == "consolidated" else "warn", detail, basis=basis))

    # 5. CIQ Estimates file vs Financials forward columns (same snapshot?)
    fwd_fin: dict[int, float] = {}
    for i in items:
        if i.line_item == "total_revenue" and i.statement == "key_stats" and (i.period_label or "").rstrip().endswith("E") and i.period_end:
            fwd_fin[int(i.period_end[:4])] = float(i.value)
    diffs = []
    for cm in session.consensus_metrics:
        if cm.metric != "Revenue" or cm.mean is None:
            continue
        y = int(cm.period[2:]) if cm.period.startswith("FY") else None
        if y in fwd_fin:
            diffs.append((y, _pct(cm.mean, fwd_fin[y])))
    if diffs:
        worst = max(diffs, key=lambda d: abs(d[1]))
        checks.append(_check(
            "ciq_snapshots", "CIQ Estimates vs Financials forward revenue",
            "ok" if abs(worst[1]) <= 0.05 else "warn",
            f"Largest gap FY{worst[0]}: {worst[1]:+.1%}"
            + ("" if abs(worst[1]) <= 0.05 else " — files downloaded on different dates; re-download both"),
        ))
    elif not session.consensus_metrics:
        gaps.append({"kind": "missing_ciq_estimates", "ask": "Upload the Capital IQ Estimates report (needed for consensus and broker comparison)."})

    # 6. Live price vs CIQ last price
    from iis.finance.market_data import is_reliable_quote

    q = session.market_quote
    ciq_px = None
    for i in sorted(
        (i for i in items if i.line_item == "share_price" and i.value),
        key=lambda i: i.period_end or "",
        reverse=True,
    ):
        ciq_px = float(i.value)
        break
    if is_reliable_quote(q) and ciq_px:
        gap = _pct(q.price, ciq_px)
        checks.append(_check(
            "price", "Live price vs CIQ share price", "ok" if abs(gap) <= 0.25 else "warn",
            f"{q.source} ₹{q.price:,.2f} vs CIQ ₹{ciq_px:,.2f} ({gap:+.0%})"
            + ("" if abs(gap) <= 0.25 else " — check for split/bonus or a stale CIQ download"),
        ))
    elif not is_reliable_quote(q):
        checks.append(_check("price", "Live price", "warn", "price unavailable — MoS not computed against market"))

    # 7. Broker basis checks
    bv = next((a.payload for a in session.agent_artifacts if a.artifact_type == "brokerage_view"), None)
    if bv and bv.get("matrix"):
        mism = [r["broker"] for r in bv["matrix"] if r.get("basis_check") == "mismatch"]
        unit = [r["broker"] for r in bv["matrix"] if any("unit mis-read" in f for f in r.get("flags") or [])]
        checks.append(_check(
            "broker_basis", "Broker actuals match CIQ basis", "ok" if not (mism or unit) else "warn",
            ("Mismatched: " + ", ".join(mism + unit)) if (mism or unit) else
            f"{sum(1 for r in bv['matrix'] if r.get('basis_check') == 'ok')} report(s) checked against CIQ actual revenue",
        ))

    # 8. CIQ vs annual report figures (existing flag logic)
    try:
        from iis.finance.flags import _reconciliation_flags

        rf = _reconciliation_flags(session)
    except Exception:
        rf = []
    if rf:
        checks.append(_check(
            "ciq_vs_ar", "CIQ vs annual report figures", "warn",
            "; ".join(getattr(f, "headline", str(f)) for f in rf[:4]),
        ))

    # 9. Freshness of CIQ
    fr = next((a.payload for a in session.agent_artifacts if a.artifact_type == "data_freshness"), None)
    if fr and fr.get("needs_update"):
        for ask in fr.get("asks") or ["Re-download Capital IQ Financials/Estimates — newer results are out."]:
            gaps.append({"kind": "stale_ciq", "ask": ask})

    # 10. Latest-quarter concall on file?
    if bv and bv.get("newest_results_quarter"):
        nq = bv["newest_results_quarter"]
        names = " ".join((d.file or "") for d in session.pdf_documents if d.doc_type == "transcript").upper()
        texts = " ".join(
            (c.text or "")[:400] for c in session.pdf_chunks if c.doc_type == "transcript" and c.page == 1
        ).upper()
        q_num, fy2 = nq[1], nq[-2:]
        has = re.search(rf"Q{q_num}\s*FY\s*'?{fy2}|{q_num}Q\s*FY\s*'?{fy2}", names + " " + texts)
        if not has:
            gaps.append({"kind": "missing_concall", "ask": f"Upload the {nq} earnings call transcript (latest results quarter)."})

    for c in checks:
        if c["status"] == "fail":
            gaps.append({"kind": f"fix_{c['id']}", "ask": f"Data check failed — {c['check']}: {c['detail']}"})
    return {"checks": checks, "gaps": gaps, "basis": basis, "as_of": date.today().isoformat()}


def apply_reconciliation(session: ResearchSession) -> ResearchSession:
    try:
        payload = run_reconciliation(session)
    except Exception as exc:
        payload = {"checks": [], "gaps": [], "error": str(exc)}
    session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "reconciliation"]
    session.agent_artifacts.append(
        AgentArtifact(agent_id="reconciler", artifact_type="reconciliation", payload=payload,
                      source_tier=SourceTier.EXTRACTED)
    )
    return session


_ICON = {"ok": "✓", "warn": "⚠", "fail": "✗", "n/a": "–"}


def reconciliation_markdown(session: ResearchSession) -> list[str]:
    p = next((a.payload for a in session.agent_artifacts if a.artifact_type == "reconciliation"), None)
    if not p or not p.get("checks"):
        return []
    lines = ["| Check | Result | Detail |", "|---|---|---|"]
    for c in p["checks"]:
        lines.append(f"| {c['check']} | {_ICON.get(c['status'], c['status'])} {c['status']} | {c['detail']} |")
    lines.append("")
    return lines
