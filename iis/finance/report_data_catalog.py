"""What data is on file for the report — always-on vs optional (Hive decides).

Always-on blocks are appended by Python every time they exist.
Optional blocks are offered to Hive with short signals; Hive returns which to include.
"""
from __future__ import annotations

import json
import re
from typing import Any

from iis.models.session import ResearchSession

# Blocks Python always appends when data exists (Hive does not veto these)
ALWAYS_ON_IDS = (
    "inertia_scorecard",  # critical-15 + YoY basics + ratio trends + flags
    "investor_checklist",  # checklist fails/watches
    "ciq_financials",
    "ciq_estimates",
    "capex",
    "peer_timeline",  # only if confirmed peers with CIQ exist
)

# Default omit unless Hive explicitly includes (or signal is elevated)
DEFAULT_OMIT = {
    "csr_esg",
    "remuneration",
    "brsr_brochure",
    "agm_iepf_admin",
}

RELEVANCE_RULES = """
REPORT SHAPE (initiating coverage):
- Part A (business understanding) BEFORE Part B (financials / valuation).
- Educate on what the company IS; then discuss what is happening now.
- Prefer segment / peer / guidance optional blocks when they clarify the business or thesis.

OPTIONAL DATA — INCLUDE ONLY WHEN INVESTMENT-RELEVANT:
- csr_esg / BRSR brochure: OMIT by default. Include only if a concrete controversy,
  regulatory hit, or material cash commitment tied to returns.
- remuneration (KMP / directors): OMIT unless pay looks exorbitant vs peers/profits,
  or jumps sharply in one year without clear performance linkage.
- auditor_fees / auditor change: INCLUDE if fees jump sharply YoY, look out of line
  vs company scale, or auditor changed / qualified opinion / emphasis of matter.
- related_party: INCLUDE if material RPTs, promoter pledges, or unusual flows.
- segments: INCLUDE when multi-segment economics matter to the thesis.
- brokerage_deep: INCLUDE when broker PDFs add non-consensus insight (label STREET).
- guidance_track: INCLUDE when guidance vs actual shows pattern (beat/miss/sandbag).
- ratio_deep: INCLUDE when leverage, WC, or cash conversion drives the debate.

ALWAYS-ON (Python will append — do not dump raw tables in narrative; you may
reference them and leave a marker):
- ciq_financials, ciq_estimates, capex, peer_timeline (if peers on file)

At the END of your markdown, add exactly one fenced block:

```report_include
{"include": ["auditor_fees", "segments"], "omit": ["csr_esg", "remuneration"], "notes": "auditor fees +42% YoY"}
```

Only list optional ids you want Python to append. Unknown ids are ignored.
"""


def _artifact(session: ResearchSession, kind: str) -> dict[str, Any]:
    for a in session.agent_artifacts or []:
        if a.artifact_type == kind and isinstance(a.payload, dict):
            return a.payload
    return {}


def _fy_span(items: list) -> str | None:
    ends = sorted({(getattr(i, "period_end", None) or "")[:10] for i in items if getattr(i, "period_end", None)})
    ends = [e for e in ends if len(e) >= 4]
    if not ends:
        return None
    return f"{ends[0][:4]} → {ends[-1]}"


def _consensus_span(session: ResearchSession) -> str | None:
    periods = sorted({cm.period for cm in (session.consensus_metrics or []) if cm.period})
    if not periods:
        return None
    return f"{periods[0]} → {periods[-1]}"


