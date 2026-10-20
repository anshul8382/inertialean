"""
Per-remaining-mismatch collapsible notes.

For each open holding: list recos, suspect qty≈|Δ|, try “add trade of |Δ|”,
flag trade-without-reco / reco-without-trade, rank potential issues.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from services.reconciliation.reco_delta_match import qty_close


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _gap(delta: float) -> float:
    return abs(float(delta or 0))


def build_remaining_note(
    *,
    symbol: str,
    delta: float,
    statement_qty: float,
    system_qty: float,
    email_lines: Optional[List[Dict[str, Any]]] = None,
    recos: Optional[List[Dict[str, Any]]] = None,
    trades: Optional[List[Dict[str, Any]]] = None,
    as_of: Optional[date] = None,
    liquid_bees_pack: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Pure checks for one remaining mismatch (no DB).

    Example gap=5:
    - Reco BUY/SELL 5 with no matching trade → missing trade (books short/long)
    - Trade 5 with no matching reco → incorrect / unsolicited entry
    """
    sym = _norm_sym(symbol)
    dlt = float(delta or 0)
    gap = _gap(dlt)
    stmt = float(statement_qty or 0)
    sys = float(system_qty or 0)
    email_lines = list(email_lines or [])
    recos = list(recos or [])
    trades = list(trades or [])

    checks: List[str] = []
    suspects: List[Dict[str, Any]] = []
    potential: List[str] = []

    checks.append(
        f"Holding is {sym}: statement {stmt:g} vs system {sys:g} (gap {gap:g}, Δ {dlt:g})."
    )

    # Direction cue
    if dlt > 0.001:
        checks.append(
            f"Statement is higher by {gap:g} — books may be missing a BUY of ~{gap:g}, "
            f"or an extra SELL was booked. Try adding a BUY trade of {gap:g} and see if books match."
        )
        needed_action = "buy"
    elif dlt < -0.001:
        checks.append(
            f"System is higher by {gap:g} — books may be missing a SELL of ~{gap:g}, "
            f"or an extra BUY was booked. Try adding a SELL trade of {gap:g} and see if books match."
        )
        needed_action = "sell"
    else:
        needed_action = None
        checks.append("Gap is within tolerance — confirm as matched.")

    # Recos / email lines for this security
    reco_bits = []
    for r in recos[:12]:
        act = (r.get("action") or "?").upper()
        q = r.get("quantity")
        when = r.get("sent_at") or ""
        st = r.get("status") or ""
        bit = f"{act} {q:g}" if q is not None else act
        reco_bits.append(f"{bit} ({when} {st})".strip())
        if q is not None and gap > 0 and qty_close(float(q), gap):
            suspects.append(
                {
                    "kind": "reco_qty_matches_gap",
                    "priority": 1,
                    "action": (r.get("action") or "").lower(),
                    "quantity": float(q),
                    "source": "reco",
                    "sent_at": when,
                    "detail": (
                        f"Reco {act} {q:g} matches the gap of {gap:g} — first suspect: "
                        f"either the reco was executed and the trade is missing, "
                        f"or a trade of {gap:g} exists without this reco (incorrect entry)."
                    ),
                }
            )

    for h in email_lines[:12]:
        act = (h.get("action") or "?").upper()
        q = h.get("quantity")
        when = h.get("email_sent_at") or h.get("sent_at") or ""
        subj = (h.get("subject") or "")[:50]
        if q is not None:
            reco_bits.append(f"email {act} {q:g} ({when} {subj})".strip())
        if q is not None and gap > 0 and qty_close(float(q), gap):
            suspects.append(
                {
                    "kind": "email_qty_matches_gap",
                    "priority": 1,
                    "action": (h.get("action") or "").lower(),
                    "quantity": float(q),
                    "source": "email",
                    "sent_at": when,
                    "detail": (
                        f"Sent email {act} {q:g} matches gap {gap:g} — check whether that "
                        f"trade is in books; if not, add trade with qty {gap:g}."
                    ),
                }
            )

    # Liquid Bees interest / dividend units (@ ~₹0)
    liquid_hint = liquid_bees_pack
    if liquid_hint is None:
        try:
            from services.reconciliation.liquid_bees_interest import (
                assess_liquid_bees_gap,
                is_liquid_bees_symbol,
            )

            if is_liquid_bees_symbol(sym):
                liquid_hint = assess_liquid_bees_gap(
                    symbol=sym,
                    statement_qty=stmt,
                    system_qty=sys,
                    delta=dlt,
                    as_of=as_of or date.today(),
                    trades=trades,
                )
        except Exception:
            liquid_hint = None

    if liquid_hint and liquid_hint.get("matches"):
        checks.append(liquid_hint.get("message") or "")
        potential.insert(
            0,
            liquid_hint.get("recommendation")
            or liquid_hint.get("message")
            or "Book Liquid Bees interest units at ~₹0.",
        )
        suspects.insert(
            0,
            {
                "kind": "liquid_bees_interest_units",
                "priority": 0,
                "action": "buy",
                "quantity": gap,
                "source": "liquid_bees",
                "detail": liquid_hint.get("recommendation"),
            },
        )
    elif liquid_hint and liquid_hint.get("applicable"):
        checks.append(liquid_hint.get("message") or "")

    # Duplicate / holdings>reco (exceed only as last resort when no stronger suspects)
    try:
        from services.reconciliation.holdings_vs_reco import assess_holdings_vs_reco

        stronger = bool(
            (liquid_hint and liquid_hint.get("matches"))
            or any(
                (s.get("kind") or "")
                in (
                    "reco_qty_matches_gap",
                    "email_qty_matches_gap",
                    "trade_qty_matches_gap",
                    "liquid_bees_interest_units",
                )
                for s in suspects
            )
        )
        hvr = assess_holdings_vs_reco(
            symbol=sym,
            statement_qty=stmt,
            system_qty=sys,
            delta=dlt,
            recos=recos,
            email_lines=email_lines,
            trades=trades,
            include_holdings_exceed=not stronger,
        )
        for f in hvr.get("findings") or []:
            if f not in checks:
                checks.append(f)
            # Duplicate goes first; exceed only if still nothing stronger
            if "HOLDINGS_EXCEED_RECO" in (hvr.get("hypotheses") or []):
                if not potential:
                    potential.append(f)
            elif f not in potential:
                potential.insert(0, f)
        for s in hvr.get("suspects") or []:
            suspects.append(s)
    except Exception:
        pass

    if reco_bits:
        checks.append(
            f"There are recommendation(s) / Sent lines for {sym}: "
            + "; ".join(reco_bits[:8])
            + ("…" if len(reco_bits) > 8 else "")
            + "."
        )
    else:
        checks.append(
            f"No Sent recommendation lines found for {sym} near as-of — "
            "search archive by symbol, or this may be client’s own / corp action / transfer."
        )
        if not (liquid_hint and liquid_hint.get("matches")):
            potential.append(
                f"No reco for {sym}: gap {gap:g} may be out-of-scope holding, corp action, "
                "or a books entry without a recommendation."
            )

    # Trades with qty ≈ gap
    trade_bits = []
    gap_trades = []
    for t in trades[:20]:
        ttype = (t.get("type") or t.get("action") or "").upper()
        q = t.get("quantity")
        when = t.get("date") or t.get("trade_date") or ""
        if q is None:
            continue
        trade_bits.append(f"{ttype} {float(q):g} ({when})")
        if gap > 0 and qty_close(float(q), gap):
            gap_trades.append(t)
            suspects.append(
                {
                    "kind": "trade_qty_matches_gap",
                    "priority": 2,
                    "action": ttype.lower(),
                    "quantity": float(q),
                    "source": "trade",
                    "sent_at": when,
                    "detail": (
                        f"Trade {ttype} {float(q):g} on {when} matches gap {gap:g} — "
                        f"suspect incorrect entry if there is no matching reco of {gap:g}, "
                        f"or confirm this trade should reverse / adjust the books."
                    ),
                }
            )

    if trade_bits:
        checks.append(
            "Recent trades: " + "; ".join(trade_bits[:8])
            + ("…" if len(trade_bits) > 8 else "") + "."
        )
    else:
        checks.append(f"No recent trades found for {sym} near as-of.")

    # Cross: reco of gap without trade of gap vs trade of gap without reco of gap
    if gap > 0.001 and needed_action:
        reco_match = [
            s
            for s in suspects
            if s["kind"] in ("reco_qty_matches_gap", "email_qty_matches_gap")
            and s.get("action") == needed_action
        ]
        trade_match = [
            s
            for s in suspects
            if s["kind"] == "trade_qty_matches_gap"
        ]
        if reco_match and not trade_match:
            potential.append(
                f"Likely missing trade: reco/email for {needed_action.upper()} {gap:g} "
                f"exists but no trade of ~{gap:g} — add the trade (adjusted qty {gap:g}) "
                f"to raise/lower system toward statement {stmt:g}."
            )
            checks.append(
                f"Check: reco of {gap:g} with missing trade of {gap:g} would explain "
                f"{'lower' if dlt > 0 else 'higher'} system value."
            )
        elif trade_match and not reco_match:
            potential.append(
                f"Likely incorrect trade entry: trade of ~{gap:g} in books but no matching "
                f"recommendation of {gap:g} — review that trade for wrong qty/security."
            )
            checks.append(
                f"Check: trade of {gap:g} without a recommendation of {gap:g} points to "
                "an incorrect or unsolicited books entry."
            )
        elif reco_match and trade_match:
            potential.append(
                f"Both a reco and a trade of ~{gap:g} exist — gap remains; check dates "
                f"(trade after as-of?), wrong security mapping, or partial fill."
            )
            checks.append(
                "Check: reco and trade both show ~gap qty but mismatch remains — "
                "verify trade date vs as-of and security mapping."
            )
        elif not reco_match and not trade_match:
            # Half-gap / 2x suspects
            half = gap / 2.0 if gap >= 1 else None
            for r in recos + [
                {"action": h.get("action"), "quantity": h.get("quantity"), "sent_at": h.get("sent_at"), "source": "email"}
                for h in email_lines
            ]:
                q = r.get("quantity")
                if q is None:
                    continue
                qf = float(q)
                if half and qty_close(qf, half):
                    suspects.append(
                        {
                            "kind": "reco_half_gap",
                            "priority": 3,
                            "action": (r.get("action") or "").lower(),
                            "quantity": qf,
                            "source": r.get("source") or "reco",
                            "detail": (
                                f"Reco qty {qf:g} is ~half the gap ({gap:g}) — "
                                "possible two missing lots or partial execution."
                            ),
                        }
                    )
                if qty_close(qf, gap * 2):
                    suspects.append(
                        {
                            "kind": "reco_double_gap",
                            "priority": 3,
                            "action": (r.get("action") or "").lower(),
                            "quantity": qf,
                            "source": r.get("source") or "reco",
                            "detail": (
                                f"Reco qty {qf:g} is ~2× gap ({gap:g}) — "
                                "possible partial fill or qty typo in reco vs trade."
                            ),
                        }
                    )
            potential.append(
                f"No exact qty={gap:g} reco/trade hit — try searching Sent for {sym}, "
                f"then test booking a {needed_action.upper()} of {gap:g} (or reverse "
                f"the opposite side) and re-match."
            )

    # What-if: system adjusted by gap
    if gap > 0.001 and needed_action == "buy":
        checks.append(
            f"What-if: system {sys:g} + BUY {gap:g} → {sys + gap:g} "
            f"(target statement {stmt:g})."
        )
    elif gap > 0.001 and needed_action == "sell":
        checks.append(
            f"What-if: system {sys:g} − SELL {gap:g} → {sys - gap:g} "
            f"(target statement {stmt:g})."
        )

    # Rank suspects
    suspects.sort(key=lambda s: (s.get("priority") or 9, s.get("quantity") or 0))
    # Dedupe by kind+qty+action
    seen = set()
    uniq = []
    for s in suspects:
        key = (s.get("kind"), s.get("action"), s.get("quantity"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    suspects = uniq[:10]

    for s in suspects[:5]:
        if s.get("detail") and s["detail"] not in checks:
            checks.append(s["detail"])

    if not potential and suspects:
        potential.append(suspects[0]["detail"])
    if not potential:
        potential.append(
            f"Open issue on {sym}: gather Sent email + trades, then test a "
            f"qty-{gap:g} adjustment against books."
        )

    return {
        "symbol": sym,
        "delta": dlt,
        "gap": gap,
        "statement_qty": stmt,
        "system_qty": sys,
        "checks": checks,
        "suspects": suspects,
        "potential_issues": potential[:6],
        "summary": potential[0] if potential else checks[0],
    }


def build_remaining_notes(
    remaining_symbols: List[str],
    diagnosed_breaks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach notes for each remaining symbol using diagnosed break packs."""
    by_sym: Dict[str, Dict[str, Any]] = {}
    for b in diagnosed_breaks or []:
        sym = _norm_sym(b.get("symbol"))
        if sym:
            by_sym[sym] = b

    notes = []
    for sym in remaining_symbols or []:
        s = _norm_sym(sym)
        br = by_sym.get(s) or {"symbol": s, "delta": 0, "statement_qty": 0, "system_qty": 0}
        d = br.get("diagnosis") or {}
        email_pack = d.get("emails") or {}
        hints = list(email_pack.get("symbol_hints") or [])
        # Attach subject from emails list when possible
        for e in email_pack.get("emails") or []:
            for h in e.get("body_reco_hints") or []:
                if _norm_sym(h.get("symbol")) == s:
                    hints.append(
                        {
                            **h,
                            "subject": e.get("subject"),
                            "sent_at": e.get("sent_at"),
                            "email_sent_at": e.get("sent_at"),
                        }
                    )
        reco_pack = d.get("reco") or {}
        story = d.get("trade_story") or {}
        trades = list(story.get("recent_trades") or [])
        as_of_d = None
        try:
            raw_as = br.get("as_of") or d.get("as_of")
            if raw_as:
                as_of_d = date.fromisoformat(str(raw_as)[:10])
        except Exception:
            as_of_d = None
        notes.append(
            build_remaining_note(
                symbol=s,
                delta=float(br.get("delta") or 0),
                statement_qty=float(br.get("statement_qty") or 0),
                system_qty=float(br.get("system_qty") or 0),
                email_lines=hints,
                recos=list(reco_pack.get("recos") or []),
                trades=trades,
                as_of=as_of_d,
                liquid_bees_pack=d.get("liquid_bees"),
            )
        )
    return notes
