#!/usr/bin/env python3
"""
Group MTM trade rows (from calculate_mtm_trades_profitability) by trade date.

No portfolio reconstruction — reuses the same helper as best/worst MTM trades.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Dict, List

import logging

logger = logging.getLogger(__name__)


class PeriodMtmTradesLedgerService:
    def build_ledger(self, client_id: int, start_date: date, end_date: date) -> Dict[str, Any]:
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        from api.v1.period_analysis import calculate_mtm_trades_profitability

        mtm = calculate_mtm_trades_profitability(client_id, start_date, end_date, end_holdings_map=None)
        trades = mtm.get("all_mtm_trades") or []

        return self._group_trades(trades, start_date, end_date)

    @staticmethod
    def group_from_mtm_analysis(mtm_analysis: Dict[str, Any], start_date: date, end_date: date) -> Dict[str, Any]:
        """Build grouped ledger from period analysis payload (no extra DB work)."""
        trades = (mtm_analysis or {}).get("all_mtm_trades") or []
        return PeriodMtmTradesLedgerService._group_trades(trades, start_date, end_date)

    @staticmethod
    def _group_trades(trades: List[Dict[str, Any]], start_date: date, end_date: date) -> Dict[str, Any]:
        by_date_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for t in trades:
            d = (t.get("trade_date") or "")[:10]
            if d:
                by_date_map[d].append(t)

        by_date: List[Dict[str, Any]] = []
        for d in sorted(by_date_map.keys(), reverse=True):
            day_trades = by_date_map[d]
            day_total = sum(float(x.get("mtm_profit") or 0) for x in day_trades)
            by_date.append(
                {
                    "date": d,
                    "total_mtm": round(day_total, 2),
                    "trade_count": len(day_trades),
                    "trades": sorted(day_trades, key=lambda x: x.get("symbol") or ""),
                }
            )

        total_mtm = sum(float(t.get("mtm_profit") or 0) for t in trades)

        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            "total_mtm": round(total_mtm, 2),
            "trade_count": len(trades),
            "trades": trades,
            "by_date": by_date,
        }
