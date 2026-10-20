"""
Second investigation thread: matched / mismatched inventory, then
security-wise (≤2 breaks) or reco-wise concentration (several breaks).

Does not pick a "winner" cause — surfaces facts for advisors.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _email_key(e: Dict[str, Any]) -> str:
    return str(e.get("id") or e.get("sent_at") or e.get("subject") or "?")


def _sell_buy_for_symbol(
    email: Dict[str, Any], symbol: str
) -> Dict[str, Any]:
    sym = _norm_sym(symbol)
    buy = 0.0
    sell = 0.0
    lines_hit = []
    for ln in email.get("lines") or email.get("body_reco_hints") or []:
        if _norm_sym(ln.get("symbol")) != sym:
            continue
        act = (ln.get("action") or "").strip().lower()
        q = ln.get("quantity")
        if q is None:
            continue
        qf = float(q)
        if act == "buy":
            buy += qf
        elif act == "sell":
            sell += qf
        lines_hit.append(
            {"action": act.upper(), "quantity": qf, "symbol": sym}
        )
    return {"buy": buy, "sell": sell, "lines": lines_hit}


def _security_review(break_row: Dict[str, Any]) -> Dict[str, Any]:
    """Per-security: is gap explainable from reco / trades already on the row?"""
    d = break_row.get("diagnosis") or {}
    sym = _norm_sym(break_row.get("symbol")) or str(break_row.get("security_id") or "?")
    delta = float(break_row.get("delta") or 0)
    if_applied = d.get("if_reco_applied") or {}
    three_way = d.get("three_way") or {}
    story = d.get("trade_story") or {}
    reco = d.get("reco") or {}
    emails = d.get("emails") or {}
    hints = list(emails.get("symbol_hints") or [])

    explainable = bool(if_applied.get("explains_gap")) or bool(
        three_way.get("explains_gap")
    )
    issues: List[str] = []
    if not explainable and abs(delta) > 0.001:
        if hints:
            issues.append(
                "Reco/email lines present but do not close the qty gap "
                f"(Δ={delta:g})."
            )
        else:
            issues.append("No reco/email line for this name near the gap.")
        hyp = d.get("top_hypothesis") or story.get("hypothesis")
        if hyp:
            issues.append(f"Working hypothesis: {hyp}.")
    elif explainable:
        issues.append(
            if_applied.get("message")
            or three_way.get("message")
            or "Reco/trade path explains the gap if applied."
        )

    return {
        "symbol": sym,
        "security_id": break_row.get("security_id"),
        "statement_qty": break_row.get("statement_qty"),
        "system_qty": break_row.get("system_qty"),
        "delta": delta,
        "status": break_row.get("status"),
        "explainable": explainable,
        "top_hypothesis": d.get("top_hypothesis"),
        "if_reco_applied": bool(if_applied.get("explains_gap")),
        "three_way_hypothesis": three_way.get("hypothesis"),
        "trade_hypothesis": story.get("hypothesis"),
        "reco_count": len(reco.get("recos") or []),
        "email_hint_count": len(hints),
        "notes": issues,
    }


def _reco_touch_map(
    breaks: List[Dict[str, Any]],
    emails: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Rank reco emails by how many mismatched names / |Δ| they touch."""
    break_syms = {
        _norm_sym(b.get("symbol")): b
        for b in breaks
        if _norm_sym(b.get("symbol"))
    }
    total_abs = sum(abs(float(b.get("delta") or 0)) for b in breaks) or 1.0
    out: List[Dict[str, Any]] = []
    for e in emails:
        if not (e.get("is_recommendation") or e.get("lines") or e.get("body_reco_hints")):
            continue
        touched: List[Dict[str, Any]] = []
        covered_abs = 0.0
        issue_syms: List[str] = []
        for sym, br in break_syms.items():
            hit = _sell_buy_for_symbol(e, sym)
            if not hit["lines"]:
                continue
            abs_d = abs(float(br.get("delta") or 0))
            # cover estimate: sell toward system-long, buy toward statement-long
            delta = float(br.get("delta") or 0)
            cover = 0.0
            if delta < -0.001 and hit["sell"] > 0:
                cover = min(hit["sell"], abs_d)
            elif delta > 0.001 and hit["buy"] > 0:
                cover = min(hit["buy"], abs_d)
            covered_abs += cover
            d = br.get("diagnosis") or {}
            explainable = bool((d.get("if_reco_applied") or {}).get("explains_gap"))
            row = {
                "symbol": sym,
                "delta": delta,
                "sell_qty": hit["sell"] or None,
                "buy_qty": hit["buy"] or None,
                "cover": cover,
                "explainable": explainable,
            }
            touched.append(row)
            if not explainable and abs_d > 0.001:
                issue_syms.append(sym)
        if not touched:
            continue
        out.append(
            {
                "id": e.get("id"),
                "sent_at": e.get("sent_at"),
                "subject": (e.get("subject") or "")[:120],
                "break_name_count": len(touched),
                "issue_name_count": len(issue_syms),
                "issue_symbols": issue_syms,
                "touched_symbols": [t["symbol"] for t in touched],
                "coverage_abs_delta": covered_abs / total_abs,
                "covered_abs_delta": covered_abs,
                "has_issues": bool(issue_syms),
                "lines": touched,
            }
        )
    out.sort(
        key=lambda r: (
            -r["break_name_count"],
            -r["coverage_abs_delta"],
            -r["issue_name_count"],
        )
    )
    return out


