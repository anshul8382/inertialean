"""
Client Data Integrity — attention analysis (Layer 4–7).

Python owns which sections need attention (from case pack).
LLM (optional, local) phrases analysis + resolution steps from policy.
Fail closed to deterministic playbooks. Suggest-only — never mutates books.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MODULE_ID = "client_data_integrity"
_GUIDELINES = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "assistant_modules"
    / "CLIENT_DATA_INTEGRITY_GUIDELINES.md"
)

# Deterministic how-to-resolve by section / signal
_PLAYBOOKS: Dict[str, Dict[str, Any]] = {
    "cashflow_trade": {
        "why": "Cashflow totals / day series do not line up with BUY−SELL within materiality (G8).",
        "steps": [
            "Open the cashflow ↔ trades playbook for this client.",
            "Check opening-book epoch vs first real trade — do not chase pre-cutoff cashflows.",
            "Work largest day/amount gaps chronologically; club trades ±14 days only when notes support it.",
            "Edit cashflows or trades in existing screens, then Re-check. Do not invent fills.",
        ],
        "url_key": "cashflow_trade",
    },
    "duplicates": {
        "why": "Possible duplicate trades inflate quantities / book.",
        "steps": [
            "Open each duplicate issue and compare trade ids/dates/qty.",
            "Delete or correct the false duplicate in Transactions — keep the true lot.",
            "Re-check (or wait for the daily issue healer). Open issues auto-clear when "
            "fewer than two exact-match trades remain — do not leave stale criticals.",
        ],
        "url_key": "firm_data_integrity",
    },
    "dates": {
        "why": "Future-dated trades distort holdings and performance.",
        "steps": [
            "Open the future-date issue and the trade edit screen.",
            "Correct the trade date to the actual execution date.",
            "Re-check.",
        ],
        "url_key": "firm_data_integrity",
    },
    "negatives": {
        "why": "Negative holdings or prices usually mean bad trade history or bad price rows.",
        "steps": [
            "Trace BUY/SELL chronology for the security (Portfolio Construction / trades).",
            "Fix mistyped qty/sign or missing BUY — do not force-edit a recalculated holding.",
            "For negative prices: fix the price row in the price accuracy path.",
        ],
        "url_key": "firm_data_integrity",
    },
    "reco_match": {
        "why": "Recommendations and executions do not line up (unexecuted batch or qty/price mismatch).",
        "steps": [
            "Open the monthly investment / recommendation lines cited in the issue.",
            "Decide: still pending client/ops, ₹0 cycle, or wrong trade — then execute or correct.",
            "Do not invent fills. Re-check after real edits.",
        ],
        "url_key": "firm_data_integrity",
    },
    "orphan_cashflow": {
        "why": "Agent flagged cashflow(s) without adequate trade linkage in its window (G7).",
        "steps": [
            "Prefer the G8 cashflow ↔ trades page if opening-book or lifetime gap is involved.",
            "Otherwise open the cashflow, find trades in that month / ±14d, align dates or amounts.",
            "Re-check; do not clear with corporate actions.",
        ],
        "url_key": "cashflow_trade",
    },
    "holdings": {
        "why": "Stored holding qty may disagree with trade sum (often superseded by forward calc).",
        "steps": [
            "Spot-check Portfolio Construction / forward holdings vs trade history.",
            "Fix the trade history if wrong — do not patch a recalculated holding.",
        ],
        "url_key": "firm_data_integrity",
    },
    "corporate_actions": {
        "why": "Corporate action may not be reflected in quantities.",
        "steps": [
            "Verify CA record and lots in portfolio construction.",
            "Never use CA to explain away a cashflow ↔ trade gap.",
        ],
        "url_key": "firm_data_integrity",
    },
    "prices": {
        "why": (
            "Price scan found SPIKE / MISSING / ZERO on securities this client holds (P1) — "
            "valuations and performance can be wrong on those dates."
        ),
        "steps": [
            "Open Hub → Price accuracy for the listed symbols.",
            "SPIKE: skip if it matches a split/bonus on file; otherwise verify vs exchange and acknowledge only if the move is real.",
            "MISSING / ZERO: one-day holes, weekends, and days with no trade are not issues. Confirm market holidays on Hub only when names that traded that day all have no close. Fix only gaps longer than 5 open days that contain a trade.",
            "Re-run price scan, then open this page with Live re-check (do not wait for nightly).",
        ],
        "url_key": "prices",
    },
    "other_issues": {
        "why": "Open data-integrity issue(s) need a human look.",
        "steps": [
            "Open firm issues for this client.",
            "Fix the underlying fact in the linked screen, then resolve/re-check.",
        ],
        "url_key": "firm_data_integrity",
    },
}


def _load_guidelines(max_chars: int = 3500) -> str:
    try:
        return _GUIDELINES.read_text(encoding="utf-8")[:max_chars]
    except Exception:
        return "Suggest only. Cite facts. Never claim you edited data. Worst section wins."


def _compact_context(case: Dict[str, Any]) -> Dict[str, Any]:
    """Token-budget context: only sections needing attention + capped facts."""
    attention_sections = []
    for sec in case.get("sections") or []:
        if sec.get("informational"):
            continue
        if sec.get("status") not in ("needs_review", "not_matched"):
            continue
        findings = []
        for it in (sec.get("items") or [])[:8]:
            findings.append(
                {
                    "fact_id": it.get("fact_id"),
                    "title": (it.get("title") or "")[:160],
                    "kind": it.get("kind"),
                    "url": it.get("url"),
                }
            )
        attention_sections.append(
            {
                "section_id": sec.get("id"),
                "title": sec.get("title"),
                "status": sec.get("status"),
                "signal_ids": sec.get("signal_ids") or [],
                "summary": (sec.get("summary") or "")[:280],
                "detail_url": sec.get("detail_url"),
                "findings": findings,
            }
        )
    return {
        "module_id": MODULE_ID,
        "client_id": case.get("client_id"),
        "client_name": case.get("client_name"),
        "overall_status": case.get("overall_status"),
        "overall_label": case.get("overall_label"),
        "attention_sections": attention_sections,
        "deep_links": case.get("deep_links") or {},
    }


def _url_for_section(section_id: str, case: Dict[str, Any], sec: Optional[Dict] = None) -> str:
    links = case.get("deep_links") or {}
    if sec and sec.get("detail_url"):
        return sec["detail_url"]
    play = _PLAYBOOKS.get(section_id) or {}
    key = play.get("url_key")
    if key == "prices":
        return "/hub/system/price-accuracy"
    if key == "cashflow_trade":
        return links.get("cashflow_trade") or f"/clients/{case.get('client_id')}/cashflow-trade-integrity"
    if key == "firm_data_integrity":
        return links.get("firm_data_integrity") or f"/data-integrity/client/{case.get('client_id')}"
    return links.get("data_integrity") or f"/clients/{case.get('client_id')}/data-integrity"


def _deterministic_attention(case: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    priority = 1
    sec_by_id = {s.get("id"): s for s in (case.get("sections") or [])}
    order = sorted(
        ctx.get("attention_sections") or [],
        key=lambda s: (0 if s.get("status") == "not_matched" else 1, s.get("section_id") or ""),
    )
    for sec in order:
        sid = sec.get("section_id") or "other_issues"
        play = _PLAYBOOKS.get(sid) or _PLAYBOOKS["other_issues"]
        findings = sec.get("findings") or []
        title = sec.get("title") or sid
        if sid == "prices" and findings:
            syms = []
            for f in findings[:3]:
                t = f.get("title") or ""
                if "·" in t:
                    syms.append(t.split("·", 1)[1].split("—")[0].strip())
            if syms:
                title = f"{title}: {', '.join(syms)}"
        cited = [f.get("fact_id") for f in findings if f.get("fact_id")][:8]
        items.append(
            {
                "section_id": sid,
                "priority": priority,
                "title": title,
                "status": sec.get("status"),
                "signal_ids": sec.get("signal_ids") or [],
                "why_it_matters": play["why"],
                "how_to_resolve": list(play["steps"]),
                "open_url": _url_for_section(sid, case, sec_by_id.get(sid)),
                "cited_fact_ids": cited,
                "finding_summaries": [f.get("title") for f in findings[:5] if f.get("title")],
            }
        )
        priority += 1

    if not items:
        return {
            "module_id": MODULE_ID,
            "source": "deterministic",
            "headline": f"No open data-integrity sections — overall {case.get('overall_label') or 'Matched'}.",
            "attention": [],
            "suggest_only": True,
        }

    blocking = sum(1 for i in items if i.get("status") == "not_matched")
    review = sum(1 for i in items if i.get("status") == "needs_review")
    headline = (
        f"{len(items)} area(s) need attention"
        + (f" ({blocking} not matched, {review} needs review)" if blocking or review else "")
        + f" for {case.get('client_name') or 'this client'}."
    )
    return {
        "module_id": MODULE_ID,
        "source": "deterministic",
        "headline": headline,
        "attention": items,
        "suggest_only": True,
    }


def _validate_llm_attention(
    raw: Any,
    *,
    allow_sections: set,
    allow_facts: set,
    case: Dict[str, Any],
    fallback: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    rows_in = raw.get("attention") or raw.get("items") or []
    if not isinstance(rows_in, list) or not rows_in:
        return None
    out_rows: List[Dict[str, Any]] = []
    for i, row in enumerate(rows_in[:8]):
        if not isinstance(row, dict):
            continue
        sid = str(row.get("section_id") or "").strip()
        if sid not in allow_sections:
            continue
        steps = row.get("how_to_resolve") or row.get("steps") or []
        if not isinstance(steps, list):
            steps = [str(steps)]
        steps = [str(s)[:240] for s in steps if str(s).strip()][:6]
        if not steps:
            continue
        cited = [c for c in (row.get("cited_fact_ids") or []) if c in allow_facts][:8]
        url = _url_for_section(sid, case)
        out_rows.append(
            {
                "section_id": sid,
                "priority": int(row.get("priority") or (i + 1)),
                "title": str(row.get("title") or sid)[:160],
                "status": str(row.get("status") or "")[:40],
                "signal_ids": [
                    str(x) for x in (row.get("signal_ids") or []) if str(x).strip()
                ][:6],
                "why_it_matters": str(row.get("why_it_matters") or row.get("why") or "")[:500],
                "how_to_resolve": steps,
                "open_url": url,
                "cited_fact_ids": cited,
                "finding_summaries": [
                    str(x)[:160]
                    for x in (row.get("finding_summaries") or [])
                    if str(x).strip()
                ][:5],
            }
        )
    if not out_rows:
        return None
    out_rows.sort(key=lambda r: r.get("priority") or 99)
    headline = str(raw.get("headline") or fallback.get("headline") or "")[:300]
    return {
        "module_id": MODULE_ID,
        "source": "llm",
        "headline": headline or fallback.get("headline"),
        "attention": out_rows,
        "suggest_only": True,
    }


def build_attention_analysis(
    client_id: int,
    *,
    case: Optional[Dict[str, Any]] = None,
    use_llm: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Analysis for sections that need attention: why + how to resolve.
    LLM optional; always fail closed to deterministic playbooks.
    """
    from services.client_data_integrity_case_service import build_case

    if case is None:
        case = build_case(client_id, include_g8_detail=True, live_g8=True)
    ctx = _compact_context(case)
    fallback = _deterministic_attention(case, ctx)

    if use_llm is False:
        return fallback
    if not ctx.get("attention_sections"):
        return fallback

    try:
        from services.assistant_llm import generate_json, local_ai_available

        if not local_ai_available():
            return fallback

        allow_sections = {
            s["section_id"] for s in ctx["attention_sections"] if s.get("section_id")
        }
        allow_facts = set()
        for s in ctx["attention_sections"]:
            for f in s.get("findings") or []:
                if f.get("fact_id"):
                    allow_facts.add(f["fact_id"])

        policy = _load_guidelines()
        prompt = (
            "You are an ops guide for Inertia data integrity. Suggest-only — never claim you fixed data.\n"
            "Go through EACH attention_section. For each: short analysis of what is wrong, "
            "why it matters for this client, and ordered how_to_resolve steps the advisor must do in the app.\n"
            "Cite only fact_ids from CONTEXT. Do not invent amounts, symbols, or issue ids.\n"
            "Prioritize not_matched before needs_review.\n"
            "CRITICAL: Reply with ONLY one JSON object. No markdown, no headings, no prose outside JSON.\n"
            f"POLICY:\n{policy}\n"
            f"CONTEXT:\n{ctx}\n"
            "JSON keys: headline (str), attention (array of objects with: "
            "section_id, priority, title, status, signal_ids, why_it_matters, "
            "how_to_resolve [str], cited_fact_ids, finding_summaries).\n"
            "section_id must be one of the CONTEXT attention_sections section_id values."
        )
        raw = generate_json(prompt, temperature=0.1, max_tokens=700)
        validated = _validate_llm_attention(
            raw,
            allow_sections=allow_sections,
            allow_facts=allow_facts,
            case=case,
            fallback=fallback,
        )
        if validated:
            by_id = {s["section_id"]: s for s in ctx["attention_sections"]}
            for row in validated["attention"]:
                if not row.get("finding_summaries"):
                    src = by_id.get(row["section_id"]) or {}
                    row["finding_summaries"] = [
                        f.get("title")
                        for f in (src.get("findings") or [])[:5]
                        if f.get("title")
                    ]
                if not row.get("status"):
                    src = by_id.get(row["section_id"]) or {}
                    row["status"] = src.get("status")
            return validated
    except Exception as exc:
        logger.warning("DI attention LLM failed for %s: %s", client_id, exc)

    return fallback
