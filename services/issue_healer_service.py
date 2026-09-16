"""
Scheduled healer for issue lifecycle consistency.

Evaluates open issues and auto-resolves ones whose root cause is cleared.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from models import DataIntegrityIssue
from services.issue_lifecycle_service import resolve_issue_and_sync
from services.issue_root_cause_evaluator import (
    compute_issue_priority_score,
    evaluate_issue_root_cause,
)


DEFAULT_CATEGORIES = (
    "RECOMMENDATION_EXECUTION",
    "PORTFOLIO_PERFORMANCE",
    "DUPLICATES",
)


def _normalize_categories(categories: Optional[Iterable[str]]) -> List[str]:
    if not categories:
        return list(DEFAULT_CATEGORIES)
    return [str(c).upper() for c in categories if c]


def run_issue_healer(
    *,
    dry_run: bool = False,
    client_id: Optional[int] = None,
    categories: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
    actor_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Evaluate open issues; resolve and sync lifecycle when root cause is cleared.
    """
    selected_categories = _normalize_categories(categories)
    q = DataIntegrityIssue.query.filter(
        DataIntegrityIssue.status.in_(("open", "baseline")),
        DataIntegrityIssue.check_category.in_(selected_categories),
    )
    if client_id:
        q = q.filter(DataIntegrityIssue.client_id == client_id)

    issues = q.all()
    scored = []
    for issue in issues:
        p = compute_issue_priority_score(issue)
        scored.append((p["score"], issue, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    if limit and limit > 0:
        scored = scored[:limit]

    stats: Dict[str, Any] = {
        "dry_run": dry_run,
        "evaluated": 0,
        "cleared": 0,
        "issues_resolved": 0,
        "tasks_closed": 0,
        "linked_alerts_resolved": 0,
        "client_stale_alerts_resolved": 0,
        "categories": selected_categories,
    }
    decisions: List[Dict[str, Any]] = []

    for _, issue, priority in scored:
        eval_result = evaluate_issue_root_cause(issue)
        is_cleared = bool(eval_result.get("is_cleared"))
        stats["evaluated"] += 1
        if is_cleared:
            stats["cleared"] += 1

        decision_row = {
            "issue_id": issue.id,
            "client_id": issue.client_id,
            "category": issue.check_category,
            "check_name": issue.check_name,
            "priority_score": priority["score"],
            "days_open": priority["days_open"],
            "is_cleared": is_cleared,
            "reason": eval_result.get("reason"),
        }
        decisions.append(decision_row)

        if not is_cleared or dry_run:
            continue

        outcome = resolve_issue_and_sync(
            issue,
            resolution_type="auto_cleared",
            actor_user_id=actor_user_id,
            notes=eval_result.get("reason") or "Auto-cleared by issue healer",
            source="issue_healer",
            commit=True,
        )
        stats["issues_resolved"] += int(outcome.get("issue_resolved", 0) or 0)
        stats["tasks_closed"] += int(outcome.get("linked_tasks_closed", 0) or 0)
        stats["linked_alerts_resolved"] += int(outcome.get("linked_alerts_resolved", 0) or 0)
        stats["client_stale_alerts_resolved"] += int(
            outcome.get("client_stale_alerts_resolved", 0) or 0
        )

    stats["decisions"] = decisions
    return stats
