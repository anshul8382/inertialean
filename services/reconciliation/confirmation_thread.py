"""
Step-by-step confirmation thread for holdings recon.

Real ops flow (in order):
  1. Draft reco          — usually ignore (never reached client)
  2. Sent reco           — must align with Sent email
  3. Client trade data   — contract notes / trade Excel / screenshots
  4. Recorded in books   — our Transaction row
  5. Holding             — demat statement cumulative

Investigate left → right. The first failed step is the break.
Exception: an unsent draft that matches client trade data may mean we sent
manually outside the system — then treat as a weak confirmed thread.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.reconciliation.reco_delta_match import qty_close

STEP_KEYS = (
    "draft",
    "sent",
    "client_trade",
    "books",
    "holding",
)

STEP_LABELS = {
    "draft": "Draft",
    "sent": "Sent (email)",
    "client_trade": "Client trade data",
    "books": "Recorded in books",
    "holding": "Holding",
}


def _norm(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def _act(a: Optional[str]) -> str:
    x = (a or "").strip().lower()
    if x in ("b", "buy", "purchase"):
        return "buy"
    if x in ("s", "sell", "sale"):
        return "sell"
    return x


def reco_is_emailed(reco: Dict[str, Any]) -> bool:
    """Sent through system / has send date / status sent|executed|…"""
    if reco.get("emailed") is True:
        return True
    if reco.get("sent_at"):
        return True
    st = (reco.get("status") or "").strip().lower()
    return st in ("sent", "executed", "closed", "partial")


def reco_is_draft_only(reco: Dict[str, Any]) -> bool:
    if reco_is_emailed(reco):
        return False
    st = (reco.get("status") or "").strip().lower()
    return st in ("pending", "draft", "", "open") or not st


def _qty_hit(pool: List[Dict[str, Any]], *, action: str, qty: float, side_key: str = "side") -> Optional[Dict[str, Any]]:
    act = _act(action)
    for row in pool or []:
        side = _act(row.get(side_key) or row.get("type") or row.get("action"))
        if act and side and act != side:
            continue
        q = row.get("quantity")
        if q is None:
            continue
        if qty_close(float(q), float(qty)):
            return row
    return None


def build_confirmation_thread(
    *,
    symbol: str,
    reco: Dict[str, Any],
    email_lines: Optional[List[Dict[str, Any]]] = None,
    broker_fills: Optional[List[Dict[str, Any]]] = None,
    book_trades: Optional[List[Dict[str, Any]]] = None,
    statement_qty: Optional[float] = None,
    system_qty: Optional[float] = None,
    delta: Optional[float] = None,
    trade_folder_linked: bool = True,
) -> Dict[str, Any]:
    """
    Build one confirmed (or candidate) thread for a single reco line vs evidence.
    """
    sym = _norm(symbol)
    act = _act(reco.get("action"))
    qty = reco.get("quantity")
    if qty is None or act not in ("buy", "sell"):
        return {"ok": False, "skip": True, "reason": "bad_reco"}

    qf = float(qty)
    emailed = reco_is_emailed(reco)
    draft_only = reco_is_draft_only(reco)

    # Email confirmation: same symbol+action+qty in parsed Sent lines
    email_hit = None
    for h in email_lines or []:
        if _norm(h.get("symbol")) != sym:
            continue
        if _act(h.get("action")) != act:
            continue
        hq = h.get("quantity")
        if hq is not None and qty_close(float(hq), qf):
            email_hit = h
            break

    broker_hit = _qty_hit(list(broker_fills or []), action=act, qty=qf, side_key="side")
    # Partial: broker/books may match the holdings slice, not full reco
    gap = None
    if delta is not None:
        try:
            gap = float(delta)
        except (TypeError, ValueError):
            gap = None
    if gap is None and statement_qty is not None and system_qty is not None:
        try:
            gap = float(statement_qty) - float(system_qty)
        except (TypeError, ValueError):
            gap = None

    held_slice = None
    if act == "buy" and gap is not None and gap > 0.01:
        held_slice = gap
    elif act == "sell" and gap is not None and gap < -0.01:
        held_slice = abs(gap)

    broker_partial = None
    if not broker_hit and held_slice and held_slice + 0.51 < qf:
        broker_partial = _qty_hit(
            list(broker_fills or []), action=act, qty=held_slice, side_key="side"
        )

    book_hit = _qty_hit(list(book_trades or []), action=act, qty=qf, side_key="type")
    book_partial = None
    if not book_hit and held_slice and held_slice + 0.51 < qf:
        book_partial = _qty_hit(
            list(book_trades or []), action=act, qty=held_slice, side_key="type"
        )

    # Holding supports this reco?
    holding_full = bool(
        held_slice is not None and qty_close(held_slice, qf)
    )
    holding_partial = bool(
        held_slice is not None and (not holding_full) and 0 < held_slice < qf - 0.51
    )
    holding_ok = holding_full or holding_partial

    client_trade_ok = bool(broker_hit or broker_partial)
    books_ok = bool(book_hit or book_partial)

    # --- Draft policy ---
    # Default: ignore draft-only threads unless client trade matches (manual send).
    manual_send_hypothesis = bool(draft_only and client_trade_ok)
    if draft_only and not manual_send_hypothesis:
        return {
            "ok": True,
            "skip": True,
            "reason": "unsent_draft",
            "symbol": sym,
            "action": act.upper(),
            "quantity": qf,
            "message": (
                f"{sym}: unsent draft {act.upper()} {qf:g} — ignored "
                "(never reached client via system)."
            ),
        }

    sent_ok = bool(emailed or email_hit or manual_send_hypothesis)
    sent_text = "Confirmed in Sent email" if email_hit else (
        "Marked sent in system" if emailed and not draft_only else (
            "Likely sent manually (draft matches client trade)" if manual_send_hypothesis
            else "Not confirmed sent"
        )
    )

    steps = [
        {
            "key": "draft",
            "label": STEP_LABELS["draft"],
            "ok": True if (draft_only or emailed or email_hit) else None,
            "na": not draft_only,
            "text": (
                "Unsent draft"
                if draft_only
                else ("Had a draft" if True else "—")
            ),
        },
        {
            "key": "sent",
            "label": STEP_LABELS["sent"],
            "ok": sent_ok,
            "text": sent_text,
        },
        {
            "key": "client_trade",
            "label": STEP_LABELS["client_trade"],
            "ok": client_trade_ok if trade_folder_linked else None,
            "text": (
                (
                    f"Found in {(broker_hit or broker_partial or {}).get('source_file') or 'Trade details'}"
                    if client_trade_ok
                    else (
                        "Trade folder not linked"
                        if not trade_folder_linked
                        else "Not found in Trade details"
                    )
                )
            ),
        },
        {
            "key": "books",
            "label": STEP_LABELS["books"],
            "ok": books_ok,
            "text": "Recorded" if books_ok else "Missing",
        },
        {
            "key": "holding",
            "label": STEP_LABELS["holding"],
            "ok": holding_ok,
            "text": (
                f"{held_slice:g} of {qf:g} on statement"
                if holding_partial and held_slice is not None
                else (
                    f"{held_slice:g} on statement"
                    if holding_full and held_slice is not None
                    else "Not explained by this reco"
                )
            ),
        },
    ]
    # Clean draft step display when not a draft thread
    if not draft_only:
        steps[0]["ok"] = True
        steps[0]["na"] = True
        steps[0]["text"] = "—"

    # First break (skip N/A draft)
    break_at = None
    for s in steps:
        if s.get("na"):
            continue
        if s.get("ok") is False:
            break_at = s["key"]
            break

    # Verdict from first break
    if books_ok and holding_ok:
        verdict = "OK"
        headline = f"{sym}: thread complete — {act.upper()} {qf:g} is in books and holdings."
    elif break_at == "sent":
        verdict = "RECO_NOT_SENT"
        headline = (
            f"{sym}: no confirmed Sent email for {act.upper()} {qf:g} "
            "(and no matching client trade for a manual-send theory)."
        )
    elif break_at == "client_trade" and not holding_ok:
        verdict = "RECO_NOT_EXECUTED"
        headline = (
            f"{sym}: sent {act.upper()} {qf:g}, but no client trade data and "
            "holdings do not show it — may not have been done."
        )
    elif break_at == "books" and (client_trade_ok or holding_ok):
        verdict = "EXECUTED_NOT_RECORDED"
        if holding_partial and held_slice is not None:
            headline = (
                f"{sym}: confirmed thread for {act.upper()} {qf:g}; "
                f"holdings show {held_slice:g} of that (partial) — "
                f"record {held_slice:g} in books."
            )
        elif client_trade_ok:
            headline = (
                f"{sym}: client trade data found for {act.upper()} {qf:g}, "
                "but it is not recorded in our books."
            )
        else:
            headline = (
                f"{sym}: holdings support {act.upper()} {qf:g}, "
                "but the trade is not in our books — check Trade details, then record."
            )
    elif break_at == "holding" and books_ok:
        verdict = "BOOKS_AHEAD_OF_HOLDING"
        headline = (
            f"{sym}: recorded in books but holdings do not match — check statement / other demat."
        )
    elif break_at == "client_trade" and holding_ok:
        # Holding without trade file yet
        verdict = "EXECUTED_NOT_RECORDED"
        headline = (
            f"{sym}: holdings look like {act.upper()} "
            f"{(held_slice if holding_partial else qf):g} of recommended {qf:g}, "
            "but Trade details did not show it and books are empty — find trade proof, then record."
        )
    else:
        verdict = "MIXED"
        headline = f"{sym}: incomplete confirmation thread for {act.upper()} {qf:g}."

    next_steps: List[str] = []
    if break_at == "sent":
        next_steps = [
            "Confirm whether this recommendation was emailed (Sent) or only a draft.",
            "If sent outside the system, link Trade details and match the client trade.",
        ]
    elif verdict == "EXECUTED_NOT_RECORDED":
        if client_trade_ok:
            src = (broker_hit or broker_partial or {}).get("source_file") or "Trade details"
            next_steps = [
                f"Open {src} and confirm the trade.",
                f"Add {act.upper()} {(held_slice if holding_partial and held_slice else qf):g} to books.",
            ]
        else:
            next_steps = [
                "Search Trade details (PDFs / Excel / screenshots) for this name.",
                "If found — add the trade to books.",
                "If not — ask the client to share trades for this period.",
            ]
        if holding_partial and held_slice is not None and held_slice + 0.51 < qf:
            next_steps.append(
                f"Remaining {qf - held_slice:g} of recommended {qf:g} still looks open."
            )
    elif verdict == "RECO_NOT_EXECUTED":
        next_steps = [
            "Confirm with advisor/client whether this recommendation was executed.",
            "Do not book until trade proof exists.",
        ]

    return {
        "ok": True,
        "skip": False,
        "confirmed": bool(sent_ok and (client_trade_ok or holding_ok or books_ok)),
        "manual_send_hypothesis": manual_send_hypothesis,
        "symbol": sym,
        "action": act.upper(),
        "quantity": qf,
        "held_qty": held_slice if holding_ok else None,
        "partial": holding_partial,
        "verdict": verdict,
        "break_at": break_at,
        "headline": headline,
        "next_steps": next_steps,
        "pipeline": steps,
        "email_confirmed": bool(email_hit),
        "client_trade_ok": client_trade_ok,
        "books_ok": books_ok,
        "holding_ok": holding_ok,
        "reco_id": reco.get("id"),
        "sent_at": reco.get("sent_at") or reco.get("when_label"),
        "session_name": reco.get("session_name"),
    }


def select_confirmed_threads(
    *,
    symbol: str,
    recos: List[Dict[str, Any]],
    email_lines: Optional[List[Dict[str, Any]]] = None,
    broker_fills: Optional[List[Dict[str, Any]]] = None,
    book_trades: Optional[List[Dict[str, Any]]] = None,
    statement_qty: Optional[float] = None,
    system_qty: Optional[float] = None,
    delta: Optional[float] = None,
    trade_folder_linked: bool = True,
) -> Dict[str, Any]:
    """
    Prefer emailed/sent threads. Drafts only if trade matches.
    Attribute a positive holdings gap to at most one BUY thread (best fit).
    """
    threads: List[Dict[str, Any]] = []
    skipped_drafts: List[Dict[str, Any]] = []

    for r in recos or []:
        th = build_confirmation_thread(
            symbol=symbol,
            reco=r,
            email_lines=email_lines,
            broker_fills=broker_fills,
            book_trades=book_trades,
            statement_qty=statement_qty,
            system_qty=system_qty,
            delta=delta,
            trade_folder_linked=trade_folder_linked,
        )
        if th.get("skip") and th.get("reason") == "unsent_draft":
            skipped_drafts.append(th)
            continue
        if th.get("skip"):
            continue
        threads.append(th)

    # Rank: email-confirmed + holdings fit first, then sent, then manual-send
    def _rank(t: Dict[str, Any]) -> Tuple:
        return (
            0 if t.get("email_confirmed") else 1,
            0 if t.get("holding_ok") else 1,
            0 if t.get("client_trade_ok") else 1,
            0 if t.get("confirmed") else 1,
            0 if not t.get("manual_send_hypothesis") else 1,
            -float(t.get("quantity") or 0),
        )

    threads.sort(key=_rank)

    # One BUY attribution for holdings gap
    chosen: List[Dict[str, Any]] = []
    gap_claimed = False
    for t in threads:
        if (t.get("action") or "").upper() == "BUY" and t.get("holding_ok"):
            if gap_claimed:
                continue
            chosen.append(t)
            gap_claimed = True
        elif (t.get("action") or "").upper() == "SELL":
            chosen.append(t)
        elif t.get("verdict") in ("EXECUTED_NOT_RECORDED", "RECO_NOT_EXECUTED", "RECO_NOT_SENT"):
            # Keep sent threads that need action even without holding claim
            if t.get("confirmed") or t.get("email_confirmed") or reco_is_emailed(
                {"emailed": t.get("email_confirmed"), "sent_at": t.get("sent_at"), "status": "sent"}
            ):
                if not (t.get("action") == "BUY" and gap_claimed and t.get("holding_ok")):
                    # Avoid duplicate BUY noise once gap claimed
                    if (t.get("action") or "").upper() == "BUY" and gap_claimed:
                        continue
                    chosen.append(t)

    return {
        "symbol": _norm(symbol),
        "threads": chosen,
        "all_threads": threads,
        "skipped_drafts": skipped_drafts,
        "primary": chosen[0] if chosen else None,
    }
