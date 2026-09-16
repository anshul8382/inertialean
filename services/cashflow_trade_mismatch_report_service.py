"""
Trade vs recorded cashflow totals mismatch report.

Compares lifetime signed net cashflow (Cashflow rows) to net implied by BUY/SELL
trades, with opening-book epoch split (2000-01-01), 1%+floor materiality, and
day pairing: same-day band, then ±3d settlement, then ±14d amount clubbing.
matched requires both lifetime net and series alignment; large unclubbed day
gaps → mismatch_review (XIRR impact proxy).

Statuses:
  matched | ignore_opening_book | mismatch_material | mismatch_review
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import case, func

from models import Cashflow, Client, Transaction, db
from services.cashflow_service import (
    MATCH_CLUB_DAYS,
    MATCH_NEARBY_FLOOR,
    MATCH_NEARBY_PCT,
    MATCH_SAME_DAY_FLOOR,
    MATCH_SAME_DAY_PCT,
    MATCH_SETTLEMENT_DAYS,
    canonical_cashflow_signed_amount,
)

# Opening / unknown-date trades — excluded from post-cutoff reconcile net.
SKIP_TRADE_DATE = date(2000, 1, 1)
# Legacy flat tolerance kept for CLI backward-compat flag; materiality uses %.
DEFAULT_TOLERANCE = 10000.0
MATERIALITY_PCT = MATCH_SAME_DAY_PCT
MATERIALITY_FLOOR = MATCH_SAME_DAY_FLOOR
CLUB_DAYS = MATCH_CLUB_DAYS
SETTLEMENT_DAYS = MATCH_SETTLEMENT_DAYS
NEARBY_PCT = MATCH_NEARBY_PCT
NEARBY_FLOOR = MATCH_NEARBY_FLOOR


def first_real_trade_date(
    trades,
    *,
    skip_date: date = SKIP_TRADE_DATE,
) -> Optional[date]:
    """Earliest BUY/SELL date that is not the opening-book dummy date."""
    real: List[date] = []
    for t in trades or []:
        d = _as_date(getattr(t, "transaction_date", None))
        if d is not None and d != skip_date:
            real.append(d)
    return min(real) if real else None


def split_cashflows_at_reconcile_start(
    cashflows,
    *,
    reconcile_start: Optional[date],
    has_opening_book: bool,
) -> Tuple[List[Any], List[Any]]:
    """
    Split cashflows into opening-epoch vs post-cutoff reconcile window.

    With 2000-01-01 opening book, cashflows **before** the first real trade date
    funded the opening positions (datewise trade history was never entered). They
    must not be day-matched to post-cutoff trades. Guideline: after first real
    trade date, post-cutoff trades and cashflows should match.

    When ``reconcile_start`` is set (opening book and/or approved advisor notes
    through-date + 1 day), always split — even if there is no 2000-01-01 book.
    """
    if reconcile_start is None:
        if not has_opening_book:
            return [], list(cashflows or [])
        # Opening book only (no real trades yet) — entire CF history is opening-epoch.
        return list(cashflows or []), []
    pre: List[Any] = []
    post: List[Any] = []
    for cf in cashflows or []:
        d = _as_date(getattr(cf, "date", None))
        if d is None:
            continue
        if d < reconcile_start:
            pre.append(cf)
        else:
            post.append(cf)
    return pre, post


def _advisor_notes_reconcile_bounds(client_id: int) -> Tuple[Optional[date], Optional[date]]:
    """(notes_through, day_after) from approved advisor-notes artifact, if any."""
    try:
        from services.cashflow_trade_advisor_notes_service import get_approved_reconciled_through

        through = get_approved_reconciled_through(int(client_id))
        if through is None:
            return None, None
        return through, through + timedelta(days=1)
    except Exception:
        return None, None


def resolve_effective_reconcile_start(
    client_id: int,
    *,
    opening_reconcile_start: Optional[date],
    has_opening_book: bool,
) -> Tuple[Optional[date], Optional[date], bool]:
    """
    Effective CF↔trade window start + notes_through + whether an epoch split applies.

    Approved advisor notes through D → window starts at D+1 (history through D trusted).
    Combined with opening-book first-real via max().
    """
    notes_through, day_after = _advisor_notes_reconcile_bounds(client_id)
    starts: List[date] = []
    if has_opening_book and opening_reconcile_start is not None:
        starts.append(opening_reconcile_start)
    if day_after is not None:
        starts.append(day_after)
    if not starts:
        return None, notes_through, bool(has_opening_book)
    return max(starts), notes_through, True


def materiality_threshold(recorded_net: float) -> float:
    return max(MATERIALITY_PCT * abs(float(recorded_net or 0.0)), MATERIALITY_FLOOR)


def _trade_amount(txn: Transaction) -> float:
    raw = getattr(txn, "amount", None)
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    try:
        return float(txn.quantity) * float(txn.price)
    except (TypeError, ValueError):
        return 0.0


def net_cashflow_from_trades(transactions) -> float:
    """
    Signed net from trades (same convention as Cashflow.amount):
    BUY → negative (invested), SELL → positive (withdrawn).
    """
    total = 0.0
    for t in transactions:
        ttype = (getattr(t, "type", None) or "").upper()
        amt = _trade_amount(t)
        if ttype == "BUY":
            total -= amt
        elif ttype == "SELL":
            total += amt
    return total


def net_cashflow_from_records(cashflows) -> float:
    """Sum using matching convention (INFLOW +amount ≡ INFLOW −amount)."""
    total = 0.0
    for cf in cashflows:
        try:
            total += canonical_cashflow_signed_amount(cf)
        except (TypeError, ValueError):
            continue
    return total


def same_day_amount_tolerance(a: float, b: float = 0.0) -> float:
    """Same-calendar-day band: max(₹1k, 1% of either side)."""
    return max(
        MATERIALITY_FLOOR,
        abs(float(a or 0)) * MATERIALITY_PCT,
        abs(float(b or 0)) * MATERIALITY_PCT,
    )


def nearby_amount_tolerance(a: float, b: float = 0.0) -> float:
    """Cross-day settlement / split band: max(₹2k, 2% of either side)."""
    return max(
        NEARBY_FLOOR,
        abs(float(a or 0)) * NEARBY_PCT,
        abs(float(b or 0)) * NEARBY_PCT,
    )


def _within_amount(a: float, b: float, tol: float) -> bool:
    return abs(round(float(a) - float(b), 2)) <= float(tol)


def _nonzero_day_keys(book: Dict[date, float]) -> List[date]:
    return [d for d, v in book.items() if abs(round(float(v or 0), 2)) > 0.005]


def _days_in_net_window(
    center: date,
    book: Dict[date, float],
    window: int,
) -> List[date]:
    out: List[date] = []
    for d in _nonzero_day_keys(book):
        if abs((d - center).days) <= int(window):
            out.append(d)
    return sorted(out)


def pair_day_nets(
    cf_book: Dict[date, float],
    tx_book: Dict[date, float],
    *,
    settlement_days: int = SETTLEMENT_DAYS,
    club_days: int = CLUB_DAYS,
) -> Tuple[List[Dict[str, Any]], Dict[date, float], Dict[date, float]]:
    """
    Clear matching day-nets: same-day band, then nearest ±settlement_days pair,
    then one↔sum within ±club_days. Returns (cross-day explanations, leftover CF, leftover TX).
    """
    cf = {d: round(float(v or 0), 2) for d, v in (cf_book or {}).items()}
    tx = {d: round(float(v or 0), 2) for d, v in (tx_book or {}).items()}
    explanations: List[Dict[str, Any]] = []

    for d in sorted(set(cf) | set(tx)):
        a = cf.get(d, 0.0)
        s = tx.get(d, 0.0)
        if a == 0.0 and s == 0.0:
            continue
        if _within_amount(a, s, same_day_amount_tolerance(a, s)):
            cf[d] = 0.0
            tx[d] = 0.0

    def _explain_orphan(cf_date: date, cf_net: float, trade_dates: List[date], trade_net: float) -> Dict[str, Any]:
        trade_dates = sorted(trade_dates)
        labels = [td.isoformat() for td in trade_dates]
        return {
            "issue": "orphan_cashflow",
            "cashflow_date": cf_date.isoformat(),
            "trade_dates": labels,
            "trade_date": labels[0] if labels else None,
            "cashflow_net": round(cf_net, 2),
            "trade_net": round(trade_net, 2),
            "trade_count": len(trade_dates),
            "summary": (
                f"Cashflow on {cf_date.isoformat()} ({cf_net:,.0f}) is explained by "
                f"trade(s) on {', '.join(labels[:5])}"
                f"{'…' if len(labels) > 5 else ''} "
                f"(combined {trade_net:,.0f})."
            ),
        }

    def _explain_missing(tx_date: date, trade_net: float, cf_dates: List[date], cf_net: float) -> Dict[str, Any]:
        cf_dates = sorted(cf_dates)
        labels = [cd.isoformat() for cd in cf_dates]
        return {
            "issue": "missing_cashflow",
            "trade_date": tx_date.isoformat(),
            "cashflow_dates": labels,
            "cashflow_date": labels[0] if labels else None,
            "cashflow_net": round(cf_net, 2),
            "trade_net": round(trade_net, 2),
            "cashflow_count": len(cf_dates),
            "summary": (
                f"Trades on {tx_date.isoformat()} ({trade_net:,.0f}) are explained by "
                f"cashflow(s) on {', '.join(labels[:5])}"
                f"{'…' if len(labels) > 5 else ''} "
                f"(combined {cf_net:,.0f})."
            ),
        }

    for _ in range(50):
        progress = False

        for d in list(_nonzero_day_keys(cf)):
            a = cf.get(d, 0.0)
            if a == 0.0:
                continue
            # Closer dates first so T+1 wins over a same-amount row later in the ±14d window.
            candidates = _days_in_net_window(d, tx, club_days)
            candidates.sort(
                key=lambda x: (
                    0 if abs((x - d).days) <= settlement_days else 1,
                    abs((x - d).days),
                )
            )
            for d2 in candidates:
                if d2 == d:
                    continue
                s = tx.get(d2, 0.0)
                if s == 0.0:
                    continue
                if _within_amount(a, s, nearby_amount_tolerance(a, s)):
                    explanations.append(_explain_orphan(d, a, [d2], s))
                    cf[d] = 0.0
                    tx[d2] = 0.0
                    progress = True
                    break

        for d in list(_nonzero_day_keys(tx)):
            s = tx.get(d, 0.0)
            if s == 0.0:
                continue
            candidates = _days_in_net_window(d, cf, club_days)
            candidates.sort(
                key=lambda x: (
                    0 if abs((x - d).days) <= settlement_days else 1,
                    abs((x - d).days),
                )
            )
            for d2 in candidates:
                if d2 == d:
                    continue
                a = cf.get(d2, 0.0)
                if a == 0.0:
                    continue
                if _within_amount(a, s, nearby_amount_tolerance(a, s)):
                    explanations.append(_explain_missing(d, s, [d2], a))
                    tx[d] = 0.0
                    cf[d2] = 0.0
                    progress = True
                    break

        for d in list(_nonzero_day_keys(cf)):
            a = cf.get(d, 0.0)
            if a == 0.0:
                continue
            peers = _days_in_net_window(d, tx, club_days)
            if len(peers) < 2:
                continue
            total = round(sum(tx.get(p, 0.0) for p in peers), 2)
            if _within_amount(a, total, nearby_amount_tolerance(a, total)):
                explanations.append(_explain_orphan(d, a, peers, total))
                cf[d] = 0.0
                for p in peers:
                    tx[p] = 0.0
                progress = True

        for d in list(_nonzero_day_keys(tx)):
            s = tx.get(d, 0.0)
            if s == 0.0:
                continue
            peers = _days_in_net_window(d, cf, club_days)
            if len(peers) < 2:
                continue
            total = round(sum(cf.get(p, 0.0) for p in peers), 2)
            if _within_amount(s, total, nearby_amount_tolerance(s, total)):
                explanations.append(_explain_missing(d, s, peers, total))
                tx[d] = 0.0
                for p in peers:
                    cf[p] = 0.0
                progress = True

        if not progress:
            break

    return explanations, cf, tx


def client_ids_with_skip_trade_date(skip_date: date = SKIP_TRADE_DATE) -> set[int]:
    rows = (
        db.session.query(Transaction.client_id)
        .filter(func.date(Transaction.transaction_date) == skip_date)
        .distinct()
        .all()
    )
    return {int(r[0]) for r in rows if r[0] is not None}


def _aggregate_trade_nets_split(
    client_ids: Optional[List[int]] = None,
    skip_date: date = SKIP_TRADE_DATE,
) -> Tuple[Dict[int, float], Dict[int, float]]:
    """Return (opening_nets, post_cutoff_nets) per client.

    Amount uses COALESCE(amount, quantity*price) to match Python `_trade_amount`.
    """
    opening: Dict[int, float] = {}
    post: Dict[int, float] = {}

    trade_amt = func.coalesce(
        Transaction.amount,
        Transaction.quantity * Transaction.price,
    )
    buy_sell = case(
        (func.upper(Transaction.type) == "BUY", -trade_amt),
        (func.upper(Transaction.type) == "SELL", trade_amt),
        else_=0,
    )
    q = db.session.query(
        Transaction.client_id,
        func.date(Transaction.transaction_date),
        func.coalesce(func.sum(buy_sell), 0),
    ).filter(func.upper(Transaction.type).in_(["BUY", "SELL"]))
    if client_ids is not None:
        if not client_ids:
            return {}, {}
        q = q.filter(Transaction.client_id.in_(client_ids))
    q = q.group_by(Transaction.client_id, func.date(Transaction.transaction_date))
    for cid, d, net in q.all():
        cid_i = int(cid)
        n = float(net or 0)
        d_norm = d if isinstance(d, date) else _as_date(d)
        if d_norm == skip_date:
            opening[cid_i] = opening.get(cid_i, 0.0) + n
        else:
            post[cid_i] = post.get(cid_i, 0.0) + n
    return opening, post


def _aggregate_trade_nets(client_ids: Optional[List[int]] = None) -> Dict[int, float]:
    """client_id → signed trade net (all BUY/SELL including opening)."""
    opening, post = _aggregate_trade_nets_split(client_ids)
    all_ids = set(opening) | set(post)
    return {cid: opening.get(cid, 0.0) + post.get(cid, 0.0) for cid in all_ids}


def _first_real_trade_dates_by_client(
    client_ids: Optional[List[int]] = None,
    skip_date: date = SKIP_TRADE_DATE,
) -> Dict[int, date]:
    q = db.session.query(
        Transaction.client_id,
        func.min(func.date(Transaction.transaction_date)),
    ).filter(
        func.upper(Transaction.type).in_(["BUY", "SELL"]),
        func.date(Transaction.transaction_date) != skip_date,
    )
    if client_ids is not None:
        if not client_ids:
            return {}
        q = q.filter(Transaction.client_id.in_(client_ids))
    q = q.group_by(Transaction.client_id)
    out: Dict[int, date] = {}
    for cid, d in q.all():
        d_norm = d if isinstance(d, date) else _as_date(d)
        if d_norm is not None:
            out[int(cid)] = d_norm
    return out


def _aggregate_cashflow_nets_split_by_reconcile_start(
    client_ids: List[int],
    *,
    first_real_by_client: Dict[int, date],
    opening_client_ids: set[int],
) -> Tuple[Dict[int, float], Dict[int, float]]:
    """
    Return (opening_epoch_cf_nets, post_cutoff_cf_nets).

    Non-opening-book clients: all CF in post bucket.
    Opening-book with no real trades: all CF in opening-epoch bucket.
    """
    if not client_ids:
        return {}, {}
    rows = (
        db.session.query(
            Cashflow.client_id,
            func.date(Cashflow.date),
            func.coalesce(func.sum(Cashflow.amount), 0),
        )
        .filter(Cashflow.client_id.in_(client_ids))
        .group_by(Cashflow.client_id, func.date(Cashflow.date))
        .all()
    )
    pre: Dict[int, float] = {}
    post: Dict[int, float] = {}
    for cid, d, net in rows:
        cid_i = int(cid)
        n = float(net or 0)
        d_norm = d if isinstance(d, date) else _as_date(d)
        if cid_i not in opening_client_ids:
            post[cid_i] = post.get(cid_i, 0.0) + n
            continue
        fr = first_real_by_client.get(cid_i)
        if fr is None or d_norm is None or d_norm < fr:
            pre[cid_i] = pre.get(cid_i, 0.0) + n
        else:
            post[cid_i] = post.get(cid_i, 0.0) + n
    return pre, post


def _aggregate_cashflow_nets(client_ids: Optional[List[int]] = None) -> Dict[int, float]:
    q = db.session.query(
        Cashflow.client_id,
        func.coalesce(func.sum(Cashflow.amount), 0),
    )
    if client_ids is not None:
        if not client_ids:
            return {}
        q = q.filter(Cashflow.client_id.in_(client_ids))
    q = q.group_by(Cashflow.client_id)
    return {int(cid): float(net or 0) for cid, net in q.all()}


def _aggregate_counts(client_ids: Optional[List[int]] = None) -> Dict[int, Dict[str, int]]:
    counts: Dict[int, Dict[str, int]] = {}

    cf_q = db.session.query(Cashflow.client_id, func.count(Cashflow.id))
    if client_ids is not None:
        if not client_ids:
            return {}
        cf_q = cf_q.filter(Cashflow.client_id.in_(client_ids))
    for cid, n in cf_q.group_by(Cashflow.client_id).all():
        counts.setdefault(int(cid), {"cashflow_count": 0, "buy_sell_count": 0})
        counts[int(cid)]["cashflow_count"] = int(n)

    tx_q = db.session.query(Transaction.client_id, func.count(Transaction.id)).filter(
        func.upper(Transaction.type).in_(["BUY", "SELL"])
    )
    if client_ids is not None:
        tx_q = tx_q.filter(Transaction.client_id.in_(client_ids))
    for cid, n in tx_q.group_by(Transaction.client_id).all():
        counts.setdefault(int(cid), {"cashflow_count": 0, "buy_sell_count": 0})
        counts[int(cid)]["buy_sell_count"] = int(n)

    return counts


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return value.date()  # type: ignore[attr-defined]
    except Exception:
        return None


def classify_client_totals(
    *,
    recorded_net: float,
    opening_trade_net: float,
    post_cutoff_trade_net: float,
    has_opening_book: bool,
    materiality: Optional[float] = None,
    post_cutoff_cashflow_net: Optional[float] = None,
    opening_epoch_cashflow_net: Optional[float] = None,
    first_real_trade_date_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pure classifier on nets. Series gate applied in diagnose / report.

    With opening book, pass ``post_cutoff_cashflow_net`` = cashflows on/after the
    first real trade date. Lifetime ``recorded_net`` stays informational; the
    material gap is post-cutoff CF vs post-cutoff trades only.
    """
    post_cf = float(
        recorded_net if post_cutoff_cashflow_net is None else post_cutoff_cashflow_net
    )
    pre_cf = float(
        opening_epoch_cashflow_net
        if opening_epoch_cashflow_net is not None
        else (float(recorded_net) - post_cf if has_opening_book else 0.0)
    )
    scale = post_cf if has_opening_book else float(recorded_net)
    thresh = float(materiality if materiality is not None else materiality_threshold(scale))
    post_diff = post_cf - float(post_cutoff_trade_net)
    abs_post = abs(post_diff)
    opening_gap = pre_cf - float(opening_trade_net)

    if abs_post <= thresh:
        status = "ignore_opening_book" if has_opening_book else "matched"
    elif abs_post <= thresh * 1.5:
        status = "mismatch_review"
    else:
        status = "mismatch_material"

    return {
        "status": status,
        "recorded_net_cashflow": round(float(recorded_net), 2),
        "post_cutoff_cashflow_net": round(post_cf, 2),
        "opening_epoch_cashflow_net": round(pre_cf, 2),
        "opening_trade_net": round(float(opening_trade_net), 2),
        "post_cutoff_trade_net": round(float(post_cutoff_trade_net), 2),
        "trade_net_cashflow": round(float(opening_trade_net) + float(post_cutoff_trade_net), 2),
        "difference": round(post_diff, 2),
        "abs_difference": round(abs_post, 2),
        "opening_book_gap": round(opening_gap, 2),
        "materiality_threshold": round(thresh, 2),
        "has_skip_trade_date": bool(has_opening_book),
        "first_real_trade_date": first_real_trade_date_iso,
        "reconcile_start_date": first_real_trade_date_iso if has_opening_book else None,
    }


