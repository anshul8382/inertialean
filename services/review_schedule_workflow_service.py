"""Create missing ReviewWorkflow rows from an active ReviewSchedule.

Policy: create at most the next one open review (not a multi-year horizon).
If any non-closed workflow already exists, sync is a no-op.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)


def _step_months(frequency: str) -> int:
    return 6 if frequency == "half_yearly" else 12


def iter_review_dates(first: date, frequency: str, until: date):
    """Yield cadence dates from first through until (inclusive). Kept for callers/tests."""
    d = first
    step = _step_months(frequency)
    while d <= until:
        yield d
        d = d + relativedelta(months=step)


def next_review_date_after(
    first: date,
    frequency: str,
    *,
    after: Optional[date] = None,
) -> date:
    """
    Next schedule date strictly after `after`, or `first` when after is None.
    Advances from first by frequency until the result is > after.
    """
    if after is None:
        return first
    d = first
    step = _step_months(frequency)
    # Guard against pathological loops if after << first
    if d > after:
        return d
    while d <= after:
        d = d + relativedelta(months=step)
    return d


def sync_missing_workflows(schedule, user_id: int, horizon_months: int = 24) -> int:
    """
    Ensure the schedule has the next single open review workflow.

    ``horizon_months`` is ignored (kept for call-site compatibility). We no longer
    pre-create 24 months of reviews.
    """
    del horizon_months  # unused — previously created a full horizon

    from extensions import db
    from models import ReviewWorkflow
    from services.task_assignment_service import apply_review_assignment

    workflows = list(schedule.workflows or [])
    open_existing = [
        w for w in workflows if (getattr(w, "status", None) or "").strip().lower() != "closed"
    ]
    if open_existing:
        return 0

    if workflows:
        last_date = max(w.review_date for w in workflows if w.review_date)
        target = next_review_date_after(
            schedule.first_review_date,
            schedule.frequency,
            after=last_date,
        )
    else:
        target = schedule.first_review_date

    existing_dates = {w.review_date for w in workflows if w.review_date}
    if target in existing_dates:
        return 0

    rw = ReviewWorkflow(
        schedule_id=schedule.id,
        client_id=schedule.client_id,
        review_date=target,
        status="initiated",
        created_by=user_id,
    )
    db.session.add(rw)
    db.session.flush()
    try:
        apply_review_assignment(rw)
    except Exception as e:
        logger.warning("apply_review_assignment failed for new workflow: %s", e)
    db.session.commit()
    return 1
