"""
Single |Δ| attribution ledger: each break's abs delta assigned to at most
one reason (no double-counting across near cluster / hist SELL / settle).

Ops rule: if the best 1–2 reasons do not cover most of total |Δ|, the
investigation is incomplete — do not trust a ranked “best explanation.”
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


FULL_COVER = 0.70
PARTIAL_COVER = 0.40


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _syms_from_reason_source(src: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for s in src.get("explained_symbols") or []:
        n = _norm_sym(s)
        if n:
            out.add(n)
    for s in src.get("touched_symbols") or []:
        n = _norm_sym(s)
        if n:
            out.add(n)
    # details list from historical sell
    for d in src.get("details") or []:
        if isinstance(d, dict):
            n = _norm_sym(d.get("symbol"))
            if n:
                out.add(n)
    return out


def _candidate_pools(
    *,
    email_cluster: Optional[Dict[str, Any]],
    historical_sell_batch: Optional[Dict[str, Any]],
    security_reco_thread: Optional[Dict[str, Any]],
    timing: Optional[Dict[str, Any]],
    breaks: List[Dict[str, Any]],
    allocation: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Ordered candidate reasons with symbol sets (prefer stronger probes first)."""
    cluster = email_cluster or {}
    sell = historical_sell_batch or {}
    thread = security_reco_thread or {}
    timing = timing or {}
    allocation = allocation or {}
    pools: List[Dict[str, Any]] = []

    if timing.get("applied"):
        # Settlement claims all breaks when applied (batch clear)
        all_syms = {_norm_sym(b.get("symbol")) for b in breaks if _norm_sym(b.get("symbol"))}
        pools.append(
            {
                "code": "SETTLEMENT_LAG",
                "title": "Settlement / as-of lag",
                "symbols": all_syms,
                "subjects": [],
                "sent_ats": [],
                "priority": 0,
            }
        )

    # Detective allocation focus 1–2 uncleared recos (highest priority after settle)
    # Prefer qty-explainable symbols so BAJFINANCE-style leftovers don't steal ledger cover
    for fr in (allocation.get("focus_recos") or [])[:2]:
        prefer = fr.get("explained_symbols") or fr.get("cover_symbols") or []
        syms = {_norm_sym(s) for s in prefer if _norm_sym(s)}
        if not syms:
            continue
        pools.append(
            {
                "code": "ALLOCATION_FOCUS_RECO",
                "title": f"Allocated reco: {(fr.get('subject') or '')[:60]}",
                "symbols": syms,
                "subjects": [fr.get("subject")],
                "sent_ats": [str(fr.get("sent_at") or "")[:10]],
                "priority": 1,
            }
        )
    for iss in (allocation.get("specific_security_issues") or [])[:12]:
        n = _norm_sym(iss.get("symbol"))
        if not n:
            continue
        pools.append(
            {
                "code": "ALLOCATION_SPECIFIC_SECURITY",
                "title": f"Specific security on cleared reco: {n}",
                "symbols": {n},
                "subjects": [iss.get("reco_subject")],
                "sent_ats": [str(iss.get("reco_sent_at") or "")[:10]],
                "priority": 1,
            }
        )

    if cluster.get("found") or _syms_from_reason_source(cluster):
        emails = cluster.get("emails") or []
        pools.append(
            {
                "code": "NEAR_RECO_CLUSTER",
                "title": "Near-as-of reco email cluster",
                "symbols": _syms_from_reason_source(cluster),
                "subjects": [e.get("subject") for e in emails if isinstance(e, dict)],
                "sent_ats": [str(e.get("sent_at") or "")[:10] for e in emails if isinstance(e, dict)],
                "priority": 2,
            }
        )

    if sell.get("found") or _syms_from_reason_source(sell) or sell.get("message"):
        emails = sell.get("emails") or []
        pools.append(
            {
                "code": "HISTORICAL_SELL_BATCH",
                "title": "Full-archive SELL reco batch",
                "symbols": _syms_from_reason_source(sell),
                "subjects": [e.get("subject") for e in emails if isinstance(e, dict)],
                "sent_ats": [str(e.get("sent_at") or "")[:10] for e in emails if isinstance(e, dict)],
                "priority": 3,
            }
        )

    for r in (thread.get("highlighted_recos") or [])[:3]:
        syms = set()
        for s in r.get("touched_symbols") or r.get("issue_symbols") or []:
            n = _norm_sym(s)
            if n:
                syms.add(n)
        if not syms:
            continue
        pools.append(
            {
                "code": "HIGHLIGHTED_RECO",
                "title": f"Reco: {(r.get('subject') or '')[:60]}",
                "symbols": syms,
                "subjects": [r.get("subject")],
                "sent_ats": [str(r.get("sent_at") or "")[:10]],
                "priority": 4,
            }
        )

    # Per-security explainable (≤2 breaks path)
    if len(breaks) <= 2 and thread.get("mode") == "security_wise":
        syms = set()
        for p in thread.get("per_security") or []:
            if p.get("explainable"):
                n = _norm_sym(p.get("symbol"))
                if n:
                    syms.add(n)
        if syms:
            pools.append(
                {
                    "code": "PER_SECURITY_RECO",
                    "title": "Security-wise reco/trade explainability",
                    "symbols": syms,
                    "subjects": [],
                    "sent_ats": [],
                    "priority": 5,
                }
            )

    pools.sort(key=lambda p: p["priority"])
    return pools


