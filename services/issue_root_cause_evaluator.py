"""
Rule-based root-cause evaluator for open DataIntegrityIssue rows.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from models import DataIntegrityIssue, Transaction
from sqlalchemy import func


SEVERITY_WEIGHT = {"critical": 100, "warning": 60, "info": 20}
_FLOAT_EPS = 1e-6


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _floats_equal(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= _FLOAT_EPS


def _parse_trade_date(raw: Any) -> Optional[date]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    # ISO datetime or date
    try:
        if "T" in text or " " in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _duplicate_signature_from_issue(
    issue: DataIntegrityIssue,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Extract (client, security, type, date, qty, price, amount) used by check_duplicates.
    Returns (payload, error_reason).
    """
    details = issue.details or {}
    if isinstance(details, str):
        details = {}

    security_id = issue.security_id or details.get("security_id")
    txn_type = details.get("type")
    quantity = _as_float(details.get("quantity"))
    price = _as_float(details.get("price"))
    amount = _as_float(details.get("amount"))
    trade_date = issue.reference_date or _parse_trade_date(details.get("date"))

    txn_id = issue.transaction_id or details.get("transaction_id")
    if txn_id and (
        security_id is None
        or not txn_type
        or quantity is None
        or price is None
        or amount is None
        or trade_date is None
    ):
        txn = Transaction.query.get(txn_id)
        if txn:
            security_id = security_id or txn.security_id
            txn_type = txn_type or txn.type
            quantity = quantity if quantity is not None else _as_float(txn.quantity)
            price = price if price is not None else _as_float(txn.price)
            amount = amount if amount is not None else _as_float(txn.amount)
            trade_date = trade_date or (
                txn.transaction_date.date() if txn.transaction_date else None
            )

    if not issue.client_id:
        return None, "Missing client_id"
    if security_id is None or not txn_type or trade_date is None:
        return None, "Insufficient duplicate identity (security/type/date)"
    if quantity is None or price is None or amount is None:
        return None, "Insufficient duplicate signature (qty/price/amount)"

    return {
        "client_id": issue.client_id,
        "security_id": int(security_id),
        "type": str(txn_type),
        "trade_date": trade_date,
        "quantity": quantity,
        "price": price,
        "amount": amount,
        "transaction_id": txn_id,
    }, None


def find_matching_duplicate_transactions(
    *,
    client_id: int,
    security_id: int,
    txn_type: str,
    trade_date: date,
    quantity: float,
    price: float,
    amount: float,
) -> List[Transaction]:
    """
    Same matching rule as DataIntegrityManager.check_duplicates:
    same calendar day + security + type + qty + price + amount.
    """
    candidates = (
        Transaction.query.filter(
            Transaction.client_id == client_id,
            Transaction.security_id == security_id,
            Transaction.type == txn_type,
            func.date(Transaction.transaction_date) == trade_date,
        )
        .order_by(Transaction.id)
        .all()
    )
    matches: List[Transaction] = []
    for txn in candidates:
        if (
            _floats_equal(_as_float(txn.quantity), quantity)
            and _floats_equal(_as_float(txn.price), price)
            and _floats_equal(_as_float(txn.amount), amount)
        ):
            matches.append(txn)
    return matches