def apply_series_match_gate(status: str, large_day_count: int) -> str:
    """
    matched / ignore_opening_book require both lifetime net and series alignment.

    Large unclubbed day gaps (amount+date) are a proxy for XIRR-relevant timing/amount
    skew — v1 does not recompute live XIRR. Small day hints may remain.
    """
    if int(large_day_count or 0) <= 0:
        return status
    if status in ("matched", "ignore_opening_book"):
        return "mismatch_review"
    return status


def _fmt_inr(x: float) -> str:
    try:
        return f"{float(x):,.0f}"
    except (TypeError, ValueError):
        return "0"


def _nearby_cashflows_near_amount(
    cashflows: List[Any],
    *,
    focus_d: date,
    target_amount: float,
    club_days: int = CLUB_DAYS,
    exclude_same_day: bool = True,
) -> List[Dict[str, Any]]:
    """Cashflows in ±club_days whose amount is near target (for misdate / mistype hints)."""
    if abs(float(target_amount or 0)) < 1:
        return []
    target = float(target_amount)
    band = max(materiality_threshold(target), abs(target) * 0.05)  # 5% soft nearness for hints
    out: List[Dict[str, Any]] = []
    for cf in cashflows:
        cd = _as_date(cf.date)
        if cd is None:
            continue
        if exclude_same_day and cd == focus_d:
            continue
        if abs((cd - focus_d).days) > club_days:
            continue
        amt = canonical_cashflow_signed_amount(cf)
        delta = abs(amt - target)
        if delta <= band or abs(abs(amt) - abs(target)) <= band:
            out.append(
                {
                    "id": cf.id,
                    "date": cd.isoformat(),
                    "amount": round(amt, 2),
                    "delta_vs_target": round(amt - target, 2),
                    "description": (getattr(cf, "description", None) or "")[:120],
                    "url": (
                        f"/cashflows?client_id={getattr(cf, 'client_id', '')}"
                        f"&start_date={cd.isoformat()}&end_date={cd.isoformat()}"
                    ),
                    "fact_id": f"cf:{cf.id}",
                }
            )
    out.sort(key=lambda r: abs(float(r.get("delta_vs_target") or 0)))
    return out[:8]


