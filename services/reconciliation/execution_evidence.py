"""
For a focus reco (e.g. Jun 2026): check whether matching trades exist in
Jun–Jul books, and whether reco/workflow rows are still open (not executed).

Supports the advisor story: “executed but not updated in the system”.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from services.reconciliation.reco_delta_match import qty_close


def _norm_sym(sym: Optional[str]) -> str:
    s = (sym or "").upper().strip()
    if ":" in s:
        s = s.split(":")[-1].strip()
    return s


def reco_trade_window(sent_at: Optional[str]) -> Tuple[Optional[date], Optional[date], str]:
    """
    Window to look for execution: reco month through end of following month
    (e.g. Jun 2026 sent → 2026-06-01 … 2026-07-31).
    """
    if not sent_at:
        return None, None, ""
    try:
        d = date.fromisoformat(str(sent_at)[:10])
    except Exception:
        return None, None, ""
    start = date(d.year, d.month, 1)
    # End of next month
    if d.month == 12:
        end = date(d.year + 1, 1, 31)
        nm, ny = 1, d.year + 1
    else:
        nm, ny = d.month + 1, d.year
        end = date(ny, nm, monthrange(ny, nm)[1])
    label = f"{start.strftime('%b %Y')}–{end.strftime('%b %Y')}"
    return start, end, label


def summarize_trade_hits(
    *,
    fix_details: List[Dict[str, Any]],
    trades_by_symbol: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Pure: for each fix line (action+qty), see if a trade of same action≈qty exists.
    """
    matched = []
    missing = []
    for d in fix_details or []:
        sym = _norm_sym(d.get("symbol"))
        act = (d.get("action") or "").strip().lower()
        q = d.get("quantity")
        if not sym or q is None:
            continue
        qf = float(q)
        trades = trades_by_symbol.get(sym) or []
        hit = None
        for t in trades:
            tt = (t.get("type") or t.get("action") or "").strip().lower()
            tq = t.get("quantity")
            if tq is None:
                continue
            # BUY ↔ buy, SELL ↔ sell
            if act and tt and act != tt:
                continue
            if qty_close(float(tq), qf):
                hit = t
                break
        row = {
            "symbol": sym,
            "action": act,
            "quantity": qf,
            "trade": hit,
        }
        if hit:
            matched.append(row)
        else:
            missing.append(row)
    return {
        "lines_checked": len(matched) + len(missing),
        "trades_matching": matched,
        "trades_missing": missing,
        "all_missing": bool(missing) and not matched,
        "partial": bool(matched) and bool(missing),
        "all_found": bool(matched) and not missing,
    }


