"""
Holdings vs recommendation cues for advisor confirmation.

1. Gap |Δ| matches a reco qty **and books already have that trade** → possible
   duplicate client order (gap is a replica of an existing booked fill).
2. Gap |Δ| matches a reco qty **but no matching book trade** → executed but
   not recorded — check linked trade statements / contract notes next.
3. Holdings exceed recommended BUY qty → previously held / bought more than
   recommended — **only as a last resort** when other investigations did not
   explain the gap (`include_holdings_exceed=True`).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.reconciliation.reco_delta_match import qty_close

# Hypotheses that already explain the gap — do not surface HOLDINGS_EXCEED_RECO.
_STRONG_HYP_PREFIXES = (
    "FOUND_MISSING_RECOMMENDATION",
    "EXECUTED_NOT_RECORDED",
    "LIQUIDBEES_INTEREST_UNITS",
    "POSSIBLE_DUPLICATE_CLIENT_ORDER",
    "RECENT_TRADES_EXPLAIN_GAP",
    "BATCH_RECO_EMAIL_CLUSTER",
    "IF_RECO",
    "EMAIL_",
    "RECO_",
    "BOOKING_ERROR",
    "PHANTOM",
    "UNEXECUTED_RECO",
    "CORP_ACTION",
    "PORTFOLIO_BOOKS_LONG",
    "NEW_ADDITION",
)

_WEAK_HYPS = {
    None,
    "",
    "UNKNOWN",
    "QTY_BREAK",
    "MISSING_IN_SYSTEM",
    "CHECK_SENT_EMAIL",
    "CHECK_CALL_NOTES",
    "CHECK_CASHFLOW_NOTES",
    "WRONG_SECURITY_NAME",
    "HOLDINGS_EXCEED_RECO",
}


def other_investigations_failed(diagnosis: Optional[Dict[str, Any]] = None) -> bool:
    """True when no stronger investigation already explains this break."""
    d = diagnosis or {}
    hyp = str(d.get("top_hypothesis") or "")
    if hyp in _WEAK_HYPS or hyp.startswith("TIMING"):
        pass
    else:
        for p in _STRONG_HYP_PREFIXES:
            if hyp.startswith(p):
                return False
        if hyp and hyp not in _WEAK_HYPS:
            return False

    lb = d.get("liquid_bees") or {}
    if lb.get("matches"):
        return False
    be = d.get("broker_evidence") or {}
    for u in be.get("missing_from_books") or be.get("unexecuted_recos") or []:
        if (u.get("verdict") or "") == "EXECUTED_NOT_RECORDED":
            return False
    if d.get("high_confidence"):
        return False
    if d.get("in_email_cluster"):
        return False
    if_applied = d.get("if_reco_applied") or {}
    if if_applied.get("explains_gap"):
        return False
    story = d.get("trade_story") or {}
    if (story.get("gap_explained_by_trades") or {}).get("ok"):
        return False
    return True


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _buy_lines(
    recos: Optional[List[Dict[str, Any]]],
    email_lines: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in recos or []:
        act = (r.get("action") or "").strip().lower()
        if act not in ("buy", "b"):
            continue
        q = r.get("quantity")
        if q is None:
            continue
        out.append(
            {
                "action": "buy",
                "quantity": float(q),
                "sent_at": r.get("sent_at"),
                "status": (r.get("status") or "").strip().lower(),
                "executed_at": r.get("executed_at"),
                "source": "reco",
                "id": r.get("id"),
            }
        )
    for h in email_lines or []:
        act = (h.get("action") or "").strip().lower()
        if act not in ("buy", "b"):
            continue
        q = h.get("quantity")
        if q is None:
            continue
        out.append(
            {
                "action": "buy",
                "quantity": float(q),
                "sent_at": h.get("sent_at") or h.get("email_sent_at"),
                "status": "",
                "executed_at": None,
                "source": "email",
                "subject": h.get("subject"),
            }
        )
    return out


def _trade_covers_qty(
    trades: List[Dict[str, Any]], qty: float, *, side: str = "buy"
) -> bool:
    side = (side or "buy").lower()
    for t in trades or []:
        tt = (t.get("type") or t.get("action") or "").lower()
        if side in ("buy", "b") and tt not in ("buy", "b", "purchase"):
            continue
        if side in ("sell", "s") and tt not in ("sell", "s", "sale"):
            continue
        q = t.get("quantity")
        if q is not None and qty_close(float(q), float(qty)):
            return True
    # Split fills same side summing to qty
    total = 0.0
    for t in trades or []:
        tt = (t.get("type") or t.get("action") or "").lower()
        if side in ("buy", "b") and tt not in ("buy", "b", "purchase"):
            continue
        if side in ("sell", "s") and tt not in ("sell", "s", "sale"):
            continue
        if t.get("quantity") is not None:
            total += float(t["quantity"])
    return bool(total > 0 and qty_close(total, float(qty), tol=1.0))


def assess_holdings_vs_reco(
    *,
    symbol: str,
    statement_qty: float,
    system_qty: float,
    delta: float,
    recos: Optional[List[Dict[str, Any]]] = None,
    email_lines: Optional[List[Dict[str, Any]]] = None,
    trades: Optional[List[Dict[str, Any]]] = None,
    include_holdings_exceed: bool = False,
) -> Dict[str, Any]:
    """
    Returns cues for advisor confirmation.

    ``include_holdings_exceed`` must be True to emit HOLDINGS_EXCEED_RECO
    (last resort after other investigations failed).
    """
    sym = _norm_sym(symbol)
    stmt = float(statement_qty or 0)
    sys = float(system_qty or 0)
    dlt = float(delta if delta is not None else (stmt - sys))
    gap = abs(dlt)
    buys = _buy_lines(recos, email_lines)
    trades = list(trades or [])

    total_buy_reco = sum(b["quantity"] for b in buys)
    unique_buy_qtys = sorted({round(b["quantity"], 4) for b in buys})

    findings: List[str] = []
    suspects: List[Dict[str, Any]] = []
    hypotheses: List[str] = []

    # --- 1) Δ matches a specific reco BUY ---
    # Duplicate ONLY when books already contain that fill (gap = replica).
    # No matching book trade → executed-not-recorded; check trade statements next.
    if gap > 0.001 and buys and dlt > 0.001:
        for b in buys:
            q = b["quantity"]
            if not qty_close(gap, q):
                continue
            book_has = _trade_covers_qty(trades, q, side="buy")
            when = b.get("sent_at") or ""
            when_bit = f" ({when})" if when else ""
            if book_has:
                msg = (
                    f"{sym}: gap {gap:g} matches reco BUY {q:g}{when_bit}, and books "
                    f"already show a BUY of that qty — possible duplicate order by "
                    f"the client (extra fill beyond the booked trade). Ask the "
                    f"advisor to confirm with the client."
                )
                findings.append(msg)
                hypotheses.append("POSSIBLE_DUPLICATE_CLIENT_ORDER")
                suspects.append(
                    {
                        "kind": "possible_duplicate_client_order",
                        "priority": 0,
                        "action": "buy",
                        "quantity": q,
                        "sent_at": when,
                        "detail": msg,
                        "advisor_action": (
                            "Confirm with client whether they placed a duplicate buy "
                            f"of ~{q:g} in addition to the trade already in books."
                        ),
                    }
                )
            else:
                msg = (
                    f"{sym}: gap {gap:g} matches reco BUY {q:g}{when_bit}, but books "
                    f"have no matching BUY — likely executed and not recorded. "
                    f"Check linked trade statements / contract notes for a fill of "
                    f"~{q:g}, then add the trade to books."
                )
                findings.append(msg)
                hypotheses.append("EXECUTED_NOT_RECORDED")
                suspects.append(
                    {
                        "kind": "executed_not_recorded_vs_reco",
                        "priority": 0,
                        "action": "buy",
                        "quantity": q,
                        "sent_at": when,
                        "detail": msg,
                        "advisor_action": (
                            "Search trade statements / contract notes for "
                            f"{sym} BUY ~{q:g}{when_bit}; if found, book the trade "
                            "(not a duplicate — nothing is in books yet)."
                        ),
                        "next_check": "trade_statements",
                    }
                )
            break

    # --- 2) Holdings exceed recommended BUY total (LAST RESORT only) ---
    # Skip entirely when a stronger cue (e.g. duplicate) already explains the gap,
    # or when caller has not confirmed other investigations failed.
    if (
        include_holdings_exceed
        and "POSSIBLE_DUPLICATE_CLIENT_ORDER" not in hypotheses
        and buys
    ):
        if stmt > total_buy_reco + 0.51:
            excess = stmt - total_buy_reco
            msg = (
                f"{sym}: holdings {stmt:g} exceed recommended BUY total {total_buy_reco:g} "
                f"by {excess:g}. Other checks did not explain this — likely previously "
                f"held stock and/or the client bought more than recommended. "
                f"Ask the advisor to confirm with the client."
            )
            findings.append(msg)
            hypotheses.append("HOLDINGS_EXCEED_RECO")
            suspects.append(
                {
                    "kind": "holdings_exceed_reco",
                    "priority": 9,
                    "quantity": excess,
                    "reco_buy_total": total_buy_reco,
                    "statement_qty": stmt,
                    "detail": msg,
                    "advisor_action": (
                        "Confirm with client: was this security held before advice, "
                        "and/or did they buy more than the recommended qty?"
                    ),
                }
            )
        elif sys > total_buy_reco + 0.51:
            excess_sys = sys - total_buy_reco
            msg = (
                f"{sym}: system books {sys:g} exceed recommended BUY total "
                f"{total_buy_reco:g} by {excess_sys:g}. Other checks did not explain "
                f"this — likely previously held and/or client bought more than "
                f"recommended. Ask the advisor to confirm with the client."
            )
            findings.append(msg)
            hypotheses.append("HOLDINGS_EXCEED_RECO")
            suspects.append(
                {
                    "kind": "books_exceed_reco",
                    "priority": 9,
                    "quantity": excess_sys,
                    "detail": msg,
                    "advisor_action": (
                        "Confirm with client: prior holding and/or buys above "
                        "recommended qty."
                    ),
                }
            )

    primary = hypotheses[0] if hypotheses else None
    summary = findings[0] if findings else None
    return {
        "symbol": sym,
        "buy_reco_total": total_buy_reco,
        "buy_reco_qtys": unique_buy_qtys,
        "statement_qty": stmt,
        "system_qty": sys,
        "delta": dlt,
        "hypotheses": hypotheses,
        "top_hypothesis": primary,
        "findings": findings[:6],
        "suspects": suspects[:6],
        "summary": summary,
        "advisor_confirm": bool(findings),
    }


def apply_holdings_vs_reco_to_breaks(
    breaks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Annotate diagnosed breaks with holdings-vs-reco / duplicate cues.

    HOLDINGS_EXCEED_RECO only when ``other_investigations_failed`` for that break.
    """
    hits = []
    for b in breaks or []:
        sym = _norm_sym(b.get("symbol"))
        if not sym:
            continue
        d = dict(b.get("diagnosis") or {})
        reco_pack = d.get("reco") or {}
        email_pack = d.get("emails") or {}
        story = d.get("trade_story") or {}
        allow_exceed = other_investigations_failed(d)
        pack = assess_holdings_vs_reco(
            symbol=sym,
            statement_qty=float(b.get("statement_qty") or 0),
            system_qty=float(b.get("system_qty") or 0),
            delta=float(b.get("delta") or 0),
            recos=list(reco_pack.get("recos") or []),
            email_lines=list(email_pack.get("symbol_hints") or []),
            trades=list(story.get("recent_trades") or []),
            include_holdings_exceed=allow_exceed,
        )
        if not pack.get("findings"):
            continue
        d["holdings_vs_reco"] = pack
        notes = list(d.get("investigation_notes") or [])
        for f in pack.get("findings") or []:
            if f not in notes:
                notes.append(f)
        d["investigation_notes"] = notes
        hyp = str(d.get("top_hypothesis") or "")
        primary = pack.get("top_hypothesis")
        # Executed-not-recorded / true duplicate may upgrade weak hyps;
        # exceed only when still weak / last resort.
        if primary in (
            "POSSIBLE_DUPLICATE_CLIENT_ORDER",
            "EXECUTED_NOT_RECORDED",
        ) and (
            not hyp
            or hyp in _WEAK_HYPS
            or hyp.startswith("TIMING")
            or hyp.startswith("RECO_NOT_EXECUTED")
            or "not executed" in hyp.lower()
        ):
            if not d.get("prior_hypothesis"):
                d["prior_hypothesis"] = hyp or None
            d["top_hypothesis"] = primary
            if primary == "EXECUTED_NOT_RECORDED":
                d["check_trade_statements"] = True
        elif (
            primary == "HOLDINGS_EXCEED_RECO"
            and allow_exceed
            and other_investigations_failed({**d, "top_hypothesis": hyp})
        ):
            if not d.get("prior_hypothesis"):
                d["prior_hypothesis"] = hyp or None
            d["top_hypothesis"] = "HOLDINGS_EXCEED_RECO"
        if pack.get("advisor_confirm"):
            d["advisor_confirm_with_client"] = True
        b["diagnosis"] = d
        hits.append(
            {
                "symbol": sym,
                "hypothesis": primary,
                "summary": pack.get("summary"),
                "exceed_fallback": allow_exceed
                and primary == "HOLDINGS_EXCEED_RECO",
            }
        )
    return {
        "checked": True,
        "hits": hits,
        "summary": (
            f"{len(hits)} name(s) need advisor confirmation with client "
            f"(duplicate and/or last-resort holdings>reco)."
            if hits
            else "No holdings-vs-reco / duplicate cues on open gaps."
        ),
    }