def build_resolution_guidance(
    *,
    client_id: int,
    status: str,
    difference: float,
    materiality_threshold: float,
    large_days: Optional[List[Dict[str, Any]]] = None,
    small_days: Optional[List[Dict[str, Any]]] = None,
    clubbed_explanations: Optional[List[Dict[str, Any]]] = None,
    suspect_trades: Optional[List[Dict[str, Any]]] = None,
    suspect_cashflows: Optional[List[Dict[str, Any]]] = None,
    nearby_cashflow_candidates: Optional[List[Dict[str, Any]]] = None,
    club_days: int = CLUB_DAYS,
    chronological: Optional[Dict[str, Any]] = None,
    has_opening_book: bool = False,
    reconcile_start_date: Optional[str] = None,
    opening_epoch_cashflow_net: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Deterministic source-of-mismatch + exact checks + fix options (guidelines).
    Suggest-only — never mutates data. Corporate actions do not affect cashflows.
    """
    large_days = large_days or []
    small_days = small_days or []
    clubbed = clubbed_explanations or []
    nearby_cfs = nearby_cashflow_candidates or []
    chrono = chronological or {}
    focus = large_days[0] if large_days else None
    gap = float(difference or 0)
    thresh = float(materiality_threshold or 0)
    abs_gap = abs(gap)

    empty_source = {
        "code": "none",
        "label": "No material mismatch",
        "confidence": "high",
        "evidence": [],
    }

    if status in ("matched", "ignore_opening_book", "accepted_non_material"):
        opening_note = ""
        if status == "ignore_opening_book" or has_opening_book:
            opening_note = (
                f" Opening-book trades on {SKIP_TRADE_DATE.isoformat()} are excluded; "
                f"cashflows before first real trade"
                f"{(' (' + reconcile_start_date + ')') if reconcile_start_date else ''} "
                "are opening-epoch (not day-matched)."
            )
        return {
            "headline": "No material fix needed",
            "finding": (
                f"Post-cutoff net gap {_fmt_inr(gap)} is within band {_fmt_inr(thresh)}, "
                "and series has no large unclubbed day gaps."
                + opening_note
            ),
            "source": empty_source,
            "check_exactly": [],
            "root_cause": None,
            "explains_lifetime_gap": False,
            "focus_day": None,
            "fix_options": [],
            "primary_action": {
                "label": "Optional: skim small day hints",
                "url": f"/cashflows?client_id={client_id}",
            },
        }

    finding_lines: List[str] = []
    if has_opening_book:
        finding_lines.append(
            f"Opening book present ({SKIP_TRADE_DATE.isoformat()} trades). "
            "First real trade date = earliest BUY/SELL after 2000-01-01. "
            "Cashflows before that first real trade are opening-epoch — "
            "they funded positions entered without dated trade history and are "
            "not day-matched to BUY/SELL."
        )
        if reconcile_start_date:
            finding_lines.append(
                f"CF↔trade reconcile window for this page starts at {reconcile_start_date} "
                f"(first real trade and/or day after approved advisor notes). "
                f"Opening-epoch cashflow net {_fmt_inr(opening_epoch_cashflow_net or 0)} "
                "is ignored for day-level orphans (gap vs opening trades is not a concern)."
            )
        else:
            finding_lines.append(
                "No post-cutoff trades yet — only opening-book epoch applies."
            )
    finding_lines.append(
        f"Post-cutoff gap is {_fmt_inr(gap)} vs allowed band {_fmt_inr(thresh)} "
        f"({'material' if status == 'mismatch_material' else 'review'})."
    )
    if chrono.get("matched_from_recent_date") or chrono.get("matched_through_date"):
        mf = chrono.get("matched_from_recent_date") or chrono.get("matched_through_date")
        finding_lines.append(
            f"From most recent activity back, CF↔trades stay within band from {mf} through today "
            f"(CF {_fmt_inr(chrono.get('matched_through_cashflow_net') or 0)} / "
            f"trades {_fmt_inr(chrono.get('matched_through_trade_net') or 0)})."
        )
        if chrono.get("first_break_date") and not chrono.get("fully_matched"):
            finding_lines.append(
                f"Older history breaks at {chrono['first_break_date']} — "
                "bad apples are unexplained days before the recent-matched window."
            )
    elif chrono.get("first_break_date"):
        finding_lines.append(
            f"From most recent activity back, the series is outside band at "
            f"{chrono['first_break_date']}."
        )
    if clubbed:
        finding_lines.append(
            f"{len(clubbed)} day(s) were cleared by clubbing (nearby dates + similar amounts) "
            "and are not the problem."
        )

    root_cause = None
    explains = False
    fix_options: List[Dict[str, Any]] = []
    focus_day_pack = None
    source = {
        "code": "unlocated",
        "label": "Lifetime nets disagree; no single large day isolated",
        "confidence": "low",
        "evidence": [
            f"Gap {_fmt_inr(gap)} exceeds band {_fmt_inr(thresh)} after clubbing.",
        ],
    }
    check_exactly: List[Dict[str, Any]] = []

    if focus:
        issue = focus.get("issue")
        d = focus.get("date")
        day_diff = float(focus.get("day_difference") or 0)
        cf_net = float(focus.get("cashflow_net") or 0)
        trade_net = float(focus.get("trade_net") or 0)
        tx_ids = list(focus.get("transaction_ids") or [])[:12]
        cf_ids = list(focus.get("cashflow_ids") or [])[:12]
        explains = abs_gap > 0 and abs(abs(day_diff) - abs_gap) <= max(1.0, abs_gap * 0.02)

        finding_lines.append(
            f"Largest unexplained day: {d} ({issue}) — cashflow net {_fmt_inr(cf_net)}, "
            f"trade net {_fmt_inr(trade_net)}, day gap {_fmt_inr(day_diff)}."
        )
        if explains:
            finding_lines.append(
                "This day alone accounts for essentially the full lifetime gap — fix here first."
            )
        else:
            finding_lines.append(
                "Lifetime gap may combine several days; start with this largest day, then re-check."
            )

        try:
            focus_d = date.fromisoformat(str(d))
            win_start = (focus_d - timedelta(days=club_days)).isoformat()
            win_end = (focus_d + timedelta(days=club_days)).isoformat()
        except Exception:
            win_start = d
            win_end = d

        trade_links = [
            {"id": tid, "url": f"/transaction/{tid}/edit", "label": f"Edit trade #{tid}"}
            for tid in tx_ids[:8]
        ]
        day_trades = [
            t
            for t in (suspect_trades or [])
            if str(t.get("date")) == str(d)
        ][:12]
        day_cfs = [
            c
            for c in (suspect_cashflows or [])
            if str(c.get("date")) == str(d)
        ][:8]

        trade_lines = []
        for t in day_trades:
            trade_lines.append(
                f"#{t.get('id')} {(t.get('type') or '').upper()} "
                f"amount {_fmt_inr(t.get('amount') or 0)} "
                f"(CF effect {_fmt_inr(t.get('signed_cashflow_effect') or 0)})"
            )
        if not trade_lines and tx_ids:
            trade_lines = [f"#{tid}" for tid in tx_ids]

        focus_day_pack = {
            "date": d,
            "issue": issue,
            "day_difference": round(day_diff, 2),
            "cashflow_net": round(cf_net, 2),
            "trade_net": round(trade_net, 2),
            "transaction_ids": tx_ids,
            "cashflow_ids": cf_ids,
            "guess": focus.get("guess"),
            "cashflows_url": f"/cashflows?client_id={client_id}&start_date={d}&end_date={d}",
            "window_cashflows_url": (
                f"/cashflows?client_id={client_id}&start_date={win_start}&end_date={win_end}"
            ),
            "transactions_url": f"/transactions?client_id={client_id}",
            "trade_edit_links": trade_links,
            "day_trades": day_trades,
            "day_cashflows": day_cfs,
            "trade_lines": trade_lines,
            "nearby_cashflow_candidates": nearby_cfs,
        }

        near_exact = [
            c
            for c in nearby_cfs
            if abs(float(c.get("delta_vs_target") or 999999)) <= max(1000.0, abs(trade_net or cf_net) * 0.01)
        ]
        near_loose = [c for c in nearby_cfs if c not in near_exact]

        if issue == "missing_cashflow":
            expected = trade_net
            if near_exact:
                source = {
                    "code": "cashflow_date_skew",
                    "label": "Source: cashflow exists nearby but not aligned to these trades (date skew)",
                    "confidence": "high",
                    "evidence": [
                        f"Trades on {d} imply cashflow {_fmt_inr(expected)}; no cashflow on that exact date.",
                        "Clubbing requires ±14d AND amount match — a near-amount cashflow was found in the window "
                        "but did not clear the day (check date/amount).",
                        *[
                            f"Candidate CF #{c['id']} on {c['date']} amount {_fmt_inr(c['amount'])} "
                            f"(Δ {_fmt_inr(c['delta_vs_target'])} vs expected)."
                            for c in near_exact[:3]
                        ],
                    ],
                }
                root_cause = (
                    f"Trades on {d} need cashflow {_fmt_inr(expected)}. A cashflow with a similar amount exists "
                    f"in ±{club_days} days (see candidates) — most likely wrong date or partial amount, not a missing note."
                )
            elif near_loose:
                source = {
                    "code": "possible_mistyped_cashflow",
                    "label": "Source: likely mistyped / partial cashflow near these trades",
                    "confidence": "medium",
                    "evidence": [
                        f"Trades on {d} imply {_fmt_inr(expected)}; no same-day cashflow.",
                        "Nearby cashflows exist with amounts in the ballpark but not within the tight clubbing band.",
                        *[
                            f"Candidate CF #{c['id']} on {c['date']} amount {_fmt_inr(c['amount'])} "
                            f"(Δ {_fmt_inr(c['delta_vs_target'])})."
                            for c in near_loose[:3]
                        ],
                    ],
                }
                root_cause = (
                    f"Trades on {d} imply {_fmt_inr(expected)}. Nearby notes look related but amounts do not match "
                    "closely enough for clubbing — check for typo or split notes."
                )
            else:
                source = {
                    "code": "missing_advisor_cashflow",
                    "label": "Source: missing advisor cashflow for these trades",
                    "confidence": "high" if explains else "medium",
                    "evidence": [
                        f"On {d}: trade net {_fmt_inr(trade_net)}, cashflow net 0 (missing_cashflow).",
                        f"No cashflow within ±{club_days} days is near {_fmt_inr(expected)}.",
                        (
                            f"Day gap {_fmt_inr(day_diff)} matches the lifetime gap {_fmt_inr(gap)}."
                            if explains
                            else f"Day gap {_fmt_inr(day_diff)}; lifetime gap {_fmt_inr(gap)}."
                        ),
                        f"Trade ids: {', '.join(str(i) for i in tx_ids) or 'n/a'}.",
                    ],
                }
                root_cause = (
                    f"BUY/SELL trades on {d} imply cashflow {_fmt_inr(expected)}, but no cashflow row exists "
                    f"on that date and nothing within ±{club_days} days is near that amount. "
                    "The mismatch source is the cashflow side (note not entered), not opening book."
                )

            check_exactly = [
                {
                    "n": 1,
                    "title": f"Confirm the trades on {d}",
                    "detail": (
                        "Open each trade below and verify it is a real BUY/SELL that should move cash. "
                        f"Combined CF effect should be about {_fmt_inr(expected)}. "
                        + ("Trades: " + "; ".join(trade_lines[:6]) if trade_lines else f"Ids: {tx_ids}")
                    ),
                    "url": f"/transactions?client_id={client_id}",
                    "links": trade_links,
                },
                {
                    "n": 2,
                    "title": f"Search cashflows {win_start} → {win_end} for ~{_fmt_inr(expected)}",
                    "detail": (
                        "Look for any advisor note near this amount (month aggregates count). "
                        + (
                            "Candidates already found — open them first: "
                            + ", ".join(f"#{c['id']}@{c['date']}" for c in nearby_cfs[:5])
                            if nearby_cfs
                            else "No near-amount candidates found in the window — the note is likely missing."
                        )
                    ),
                    "url": f"/cashflows?client_id={client_id}&start_date={win_start}&end_date={win_end}",
                    "links": [
                        {"label": f"CF #{c['id']} ({c['date']})", "url": c.get("url")}
                        for c in nearby_cfs[:5]
                        if c.get("url")
                    ],
                },
                {
                    "n": 3,
                    "title": "Decide the fix from what you see",
                    "detail": (
                        "If trades are correct and no covering cashflow exists → add cashflow "
                        f"~{_fmt_inr(expected)} (upload or delete+re-add). "
                        "If a nearby cashflow is the same money with wrong date/amount → correct that row only "
                        "(do not double-count). "
                        "If a trade should not exist → edit/remove that trade last (may sync cashflows)."
                    ),
                    "url": f"/cashflows?client_id={client_id}&start_date={win_start}&end_date={win_end}",
                },
                {
                    "n": 4,
                    "title": "Re-check this page",
                    "detail": "Return here and click Re-check. Status should move toward matched if the gap is closed.",
                    "url": f"/clients/{client_id}/cashflow-trade-integrity",
                },
            ]

            fix_options = [
                {
                    "priority": 1,
                    "title": (
                        f"Correct the nearby cashflow candidate(s)"
                        if near_exact or near_loose
                        else f"Add the missing advisor cashflow (~{_fmt_inr(expected)})"
                    ),
                    "why": source["label"],
                    "how": check_exactly[2]["detail"],
                    "url": f"/cashflows?client_id={client_id}&start_date={win_start}&end_date={win_end}",
                    "url_label": "Open cashflows (±14d)",
                },
                {
                    "priority": 2,
                    "title": "Verify each listed trade is intentional",
                    "why": "If a BUY/SELL is erroneous, fixing cashflows alone will recreate the gap.",
                    "how": "; ".join(trade_lines[:6]) or f"Review ids {tx_ids}",
                    "url": f"/transactions?client_id={client_id}",
                    "url_label": "Open trades",
                    "trade_links": trade_links,
                },
                {
                    "priority": 3,
                    "title": "Only then edit/remove wrong trades",
                    "why": "Trade edits may sync cashflows — prefer cashflow correction first.",
                    "how": f"Edit links for: {', '.join(str(i) for i in tx_ids)}",
                    "url": f"/transactions?client_id={client_id}",
                    "url_label": "Open trades",
                    "trade_links": trade_links,
                },
            ]

        elif issue == "orphan_cashflow":
            source = {
                "code": "orphan_advisor_cashflow",
                "label": "Source: advisor cashflow without matching trades in window",
                "confidence": "high" if explains else "medium",
                "evidence": [
                    f"On {d}: cashflow net {_fmt_inr(cf_net)}, trade net 0.",
                    f"No BUY/SELL bundle within ±{club_days} days matched amount {_fmt_inr(cf_net)}.",
                    f"Cashflow ids: {', '.join(str(i) for i in cf_ids) or 'n/a'}.",
                ],
            }
            root_cause = (
                f"Cashflow(s) on {d} ({_fmt_inr(cf_net)}) have no matching BUY/SELL on that date or in the "
                f"±{club_days}d clubbing window with a similar amount. Source is either an extra/wrong cashflow "
                "note, or trades missing / dated too far away."
            )
            check_exactly = [
                {
                    "n": 1,
                    "title": f"Open cashflow(s) on {d}",
                    "detail": (
                        f"Ids {', '.join(str(i) for i in cf_ids)}. Confirm amount {_fmt_inr(cf_net)} and whether "
                        "this is a real advisor note or a duplicate."
                    ),
                    "url": f"/cashflows?client_id={client_id}&start_date={d}&end_date={d}",
                },
                {
                    "n": 2,
                    "title": f"Find trades {win_start} → {win_end} totaling ~{_fmt_inr(cf_net)}",
                    "detail": (
                        "If trades exist outside the window, move the cashflow date closer (delete+re-add) "
                        "or accept clubbing cannot clear until dates/amounts align."
                    ),
                    "url": f"/transactions?client_id={client_id}",
                },
                {
                    "n": 3,
                    "title": "Fix the wrong side only",
                    "detail": (
                        "Duplicate/wrong note → remove/re-enter cashflow. Missing trades with proof in holdings → "
                        "add BUY/SELL. Do not invent both."
                    ),
                    "url": f"/cashflows?client_id={client_id}&start_date={d}&end_date={d}",
                },
                {
                    "n": 4,
                    "title": "Re-check this page",
                    "detail": "Click Re-check after edits.",
                    "url": f"/clients/{client_id}/cashflow-trade-integrity",
                },
            ]
            fix_options = [
                {
                    "priority": 1,
                    "title": "Find trades this cashflow was meant to cover",
                    "why": source["label"],
                    "how": check_exactly[1]["detail"],
                    "url": f"/transactions?client_id={client_id}",
                    "url_label": "Open trades",
                },
                {
                    "priority": 2,
                    "title": "Correct or remove duplicate / wrong cashflow",
                    "why": "Extra notes keep the lifetime gap open.",
                    "how": check_exactly[0]["detail"],
                    "url": f"/cashflows?client_id={client_id}&start_date={d}&end_date={d}",
                    "url_label": "Open cashflows that day",
                },
            ]

        else:  # amount_mismatch
            source = {
                "code": "same_day_amount_mismatch",
                "label": "Source: same-day cashflow and trades disagree on amount",
                "confidence": "high",
                "evidence": [
                    f"On {d}: CF {_fmt_inr(cf_net)} vs trades {_fmt_inr(trade_net)} (Δ {_fmt_inr(day_diff)}).",
                    f"Cashflow ids: {', '.join(str(i) for i in cf_ids) or 'n/a'}.",
                    f"Trade ids: {', '.join(str(i) for i in tx_ids) or 'n/a'}.",
                ],
            }
            root_cause = (
                f"Both cashflows and trades exist on {d}, but amounts do not align "
                f"(Δ {_fmt_inr(day_diff)}). Source is a mistyped cashflow amount and/or an extra/missing trade "
                "on that day — not a pure missing-note case."
            )
            check_exactly = [
                {
                    "n": 1,
                    "title": f"Line up cashflows vs trades on {d}",
                    "detail": (
                        f"CF ids {cf_ids} total {_fmt_inr(cf_net)}; "
                        f"trades {'; '.join(trade_lines[:5]) or tx_ids} total {_fmt_inr(trade_net)}."
                    ),
                    "url": f"/cashflows?client_id={client_id}&start_date={d}&end_date={d}",
                    "links": trade_links,
                },
                {
                    "n": 2,
                    "title": f"Also scan ±{club_days}d for split notes",
                    "detail": (
                        f"Window {win_start} → {win_end}. Part of the amount may sit on a neighbour date."
                    ),
                    "url": f"/cashflows?client_id={client_id}&start_date={win_start}&end_date={win_end}",
                },
                {
                    "n": 3,
                    "title": "Correct the wrong amount (usually the cashflow note)",
                    "detail": (
                        f"Prefer adjusting cashflow to trade-implied {_fmt_inr(trade_net)} unless the advisor note "
                        "is known-correct and a trade is wrong."
                    ),
                    "url": f"/cashflows?client_id={client_id}&start_date={d}&end_date={d}",
                },
                {
                    "n": 4,
                    "title": "Re-check this page",
                    "detail": "Click Re-check after edits.",
                    "url": f"/clients/{client_id}/cashflow-trade-integrity",
                },
            ]
            fix_options = [
                {
                    "priority": 1,
                    "title": "Reconcile which side is wrong for this day",
                    "why": source["label"],
                    "how": check_exactly[0]["detail"],
                    "url": f"/cashflows?client_id={client_id}&start_date={d}&end_date={d}",
                    "url_label": "Open that day’s cashflows",
                    "trade_links": trade_links,
                },
                {
                    "priority": 2,
                    "title": "Check ±14d for split notes vs clubbed trades",
                    "why": "Part of the money may sit on a neighbouring date.",
                    "how": check_exactly[1]["detail"],
                    "url": f"/cashflows?client_id={client_id}&start_date={win_start}&end_date={win_end}",
                    "url_label": "Open ±14d cashflows",
                },
            ]

        if status in ("mismatch_material", "mismatch_review"):
            fix_options.append(
                {
                    "priority": 9,
                    "title": "Optional: Accept residual as non-material",
                    "why": (
                        "Only if you judge the leftover gap would not meaningfully change overall XIRR. "
                        "Audited note only — does not change cashflows or trades."
                    ),
                    "how": "Use Accept residual on this page after completing the checks above.",
                    "url": None,
                    "url_label": None,
                }
            )
    else:
        root_cause = (
            "Lifetime nets disagree beyond the band, but no large day deviation was isolated after clubbing."
        )
        check_exactly = [
            {
                "n": 1,
                "title": "Compare lifetime cashflows to post-cutoff trades",
                "detail": "Reconcile month notes to BUY/SELL totals after 2000-01-01.",
                "url": f"/cashflows?client_id={client_id}",
            },
            {
                "n": 2,
                "title": "Re-check after any edit",
                "detail": "Click Re-check on this page.",
                "url": f"/clients/{client_id}/cashflow-trade-integrity",
            },
        ]
        fix_options = [
            {
                "priority": 1,
                "title": "Compare lifetime cashflows to post-cutoff trades",
                "why": source["label"],
                "how": check_exactly[0]["detail"],
                "url": f"/cashflows?client_id={client_id}",
                "url_label": "Open cashflows",
            },
            {
                "priority": 9,
                "title": "Optional: Accept residual as non-material",
                "why": "Audited acknowledgment only if XIRR impact is negligible.",
                "how": "Use Accept residual on this page.",
                "url": None,
                "url_label": None,
            },
        ]

    fix_options.sort(key=lambda o: int(o.get("priority") or 99))
    primary = next((o for o in fix_options if o.get("priority", 99) < 9), None)

    # Prepend chronological / opening-book framing to exact checks
    preface: List[Dict[str, Any]] = []
    if has_opening_book:
        preface.append(
            {
                "n": 0,
                "title": "Opening book — do not day-match pre-first-real cashflows",
                "detail": (
                    f"Trades on {SKIP_TRADE_DATE.isoformat()} are opening positions. "
                    f"Cashflows before first real trade"
                    f"{(' (' + reconcile_start_date + ')') if reconcile_start_date else ''} "
                    "funded that book and are excluded from orphan/missing day review. "
                    "Focus reconcile on/after the first real trade date only."
                ),
                "url": (
                    f"/transactions?client_id={client_id}"
                    if not reconcile_start_date
                    else f"/transactions?client_id={client_id}&start_date={reconcile_start_date}"
                ),
            }
        )
    if chrono.get("matched_through_date") or chrono.get("first_break_date"):
        preface.append(
            {
                "n": 0,
                "title": "Trust the healthy prefix; focus after the break",
                "detail": (
                    f"Post-cutoff series matched through {chrono.get('matched_through_date') or '—'}; "
                    f"first break {chrono.get('first_break_date') or '—'}. "
                    "Ignore corporate actions for this reconcile — they change quantities, not advisor cashflows."
                ),
                "url": f"/cashflows?client_id={client_id}",
            }
        )
    if preface:
        for i, p in enumerate(preface):
            p["n"] = i
        check_exactly = preface + [
            {**chk, "n": int(chk.get("n") or 0) + len(preface)}
            for chk in check_exactly
        ]

    return {
        "headline": "Source of mismatch — what to check exactly",
        "finding": " ".join(finding_lines),
        "source": source,
        "check_exactly": check_exactly,
        "root_cause": root_cause,
        "explains_lifetime_gap": explains,
        "focus_day": focus_day_pack,
        "chronological": chrono,
        "fix_options": fix_options,
        "primary_action": {
            "label": (primary or {}).get("title") or "Re-check after edits",
            "url": (primary or {}).get("url") or f"/clients/{client_id}/cashflow-trade-integrity",
        },
        "after_fix": "After you edit cashflows or trades yourself, return here and click Re-check. The system never auto-fixes.",
    }


def _build_checklist(
    status: str,
    client_id: int,
    *,
    top_suspect: Optional[Dict[str, Any]] = None,
    large_day_count: int = 0,
    resolution: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    resolution = resolution or {}
    if status in ("matched", "ignore_opening_book", "accepted_non_material"):
        steps.append(
            {
                "step_id": "explain_status",
                "title": f"Status: {status}",
                "hint": (
                    "Lifetime net and cashflow↔trade series align within band "
                    "(clubbing needs nearby dates and similar amounts). "
                    "Small leftover day hints should not move overall XIRR."
                ),
            }
        )
        if status == "ignore_opening_book":
            steps.append(
                {
                    "step_id": "opening_book_ok",
                    "title": "Opening book (2000-01-01) excluded from post-cutoff check",
                    "hint": "Ask below if you have questions.",
                }
            )
        steps.append(
            {
                "step_id": "review_informational_days",
                "title": "Optional: skim smaller day hints",
                "hint": "No large series gaps; XIRR impact from remaining hints is treated as negligible.",
                "url": f"/cashflows?client_id={client_id}",
            }
        )
        return steps

    src = resolution.get("source") or {}
    if src.get("label"):
        steps.append(
            {
                "step_id": "source",
                "title": src.get("label"),
                "hint": " · ".join(src.get("evidence") or [])[:500],
            }
        )
    for chk in (resolution.get("check_exactly") or [])[:5]:
        steps.append(
            {
                "step_id": f"check_{chk.get('n')}",
                "title": f"Check {chk.get('n')}: {chk.get('title')}",
                "hint": chk.get("detail") or "",
                "url": chk.get("url"),
            }
        )
    if not resolution.get("check_exactly"):
        if resolution.get("finding"):
            steps.append(
                {
                    "step_id": "finding",
                    "title": "What we found",
                    "hint": resolution.get("finding"),
                }
            )
        if top_suspect:
            d = top_suspect.get("date")
            steps.append(
                {
                    "step_id": "review_suspect_day",
                    "title": f"Review suspect day {d} ({top_suspect.get('issue')})",
                    "hint": top_suspect.get("guess") or "",
                    "url": (
                        f"/cashflows?client_id={client_id}&start_date={d}&end_date={d}"
                        if d
                        else f"/cashflows?client_id={client_id}"
                    ),
                }
            )

    steps.append(
        {
            "step_id": "rerun_diagnose",
            "title": "Return and re-check",
            "hint": resolution.get("after_fix")
            or "Click Re-check on the integrity page (no auto-fix).",
        }
    )
    if status in ("mismatch_material", "mismatch_review"):
        steps.append(
            {
                "step_id": "accept_residual",
                "title": "Optional: Accept residual as non-material",
                "hint": "Only if leftover gap would not meaningfully change overall XIRR (audited note only).",
            }
        )
    return steps


def _club_pair_for_day(
    d: date,
    issue: str,
    cf_by_date: Dict[date, List[Any]],
    tx_by_date: Dict[date, List[Any]],
    day_tolerance: float,
    club_days: int = CLUB_DAYS,
    settlement_days: int = SETTLEMENT_DAYS,
) -> Optional[Dict[str, Any]]:
    """
    Explain orphan/missing: nearest ±settlement_days counterpart first, then
    sum of opposite activity across ±club_days. Amounts must agree — e.g. ₹10L
    CF vs ₹1L nearby trades is NOT clubbing.
    """
    window_dates = [d + timedelta(days=offset) for offset in range(-club_days, club_days + 1)]

    if issue == "orphan_cashflow":
        day_cfs = cf_by_date.get(d, [])
        cf_net = net_cashflow_from_records(day_cfs)
        if abs(cf_net) < 1:
            return None
        near_offsets = sorted(
            range(-settlement_days, settlement_days + 1),
            key=lambda o: (abs(o), o),
        )
        for offset in near_offsets:
            if offset == 0:
                continue
            wd = d + timedelta(days=offset)
            day_txs = tx_by_date.get(wd) or []
            if not day_txs:
                continue
            trade_net = net_cashflow_from_trades(day_txs)
            if _within_amount(cf_net, trade_net, nearby_amount_tolerance(cf_net, trade_net)):
                return {
                    "cashflow_date": d.isoformat(),
                    "trade_dates": [wd.isoformat()],
                    "trade_date": wd.isoformat(),
                    "issue": issue,
                    "cashflow_net": round(cf_net, 2),
                    "trade_net": round(trade_net, 2),
                    "trade_count": len(day_txs),
                    "cashflow_ids": [cf.id for cf in day_cfs[:8]],
                    "transaction_ids": [tx.id for tx in day_txs[:12]],
                    "summary": (
                        f"Cashflow on {d.isoformat()} ({cf_net:,.0f}) is explained by "
                        f"{len(day_txs)} trade(s) on {wd.isoformat()} "
                        f"({trade_net:,.0f}) within ±{settlement_days} days — amounts align."
                    ),
                }
        window_txs = []
        for wd in window_dates:
            if wd == d:
                continue
            window_txs.extend(tx_by_date.get(wd) or [])
        if not window_txs:
            return None
        trade_net = net_cashflow_from_trades(window_txs)
        amt_ok = _within_amount(
            trade_net,
            cf_net,
            max(float(day_tolerance), nearby_amount_tolerance(cf_net, trade_net)),
        )
        if not amt_ok:
            return None
        trade_dates = sorted(
            {
                wd.isoformat()
                for wd in window_dates
                if wd != d and (tx_by_date.get(wd) or [])
            }
        )
        return {
            "cashflow_date": d.isoformat(),
            "trade_dates": trade_dates,
            "trade_date": trade_dates[0] if trade_dates else None,
            "issue": issue,
            "cashflow_net": round(cf_net, 2),
            "trade_net": round(trade_net, 2),
            "trade_count": len(window_txs),
            "cashflow_ids": [cf.id for cf in day_cfs[:8]],
            "transaction_ids": [tx.id for tx in window_txs[:12]],
            "summary": (
                f"Cashflow on {d.isoformat()} ({cf_net:,.0f}) is explained by "
                f"{len(window_txs)} trade(s) across {', '.join(trade_dates[:5])}"
                f"{'…' if len(trade_dates) > 5 else ''} "
                f"(combined {trade_net:,.0f}) within ±{club_days} days — amounts align."
            ),
        }

    if issue == "missing_cashflow":
        day_txs = tx_by_date.get(d, [])
        trade_net = net_cashflow_from_trades(day_txs)
        if abs(trade_net) < 1:
            return None
        near_offsets = sorted(
            range(-settlement_days, settlement_days + 1),
            key=lambda o: (abs(o), o),
        )
        for offset in near_offsets:
            if offset == 0:
                continue
            wd = d + timedelta(days=offset)
            day_cfs = cf_by_date.get(wd) or []
            if not day_cfs:
                continue
            cf_net = net_cashflow_from_records(day_cfs)
            if _within_amount(cf_net, trade_net, nearby_amount_tolerance(cf_net, trade_net)):
                return {
                    "cashflow_date": wd.isoformat(),
                    "cashflow_dates": [wd.isoformat()],
                    "trade_date": d.isoformat(),
                    "issue": issue,
                    "cashflow_net": round(cf_net, 2),
                    "trade_net": round(trade_net, 2),
                    "trade_count": len(day_txs),
                    "cashflow_ids": [cf.id for cf in day_cfs[:8]],
                    "transaction_ids": [tx.id for tx in day_txs[:12]],
                    "summary": (
                        f"{len(day_txs)} trade(s) on {d.isoformat()} ({trade_net:,.0f}) "
                        f"are explained by cashflow on {wd.isoformat()} "
                        f"({cf_net:,.0f}) within ±{settlement_days} days — amounts align."
                    ),
                }
        window_cfs = []
        for wd in window_dates:
            if wd == d:
                continue
            window_cfs.extend(cf_by_date.get(wd) or [])
        if not window_cfs:
            return None
        cf_net = net_cashflow_from_records(window_cfs)
        amt_ok = _within_amount(
            cf_net,
            trade_net,
            max(float(day_tolerance), nearby_amount_tolerance(trade_net, cf_net)),
        )
        if not amt_ok:
            return None
        cf_dates = sorted(
            {
                wd.isoformat()
                for wd in window_dates
                if wd != d and (cf_by_date.get(wd) or [])
            }
        )
        return {
            "cashflow_date": cf_dates[0] if cf_dates else None,
            "cashflow_dates": cf_dates,
            "trade_date": d.isoformat(),
            "issue": issue,
            "cashflow_net": round(cf_net, 2),
            "trade_net": round(trade_net, 2),
            "trade_count": len(day_txs),
            "cashflow_ids": [cf.id for cf in window_cfs[:8]],
            "transaction_ids": [tx.id for tx in day_txs[:12]],
            "summary": (
                f"{len(day_txs)} trade(s) on {d.isoformat()} ({trade_net:,.0f}) "
                f"are explained by cashflow(s) on {', '.join(cf_dates[:5])} "
                f"(combined {cf_net:,.0f}) within ±{club_days} days — amounts align."
            ),
        }
    return None


CA_WINDOW_DAYS = 7  # unused; corporate actions do not affect cashflow↔trade nets


def _clubbing_adjusted_daily_nets(
    cf_by_date: Dict[date, List[Any]],
    tx_by_date: Dict[date, List[Any]],
    clubbed_explanations: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Dict[date, float], Dict[date, float]]:
    """
    Daily CF/trade nets with clubbed pairs collapsed onto the primary date so
    chronological cumulative matching agrees with ±14d clubbing.
    """
    day_cf: Dict[date, float] = {
        d: net_cashflow_from_records(rows) for d, rows in cf_by_date.items()
    }
    day_tx: Dict[date, float] = {
        d: net_cashflow_from_trades(rows) for d, rows in tx_by_date.items()
    }

    def _move_net(src_days: List[date], dest: date, amount: float, bucket: Dict[date, float]) -> None:
        if not src_days or abs(amount) < 1e-9:
            return
        total = sum(bucket.get(d, 0.0) for d in src_days)
        if abs(total) < 1e-9:
            # Counterpart days already empty — still place amount on dest
            bucket[dest] = bucket.get(dest, 0.0) + amount
            return
        for d in src_days:
            share = bucket.get(d, 0.0) / total * amount
            bucket[d] = bucket.get(d, 0.0) - share
        bucket[dest] = bucket.get(dest, 0.0) + amount

    for pair in clubbed_explanations or []:
        issue = pair.get("issue")
        try:
            if issue == "orphan_cashflow" and pair.get("cashflow_date"):
                cd = date.fromisoformat(str(pair["cashflow_date"]))
                tnet = float(pair.get("trade_net") or 0)
                src = [
                    date.fromisoformat(str(td_s))
                    for td_s in (pair.get("trade_dates") or [])
                ]
                if not src and pair.get("trade_date"):
                    src = [date.fromisoformat(str(pair["trade_date"]))]
                _move_net(src, cd, tnet, day_tx)
            elif issue == "missing_cashflow" and pair.get("trade_date"):
                td = date.fromisoformat(str(pair["trade_date"]))
                cnet = float(pair.get("cashflow_net") or 0)
                cf_dates = list(pair.get("cashflow_dates") or [])
                if not cf_dates and pair.get("cashflow_date"):
                    cf_dates = [pair["cashflow_date"]]
                src = [date.fromisoformat(str(cd_s)) for cd_s in cf_dates]
                _move_net(src, td, cnet, day_cf)
        except Exception:
            continue
    return day_cf, day_tx


def build_chronological_reconcile(
    cf_by_date: Dict[date, List[Any]],
    tx_by_date: Dict[date, List[Any]],
    clubbed_explanations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Walk dates from **most recent → older**; grow a suffix while cumulative
    CF vs trade gap stays within the materiality band.

    ``matched_from_recent_date`` / ``matched_through_date`` = oldest date still in
    that good suffix (i.e. from that date through today is within band).
    Older history before that point is where breaks / bad apples live.

    Clubbed pairs are collapsed onto the primary date first so temporary
    date-skew within ±14d does not create a false break.

    Note: corporate actions change quantities, not advisor cashflows — unused here.
    """
    day_cf, day_tx = _clubbing_adjusted_daily_nets(
        cf_by_date, tx_by_date, clubbed_explanations
    )
    all_dates = sorted(set(day_cf) | set(day_tx) | set(cf_by_date) | set(tx_by_date))
    suffix_cf = 0.0
    suffix_tx = 0.0
    matched_from: Optional[date] = None
    matched_from_cf = 0.0
    matched_from_tx = 0.0
    first_break_older: Optional[date] = None
    series_newest_first: List[Dict[str, Any]] = []

    for d in reversed(all_dates):
        d_cf = float(day_cf.get(d, 0.0))
        d_tx = float(day_tx.get(d, 0.0))
        suffix_cf += d_cf
        suffix_tx += d_tx
        gap = suffix_cf - suffix_tx
        band = materiality_threshold(max(abs(suffix_cf), abs(suffix_tx), 1.0))
        ok = abs(gap) <= band
        series_newest_first.append(
            {
                "date": d.isoformat(),
                "day_cashflow_net": round(d_cf, 2),
                "day_trade_net": round(d_tx, 2),
                "suffix_cashflow_net": round(suffix_cf, 2),
                "suffix_trade_net": round(suffix_tx, 2),
                "suffix_gap": round(gap, 2),
                # Compat aliases used by older UI/tests
                "cum_cashflow_net": round(suffix_cf, 2),
                "cum_trade_net": round(suffix_tx, 2),
                "cum_gap": round(gap, 2),
                "band": round(band, 2),
                "matched": ok,
            }
        )
        if ok:
            matched_from = d
            matched_from_cf = suffix_cf
            matched_from_tx = suffix_tx
        else:
            first_break_older = d
            break

    series_oldest_first = list(reversed(series_newest_first))
    fully_matched = bool(all_dates) and matched_from is not None and matched_from == all_dates[0]
    # Forward-compat: first_break_date = oldest day that fell outside the recent-matched suffix
    first_break = first_break_older
    if first_break is None and matched_from is None and all_dates:
        first_break = all_dates[-1]

    return {
        "scan_direction": "from_recent",
        "matched_from_recent_date": matched_from.isoformat() if matched_from else None,
        # Alias: "matched through DATE" when scanning from recent = oldest good suffix date
        "matched_through_date": matched_from.isoformat() if matched_from else None,
        "first_break_date": first_break.isoformat() if first_break else None,
        "matched_through_cashflow_net": round(matched_from_cf, 2),
        "matched_through_trade_net": round(matched_from_tx, 2),
        "final_cum_cashflow_net": round(suffix_cf, 2) if matched_from else round(
            sum(float(day_cf.get(d, 0.0)) for d in all_dates), 2
        ),
        "final_cum_trade_net": round(suffix_tx, 2) if matched_from else round(
            sum(float(day_tx.get(d, 0.0)) for d in all_dates), 2
        ),
        "final_cum_gap": round(
            (matched_from_cf - matched_from_tx)
            if matched_from
            else (
                sum(float(day_cf.get(d, 0.0)) for d in all_dates)
                - sum(float(day_tx.get(d, 0.0)) for d in all_dates)
            ),
            2,
        ),
        "fully_matched": fully_matched,
        "days_scanned": len(all_dates),
        "series_sample": _chrono_series_sample(series_oldest_first, first_break),
        "clubbing_applied": bool(clubbed_explanations),
    }


def _chrono_series_sample(
    series: List[Dict[str, Any]], first_break: Optional[date]
) -> List[Dict[str, Any]]:
    if not series:
        return []
    if first_break is None:
        return series[-8:]
    break_s = first_break.isoformat()
    idx = next((i for i, r in enumerate(series) if r["date"] == break_s), len(series) // 2)
    start = max(0, idx - 3)
    end = min(len(series), idx + 8)
    return series[start:end]


def partition_bad_apples(
    large_days: List[Dict[str, Any]],
    *,
    matched_through_date: Optional[str] = None,
    matched_from_recent_date: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    After a from-recent scan: the healthy suffix is on/after matched_from.
    Bad apples = large unexplained days **before** that boundary.
    """
    boundary = matched_from_recent_date or matched_through_date
    if not boundary:
        return [], list(large_days)
    healthy: List[Dict[str, Any]] = []
    bad: List[Dict[str, Any]] = []
    for row in large_days:
        d = str(row.get("date") or "")
        if d and d >= boundary:
            healthy.append(row)
        else:
            bad.append(row)
    return healthy, bad


def _partition_day_deviations(
    suspect_days: List[Dict[str, Any]],
    *,
    lifetime_threshold: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Split remaining day rows into large (review) vs small (hint).

    Large = |day_diff| above the lifetime materiality band (same 1%/₹1k idea applied
    to the day gap). A ₹10L same-day miss is never “small” just because totals cancel.
    """
    large: List[Dict[str, Any]] = []
    small: List[Dict[str, Any]] = []
    thresh = max(float(lifetime_threshold or 0), MATERIALITY_FLOOR)
    for row in suspect_days:
        day_abs = abs(float(row.get("day_difference") or 0))
        # Also treat relative to that day's cashflow/trade scale when present
        day_scale = max(
            abs(float(row.get("cashflow_net") or 0)),
            abs(float(row.get("trade_net") or 0)),
            day_abs,
        )
        day_thresh = max(thresh, materiality_threshold(day_scale))
        annotated = dict(row)
        annotated["day_amount_threshold"] = round(day_thresh, 2)
        annotated["amount_in_band"] = day_abs <= day_thresh
        if day_abs > day_thresh:
            annotated["severity"] = "review"
            large.append(annotated)
        else:
            annotated["severity"] = "hint"
            small.append(annotated)
    return large, small


def build_match_explanation(
    *,
    status: str,
    recorded_net: float,
    post_cutoff_trade_net: float,
    difference: float,
    materiality_threshold: float,
    clubbed_explanations: Optional[List[Dict[str, Any]]] = None,
    suspect_day_count: int = 0,
    large_day_count: int = 0,
    small_day_count: int = 0,
    club_days: int = CLUB_DAYS,
    chronological: Optional[Dict[str, Any]] = None,
) -> str:
    """Plain-language how we matched — net + series; chrono prefix; no CA cash impact."""
    abs_diff = abs(float(difference or 0))
    thresh = float(materiality_threshold or 0)
    clubbed = clubbed_explanations or []
    chrono = chronological or {}
    matched_thru = chrono.get("matched_through_date")
    first_break = chrono.get("first_break_date")

    chrono_line = ""
    matched_from = chrono.get("matched_from_recent_date") or matched_thru
    if matched_from:
        chrono_line = (
            f" Working from most recent activity back, CF↔trades stay within band "
            f"from {matched_from} through today "
            f"(CF {_fmt_inr(chrono.get('matched_through_cashflow_net') or 0)} / "
            f"trades {_fmt_inr(chrono.get('matched_through_trade_net') or 0)})."
        )
        if first_break and not chrono.get("fully_matched"):
            chrono_line += (
                f" History before {matched_from} is outside band "
                f"(break at {first_break}) — review those earlier days."
            )
    elif first_break:
        chrono_line = (
            f" Working from most recent activity back, the series is outside band "
            f"already at {first_break}."
        )

    if status in ("matched", "ignore_opening_book", "accepted_non_material"):
        lines = [
            f"Matched on both lifetime net and series: post-cutoff gap is {difference:,.0f} "
            f"(absolute {abs_diff:,.0f}), within the allowed band of {thresh:,.0f} "
            f"(max of 1% of |recorded cashflow| and ₹1,000), and no large unclubbed day/amount gaps remain.",
            f"Clubbing allows several trades within ±{club_days} days to explain one cashflow only when "
            "combined amounts also align (~1%/₹1k of that cashflow).",
        ]
        if chrono_line:
            lines.append(chrono_line.strip())
        if clubbed:
            lines.append(
                f"{len(clubbed)} day gap(s) were explained by clubbing with matching amounts."
            )
        if small_day_count:
            lines.append(
                f"{small_day_count} small day hint(s) remain; treated as not material to overall XIRR."
            )
        if status == "ignore_opening_book":
            lines.append(
                "Opening-book trades on 2000-01-01 are excluded from the post-cutoff check."
            )
        if status == "accepted_non_material":
            lines.append("You previously accepted a residual as non-material (audited note).")
        return " ".join(lines)
    if status == "mismatch_review":
        base = (
            f"Series review: lifetime gap {difference:,.0f} may be within/near the band "
            f"({thresh:,.0f}), but {large_day_count} bad-apple day(s) remain after clubbing."
            if large_day_count
            else (
                f"Borderline totals: gap {difference:,.0f} is near the band ({thresh:,.0f}). "
                "Review suspect days; clubbing requires both nearby dates and similar amounts."
            )
        )
        return (base + " " + chrono_line).strip()
    return (
        f"Material totals mismatch: gap {difference:,.0f} exceeds the allowed band ({thresh:,.0f}). "
        f"Working from most recent activity back, isolate days before the recent-matched window."
        + ((" " + chrono_line) if chrono_line else "")
    )


def _clubbed_orphan_or_missing(
    d: date,
    issue: str,
    cf_by_date: Dict[date, List[Any]],
    tx_by_date: Dict[date, List[Any]],
    day_tolerance: float,
    club_days: int = CLUB_DAYS,
) -> bool:
    """Backward-compat bool wrapper. """
    return (
        _club_pair_for_day(d, issue, cf_by_date, tx_by_date, day_tolerance, club_days)
        is not None
    )


def diagnose_client_mismatch(
    client_id: int,
    *,
    total_difference: Optional[float] = None,
    day_tolerance: float = 1.0,
    skip_date: date = SKIP_TRADE_DATE,
    club_days: int = CLUB_DAYS,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Date-level guess of which cashflows / trades drive a totals mismatch.

    Ignores skip_date trades. Same-day amount band, then ±settlement-day pairs,
    then ±club_days one↔sum before leftover orphan/missing flags.
    """
    cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
    trades = (
        Transaction.query.filter(
            Transaction.client_id == client_id,
            func.upper(Transaction.type).in_(["BUY", "SELL"]),
        )
        .order_by(Transaction.transaction_date)
        .all()
    )

    opening_trades = [t for t in trades if _as_date(t.transaction_date) == skip_date]
    non_skip_trades = [t for t in trades if _as_date(t.transaction_date) != skip_date]
    has_opening_book = bool(opening_trades)
    opening_reconcile = first_real_trade_date(trades, skip_date=skip_date) if has_opening_book else None
    notes_through, _day_after = _advisor_notes_reconcile_bounds(client_id)
    # Epoch for chrono / day-match: true first real trade only (never notes+1).
    epoch_start = opening_reconcile
    use_epoch = bool(has_opening_book or notes_through)

    if epoch_start is not None:
        post_trades = [
            t
            for t in non_skip_trades
            if (_as_date(t.transaction_date) or date.min) >= epoch_start
        ]
    else:
        post_trades = list(non_skip_trades)

    recorded_net = net_cashflow_from_records(cashflows)
    opening_net = net_cashflow_from_trades(opening_trades)
    pre_cfs, post_cfs = split_cashflows_at_reconcile_start(
        cashflows,
        reconcile_start=epoch_start,
        has_opening_book=bool(has_opening_book),
    )
    # Without opening book, all cashflows are in the live series.
    if not has_opening_book:
        pre_cfs, post_cfs = [], list(cashflows)

    pre_cf_net = net_cashflow_from_records(pre_cfs)
    post_opening_cf_net = net_cashflow_from_records(post_cfs)
    post_opening_trade_net = net_cashflow_from_trades(post_trades)

    cf_by_date: Dict[date, List[Any]] = {}
    for cf in post_cfs:
        d = _as_date(cf.date)
        if d is None:
            continue
        cf_by_date.setdefault(d, []).append(cf)

    tx_by_date: Dict[date, List[Any]] = {}
    for tx in post_trades:
        d = _as_date(tx.transaction_date)
        if d is None:
            continue
        tx_by_date.setdefault(d, []).append(tx)

    cf_nets = {
        d: net_cashflow_from_records(rows) for d, rows in cf_by_date.items()
    }
    tx_nets = {d: net_cashflow_from_trades(rows) for d, rows in tx_by_date.items()}
    clubbed_explanations, leftover_cf, leftover_tx = pair_day_nets(
        cf_nets,
        tx_nets,
        settlement_days=SETTLEMENT_DAYS,
        club_days=club_days,
    )

    suspect_days: List[Dict[str, Any]] = []
    suspect_cashflows: List[Dict[str, Any]] = []
    suspect_trades: List[Dict[str, Any]] = []

    leftover_dates = sorted(set(leftover_cf) | set(leftover_tx) | set(cf_by_date) | set(tx_by_date))
    for d in leftover_dates:
        cf_net = float(leftover_cf.get(d, 0.0) or 0.0)
        trade_net = float(leftover_tx.get(d, 0.0) or 0.0)
        day_diff = cf_net - trade_net
        day_band = max(
            float(day_tolerance),
            same_day_amount_tolerance(cf_net, trade_net),
        )
        if _within_amount(cf_net, trade_net, day_band):
            continue

        day_cfs = cf_by_date.get(d, [])
        day_txs = tx_by_date.get(d, [])
        if abs(trade_net) < 1 and abs(cf_net) >= 1:
            issue = "orphan_cashflow"
        elif abs(cf_net) < 1 and abs(trade_net) >= 1:
            issue = "missing_cashflow"
        else:
            issue = "amount_mismatch"

        day_row = {
            "client_id": client_id,
            "date": d.isoformat(),
            "issue": issue,
            "cashflow_net": round(cf_net, 2),
            "trade_net": round(trade_net, 2),
            "day_difference": round(day_diff, 2),
            "cashflow_ids": [cf.id for cf in day_cfs],
            "transaction_ids": [tx.id for tx in day_txs],
            "cashflow_count": len(day_cfs),
            "trade_count": len(day_txs),
            "guess": _guess_for_day(issue, day_diff, day_cfs, day_txs),
            "fact_id": f"suspect_day:{d.isoformat()}",
            "blocking": False,  # filled after status known
        }
        suspect_days.append(day_row)

        for cf in day_cfs:
            if issue in ("orphan_cashflow", "amount_mismatch"):
                signed_amt = canonical_cashflow_signed_amount(cf)
                suspect_cashflows.append(
                    {
                        "client_id": client_id,
                        "kind": "cashflow",
                        "issue": issue,
                        "date": d.isoformat(),
                        "id": cf.id,
                        "fact_id": f"cf:{cf.id}",
                        "amount": round(signed_amt, 2),
                        "stored_amount": round(float(cf.amount or 0), 2),
                        "type": getattr(cf, "type", None),
                        "description": (getattr(cf, "description", None) or "")[:200],
                        "day_difference": round(day_diff, 2),
                        "guess": day_row["guess"],
                        "url": f"/cashflows?client_id={client_id}&start_date={d.isoformat()}&end_date={d.isoformat()}",
                    }
                )

        for tx in day_txs:
            if issue in ("missing_cashflow", "amount_mismatch"):
                ttype = (tx.type or "").upper()
                if ttype == "BUY":
                    signed = -_trade_amount(tx)
                elif ttype == "SELL":
                    signed = _trade_amount(tx)
                else:
                    signed = 0.0
                suspect_trades.append(
                    {
                        "client_id": client_id,
                        "kind": "trade",
                        "issue": issue,
                        "date": d.isoformat(),
                        "id": tx.id,
                        "fact_id": f"tx:{tx.id}",
                        "amount": round(float(_trade_amount(tx)), 2),
                        "signed_cashflow_effect": round(signed, 2),
                        "type": tx.type,
                        "security_id": getattr(tx, "security_id", None),
                        "day_difference": round(day_diff, 2),
                        "guess": day_row["guess"],
                        "url": f"/transaction/{tx.id}/edit",
                    }
                )

    suspect_days.sort(key=lambda r: abs(r["day_difference"]), reverse=True)

    chrono = build_chronological_reconcile(
        cf_by_date, tx_by_date, clubbed_explanations=clubbed_explanations
    )
    trade_reconcile_from_s = chrono.get("matched_from_recent_date") or chrono.get(
        "matched_through_date"
    )
    trade_reconcile_from: Optional[date] = None
    if trade_reconcile_from_s:
        try:
            trade_reconcile_from = date.fromisoformat(str(trade_reconcile_from_s)[:10])
        except Exception:
            trade_reconcile_from = None

    # Nets for the *calculated* CF↔trade window (from-recent), not notes+1.
    if trade_reconcile_from is not None:
        recon_cfs = [
            cf
            for cf in post_cfs
            if (_as_date(cf.date) or date.min) >= trade_reconcile_from
        ]
        recon_txs = [
            tx
            for tx in post_trades
            if (_as_date(tx.transaction_date) or date.min) >= trade_reconcile_from
        ]
    else:
        recon_cfs, recon_txs = list(post_cfs), list(post_trades)

    post_cf_net = net_cashflow_from_records(recon_cfs)
    post_net = net_cashflow_from_trades(recon_txs)
    gap_now = float(post_cf_net - post_net)
    if total_difference is None:
        total_difference = gap_now

    opening_fr_iso = opening_reconcile.isoformat() if opening_reconcile else None
    reconcile_iso = (
        trade_reconcile_from.isoformat()
        if trade_reconcile_from is not None
        else (epoch_start.isoformat() if epoch_start else None)
    )

    # Advisor notes cover cashflows before the calculated trade-reconcile-from date?
    notes_cover_pre = False
    if notes_through and trade_reconcile_from is not None:
        notes_cover_pre = notes_through >= (trade_reconcile_from - timedelta(days=1))
    elif notes_through and chrono.get("fully_matched"):
        notes_cover_pre = True

    mat = materiality_threshold(
        post_cf_net if (has_opening_book or trade_reconcile_from or notes_through) else recorded_net
    )

    close_hits: List[Dict[str, Any]] = []
    gap = float(gap_now)
    if abs(gap) > 0:
        for cf in post_cfs:
            amt = canonical_cashflow_signed_amount(cf)
            if abs(abs(amt) - abs(gap)) <= max(1.0, abs(gap) * 0.02) or abs(amt - gap) <= max(
                1.0, abs(gap) * 0.02
            ):
                close_hits.append(
                    {
                        "kind": "cashflow",
                        "id": cf.id,
                        "fact_id": f"cf:{cf.id}",
                        "date": (_as_date(cf.date) or date.min).isoformat(),
                        "amount": round(amt, 2),
                        "relation": "amount_near_total_gap",
                    }
                )
        for d, day_txs in tx_by_date.items():
            day_net = net_cashflow_from_trades(day_txs)
            if abs(day_net + gap) <= max(1.0, abs(gap) * 0.02) or abs(day_net - gap) <= max(
                1.0, abs(gap) * 0.02
            ):
                if d not in cf_by_date or not _within_amount(
                    net_cashflow_from_records(cf_by_date.get(d, [])),
                    day_net,
                    max(float(day_tolerance), same_day_amount_tolerance(day_net)),
                ):
                    close_hits.append(
                        {
                            "kind": "trade_day",
                            "id": None,
                            "date": d.isoformat(),
                            "amount": round(day_net, 2),
                            "transaction_ids": [tx.id for tx in day_txs],
                            "relation": "trade_day_net_near_total_gap",
                        }
                    )

    explained = sum(r["day_difference"] for r in suspect_days)
    orphan_n = sum(1 for r in suspect_days if r["issue"] == "orphan_cashflow")
    missing_n = sum(1 for r in suspect_days if r["issue"] == "missing_cashflow")
    amt_n = sum(1 for r in suspect_days if r["issue"] == "amount_mismatch")
    clubbed_away = len(clubbed_explanations)

    parts = []
    if orphan_n:
        parts.append(f"{orphan_n} informational orphan cashflow day(s)")
    if missing_n:
        parts.append(f"{missing_n} informational missing cashflow day(s)")
    if amt_n:
        parts.append(f"{amt_n} informational date amount mismatch(es)")
    if clubbed_away:
        parts.append(f"{clubbed_away} day(s) explained by ±{club_days}d clubbing")
    if not parts:
        parts.append("no notable day-level gaps")

    top = suspect_days[0] if suspect_days else None
    top_note = ""
    if top:
        top_note = (
            f"; largest day hint: {top['date']} {top['issue']} "
            f"day_diff={top['day_difference']:,.2f}"
        )

    # Prefer freshly classified status from the calculated reconcile window.
    classified = classify_client_totals(
        recorded_net=recorded_net,
        opening_trade_net=opening_net,
        post_cutoff_trade_net=post_net,
        has_opening_book=bool(has_opening_book),
        materiality=mat,
        post_cutoff_cashflow_net=post_cf_net,
        opening_epoch_cashflow_net=pre_cf_net,
        first_real_trade_date_iso=opening_fr_iso,
    )
    resolved_status = status if status is not None else classified["status"]
    if status is None:
        if (
            resolved_status == "ignore_opening_book"
            and not has_opening_book
        ):
            resolved_status = "matched"
        # Recent window within band + notes cover older CF → overall matched for calcs
        if (
            trade_reconcile_from is not None
            and abs(gap_now) <= mat
            and (chrono.get("fully_matched") or notes_cover_pre)
        ):
            resolved_status = "ignore_opening_book" if has_opening_book else "matched"

    large_days, small_days = _partition_day_deviations(suspect_days, lifetime_threshold=mat)
    _healthy_suffix, bad_apples = partition_bad_apples(
        large_days,
        matched_from_recent_date=trade_reconcile_from_s,
        matched_through_date=trade_reconcile_from_s,
    )
    # Large days on/before approved notes through-date are covered by notes, not trade review
    if notes_through:
        notes_iso = notes_through.isoformat()
        bad_apples = [
            r for r in bad_apples if str(r.get("date") or "") > notes_iso
        ]

    if bad_apples:
        review_days = bad_apples
    elif chrono.get("fully_matched") or (
        trade_reconcile_from is not None and abs(gap_now) <= mat and notes_cover_pre
    ):
        review_days = []
    elif trade_reconcile_from_s:
        review_days = [
            r for r in large_days if str(r.get("date") or "") < str(trade_reconcile_from_s)
        ]
        if notes_through:
            notes_iso = notes_through.isoformat()
            review_days = [r for r in review_days if str(r.get("date") or "") > notes_iso]
    elif abs(gap_now) > mat:
        review_days = list(large_days)
    else:
        review_days = []

    # matched requires net + series; unexplained bad apples → review
    resolved_status = apply_series_match_gate(resolved_status, len(review_days))

    blocking = resolved_status in ("mismatch_material", "mismatch_review")
    for row in review_days:
        row["blocking"] = True
    for row in small_days:
        row["blocking"] = blocking

    match_explanation = build_match_explanation(
        status=resolved_status,
        recorded_net=recorded_net,
        post_cutoff_trade_net=post_net,
        difference=gap_now,
        materiality_threshold=mat,
        clubbed_explanations=clubbed_explanations,
        suspect_day_count=len(suspect_days),
        large_day_count=len(review_days),
        small_day_count=len(small_days),
        club_days=club_days,
        chronological=chrono,
    )

    focus = review_days[0] if review_days else (top if blocking else None)
    nearby_cf_candidates: List[Dict[str, Any]] = []
    if focus and focus.get("issue") == "missing_cashflow" and focus.get("date"):
        try:
            fd = date.fromisoformat(str(focus["date"]))
            nearby_cf_candidates = _nearby_cashflows_near_amount(
                cashflows,
                focus_d=fd,
                target_amount=float(focus.get("trade_net") or 0),
                club_days=club_days,
            )
            for c in nearby_cf_candidates:
                c["url"] = (
                    f"/cashflows?client_id={client_id}"
                    f"&start_date={c['date']}&end_date={c['date']}"
                )
        except Exception:
            nearby_cf_candidates = []
    resolution = build_resolution_guidance(
        client_id=client_id,
        status=resolved_status,
        difference=gap_now,
        materiality_threshold=mat,
        large_days=review_days,
        small_days=small_days,
        clubbed_explanations=clubbed_explanations,
        suspect_trades=suspect_trades,
        suspect_cashflows=suspect_cashflows,
        nearby_cashflow_candidates=nearby_cf_candidates,
        club_days=club_days,
        chronological=chrono,
        has_opening_book=has_opening_book,
        reconcile_start_date=reconcile_iso,
        opening_epoch_cashflow_net=pre_cf_net,
    )
    checklist = _build_checklist(
        resolved_status,
        client_id,
        top_suspect=focus,
        large_day_count=len(review_days),
        resolution=resolution,
    )

    return {
        "client_id": client_id,
        "status": resolved_status,
        "total_difference": round(gap_now, 2),
        "recorded_net_cashflow": round(recorded_net, 2),
        "post_cutoff_cashflow_net": round(post_cf_net, 2),
        "opening_epoch_cashflow_net": round(pre_cf_net, 2),
        "opening_trade_net": round(opening_net, 2),
        "post_cutoff_trade_net": round(post_net, 2),
        "materiality_threshold": round(mat, 2),
        "first_real_trade_date": opening_fr_iso,
        "opening_first_real_trade_date": opening_fr_iso,
        "trade_reconcile_from": trade_reconcile_from_s,
        "reconcile_start_date": reconcile_iso,
        "notes_cover_pre_reconcile": bool(notes_cover_pre),
        "has_opening_book": has_opening_book,
        "advisor_notes_through": notes_through.isoformat() if notes_through else None,
        "opening_epoch_cashflow_count": len(pre_cfs),
        "post_cutoff_cashflow_count": len(recon_cfs),
        "post_opening_cashflow_count": len(post_cfs),
        "suspect_day_count": len(suspect_days),
        "orphan_cashflow_days": orphan_n,
        "missing_cashflow_days": missing_n,
        "amount_mismatch_days": amt_n,
        "clubbed_away_days": clubbed_away,
        "explained_by_suspect_days": round(explained, 2),
        "guess_summary": "; ".join(parts) + top_note,
        "match_explanation": match_explanation,
        "clubbed_explanations": clubbed_explanations[:25],
        "large_day_deviations": review_days[:20],
        "small_day_deviations": small_days[:20],
        "chronological": chrono,
        "bad_apple_days": review_days[:20],
        "suspect_days": suspect_days,
        "suspect_cashflows": suspect_cashflows,
        "suspect_trades": suspect_trades,
        "close_to_total_gap": close_hits[:20],
        "resolution": resolution,
        "checklist": checklist,
        "skip_trade_date": skip_date.isoformat(),
        "club_days": club_days,
        "settlement_days": SETTLEMENT_DAYS,
        "has_large_day_review": bool(review_days),
        "series_matched": not bool(review_days),
    }


def _guess_for_day(issue: str, day_diff: float, day_cfs, day_txs) -> str:
    if issue == "orphan_cashflow":
        ids = ",".join(str(cf.id) for cf in day_cfs[:5])
        return (
            f"Cashflow(s) [{ids}] have no matching BUY/SELL on this date "
            f"(may be month aggregate or nearby within ±{SETTLEMENT_DAYS}d / ±{CLUB_DAYS}d)"
        )
    if issue == "missing_cashflow":
        ids = ",".join(str(tx.id) for tx in day_txs[:5])
        expected_cf = net_cashflow_from_trades(day_txs)
        return (
            f"Trade(s) [{ids}] have no cashflow row; "
            f"expected cashflow amount {expected_cf:,.2f}"
        )
    return (
        f"Cashflow net and trade net differ by {day_diff:,.2f} on this date — "
        f"check CF ids {[cf.id for cf in day_cfs[:5]]} vs trade ids {[tx.id for tx in day_txs[:5]]}"
    )


def compare_client_totals_vs_trades(
    client_id: int,
    *,
    tolerance: Optional[float] = None,
    skip_date: date = SKIP_TRADE_DATE,
    skip_client_ids: Optional[set[int]] = None,
) -> Dict[str, Any]:
    """
    Compare one client's recorded net cashflow vs post-cutoff trade-implied net.
    """
    if skip_client_ids is None:
        has_skip = (
            Transaction.query.filter(
                Transaction.client_id == client_id,
                func.date(Transaction.transaction_date) == skip_date,
            ).first()
            is not None
        )
    else:
        has_skip = client_id in skip_client_ids

    cashflows = Cashflow.query.filter_by(client_id=client_id).all()
    trades = Transaction.query.filter(
        Transaction.client_id == client_id,
        func.upper(Transaction.type).in_(["BUY", "SELL"]),
    ).all()

    opening = [t for t in trades if _as_date(t.transaction_date) == skip_date]
    non_skip = [t for t in trades if _as_date(t.transaction_date) != skip_date]
    has_opening = has_skip or bool(opening)
    opening_reconcile = first_real_trade_date(trades, skip_date=skip_date) if has_opening else None
    notes_through, _ = _advisor_notes_reconcile_bounds(client_id)
    # Totals classify on post–first-real window only (notes do not redefine first real).
    epoch_start = opening_reconcile
    if epoch_start is not None:
        post = [
            t
            for t in non_skip
            if (_as_date(t.transaction_date) or date.min) >= epoch_start
        ]
    else:
        post = list(non_skip)
    recorded_net = net_cashflow_from_records(cashflows)
    opening_net = net_cashflow_from_trades(opening)
    post_net = net_cashflow_from_trades(post)
    pre_cfs, post_cfs = split_cashflows_at_reconcile_start(
        cashflows,
        reconcile_start=epoch_start,
        has_opening_book=bool(has_opening),
    )
    if not has_opening:
        pre_cfs, post_cfs = [], list(cashflows)
    pre_cf_net = net_cashflow_from_records(pre_cfs)
    post_cf_net = net_cashflow_from_records(post_cfs)

    # Optional absolute override for CLI --tolerance (legacy); else %-floor on post window.
    scale = post_cf_net if has_opening else recorded_net
    mat = float(tolerance) if tolerance is not None else materiality_threshold(scale)
    classified = classify_client_totals(
        recorded_net=recorded_net,
        opening_trade_net=opening_net,
        post_cutoff_trade_net=post_net,
        has_opening_book=bool(has_opening),
        materiality=mat,
        post_cutoff_cashflow_net=post_cf_net,
        opening_epoch_cashflow_net=pre_cf_net,
        first_real_trade_date_iso=opening_reconcile.isoformat() if opening_reconcile else None,
    )
    classified.update(
        {
            "client_id": client_id,
            "tolerance": mat,
            "cashflow_count": len(cashflows),
            "buy_sell_count": len(trades),
            "advisor_notes_through": notes_through.isoformat() if notes_through else None,
            "has_opening_book_trades": bool(has_opening),
            "opening_first_real_trade_date": (
                opening_reconcile.isoformat() if opening_reconcile else None
            ),
            "skip_trade_date": skip_date.isoformat(),
        }
    )
    return classified


def build_totals_mismatch_report(
    *,
    client_ids: Optional[List[int]] = None,
    active_only: bool = True,
    tolerance: Optional[float] = None,
    skip_date: date = SKIP_TRADE_DATE,
    diagnose_mismatches: bool = True,
    material_only: bool = False,
) -> Dict[str, Any]:
    """
    Build the full report with epoch-aware statuses.

    - matched / ignore_opening_book
    - mismatch_material / mismatch_review (listed under mismatches)
    - skipped_dummy_trades list kept for backward compat = ignore_opening_book rows
    """
    q = Client.query
    if active_only and hasattr(Client, "is_active"):
        q = q.filter(Client.is_active.is_(True))
    if client_ids:
        q = q.filter(Client.id.in_(client_ids))
    clients = q.order_by(Client.name).all()
    id_list = [c.id for c in clients]
    name_by_id = {c.id: c.name for c in clients}

    empty = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "tolerance_mode": "pct_floor" if tolerance is None else "absolute",
        "tolerance": float(tolerance) if tolerance is not None else None,
        "materiality_pct": MATERIALITY_PCT,
        "materiality_floor": MATERIALITY_FLOOR,
        "skip_trade_date": skip_date.isoformat(),
        "active_only": active_only,
        "clients_considered": 0,
        "matched_count": 0,
        "mismatch_count": 0,
        "ignore_opening_book_count": 0,
        "skipped_dummy_count": 0,
        "matched": [],
        "mismatches": [],
        "ignore_opening_book": [],
        "skipped_dummy_trades": [],
        "mismatch_suspects": [],
    }
    if not id_list:
        return empty

    skip_ids = client_ids_with_skip_trade_date(skip_date)
    opening_nets, post_nets = _aggregate_trade_nets_split(id_list, skip_date=skip_date)
    opening_client_ids = set(skip_ids) | {cid for cid, n in opening_nets.items() if abs(n) > 0}
    first_real = _first_real_trade_dates_by_client(id_list, skip_date=skip_date)
    pre_cf_nets, post_cf_nets = _aggregate_cashflow_nets_split_by_reconcile_start(
        id_list,
        first_real_by_client=first_real,
        opening_client_ids=opening_client_ids,
    )
    cf_nets = _aggregate_cashflow_nets(id_list)
    counts = _aggregate_counts(id_list)

    considered_ids = set(opening_nets) | set(post_nets) | set(cf_nets)
    matched: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []
    ignore_opening: List[Dict[str, Any]] = []
    mismatch_suspects: List[Dict[str, Any]] = []

    for cid in considered_ids:
        recorded_net = float(cf_nets.get(cid, 0.0))
        opening_net = float(opening_nets.get(cid, 0.0))
        post_net = float(post_nets.get(cid, 0.0))
        has_skip = cid in opening_client_ids
        post_cf = float(post_cf_nets.get(cid, 0.0 if has_skip else recorded_net))
        pre_cf = float(pre_cf_nets.get(cid, 0.0))
        if not has_skip:
            post_cf = recorded_net
            pre_cf = 0.0
        fr = first_real.get(cid)
        scale = post_cf if has_skip else recorded_net
        mat = float(tolerance) if tolerance is not None else materiality_threshold(scale)
        classified = classify_client_totals(
            recorded_net=recorded_net,
            opening_trade_net=opening_net,
            post_cutoff_trade_net=post_net,
            has_opening_book=has_skip,
            materiality=mat,
            post_cutoff_cashflow_net=post_cf,
            opening_epoch_cashflow_net=pre_cf,
            first_real_trade_date_iso=fr.isoformat() if fr else None,
        )
        ccounts = counts.get(cid, {"cashflow_count": 0, "buy_sell_count": 0})
        row = {
            "client_id": cid,
            "client_name": name_by_id.get(cid, ""),
            **classified,
            "tolerance": mat,
            "cashflow_count": ccounts["cashflow_count"],
            "buy_sell_count": ccounts["buy_sell_count"],
            "skip_trade_date": skip_date.isoformat(),
            "guess_summary": "",
            "orphan_cashflow_days": 0,
            "missing_cashflow_days": 0,
            "amount_mismatch_days": 0,
            "explained_by_suspect_days": 0.0,
            "checklist": [],
        }

        status = row["status"]
        # Series gate needs diagnose even when lifetime net looks matched
        if diagnose_mismatches:
            diag = diagnose_client_mismatch(
                cid, total_difference=row["difference"], status=status, skip_date=skip_date
            )
            row["status"] = diag.get("status") or status
            row["guess_summary"] = diag.get("guess_summary") or ""
            row["orphan_cashflow_days"] = diag.get("orphan_cashflow_days", 0)
            row["missing_cashflow_days"] = diag.get("missing_cashflow_days", 0)
            row["amount_mismatch_days"] = diag.get("amount_mismatch_days", 0)
            row["explained_by_suspect_days"] = diag.get("explained_by_suspect_days", 0.0)
            row["checklist"] = diag.get("checklist") or []
            row["has_large_day_review"] = diag.get("has_large_day_review", False)
            row["series_matched"] = diag.get("series_matched", True)
            status = row["status"]
            if status in ("mismatch_material", "mismatch_review"):
                client_name = row["client_name"]
                for day in diag.get("large_day_deviations") or diag.get("suspect_days") or []:
                    mismatch_suspects.append(
                        {
                            "client_id": cid,
                            "client_name": client_name,
                            "total_difference": row["difference"],
                            "date": day["date"],
                            "issue": day["issue"],
                            "cashflow_net": day["cashflow_net"],
                            "trade_net": day["trade_net"],
                            "day_difference": day["day_difference"],
                            "cashflow_ids": ";".join(str(i) for i in (day.get("cashflow_ids") or [])),
                            "transaction_ids": ";".join(
                                str(i) for i in (day.get("transaction_ids") or [])
                            ),
                            "guess": day.get("guess"),
                        }
                    )

        if status == "matched":
            matched.append(row)
        elif status == "ignore_opening_book":
            ignore_opening.append(row)
        else:
            if material_only and status != "mismatch_material":
                continue
            mismatches.append(row)

    matched.sort(key=lambda r: (r.get("client_name") or "").lower())
    mismatches.sort(key=lambda r: r["abs_difference"], reverse=True)
    ignore_opening.sort(key=lambda r: (r.get("client_name") or "").lower())

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "tolerance_mode": "pct_floor" if tolerance is None else "absolute",
        "tolerance": float(tolerance) if tolerance is not None else None,
        "materiality_pct": MATERIALITY_PCT,
        "materiality_floor": MATERIALITY_FLOOR,
        "skip_trade_date": skip_date.isoformat(),
        "active_only": active_only,
        "clients_considered": len(considered_ids),
        "matched_count": len(matched),
        "mismatch_count": len(mismatches),
        "ignore_opening_book_count": len(ignore_opening),
        "skipped_dummy_count": len(ignore_opening),  # compat alias
        "matched": matched,
        "mismatches": mismatches,
        "ignore_opening_book": ignore_opening,
        "skipped_dummy_trades": ignore_opening,  # compat
        "mismatch_suspects": mismatch_suspects,
    }
