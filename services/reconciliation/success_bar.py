"""
Success bar for multi-break holdings recon runs (Raghu-class).

After Refresh investigation, overall must satisfy one of:
- HIGH/MEDIUM with trusted primary (optional) and a reco/settle top reason
- LOW with incomplete messaging and no trusted primary banner
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def evaluate_run_success_bar(overall: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns {ok, mode, checks, failures}.
    ``ok`` True when the run meets the documented success bar.
    """
    conf = overall.get("investigation_confidence") or {}
    pc = overall.get("primary_cause") or {}
    level = (conf.get("level") or "").lower()
    trust = conf.get("trust_primary_cause")
    if trust is None:
        trust = level in ("high", "medium", "n/a")
    demoted = bool(pc.get("demoted"))
    top = conf.get("top_reasons") or []
    action = (conf.get("action") or "").strip()
    failures: List[str] = []
    checks: List[str] = []

    if level in ("", "n/a"):
        checks.append("no_breaks_or_na")
        return {"ok": True, "mode": "clean_or_na", "checks": checks, "failures": []}

    if level == "low":
        checks.append("low_confidence")
        if trust or (pc.get("trusted") is True and not demoted):
            failures.append("low_must_not_trust_primary_cause")
        if "incomplete" not in (conf.get("summary") or "").lower():
            failures.append("low_summary_must_say_incomplete")
        if not action:
            failures.append("low_needs_do_next_action")
        # Banner trust: demoted or missing trusted flag
        if pc.get("title") and not demoted and trust:
            failures.append("low_primary_still_trusted_in_payload")
        return {
            "ok": not failures,
            "mode": "low_incomplete",
            "checks": checks,
            "failures": failures,
        }

    # medium / high
    checks.append(f"confidence_{level}")
    cov = float(conf.get("combined_coverage_abs") or 0)
    if level == "high" and cov < 0.70:
        failures.append("high_requires_ge_70pct_coverage")
    if level == "medium" and cov < 0.40:
        failures.append("medium_requires_ge_40pct_coverage")
    if not trust:
        failures.append("medium_high_should_trust_primary_flag")
    if demoted:
        failures.append("medium_high_primary_should_not_be_demoted")
    if not top:
        failures.append("medium_high_needs_top_reason")
    else:
        subj = (top[0].get("subjects") or [None])[0] or top[0].get("title")
        if not subj:
            failures.append("top_reason_needs_subject_or_title")
        else:
            checks.append(f"top_reason:{str(subj)[:60]}")
    if not action:
        failures.append("medium_high_needs_do_next")

    return {
        "ok": not failures,
        "mode": f"trusted_{level}",
        "checks": checks,
        "failures": failures,
        "combined_coverage_abs": cov,
    }
