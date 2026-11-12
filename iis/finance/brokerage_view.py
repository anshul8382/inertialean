"""Brokerage View — broker estimates parsed by Python and mapped to Capital IQ.

Per company-specific broker report (doc_type brokerage_report):
  broker, report date, latest results quarter covered, rating, target price, CMP at report,
  key-financials table (Revenue / EBITDA / PAT / EPS by FY) with units normalised to INR mn.

Reliability gates:
  • only tables whose header has distinct FY columns and rows with exactly one value per column
  • unit must be stated near the table (INR mn / bn / cr) — else money rows are dropped
  • basis check: broker's actual-year revenue vs CIQ actual (±8%) — fails → excluded from consensus
  • staleness: report covering an older results quarter than the newest on file, or > 120 days old
Consensus comparison uses CIQ Estimates (mean / high / low / no. of estimates).
"""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime
from pathlib import Path
from typing import Any

from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

METRICS = ("revenue", "ebitda", "pat", "eps")
METRIC_LABELS = {"revenue": "Revenue (₹ mn)", "ebitda": "EBITDA (₹ mn)", "pat": "PAT (₹ mn)", "eps": "EPS (₹)"}
_CIQ_CONSENSUS_NAMES = {
    "revenue": ("Revenue",),
    "ebitda": ("EBITDA",),
    "pat": ("Net Income (Excl. Excep)", "Net Income (GAAP)"),
    "eps": ("EPS Normalized", "EPS (GAAP)"),
}
_CIQ_ACTUAL_ITEMS = {
    "revenue": ("total_revenue",),
    "ebitda": ("ebitda",),
    "pat": ("net_income",),
    "eps": ("diluted_eps_excl_extra_items", "diluted_eps"),
}
BASIS_TOLERANCE = 0.08
OUTLIER_PCT = 0.10
UNIT_SUSPECT_PCT = 0.50
STALE_DAYS = 120

_LABEL_MAP: list[tuple[str, re.Pattern[str]]] = [
    ("revenue", re.compile(r"^(?:net\s+|total\s+)?(?:revenues?|sales|net sales|revenue from operations|income from operations|total income)\b", re.I)),
    ("ebitda", re.compile(r"^(?:adj(?:usted|\.)?\s*|reported\s+)?ebitda\b", re.I)),
    ("pat", re.compile(r"^(?:adj(?:usted|\.)?\s*|reported\s+|recurring\s+)?(?:pat|apat|net profit|profit after tax|adjusted profit|net income|profit)\b", re.I)),
    ("eps", re.compile(r"^(?:adj(?:usted|\.)?\s*|diluted\s+|reported\s+)?eps\b", re.I)),
]
_SKIP_LABEL = re.compile(r"%|growth|margin|\(x\)|yoy|chg|change|per share.*growth|cagr", re.I)
_PERIOD_TOKEN = re.compile(r"^(?:FY|CY)?'?(\d{2}|\d{4})([AEP])?$", re.I)
_UNIT_RE = re.compile(
    r"(?:INR|Rs\.?|₹)\s*\.?\s*(mn|m|million|bn|b|billion|cr|crore|crores|lakh|lakhs)\b", re.I
)
_UNIT_FACTOR = {
    "mn": 1.0, "m": 1.0, "million": 1.0, "bn": 1000.0, "b": 1000.0, "billion": 1000.0,
    "cr": 10.0, "crore": 10.0, "crores": 10.0, "lakh": 0.1, "lakhs": 0.1,
}
_NUM = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")
_RATING_WORDS = r"BUY|SELL|HOLD|ADD|REDUCE|NEUTRAL|ACCUMULATE|OUTPERFORM|UNDERPERFORM|OVERWEIGHT|UNDERWEIGHT"
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ── text helpers ─────────────────────────────────────────────────────────


def _pages(session: ResearchSession, filename: str, max_page: int = 4) -> list[tuple[int, str]]:
    by_page: dict[int, list[tuple[int, str]]] = {}
    for c in session.pdf_chunks:
        if Path(c.file).name == filename and c.page <= max_page:
            by_page.setdefault(c.page, []).append((c.chunk_index, c.text or ""))
    return [(p, "".join(t for _, t in sorted(v))) for p, v in sorted(by_page.items())]


