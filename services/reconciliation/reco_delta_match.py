"""
Match holding deltas to recommendation emails + Recommendation rows + trades.

Strategy (ops):
1. Collect securities with qty delta.
2. See if 1–2 reco emails explain most of the abs-delta / break count
   (rest of book mostly matched) → those emails/recos are the problem cluster.
3. For remaining / few breaks: 3-way per security (email line ↔ reco ↔ trades)
   to explain the difference.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple


def qty_close(a: Optional[float], b: Optional[float], *, tol: float = 0.51) -> bool:
    """True when quantities match within a share (or 2% for larger sizes)."""
    if a is None or b is None:
        return False
    aa, bb = float(a), float(b)
    if abs(aa - bb) <= tol:
        return True
    if bb and abs(aa - bb) / abs(bb) <= 0.02:
        return True
    return False


def _net_reco_qty(
    email_hints: List[Dict[str, Any]],
    recos: List[Dict[str, Any]],
    *,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Net book change if recommended lines were applied: +buy −sell.
    Prefer email line qty; fall back to Recommendation qty for same action.
    """
    sym = (symbol or "").upper().strip()
    buy = 0.0
    sell = 0.0
    sources: List[str] = []

    for h in email_hints or []:
        hsym = (h.get("symbol") or "").upper()
        if sym and hsym and hsym != sym:
            continue
        act = (h.get("action") or "").strip().lower()
        q = h.get("quantity")
        if q is None:
            continue
        q = float(q)
        if act == "buy":
            buy += q
            sources.append(f"email BUY {q:g}")
        elif act == "sell":
            sell += q
            sources.append(f"email SELL {q:g}")

    # Reco rows when email had no qty for that action
    if buy == 0.0 and sell == 0.0:
        for r in recos or []:
            act = (r.get("action") or "").strip().lower()
            q = r.get("quantity")
            if q is None:
                continue
            q = float(q)
            st = (r.get("status") or "").lower()
            tag = "executed" if (st == "executed" or r.get("executed_at")) else "open"
            if act == "buy":
                buy += q
                sources.append(f"reco#{r.get('id')} BUY {q:g} ({tag})")
            elif act == "sell":
                sell += q
                sources.append(f"reco#{r.get('id')} SELL {q:g} ({tag})")

    return {
        "buy_qty": buy,
        "sell_qty": sell,
        "net_applied": buy - sell,
        "sources": sources,
    }


