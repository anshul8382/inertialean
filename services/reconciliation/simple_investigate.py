"""
Simple investigation loop (advisor process):

1. Seed = largest |Δ| mismatch still open (prefer symbols that appear in emails)
2. Find Sent reco email that mentions that symbol
3. Score that email's BUY/SELL lines vs ALL open mismatches
4. Record: executed-but-not-updated (or not-executed) → fixes list
5. Remaining = open − fixes; repeat until stuck

No ledger jargon. Facts only (no LLM).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from services.reconciliation.reco_delta_match import score_lines_vs_breaks


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _email_lines(email: Dict[str, Any]) -> List[Dict[str, Any]]:
    lines = list(email.get("lines") or email.get("body_reco_hints") or [])
    out = []
    for ln in lines:
        sym = _norm_sym(ln.get("symbol"))
        if not sym:
            continue
        out.append({**ln, "symbol": sym})
    return out


def _email_symbols(email: Dict[str, Any]) -> Set[str]:
    return {_norm_sym(ln.get("symbol")) for ln in _email_lines(email)} - {""}


def _reco_label(sent_at: Optional[str], subject: Optional[str]) -> str:
    subject = (subject or "").strip()
    sent = str(sent_at or "")
    m = re.search(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d{2}\b",
        subject,
        re.I,
    )
    if m:
        return f"{m.group(0)} recommendation"
    if len(sent) >= 10:
        try:
            from datetime import date as date_cls

            d = date_cls.fromisoformat(sent[:10])
            return f"{d.strftime('%b %Y')} recommendation"
        except Exception:
            pass
    if subject:
        return f"recommendation “{subject[:60]}”"
    return "recommendation email"


def _breaks_by_symbol(breaks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for b in breaks:
        sym = _norm_sym(b.get("symbol"))
        if not sym:
            continue
        # Keep largest |Δ| if duplicates
        prev = out.get(sym)
        if prev is None or abs(float(b.get("delta") or 0)) > abs(
            float(prev.get("delta") or 0)
        ):
            out[sym] = {**b, "symbol": sym}
    return out


def _pick_seed(
    open_syms: List[str],
    breaks_by: Dict[str, Dict[str, Any]],
    emails: List[Dict[str, Any]],
) -> Optional[str]:
    if not open_syms:
        return None
    emailed: Set[str] = set()
    for e in emails:
        emailed |= _email_symbols(e)

    def key(sym: str) -> Tuple[int, float]:
        br = breaks_by.get(sym) or {}
        in_email = 1 if sym in emailed else 0
        return (in_email, abs(float(br.get("delta") or 0)))

    # Prefer symbols that appear in archive, then largest |Δ|
    return sorted(open_syms, key=key, reverse=True)[0]


def _best_email_for_seed(
    seed: str,
    emails: List[Dict[str, Any]],
    open_breaks: Dict[str, Dict[str, Any]],
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Email that mentions seed and explains the most open |Δ|."""
    candidates = []
    for e in emails:
        lines = _email_lines(e)
        if seed not in {ln["symbol"] for ln in lines}:
            continue
        scored = score_lines_vs_breaks(lines, open_breaks)
        if scored["explained_count"] == 0:
            # Still allow seed-only mention with weak score if line present
            continue
        candidates.append((e, scored))
    if not candidates:
        # Fallback: any email mentioning seed (even if qty gate fails for others)
        for e in emails:
            if seed in _email_symbols(e):
                lines = _email_lines(e)
                scored = score_lines_vs_breaks(lines, open_breaks)
                return e, scored
        return None
    candidates.sort(
        key=lambda t: (t[1]["abs_delta_explained"], t[1]["explained_count"]),
        reverse=True,
    )
    return candidates[0]


