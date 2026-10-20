"""
Deterministic advisor narrative for holdings recon evidence packs.

No LLM/SLM — prose is built only from Python-ranked cause codes and checks.
"""
from __future__ import annotations

from typing import Any, Dict


def template_narrative(evidence_pack: Dict[str, Any]) -> Dict[str, Any]:
    """Advisor prose from ranked explanations (no model)."""
    code = evidence_pack.get("primary_cause_code") or "MIXED_BREAKS"
    title = evidence_pack.get("primary_cause_title") or code
    summary = evidence_pack.get("primary_cause_summary") or ""
    checks = list(evidence_pack.get("primary_cause_checks") or [])
    ranked = evidence_pack.get("ranked_explanations") or []
    decomp = evidence_pack.get("gap_decomposition") or {}

    bits = [f"{title}. {summary}".strip()]
    if ranked:
        bits.append(
            "Ranked explanations: "
            + "; ".join(
                f"#{i+1} [{e.get('score')}] {e.get('title')}"
                for i, e in enumerate(ranked[:4])
            )
            + "."
        )
    if decomp.get("median_stmt_over_sys") is not None:
        bits.append(
            f"Median stmt/sys among breaks: {decomp['median_stmt_over_sys']:.0%} "
            f"({decomp.get('partial_count', 0)} partial, "
            f"{decomp.get('missing_count', 0)} system-only)."
        )
    prose = " ".join(bits)
    next_checks = checks[:5]
    if not next_checks and ranked:
        for e in ranked[:2]:
            next_checks.extend(list(e.get("next_checks") or [])[:2])
        next_checks = next_checks[:5]
    if not next_checks:
        next_checks = [
            "Review investigation passes and break rows for residual names."
        ]
    return {
        "source": "template",
        "model": None,
        "prose": prose,
        "next_checks": next_checks,
        "cited_cause_code": code,
        "ok": True,
        "error": None,
    }


def narrate_investigation(evidence_pack: Dict[str, Any]) -> Dict[str, Any]:
    """Build template narrative from the evidence pack."""
    return template_narrative(evidence_pack)
