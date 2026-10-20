"""Detect breaks that are only from combining demats when books already match one account."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def explain_books_match_one_demat(
    *,
    statement_qty: float,
    system_qty: float,
    sources: Optional[List[Dict[str, Any]]],
    tol: float = 0.01,
) -> Optional[Dict[str, Any]]:
    """
    When holdings were combined across accounts and books equal one source's qty
    while |Δ| equals the other source(s), this is not a settlement/reco mystery —
    books already match that demat; the rest is another account (often out of scope
    or not booked).

    Example: UDZ 143 + TV 7 → stmt 150, books 143 → Δ 7 = entire TV lot.
    """
    if not sources or len(sources) < 2:
        return None
    try:
        sq = float(statement_qty)
        tq = float(system_qty)
    except (TypeError, ValueError):
        return None
    delta = sq - tq
    if abs(delta) <= tol:
        return None

    parsed = []
    for s in sources:
        try:
            q = float(s.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        parsed.append(
            {
                "filename": s.get("filename") or s.get("run_id") or "?",
                "qty": q,
                "run_id": s.get("run_id"),
            }
        )
    if len(parsed) < 2:
        return None

    # Books match one contributing demat
    matched_src = None
    for s in parsed:
        if abs(s["qty"] - tq) <= tol:
            matched_src = s
            break
    if not matched_src:
        return None

    others = [s for s in parsed if s is not matched_src]
    # Prefer identity by filename+qty if same object compare fails
    if len(others) == len(parsed):
        others = [s for s in parsed if not (s["filename"] == matched_src["filename"] and abs(s["qty"] - matched_src["qty"]) <= tol)]
    other_sum = sum(s["qty"] for s in others)
    if abs(other_sum - delta) > max(tol, abs(delta) * 1e-9):
        return None

    other_bits = ", ".join(f"{s['filename']}:{s['qty']:g}" for s in others[:4])
    msg = (
        f"Not a settlement/reco break — books ({tq:g}) already match "
        f"{matched_src['filename']}:{matched_src['qty']:g}. "
        f"Combined Δ {delta:g} is the other demat ({other_bits}). "
        f"Treat as matched for the booked account; other demat is out of scope or not in books."
    )
    return {
        "ok": True,
        "code": "COMBINED_DEMAT_BOOKS_MATCH_ONE",
        "message": msg,
        "books_match_file": matched_src["filename"],
        "books_match_qty": matched_src["qty"],
        "other_demat_qty": other_sum,
        "other_demats": others,
        "statement_qty": sq,
        "system_qty": tq,
        "delta": delta,
    }
