"""Initiating-coverage *detail checklist* — what we must try to find in documents.

Source of truth for *what* to collect (inspired by Telegram broker initiating notes:
PL, JM, Nirmal Bang, Nuvama, Antique, Nomura, Equirus, Motilal, …).

Design:
  - CHECKLIST = ordered research items with questions + evidence cues
  - Each item maps to one or more slots in business_slots.py
  - fill_checklist() scores documents into items → found / partial / missing
  - Missing items become research questions; found items feed the overview

This is intentionally richer than keyword topic dumps: multi-signal slot fill
plus an analyst-facing checklist status.
"""
from __future__ import annotations

from typing import Any

from iis.finance.business_slots import (
    SLOTS,
    SlotFill,
    fill_business_slots,
    slot_gaps,
    slot_research_questions,
    slots_markdown,
    slots_to_payload,
)
from iis.models.session import ResearchSession

# Ordered checklist an initiating coverage typically gathers before valuation.
# `slots` = which extractors feed this item; `ask` = question if missing.
CHECKLIST: list[dict[str, Any]] = [
    {
        "id": "business_one_liner",
        "section": "Business",
        "item": "What the company does (one clear sentence)",
        "ask": "In one sentence: what does it sell, to whom, and how does it make money?",
        "slots": ["what_they_do"],
        "priority": "must",
    },
    {
        "id": "products_portfolio",
        "section": "Business",
        "item": "Products / SKUs / applications / end-markets",
        "ask": "List top products and end-markets (agri, mining, construction, …) with any SKU or portfolio facts.",
        "slots": ["products_apps"],
        "priority": "must",
    },
    {
        "id": "revenue_mix",
        "section": "Mix",
        "item": "Revenue (and volume) mix by product / segment",
        "ask": "Get a 3-year revenue/volume mix table by product or segment (% of sales).",
        "slots": ["revenue_mix"],
        "priority": "must",
    },
    {
        "id": "geo_mix",
        "section": "Mix",
        "item": "Geography — domestic vs export and major regions",
        "ask": "Confirm India vs Outside India and Europe / Americas / RoW with % of sales; reconcile CIQ cuts vs AR.",
        "slots": ["geography"],
        "priority": "must",
    },
    {
        "id": "channel_mix",
        "section": "Mix",
        "item": "Channel mix — OEM vs replacement (or retail / B2B)",
        "ask": "What is OEM vs replacement (or equivalent) mix by volume/revenue, and which drives margin?",
        "slots": ["channel_mix"],
        "priority": "must",
    },
    {
        "id": "clients",
        "section": "Clients",
        "item": "Key clients / customer concentration / wallet share",
        "ask": "Who are largest OEM/channel customers? Top-1 / top-10 concentration? Repeat-customer %?",
        "slots": ["clients"],
        "priority": "must",
    },
    {
        "id": "market_position",
        "section": "Position",
        "item": "Market share / ranking / competitive set",
        "ask": "What share/ranking claims exist (global OHT, country agri replacement, …) and vs which peers?",
        "slots": ["market_position"],
        "priority": "should",
    },
    {
        "id": "moat_cost",
        "section": "Position",
        "item": "Cost advantage / vertical integration / differentiation",
        "ask": "What is the stated moat (cost, tech, integration, SKU breadth) and do margins support it?",
        "slots": ["moat_cost"],
        "priority": "should",
    },
    {
        "id": "industry_tam",
        "section": "Industry",
        "item": "Industry size / TAM / demand drivers",
        "ask": "What is addressable market size and the 2–3 demand drivers management emphasizes?",
        "slots": ["industry_context"],
        "priority": "should",
    },
    {
        "id": "capacity_capex",
        "section": "Operations",
        "item": "Capacity, utilisation, plants, capex plan",
        "ask": "Map plants/capacity/utilisation and whether capex is growth or maintenance; FCF inflection.",
        "slots": ["capacity_capex"],
        "priority": "must",
    },
    {
        "id": "pricing_rm",
        "section": "Operations",
        "item": "Pricing / ASP / key raw-material sensitivity",
        "ask": "How do ASP and RM (rubber, crude, etc.) move margins — any formula or lag disclosed?",
        "slots": ["moat_cost", "products_apps"],
        "priority": "should",
    },
    {
        "id": "management",
        "section": "Governance",
        "item": "Promoter / board / key management (brief)",
        "ask": "Promoter stake, key executives, any governance flags in AR.",
        "slots": [],  # filled later from governance dossier / AR
        "priority": "nice",
        "fallback_topics": ["governance"],
    },
]


def _status_from_fills(item: dict[str, Any], fills: dict[str, SlotFill]) -> tuple[str, list[str], float]:
    """Return status, snippets, best score for a checklist item."""
    slot_ids = item.get("slots") or []
    if not slot_ids:
        return "missing", [], 0.0
    snippets: list[str] = []
    best = 0.0
    confs = []
    for sid in slot_ids:
        f = fills.get(sid)
        if not f:
            confs.append("empty")
            continue
        confs.append(f.confidence)
        best = max(best, f.hits[0].score if f.hits else 0.0)
        snippets.extend(f.best_texts(4))
    # Drop AGM / IEPF / CSR admin noise even if a slot scored it
    try:
        from iis.finance.business_overview import _filter_snips

        snippets = _filter_snips(snippets, n=4)
    except Exception:
        snippets = snippets[:4]
    # Aggregate — empty evidence after filtering ⇒ missing/partial, not false "found"
    if all(c == "empty" for c in confs):
        return "missing", [], 0.0
    if not snippets:
        if any(c in ("ok", "strong", "weak") for c in confs):
            return "partial", [], best
        return "missing", [], best
    if any(c == "strong" for c in confs) or (any(c == "ok" for c in confs) and best >= 5):
        return "found", snippets[:4], best
    if any(c in ("ok", "weak") for c in confs):
        return "partial", snippets[:4], best
    return "missing", [], best


