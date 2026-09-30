"""Review board buckets: current / priority / this month / next month / suggested close / date missing."""

from datetime import date
from types import SimpleNamespace

import pytest

from services.review_list_service import (
    classify_review_workflows,
    normalize_bucket,
    workflows_for_bucket,
)

pytestmark = pytest.mark.no_app


def _wf(wf_id, review_date, status="initiated", name="Client", client_id=None):
    return SimpleNamespace(
        id=wf_id,
        client_id=client_id if client_id is not None else wf_id,
        review_date=review_date,
        status=status,
        client=SimpleNamespace(id=client_id if client_id is not None else wf_id, name=name),
    )


def test_normalize_bucket_defaults_and_rejects_unknown():
    assert normalize_bucket(None) == "board"
    assert normalize_bucket("priority") == "priority"
    assert normalize_bucket("DUE") == "due"
    assert normalize_bucket("junk") == "board"


def test_classify_sections_and_hides_beyond_next_month():
    today = date(2026, 9, 30)
    rows = [
        _wf(1, date(2026, 8, 1), "meeting", "Zeta", client_id=1),  # current
        _wf(2, date(2026, 9, 11), "initiated", "Alpha", client_id=2),  # priority
        _wf(3, date(2026, 10, 15), "initiated", "Beta", client_id=3),  # next_month (Oct)
        _wf(4, date(2026, 9, 30), "sent", "Gamma", client_id=4),  # current (due today in progress)
        _wf(5, date(2027, 1, 15), "initiated", "Far", client_id=5),  # beyond horizon
        _wf(6, date(2026, 8, 1), "closed", "Done", client_id=6),  # excluded
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["current"]] == [1, 4]
    assert [w.id for w in out["priority"]] == [2]
    assert [w.id for w in out["this_month"]] == []  # Sep 30 = last day; no "tomorrow–month end"
    assert [w.id for w in out["next_month"]] == [3]
    assert out["upcoming_end"] == date(2026, 10, 31)
    assert [w.id for w in out["open"]] == [1, 2, 4, 3]
    assert 5 not in [w.id for w in out["open"]]


def test_this_month_bucket_mid_month():
    today = date(2026, 9, 11)
    rows = [
        _wf(3, date(2026, 10, 1), "initiated", "Beta", client_id=3),
        _wf(7, date(2026, 9, 20), "initiated", "Soon", client_id=7),
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["this_month"]] == [7]
    assert [w.id for w in out["next_month"]] == [3]


def test_one_primary_per_client_earliest_due_hides_later_plan_rows():
    """Multiple initiated: primary = earliest due; later alignment rows stay off the board."""
    today = date(2026, 9, 30)
    rows = [
        _wf(73, date(2026, 7, 1), "initiated", "Aditya", client_id=10),
        _wf(74, date(2026, 8, 1), "initiated", "Aditya", client_id=10),
        _wf(75, date(2026, 9, 1), "initiated", "Aditya", client_id=10),
        _wf(76, date(2026, 10, 15), "initiated", "Aditya", client_id=10),
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["priority"]] == [73]
    assert out["next_month"] == []
    assert out["suggested_close"] == []


def test_older_initiated_suggested_close_when_newer_is_current():
    today = date(2026, 9, 30)
    rows = [
        _wf(73, date(2026, 7, 1), "initiated", "Aditya", client_id=10),
        _wf(74, date(2026, 8, 1), "initiated", "Aditya", client_id=10),
        _wf(76, date(2026, 9, 15), "meeting", "Aditya", client_id=10),  # current in progress
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["current"]] == [76]
    assert sorted(w.id for w in out["suggested_close"]) == [73, 74]


def test_prefer_in_progress_due_over_later_initiated():
    today = date(2026, 9, 30)
    rows = [
        _wf(1, date(2026, 9, 1), "meeting", "Ajith", client_id=20),
        _wf(2, date(2026, 10, 10), "initiated", "Ajith", client_id=20),
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["current"]] == [1]
    assert [w.id for w in out["suggested_close"]] == []  # later planning row hidden, not obsolete
    assert [w.id for w in out["next_month"]] == []


def test_date_missing_at_bottom_bucket():
    today = date(2026, 9, 30)
    rows = [
        _wf(1, None, "initiated", "NoDate", client_id=30),
        _wf(2, date(2026, 9, 1), "initiated", "HasDate", client_id=31),
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["date_missing"]] == [1]
    assert [w.id for w in out["priority"]] == [2]


def test_due_sorted_by_date_then_name():
    today = date(2026, 9, 11)
    rows = [
        _wf(1, date(2026, 8, 1), name="Bravo", client_id=1),
        _wf(2, date(2026, 8, 1), name="Alpha", client_id=2),
        _wf(3, date(2026, 7, 1), name="Charlie", client_id=3),
    ]
    out = classify_review_workflows(rows, today=today)
    assert [w.id for w in out["due"]] == [3, 2, 1]


def test_workflows_for_bucket():
    today = date(2026, 9, 11)
    rows = [_wf(1, date(2026, 8, 1), "meeting", "A", client_id=1)]
    out = classify_review_workflows(rows, today=today)
    assert workflows_for_bucket(out, "current") == out["current"]
    assert workflows_for_bucket(out, "due") == out["due"]
