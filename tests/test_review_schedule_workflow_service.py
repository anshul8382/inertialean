"""Review schedule sync creates only the next one open review."""

from datetime import date
from types import SimpleNamespace

import pytest

from services.review_schedule_workflow_service import (
    next_review_date_after,
    sync_missing_workflows,
)

pytestmark = pytest.mark.no_app


def test_next_review_date_after_first_when_no_after():
    assert next_review_date_after(date(2025, 7, 12), "half_yearly") == date(2025, 7, 12)


def test_next_review_date_after_advances_past_closed():
    assert next_review_date_after(
        date(2025, 7, 12), "half_yearly", after=date(2026, 7, 12)
    ) == date(2027, 1, 12)


def test_sync_skips_when_open_workflow_exists(monkeypatch):
    schedule = SimpleNamespace(
        id=1,
        client_id=9,
        first_review_date=date(2025, 7, 12),
        frequency="half_yearly",
        workflows=[
            SimpleNamespace(review_date=date(2026, 7, 12), status="initiated"),
        ],
    )
    created = sync_missing_workflows(schedule, user_id=1)
    assert created == 0


def test_sync_creates_only_first_when_empty(monkeypatch):
    added = []

    class _Sess:
        def add(self, obj):
            added.append(obj)

        def flush(self):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(
        "extensions.db",
        SimpleNamespace(session=_Sess()),
        raising=False,
    )
    # Patch imports used inside sync
    import services.review_schedule_workflow_service as mod

    class _RW:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.id = 101

    monkeypatch.setattr(mod, "sync_missing_workflows", sync_missing_workflows)

    # Directly exercise by patching models + assignment at import sites
    import sys
    from unittest.mock import MagicMock

    fake_models = MagicMock()
    fake_models.ReviewWorkflow = _RW
    monkeypatch.setitem(sys.modules, "models", fake_models)

    fake_assign = MagicMock()
    monkeypatch.setitem(
        sys.modules,
        "services.task_assignment_service",
        SimpleNamespace(apply_review_assignment=lambda rw: True),
    )

    # Re-import path: call function which imports models inside
    schedule = SimpleNamespace(
        id=1,
        client_id=9,
        first_review_date=date(2025, 7, 12),
        frequency="half_yearly",
        workflows=[],
    )

    # Inject db into extensions module used by sync
    import extensions

    sess = _Sess()
    monkeypatch.setattr(extensions, "db", SimpleNamespace(session=sess))

    n = sync_missing_workflows(schedule, user_id=7)
    assert n == 1
    assert len(added) == 1
    assert added[0].review_date == date(2025, 7, 12)
    assert added[0].status == "initiated"


def test_sync_creates_one_after_closed(monkeypatch):
    added = []

    class _Sess:
        def add(self, obj):
            added.append(obj)

        def flush(self):
            pass

        def commit(self):
            pass

    import extensions
    from unittest.mock import MagicMock
    import sys

    class _RW:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(extensions, "db", SimpleNamespace(session=_Sess()))
    monkeypatch.setitem(sys.modules, "models", SimpleNamespace(ReviewWorkflow=_RW))
    monkeypatch.setitem(
        sys.modules,
        "services.task_assignment_service",
        SimpleNamespace(apply_review_assignment=lambda rw: True),
    )

    schedule = SimpleNamespace(
        id=1,
        client_id=9,
        first_review_date=date(2025, 7, 12),
        frequency="half_yearly",
        workflows=[
            SimpleNamespace(review_date=date(2026, 7, 12), status="closed"),
        ],
    )
    n = sync_missing_workflows(schedule, user_id=7)
    assert n == 1
    assert len(added) == 1
    assert added[0].review_date == date(2027, 1, 12)