def build_coverage_ledger(
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
    Attribute each break's |Δ| to at most one reason.
    Returns ledger rows + combined coverage + trust flags for the UI.
    """
    breaks = list(breaks or [])
    n = len(breaks)
    total_abs = sum(abs(float(b.get("delta") or 0)) for b in breaks) or 0.0

    if n == 0 or total_abs <= 0:
        return {
            "rows": [],
            "attributions": [],
            "unattributed_symbols": [],
            "reasons": [],
            "top_reasons": [],
            "combined_coverage_abs": 1.0,
            "level": "n/a",
            "score": 100,
            "process_ok": True,
            "trust_primary_cause": True,
            "summary": "No qty breaks — nothing to explain.",
            "action": "",
            "break_count": 0,
            "matched_count": matched_count,
            "total_abs_delta_qty": 0.0,
            "full_cover_threshold": FULL_COVER,
            "partial_cover_threshold": PARTIAL_COVER,
        }

    pools = _candidate_pools(
        email_cluster=email_cluster,
        historical_sell_batch=historical_sell_batch,
        security_reco_thread=security_reco_thread,
        timing=timing,
        breaks=breaks,
        allocation=allocation,
    )

    # Claim breaks greedily by pool priority (no double-count)
    claimed: Dict[str, str] = {}  # symbol -> reason code
    reason_abs: Dict[str, float] = {}
    reason_meta: Dict[str, Dict[str, Any]] = {}
    attributions: List[Dict[str, Any]] = []

    for pool in pools:
        code = pool["code"]
        if code not in reason_meta:
            reason_meta[code] = {
                "code": code,
                "title": pool["title"],
                "subjects": list(pool.get("subjects") or [])[:3],
                "sent_ats": list(pool.get("sent_ats") or [])[:3],
                "symbols": [],
                "abs_delta": 0.0,
            }
        for b in breaks:
            sym = _norm_sym(b.get("symbol"))
            if not sym or sym in claimed:
                continue
            if sym not in pool["symbols"]:
                continue
            abs_d = abs(float(b.get("delta") or 0))
            claimed[sym] = code
            reason_abs[code] = reason_abs.get(code, 0.0) + abs_d
            reason_meta[code]["symbols"].append(sym)
            reason_meta[code]["abs_delta"] = reason_abs[code]
            attributions.append(
                {
                    "symbol": sym,
                    "abs_delta": abs_d,
                    "delta": float(b.get("delta") or 0),
                    "reason_code": code,
                    "reason_title": pool["title"],
                }
            )

    unattributed = []
    for b in breaks:
        sym = _norm_sym(b.get("symbol"))
        if not sym or sym in claimed:
            continue
        unattributed.append(
            {
                "symbol": sym,
                "abs_delta": abs(float(b.get("delta") or 0)),
                "delta": float(b.get("delta") or 0),
            }
        )

    attributed_abs = sum(reason_abs.values())
    combined = attributed_abs / total_abs if total_abs else 0.0

    reasons: List[Dict[str, Any]] = []
    for code, meta in reason_meta.items():
        cov = (meta["abs_delta"] / total_abs) if total_abs else 0.0
        if cov < 0.01 and not meta["symbols"]:
            continue
        if cov >= FULL_COVER:
            depth = "full"
        elif cov >= PARTIAL_COVER:
            depth = "partial"
        elif cov > 0.05:
            depth = "weak"
        else:
            depth = "none"
        when = (meta.get("sent_ats") or [""])[0]
        reasons.append(
            {
                "code": code,
                "title": meta["title"],
                "coverage_abs_delta": round(cov, 3),
                "coverage_count": round(len(meta["symbols"]) / n, 3) if n else 0.0,
                "depth": depth,
                "subjects": meta.get("subjects") or [],
                "sent_ats": meta.get("sent_ats") or [],
                "symbols": meta["symbols"][:20],
                "abs_delta_attributed": round(meta["abs_delta"], 3),
                "one_liner": (
                    f"{depth}: {cov*100:.0f}% |Δ|"
                    + (f" · {when}" if when else "")
                    + (
                        f" · {', '.join(meta['symbols'][:5])}"
                        if meta["symbols"]
                        else ""
                    )
                ),
            }
        )
    reasons.sort(key=lambda r: -float(r["coverage_abs_delta"]))
    top_reasons = reasons[:2]

    if combined >= FULL_COVER:
        level = "high"
        process_ok = True
        trust_primary = True
        summary = f"1–2 reasons cover {combined*100:.0f}% of |Δ| (ledger, no double-count)."
    elif combined >= PARTIAL_COVER:
        level = "medium"
        process_ok = True
        trust_primary = True
        summary = f"Partial: ledger attributes {combined*100:.0f}% of |Δ| to 1–2 reasons."
    else:
        level = "low"
        process_ok = n <= 2
        trust_primary = False  # ops rule: do not trust ranked best cause
        summary = (
            f"Only {combined*100:.0f}% of |Δ| attributed ({n} breaks) — "
            f"investigation incomplete; do not trust a single cause label."
        )

    score = int(round(100 * combined))
    if level == "high":
        score = max(score, 70)
    elif level == "medium":
        score = max(40, min(score, 69))
    else:
        score = min(score, 39)

    action = ""
    if level == "low" and n > 2:
        action = (
            "Incomplete: find 1–2 reco emails covering most break |Δ| "
            "(full archive), or confirm statement scope — then refresh."
        )
    elif top_reasons:
        r0 = top_reasons[0]
        when = (r0.get("sent_ats") or [""])[0]
        subj = (r0.get("subjects") or [r0.get("title")])[0] or r0["title"]
        if level == "high":
            action = f"Start here: {subj}" + (f" ({when})" if when else "") + "."
        else:
            action = (
                f"Book/reconcile “{subj}”"
                + (f" ({when})" if when else "")
                + "; then security-wise the residual breaks."
            )
    elif timing.get("applied"):
        action = "Re-match at the shifted as-of from settlement lag."

    return {
        "rows": attributions,
        "attributions": attributions,
        "unattributed_symbols": [u["symbol"] for u in unattributed],
        "unattributed": unattributed[:30],
        "reasons": reasons[:8],
        "top_reasons": top_reasons,
        "combined_coverage_abs": round(combined, 3),
        "level": level,
        "score": score,
        "process_ok": process_ok,
        "trust_primary_cause": trust_primary,
        "summary": summary,
        "action": action,
        "guidance": [action] if action else [],
        "break_count": n,
        "matched_count": matched_count,
        "total_abs_delta_qty": total_abs,
        "full_cover_threshold": FULL_COVER,
        "partial_cover_threshold": PARTIAL_COVER,
        "attributed_abs_delta": round(attributed_abs, 3),
    }


def apply_primary_cause_trust(
    primary_cause: Optional[Dict[str, Any]],
    ledger: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    When ledger says do not trust primary, demote to untrusted candidate
    (UI should hide / collapse it).
    """
    if not primary_cause:
        return primary_cause
    pc = dict(primary_cause)
    if ledger.get("trust_primary_cause"):
        pc["trusted"] = True
        pc["demoted"] = False
        return pc
    pc["trusted"] = False
    pc["demoted"] = True
    pc["demote_reason"] = (
        ledger.get("summary")
        or "Low ledger coverage — ranked cause is not trusted."
    )
    # Keep code/title for details, but flag for UI
    return pc