def _scan_text_signals(session: ResearchSession) -> dict[str, Any]:
    """Light heuristics from AR/concall pack claims + chunk text."""
    signals: dict[str, Any] = {
        "auditor_fee_spike": False,
        "auditor_fee_note": None,
        "remuneration_flag": False,
        "remuneration_note": None,
        "csr_only_brochure": True,
        "related_party_flag": False,
    }
    blobs: list[str] = []
    for art_type in ("annual_report_analysis", "ar_pack", "evidence_dossier"):
        p = _artifact(session, art_type)
        if p:
            blobs.append(json.dumps(p)[:80000])
    for c in (session.pdf_chunks or [])[:400]:
        t = (c.text or "").strip()
        if t:
            blobs.append(t[:500])
    text = "\n".join(blobs).lower()
    if not text:
        return signals

    # Auditor fee YoY jump phrases / large % increases near "audit fee"
    if re.search(r"audit(?:or)?\s+fee.{0,80}(?:increas|jump|rose|higher).{0,40}\d{2,3}\s*%", text, re.I):
        signals["auditor_fee_spike"] = True
        signals["auditor_fee_note"] = "Text near auditor fees mentions a large % increase"
    if re.search(r"(qualified opinion|emphasis of matter|auditor.{0,30}resign)", text, re.I):
        signals["auditor_fee_spike"] = True
        signals["auditor_fee_note"] = (signals["auditor_fee_note"] or "") + "; auditor opinion/change flagged"

    if re.search(
        r"(managerial remuneration|kmp remuneration|director.{0,20}remuneration).{0,60}"
        r"(exorbitant|disproportion|sharp(ly)?\s+increas|\d{2,3}\s*%\s*(increas|jump))",
        text,
        re.I,
    ):
        signals["remuneration_flag"] = True
        signals["remuneration_note"] = "Remuneration mentioned with sharp increase / disproportion language"

    if re.search(r"related party transaction|rpt\b|promoter.{0,40}pledge", text, re.I):
        signals["related_party_flag"] = True

    if re.search(r"\bcsr\b|corporate social responsibility|brsr", text, re.I) and not re.search(
        r"csr.{0,40}(penalty|violat|controvers|fine|sebi)", text, re.I
    ):
        signals["csr_only_brochure"] = True
    else:
        signals["csr_only_brochure"] = False

    return signals


def build_report_data_catalog(session: ResearchSession) -> dict[str, Any]:
    """Structured inventory for Hive + Python finalize."""
    items = list(session.financial_line_items or [])
    signals = _scan_text_signals(session)

    always: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []

    if items:
        always.append(
            {
                "id": "inertia_scorecard",
                "title": "Inertia scorecard (critical-15, YoY basics, ratio trends, flags)",
                "coverage": _fy_span(items),
                "mandatory": True,
            }
        )
        always.append(
            {
                "id": "investor_checklist",
                "title": "Investor checklist concerns (accounting, BS, moat, WC, cash)",
                "mandatory": True,
            }
        )
        always.append(
            {
                "id": "ciq_financials",
                "title": "Capital IQ financials (IS/BS/CF/ratios/segments)",
                "coverage": _fy_span(items),
                "rows": len(items),
                "mandatory": True,
            }
        )
    if session.consensus_metrics:
        always.append(
            {
                "id": "ciq_estimates",
                "title": "Capital IQ consensus estimates",
                "coverage": _consensus_span(session),
                "rows": len(session.consensus_metrics),
                "mandatory": True,
            }
        )

    capex_ok = False
    try:
        from iis.finance.metric_series import build_capex_analysis

        capex = build_capex_analysis(items, recent_years=8)
        if capex:
            capex_ok = True
            always.append(
                {
                    "id": "capex",
                    "title": "Capex series (Capital IQ)",
                    "coverage": "multi-year",
                    "mandatory": True,
                    "summary": str(capex)[:240],
                }
            )
    except Exception:
        pass

    peers_n = 0
    try:
        from iis.finance.competitor_discovery import confirmed_peer_libraries

        peers = confirmed_peer_libraries(session)
        peers_n = len(peers)
        if peers:
            always.append(
                {
                    "id": "peer_timeline",
                    "title": "Peer timeline (confirmed peers with CIQ)",
                    "peers": [p.get("name") for p in peers],
                    "mandatory": True,
                }
            )
        else:
            optional.append(
                {
                    "id": "peer_ask",
                    "title": "Peers uploaded but not confirmed / no CIQ match",
                    "default": "omit",
                    "hint": "Confirm peers (UPL, BASF, …) with CIQ Financials to unlock timeline tables",
                }
            )
    except Exception:
        pass

    # Optional catalogue
    optional.extend(
        [
            {
                "id": "ratio_deep",
                "title": "Ratio interpretation (margins, ROE/ROCE, leverage, WC)",
                "default": "include_if_useful",
                "available": bool(items),
                "hint": "Prefer when leverage/WC/cash conversion is material to thesis",
            },
            {
                "id": "segments",
                "title": "Segment detail tables",
                "default": "include_if_multi_segment",
                "available": any((i.statement or "") == "segments" for i in items),
            },
            {
                "id": "guidance_track",
                "title": "Guidance vs actual track record",
                "default": "include_if_pattern",
                "available": bool(session.guidance_records or _artifact(session, "guidance_summary")),
            },
            {
                "id": "brokerage_deep",
                "title": "Brokerage STREET deep-read / estimate matrix",
                "default": "include_if_insight",
                "available": any(
                    "broker" in ((d.doc_type or "") + (d.file or "")).lower()
                    for d in (session.pdf_documents or [])
                )
                or bool(_artifact(session, "brokerage_view")),
            },
            {
                "id": "auditor_fees",
                "title": "Auditor fees / opinion / change",
                "default": "omit_unless_signal",
                "available": True,
                "signal": signals.get("auditor_fee_spike"),
                "signal_note": signals.get("auditor_fee_note"),
                "hint": "Include if fees jump sharply YoY or opinion/change issues",
            },
            {
                "id": "remuneration",
                "title": "Managerial / director remuneration",
                "default": "omit_unless_exorbitant",
                "available": True,
                "signal": signals.get("remuneration_flag"),
                "signal_note": signals.get("remuneration_note"),
                "hint": "Omit unless pay is exorbitant or jumps without performance link",
            },
            {
                "id": "csr_esg",
                "title": "CSR / BRSR / ESG brochure",
                "default": "omit",
                "available": True,
                "signal": not signals.get("csr_only_brochure"),
                "hint": "Almost always omit — not investment-critical brochure text",
            },
            {
                "id": "related_party",
                "title": "Related-party transactions / pledges",
                "default": "include_if_material",
                "available": True,
                "signal": signals.get("related_party_flag"),
                "hint": "Include when RPTs or pledges are material",
            },
        ]
    )

    return {
        "company": session.company.name if session.company else session.company_key,
        "always_on": always,
        "optional": optional,
        "signals": signals,
        "meta": {
            "financial_line_items": len(items),
            "consensus_metrics": len(session.consensus_metrics or []),
            "pdf_documents": len(session.pdf_documents or []),
            "confirmed_peers": peers_n,
            "capex_on_file": capex_ok,
        },
    }


