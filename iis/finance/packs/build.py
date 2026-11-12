"""Build all research packs, persist JSON/MD, attach session artifact."""
from __future__ import annotations

import logging
from typing import Any

from iis.finance.packs.ar_pack import build_ar_pack
from iis.finance.packs.broker_pack import build_broker_pack
from iis.finance.packs.concall_pack import build_concall_pack
from iis.finance.packs.joins import build_commentary_matrix_stub, build_mgmt_vs_broker
from iis.finance.packs.render import render_pack_markdown
from iis.finance.packs.store import read_pack_markdown, write_pack
from iis.models.artifacts import AgentArtifact
from iis.models.enums import SourceTier
from iis.models.session import ResearchSession

logger = logging.getLogger(__name__)


def build_all_research_packs(session: ResearchSession) -> dict[str, Any]:
    """Create AR / concall / broker packs + joins; write to company packs/ dir."""
    summary: dict[str, Any] = {"ok": True, "packs": {}, "paths": {}}
    cc: dict[str, Any] | None = None

    try:
        ar = build_ar_pack(session)
        ar_md = render_pack_markdown(ar, title="Annual report research pack")
        paths = write_pack(session, "ar_pack", ar, ar_md)
        summary["packs"]["ar"] = {"claims": ar.get("claim_count"), "sections": list((ar.get("sections") or {}).keys())}
        summary["paths"]["ar"] = paths
    except Exception as exc:
        logger.info("ar_pack failed: %s", exc)
        summary["packs"]["ar"] = {"error": str(exc)[:200]}

    try:
        cc = build_concall_pack(session)
        cc["sections"]["commentary_changes"] = build_commentary_matrix_stub(cc)
        from iis.finance.packs.models import finalize_pack

        cc = finalize_pack(cc)
        cc_md = render_pack_markdown(cc, title="Concall research pack")
        # Standard template appendix
        cc_md += "\n\n" + _concall_template_skeleton(cc)
        paths = write_pack(session, "concall_pack", cc, cc_md)
        summary["packs"]["concall"] = {"claims": cc.get("claim_count"), "qa_themes": (cc.get("meta") or {}).get("repeated_qa_themes")}
        summary["paths"]["concall"] = paths
    except Exception as exc:
        logger.info("concall_pack failed: %s", exc)
        summary["packs"]["concall"] = {"error": str(exc)[:200]}
        cc = None

    try:
        br = build_broker_pack(session)
        if cc is not None:
            gaps = build_mgmt_vs_broker(cc, br)
            br["sections"]["mgmt_vs_broker"] = gaps
            from iis.finance.packs.models import finalize_pack

            br = finalize_pack(br)
        br_md = render_pack_markdown(br, title="Brokerage research pack (STREET views)")
        br_md += "\n\n> **Label:** All estimates, TPs and ratings are brokerage opinions — test against AR/CIQ/concall.\n"
        paths = write_pack(session, "broker_pack", br, br_md)
        summary["packs"]["broker"] = {"claims": br.get("claim_count"), "mgmt_vs_broker": len((br.get("sections") or {}).get("mgmt_vs_broker") or [])}
        summary["paths"]["broker"] = paths
    except Exception as exc:
        logger.info("broker_pack failed: %s", exc)
        summary["packs"]["broker"] = {"error": str(exc)[:200]}

    # Cross-source index
    try:
        index = {
            "ar": summary["packs"].get("ar"),
            "concall": summary["packs"].get("concall"),
            "broker": summary["packs"].get("broker"),
            "paths": summary.get("paths"),
            "reading_order": [
                "1. AR business / governance / risks",
                "2. CIQ numbers + guidance track (in concall_pack K)",
                "3. Concall guidance (explicit only for forecasts) + Q&A",
                "4. Broker thesis / estimates / valuation — stress-test vs 1–3",
                "5. Mgmt vs broker gaps",
            ],
        }
        from iis.finance.packs.store import packs_dir
        import json

        ip = packs_dir(session) / "index.json"
        ip.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
        summary["paths"]["index"] = str(ip)
    except Exception:
        pass

    # Session artifact for UI / tools
    session.agent_artifacts = [a for a in session.agent_artifacts if a.artifact_type != "research_packs"]
    session.agent_artifacts.append(
        AgentArtifact(
            agent_id="research_packs",
            artifact_type="research_packs",
            payload=summary,
            source_tier=SourceTier.EXTRACTED,
        )
    )
    return summary


def packs_seed_markdown(session: ResearchSession, *, max_chars: int = 10000) -> str:
    """Compact seed for LLM analyst: pack excerpts + reading order."""
    parts = [
        "=== RESEARCH PACKS (Python-retrieved; cite claim IDs) ===",
        "Use get_research_pack tool for full sections. STREET = brokerage only.",
        "",
    ]
    for name, label in (
        ("ar_pack", "ANNUAL REPORT PACK"),
        ("concall_pack", "CONCALL PACK"),
        ("broker_pack", "BROKERAGE PACK (STREET)"),
    ):
        body = read_pack_markdown(session, name, max_chars=3500)
        if body:
            parts.append(f"--- {label} ---")
            parts.append(body[:3500])
            parts.append("")
    text = "\n".join(parts)
    return text[:max_chars]


def _concall_template_skeleton(pack: dict[str, Any]) -> str:
    """Standard concall research template headings with claim counts."""
    sec = pack.get("sections") or {}
    mapping = [
        ("1. Executive summary", "guidance_outlook"),
        ("2. Business performance", "growth_quality"),
        ("3. Forward guidance", "guidance_outlook"),
        ("4. Margin and profitability", "margins"),
        ("5. Capex and capital allocation", "capex_capacity"),
        ("6. Q&A insights", "qa_insights"),
        ("7. Guidance tracking", "guidance_vs_actual"),
        ("8. Red flags and concerns", "management_credibility"),
        ("9. Changes in investment thesis", "commentary_changes"),
        ("10. Analyst conclusion", "risks_challenges"),
    ]
    lines = ["# Standard concall research template", ""]
    for title, key in mapping:
        n = len(sec.get(key) or [])
        lines.append(f"## {title}")
        lines.append(f"_Source section `{key}` — {n} claims on file. Fill from claims above; do not invent._")
        lines.append("")
    return "\n".join(lines)