def enrich_story_counts(
    story: Dict[str, Any],
    *,
    breaks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Add resolved/pending counts and high-confidence symbol set."""
    out = dict(story or {})
    fixes = list(out.get("fixes") or [])
    remaining = list(out.get("remaining") or [])
    # Prefer break universe if given
    if breaks is not None:
        all_syms = {
            _norm_sym(b.get("symbol"))
            for b in breaks
            if b.get("symbol")
        }
        fix_set = {_norm_sym(s) for s in fixes}
        remaining = sorted(all_syms - fix_set) or remaining
    resolved = len(fixes)
    pending = len(remaining)
    total = resolved + pending
    sent = out.get("sent_at") or ""
    # Month label from subject/sent
    from services.reconciliation.simple_investigate import _reco_label

    reco_lbl = _reco_label(sent, out.get("subject"))
    out["resolved_count"] = resolved
    out["pending_count"] = pending
    out["gap_total"] = total
    out["high_confidence_symbols"] = fixes
    out["reco_label"] = reco_lbl
    out["resolved_summary"] = (
        f"{resolved} of {total} gap(s) explained by the {reco_lbl} "
        f"— these look missing in system books (high confidence)."
        if resolved
        else f"0 of {total} gap(s) explained by a recommendation email yet."
    )
    out["pending_summary"] = (
        f"{pending} other mismatch(es) still open."
        if pending
        else "No other mismatches pending."
    )
    out["remaining"] = remaining[:40]
    return out


def apply_found_missing_recommendation_labels(
    breaks: List[Dict[str, Any]],
    story: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    For symbols explained by the focus reco (e.g. Jun 2026), set status and
    hypothesis to FOUND_MISSING_RECOMMENDATION — not TIMING_SETTLEMENT_BATCH
    or raw MISSING_IN_SYSTEM / QTY_BREAK.
    """
    from services.reconciliation.simple_investigate import _reco_label

    hi = {
        _norm_sym(s)
        for s in (story.get("high_confidence_symbols") or story.get("fixes") or [])
    }
    if not hi:
        return breaks
    reco_lbl = story.get("reco_label") or _reco_label(
        story.get("sent_at"), story.get("subject")
    )
    for b in breaks:
        sym = _norm_sym(b.get("symbol"))
        if sym not in hi:
            continue
        b["high_confidence"] = True
        d = dict(b.get("diagnosis") or {})
        d["high_confidence"] = True
        d["confidence_label"] = (
            f"high — {reco_lbl} explains this gap (missing in books)"
        )
        if not d.get("match_status"):
            d["match_status"] = b.get("status")
        if d.get("top_hypothesis") and not d.get("prior_hypothesis"):
            d["prior_hypothesis"] = d.get("top_hypothesis")
        d["top_hypothesis"] = f"FOUND_MISSING_RECOMMENDATION — {reco_lbl}"
        b["status_display"] = "FOUND_MISSING_RECOMMENDATION"
        b["status"] = "FOUND_MISSING_RECOMMENDATION"
        b["diagnosis"] = d
    return breaks


def check_execution_and_workflow(
    *,
    client_id: int,
    story: Dict[str, Any],
    fix_details: List[Dict[str, Any]],
    security_ids_by_symbol: Dict[str, int],
) -> Dict[str, Any]:
    """
    DB: trades in reco month→next month; Recommendation statuses; session/workflow stage.
    """
    sent_at = story.get("sent_at")
    start, end, window_label = reco_trade_window(sent_at)
    empty = {
        "checked": False,
        "window": window_label or None,
        "window_start": start.isoformat() if start else None,
        "window_end": end.isoformat() if end else None,
        "message": "No reco sent-at — skipped trade window check.",
        "trades_matching_count": 0,
        "trades_missing_count": 0,
        "open_reco_count": 0,
        "executed_reco_count": 0,
        "session_status": None,
        "workflow_stage": None,
        "supports_not_booked": False,
    }
    if not start or not end:
        return empty

    from models import (
        MonthlyInvestment,
        Recommendation,
        RecommendationSession,
        Security,
        Transaction,
        Workflow,
    )

    # Collect trades per fix symbol in window
    trades_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for d in fix_details or []:
        sym = _norm_sym(d.get("symbol"))
        sid = security_ids_by_symbol.get(sym)
        if not sid:
            sec = Security.query.filter(Security.symbol == sym).first()
            sid = sec.id if sec else None
        if not sid:
            trades_by_symbol[sym] = []
            continue
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.max.time())
        rows = (
            Transaction.query.filter(
                Transaction.client_id == int(client_id),
                Transaction.security_id == int(sid),
                Transaction.transaction_date >= start_dt,
                Transaction.transaction_date <= end_dt,
            )
            .order_by(Transaction.transaction_date.asc())
            .limit(40)
            .all()
        )
        trades_by_symbol[sym] = [
            {
                "id": t.id,
                "date": t.transaction_date.date().isoformat()
                if t.transaction_date
                else None,
                "type": (t.type or "").lower(),
                "quantity": float(t.quantity) if t.quantity is not None else None,
            }
            for t in rows
        ]

    hit = summarize_trade_hits(
        fix_details=fix_details, trades_by_symbol=trades_by_symbol
    )

    # Recommendation rows near window for fix symbols
    open_recos = []
    executed_recos = []
    session_ids = set()
    for d in fix_details or []:
        sym = _norm_sym(d.get("symbol"))
        sid = security_ids_by_symbol.get(sym)
        if not sid:
            sec = Security.query.filter(Security.symbol == sym).first()
            sid = sec.id if sec else None
        if not sid:
            continue
        recs = (
            Recommendation.query.filter(
                Recommendation.client_id == int(client_id),
                Recommendation.security_id == int(sid),
                Recommendation.sent_at.isnot(None),
                Recommendation.sent_at >= datetime.combine(start, datetime.min.time()),
                Recommendation.sent_at
                <= datetime.combine(end + timedelta(days=14), datetime.max.time()),
            )
            .order_by(Recommendation.sent_at.desc())
            .limit(8)
            .all()
        )
        for r in recs:
            if r.session_id:
                session_ids.add(r.session_id)
            st = (r.status or "").strip().lower()
            row = {
                "symbol": sym,
                "status": st,
                "quantity": float(r.quantity) if r.quantity is not None else None,
                "sent_at": r.sent_at.date().isoformat() if r.sent_at else None,
                "session_id": r.session_id,
                "open": st in ("sent", "pending", "active", ""),
            }
            if row["open"] and st != "executed":
                open_recos.append(row)
            if st == "executed" or r.executed_at:
                executed_recos.append(row)

    session_status = None
    if session_ids:
        sess = RecommendationSession.query.get(next(iter(session_ids)))
        if sess:
            session_status = sess.status

    # Monthly investment workflow still open? (via MonthlyInvestment.client_id)
    workflow_stage = None
    try:
        wfs = (
            Workflow.query.join(
                MonthlyInvestment,
                Workflow.monthly_investment_id == MonthlyInvestment.id,
            )
            .filter(MonthlyInvestment.client_id == int(client_id))
            .order_by(Workflow.id.desc())
            .limit(8)
            .all()
        )
        for w in wfs:
            stage = (w.current_stage or "").upper()
            # Prefer workflow whose investment_date falls in/near window
            idate = w.investment_date
            in_window = bool(
                idate and start <= idate <= (end + timedelta(days=45))
            )
            if stage and stage != "COMPLETED" and (in_window or workflow_stage is None):
                workflow_stage = stage
                if in_window:
                    break
            elif not workflow_stage and stage:
                workflow_stage = stage
    except Exception:
        workflow_stage = None

    supports = hit.get("all_missing") or (
        bool(hit.get("trades_missing")) and len(open_recos) > 0
    )
    parts = [
        f"Checked trades in {window_label} for the {len(fix_details or [])} "
        f"reco line(s) that explain gaps."
    ]
    if hit.get("all_missing"):
        parts.append(
            "No matching execution trades found — supports "
            "“recommendation executed but not updated in the system”."
        )
    elif hit.get("all_found"):
        parts.append(
            "Matching trades exist for these lines — gap may be as-of timing "
            "or a different cause; re-check dates."
        )
    elif hit.get("partial"):
        parts.append(
            f"{len(hit['trades_matching'])} line(s) have trades; "
            f"{len(hit['trades_missing'])} still missing in books."
        )
    if open_recos:
        parts.append(
            f"{len(open_recos)} recommendation row(s) still open/sent "
            f"(not marked executed) — workflow/books not closed out."
        )
    if executed_recos and hit.get("trades_missing"):
        parts.append(
            f"{len(executed_recos)} marked executed in app but trade qty still "
            "missing in books — recording lag."
        )
    if session_status and session_status.lower() not in ("completed", "cancelled"):
        parts.append(f"Recommendation session status is “{session_status}” (not completed).")
    if workflow_stage and workflow_stage != "COMPLETED":
        parts.append(
            f"Client investment workflow stage is {workflow_stage} "
            "(still open — consistent with trades not fully updated)."
        )

    return {
        "checked": True,
        "window": window_label,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "message": " ".join(parts),
        "trades_matching_count": len(hit.get("trades_matching") or []),
        "trades_missing_count": len(hit.get("trades_missing") or []),
        "trades_missing_symbols": [
            m["symbol"] for m in (hit.get("trades_missing") or [])[:20]
        ],
        "trades_matching_symbols": [
            m["symbol"] for m in (hit.get("trades_matching") or [])[:20]
        ],
        "open_reco_count": len(open_recos),
        "executed_reco_count": len(executed_recos),
        "open_recos": open_recos[:15],
        "session_status": session_status,
        "workflow_stage": workflow_stage,
        "supports_not_booked": bool(supports),
    }
