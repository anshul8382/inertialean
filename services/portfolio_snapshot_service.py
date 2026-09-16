#!/usr/bin/env python3
"""
Portfolio Snapshot Service

Builds a compact portfolio snapshot summary comparing two as-of dates (lifetime metrics):
  - Net Investment, Current Value, optional asset-class breakdown (2+ classes only),
  - Dividends Received (cumulative imputed gross), Portfolio Profit,
  - Equity Allocation %, Portfolio Return %, Nifty Return %, XIRR %, Average Duration (Years)

Uses the same calculation stack as period analysis v2 internally (not exposed via period API).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import logging

logger = logging.getLogger(__name__)

EQUITY_LIKE_CLASSES = ("Equity", "International Equity", "Equity ETF")


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
        except Exception:
            return None
    return None


def _subtract_one_year(d: date) -> date:
    """Best-effort 'same day last year' without external deps."""
    try:
        return date(d.year - 1, d.month, d.day)
    except ValueError:
        dd = d.day
        while dd > 28:
            dd -= 1
            try:
                return date(d.year - 1, d.month, dd)
            except ValueError:
                continue
        return date(d.year - 1, d.month, 28)


def _fmt_month_year(d: date) -> str:
    return d.strftime("%b %Y")


def _to_lakhs(value_rs: float) -> float:
    return float(value_rs or 0.0) / 100000.0


def _pct(n: float, d: float) -> float:
    if not d:
        return 0.0
    return (n / d) * 100.0


def _count_asset_classes_with_value(asset_class_totals: Dict[str, float]) -> int:
    return sum(1 for v in asset_class_totals.values() if float(v or 0) > 0)


@dataclass
class SnapshotPoint:
    as_of_date: date
    net_investment_rs: float
    current_value_rs: float
    asset_class_totals: Dict[str, float]
    dividends_received_rs: float
    nifty_return_percent: float
    xirr_percent: float
    first_activity_date: Optional[date]
    period_results: Dict[str, Any]
    warnings: List[Dict[str, Any]]


class PortfolioSnapshotService:
    @staticmethod
    def _get_first_activity_date(client_id: int) -> Optional[date]:
        from models import Cashflow, Transaction
        from services.cashflow_service import DUMMY_DATES

        earliest: Optional[date] = None

        cf = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date.asc()).first()
        if cf and cf.date:
            cfd = _safe_date(cf.date)
            if cfd and cfd not in DUMMY_DATES:
                earliest = cfd

        txn = Transaction.query.filter_by(client_id=client_id).order_by(Transaction.transaction_date.asc()).first()
        if txn and txn.transaction_date:
            td = _safe_date(txn.transaction_date)
            if td and td not in DUMMY_DATES:
                if earliest is None or td < earliest:
                    earliest = td

        return earliest

    @staticmethod
    def _build_lifetime_start_date(client_id: int) -> Optional[date]:
        earliest = PortfolioSnapshotService._get_first_activity_date(client_id)
        if not earliest:
            return None
        return earliest - timedelta(days=1)

    @staticmethod
    def _cumulative_dividends_rs(client_id: int, as_of: date, first_activity: Optional[date]) -> float:
        """Cumulative imputed gross dividends from first activity through as_of (matches client details)."""
        if not first_activity:
            return 0.0
        from services.snapshot_metrics_cache import get_dividends, set_dividends

        fa_key = first_activity.isoformat()
        cached = get_dividends(client_id, as_of, fa_key)
        if cached is not None:
            return cached
        try:
            from services.imputed_dividend_service import compute_imputed_dividends

            data = compute_imputed_dividends(client_id, start_date=first_activity, end_date=as_of)
            amount = float(data.get("grand_total") or 0.0)
            set_dividends(client_id, as_of, fa_key, amount)
            return amount
        except Exception as e:
            logger.warning(
                "Portfolio snapshot: imputed dividends failed client=%s as_of=%s: %s",
                client_id,
                as_of,
                e,
            )
            return 0.0

    @staticmethod
    def _asset_breakdown_from_portfolio(
        portfolio: Dict[str, Any],
    ) -> Tuple[float, Dict[str, float], List[Dict[str, Any]]]:
        holdings = portfolio.get("holdings") or []
        total = float(portfolio.get("total_value") or 0.0)
        warnings = list(portfolio.get("warnings") or [])

        asset_class_totals: Dict[str, float] = {}
        for h in holdings:
            v = float(h.get("current_value") or h.get("value") or 0.0)
            asset_class = (h.get("asset_class") or "Equity").strip()
            asset_class_totals[asset_class] = asset_class_totals.get(asset_class, 0.0) + v

        classified_total = sum(asset_class_totals.values())
        if abs(total - classified_total) > 0.01:
            logger.warning(
                "Portfolio snapshot asset breakdown mismatch: total=%s, classified=%s",
                total,
                classified_total,
            )
            warnings.append(
                {
                    "type": "data_integrity",
                    "severity": "warning",
                    "message": (
                        f"Asset class totals ({classified_total:.2f}) don't match "
                        f"portfolio total ({total:.2f})"
                    ),
                }
            )

        return total, asset_class_totals, warnings

    @staticmethod
    def _asset_breakdown_at_date(client_id: int, as_of: date) -> Tuple[float, Dict[str, float], List[Dict[str, Any]]]:
        from services.forward_holding_calculation_service import get_client_portfolio_by_date

        p = get_client_portfolio_by_date(client_id, as_of)
        return PortfolioSnapshotService._asset_breakdown_from_portfolio(p)

    @staticmethod
    def _get_period_analysis_results(client_id: int, start_date: date, as_of: date) -> Dict[str, Any]:
        from api.v2.period_analysis_data import PeriodAnalysisDataCollector
        from api.v2.period_enhanced_analytics import PeriodEnhancedAnalyticsCalculator
        from api.v2.period_performance_calculator import PeriodPerformanceCalculator
        from flask import current_app, has_request_context
        from services.snapshot_metrics_cache import get_lifetime_metrics, set_lifetime_metrics

        cached = get_lifetime_metrics(client_id, as_of)
        if cached is not None:
            return cached

        def _compute() -> Dict[str, Any]:
            data = PeriodAnalysisDataCollector(client_id, start_date, as_of)
            ok, errors = data.collect_and_validate()
            if not ok:
                logger.warning(
                    "PortfolioSnapshot: data collection failed for client=%s as_of=%s: %s",
                    client_id,
                    as_of,
                    errors,
                )
                return {}

            perf = PeriodPerformanceCalculator(data).calculate_all(basis="adjusted", matching="fifo")
            enh, _ = PeriodEnhancedAnalyticsCalculator(data).calculate_all(
                perf, prev_period_start_date=None
            )

            payload = {
                "period_xirr": perf.get("period_xirr", {}),
                "benchmark_comparison": enh.get("benchmark_comparison", {}),
                "segment_wise_xirr": enh.get("segment_wise_xirr", {}),
                "end_portfolio": data.end_portfolio,
            }
            set_lifetime_metrics(client_id, as_of, payload)
            return payload

        if has_request_context():
            return _compute()
        with current_app.test_request_context("/internal/portfolio-snapshot"):
            return _compute()

    @staticmethod
    def _period_metrics_to_date(
        client_id: int, as_of: date
    ) -> Tuple[float, float, float, Optional[date], Dict[str, Any]]:
        first_activity = PortfolioSnapshotService._get_first_activity_date(client_id)
        start_date = PortfolioSnapshotService._build_lifetime_start_date(client_id)
        if not start_date:
            return 0.0, 0.0, 0.0, first_activity, {}

        period_results = PortfolioSnapshotService._get_period_analysis_results(client_id, start_date, as_of)
        if not period_results:
            return 0.0, 0.0, 0.0, first_activity, {}

        xirr = period_results.get("period_xirr", {}) or {}
        bench = period_results.get("benchmark_comparison", {}) or {}

        actual_net = float(xirr.get("actual_net_investment") or 0.0)
        xirr_percent = float(xirr.get("xirr_percent") or 0.0)
        nifty_return_percent = float(bench.get("benchmark_gain_loss_percent") or 0.0)

        if actual_net < 0:
            actual_net = abs(actual_net)

        return actual_net, xirr_percent, nifty_return_percent, first_activity, period_results

    @staticmethod
    def _derived_metrics(pt: SnapshotPoint) -> Dict[str, Any]:
        period_results = pt.period_results or {}
        profit = pt.current_value_rs - pt.net_investment_rs

        equity_total = sum(pt.asset_class_totals.get(ac, 0.0) for ac in EQUITY_LIKE_CLASSES)
        equity_alloc = _pct(equity_total, pt.current_value_rs)

        if pt.net_investment_rs > 0:
            port_return = ((pt.current_value_rs / pt.net_investment_rs) - 1.0) * 100.0
        else:
            port_return = 0.0

        benchmark_comparison = period_results.get("benchmark_comparison", {}) or {}
        benchmark_end_value = float(benchmark_comparison.get("benchmark_end_value", 0.0))
        if pt.net_investment_rs > 0:
            nifty_return = ((benchmark_end_value / pt.net_investment_rs) - 1.0) * 100.0
        else:
            nifty_return = 0.0

        segment_data = period_results.get("segment_wise_xirr", {}) or {}
        segments = segment_data.get("segments", {})
        duration_years = 0.0
        if segments:
            total_weighted_duration = 0.0
            total_portfolio_value = 0.0
            for seg_data in segments.values():
                current_value = float(seg_data.get("current_value", 0.0))
                avg_duration_years = float(seg_data.get("avg_duration_years", 0.0))
                if current_value > 0 and avg_duration_years > 0:
                    total_weighted_duration += avg_duration_years * current_value
                    total_portfolio_value += current_value
            if total_portfolio_value > 0:
                duration_years = round(total_weighted_duration / total_portfolio_value, 2)
        if duration_years == 0.0 and pt.first_activity_date:
            duration_years = round((pt.as_of_date - pt.first_activity_date).days / 365.25, 2)

        return {
            "profit_rs": profit,
            "equity_alloc": equity_alloc,
            "port_return": port_return,
            "nifty_return": nifty_return,
            "duration_years": duration_years,
        }

    @staticmethod
    def _should_show_asset_breakdown(cur_pt: SnapshotPoint, prev_pt: SnapshotPoint) -> bool:
        cur_n = _count_asset_classes_with_value(cur_pt.asset_class_totals)
        prev_n = _count_asset_classes_with_value(prev_pt.asset_class_totals)
        return max(cur_n, prev_n) >= 2

    @staticmethod
    def _asset_class_union_sorted(cur_pt: SnapshotPoint, prev_pt: SnapshotPoint) -> List[str]:
        all_asset_classes = set(cur_pt.asset_class_totals.keys()) | set(prev_pt.asset_class_totals.keys())
        return sorted(
            all_asset_classes,
            key=lambda ac: (cur_pt.asset_class_totals.get(ac, 0.0), ac),
            reverse=True,
        )

    @staticmethod
    def _build_comparison_rows(
        cur_pt: SnapshotPoint,
        prev_pt: SnapshotPoint,
        *,
        single_column: bool = False,
    ) -> List[Dict[str, Any]]:
        def _row(label: str, cur_v: Any, prev_v: Any, kind: str) -> Dict[str, Any]:
            if single_column:
                return {"label": label, "cur": cur_v, "kind": kind}
            return {"label": label, "cur": cur_v, "prev": prev_v, "kind": kind}

        cur_m = PortfolioSnapshotService._derived_metrics(cur_pt)
        prev_m = PortfolioSnapshotService._derived_metrics(prev_pt)

        rows: List[Dict[str, Any]] = [
            _row("Net Investment", _to_lakhs(cur_pt.net_investment_rs), _to_lakhs(prev_pt.net_investment_rs), "money"),
            _row("Current Value", _to_lakhs(cur_pt.current_value_rs), _to_lakhs(prev_pt.current_value_rs), "money"),
        ]

        if PortfolioSnapshotService._should_show_asset_breakdown(cur_pt, prev_pt):
            for asset_class_name in PortfolioSnapshotService._asset_class_union_sorted(cur_pt, prev_pt):
                cur_value = cur_pt.asset_class_totals.get(asset_class_name, 0.0)
                prev_value = prev_pt.asset_class_totals.get(asset_class_name, 0.0)
                if cur_value > 0 or prev_value > 0:
                    rows.append(
                        _row(
                            f"- Of Which {asset_class_name}",
                            _to_lakhs(cur_value),
                            _to_lakhs(prev_value),
                            "money",
                        )
                    )

        rows.extend(
            [
                _row(
                    "Dividends Received",
                    _to_lakhs(cur_pt.dividends_received_rs),
                    _to_lakhs(prev_pt.dividends_received_rs),
                    "money",
                ),
                _row("Portfolio Profit", _to_lakhs(cur_m["profit_rs"]), _to_lakhs(prev_m["profit_rs"]), "money"),
                _row("Equity Allocation (%)", cur_m["equity_alloc"], prev_m["equity_alloc"], "percent"),
                _row("Portfolio Return (%)", cur_m["port_return"], prev_m["port_return"], "percent"),
                _row("Nifty Return (%)", cur_m["nifty_return"], prev_m["nifty_return"], "percent"),
                _row("XIRR (%)", cur_pt.xirr_percent, prev_pt.xirr_percent, "percent"),
                _row(
                    "Average Duration (Years)",
                    cur_m["duration_years"],
                    prev_m["duration_years"],
                    "number",
                ),
            ]
        )
        return rows

    def build_snapshot_point(
        self,
        client_id: int,
        as_of: date,
        *,
        prebuilt_portfolio: Optional[Dict[str, Any]] = None,
    ) -> SnapshotPoint:
        net_inv, xirr_pct, nifty_pct, first_activity, period_results = self._period_metrics_to_date(
            client_id, as_of
        )
        portfolio = prebuilt_portfolio or (period_results or {}).get("end_portfolio")
        if portfolio:
            total, asset_class_totals, warnings = self._asset_breakdown_from_portfolio(portfolio)
        else:
            total, asset_class_totals, warnings = self._asset_breakdown_at_date(client_id, as_of)
        dividends_rs = self._cumulative_dividends_rs(client_id, as_of, first_activity)
        return SnapshotPoint(
            as_of_date=as_of,
            net_investment_rs=net_inv,
            current_value_rs=total,
            asset_class_totals=asset_class_totals,
            dividends_received_rs=dividends_rs,
            nifty_return_percent=nifty_pct,
            xirr_percent=xirr_pct,
            first_activity_date=first_activity,
            period_results=period_results or {},
            warnings=warnings or [],
        )

    def _empty_snapshot_point(self, as_of: date, first_activity: Optional[date]) -> SnapshotPoint:
        return SnapshotPoint(
            as_of_date=as_of,
            net_investment_rs=0.0,
            current_value_rs=0.0,
            asset_class_totals={},
            dividends_received_rs=0.0,
            nifty_return_percent=0.0,
            xirr_percent=0.0,
            first_activity_date=first_activity,
            period_results={},
            warnings=[],
        )

    def _comparison_payload(
        self,
        cur_pt: SnapshotPoint,
        prev_pt: SnapshotPoint,
        *,
        current_label: str,
        previous_label: str,
        single_column: bool = False,
    ) -> Dict[str, Any]:
        rows = self._build_comparison_rows(cur_pt, prev_pt, single_column=single_column)
        all_warnings = (cur_pt.warnings or []) + (prev_pt.warnings or [])
        payload: Dict[str, Any] = {
            "title": "Portfolio Snapshot",
            "rows": rows,
            "warnings": all_warnings,
            "has_warnings": len(all_warnings) > 0,
        }
        if single_column:
            payload["columns"] = {"current": current_label}
        else:
            payload["columns"] = {
                "current": current_label,
                "previous": previous_label,
            }
        return payload

    def build_period_comparison(self, client_id: int, start_date: date, end_date: date) -> Dict[str, Any]:
        """
        Compare lifetime as-of metrics at period end vs period start.
        columns.current = end_date; columns.previous = start_date.
        """
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        from services.period_portfolio_cache import get_period_portfolios

        cached = get_period_portfolios(client_id, start_date, end_date)
        prefetch_start = cached[0] if cached else None
        prefetch_end = cached[1] if cached else None

        try:
            cur_pt = self.build_snapshot_point(
                client_id, end_date, prebuilt_portfolio=prefetch_end
            )
        except Exception as e:
            logger.error(
                "Error building snapshot end point client=%s end=%s: %s",
                client_id,
                end_date,
                e,
                exc_info=True,
            )
            raise

        try:
            prev_pt = self.build_snapshot_point(
                client_id, start_date, prebuilt_portfolio=prefetch_start
            )
        except Exception as e:
            logger.warning(
                "Error building snapshot start point client=%s start=%s: %s",
                client_id,
                start_date,
                e,
                exc_info=True,
            )
            prev_pt = self._empty_snapshot_point(start_date, cur_pt.first_activity_date)

        return self._comparison_payload(
            cur_pt,
            prev_pt,
            current_label=f"Rs Lakhs {_fmt_month_year(end_date)}",
            previous_label=f"Rs Lakhs {_fmt_month_year(start_date)}",
        )

    def build_single_snapshot(self, client_id: int, as_of_date: date) -> Dict[str, Any]:
        try:
            pt = self.build_snapshot_point(client_id, as_of_date)
        except Exception as e:
            logger.error(
                "Error building snapshot point for client %s at %s: %s",
                client_id,
                as_of_date,
                e,
                exc_info=True,
            )
            raise

        empty = self._empty_snapshot_point(as_of_date, pt.first_activity_date)
        return self._comparison_payload(
            pt,
            empty,
            current_label=f"Rs Lakhs {_fmt_month_year(as_of_date)}",
            previous_label="",
            single_column=True,
        )

    def build_comparison(self, client_id: int, end_date: date) -> Dict[str, Any]:
        """Legacy: end_date vs same day one year earlier."""
        prev = _subtract_one_year(end_date)
        return self.build_period_comparison(client_id, prev, end_date)
