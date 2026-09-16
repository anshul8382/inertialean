"""
Session-first matching of recorded trades to sent recommendations.

Trades are assigned to at most one recommendation session. Executed recos
claim fills first so leftover sent lines cannot grade a later session's trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


CHECK_UNRECORDED_SUPERSEDED_SESSION = "unrecorded_superseded_session"


@dataclass
class RecoSnap:
    id: int
    session_id: Optional[int]
    security_id: int
    symbol: str
    action: str  # buy / sell / hold
    quantity: Optional[float]
    target_price: Optional[float]
    actual_price: Optional[float]
    status: str
    sent_at: Optional[datetime]
    executed_at: Optional[datetime]
    is_hold: bool = False
    session_name: Optional[str] = None

    @property
    def is_executable(self) -> bool:
        if self.is_hold:
            return False
        if self.quantity is None:
            return False
        return (self.action or "").strip().lower() in ("buy", "sell")

    @property
    def side(self) -> str:
        return (self.action or "").strip().lower()


@dataclass
class TradeSnap:
    id: int
    security_id: int
    txn_type: str  # BUY / SELL
    quantity: float
    price: float
    transaction_date: datetime

    @property
    def side(self) -> str:
        return (self.txn_type or "").strip().lower()


def action_matches_trade(rec: RecoSnap, trade: TradeSnap) -> bool:
    return rec.side == trade.side and rec.security_id == trade.security_id


def _as_dt(value: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, datetime.min.time())


def trade_in_reco_window(rec: RecoSnap, trade: TradeSnap, window_days: int) -> bool:
    if not rec.sent_at:
        return False
    start = _as_dt(rec.sent_at)
    end = start + timedelta(days=window_days)
    tdt = _as_dt(trade.transaction_date)
    return start <= tdt <= end


def qty_closeness(rec_qty: Optional[float], trade_qty: float) -> float:
    """1.0 = exact; 0.0 = 100%+ off or missing reco qty."""
    if not rec_qty or rec_qty <= 0:
        return 0.0
    variance = abs(trade_qty - rec_qty) / rec_qty
    return max(0.0, 1.0 - variance)


def price_closeness(rec_price: Optional[float], trade_price: float) -> float:
    if not rec_price or rec_price <= 0 or not trade_price:
        return 0.0
    variance = abs(trade_price - rec_price) / rec_price
    return max(0.0, 1.0 - variance)


def score_reco_trade(rec: RecoSnap, trade: TradeSnap, window_days: int) -> float:
    if not rec.is_executable or not action_matches_trade(rec, trade):
        return 0.0
    in_sent_window = trade_in_reco_window(rec, trade, window_days)
    near_exec = False
    if rec.executed_at:
        delta = abs((_as_dt(trade.transaction_date) - _as_dt(rec.executed_at)).days)
        near_exec = delta <= 2
    if not in_sent_window and not near_exec:
        return 0.0
    q = qty_closeness(float(rec.quantity) if rec.quantity is not None else None, float(trade.quantity))
    p = price_closeness(
        float(rec.target_price or rec.actual_price or 0) or None,
        float(trade.price),
    )
    # Quantity dominates so a 50-share fill prefers a 50-share reco over a 100-share leftover.
    return (q * 3.0) + (p * 1.0) + (0.15 if in_sent_window else 0.0)


def claim_trades_for_executed(
    executed: Sequence[RecoSnap],
    trades: Sequence[TradeSnap],
    window_days: int,
    min_score: float = 1.2,
) -> Dict[int, int]:
    """
    trade_id -> session_id for fills already explained by executed recos.
    Exclusive: one trade per executed reco.
    """
    claimed: Dict[int, int] = {}
    used_recos: Set[int] = set()
    exec_list = [r for r in executed if r.is_executable and r.session_id]
    # Greedy: highest score first
    scored: List[Tuple[float, RecoSnap, TradeSnap]] = []
    for rec in exec_list:
        for trade in trades:
            s = score_reco_trade(rec, trade, window_days)
            if s >= min_score:
                scored.append((s, rec, trade))
    scored.sort(key=lambda x: x[0], reverse=True)
    for _s, rec, trade in scored:
        if rec.id in used_recos or trade.id in claimed:
            continue
        claimed[trade.id] = int(rec.session_id)
        used_recos.add(rec.id)
    return claimed


def assign_unclaimed_trades_to_sent_sessions(
    sent_open: Sequence[RecoSnap],
    trades: Sequence[TradeSnap],
    window_days: int,
    claimed_trade_ids: Set[int],
    min_score: float = 0.9,
) -> Dict[int, int]:
    """
    trade_id -> session_id for leftover sent recos. Exclusive.
    Trades already claimed by executed recos are skipped.
    """
    assigned: Dict[int, int] = {}
    used_recos: Set[int] = set()
    open_list = [r for r in sent_open if r.is_executable and r.session_id]
    scored: List[Tuple[float, RecoSnap, TradeSnap]] = []
    for rec in open_list:
        for trade in trades:
            if trade.id in claimed_trade_ids:
                continue
            s = score_reco_trade(rec, trade, window_days)
            if s >= min_score:
                scored.append((s, rec, trade))
    scored.sort(key=lambda x: x[0], reverse=True)
    for _s, rec, trade in scored:
        if rec.id in used_recos or trade.id in assigned or trade.id in claimed_trade_ids:
            continue
        assigned[trade.id] = int(rec.session_id)
        used_recos.add(rec.id)
    return assigned


def best_trade_for_reco(
    rec: RecoSnap, session_trades: Sequence[TradeSnap], window_days: int, used_trade_ids: Set[int]
) -> Optional[TradeSnap]:
    best: Optional[TradeSnap] = None
    best_score = 0.0
    for trade in session_trades:
        if trade.id in used_trade_ids:
            continue
        s = score_reco_trade(rec, trade, window_days)
        if s > best_score:
            best_score = s
            best = trade
    return best


def month_key_for_sent(recos: Sequence[RecoSnap]) -> Optional[str]:
    dates = [r.sent_at for r in recos if r.sent_at]
    if not dates:
        return None
    earliest = min(dates)
    return earliest.strftime("%Y-%m")


def session_is_fully_unrecorded(sent_open: Sequence[RecoSnap], assigned_trade_ids_for_session: Set[int]) -> bool:
    """True when every executable sent line in this list has no assigned trade."""
    exec_lines = [r for r in sent_open if r.is_executable]
    if not exec_lines:
        return False
    return len(assigned_trade_ids_for_session) == 0


def pick_recorded_session_same_month(
    unused_session_id: int,
    month_key: str,
    executed: Sequence[RecoSnap],
    claimed: Dict[int, int],
) -> Optional[int]:
    """
    Another session in the same calendar month that has executed recos.
    ``claimed`` is unused (kept for call-site compatibility).
    """
    _ = claimed
    counts: Dict[int, int] = {}
    latest: Dict[int, datetime] = {}
    for rec in executed:
        if not rec.is_executable or not rec.session_id:
            continue
        if rec.session_id == unused_session_id:
            continue
        mk = None
        if rec.sent_at:
            mk = rec.sent_at.strftime("%Y-%m")
        elif rec.executed_at:
            mk = rec.executed_at.strftime("%Y-%m")
        if mk != month_key:
            continue
        sid = int(rec.session_id)
        counts[sid] = counts.get(sid, 0) + 1
        stamp = rec.executed_at or rec.sent_at
        if stamp and (sid not in latest or stamp > latest[sid]):
            latest[sid] = stamp
    if not counts:
        return None
    return max(counts.keys(), key=lambda s: (counts[s], latest.get(s, datetime.min)))


def reco_line_payload(rec: RecoSnap) -> Dict[str, Any]:
    qty = float(rec.quantity) if rec.quantity is not None else None
    target = float(rec.target_price) if rec.target_price is not None else None
    actual = float(rec.actual_price) if rec.actual_price is not None else None
    amount = None
    price = actual if actual else target
    if qty is not None and price:
        amount = round(qty * price, 2)
    return {
        "recommendation_id": rec.id,
        "security_id": rec.security_id,
        "security_symbol": rec.symbol,
        "action": rec.action,
        "quantity": qty,
        "target_price": target,
        "actual_price": actual,
        "amount": amount,
        "status": rec.status,
        "is_hold": rec.is_hold,
    }


def session_detail_payload(session_id: int, recos: Sequence[RecoSnap]) -> Dict[str, Any]:
    lines = [reco_line_payload(r) for r in recos]
    executable = [r for r in recos if r.is_executable]
    recorded = [r for r in executable if (r.status or "").lower() == "executed" or r.executed_at]
    sent_at = None
    stamps = [r.sent_at for r in recos if r.sent_at]
    if stamps:
        sent_at = min(stamps).isoformat()
    name = next((r.session_name for r in recos if r.session_name), None)
    return {
        "session_id": session_id,
        "session_name": name,
        "sent_at": sent_at,
        "line_count": len(lines),
        "executable_count": len(executable),
        "recorded_count": len(recorded),
        "lines": lines,
    }


def superseded_session_message(
    unused_sent_at: Optional[str],
    month_key: str,
    recorded_sent_at: Optional[str],
    unused_session_id: int,
    recorded_session_id: int,
) -> str:
    unused_d = (unused_sent_at or "")[:10] or "unknown date"
    recorded_d = (recorded_sent_at or "")[:10] or "unknown date"
    return (
        f"Recommendation session {unused_session_id} (sent {unused_d}) was not recorded. "
        f"In {month_key}, session {recorded_session_id} (sent {recorded_d}) was recorded. "
        "This often means the advice was revised and resent. "
        "Confirm Session A was unused, then close this alert."
    )
