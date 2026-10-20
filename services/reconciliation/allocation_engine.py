"""
Detective allocation: collect evidence → clear executed reco sets →
allocate mismatches to 1–2 uncleared recos → residual hypotheses.

Matched holdings are positive evidence that recos touching those symbols
were executed — not ignored.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from services.reconciliation.reco_delta_match import qty_close


CLEAR_MIN_EVIDENCES = 3
CLEAR_LINE_RATIO = 0.50
TRADE_WINDOW_DAYS = 21
MAX_ITERATIONS = 5
RUPEE_MATERIALITY = 500_000.0  # ₹5L


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _as_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return None


def _email_lines(email: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for ln in email.get("lines") or email.get("body_reco_hints") or []:
        sym = _norm_sym(ln.get("symbol"))
        act = (ln.get("action") or "").strip().lower()
        if not sym or act not in ("buy", "sell"):
            continue
        q = ln.get("quantity")
        out.append(
            {
                "symbol": sym,
                "action": act,
                "quantity": float(q) if q is not None else None,
            }
        )
    return out


def _is_reco_email(email: Dict[str, Any]) -> bool:
    if email.get("is_recommendation"):
        return True
    if _email_lines(email):
        return True
    subj = (email.get("subject") or "").lower()
    return "portfolio investment reco" in subj or "recommendation" in subj


def _trade_hits_for_line(
    line: Dict[str, Any],
    trades: List[Dict[str, Any]],
    sent_at: Optional[date],
) -> List[Dict[str, Any]]:
    """Trades near reco date matching action + approx qty."""
    hits = []
    act = line["action"]
    q = line.get("quantity")
    window = TRADE_WINDOW_DAYS
    for t in trades or []:
        t_act = (t.get("transaction_type") or t.get("type") or "").strip().lower()
        if act == "buy" and t_act not in ("buy", "b", "purchase"):
            continue
        if act == "sell" and t_act not in ("sell", "s", "sale"):
            continue
        td = _as_date(t.get("transaction_date") or t.get("date"))
        if sent_at and td:
            if abs((td - sent_at).days) > window:
                continue
        tq = t.get("quantity")
        if tq is None:
            continue
        tqf = float(tq)
        close = q is None or qty_close(tqf, float(q))
        typo = False
        if q is not None and not close:
            qq = float(q)
            if qq > 0 and (
                qty_close(tqf, qq * 10)
                or qty_close(tqf, qq / 10)
                or qty_close(tqf, qq * 100)
                or qty_close(tqf, qq / 100)
            ):
                typo = True
        if close or typo:
            hits.append(
                {
                    "symbol": line["symbol"],
                    "trade_qty": tqf,
                    "reco_qty": q,
                    "exact": close,
                    "typo_suspect": typo,
                    "trade_date": td.isoformat() if td else None,
                    "price": t.get("price") or t.get("rate"),
                }
            )
    return hits


def _typo_suspect(reco_q: Optional[float], trade_q: Optional[float]) -> bool:
    if reco_q is None or trade_q is None:
        return False
    if qty_close(reco_q, trade_q):
        return False
    for mult in (10, 100, 0.1, 0.01):
        if qty_close(trade_q, reco_q * mult):
            return True
    return False


def _sync_label(in_sync: int, line_count: int) -> str:
    """Human sync status for a reco email, e.g. 'all recommendations in sync (5/5)'."""
    n = max(int(line_count or 0), 0)
    k = max(0, min(int(in_sync or 0), n if n else int(in_sync or 0)))
    if n <= 0:
        return "no reco lines parsed"
    if k >= n:
        return f"all recommendations in sync ({n}/{n})"
    return f"{k}/{n} recommendations in sync"


def _qty_can_explain(delta: float, reco_q: Optional[float]) -> bool:
    """True when |Δ| is within reco qty (±5% + 0.51 share slack)."""
    if reco_q is None:
        return False
    try:
        rq = abs(float(reco_q))
    except Exception:
        return False
    if rq <= 0:
        return False
    return abs(float(delta)) <= rq * 1.05 + 0.51


def _explain_symbol_gap(
    *,
    symbol: str,
    br: Dict[str, Any],
    line: Optional[Dict[str, Any]],
    hits: List[Dict[str, Any]],
    code: str,
) -> Dict[str, Any]:
    """
    Per-security reasoning for a focus reco break.

    Qty gate first: if |Δ| ≫ reco qty, do not blame missed/unrecorded —
    residual (bonus/split/corp action / other).
    """
    stmt = float(br.get("statement_qty") or 0)
    sys_q = float(br.get("system_qty") or 0)
    delta = float(br.get("delta") if br.get("delta") is not None else (stmt - sys_q))
    act = (line or {}).get("action") or "?"
    reco_q = (line or {}).get("quantity")
    reco_q_s = f"{float(reco_q):g}" if reco_q is not None else "?"
    gap_s = f"{delta:+g}" if delta else "0"
    can_explain = _qty_can_explain(delta, reco_q)

    if abs(delta) < 0.01:
        hold_vs_books = "statement already matches books"
    elif delta < 0:
        hold_vs_books = (
            f"statement has {abs(delta):g} fewer than books "
            f"(stmt {stmt:g} vs books {sys_q:g})"
        )
    else:
        hold_vs_books = (
            f"statement has {delta:g} more than books "
            f"(stmt {stmt:g} vs books {sys_q:g})"
        )

    # Qty gate — BAJFINANCE −70 vs BUY 7 must not become "reco missed"
    if line and reco_q is not None and not can_explain and code != "EXEC_OR_RECORD_TYPO":
        reason = (
            f"{symbol}: gap {gap_s} ({hold_vs_books}). "
            f"Reco {act.upper()} {reco_q_s} cannot cover |Δ| {abs(delta):g} — "
            f"not explained by this recommendation. "
            f"Likely bonus/split/corporate action, wrong map, or another event; "
            f"review after the main reco-date story."
        )
        return {
            "symbol": symbol,
            "delta": delta,
            "statement_qty": stmt,
            "system_qty": sys_q,
            "reco_action": act,
            "reco_qty": reco_q,
            "code": "CORP_ACTION_OR_OTHER",
            "preferred": "corp_action_or_other",
            "reason": reason,
            "explained_by_reco": False,
            "explainable_if_missed": False,
            "explainable_if_unrecorded": False,
            "has_trade_near_reco": bool(hits),
        }

    fits_executed_unrecorded = False
    fits_reco_missed = False
    if act == "sell":
        fits_executed_unrecorded = delta < -0.01
        fits_reco_missed = delta > 0.01 and not hits
    elif act == "buy":
        fits_executed_unrecorded = delta > 0.01
        fits_reco_missed = delta < -0.01 and not hits

    if code == "EXEC_OR_RECORD_TYPO":
        reason = (
            f"{symbol}: gap {gap_s} ({hold_vs_books}). "
            f"Reco {act.upper()} {reco_q_s}; a trade exists but qty looks off "
            f"(10× / digit typo) — fix the booking, not the reco email."
        )
        preferred = "typo_in_books"
        code = "EXEC_OR_RECORD_TYPO"
    elif code == "EXECUTED_NOT_RECORDED" or (
        fits_executed_unrecorded and not fits_reco_missed
    ):
        reason = (
            f"{symbol}: gap {gap_s} ({hold_vs_books}). "
            f"Reco {act.upper()} {reco_q_s} — holdings look like this ran, "
            f"but no matching trade in books → likely executed, not recorded."
        )
        preferred = "executed_not_recorded"
        code = "EXECUTED_NOT_RECORDED"
    elif code == "RECO_NOT_EXECUTED" or fits_reco_missed:
        reason = (
            f"{symbol}: gap {gap_s} ({hold_vs_books}). "
            f"Reco {act.upper()} {reco_q_s} is on file; holdings do not look like "
            f"it ran → likely recommendation missed / not executed "
            f"(or executed in another demat)."
        )
        preferred = "reco_missed"
        code = "RECO_NOT_EXECUTED"
    else:
        reason = (
            f"{symbol}: gap {gap_s} ({hold_vs_books}). "
            f"Reco line {act.upper()} {reco_q_s}; check whether this email "
            f"explains the break."
        )
        preferred = "unclear"

    if preferred in ("executed_not_recorded", "reco_missed") and can_explain:
        reason += (
            f" |Δ| {abs(delta):g} fits reco qty {reco_q_s} — "
            f"this line can explain the gap under the email assumption."
        )

    return {
        "symbol": symbol,
        "delta": delta,
        "statement_qty": stmt,
        "system_qty": sys_q,
        "reco_action": act,
        "reco_qty": reco_q,
        "code": code,
        "preferred": preferred,
        "reason": reason,
        "explained_by_reco": bool(can_explain or preferred == "typo_in_books"),
        "explainable_if_missed": bool(
            preferred == "reco_missed" and can_explain
        ),
        "explainable_if_unrecorded": bool(
            preferred == "executed_not_recorded" and can_explain
        ),
        "has_trade_near_reco": bool(hits),
    }


def _reco_label(fr: Dict[str, Any]) -> str:
    """Short human label: 'Jun 2026 recommendation' from date/subject."""
    sent = str(fr.get("sent_at") or "")
    subject = (fr.get("subject") or "").strip()
    month_name = None
    # Prefer month in subject (e.g. "… Jun 2026")
    m = re.search(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+20\d{2}\b",
        subject,
        re.I,
    )
    if m:
        month_name = m.group(0)
    elif len(sent) >= 7:
        try:
            from datetime import date as date_cls

            d = date_cls.fromisoformat(sent[:10])
            month_name = d.strftime("%b %Y")
        except Exception:
            month_name = sent[:7]
    if month_name:
        return f"{month_name} recommendation"
    if subject:
        return f"recommendation “{subject[:60]}”"
    return "recommendation email"


def _batch_story(fr: Dict[str, Any]) -> str:
    """Short one-liner for logs; UI prefers advisor_story."""
    story = _advisor_story_from_focus(fr, residual_symbols=[])
    return story.get("headline") or ""


def _advisor_story_from_focus(
    fr: Dict[str, Any],
    *,
    residual_symbols: List[str],
) -> Dict[str, Any]:
    """
    Crisp advisor narrative:
      Looks like the Jun 2026 recommendation was executed but not updated…
      This fixes: ATHER, CONCOR, …
      Next: remaining mismatches — repeat.
    """
    explained = [
        d["symbol"]
        for d in (fr.get("symbol_details") or [])
        if d.get("explained_by_reco") and d.get("preferred") != "corp_action_or_other"
    ]
    if not explained:
        explained = list(fr.get("explained_symbols") or [])
    prefs = {
        d.get("preferred")
        for d in (fr.get("symbol_details") or [])
        if d.get("explained_by_reco")
    }
    label = _reco_label(fr)
    if "executed_not_recorded" in prefs and "reco_missed" not in prefs:
        headline = (
            f"Looks like the {label} was executed but not updated in the system."
        )
    elif "reco_missed" in prefs and "executed_not_recorded" not in prefs:
        headline = f"Looks like the {label} was not executed (or only partly executed)."
    else:
        headline = (
            f"Looks like the {label} was executed but not updated in the system "
            "(or not booked)."
        )
    remaining = [
        s
        for s in residual_symbols
        if (s or "").upper() not in {(x or "").upper() for x in explained}
    ]
    next_step = (
        "Next: pick another mismatch, search Sent email for that symbol, "
        "and repeat — see which other gaps the same recommendation clears."
        if remaining
        else "Next: confirm books are updated for the names above, then re-run match."
    )
    return {
        "headline": headline,
        "subject": fr.get("subject"),
        "sent_at": fr.get("sent_at"),
        "fixes": explained[:40],
        "remaining": remaining[:40],
        "next": next_step,
    }


def build_advisor_story(
    *,
    focus_recos: List[Dict[str, Any]],
    residuals: List[Dict[str, Any]],
    breaks: Optional[List[Dict[str, Any]]] = None,
    email_cluster: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Primary advisor-facing story for the hub UI."""
    residual_syms = [
        r.get("symbol")
        for r in (residuals or [])
        if r.get("symbol")
    ]
    if not residual_syms and breaks:
        residual_syms = [b.get("symbol") for b in breaks if b.get("symbol")]

    if focus_recos:
        return _advisor_story_from_focus(
            focus_recos[0], residual_symbols=residual_syms
        )

    cluster = email_cluster or {}
    if cluster.get("found") and cluster.get("explained_symbols"):
        # Fall back to email-cluster hit (same shape as focus)
        fr = {
            "sent_at": (cluster.get("emails") or [{}])[0].get("sent_at")
            if cluster.get("emails")
            else cluster.get("sent_at"),
            "subject": (cluster.get("emails") or [{}])[0].get("subject")
            if cluster.get("emails")
            else cluster.get("subject"),
            "explained_symbols": cluster.get("explained_symbols") or [],
            "symbol_details": [
                {
                    "symbol": s,
                    "explained_by_reco": True,
                    "preferred": "executed_not_recorded",
                }
                for s in (cluster.get("explained_symbols") or [])
            ],
        }
        return _advisor_story_from_focus(fr, residual_symbols=residual_syms)

    if residual_syms:
        return {
            "headline": (
                "Start with the first mismatch: search Sent email for that symbol, "
                "then see which other gaps the same recommendation clears."
            ),
            "subject": None,
            "sent_at": None,
            "fixes": [],
            "remaining": residual_syms[:40],
            "next": (
                "Next: pick another remaining mismatch and repeat until gaps are gone "
                "or explained."
            ),
        }
    return {
        "headline": "No quantity mismatches left to explain.",
        "subject": None,
        "sent_at": None,
        "fixes": [],
        "remaining": [],
        "next": None,
    }