def _num(tok: str) -> float | None:
    t = tok.strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _fy(tok: str) -> tuple[int, str | None] | None:
    m = _PERIOD_TOKEN.match(tok.strip())
    if not m:
        return None
    y = int(m.group(1))
    if y < 100:
        y += 2000
    if not 2015 <= y <= 2040:
        return None
    return y, (m.group(2) or "").upper() or None


def _parse_date(text: str) -> date | None:
    cands: list[date] = []
    for m in re.finditer(
        r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[,\s\-]+(\d{2,4})\b",
        text, re.I,
    ):
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            cands.append(date(y, _MONTHS[m.group(2).lower()[:3]], int(m.group(1))))
        except ValueError:
            pass
    for m in re.finditer(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b", text, re.I
    ):
        try:
            cands.append(date(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2))))
        except ValueError:
            pass
    today = date.today()
    cands = [d for d in cands if d <= today and d.year >= 2015]
    return max(cands) if cands else None


def _latest_quarter(text: str) -> tuple[int, int] | None:
    best = None
    for m in re.finditer(r"\b(?:Q([1-4])\s?FY'?(\d{2})|([1-4])Q\s?FY'?(\d{2}))\b", text, re.I):
        q = int(m.group(1) or m.group(3))
        y = 2000 + int(m.group(2) or m.group(4))
        if best is None or (y, q) > best:
            best = (y, q)
    return best


def _quarter_label(q: tuple[int, int] | None) -> str | None:
    return f"Q{q[1]}FY{str(q[0])[2:]}" if q else None


def _rating(text: str) -> str | None:
    pats = [
        rf"\bRating\s*:?\s*({_RATING_WORDS})\b",
        rf"\b(?:Reiterate|Maintain|Retain|Upgrade(?:d)?\s+to|Downgrade(?:d)?\s+to|rating on the stock to|initiate[sd]? (?:coverage )?with(?: an?)?)\s+['‘’\"]?({_RATING_WORDS})\b",
        rf"\bTP\s*:?\s*(?:INR|Rs\.?|₹)\s*[\d,]+(?:\.\d+)?\s*\([^)]*\)\s*({_RATING_WORDS})\b",
        rf"(?m)^\s*({_RATING_WORDS})\s*$",
    ]
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1).upper()
    return None


def _money(text: str, pats: list[str]) -> float | None:
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            v = _num(m.group(1))
            if v and v > 0:
                return v
    return None


def _target_price(text: str) -> float | None:
    return _money(text, [
        r"price\s+target\s*\((?:INR|Rs\.?)\)\s*([\d,]+(?:\.\d+)?)",
        r"target\s+price\s*(?:\((?:INR|Rs\.?)\))?\s*(?:of|:)?\s*(?:INR|Rs\.?|₹)?\s*([\d,]{3,}(?:\.\d+)?)",
        r"\bTP\s*(?:of|:)?\s*(?:INR|Rs\.?|₹)\s*([\d,]{3,}(?:\.\d+)?)",
        r"\bTarget\s*:?\s*(?:INR|Rs\.?|₹)\s*([\d,]{3,}(?:\.\d+)?)",
    ])


def _cmp(text: str) -> float | None:
    return _money(text, [
        r"\bCMP\s*:?\s*(?:INR|Rs\.?|₹)\s*([\d,]{3,}(?:\.\d+)?)",
        r"\bPrice\s*\((?:INR|Rs\.?)\)\s*([\d,]{3,}(?:\.\d+)?)",
        r"\bCurrent\s+(?:Market\s+)?Price\s*:?\s*(?:INR|Rs\.?|₹)?\s*([\d,]{3,}(?:\.\d+)?)",
    ])


def _broker_name(filename: str, text: str) -> str:
    try:
        from iis.integrations.telegram_report_index import _detect_broker
    except Exception:
        _detect_broker = None  # type: ignore[assignment]
    clean = re.sub(r"^tg_\d+_", "", filename).replace("_", " ")
    if _detect_broker:
        b = _detect_broker(clean) or _detect_broker(text[:1500])
        if b:
            return b
    for name in ("Geojit", "DRChoksey", "ICICI Direct", "HDFC Securities", "HSIE", "Nirmal Bang", "Niraml Bang"):
        if name.lower() in clean.lower():
            return "Nirmal Bang" if name == "Niraml Bang" else name
    return clean.split(" ")[0]