def build_business_checklist(session: ResearchSession) -> dict[str, Any]:
    """Run slot extractors and score every checklist item against documents."""
    fills = fill_business_slots(session, per_slot=4)
    items: list[dict[str, Any]] = []
    counts = {"found": 0, "partial": 0, "missing": 0}

    for raw in CHECKLIST:
        status, snippets, score = _status_from_fills(raw, fills)
        # Optional governance fallback from evidence dossier topics
        if status == "missing" and raw.get("fallback_topics"):
            snippets = _dossier_topic_snips(session, list(raw["fallback_topics"]), n=2)
            if snippets:
                status = "partial"
                score = 3.0
        counts[status] = counts.get(status, 0) + 1
        items.append({
            "id": raw["id"],
            "section": raw["section"],
            "item": raw["item"],
            "priority": raw["priority"],
            "status": status,
            "ask": raw["ask"],
            "snippets": snippets,
            "score": round(score, 2),
            "slots": raw.get("slots") or [],
        })

    open_asks = [
        it["ask"] for it in items
        if it["status"] in ("missing", "partial") and it["priority"] in ("must", "should")
    ]
    # Also include slot-level questions
    for q in slot_research_questions(fills):
        if q not in open_asks:
            open_asks.append(q)

    return {
        "company": session.company.name if session.company else "Company",
        "summary": counts,
        "coverage_pct": round(
            100.0 * (counts["found"] + 0.5 * counts["partial"]) / max(len(items), 1), 1
        ),
        "items": items,
        "open_asks": open_asks[:15],
        "slots": slots_to_payload(fills),
        "slot_gaps": slot_gaps(fills),
    }


def _dossier_topic_snips(session: ResearchSession, topics: list[str], n: int = 2) -> list[str]:
    try:
        from iis.finance.evidence_dossier import build_evidence_dossier

        d = build_evidence_dossier(session)
    except Exception:
        return []
    out: list[str] = []
    for hit in d.hits:
        if hit.topic not in topics:
            continue
        from pathlib import Path
        import re

        text = re.sub(r"\s+", " ", (hit.text or "")[:280]).strip()
        if len(text) < 40:
            continue
        src = Path(hit.source_file or "").name
        page = f" p.{hit.page}" if hit.page else ""
        out.append(f"{text} _({src}{page})_")
        if len(out) >= n:
            break
    return out


def checklist_markdown(payload: dict[str, Any] | None = None, session: ResearchSession | None = None) -> list[str]:
    p = payload or (build_business_checklist(session) if session else {})
    if not p:
        return []
    s = p.get("summary") or {}
    lines: list[str] = [
        "### Initiating-coverage detail checklist",
        "",
        f"_Coverage **{p.get('coverage_pct', 0)}%** — "
        f"found {s.get('found', 0)} · partial {s.get('partial', 0)} · "
        f"missing {s.get('missing', 0)} "
        f"(must-have business / mix / clients / capacity facts)._",
        "",
        "| Status | Section | Detail | Evidence (on file) |",
        "|---|---|---|---|",
    ]
    icon = {"found": "✓", "partial": "◐", "missing": "✗"}
    for it in p.get("items") or []:
        ev = " — ".join((it.get("snippets") or [])[:1]) or "—"
        ev = ev.replace("|", "/").replace("\n", " ")
        if len(ev) > 160:
            ev = ev[:157] + "…"
        lines.append(
            f"| {icon.get(it['status'], '?')} {it['status']} | {it['section']} | "
            f"{it['item']} | {ev} |"
        )
    lines.append("")

    # Found details expanded
    found = [it for it in (p.get("items") or []) if it["status"] in ("found", "partial") and it.get("snippets")]
    if found:
        lines.append("### Details found in documents")
        lines.append("")
        for it in found:
            lines.append(f"**{it['item']}** _{it['status']}_")
            for sn in it["snippets"][:3]:
                lines.append(f"- {sn}")
            lines.append("")

    missing = [it for it in (p.get("items") or []) if it["status"] == "missing" and it["priority"] == "must"]
    if missing:
        lines.append("### Still missing (must-have)")
        lines.append("")
        for it in missing:
            lines.append(f"- **{it['item']}** — {it['ask']}")
        lines.append("")

    asks = p.get("open_asks") or []
    if asks:
        lines.append("### Research questions to carry forward")
        lines.append("")
        for i, q in enumerate(asks[:10], 1):
            lines.append(f"{i}. {q}")
        lines.append("")
    return lines


def run_checklist_with_slots(session: ResearchSession) -> tuple[dict[str, Any], dict[str, SlotFill]]:
    fills = fill_business_slots(session)
    payload = build_business_checklist(session)
    return payload, fills


# Re-export for callers that want slot markdown alongside checklist
__all__ = [
    "CHECKLIST",
    "SLOTS",
    "build_business_checklist",
    "checklist_markdown",
    "fill_business_slots",
    "slots_markdown",
    "run_checklist_with_slots",
]