def run_allocation(
    *,
    breaks: List[Dict[str, Any]],
    matched_rows: Optional[List[Dict[str, Any]]] = None,
    emails: Optional[List[Dict[str, Any]]] = None,
    trades_by_symbol: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    cashflow_month_cues: Optional[List[Dict[str, Any]]] = None,
    price_findings: Optional[List[Dict[str, Any]]] = None,
    holdings_notes_drift: Optional[List[Dict[str, Any]]] = None,
    holdings_notes_meta: Optional[Dict[str, Any]] = None,
    cashflow_notes_meta: Optional[Dict[str, Any]] = None,
    prices_by_symbol: Optional[Dict[str, float]] = None,
    clear_min_evidences: int = CLEAR_MIN_EVIDENCES,
    clear_line_ratio: float = CLEAR_LINE_RATIO,
    rupee_materiality: float = RUPEE_MATERIALITY,
    max_iterations: int = MAX_ITERATIONS,
) -> Dict[str, Any]:
    """
    Returns allocation pack for UI / confidence.
    ``trades_by_symbol`` maps NSE symbol → list of trade dicts.
    """
    breaks = list(breaks or [])
    matched_rows = list(matched_rows or [])
    emails = [e for e in (emails or []) if _is_reco_email(e)]
    trades_by_symbol = trades_by_symbol or {}
    prices_by_symbol = prices_by_symbol or {}
    cashflow_month_cues = list(cashflow_month_cues or [])
    price_findings = list(price_findings or [])
    holdings_notes_drift = list(holdings_notes_drift or [])
    holdings_notes_meta = dict(holdings_notes_meta or {})
    cashflow_notes_meta = dict(cashflow_notes_meta or {})

    matched_syms: Set[str] = {
        _norm_sym(m.get("symbol")) for m in matched_rows if _norm_sym(m.get("symbol"))
    }
    breaks_by: Dict[str, Dict[str, Any]] = {}
    for b in breaks:
        sym = _norm_sym(b.get("symbol"))
        if sym:
            breaks_by[sym] = b
    break_syms = set(breaks_by.keys())
    total_abs = sum(abs(float(b.get("delta") or 0)) for b in breaks) or 1.0

    evidence_bag: List[Dict[str, Any]] = []

    # Global matched evidence
    for sym in sorted(matched_syms):
        evidence_bag.append(
            {
                "type": "MATCHED_HOLDING",
                "symbol": sym,
                "detail": "Statement qty matches books — prior recos on this name likely executed.",
            }
        )

    for d in holdings_notes_drift:
        evidence_bag.append(
            {
                "type": "HOLDINGS_NOTES_DRIFT",
                "symbol": d.get("symbol"),
                "detail": d.get("detail") or "Advisor holdings paste differs from books.",
                "paste_qty": d.get("paste_qty"),
                "system_qty": d.get("system_qty"),
            }
        )

    # Advisor notes match books → narrow which reco dates matter
    notes_focus: Dict[str, Any] = {"matches_books": False, "sync_through": None, "cue": None}
    sync_candidates: List[date] = []
    for raw in (
        holdings_notes_meta.get("sync_through"),
        cashflow_notes_meta.get("reconciled_through"),
    ):
        d = _as_date(raw)
        if d:
            sync_candidates.append(d)
    sync_through = max(sync_candidates) if sync_candidates else None
    h_drift_n = int(
        holdings_notes_meta.get("drift_count")
        if holdings_notes_meta.get("drift_count") is not None
        else len(holdings_notes_drift)
    )
    notes_match = bool(
        holdings_notes_meta.get("has_notes")
        and h_drift_n == 0
        and (holdings_notes_meta.get("row_count") or 0) > 0
    )
    if notes_match and sync_through:
        notes_focus = {
            "matches_books": True,
            "sync_through": sync_through.isoformat(),
            "source": "holdings_notes",
            "cue": (
                f"Advisor holdings notes match books through {sync_through.isoformat()}. "
                f"Qty breaks are unlikely from that synced snapshot — focus on recos "
                f"before that date (already drifted then) or after it (happened recently)."
            ),
        }
        evidence_bag.append(
            {
                "type": "NOTES_MATCH_BOOKS",
                "detail": notes_focus["cue"],
                "sync_through": sync_through.isoformat(),
            }
        )
    elif cashflow_notes_meta.get("approved") and sync_through and not notes_match:
        notes_focus = {
            "matches_books": False,
            "sync_through": sync_through.isoformat(),
            "source": "cashflow_notes",
            "cue": (
                f"CF advisor notes reconciled through {sync_through.isoformat()} — "
                f"use as a time fence when judging which reco emails matter."
            ),
        }
        evidence_bag.append(
            {
                "type": "CF_NOTES_THROUGH",
                "detail": notes_focus["cue"],
                "sync_through": sync_through.isoformat(),
            }
        )

    for f in price_findings:
        evidence_bag.append(
            {
                "type": f"PRICE_{((f.get('kind') or 'SPIKE')).upper()}",
                "symbol": _norm_sym(f.get("symbol")),
                "security_id": f.get("security_id"),
                "detail": (
                    f"Price-accuracy {(f.get('kind') or '?')} "
                    f"pct={f.get('pct_change')}"
                ),
            }
        )

    for cue in cashflow_month_cues:
        evidence_bag.append(
            {
                "type": "CASHFLOW_MONTH",
                "month": cue.get("month"),
                "detail": cue.get("detail") or cue.get("summary"),
                "net_inr": cue.get("net_inr"),
            }
        )

    # Build per-email sets
    sets: List[Dict[str, Any]] = []
    for email in emails:
        lines = _email_lines(email)
        if not lines:
            continue
        sent = _as_date(email.get("sent_at"))
        line_syms = {ln["symbol"] for ln in lines}
        matched_on = sorted(line_syms & matched_syms)
        break_on = sorted(line_syms & break_syms)
        trade_hits: List[Dict[str, Any]] = []
        exec_evidences = 0
        in_sync_syms: Set[str] = set()
        for ln in lines:
            line_ok = False
            # matched holding counts as execution evidence for this line
            if ln["symbol"] in matched_syms:
                exec_evidences += 1
                line_ok = True
                evidence_bag.append(
                    {
                        "type": "RECO_LINE_MATCHED_HOLDING",
                        "email_id": email.get("id"),
                        "subject": email.get("subject"),
                        "sent_at": email.get("sent_at"),
                        "symbol": ln["symbol"],
                        "detail": "Reco line symbol matches clean holding.",
                    }
                )
            hits = _trade_hits_for_line(
                ln, trades_by_symbol.get(ln["symbol"]) or [], sent
            )
            if hits:
                exact = [h for h in hits if h.get("exact")]
                if exact:
                    exec_evidences += 1
                    line_ok = True
                    trade_hits.extend(exact)
                    evidence_bag.append(
                        {
                            "type": "RECO_LINE_TRADE_HIT",
                            "email_id": email.get("id"),
                            "subject": email.get("subject"),
                            "symbol": ln["symbol"],
                            "detail": f"Trade qty≈reco {ln.get('quantity')} near {sent}.",
                        }
                    )
                elif any(h.get("typo_suspect") for h in hits):
                    trade_hits.extend(hits)
                    evidence_bag.append(
                        {
                            "type": "RECO_LINE_TYPO_SUSPECT",
                            "email_id": email.get("id"),
                            "symbol": ln["symbol"],
                            "detail": "Trade qty looks like 10×/digit slip vs reco.",
                        }
                    )
            if line_ok:
                in_sync_syms.add(ln["symbol"])

        # CF month overlap with sent month
        if sent:
            ym = f"{sent.year:04d}-{sent.month:02d}"
            for cue in cashflow_month_cues:
                if cue.get("month") == ym:
                    exec_evidences += 1
                    evidence_bag.append(
                        {
                            "type": "RECO_CF_MONTH_ALIGN",
                            "email_id": email.get("id"),
                            "month": ym,
                            "detail": "Cashflow activity in reco month.",
                        }
                    )
                    break

        n_lines = max(len(lines), 1)
        in_sync_count = len(in_sync_syms)
        line_ratio = in_sync_count / n_lines
        evidence_cleared = (
            exec_evidences >= clear_min_evidences or line_ratio >= clear_line_ratio
        )
        residual_abs = sum(
            abs(float(breaks_by[s].get("delta") or 0)) for s in break_on
        )
        specific = [
            {
                "symbol": s,
                "delta": float(breaks_by[s].get("delta") or 0),
                "note": "Break remains on otherwise execution-evidenced reco.",
            }
            for s in break_on
        ] if evidence_cleared and break_on else []

        sets.append(
            {
                "id": email.get("id"),
                "subject": (email.get("subject") or "")[:120],
                "sent_at": str(email.get("sent_at") or "")[:10],
                "lines": lines,
                "line_count": len(lines),
                "in_sync_count": in_sync_count,
                "sync_label": _sync_label(in_sync_count, len(lines)),
                "matched_symbols": matched_on,
                "break_symbols": break_on,
                "trade_hits": trade_hits[:40],
                "execution_evidence_count": exec_evidences,
                "corroborated_line_ratio": round(line_ratio, 3),
                "evidence_cleared": evidence_cleared,
                "cleared": evidence_cleared,
                "specific_security_issues": specific,
                "residual_abs": residual_abs,
            }
        )

    iterations: List[Dict[str, Any]] = []
    remaining = set(break_syms)
    claimed: Dict[str, str] = {}
    focus_recos: List[Dict[str, Any]] = []
    hypotheses: List[Dict[str, Any]] = []
    handled_ids: Set[str] = set()

    for it in range(max_iterations):
        # Blame only uncleared-by-evidence sets not already handled
        candidates = [
            s
            for s in sets
            if not s.get("evidence_cleared")
            and str(s.get("id") or s.get("subject")) not in handled_ids
        ]
        scored = []
        for s in candidates:
            cover_syms = [sym for sym in s["break_symbols"] if sym in remaining]
            # Prefer |Δ| that reco qty can actually explain
            explain_abs = 0.0
            for sym in cover_syms:
                br = breaks_by[sym]
                delta = float(br.get("delta") or 0)
                lines_for = [ln for ln in s["lines"] if ln["symbol"] == sym]
                rq = lines_for[0].get("quantity") if lines_for else None
                if _qty_can_explain(delta, rq):
                    explain_abs += abs(delta)
            cover_abs = sum(
                abs(float(breaks_by[sym].get("delta") or 0)) for sym in cover_syms
            )
            if cover_abs <= 0 and not cover_syms:
                continue
            # Boost emails after notes sync-through when notes match books
            date_boost = 0.0
            sent_d = _as_date(s.get("sent_at"))
            if notes_focus.get("matches_books") and sync_through and sent_d:
                if sent_d > sync_through:
                    date_boost = cover_abs * 0.15  # recent after sync
                elif sent_d < sync_through:
                    date_boost = cover_abs * 0.05  # possible pre-existing
            scored.append(
                {
                    **s,
                    "cover_abs": cover_abs,
                    "explain_abs": explain_abs,
                    "score_abs": explain_abs * 2.0 + cover_abs * 0.25 + date_boost,
                    "cover_symbols": cover_syms,
                }
            )
        scored.sort(
            key=lambda x: (-float(x.get("score_abs") or 0), -float(x.get("explain_abs") or 0))
        )
        iterations.append(
            {
                "iteration": it + 1,
                "remaining_breaks": len(remaining),
                "uncleared_candidates": len(scored),
                "top": [
                    {
                        "subject": x["subject"],
                        "sent_at": x["sent_at"],
                        "cover_abs": x["cover_abs"],
                        "cover_symbols": x["cover_symbols"][:12],
                    }
                    for x in scored[:3]
                ],
            }
        )
        if not scored:
            break
        pick = scored[:2]
        newly = False
        for p in pick:
            handled_ids.add(str(p.get("id") or p.get("subject")))
            for sym in p["cover_symbols"]:
                if sym in claimed:
                    continue
                claimed[sym] = p["id"] or p["subject"]
                remaining.discard(sym)
                newly = True
            symbol_details: List[Dict[str, Any]] = []
            for sym in p["cover_symbols"]:
                br = breaks_by[sym]
                lines_for = [ln for ln in p["lines"] if ln["symbol"] == sym]
                hits = [
                    h
                    for h in p.get("trade_hits") or []
                    if h.get("symbol") == sym
                ]
                # CF / amount cue without trade → executed-not-recorded
                cf_align = False
                sent_d = _as_date(p.get("sent_at"))
                if sent_d and not hits:
                    ym = f"{sent_d.year:04d}-{sent_d.month:02d}"
                    cf_align = any(
                        (c.get("month") == ym)
                        and abs(float(c.get("net_inr") or 0)) > 0
                        for c in cashflow_month_cues
                    )
                if hits and any(h.get("typo_suspect") for h in hits):
                    code = "EXEC_OR_RECORD_TYPO"
                    detail = "Trade exists but qty suggests typo vs reco."
                elif hits and not any(h.get("exact") for h in hits):
                    code = "EXEC_OR_RECORD_TYPO"
                    detail = "Trade near reco but qty not close."
                elif lines_for and not hits and cf_align:
                    code = "EXECUTED_NOT_RECORDED"
                    detail = (
                        "Reco line + cashflow in reco month, but no matching trade "
                        "— likely executed not recorded."
                    )
                elif lines_for and not hits:
                    code = "RECO_NOT_EXECUTED"
                    detail = "Reco line present; no matching trade near sent date."
                elif lines_for:
                    code = "EXECUTED_NOT_RECORDED"
                    detail = (
                        "Gap aligns with reco direction; trade may be missing "
                        "or unbooked."
                    )
                else:
                    code = "UNALLOCATED_BREAK"
                    detail = "Break allocated via email touch without clear line."
                expl = _explain_symbol_gap(
                    symbol=sym,
                    br=br,
                    line=lines_for[0] if lines_for else None,
                    hits=hits,
                    code=code,
                )
                code = expl["code"]
                detail = expl["reason"]
                hypotheses.append(
                    {
                        "symbol": sym,
                        "delta": float(br.get("delta") or 0),
                        "code": code,
                        "detail": detail,
                        "preferred": expl.get("preferred"),
                        "reco_subject": p["subject"],
                        "reco_sent_at": p["sent_at"],
                        "email_id": p.get("id"),
                    }
                )
                symbol_details.append(expl)
            explained_syms = [
                d["symbol"]
                for d in symbol_details
                if d.get("explained_by_reco")
            ]
            leftover_syms = [
                d["symbol"]
                for d in symbol_details
                if not d.get("explained_by_reco")
            ]
            explained_abs = sum(
                abs(float(breaks_by[s].get("delta") or 0)) for s in explained_syms
            )
            fr_row = {
                "id": p.get("id"),
                "subject": p["subject"],
                "sent_at": p["sent_at"],
                "cover_abs": p["cover_abs"],
                "coverage_abs_delta": p["cover_abs"] / total_abs,
                "explained_abs": explained_abs,
                "explained_coverage_abs_delta": explained_abs / total_abs,
                "cover_symbols": p["cover_symbols"],
                "explained_symbols": explained_syms,
                "leftover_symbols": leftover_syms,
                "execution_evidence_count": p["execution_evidence_count"],
                "in_sync_count": p.get("in_sync_count"),
                "line_count": p.get("line_count"),
                "sync_label": p.get("sync_label"),
                "symbol_details": symbol_details,
                "cleared": False,
            }
            fr_row["batch_story"] = _batch_story(fr_row)
            focus_recos.append(fr_row)
        if not newly or not remaining:
            break

    by_id: Dict[str, Dict[str, Any]] = {}
    for fr in focus_recos:
        key = str(fr.get("id") or fr.get("subject"))
        prev = by_id.get(key)
        if not prev or float(fr.get("cover_abs") or 0) > float(prev.get("cover_abs") or 0):
            by_id[key] = fr
    focus_recos = sorted(by_id.values(), key=lambda x: -float(x.get("cover_abs") or 0))[:2]

    cleared_recos = [
        {
            "id": s.get("id"),
            "subject": s["subject"],
            "sent_at": s["sent_at"],
            "execution_evidence_count": s["execution_evidence_count"],
            "corroborated_line_ratio": s["corroborated_line_ratio"],
            "in_sync_count": s.get("in_sync_count"),
            "line_count": s.get("line_count"),
            "sync_label": s.get("sync_label")
            or _sync_label(s.get("in_sync_count") or 0, s.get("line_count") or 0),
            "matched_symbols": s["matched_symbols"][:20],
            "specific_security_issues": s["specific_security_issues"],
        }
        for s in sets
        if s.get("evidence_cleared")
    ]

    specific_security_issues = []
    for s in sets:
        for iss in s.get("specific_security_issues") or []:
            specific_security_issues.append(
                {**iss, "reco_subject": s["subject"], "reco_sent_at": s["sent_at"]}
            )

    # Unallocated breaks + focus leftovers that reco qty cannot explain
    residual_map: Dict[str, Dict[str, Any]] = {}
    for sym in remaining:
        residual_map[sym] = {
            "symbol": sym,
            "delta": float(breaks_by[sym].get("delta") or 0),
            "abs_delta": abs(float(breaks_by[sym].get("delta") or 0)),
            "reason": "Not covered by a focus reco email.",
            "code": "UNATTRIBUTED",
        }
    for fr in focus_recos:
        for d in fr.get("symbol_details") or []:
            if d.get("explained_by_reco"):
                continue
            sym = d["symbol"]
            residual_map[sym] = {
                "symbol": sym,
                "delta": d.get("delta"),
                "abs_delta": abs(float(d.get("delta") or 0)),
                "reason": d.get("reason")
                or "Reco qty cannot cover gap — bonus/split/other.",
                "code": d.get("code") or "CORP_ACTION_OR_OTHER",
                "reco_sent_at": fr.get("sent_at"),
                "reco_subject": fr.get("subject"),
            }
    residuals = sorted(
        residual_map.values(),
        key=lambda r: -float(r.get("abs_delta") or 0),
    )

    # Amount gap cue (prefer explicit prices; else last trade price on symbol)
    amount_gap = 0.0
    for b in breaks:
        sym = _norm_sym(b.get("symbol"))
        px = prices_by_symbol.get(sym)
        if px is None:
            px = float(b.get("price") or b.get("current_price") or 0) or None
        if px is None:
            for t in trades_by_symbol.get(sym) or []:
                tp = t.get("price") or t.get("rate")
                if tp is not None:
                    try:
                        px = float(tp)
                        break
                    except Exception:
                        pass
        if px:
            amount_gap += abs(float(b.get("delta") or 0)) * float(px)
    large_amount = amount_gap >= rupee_materiality
    if large_amount:
        evidence_bag.append(
            {
                "type": "LARGE_AMOUNT_GAP",
                "detail": f"Est. |Δ|×price ≈ ₹{amount_gap:,.0f} (≥ ₹{rupee_materiality:,.0f}).",
                "amount_inr": round(amount_gap, 2),
            }
        )

    attributed_abs = sum(
        abs(float(breaks_by[s].get("delta") or 0)) for s in claimed
    )
    coverage = attributed_abs / total_abs if total_abs else 0.0

    # Attach symbol_details / batch_story onto deduped focus
    for fr in focus_recos:
        if not fr.get("symbol_details"):
            fr["symbol_details"] = [
                {
                    "symbol": h["symbol"],
                    "delta": h.get("delta"),
                    "code": h.get("code"),
                    "reason": h.get("detail"),
                    "preferred": h.get("preferred"),
                    "explained_by_reco": h.get("code") != "CORP_ACTION_OR_OTHER",
                }
                for h in hypotheses
                if (h.get("email_id") == fr.get("id"))
                or (h.get("reco_subject") == fr.get("subject"))
            ]
        fr["batch_story"] = _batch_story(fr)
        if notes_focus.get("cue"):
            fr["notes_cue"] = notes_focus.get("cue")

    # Ledger: prefer symbols actually explained by reco qty
    explained_for_ledger: List[str] = []
    for fr in focus_recos:
        explained_for_ledger.extend(fr.get("explained_symbols") or [])
    if not explained_for_ledger:
        explained_for_ledger = list(claimed.keys())

    advisor_story = build_advisor_story(
        focus_recos=focus_recos,
        residuals=residuals,
        breaks=breaks,
    )
    action = advisor_story.get("headline") or "Review mismatches."
    if advisor_story.get("next"):
        action = f"{action} {advisor_story['next']}"
    if notes_focus.get("matches_books") and notes_focus.get("cue"):
        action = f"{notes_focus['cue']} {action}"

    # Crisp summary for UI (no ledger jargon)
    if advisor_story.get("fixes"):
        summary = advisor_story["headline"]
    elif residuals:
        summary = (
            f"{len(residuals)} mismatch(es) still open — start with the first symbol’s "
            "Sent email and see what else that recommendation clears."
        )
    else:
        summary = "No quantity mismatches left to explain."

    return {
        "evidence_bag": evidence_bag[:200],
        "evidence_count": len(evidence_bag),
        "sets": [
            {
                "id": s.get("id"),
                "subject": s["subject"],
                "sent_at": s["sent_at"],
                "cleared": bool(s.get("evidence_cleared")),
                "execution_evidence_count": s["execution_evidence_count"],
                "corroborated_line_ratio": s["corroborated_line_ratio"],
                "matched_symbols": s["matched_symbols"],
                "break_symbols": s["break_symbols"],
                "residual_abs": s["residual_abs"],
                "specific_security_issues": s["specific_security_issues"],
            }
            for s in sets
        ],
        "cleared_recos": cleared_recos[:15],
        "focus_recos": focus_recos,
        "specific_security_issues": specific_security_issues[:20],
        "residuals": residuals[:40],
        "hypotheses": hypotheses[:40],
        "iterations": iterations,
        "claimed_symbols": sorted(claimed.keys()),
        "coverage_abs_delta": round(coverage, 3),
        "amount_gap_inr": round(amount_gap, 2),
        "large_amount_gap": large_amount,
        "notes_focus": notes_focus,
        "summary": summary,
        "action": action,
        "advisor_story": advisor_story,
        # Ledger prefers qty-explainable symbols from focus emails
        "explained_symbols": explained_for_ledger,
        "emails": [
            {"id": f.get("id"), "subject": f.get("subject"), "sent_at": f.get("sent_at")}
            for f in focus_recos
        ],
    }