# ── estimate tables ──────────────────────────────────────────────────────


def parse_estimate_tables(text: str) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines()]
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    i = 0
    while i < len(lines):
        toks = lines[i].split()
        periods = []
        for t in toks:
            fy = _fy(t)
            if fy and (t.upper().startswith(("FY", "CY")) or fy[1]):
                periods.append(fy)
        prefix = " ".join(t for t in toks if not _fy(t))
        if len(periods) < 2 or len(prefix) > 45 or len({p[0] for p in periods}) != len(periods):
            i += 1
            continue
        window = " ".join(lines[max(0, i - 3) : i + 1])
        found = list(_UNIT_RE.finditer(window))
        um = found[-1] if found else None
        factor = _UNIT_FACTOR.get(um.group(1).lower()) if um else None
        unit_text = um.group(0) if um else None
        j = i + 1
        while j < len(lines) and j <= i + 25:
            row = lines[j].split()
            if not row:
                j += 1
                continue
            if sum(1 for t in row if _fy(t) and t.upper().startswith("FY")) >= 2:
                break
            k = len(row)
            while k > 0 and _NUM.match(row[k - 1]):
                k -= 1
            label = " ".join(row[:k])
            nums = row[k:]
            j += 1
            if not label or len(nums) != len(periods) or _SKIP_LABEL.search(label):
                continue
            metric = next((m for m, pat in _LABEL_MAP if pat.search(label)), None)
            if not metric:
                continue
            per_share = metric == "eps"
            if not per_share and factor is None:
                continue
            for (year, flag), tok in zip(periods, nums):
                v = _num(tok)
                if v is None or (metric, year) in seen:
                    continue
                seen.add((metric, year))
                out.append(
                    {
                        "metric": metric,
                        "fy": year,
                        "flag": flag,
                        "value": round(v if per_share else v * factor, 2),
                        "raw": tok,
                        "label": label,
                        "unit": "INR/share" if per_share else unit_text,
                    }
                )
        i = j
    return out


# ── CIQ baselines ────────────────────────────────────────────────────────


def _ciq_actual(session: ResearchSession, metric: str, fy: int) -> float | None:
    for item in session.financial_line_items:
        if item.statement != "income_statement" or item.line_item not in _CIQ_ACTUAL_ITEMS[metric]:
            continue
        if item.value is None or (item.period_label or "").rstrip().endswith("E"):
            continue
        if "LTM" in (item.period_label or "") or not item.period_end:
            continue
        if item.period_end.startswith(f"{fy}-03"):
            return float(item.value)
    return None


def _ciq_consensus(session: ResearchSession, metric: str, fy: int) -> dict[str, Any] | None:
    for name in _CIQ_CONSENSUS_NAMES[metric]:
        for cm in session.consensus_metrics:
            if cm.metric == name and cm.period == f"FY{fy}" and cm.mean is not None:
                return {"mean": cm.mean, "high": cm.high, "low": cm.low, "n": cm.num_estimates, "ciq_metric": name}
    return None


def _ciq_latest_quarter(session: ResearchSession) -> tuple[int, int] | None:
    best = None
    for item in session.financial_line_items:
        pe = item.period_end or ""
        m = re.match(r"(\d{4})-(\d{2})", pe)
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        if "E" in (item.period_label or "")[-2:]:
            continue
        fy = y + 1 if mo >= 4 else y
        q = {6: 1, 9: 2, 12: 3, 3: 4}.get(mo)
        if q and (best is None or (fy, q) > best):
            best = (fy, q)
    return best


# ── main ─────────────────────────────────────────────────────────────────


def _company_reports(session: ResearchSession) -> list[str]:
    from iis.finance.competitor_discovery import _company_specific_broker_files, _subject_listing

    name_tokens = [
        t for t in re.findall(r"[a-z0-9]+", (session.company.name if session.company else "").lower())
        if len(t) >= 4 and t not in ("limited", "industries", "india", "company")
    ][:2]
    try:
        subj = _subject_listing(session)
    except Exception:
        subj = None
    if subj:
        name_tokens.append(subj.symbol.lower())
    files = _company_specific_broker_files(session, name_tokens)
    return sorted(files, key=lambda f: int(m.group(1)) if (m := re.match(r"tg_(\d+)_", f)) else 0)


