"""
Client Health Phrase Service
============================
LLM **interprets** ranked violations into short UI copy.
The **output wrapper** takes the final call: validate against the allowlist,
cap lengths, and fall back to deterministic text.

Never creates tasks, alerts, or assignments.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from services.client_health_policy_context import load_client_health_llm_context
from services.client_health_priority_service import (
    MAX_RECOMMENDATIONS,
    MAX_VIOLATIONS,
    deterministic_improvement_action,
    deterministic_recommendations,
    rank_violations,
)

logger = logging.getLogger(__name__)

# In-process cache: fingerprint → (expires_at, payload). Avoids re-hitting Ollama
# on every client-details refresh when nothing changed.
_PHRASE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SEC = 6 * 3600


def client_health_llm_enabled() -> bool:
    """Interactive LLM phrasing is opt-in (CPU-heavy). Default off; fail closed."""
    flag = os.environ.get("CLIENT_HEALTH_USE_LLM", "").lower()
    return flag in ("1", "true", "yes", "on")


def _load_guidelines_excerpt(
    max_chars: int = 3500,
    signal_ids: Optional[List[str]] = None,
) -> str:
    return load_client_health_llm_context(max_chars=max_chars, signal_ids=signal_ids)


def _fingerprint(ranked: List[Dict[str, Any]]) -> str:
    key_parts = []
    for v in ranked:
        key_parts.append(
            f"{v.get('alert_id')}|{v.get('severity')}|{v.get('title')}|{v.get('age_hours')}"
        )
    raw = "\n".join(key_parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _facts_for_llm(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Minimize fields sent to the model."""
    facts = []
    for v in ranked:
        facts.append(
            {
                "alert_id": v.get("alert_id"),
                "severity": v.get("severity"),
                "type": v.get("type"),
                "subtype": v.get("subtype"),
                "title": v.get("title"),
                "age_hours": v.get("age_hours"),
                "domain": v.get("domain"),
                "priority_band": v.get("priority_band"),
                "deterministic_action": v.get("improvement_action")
                or deterministic_improvement_action(v),
            }
        )
    return facts


def _build_prompt(facts: List[Dict[str, Any]], guidelines: str) -> str:
    return (
        "You interpret client-health alert facts for an advisor UI. "
        "You do NOT decide which alerts matter — the list is already ranked.\n"
        "Return JSON only with keys: violations, recommendations.\n"
        "violations: array of {alert_id, title, improvement_action} — only for alert_ids given.\n"
        "recommendations: array of at most 3 short imperative strings.\n"
        "Do not invent alerts, numbers, or tasks. No emoji.\n\n"
        f"## Guidelines (excerpt)\n{guidelines}\n\n"
        f"## Ranked facts (JSON)\n{json.dumps(facts, default=str)}\n"
    )


def _apply_deterministic_actions(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for v in ranked:
        item = dict(v)
        if not item.get("improvement_action"):
            item["improvement_action"] = deterministic_improvement_action(item)
        out.append(item)
    return out


def _validate_llm_payload(
    proposed: Any,
    ranked: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Output wrapper: accept only allowlisted alert_ids; cap counts."""
    if not isinstance(proposed, dict):
        return None
    allow = {v.get("alert_id") for v in ranked if v.get("alert_id") is not None}
    by_id = {v.get("alert_id"): v for v in ranked if v.get("alert_id") is not None}

    raw_violations = proposed.get("violations") or []
    if not isinstance(raw_violations, list):
        return None

    merged: List[Dict[str, Any]] = []
    seen = set()
    for row in raw_violations:
        if not isinstance(row, dict):
            continue
        aid = row.get("alert_id")
        if aid not in allow or aid in seen:
            continue
        seen.add(aid)
        base = dict(by_id[aid])
        title = (row.get("title") or base.get("title") or "").strip()
        action = (row.get("improvement_action") or base.get("improvement_action") or "").strip()
        if not title:
            title = base.get("title") or "Alert"
        if not action:
            action = deterministic_improvement_action(base)
        # Bound length so the card stays focused
        base["title"] = title[:120]
        base["improvement_action"] = action[:240]
        base["copy_source"] = "llm"
        merged.append(base)
        if len(merged) >= MAX_VIOLATIONS:
            break

    # Fill any missing ranked slots from deterministic (final call keeps full set)
    for v in ranked:
        if len(merged) >= MAX_VIOLATIONS:
            break
        aid = v.get("alert_id")
        if aid in seen:
            continue
        item = dict(v)
        item["improvement_action"] = item.get("improvement_action") or deterministic_improvement_action(item)
        item["copy_source"] = "deterministic"
        merged.append(item)
        seen.add(aid)

    raw_recs = proposed.get("recommendations") or []
    recs: List[str] = []
    if isinstance(raw_recs, list):
        for line in raw_recs:
            if not isinstance(line, str):
                continue
            text = " ".join(line.split()).strip()
            if not text or len(text) < 8:
                continue
            # Reject coaching / assignment language
            low = text.lower()
            if "task assignment" in low or "create ops" in low:
                continue
            recs.append(text[:200])
            if len(recs) >= MAX_RECOMMENDATIONS:
                break

    if not recs:
        recs = deterministic_recommendations(merged)

    return {"violations": merged, "recommendations": recs[:MAX_RECOMMENDATIONS]}


def phrase_health_copy(
    violations: List[Dict[str, Any]],
    *,
    use_llm: Optional[bool] = None,
) -> Dict[str, Any]:
    """Rank → (optional LLM interpret) → output wrapper final call.

    Returns ``{violations, recommendations, copy_source}``.
    """
    ranked = _apply_deterministic_actions(rank_violations(violations))
    deterministic = {
        "violations": ranked,
        "recommendations": deterministic_recommendations(ranked),
        "copy_source": "deterministic",
    }

    if use_llm is None:
        use_llm = client_health_llm_enabled()
    if not use_llm or not ranked:
        return deterministic

    fp = _fingerprint(ranked)
    now = time.time()
    cached = _PHRASE_CACHE.get(fp)
    if cached and cached[0] > now:
        return cached[1]

    try:
        from services.assistant_llm import generate_json, local_ai_available

        if not local_ai_available():
            return deterministic

        signal_ids = [
            str(v.get("signal_id") or v.get("subtype") or "").strip()
            for v in ranked
            if str(v.get("signal_id") or v.get("subtype") or "").strip()
        ]
        proposed = generate_json(
            _build_prompt(
                _facts_for_llm(ranked),
                _load_guidelines_excerpt(signal_ids=signal_ids or None),
            ),
            max_tokens=280,
            temperature=0.1,
        )
        validated = _validate_llm_payload(proposed, ranked)
        if not validated:
            return deterministic
        result = {
            "violations": validated["violations"],
            "recommendations": validated["recommendations"],
            "copy_source": "llm",
        }
        _PHRASE_CACHE[fp] = (now + _CACHE_TTL_SEC, result)
        return result
    except Exception as exc:
        logger.warning("Client health LLM phrasing failed; using deterministic: %s", exc)
        return deterministic
