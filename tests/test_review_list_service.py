"""Review board buckets: open / due / upcoming (90 days)."""

from datetime import date
from types import SimpleNamespace

import pytest

from services.review_list_service import (
    UPCOMING_DAYS,
    classify_review_workflows,
    normalize_bucket,
)

pytestmark = pytest.mark.no_app


def _wf(wf_id, review_date, status="initiated", name="Client"):
    return SimpleNamespace(
        id=wf_id,
        review_date=review_date,
        status=status,
        client=SimpleNamespace(name=name),
    )


def test_normalize_bucket_defaults_and_rejects_unknown():
    assert normalize_bucket(None) == "board"
    assert normalize_bucket("due") == "due"
    assert normalize_bucket("UPCOMING") == "upcoming"
    assert normalize_bucket("junk") == "board"


def test_classify_open_due_upcoming_and_skips_closed():
    today = date(2026, 9, 11)
    rows = [
        _wf(1, date(2026, 8, 1), "meeting", "Zeta"),  # due
        _wf(2, date(2026, 9, 11), "sent", "Alpha"),  # due today
        _wf(3, date(2026, 10, 1), "initiated", "Beta"),  # upcoming
        _wf(4, date(2026, 12, 10), "initiated", "Gamma"),  # upcoming (90d)
        _wf(5, date(2027, 1, 15), "initiated", "Far"),  # open only (beyond 90d)
        _wf(6, date(2026, 8, 1), "closed", "Done"),  # excluded
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["due"]] == [1, 2]
    assert [w.id for w in out["upcoming"]] == [3, 4]
    assert [w.id for w in out["open"]] == [1, 2, 3, 4, 5]
    assert out["upcoming_days"] == UPCOMING_DAYS
    assert out["upcoming_end"] == date(2026, 12, 10)


def test_due_sorted_by_date_then_name():
    today = date(2026, 9, 11)
    rows = [
        _wf(1, date(2026, 8, 1), name="Bravo"),
        _wf(2, date(2026, 8, 1), name="Alpha"),
        _wf(3, date(2026, 7, 1), name="Charlie"),
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["due"]] == [3, 2, 1]