def catalog_markdown_for_hive(catalog: dict[str, Any]) -> str:
    lines = [
        "=== DATA AVAILABLE FOR THIS REPORT ===",
        "",
        "ALWAYS-ON (Python will append tables — reference them; do not paste full grids):",
    ]
    for b in catalog.get("always_on") or []:
        cov = b.get("coverage") or ""
        peers = b.get("peers")
        extra = f" · {cov}" if cov else ""
        if peers:
            extra += f" · peers: {', '.join(peers)}"
        lines.append(f"- [{b['id']}] {b['title']}{extra}")
    if not catalog.get("always_on"):
        lines.append("- (none yet — CIQ Financials/Estimates missing)")

    lines.append("")
    lines.append("OPTIONAL (include only if investment-relevant — see rules):")
    for b in catalog.get("optional") or []:
        if b.get("available") is False:
            continue
        sig = ""
        if b.get("signal"):
            sig = f" · SIGNAL: {b.get('signal_note') or 'elevated'}"
        lines.append(
            f"- [{b['id']}] {b['title']} (default: {b.get('default', '?')}){sig}"
            + (f" — {b['hint']}" if b.get("hint") else "")
        )
    lines.append("")
    lines.append(RELEVANCE_RULES.strip())
    return "\n".join(lines)


def parse_report_include_block(markdown: str) -> dict[str, Any]:
    """Extract ```report_include ... ``` JSON from Hive output."""
    text = markdown or ""
    m = re.search(r"```report_include\s*(\{.*?\})\s*```", text, re.S | re.I)
    if not m:
        # Also accept HTML comment form
        m = re.search(r"<!--\s*report_include\s*(\{.*?\})\s*-->", text, re.S | re.I)
    if not m:
        return {"include": [], "omit": list(DEFAULT_OMIT), "notes": ""}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {"include": [], "omit": list(DEFAULT_OMIT), "notes": "parse_failed"}
    include = [str(x) for x in (data.get("include") or []) if x]
    omit = [str(x) for x in (data.get("omit") or list(DEFAULT_OMIT)) if x]
    return {
        "include": include,
        "omit": omit,
        "notes": str(data.get("notes") or "")[:400],
        "placements": data.get("placements") if isinstance(data.get("placements"), dict) else {},
    }


def strip_report_include_block(markdown: str) -> str:
    text = markdown or ""
    text = re.sub(r"```report_include\s*\{.*?\}\s*```", "", text, flags=re.S | re.I)
    text = re.sub(r"<!--\s*report_include\s*\{.*?\}\s*-->", "", text, flags=re.S | re.I)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