def compute_issue_priority_score(issue: DataIntegrityIssue, as_of: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Priority score = severity weight + aging weight(days open, capped at 60).
    """
    now = as_of or datetime.utcnow()
    sev = (getattr(issue, "severity", "warning") or "warning").lower()
    severity_weight = SEVERITY_WEIGHT.get(sev, 20)
    detected = getattr(issue, "detected_at", None) or now
    days_open = max(0, int((now - detected).total_seconds() // 86400))
    aging_weight = min(60, days_open * 2)
    return {
        "score": severity_weight + aging_weight,
        "severity_weight": severity_weight,
        "aging_weight": aging_weight,
        "days_open": days_open,
    }


def _evaluate_recommendation_execution(issue: DataIntegrityIssue) -> Dict[str, Any]:
    from services.task_auto_close_service import _should_close_task_for_issue

    is_cleared = bool(_should_close_task_for_issue(issue))
    return {
        "is_cleared": is_cleared,
        "reason": (
            "Workflow/condition progressed; recommendation execution issue is obsolete"
            if is_cleared
            else "Underlying workflow condition still active"
        ),
        "evidence": {
            "check_name": issue.check_name,
            "workflow_id": (issue.details or {}).get("workflow_id"),
        },
    }


def _evaluate_portfolio_performance(issue: DataIntegrityIssue) -> Dict[str, Any]:
    from agents.portfolio_performance_monitor import compute_underperformance_snapshot

    snapshot = compute_underperformance_snapshot(issue.client_id)
    if not snapshot:
        return {
            "is_cleared": False,
            "reason": "Insufficient data to evaluate portfolio-vs-benchmark currently",
            "evidence": {"snapshot_available": False},
        }
    under = float(snapshot.get("underperformance_pct") or 0)
    is_cleared = under <= 0
    return {
        "is_cleared": is_cleared,
        "reason": (
            "Portfolio currently at/above benchmark"
            if is_cleared
            else "Portfolio still underperforming benchmark"
        ),
        "evidence": snapshot,
    }


def _evaluate_duplicates(issue: DataIntegrityIssue) -> Dict[str, Any]:
    """
    Cleared when fewer than two exact-match trades remain for the flagged signature.
    Does not mutate or delete transactions — only evaluates issue lifecycle.
    """
    if (issue.check_name or "") not in ("", "duplicate_transaction"):
        return {
            "is_cleared": False,
            "reason": f"Unsupported DUPLICATES check_name={issue.check_name}",
            "evidence": {"check_name": issue.check_name},
        }

    payload, err = _duplicate_signature_from_issue(issue)
    if err or not payload:
        return {
            "is_cleared": False,
            "reason": err or "Could not parse duplicate signature",
            "evidence": {"details": issue.details or {}},
        }

    matches = find_matching_duplicate_transactions(
        client_id=payload["client_id"],
        security_id=payload["security_id"],
        txn_type=payload["type"],
        trade_date=payload["trade_date"],
        quantity=payload["quantity"],
        price=payload["price"],
        amount=payload["amount"],
    )
    match_ids = [t.id for t in matches]
    is_cleared = len(matches) < 2
    return {
        "is_cleared": is_cleared,
        "reason": (
            "Exact duplicate trade no longer present (fewer than two matching rows)"
            if is_cleared
            else "Exact duplicate trade still present"
        ),
        "evidence": {
            "check_name": issue.check_name or "duplicate_transaction",
            "security_id": payload["security_id"],
            "type": payload["type"],
            "trade_date": payload["trade_date"].isoformat(),
            "quantity": payload["quantity"],
            "price": payload["price"],
            "amount": payload["amount"],
            "matching_transaction_ids": match_ids,
            "match_count": len(match_ids),
        },
    }


def evaluate_issue_root_cause(issue: DataIntegrityIssue) -> Dict[str, Any]:
    """
    Evaluate whether issue root cause is currently cleared.
    """
    if not issue:
        return {"is_cleared": False, "reason": "Issue missing", "evidence": {}}
    if issue.status not in ("open", "baseline"):
        return {
            "is_cleared": True,
            "reason": "Issue already non-open",
            "evidence": {"status": issue.status},
        }

    category = (issue.check_category or "").upper()
    if category == "RECOMMENDATION_EXECUTION":
        return _evaluate_recommendation_execution(issue)
    if category == "PORTFOLIO_PERFORMANCE":
        return _evaluate_portfolio_performance(issue)
    if category == "DUPLICATES":
        return _evaluate_duplicates(issue)

    return {
        "is_cleared": False,
        "reason": "No evaluator registered for category",
        "evidence": {"category": category},
    }
