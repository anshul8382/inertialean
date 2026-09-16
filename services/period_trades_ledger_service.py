#!/usr/bin/env python3
"""
Period trades ledger for performance review emails / UI.

Returns sell transactions in a date range with realized P&L (per trade),
grouped by trade date. Does not modify the period analysis API.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)


def _safe_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _serialize_trade_row(
    *,
    trade_date: date,
    symbol: str,
    name: str,
    quantity: float,
    avg_buy_price: float,
    sell_price: float,
    realized_gain: float,
    realized_gain_percent: float,
    transaction_id: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "trade_date": trade_date.isoformat(),
        "symbol": symbol,
        "name": name,
        "quantity": quantity,
        "avg_buy_price": avg_buy_price,
        "sell_price": sell_price,
        "realized_gain": round(realized_gain, 2),
        "realized_gain_percent": round(realized_gain_percent, 2),
        "transaction_id": transaction_id,
    }


class PeriodTradesLedgerService:
    def build_ledger(self, client_id: int, start_date: date, end_date: date) -> Dict[str, Any]:
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        trades = self._collect_sell_trades(client_id, start_date, end_date)
        by_date_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for t in trades:
            by_date_map[t["trade_date"]].append(t)

        by_date: List[Dict[str, Any]] = []
        for d in sorted(by_date_map.keys(), reverse=True):
            day_trades = by_date_map[d]
            total_pnl = sum(float(x.get("realized_gain") or 0) for x in day_trades)
            by_date.append(
                {
                    "date": d,
                    "total_realized_pnl": round(total_pnl, 2),
                    "trade_count": len(day_trades),
                    "trades": sorted(day_trades, key=lambda x: x.get("symbol") or ""),
                }
            )

        total_realized = sum(float(t.get("realized_gain") or 0) for t in trades)

        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            "total_realized_pnl": round(total_realized, 2),
            "sell_count": len(trades),
            "trades": trades,
            "by_date": by_date,
        }

    def _collect_sell_trades(self, client_id: int, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        """Per-sell rows via existing period trade analytics (same as period analysis stack)."""
        from models import Holding, Transaction

        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        trades: List[Dict[str, Any]] = []

        try:
            from api.v1.performance import calculate_period_trade_analytics

            holdings = Holding.query.filter_by(client_id=client_id).all()
            raw = calculate_period_trade_analytics(client_id, holdings, start_date, end_date)
            for t in raw.get("stocks_sold_in_period") or []:
                td = _safe_date(t.get("sell_date"))
                if not td:
                    continue
                trades.append(
                    _serialize_trade_row(
                        trade_date=td,
                        symbol=str(t.get("symbol") or "—"),
                        name=str(t.get("name") or t.get("symbol") or "—"),
                        quantity=float(t.get("quantity_sold") or 0),
                        avg_buy_price=float(t.get("avg_buy_price") or 0),
                        sell_price=float(t.get("sell_price") or 0),
                        realized_gain=float(t.get("realized_gain") or 0),
                        realized_gain_percent=float(
                            t.get("realized_gain_percent") or t.get("gain_percent") or 0
                        ),
                    )
                )
        except Exception as e:
            logger.warning(
                "PeriodTradesLedger: calculate_period_trade_analytics failed client=%s: %s",
                client_id,
                e,
            )

        if trades:
            return sorted(trades, key=lambda x: (x["trade_date"], x.get("symbol") or ""), reverse=True)

        # Fallback: direct SELL transactions in period
        sell_txns = (
            Transaction.query.filter(
                Transaction.client_id == client_id,
                Transaction.type == "SELL",
                Transaction.transaction_date >= start_datetime,
                Transaction.transaction_date <= end_datetime,
            )
            .order_by(Transaction.transaction_date.desc())
            .all()
        )

        for sell_txn in sell_txns:
            td = _safe_date(sell_txn.transaction_date)
            if not td:
                continue

            buy_txns = (
                Transaction.query.filter(
                    Transaction.client_id == client_id,
                    Transaction.security_id == sell_txn.security_id,
                    Transaction.type == "BUY",
                    Transaction.transaction_date <= sell_txn.transaction_date,
                )
                .order_by(Transaction.transaction_date)
                .all()
            )

            if buy_txns:
                total_buy_cost = sum(float(b.quantity) * float(b.price) for b in buy_txns)
                total_buy_qty = sum(float(b.quantity) for b in buy_txns)
                avg_buy = total_buy_cost / total_buy_qty if total_buy_qty > 0 else 0.0
            else:
                avg_buy = 0.0

            sell_price = float(sell_txn.price)
            qty = float(sell_txn.quantity)
            realized = (sell_price - avg_buy) * qty
            pct = ((sell_price - avg_buy) / avg_buy * 100.0) if avg_buy > 0 else 0.0
            sec = sell_txn.security

            trades.append(
                _serialize_trade_row(
                    trade_date=td,
                    symbol=sec.symbol if sec else "—",
                    name=(sec.name if sec else "") or (sec.symbol if sec else "—"),
                    quantity=qty,
                    avg_buy_price=avg_buy,
                    sell_price=sell_price,
                    realized_gain=realized,
                    realized_gain_percent=pct,
                    transaction_id=int(sell_txn.id) if sell_txn.id else None,
                )
            )

        return sorted(trades, key=lambda x: (x["trade_date"], x.get("symbol") or ""), reverse=True)