def if_reco_applied_explains_gap(
    *,
    symbol: str,
    statement_qty: float,
    system_qty: float,
    email_hints: List[Dict[str, Any]],
    recos: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Assume recommended BUY/SELL qty were booked on the system side.
    If system + buy − sell ≈ statement, the gap is explained by missed/unbooked reco.
    Also works when reco is marked executed but trade is missing.
    """
    delta = float(statement_qty) - float(system_qty)
    net = _net_reco_qty(email_hints, recos, symbol=symbol)
    buy, sell = net["buy_qty"], net["sell_qty"]
    if buy == 0.0 and sell == 0.0:
        return {
            "symbol": (symbol or "").upper(),
            "buy_qty": 0.0,
            "sell_qty": 0.0,
            "net_applied": 0.0,
            "system_if_applied": float(system_qty),
            "matches_statement": False,
            "remaining_delta": delta,
            "explains_gap": False,
            "hypothesis": None,
            "message": None,
            "sources": [],
        }

    system_if = float(system_qty) + buy - sell
    remaining = float(statement_qty) - system_if
    matches = qty_close(system_if, statement_qty)
    # Partial: |remaining| much smaller than |delta|
    abs_d = abs(delta)
    partial = (
        not matches
        and abs_d > 0.001
        and abs(remaining) < abs_d * 0.35
        and abs(remaining) <= max(2.0, abs_d * 0.5)
    )
    explains = matches or partial
    hyp = None
    msg = None
    if matches:
        hyp = "IF_RECO_EXECUTED_MATCHES"
        bits = []
        if sell:
            bits.append(f"SELL {sell:g}")
        if buy:
            bits.append(f"BUY {buy:g}")
        msg = (
            f"If recommended {' + '.join(bits)} were booked, system "
            f"{system_qty:g} → {system_if:g} matches statement {statement_qty:g} "
            f"(closes Δ {delta:g})."
        )
    elif partial:
        hyp = "RECO_PARTIAL_EXPLAINS_DELTA"
        msg = (
            f"Recommended BUY {buy:g} / SELL {sell:g} applied → system "
            f"{system_if:g} vs statement {statement_qty:g} "
            f"(remaining Δ {remaining:g}; was {delta:g})."
        )
    elif sell > 0 and delta < -0.001:
        # sell recommended, system still long — report whether qty lines up
        if qty_close(sell, abs(delta)):
            hyp = "IF_RECO_EXECUTED_MATCHES"
            explains = True
            msg = (
                f"Recommended SELL {sell:g} ≈ |Δ| {abs(delta):g} — "
                f"if sold/booked, holdings would match statement."
            )
        else:
            msg = (
                f"Recommended SELL {sell:g} vs |Δ| {abs(delta):g} — "
                f"does not fully explain the gap "
                f"(system {system_qty:g} → {system_if:g} vs stmt {statement_qty:g})."
            )
    elif buy > 0 and delta > 0.001:
        if qty_close(buy, delta):
            hyp = "IF_RECO_EXECUTED_MATCHES"
            explains = True
            msg = (
                f"Recommended BUY {buy:g} ≈ Δ {delta:g} — "
                f"if bought/booked, holdings would match statement."
            )
        else:
            msg = (
                f"Recommended BUY {buy:g} vs Δ {delta:g} — "
                f"does not fully explain the gap."
            )

    return {
        "symbol": (symbol or "").upper(),
        "buy_qty": buy,
        "sell_qty": sell,
        "net_applied": buy - sell,
        "system_if_applied": system_if,
        "matches_statement": matches,
        "remaining_delta": remaining,
        "explains_gap": explains,
        "hypothesis": hyp,
        "message": msg,
        "sources": net["sources"],
    }


def line_explains_delta(
    action: Optional[str],
    email_qty: Optional[float],
    delta: float,
) -> float:
    """
    Score 0..1 whether an email/reco line can explain statement−system delta.
    BUY Q ≈ +delta (statement higher / system missing buy).
    SELL Q ≈ −delta (system higher / statement already reduced or sell not booked).
    """
    act = (action or "").strip().lower()
    if act not in ("buy", "sell"):
        return 0.0
    if email_qty is None:
        # symbol-only weak signal
        if act == "buy" and delta > 0.001:
            return 0.35
        if act == "sell" and delta < -0.001:
            return 0.35
        return 0.0
    if act == "buy" and delta > 0.001 and qty_close(email_qty, delta):
        return 1.0
    if act == "sell" and delta < -0.001 and qty_close(email_qty, abs(delta)):
        return 1.0
    return 0.0


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def find_best_historical_sell_batch(
    emails: List[Dict[str, Any]],
    breaks: List[Dict[str, Any]],
    *,
    min_coverage: float = 0.30,
) -> Dict[str, Any]:
    """
    Scan full archive (not only ±45d) for 1–2 reco emails whose SELL lines
    cover the most system-long |Δ|. Returns coverage facts; ranking decides weight.
    """
    breaks_by = {
        _norm_sym(b.get("symbol")): b
        for b in breaks
        if _norm_sym(b.get("symbol")) and float(b.get("delta") or 0) < -0.001
    }
    if not breaks_by or not emails:
        return {
            "found": False,
            "coverage_abs_delta": 0.0,
            "coverage_count": 0.0,
            "message": None,
            "emails": [],
            "explained_symbols": [],
            "exact_count": 0,
        }

    total_abs = sum(abs(float(b.get("delta") or 0)) for b in breaks_by.values()) or 1.0
    total_breaks = len(breaks_by)

    def _sell_lines(e: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for ln in e.get("lines") or e.get("body_reco_hints") or []:
            if (ln.get("action") or "").strip().lower() != "sell":
                continue
            sym = _norm_sym(ln.get("symbol"))
            if not sym:
                continue
            out.append({**ln, "symbol": sym})
        return out

    def _score(lines: List[Dict[str, Any]]) -> Dict[str, Any]:
        covered_abs = 0.0
        explained: List[str] = []
        exact = 0
        details = []
        seen = set()
        by_sym: Dict[str, float] = {}
        for ln in lines:
            sym = _norm_sym(ln.get("symbol"))
            q = ln.get("quantity")
            if not sym or q is None:
                continue
            by_sym[sym] = by_sym.get(sym, 0.0) + float(q)
        for sym, sell_q in by_sym.items():
            br = breaks_by.get(sym)
            if not br:
                continue
            abs_d = abs(float(br.get("delta") or 0))
            cover = min(sell_q, abs_d)
            covered_abs += cover
            if sym not in seen:
                seen.add(sym)
                explained.append(sym)
            if qty_close(sell_q, abs_d):
                exact += 1
            details.append(
                {
                    "symbol": sym,
                    "sell_qty": sell_q,
                    "delta": float(br.get("delta") or 0),
                    "cover": cover,
                    "exact": qty_close(sell_q, abs_d),
                }
            )
        return {
            "explained_symbols": explained,
            "explained_count": len(explained),
            "abs_delta_explained": covered_abs,
            "exact_count": exact,
            "details": details,
            "coverage_count": len(explained) / total_breaks if total_breaks else 0.0,
            "coverage_abs_delta": covered_abs / total_abs,
        }

    candidates: List[Dict[str, Any]] = []
    sell_emails = []
    for e in emails:
        lines = _sell_lines(e)
        if not lines:
            continue
        sell_emails.append((e, lines))
        sc = _score(lines)
        if sc["explained_count"] == 0:
            continue
        candidates.append(
            {
                "email_ids": [e.get("id")],
                "subjects": [e.get("subject")],
                "sent_ats": [e.get("sent_at")],
                **sc,
            }
        )
    for i in range(len(sell_emails)):
        for j in range(i + 1, len(sell_emails)):
            a, la = sell_emails[i]
            b, lb = sell_emails[j]
            sc = _score(la + lb)
            if sc["explained_count"] == 0:
                continue
            candidates.append(
                {
                    "email_ids": [a.get("id"), b.get("id")],
                    "subjects": [a.get("subject"), b.get("subject")],
                    "sent_ats": [a.get("sent_at"), b.get("sent_at")],
                    **sc,
                }
            )

    if not candidates:
        return {
            "found": False,
            "coverage_abs_delta": 0.0,
            "coverage_count": 0.0,
            "message": "No historical SELL reco email covers the system-long gaps well.",
            "emails": [],
            "explained_symbols": [],
            "exact_count": 0,
        }

    candidates.sort(
        key=lambda c: (
            c["coverage_abs_delta"],
            c["exact_count"],
            c["coverage_count"],
            -len(c["email_ids"]),
        ),
        reverse=True,
    )
    best = candidates[0]
    found = (
        best["coverage_abs_delta"] >= min_coverage
        or best["coverage_count"] >= min_coverage
        or best["exact_count"] >= 2
    )
    subj = " | ".join((s or "")[:70] for s in best["subjects"] if s)
    when = ", ".join(str(t)[:10] for t in best["sent_ats"] if t)
    msg = (
        f"Best full-archive SELL reco match ({when}): "
        f"{best['coverage_abs_delta']*100:.0f}% of system-long |Δ|, "
        f"{best['explained_count']}/{total_breaks} break names, "
        f"{best['exact_count']} exact sell≈|Δ|"
        + (f" — {subj}" if subj else "")
        + "."
    )
    return {
        "found": found,
        "emails": [
            {"id": i, "subject": s, "sent_at": t}
            for i, s, t in zip(best["email_ids"], best["subjects"], best["sent_ats"])
        ],
        "explained_symbols": best["explained_symbols"],
        "details": best.get("details") or [],
        "coverage_count": best["coverage_count"],
        "coverage_abs_delta": best["coverage_abs_delta"],
        "exact_count": best["exact_count"],
        "message": msg,
        "all_candidates_top3": [
            {
                "subjects": c["subjects"],
                "sent_ats": c["sent_ats"],
                "coverage_abs_delta": round(c["coverage_abs_delta"], 3),
                "explained_count": c["explained_count"],
                "exact_count": c["exact_count"],
            }
            for c in candidates[:3]
        ],
    }


def score_lines_vs_breaks(
    lines: List[Dict[str, Any]],
    breaks_by_symbol: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    explained: List[Dict[str, Any]] = []
    abs_explained = 0.0
    for ln in lines:
        sym = (ln.get("symbol") or "").upper().strip()
        if not sym or sym not in breaks_by_symbol:
            continue
        br = breaks_by_symbol[sym]
        delta = float(br.get("delta") or 0)
        sc = line_explains_delta(ln.get("action"), ln.get("quantity"), delta)
        if sc < 0.34:
            continue
        explained.append(
            {
                "symbol": sym,
                "action": (ln.get("action") or "").lower(),
                "quantity": ln.get("quantity"),
                "delta": delta,
                "score": sc,
            }
        )
        abs_explained += abs(delta)
    # unique symbols
    syms = []
    seen = set()
    for e in explained:
        if e["symbol"] not in seen:
            seen.add(e["symbol"])
            syms.append(e["symbol"])
    return {
        "explained_symbols": syms,
        "explained_count": len(syms),
        "abs_delta_explained": abs_explained,
        "details": explained,
    }


def find_explaining_email_cluster(
    emails: List[Dict[str, Any]],
    breaks: List[Dict[str, Any]],
    *,
    min_coverage: float = 0.45,
) -> Dict[str, Any]:
    """
    Find 1–2 reco emails whose Buy/Sell lines explain most break abs-delta.

    ``emails`` items: {id, sent_at, subject, lines: [{action, symbol, quantity}]}
    ``breaks`` items: {symbol, delta, ...}
    """
    breaks_by = {
        (b.get("symbol") or "").upper(): b
        for b in breaks
        if b.get("symbol") and abs(float(b.get("delta") or 0)) > 0.001
    }
    if not breaks_by or not emails:
        return {
            "found": False,
            "emails": [],
            "explained_symbols": [],
            "coverage_count": 0.0,
            "coverage_abs_delta": 0.0,
            "message": None,
        }

    total_breaks = len(breaks_by)
    total_abs = sum(abs(float(b.get("delta") or 0)) for b in breaks_by.values()) or 1.0

    candidates: List[Dict[str, Any]] = []
    # singles
    for e in emails:
        lines = e.get("lines") or e.get("body_reco_hints") or []
        sc = score_lines_vs_breaks(lines, breaks_by)
        if sc["explained_count"] == 0:
            continue
        candidates.append(
            {
                "email_ids": [e.get("id")],
                "subjects": [e.get("subject")],
                "sent_ats": [e.get("sent_at")],
                **sc,
                "coverage_count": sc["explained_count"] / total_breaks,
                "coverage_abs_delta": sc["abs_delta_explained"] / total_abs,
            }
        )
    # pairs
    for a, b in combinations(emails, 2):
        lines = list(a.get("lines") or a.get("body_reco_hints") or []) + list(
            b.get("lines") or b.get("body_reco_hints") or []
        )
        sc = score_lines_vs_breaks(lines, breaks_by)
        if sc["explained_count"] == 0:
            continue
        candidates.append(
            {
                "email_ids": [a.get("id"), b.get("id")],
                "subjects": [a.get("subject"), b.get("subject")],
                "sent_ats": [a.get("sent_at"), b.get("sent_at")],
                **sc,
                "coverage_count": sc["explained_count"] / total_breaks,
                "coverage_abs_delta": sc["abs_delta_explained"] / total_abs,
            }
        )

    if not candidates:
        return {
            "found": False,
            "emails": [],
            "explained_symbols": [],
            "coverage_count": 0.0,
            "coverage_abs_delta": 0.0,
            "message": "No reco email cluster explains the qty deltas well.",
        }

    candidates.sort(
        key=lambda c: (c["coverage_abs_delta"], c["coverage_count"], -len(c["email_ids"])),
        reverse=True,
    )
    best = candidates[0]
    found = (
        best["coverage_count"] >= min_coverage
        or best["coverage_abs_delta"] >= min_coverage
    )
    n = len(best["email_ids"])
    msg = None
    if found:
        subj = " | ".join((s or "")[:60] for s in best["subjects"] if s)
        msg = (
            f"Most of the qty gap cluster is explained by {n} reco email(s) "
            f"({best['explained_count']}/{total_breaks} break names, "
            f"{best['coverage_abs_delta']*100:.0f}% of |Δ|): {subj}. "
            f"Rest of the book is mostly matched — investigate these recommendation(s)."
        )
    return {
        "found": found,
        "emails": [
            {"id": i, "subject": s, "sent_at": t}
            for i, s, t in zip(best["email_ids"], best["subjects"], best["sent_ats"])
        ],
        "explained_symbols": best["explained_symbols"],
        "details": best.get("details") or [],
        "coverage_count": best["coverage_count"],
        "coverage_abs_delta": best["coverage_abs_delta"],
        "message": msg,
        "all_candidates_top3": [
            {
                "email_ids": c["email_ids"],
                "coverage_count": round(c["coverage_count"], 3),
                "coverage_abs_delta": round(c["coverage_abs_delta"], 3),
                "explained_symbols": c["explained_symbols"],
            }
            for c in candidates[:3]
        ],
    }


def three_way_explain(
    *,
    symbol: str,
    delta: float,
    statement_qty: float,
    system_qty: float,
    email_hints: List[Dict[str, Any]],
    recos: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    For one security: compare email lines, Recommendation rows, and book trades to delta.
    """
    sym = (symbol or "").upper()
    notes: List[str] = []
    hypothesis = None

    email_buys = [
        h
        for h in email_hints
        if (h.get("action") or "").lower() == "buy"
        and (h.get("symbol") or "").upper() == sym
    ]
    email_sells = [
        h
        for h in email_hints
        if (h.get("action") or "").lower() == "sell"
        and (h.get("symbol") or "").upper() == sym
    ]
    reco_buys = [r for r in recos if (r.get("action") or "").lower() == "buy"]
    reco_sells = [r for r in recos if (r.get("action") or "").lower() == "sell"]

    def _is_buy(t):
        return (t.get("type") or "").upper() in ("BUY", "B", "PURCHASE")

    def _is_sell(t):
        return (t.get("type") or "").upper() in ("SELL", "S", "SALE")

    trade_buys = [t for t in trades if _is_buy(t) and not t.get("after_as_of")]
    trade_sells = [t for t in trades if _is_sell(t) and not t.get("after_as_of")]

    # Email vs delta
    for h in email_buys + email_sells:
        sc = line_explains_delta(h.get("action"), h.get("quantity"), delta)
        if sc >= 1.0:
            notes.append(
                f"3-way: email {(h.get('action') or '').upper()} {sym} "
                f"qty {h.get('quantity')} ≈ |Δ| {abs(delta):g} "
                f"(email {h.get('email_sent_at') or ''})."
            )
            hypothesis = hypothesis or (
                "EMAIL_BUY_EXPLAINS_DELTA"
                if (h.get("action") or "").lower() == "buy"
                else "EMAIL_SELL_EXPLAINS_DELTA"
            )

    # Reco vs email qty
    for r in reco_buys + reco_sells:
        rq = r.get("quantity")
        matched_email = None
        for h in email_buys + email_sells:
            if (h.get("action") or "").lower() != (r.get("action") or "").lower():
                continue
            if qty_close(h.get("quantity"), rq) or h.get("quantity") is None:
                matched_email = h
                break
        if matched_email and rq is not None:
            notes.append(
                f"3-way: Recommendation #{r.get('id')} {(r.get('action') or '').upper()} "
                f"qty {rq:g} status={r.get('status')} aligns with email line "
                f"({matched_email.get('email_sent_at')})."
            )
        elif rq is not None and line_explains_delta(r.get("action"), rq, delta) >= 1.0:
            notes.append(
                f"3-way: Recommendation #{r.get('id')} {(r.get('action') or '').upper()} "
                f"qty {rq:g} status={r.get('status')} ≈ |Δ| {abs(delta):g}."
            )
            if (r.get("status") or "").lower() in ("sent", "pending", "active") and not r.get(
                "executed_at"
            ):
                hypothesis = hypothesis or "RECO_OPEN_EXPLAINS_DELTA"
            else:
                hypothesis = hypothesis or "RECO_QTY_EXPLAINS_DELTA"

    # Trades vs delta / reco
    for t in trade_sells:
        tq = t.get("quantity")
        if qty_close(tq, abs(delta)) and delta < -0.001:
            notes.append(
                f"3-way: book SELL {tq:g} on {t.get('date')} ≈ |Δ| — "
                f"check as-of vs settlement / statement cut-off."
            )
            hypothesis = hypothesis or "TRADE_SELL_EXPLAINS_DELTA"
    for t in trade_buys:
        tq = t.get("quantity")
        if qty_close(tq, delta) and delta > 0.001:
            notes.append(
                f"3-way: book BUY {tq:g} on {t.get('date')} ≈ Δ — "
                f"check as-of vs statement."
            )
            hypothesis = hypothesis or "TRADE_BUY_EXPLAINS_DELTA"

    # Mismatches: email says buy/sell, no matching trade — only as hypothesis when
    # the gap direction matches (BUY explains statement-long; SELL explains system-long).
    for h in email_buys:
        q = h.get("quantity")
        if q is None:
            continue
        if not any(qty_close(t.get("quantity"), q) for t in trade_buys):
            notes.append(
                f"3-way note: email BUY {sym} qty {q:g} but no matching book BUY"
                + (
                    " — may explain Δ if unbooked."
                    if delta > 0.001
                    else " (does not explain system-long Δ)."
                )
            )
            if delta > 0.001:
                hypothesis = hypothesis or "EMAIL_BUY_NO_TRADE"
    for h in email_sells:
        q = h.get("quantity")
        if q is None:
            continue
        if not any(qty_close(t.get("quantity"), q) for t in trade_sells):
            notes.append(
                f"3-way note: email SELL {sym} qty {q:g} but no matching book SELL"
                + (
                    " — may explain Δ if unbooked."
                    if delta < -0.001
                    else " (does not explain statement-long Δ)."
                )
            )
            if delta < -0.001:
                hypothesis = hypothesis or "EMAIL_SELL_NO_TRADE"

    if not notes:
        notes.append(
            f"3-way: no email/reco/trade qty tightly explains Δ={delta:g} "
            f"(stmt {statement_qty:g} vs sys {system_qty:g})."
        )

    return {
        "symbol": sym,
        "hypothesis": hypothesis,
        "notes": notes,
        "email_buy_count": len(email_buys),
        "email_sell_count": len(email_sells),
        "reco_count": len(recos),
        "trade_buy_count": len(trade_buys),
        "trade_sell_count": len(trade_sells),
    }
