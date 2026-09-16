"""
Portfolio Performance Monitor Agent
====================================
Scans client portfolios and compares performance (XIRR) with benchmark.
Reports clients where portfolio (or equity-only) performance is worse than the benchmark.

- For clients with only equity: compare full portfolio XIRR vs benchmark.
- For clients with other assets (e.g. debt, gold): compare only equity asset
  performance with benchmark (equity XIRR vs Nifty XIRR).

Reports all issues via DataIntegrityIssue like the Data Integrity Manager.

Scheduled monthly via Airflow DAG ``portfolio_performance_monthly``; use the portfolio
performance UI or agents dashboard for an on-demand full audit between runs.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from extensions import db
from models import (
    Client, Holding, Transaction, Cashflow,
    DataIntegrityIssue,
)
from agents.base import BaseAgent

logger = logging.getLogger(__name__)

UNDERPERFORMANCE_CATEGORY = "PORTFOLIO_PERFORMANCE"
UNDERPERFORMANCE_CHECK = "underperformance_vs_benchmark"


def _resolve_open_underperformance_issues(client_id: int) -> int:
    """
    Resolve open underperformance_vs_benchmark issues for one client when the latest
    snapshot shows no underperformance. Closes linked OpsTasks and syncs alerts via
    issue_lifecycle_service.
    """
    from services.issue_lifecycle_service import resolve_issue_and_sync
    from services.alert_task_reconcile_service import get_system_resolver_user_id

    open_issues = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.client_id == client_id,
        DataIntegrityIssue.check_category == UNDERPERFORMANCE_CATEGORY,
        DataIntegrityIssue.check_name == UNDERPERFORMANCE_CHECK,
        DataIntegrityIssue.status == "open",
    ).all()
    if not open_issues:
        return 0

    actor_id = get_system_resolver_user_id()
    for issue in open_issues:
        resolve_issue_and_sync(
            issue,
            resolution_type="auto_cleared",
            actor_user_id=actor_id,
            notes="Underperformance no longer detected on latest vs-benchmark snapshot.",
            source="portfolio_performance_monitor",
            commit=False,
        )
    logger.info(
        "Cleared %s stale open underperformance issue(s) for client %s",
        len(open_issues),
        client_id,
    )
    return len(open_issues)


def _close_orphan_portfolio_underperformance_tasks(client_id: int) -> int:
    """
    Complete pending/in-progress/snoozed OpsTasks for this client when the linked
    underperformance issue is already resolved (e.g. manual resolve skipped lifecycle).
    """
    from models import OpsTask
    from services.alert_task_reconcile_service import OPEN_TASK_STATUSES

    tasks = (
        OpsTask.query.join(
            DataIntegrityIssue,
            OpsTask.data_integrity_issue_id == DataIntegrityIssue.id,
        )
        .filter(
            OpsTask.client_id == client_id,
            OpsTask.status.in_(list(OPEN_TASK_STATUSES)),
            DataIntegrityIssue.check_category == UNDERPERFORMANCE_CATEGORY,
            DataIntegrityIssue.check_name == UNDERPERFORMANCE_CHECK,
            DataIntegrityIssue.status.notin_(["open", "baseline"]),
        )
        .all()
    )
    if not tasks:
        return 0
    now = datetime.utcnow()
    prefix = "[Reconciled: linked underperformance issue is no longer open] "
    for task in tasks:
        task.status = "completed"
        task.completed_at = now
        task.completed_by = None
        task.reminder_at = None
        task.reminder_sent = True
        task.updated_at = now
        combined = prefix + (task.notes or "")
        task.notes = combined[-_NOTE_MAX_LEN:] if len(combined) > _NOTE_MAX_LEN else combined
    logger.info(
        "Closed %s orphan portfolio underperformance OpsTask(s) for client %s",
        len(tasks),
        client_id,
    )
    return len(tasks)


_NOTE_MAX_LEN = 20000


def _append_underperformance_rerun_audit(
    issue: DataIntegrityIssue,
    snapshot: Dict[str, Any],
    run_id: Optional[str],
) -> None:
    """
    When the same open underperformance issue is seen again on a new agent run,
    record current snapshot on the issue and append human-readable lines to linked
    OpsTasks and Alerts so operators know data is fresh, not stale.
    """
    from models import OpsTask
    from alert_system_models import Alert
    from services.alert_task_reconcile_service import OPEN_TASK_STATUSES, get_system_resolver_user_id

    now = datetime.utcnow()
    ts = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    det = dict(issue.details or {})
    log = det.get("agent_verification_log")
    if not isinstance(log, list):
        log = []
    entry = {
        "at": now.isoformat() + "Z",
        "run_id": run_id,
        "portfolio_xirr_pct": snapshot.get("portfolio_xirr_pct"),
        "benchmark_xirr_pct": snapshot.get("benchmark_xirr_pct"),
        "underperformance_pct": snapshot.get("underperformance_pct"),
        "comparison_type": snapshot.get("comparison_type"),
    }
    log.append(entry)
    det["agent_verification_log"] = log[-30:]
    det["last_agent_verification_at"] = entry["at"]
    det["last_agent_verification_run_id"] = run_id
    issue.details = det

    note_body = (
        f"Re-verified (current data): {snapshot.get('comparison_type', 'n/a')} — "
        f"portfolio XIRR {snapshot.get('portfolio_xirr_pct')}% vs benchmark {snapshot.get('benchmark_xirr_pct')}% "
        f"(gap benchmark−portfolio: {snapshot.get('underperformance_pct')}%). "
        f"run_id={run_id or 'n/a'}"
    )
    block = f"\n---\n[{ts}] portfolio_performance_monitor:\n{note_body}\n"

    from services.task_assignment_service import _base_issue_message_for_task

    base_msg = _base_issue_message_for_task(issue.message)
    msg_preview = base_msg[:150]
    new_task_name = f"[{issue.check_category or 'issue'}] {msg_preview}"
    if len(new_task_name) > 200:
        new_task_name = new_task_name[:197] + "..."
    sev = (issue.severity or "warning").lower()
    new_priority = "high" if sev == "critical" else "medium" if sev == "warning" else "low"

    for task in OpsTask.query.filter(
        OpsTask.data_integrity_issue_id == issue.id,
        OpsTask.status.in_(list(OPEN_TASK_STATUSES)),
    ).all():
        task.name = new_task_name
        task.priority = new_priority
        combined = (task.notes or "") + block
        if len(combined) > _NOTE_MAX_LEN:
            combined = combined[-_NOTE_MAX_LEN:]
        task.notes = combined
        task.updated_at = now

    aid = getattr(issue, "alert_id", None)
    if aid:
        alert = db.session.get(Alert, aid)
        if alert and alert.status in ("active", "acknowledged", "snoozed"):
            try:
                uid = get_system_resolver_user_id()
                alert.add_note(uid, note_body, user_name="portfolio_performance_monitor")
            except Exception as e:
                logger.warning("Could not add alert note for issue %s alert %s: %s", issue.id, aid, e)


def _derive_asset_class(security) -> str:
    """Derive asset class for a security (delegates to shared read-only util)."""
    from utils.security_asset_class import derive_asset_class
    return derive_asset_class(security)


def _equity_only_cashflows_and_value_for_client(client_id: int) -> Tuple[List[Tuple[datetime, float]], float]:
    """
    Build (date, amount) cashflows from equity-only transactions.
    BUY = negative (investment), SELL = positive (withdrawal).
    Returns (list of (date, amount)), equity_current_value.
    """
    txns = (
        Transaction.query.filter_by(client_id=client_id)
        .options(db.joinedload(Transaction.security))
        .order_by(Transaction.transaction_date)
        .all()
    )

    by_date: Dict[datetime, float] = defaultdict(float)
    for t in txns:
        if not t.security:
            continue
        if _derive_asset_class(t.security) != 'Equity':
            continue
        amt = float(t.amount) if t.amount else 0
        if amt == 0:
            continue
        dt = t.transaction_date
        if isinstance(dt, datetime):
            pass
        elif hasattr(dt, 'date'):
            d = dt.date() if callable(getattr(dt, 'date', None)) else dt
            dt = datetime.combine(d, dt_time.min)
        else:
            continue
        if t.type and str(t.type).upper() == 'BUY':
            by_date[dt] -= abs(amt)
        else:
            by_date[dt] += abs(amt)

    cashflow_tuples = [(d, by_date[d]) for d in sorted(by_date.keys())]

    holdings = Holding.query.filter_by(client_id=client_id).options(
        db.joinedload(Holding.security)
    ).all()
    equity_value = 0.0
    for h in holdings:
        if not h.security:
            continue
        if _derive_asset_class(h.security) != 'Equity':
            continue
        qty = float(h.quantity) if h.quantity else 0
        price = float(h.security.current_price) if getattr(h.security, 'current_price', None) else 0
        equity_value += qty * price

    return cashflow_tuples, equity_value


def _get_top_active_negative_contributors(
    client_id: int,
    *,
    as_of_date: Optional[datetime] = None,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    """
    Reuse Period Analysis V2 security contribution logic to identify underperformers.

    Rule:
    - Take only active positions (end_quantity > 0)
    - Take only negative value_contribution
    - Pick top-N most negative contributors
    """
    as_of = (as_of_date or datetime.utcnow()).date()
    start_date = as_of - timedelta(days=365)
    end_date = as_of
    try:
        from api.v1.period_analysis import (
            calculate_mark_to_market_gains,
            calculate_worst_performers_from_mtm,
        )

        holdings = Holding.query.filter_by(client_id=client_id).options(
            db.joinedload(Holding.security)
        ).all()
        # calculate_mark_to_market_gains is decorated for API auditing; use the undecorated
        # callable when available so this can run in background jobs without request context.
        calc_mtm = getattr(calculate_mark_to_market_gains, "__wrapped__", calculate_mark_to_market_gains)
        mtm = calc_mtm(client_id, start_date, end_date, holdings) or {}
        worst = calculate_worst_performers_from_mtm(
            mtm,
            client_id=client_id,
            start_date=start_date,
            end_date=end_date,
        ) or {}
        rows = worst.get("worst_performers") or []

        active_negative = []
        for r in rows:
            qty = float(r.get("current_quantity") or 0.0)
            contrib = float(r.get("value_contribution") or 0.0)
            if qty <= 0 or contrib >= 0:
                continue
            active_negative.append(
                {
                    "symbol": r.get("symbol"),
                    "name": r.get("name"),
                    "sector": r.get("sector"),
                    "value_contribution": round(contrib, 2),
                    "contribution_percent": round(float(r.get("contribution_percent") or 0.0), 2),
                    "price_percent_change": round(float(r.get("price_percent_change") or 0.0), 2),
                    "current_quantity": round(qty, 4),
                    "current_value": round(float(r.get("current_value") or 0.0), 2),
                }
            )

        active_negative.sort(key=lambda x: x["value_contribution"])  # most negative first
        return active_negative[:max_items]
    except Exception as e:
        logger.warning(
            "Could not compute active negative contributors for client %s: %s",
            client_id,
            e,
        )
        return []


def compute_underperformance_snapshot(client_id: int) -> Optional[Dict[str, Any]]:
    """
    Recompute current portfolio-vs-benchmark performance state for one client.
    Returns None when data is insufficient.
    """
    from api.v1.performance import calculate_xirr, calculate_nifty_xirr, calculate_nifty_xirr_from_tuples

    client = db.session.get(Client, client_id)
    if not client:
        return None

    holdings = Holding.query.filter_by(client_id=client_id).options(
        db.joinedload(Holding.security)
    ).all()

    asset_classes = set()
    equity_value = 0.0
    total_value = 0.0
    for h in holdings:
        if not h.security:
            continue
        ac = _derive_asset_class(h.security)
        asset_classes.add(ac)
        qty = float(h.quantity) if h.quantity else 0
        price = float(h.security.current_price) if getattr(h.security, 'current_price', None) else 0
        val = qty * price
        total_value += val
        if ac == 'Equity':
            equity_value += val

    has_non_equity = bool(asset_classes - {'Equity', 'Unknown'})
    use_equity_only = has_non_equity and equity_value > 0

    if use_equity_only:
        cashflow_tuples, current_value = _equity_only_cashflows_and_value_for_client(client_id)
        if not cashflow_tuples and current_value == 0:
            return None
        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_tuples, current_value)
        comparison_type = "equity_only"
    else:
        cashflows = Cashflow.query.filter_by(client_id=client_id).order_by(Cashflow.date).all()
        if not cashflows:
            return None
        cashflow_data = [(cf.date, float(cf.amount)) for cf in cashflows]
        current_value = total_value
        portfolio_xirr, _, _, _, _ = calculate_xirr(cashflow_data, current_value)
        comparison_type = "full_portfolio"

    # Benchmark must use the same cashflow basis as the portfolio leg (Period Analysis V2
    # equity benchmark uses equity trade flows; full Cashflow here would mismatch for mixed
    # portfolios and falsely flag underperformance).
    if use_equity_only:
        nifty_xirr, nifty_current_value, _ = calculate_nifty_xirr_from_tuples(cashflow_tuples)
    else:
        nifty_xirr, nifty_current_value, _ = calculate_nifty_xirr(client_id)
    if nifty_xirr is None or (
        hasattr(nifty_xirr, '__float__')
        and float(nifty_xirr) == 0
        and nifty_current_value == 0
    ):
        return None

    portfolio_xirr_pct = float(portfolio_xirr) * 100
    nifty_xirr_pct = float(nifty_xirr) * 100
    underperformance_pct = nifty_xirr_pct - portfolio_xirr_pct
    return {
        "portfolio_xirr_pct": round(portfolio_xirr_pct, 2),
        "benchmark_xirr_pct": round(nifty_xirr_pct, 2),
        "underperformance_pct": round(underperformance_pct, 2),
        "comparison_type": comparison_type,
        "portfolio_current_value": round(current_value, 2),
        "benchmark_current_value": round(float(nifty_current_value), 2),
        "asset_classes": list(asset_classes),
        "use_equity_only": use_equity_only,
    }


class PortfolioPerformanceMonitor(BaseAgent):
    """
    Scans portfolios and reports clients where performance is worse than benchmark.
    Uses equity-only comparison when client has mixed assets.
    """

    @property
    def agent_name(self) -> str:
        return "portfolio_performance_monitor"

    @property
    def agent_version(self) -> str:
        return "1.0.0"

    def run_checks(self, client_id: int, **kwargs) -> List[DataIntegrityIssue]:
        """
        Run performance vs benchmark check for one client.
        Creates an issue when portfolio (or equity) XIRR is below benchmark XIRR.
        """
        issues = []

        try:
            _close_orphan_portfolio_underperformance_tasks(client_id)
            snapshot = compute_underperformance_snapshot(client_id)
        except ImportError:
            logger.warning("api.v1.performance not available, skipping client %s", client_id)
            return issues
        try:
            if not snapshot:
                return issues
            if float(snapshot["underperformance_pct"]) <= 0:
                _resolve_open_underperformance_issues(client_id)
                return issues

            severity = 'critical' if float(snapshot["underperformance_pct"]) > 5.0 else 'warning'
            top_negative = _get_top_active_negative_contributors(client_id, max_items=5)
            snapshot["underperforming_securities"] = top_negative
            snapshot["underperforming_securities_basis"] = "period_analysis_v2_security_contribution_1y"
            if top_negative:
                line = ", ".join(
                    f"{(x.get('symbol') or x.get('name') or 'N/A')} ({float(x.get('contribution_percent') or 0.0):+.2f}%)"
                    for x in top_negative[:3]
                )
                underperformers_text = f" Top active negative contributors: {line}."
            else:
                underperformers_text = ""

            message = (
                f"Portfolio {'equity ' if snapshot['use_equity_only'] else ''}XIRR ({snapshot['portfolio_xirr_pct']:.2f}%) "
                f"is below benchmark Nifty XIRR ({snapshot['benchmark_xirr_pct']:.2f}%). "
                f"Underperformance: {snapshot['underperformance_pct']:.2f}%.{underperformers_text}"
            )
            group_key = f"PORTFOLIO_PERFORMANCE_underperformance_{client_id}"

            should_skip, _skip_reason = self.should_skip_issue(
                client_id,
                UNDERPERFORMANCE_CATEGORY,
                UNDERPERFORMANCE_CHECK,
            )
            if should_skip:
                return issues

            run_id = self.current_run.run_id if self.current_run else None
            existing = DataIntegrityIssue.query.filter_by(
                client_id=client_id,
                check_category=UNDERPERFORMANCE_CATEGORY,
                check_name=UNDERPERFORMANCE_CHECK,
                status="open",
                group_key=group_key,
            ).first()

            if existing:
                prev_log = (existing.details or {}).get("agent_verification_log")
                if not isinstance(prev_log, list):
                    prev_log = []
                existing.message = message
                existing.severity = severity
                new_details = dict(snapshot)
                new_details["agent_verification_log"] = prev_log
                existing.details = new_details
                existing.run_id = run_id or existing.run_id
                _append_underperformance_rerun_audit(existing, snapshot, run_id)
                issues.append(existing)
                return issues

            issue = self.create_issue(
                client_id=client_id,
                check_category=UNDERPERFORMANCE_CATEGORY,
                check_name=UNDERPERFORMANCE_CHECK,
                severity=severity,
                message=message,
                details=snapshot,
                suggested_action="Review portfolio allocation and holdings; consider client risk profile and time horizon.",
                group_key=group_key,
            )
            if issue:
                issues.append(issue)

            return issues
        except Exception as e:
            logger.exception(
                "Portfolio performance check failed for client %s: %s", client_id, e
            )
            return issues

    def _equity_only_cashflows_and_value(self, client_id: int) -> Tuple[List[Tuple[datetime, float]], float]:
        """
        Build (date, amount) cashflows from equity-only transactions.
        BUY = negative (investment), SELL = positive (withdrawal).
        Returns (list of (date, amount)), equity_current_value.
        """
        txns = (
            Transaction.query.filter_by(client_id=client_id)
            .options(db.joinedload(Transaction.security))
            .order_by(Transaction.transaction_date)
            .all()
        )

        by_date: Dict[datetime, float] = defaultdict(float)
        for t in txns:
            if not t.security:
                continue
            if _derive_asset_class(t.security) != 'Equity':
                continue
            amt = float(t.amount) if t.amount else 0
            if amt == 0:
                continue
            dt = t.transaction_date
            if isinstance(dt, datetime):
                pass
            elif hasattr(dt, 'date'):
                d = dt.date() if callable(getattr(dt, 'date', None)) else dt
                dt = datetime.combine(d, dt_time.min)
            else:
                continue
            if t.type and str(t.type).upper() == 'BUY':
                by_date[dt] -= abs(amt)
            else:
                by_date[dt] += abs(amt)

        cashflow_tuples = [(d, by_date[d]) for d in sorted(by_date.keys())]

        # Equity current value from holdings
        holdings = Holding.query.filter_by(client_id=client_id).options(
            db.joinedload(Holding.security)
        ).all()
        equity_value = 0.0
        for h in holdings:
            if not h.security:
                continue
            if _derive_asset_class(h.security) != 'Equity':
                continue
            qty = float(h.quantity) if h.quantity else 0
            price = float(h.security.current_price) if getattr(h.security, 'current_price', None) else 0
            equity_value += qty * price

        return cashflow_tuples, equity_value