def build_brokerage_view(session: ResearchSession) -> dict[str, Any]:
    today = date.today()
    reports: list[dict[str, Any]] = []
    newest_q: tuple[int, int] | None = _ciq_latest_quarter(session)
    last_actual_fy = max(
        (y for y in range(2015, today.year + 2) if _ciq_actual(session, "revenue", y)), default=None
    )
    for fn in _company_reports(session):
        pages = _pages(session, fn)
        if not pages:
            continue
        first = pages[0][1]
        head = "\n".join(t for _, t in pages[:2])
        rows = parse_estimate_tables("\n".join(t for _, t in pages[:3]))
        for r in rows:
            if r["flag"] is None:
                r["flag"] = "A" if last_actual_fy and r["fy"] <= last_actual_fy else "E"
        q = _latest_quarter(first) or _latest_quarter(head)
        rep = {
            "file": fn,
            "broker": _broker_name(fn, first),
            "report_date": (_parse_date(first) or _parse_date(head)),
            "covers_quarter": q,
            "rating": _rating(head),
            "target_price": _target_price(head),
            "cmp_at_report": _cmp(head),
            "estimates": rows,
            "flags": [],
        }
        if rep["target_price"] and rep["cmp_at_report"]:
            ratio = rep["target_price"] / rep["cmp_at_report"]
            if not 0.4 <= ratio <= 2.5:
                rep["flags"].append("target price looks mis-read — ignored")
                rep["target_price"] = None
        actual_rev = [r for r in rows if r["metric"] == "revenue" and r["flag"] == "A"]
        basis = "unchecked"
        for r in actual_rev:
            ciq = _ciq_actual(session, "revenue", r["fy"])
            if ciq:
                gap = (r["value"] - ciq) / ciq
                basis = "ok" if abs(gap) <= BASIS_TOLERANCE else "mismatch"
                if basis == "mismatch":
                    rep["flags"].append(
                        f"FY{str(r['fy'])[2:]}A revenue {r['value']:,.0f} vs CIQ {ciq:,.0f} ({gap:+.0%}) — "
                        "different units or standalone/consolidated basis; excluded from consensus"
                    )
                break
        rep["basis_check"] = basis
        reports.append(rep)
        if q and (newest_q is None or q > newest_q):
            newest_q = q

    for rep in reports:
        d = rep["report_date"]
        q = rep["covers_quarter"]
        stale = False
        if q and newest_q and q < newest_q:
            stale = True
            rep["flags"].append(f"covers {_quarter_label(q)}; newer results ({_quarter_label(newest_q)}) are out")
        if d and (today - d).days > STALE_DAYS:
            stale = True
        rep["stale"] = stale

    # Latest usable report per broker
    latest: dict[str, dict[str, Any]] = {}
    for rep in reports:
        if not rep["estimates"] and not rep["target_price"]:
            continue
        latest[rep["broker"]] = rep  # reports are in Telegram order (oldest → newest)

    est_years = sorted({r["fy"] for rep in latest.values() for r in rep["estimates"] if r["flag"] != "A"})
    ciq_fy = None
    for item in session.agent_artifacts:
        if item.artifact_type == "estimates_summary":
            ciq_fy = item.payload.get("current_fiscal_year")
    years = [y for y in est_years if not ciq_fy or y >= int(ciq_fy)][:2] or est_years[:2]

    matrix_rows = []
    usable: dict[tuple[str, int], list[float]] = {}
    for broker, rep in sorted(latest.items()):
        cells: dict[str, Any] = {}
        for r in rep["estimates"]:
            if r["fy"] not in years or r["flag"] == "A":
                continue
            key = f"{r['metric']}_FY{r['fy']}"
            cons = _ciq_consensus(session, r["metric"], r["fy"])
            dev = (r["value"] - cons["mean"]) / abs(cons["mean"]) if cons and cons["mean"] else None
            outlier = dev is not None and abs(dev) > OUTLIER_PCT
            unit_suspect = dev is not None and abs(dev) > UNIT_SUSPECT_PCT and r["metric"] != "eps"
            cells[key] = {"value": r["value"], "vs_ciq": dev, "outlier": outlier, "label": r["label"],
                          "excluded": unit_suspect}
            if unit_suspect:
                msg = f"{METRIC_LABELS[r['metric']]} FY{str(r['fy'])[2:]}E {dev:+.0%} vs CIQ — unit mis-read suspected; excluded"
                if msg not in rep["flags"]:
                    rep["flags"].append(msg)
                continue
            if rep["basis_check"] != "mismatch" and not rep["stale"]:
                usable.setdefault((r["metric"], r["fy"]), []).append(r["value"])
        matrix_rows.append(
            {
                "broker": broker,
                "file": rep["file"],
                "report_date": rep["report_date"].isoformat() if rep["report_date"] else None,
                "covers": _quarter_label(rep["covers_quarter"]),
                "rating": rep["rating"],
                "target_price": rep["target_price"],
                "stale": rep["stale"],
                "basis_check": rep["basis_check"],
                "flags": rep["flags"],
                "cells": cells,
            }
        )

    consensus = {}
    for metric in METRICS:
        for y in years:
            cons = _ciq_consensus(session, metric, y)
            vals = usable.get((metric, y), [])
            consensus[f"{metric}_FY{y}"] = {
                "ciq": cons,
                "broker_median": statistics.median(vals) if vals else None,
                "broker_n": len(vals),
            }

    est_summary = next((a.payload for a in session.agent_artifacts if a.artifact_type == "estimates_summary"), {})
    tps = [r["target_price"] for r in matrix_rows if r["target_price"] and not r["stale"]]
    return {
        "years": years,
        "newest_results_quarter": _quarter_label(newest_q),
        "reports_parsed": len(reports),
        "reports_with_estimates": sum(1 for r in reports if r["estimates"]),
        "matrix": matrix_rows,
        "consensus": consensus,
        "target_price": {
            "broker_median_current": statistics.median(tps) if tps else None,
            "broker_n_current": len(tps),
            "ciq_mean": est_summary.get("target_price_mean"),
            "ciq_median": est_summary.get("target_price_median"),
        },
        "ratings_current": sorted(
            {r["broker"]: r["rating"] for r in matrix_rows if r["rating"] and not r["stale"]}.items()
        ),
        "rules": {
            "basis_tolerance": BASIS_TOLERANCE,
            "outlier_pct": OUTLIER_PCT,
            "stale_days": STALE_DAYS,
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


def apply_brokerage_view(session: ResearchSession) -> ResearchSession:
    try:
        payload = build_brokerage_view(session)
    except Exception as exc:
        payload = {"error": str(exc), "matrix": [], "years": []}

    # Hive narrative loop on broker PDFs (STREET-tagged; estimates stay Python/CIQ)
    structured = None
    try:
        from iis.config import settings
        from iis.finance.broker_pipeline import run_broker_pipeline
        from iis.llm.factory import get_llm_client
        from iis.models.enums import EventType

        if bool(getattr(settings, "broker_structured_llm_enabled", False)) and get_llm_client(
            finance=True
        ).is_available():

            def _progress(msg: str) -> None:
                session.add_event(
                    EventType.PROGRESS,
                    agent="brokerage_view",
                    details={"message": msg},
                )

            session.add_event(
                EventType.PROGRESS,
                agent="brokerage_view",
                details={
                    "message": "Broker Hive analyst loop (STREET relevance→deep-read→critic)…"
                },
            )
            structured = run_broker_pipeline(session, progress_cb=_progress)
            if structured:
                payload["structured_broker"] = structured
                payload["evidence_register"] = list(structured.get("evidence_register") or [])
                payload["hive_loop"] = bool(structured.get("ok"))
    except Exception as exc:
        payload["structured_broker_error"] = str(exc)[:240]

    session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "brokerage_view"]
    provenance = [r["file"] for r in payload.get("matrix", [])]
    if structured and structured.get("units"):
        provenance = list(
            {*(provenance or []), *(u.get("file") for u in structured["units"] if u.get("file"))}
        )
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="brokerage_view",
            artifact_type="brokerage_view",
            payload=payload,
            confidence=0.88 if payload.get("hive_loop") else 0.8,
            provenance=provenance,
            source_tier=SourceTier.EXTRACTED,
        )
    )
    return session


