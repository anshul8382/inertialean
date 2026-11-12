"""Cross-pack joins: management vs broker, commentary themes."""
from __future__ import annotations

import re
from typing import Any

from iis.finance.packs.models import new_claim

_PARAM_ALIASES = {
    "revenue_growth": ["revenue_growth", "revenue", "top line"],
    "ebitda_margins": ["ebitda_margins", "ebitda", "margin"],
    "capex": ["capex", "capital expenditure"],
    "capacity": ["capacity", "utilisation", "utilization"],
    "volume_growth": ["volume_growth", "volume"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _match_param(param: str) -> str | None:
    p = _norm(param)
    for canon, aliases in _PARAM_ALIASES.items():
        if any(a in p or p in a for a in aliases):
            return canon
    return None


def build_mgmt_vs_broker(
    concall_pack: dict[str, Any],
    broker_pack: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare explicit concall guidance vs broker estimate excerpts."""
    mgmt: dict[str, list[dict[str, Any]]] = {}
    for c in (concall_pack.get("sections") or {}).get("guidance_outlook") or []:
        if c.get("claim_type") != "explicit":
            continue
        key = _match_param(str(c.get("parameter") or ""))
        if not key:
            continue
        mgmt.setdefault(key, []).append(c)

    street: dict[str, list[dict[str, Any]]] = {}
    for sec in ("financial_estimates", "earnings_assumptions", "investment_thesis"):
        for c in (broker_pack.get("sections") or {}).get(sec) or []:
            key = _match_param(str(c.get("parameter") or ""))
            if not key:
                # also scan quote
                key = _match_param(str(c.get("quote") or ""))
            if not key:
                continue
            street.setdefault(key, []).append(c)

    gaps: list[dict[str, Any]] = []
    for key in sorted(set(mgmt) | set(street)):
        m = (mgmt.get(key) or [None])[0]
        b = (street.get(key) or [None])[0]
        if not m and not b:
            continue
        gaps.append(
            new_claim(
                section="mgmt_vs_broker",
                parameter=key,
                quote=(
                    f"Parameter `{key}` — "
                    f"Management: {(m or {}).get('quote', '—')[:220]} || "
                    f"Brokerage: {(b or {}).get('quote', '—')[:220]}"
                ),
                claim_type="opinion",
                reliability="medium",
                value={
                    "mgmt_value": (m or {}).get("value"),
                    "broker_value": (b or {}).get("value"),
                    "mgmt_id": (m or {}).get("id"),
                    "broker_id": (b or {}).get("id"),
                },
                extra={"has_mgmt": bool(m), "has_broker": bool(b)},
            )
        )
    return gaps[:20]


def build_commentary_matrix_stub(concall_pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Lightweight topic presence flags from current concall pack (multi-call later)."""
    topics = {
        "demand_outlook": ["demand", "outlook", "slowdown", "strong"],
        "margin_outlook": ["margin", "ebitda", "pressure"],
        "capex_timeline": ["capex", "commission", "delay"],
        "competition": ["compet", "import", "market share"],
    }
    sections = concall_pack.get("sections") or {}
    all_quotes = []
    for claims in sections.values():
        if isinstance(claims, list):
            all_quotes.extend(str(c.get("quote") or "").lower() for c in claims)

    blob = " ".join(all_quotes)
    rows: list[dict[str, Any]] = []
    for topic, cues in topics.items():
        hits = [c for c in cues if c in blob]
        tone = "mentioned" if hits else "not_found"
        if any(x in blob for x in ("weak", "pressure", "declin", "delay", "intense")):
            if topic in ("demand_outlook", "margin_outlook", "competition", "capex_timeline"):
                for bad in ("weak", "pressure", "declin", "delay", "intense"):
                    if bad in blob and any(c in blob for c in cues):
                        tone = f"cautious:{bad}"
                        break
        rows.append(
            new_claim(
                section="commentary_changes",
                parameter=topic,
                quote=f"Current-call signal for `{topic}`: {tone} (cues hit: {hits or 'none'})",
                claim_type="qualitative",
                reliability="low",
                extra={"cues_hit": hits, "note": "Multi-call matrix needs 4–8 stored packs; this is current-call stub"},
            )
        )
    return rows