def build_security_reco_thread(
    *,
    matched_rows: Optional[List[Dict[str, Any]]] = None,
    breaks: List[Dict[str, Any]],
    emails: Optional[List[Dict[str, Any]]] = None,
    matched_count: int = 0,
) -> Dict[str, Any]:
    """
    Thread B inventory + mode switch:
    - ≤2 mismatches → security_wise
    - else → reco_wise (highlight reco emails that concentrate qty issues)
    """
    breaks = list(breaks or [])
    matched_rows = list(matched_rows or [])
    emails = list(emails or [])

    matched_list = [
        {
            "symbol": _norm_sym(m.get("symbol")) or str(m.get("security_id") or "?"),
            "security_id": m.get("security_id"),
            "statement_qty": m.get("statement_qty"),
            "system_qty": m.get("system_qty"),
        }
        for m in matched_rows
    ]
    # Fallback when only count is known (older runs)
    if not matched_list and matched_count:
        matched_list = [{"symbol": f"(matched ×{matched_count})", "security_id": None}]

    mismatched = [
        {
            "symbol": _norm_sym(b.get("symbol")) or str(b.get("security_id") or "?"),
            "security_id": b.get("security_id"),
            "statement_qty": b.get("statement_qty"),
            "system_qty": b.get("system_qty"),
            "delta": float(b.get("delta") or 0),
            "status": b.get("status"),
        }
        for b in breaks
    ]
    mismatched.sort(key=lambda x: -abs(x["delta"]))

    n_break = len(breaks)
    mode = "security_wise" if n_break <= 2 else "reco_wise"
    mode_reason = (
        f"{n_break} mismatch(es) → investigate security-by-security "
        "(recommendation tables secondary)."
        if mode == "security_wise"
        else f"{n_break} mismatches → check whether qty issues concentrate in the same reco emails."
    )

    per_security = [_security_review(b) for b in breaks]
    explainable_n = sum(1 for r in per_security if r["explainable"])
    issue_n = n_break - explainable_n

    reco_map = _reco_touch_map(breaks, emails) if n_break else []
    # Highlight: emails touching ≥2 breaks or ≥25% of |Δ|
    highlighted = [
        r
        for r in reco_map
        if r["break_name_count"] >= 2 or r["coverage_abs_delta"] >= 0.25
    ][:8]
    issue_recos = [r for r in reco_map if r["has_issues"]][:12]

    findings: List[str] = [
        f"Matched: {len(matched_rows) or matched_count} name(s)"
        + (
            " — "
            + ", ".join(m["symbol"] for m in matched_list[:40])
            + ("…" if len(matched_list) > 40 else "")
            if matched_rows
            else "."
        ),
        f"Mismatched: {n_break} name(s)"
        + (
            " — "
            + ", ".join(
                f"{m['symbol']} (Δ {m['delta']:g})" for m in mismatched[:25]
            )
            + ("…" if len(mismatched) > 25 else "")
            if mismatched
            else "."
        ),
        mode_reason,
        f"Of mismatches, {explainable_n} look explainable if reco booked; "
        f"{issue_n} still unexplained by reco/trade path on the row.",
    ]
    if mode == "reco_wise" and highlighted:
        top = highlighted[0]
        findings.append(
            f"Highlighted reco: {(top.get('subject') or '')[:70]} "
            f"({top.get('sent_at')}) touches {top['break_name_count']} mismatches "
            f"({top['coverage_abs_delta']*100:.0f}% of |Δ|); "
            f"{top['issue_name_count']} still unexplained."
        )
    elif mode == "security_wise" and per_security:
        for r in per_security:
            findings.append(
                f"{r['symbol']}: "
                + ("; ".join(r["notes"]) if r["notes"] else "no notes")
            )

    return {
        "thread": "security_reco",
        "mode": mode,
        "mode_reason": mode_reason,
        "matched": matched_list,
        "matched_count": len(matched_rows) or matched_count,
        "mismatched": mismatched,
        "mismatch_count": n_break,
        "per_security": per_security,
        "explainable_count": explainable_n,
        "unexplained_count": issue_n,
        "reco_concentration": reco_map[:15],
        "highlighted_recos": highlighted,
        "issue_recos": issue_recos,
        "findings": findings,
    }