def _f(v: float | None, metric: str) -> str:
    if v is None:
        return "—"
    return f"{v:,.1f}" if metric == "eps" else f"{v:,.0f}"


def brokerage_view_markdown(session: ResearchSession) -> list[str]:
    art = next((a for a in session.agent_artifacts if a.artifact_type == "brokerage_view"), None)
    if not art or not art.payload.get("matrix"):
        return []
    p = art.payload
    years = p.get("years") or []
    lines = [
        f"_Parsed {p['reports_parsed']} company-specific broker reports; "
        f"{p['reports_with_estimates']} had a key-financials table. Units normalised to ₹ mn; "
        f"newest results on file: {p.get('newest_results_quarter') or '—'}._",
        "",
    ]
    for metric in ("revenue", "ebitda", "pat", "eps"):
        keys = [f"{metric}_FY{y}" for y in years]
        if not any(r["cells"].get(k) for r in p["matrix"] for k in keys):
            continue
        lines.append(f"#### {METRIC_LABELS[metric]}")
        lines.append("")
        head = "| Broker | Date | Covers | " + " | ".join(f"FY{str(y)[2:]}E | vs CIQ" for y in years) + " |"
        lines.append(head)
        lines.append("|---|---|---|" + "---:|---:|" * len(years))
        for r in p["matrix"]:
            if not any(r["cells"].get(k) for k in keys):
                continue
            tag = " _(stale)_" if r["stale"] else ""
            tag += " _(basis mismatch)_" if r["basis_check"] == "mismatch" else ""
            cells = []
            for k in keys:
                c = r["cells"].get(k)
                if not c:
                    cells += ["—", "—"]
                    continue
                dev = f"{c['vs_ciq']:+.0%}" if c["vs_ciq"] is not None else "—"
                if c.get("excluded"):
                    dev = f"~~{dev}~~ excluded"
                elif c["outlier"]:
                    dev = f"**{dev}**"
                cells += [_f(c["value"], metric), dev]
            lines.append(f"| {r['broker']}{tag} | {r['report_date'] or '—'} | {r['covers'] or '—'} | " + " | ".join(cells) + " |")
        cons_cells = []
        med_cells = []
        for k in keys:
            c = p["consensus"].get(k) or {}
            ciq = c.get("ciq")
            cons_cells += [
                (_f(ciq["mean"], metric) + (f" (n={ciq['n']})" if ciq and ciq.get("n") else "")) if ciq else "—",
                (f"{_f(ciq['low'], metric)}–{_f(ciq['high'], metric)}" if ciq and ciq.get("low") is not None else ""),
            ]
            med_cells += [_f(c.get("broker_median"), metric) + (f" (n={c['broker_n']})" if c.get("broker_n") else ""), ""]
        lines.append("| **CIQ consensus mean** (low–high) | | | " + " | ".join(cons_cells) + " |")
        lines.append("| **Median of current broker reports** | | | " + " | ".join(med_cells) + " |")
        lines.append("")
    tp = p.get("target_price") or {}
    if tp.get("ciq_mean") or tp.get("broker_median_current"):
        lines.append(
            "**Target price:** "
            + (f"CIQ mean ₹{tp['ciq_mean']:,.0f} / median ₹{tp['ciq_median']:,.0f}" if tp.get("ciq_mean") else "")
            + (f" · current broker reports median ₹{tp['broker_median_current']:,.0f} (n={tp['broker_n_current']})"
               if tp.get("broker_median_current") else "")
        )
        lines.append("")
    if p.get("ratings_current"):
        lines.append("**Current ratings (street view):** " + ", ".join(f"{b}: {r}" for b, r in p["ratings_current"]))
        lines.append("")
    flagged = [r for r in p["matrix"] if r["flags"]]
    if flagged:
        lines.append("**Flags:**")
        for r in flagged:
            for f in r["flags"]:
                lines.append(f"- {r['broker']} (`{r['file'][:48]}`): {f}")
        lines.append("")
    lines.append(
        f"_Bold deviation = more than {int(OUTLIER_PCT * 100)}% from the CIQ mean. Stale or basis-mismatched reports "
        "are shown but excluded from the broker median._"
    )
    lines.append("")
    return lines