def _headline_for_step(
    email: Dict[str, Any],
    fixes: List[str],
    open_breaks: Dict[str, Dict[str, Any]],
    details: List[Dict[str, Any]],
) -> str:
    label = _reco_label(email.get("sent_at"), email.get("subject"))
    # Infer mode from deltas of fixed symbols
    buy_like = 0
    sell_like = 0
    for d in details:
        if d.get("symbol") not in fixes:
            continue
        act = (d.get("action") or "").lower()
        if act == "buy":
            buy_like += 1
        elif act == "sell":
            sell_like += 1
    if buy_like and not sell_like:
        return f"Looks like the {label} was executed but not updated in the system."
    if sell_like and not buy_like:
        return f"Looks like the {label} was executed but not updated in the system."
    return (
        f"Looks like the {label} was executed but not updated in the system "
        "(or not booked)."
    )


def run_simple_loop(
    breaks: List[Dict[str, Any]],
    emails: List[Dict[str, Any]],
    *,
    max_steps: int = 5,
) -> Dict[str, Any]:
    """
    Returns:
      steps: [{seed, email, fixes, remaining_after, headline}]
      advisor_story: {headline, subject, sent_at, fixes, remaining, next}
    """
    breaks_by = _breaks_by_symbol(breaks)
    open_syms = sorted(
        breaks_by.keys(),
        key=lambda s: -abs(float((breaks_by[s] or {}).get("delta") or 0)),
    )
    if not open_syms:
        return {
            "steps": [],
            "advisor_story": {
                "headline": "No quantity mismatches left to explain.",
                "subject": None,
                "sent_at": None,
                "fixes": [],
                "remaining": [],
                "next": None,
            },
        }

    emails = [
        e
        for e in (emails or [])
        if e.get("is_recommendation")
        or (e.get("lines") or e.get("body_reco_hints"))
    ]

    steps: List[Dict[str, Any]] = []
    claimed: Set[str] = set()
    primary_story: Optional[Dict[str, Any]] = None

    for _ in range(max_steps):
        remaining = [s for s in open_syms if s not in claimed]
        if not remaining:
            break
        open_breaks = {s: breaks_by[s] for s in remaining}
        seed = _pick_seed(remaining, breaks_by, emails)
        if not seed:
            break
        hit = _best_email_for_seed(seed, emails, open_breaks)
        if not hit:
            # No email for this seed — stop with guidance on remaining
            break
        email, scored = hit
        fixes = [s for s in (scored.get("explained_symbols") or []) if s in open_breaks]
        if not fixes and seed in open_breaks:
            # Email mentions seed but qty doesn't explain — don't claim false fixes
            break
        if not fixes:
            break

        claimed |= set(fixes)
        remaining_after = [s for s in open_syms if s not in claimed]
        headline = _headline_for_step(
            email, fixes, open_breaks, scored.get("details") or []
        )
        step = {
            "seed": seed,
            "email_id": email.get("id"),
            "subject": email.get("subject"),
            "sent_at": email.get("sent_at"),
            "fixes": fixes,
            "remaining_after": remaining_after,
            "headline": headline,
            "details": scored.get("details") or [],
        }
        steps.append(step)
        if primary_story is None:
            next_step = (
                f"Next: search Sent for {remaining_after[0]} and repeat — "
                "see which other gaps that recommendation clears."
                if remaining_after
                else "Next: confirm books are updated for the names above, then re-run match."
            )
            primary_story = {
                "headline": headline,
                "subject": email.get("subject"),
                "sent_at": email.get("sent_at"),
                "fixes": fixes[:40],
                "remaining": remaining_after[:40],
                "next": next_step,
            }

    if primary_story is None:
        remaining = [s for s in open_syms if s not in claimed]
        primary_story = {
            "headline": (
                "Start with the first mismatch: search Sent email for that symbol, "
                "then see which other gaps the same recommendation clears."
            ),
            "subject": None,
            "sent_at": None,
            "fixes": [],
            "remaining": remaining[:40],
            "next": (
                f"Next: search Sent for {remaining[0]} and repeat."
                if remaining
                else None
            ),
        }

    return {"steps": steps, "advisor_story": primary_story}
