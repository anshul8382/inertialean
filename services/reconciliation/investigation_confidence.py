"""
Investigation confidence from the coverage ledger.

Ops rule: 1–2 reasons must cover most of |Δ| (ledger, no double-count),
or confidence is low and ranked primary cause must not be trusted.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.reconciliation.coverage_ledger import (
    FULL_COVER,
    PARTIAL_COVER,
    apply_primary_cause_trust,
    build_coverage_ledger,
)

# Re-export thresholds for tests / callers
__all__ = [
    "FULL_COVER",
    "PARTIAL_COVER",
    "assess_investigation_confidence",
    "apply_primary_cause_trust",
]


def assess_investigation_confidence(
    *,
    breaks: List[Dict[str, Any]],
    email_cluster: Optional[Dict[str, Any]] = None,
    historical_sell_batch: Optional[Dict[str, Any]] = None,
    security_reco_thread: Optional[Dict[str, Any]] = None,
    timing: Optional[Dict[str, Any]] = None,
    allocation: Optional[Dict[str, Any]] = None,
    matched_count: int = 0,
) -> Dict[str, Any]:
    """
    high   — ledger attributes ≥70% of |Δ| to 1–2 reasons
    medium — 40–70%
    low    — <40% with several breaks → incomplete; trust_primary_cause=False
    n/a    — no breaks
    """
    led = build_coverage_ledger(
        breaks=breaks,
        email_cluster=email_cluster,
        historical_sell_batch=historical_sell_batch,
        security_reco_thread=security_reco_thread,
        timing=timing,
        allocation=allocation,
        matched_count=matched_count,
    )
    # Prefer crisp advisor story — do not append ledger jargon for the UI
    if allocation and led.get("level") != "n/a":
        led = dict(led)
        story = allocation.get("advisor_story") or {}
        if story.get("headline"):
            led["summary"] = story["headline"]
            led["action"] = story.get("next") or allocation.get("action")
            led["advisor_story"] = story
            led["guidance"] = [g for g in (story.get("headline"), story.get("next")) if g]
        elif allocation.get("action"):
            led["action"] = allocation.get("action")
            led["guidance"] = [led["action"]]
    return led
